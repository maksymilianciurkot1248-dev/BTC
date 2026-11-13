#!/usr/bin/env python3
"""Wykonawca zleceń składanych ze strony (usługa btc-exec).

Strona (zakładka Mój Bybit) nie ma kluczy: zapisuje tylko prośbę w bot_orders.json. Ten proces co kilka sekund sprawdza kolejkę,
weryfikuje każde zlecenie tymi samymi limitami co bot i składa je na giełdzie kluczem z trader.env / .env. Zasady:
  - działa tylko, gdy w trader.env jest TRADER_WEB_ORDERS=yes (włączasz to wyłącznie na serwerze, nie ze strony),
  - otwarcie pozycji zawsze ma stop-loss; suma pozycji nie przekroczy dźwigni x kapitał (TRADER_MAX_LEVERAGE, maks. 2),
  - ryzyko do stopa nie większe niż TRADER_MANUAL_MAX_RISK_PCT (domyślnie 5%) kapitału,
  - tryb dryrun nic nie wysyła (test całej ścieżki), plik STOP blokuje nowe pozycje, ale zamknięcie pozycji zawsze działa.
"""
import fcntl
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_botcfg as bb  # noqa: E402
import btc_risk as rk  # noqa: E402
import btc_trader as bt  # noqa: E402

MIN_NOTIONAL_USD = 5.0
POLL_S = 3


def web_enabled():
    return os.environ.get("TRADER_WEB_ORDERS", "").lower() == "yes"


def lock(d, timeout=25):
    """Wspólna blokada z traderem: nigdy nie składamy zleceń równocześnie z jego przebiegiem."""
    fh = open(os.path.join(d, ".trader.lock"), "w")
    end = time.time() + timeout
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.time() > end:
                fh.close()
                return None
            time.sleep(0.5)


def account(api, c):
    if not api:
        return (c["max_capital"] or 1000.0), [], []
    eq = float(api.ok("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})["list"][0]["totalEquity"])
    pos = [p for p in api.ok("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT"})["list"] if float(p["size"]) > 0]
    orders = api.ok("GET", "/v5/order/realtime", {"category": "linear", "settleCoin": "USDT"})["list"]
    return (min(eq, c["max_capital"]) if c["max_capital"] else eq), pos, orders


def execute_close(o, c, api):
    if not api:
        return "done", "DRYRUN: zamknięcie nie wysłane", None
    pos = [p for p in api.ok("GET", "/v5/position/list", {"category": "linear", "symbol": o["symbol"]})["list"] if float(p["size"]) > 0]
    if not pos:
        return "rejected", f"brak otwartej pozycji na {o['symbol']}", None
    p = pos[0]
    res = api.ok("POST", "/v5/order/create", {"category": "linear", "symbol": o["symbol"], "side": "Sell" if p["side"] == "Buy" else "Buy", "orderType": "Market",
                                              "qty": p["size"], "reduceOnly": True, "positionIdx": 0, "orderLinkId": o["id"]})
    bt.notify(f"[{o['symbol']}] zamknięcie pozycji ze strony: {p['side']} {p['size']}")
    bt.flush_notify()
    return "done", f"zamknięto {p['size']} {o['symbol']} (rynkowo)", res.get("orderId")


def execute_open(o, c, api, pub, stop_file, d=HERE):
    sym, long_ = o["symbol"], o["side"] == "long"
    risk_state = bb.read_risk(d)
    if risk_state.get("breakers") and time.time() - risk_state.get("ts", 0) < 1800:
        b = risk_state["breakers"][0]
        return "rejected", f"wyłącznik ryzyka: {b['label']} ({b['detail']}); nowe pozycje wstrzymane, zamykanie działa", None
    if stop_file:
        return "rejected", "plik STOP blokuje nowe pozycje", None
    if c.get("paused"):
        return "rejected", "bot jest wstrzymany w ustawieniach (pauza blokuje też zlecenia ze strony)", None
    if not 1 <= c["max_lev"] <= 2:
        return "rejected", "dźwignia poza zakresem", None
    capital, positions, orders = account(api, c)
    if any(p["symbol"] == sym for p in positions) or any(x["symbol"] == sym for x in orders):
        return "rejected", f"na {sym} jest już pozycja lub zlecenie", None
    tk = pub.ticker(sym)
    if not tk:
        return "rejected", f"nieznany symbol {sym}", None
    if float(tk.get("turnover24h") or 0) < bt.MIN_TURNOVER_USD:
        return "rejected", "za mała płynność (obrót 24 h poniżej 20 mln USDT)", None
    inst = pub._request("/v5/market/instruments-info", {"category": "linear", "symbol": sym})["list"][0]
    if inst.get("status", "Trading") != "Trading":
        return "rejected", f"instrument niedostępny ({inst.get('status')})", None
    step, min_qty, tick = float(inst["lotSizeFilter"]["qtyStep"]), float(inst["lotSizeFilter"]["minOrderQty"]), float(inst["priceFilter"]["tickSize"])
    last = float(tk["lastPrice"])
    entry = o["price"] if o["order_type"] == "limit" else last
    sg = 1 if long_ else -1
    stop_px, tp_px = entry * (1 - sg * o["stop_pct"] / 100), (entry * (1 + sg * o["tp_pct"] / 100) if o.get("tp_pct") else None)
    left = bt.notional_left(capital, c["max_lev"], [float(p["positionValue"]) for p in positions])
    if o["notional"] > left * 1.001:
        return "rejected", f"wartość {o['notional']:.2f} USDT przekracza wolny limit dźwigni (x{c['max_lev']:g} kapitału {capital:.0f} USDT, wolne {left:.2f} USDT)", None
    qty = round(math.floor(o["notional"] / entry / step + 1e-9) * step, 8)
    if qty < min_qty or qty * entry < MIN_NOTIONAL_USD:
        return "rejected", f"rozmiar poniżej minimum giełdy (min. {min_qty} szt. ≈ {min_qty * entry:.1f} USDT, nie mniej niż {MIN_NOTIONAL_USD:.0f} USDT)", None
    risk = qty * abs(entry - stop_px)
    max_risk = capital * float(os.environ.get("TRADER_MANUAL_MAX_RISK_PCT", "5")) / 100
    if risk > max_risk:
        return "rejected", f"ryzyko do stopa {risk:.2f} USDT przekracza limit {max_risk:.2f} USDT ({os.environ.get('TRADER_MANUAL_MAX_RISK_PCT', '5')}% kapitału): zmniejsz wartość lub przybliż stop", None
    if long_ and not stop_px < entry or not long_ and not stop_px > entry:
        return "rejected", "stop-loss po złej stronie ceny", None
    notional = qty * entry
    if notional > capital * c["max_lev"] * 1.001:
        return "rejected", "nominał przekracza limit dźwigni", None
    items = rk.position_items(positions)
    corr = bt.risk_corr(pub, sorted({i["symbol"] for i in items} | {sym}), {})
    limit = capital * c["max_heat_pct"] / 100
    new = {"symbol": sym, "side": 1 if long_ else -1, "risk": risk}
    if rk.fit_scale(items, new, corr, limit) < 1.0:
        return "rejected", (f"przekroczyłoby limit ryzyka portfela: skorelowane ryzyko {rk.corr_risk(items + [new], corr):.2f} USDT przy limicie {limit:.2f} USDT "
                            f"({c['max_heat_pct']:g}% kapitału). Zmniejsz wartość, przybliż stop albo zamknij inną pozycję"), None
    order = {"category": "linear", "symbol": sym, "side": "Buy" if long_ else "Sell", "qty": f"{qty}", "orderLinkId": o["id"], "positionIdx": 0, "timeInForce": "GTC",
             "stopLoss": bt.fmt_px(stop_px, tick), "slTriggerBy": "LastPrice", "tpslMode": "Full", "slOrderType": "Market",
             "orderType": "Market" if o["order_type"] == "market" else "Limit"}
    if o["order_type"] == "limit":
        order["price"] = bt.fmt_px(entry, tick)
    if tp_px:
        order.update(takeProfit=bt.fmt_px(tp_px, tick), tpTriggerBy="LastPrice", tpOrderType="Market")
    desc = f"{o['side']} {sym} qty={qty} ≈{notional:.2f} USDT, wejście {order.get('price', 'rynkowo')}, stop {order['stopLoss']}, ryzyko {risk:.2f} USDT"
    if not api:
        return "done", "DRYRUN: nie wysłano (" + desc + ")", None
    api.ok("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": sym, "buyLeverage": f"{c['max_lev']:g}", "sellLeverage": f"{c['max_lev']:g}"}, allow=(110043,))
    res = api.ok("POST", "/v5/order/create", order)
    st = bt.load_state()
    st.setdefault("managed", {})[sym] = int(time.time())              # pozycja ze strony jest nadzorowana (stop-loss, dźwignia) jak pozycje bota
    bt.save_state(st)
    bt.notify(f"zlecenie ze strony: {desc}")
    bt.flush_notify()
    return "done", "złożono: " + desc, res.get("orderId")


def process(d, c, api, pub, now=None):
    """Realizuje kolejkę. Zwraca liczbę przetworzonych zleceń."""
    queued = [o for o in bb.read_orders(d) if o.get("status") == "queued"]
    n = 0
    stop_file = os.path.exists(os.path.join(d, "STOP"))
    for o in sorted(queued, key=lambda x: x.get("created", 0)):
        if (now or time.time()) - o.get("created", 0) > 600:
            bb.set_order(d, o["id"], status="rejected", result="zlecenie wygasło w kolejce (ponad 10 minut)", done=int(now or time.time()))
            continue
        try:
            if not web_enabled():
                status, msg, oid = "rejected", "zlecenia ze strony są wyłączone na serwerze (dopisz TRADER_WEB_ORDERS=yes w trader.env)", None
            elif o["type"] == "close":
                status, msg, oid = execute_close(o, c, api)
            else:
                status, msg, oid = execute_open(o, c, api, pub, stop_file, d)
        except Exception as e:  # noqa: BLE001 — błąd jednego zlecenia nie zatrzymuje usługi
            status, msg, oid = "error", f"błąd: {str(e)[:160]}", None
        bb.set_order(d, o["id"], status=status, result=msg, order_id=oid, done=int(now or time.time()))
        n += 1
    return n


def heartbeat(d, c, err=None):
    bb.write_exec(d, {"ts": int(time.time()), "mode": c["mode"] if c else None, "web_orders": web_enabled(), "max_lev": c.get("max_lev") if c else None, "max_capital": c.get("max_capital") if c else None,
                      "max_risk_pct": float(os.environ.get("TRADER_MANUAL_MAX_RISK_PCT", "5")), "error": err})


def once(d=HERE):
    for k in [k for k in os.environ if k.startswith(("TRADER_", "BYBIT_"))]:      # świeże wczytanie kluczy i ustawień przy każdym obiegu
        os.environ.pop(k)
    bt.load_env()
    c = bt.cfg()
    bb.apply_settings(c, bb.read_settings(d))
    heartbeat(d, c)
    if not any(o.get("status") == "queued" for o in bb.read_orders(d)):
        return 0
    fh = lock(d)
    if fh is None:
        return 0
    try:
        if c["mode"] != "dryrun":
            bt.key_age_check(c)
        return process(d, c, bt.Api(c) if c["mode"] != "dryrun" else None, bt.make_pub(c))
    finally:
        fh.close()


def main():
    print("btc-exec: start", flush=True)
    while True:
        try:
            once()
        except SystemExit as e:
            heartbeat(HERE, None, str(e.code)[:200])
        except Exception as e:  # noqa: BLE001
            print(f"btc-exec: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
