#!/usr/bin/env python3
"""Test integracji z Bybit (testnet lub demo): sprawdza na prawdziwych odpowiedziach giełdy te założenia, które reszta programu
testuje tylko na atrapach. Składa i zamyka drobne zlecenia kontrolne, a na końcu zawsze sprząta (anuluje zlecenia, zamyka pozycję).

Użycie (na serwerze, jako użytkownik btc):
  sudo -u btc /opt/btc/venv/bin/python /opt/btc/btc_selftest.py --mode demo          # pełny test na koncie demo
  sudo -u btc /opt/btc/venv/bin/python /opt/btc/btc_selftest.py --mode testnet
  sudo -u btc /opt/btc/venv/bin/python /opt/btc/btc_selftest.py --read-only          # tylko odczyt (bezpieczne także na koncie live)
Klucze według trybu (BYBIT_TEST_* dla demo/testnet). Test ZAWSZE odmawia składania zleceń w trybie live i dryrun.
Wynik: tabela PASS / WARN / FAIL oraz plik selftest_report.json. Kod wyjścia 1 przy jakimkolwiek FAIL.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_botcfg as bb  # noqa: E402
import btc_trader as bt  # noqa: E402

PREFIX = "st"          # identyfikatory zleceń kontrolnych: st + hex (nie pasują do wzorców bota, więc bot ich nie rusza)


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, status, detail=""):
        self.rows.append({"name": name, "status": status, "detail": str(detail)[:300]})
        print(f"{status:5} {name}" + (f"  ({str(detail)[:140]})" if detail else ""), flush=True)

    @property
    def failed(self):
        return any(r["status"] == "FAIL" for r in self.rows)


def code_of(e):
    m = re.search(r"retCode (\d+)", str(e))
    return int(m.group(1)) if m else None


def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def rnd(px, tick, up=False):
    f = math.ceil if up else math.floor
    return bt.fmt_px(f(px / tick + 1e-9) * tick, tick)


def run(api, pub, c, symbol="BTCUSDT", read_only=False, sleep=time.sleep, now=time.time):
    r = Report()
    lid = lambda tag: f"{PREFIX}{uuid.uuid4().hex[:14]}{tag}"          # noqa: E731
    made_orders, opened, attempted = [], False, False
    cat = {"category": "linear", "symbol": symbol}
    try:
        # --- 1. odczyt: konto, instrument, cena
        try:
            w = api.ok("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})["list"][0]
            equity = fnum(w.get("totalEquity"))
            r.add("uwierzytelnienie i saldo konta", "PASS", f"equity {equity:.2f} USDT")
        except Exception as e:  # noqa: BLE001
            r.add("uwierzytelnienie i saldo konta", "FAIL", e)
            return r
        inst = pub._request("/v5/market/instruments-info", cat)["list"][0]
        lot, pf = inst["lotSizeFilter"], inst["priceFilter"]
        step, mn, tick = fnum(lot["qtyStep"]), fnum(lot["minOrderQty"]), fnum(pf["tickSize"])
        min_notional = fnum(lot.get("minNotionalValue"), 5.0)
        r.add("instrument: kroki i minimum zlecenia", "PASS", f"qtyStep {step}, min {mn}, tick {tick}, minNotionalValue {lot.get('minNotionalValue', 'brak pola (zakładam 5)')}")
        if lot.get("minNotionalValue") is None:
            r.add("pole minNotionalValue", "WARN", "brak w odpowiedzi; moduły zakładają minimum 5 USDT")
        tk = pub.ticker(symbol)
        last = fnum(tk.get("lastPrice"))
        r.add("ticker: lastPrice i turnover24h", "PASS" if last > 0 and fnum(tk.get("turnover24h")) > 0 else "FAIL", f"cena {last}, obrót {tk.get('turnover24h')}")
        r.add("ticker: pole fundingRate (zespół sygnałów)", "PASS" if tk.get("fundingRate") not in (None, "") else "WARN", f"fundingRate={tk.get('fundingRate')}")
        # --- 2. parametry używane przez nadzór bota
        for label, path, params in (("pozycje z settleCoin=USDT", "/v5/position/list", {"category": "linear", "settleCoin": "USDT"}),
                                    ("zlecenia z settleCoin=USDT", "/v5/order/realtime", {"category": "linear", "settleCoin": "USDT"})):
            try:
                n = len(api.ok("GET", path, params).get("list") or [])
                r.add(f"odczyt: {label}", "PASS", f"{n} rekordów")
            except Exception as e:  # noqa: BLE001
                r.add(f"odczyt: {label}", "FAIL", e)
        end = int(now() * 1000)
        for label, extra in (("closed-pnl BEZ symbolu (dzienna strata bota)", {}), ("closed-pnl z symbolem (wariant awaryjny)", {"symbol": symbol})):
            try:
                n = len(api.ok("GET", "/v5/position/closed-pnl", {"category": "linear", "startTime": end - 6 * 86400_000, "endTime": end, "limit": 100, **extra}).get("list") or [])
                r.add(f"odczyt: {label}", "PASS", f"{n} rekordów")
            except Exception as e:  # noqa: BLE001
                r.add(f"odczyt: {label}", "WARN" if extra == {} else "FAIL", f"{e} (bot użyje zapytania per symbol)" if extra == {} else e)
        if read_only:
            r.add("składanie zleceń", "SKIP", "tryb tylko do odczytu")
            return r

        # --- 3. zlecenie limit daleko od rynku z stop-lossem: tworzenie, odczyt, duplikat identyfikatora, anulowanie
        qty = math.ceil(max(mn, min_notional * 1.3 / last) / step - 1e-9) * step
        qty = round(qty, 8)
        px, sl = rnd(last * 0.85, tick), rnd(last * 0.80, tick)
        link = lid("a")
        entry = {**cat, "side": "Buy", "orderType": "Limit", "qty": f"{qty}", "price": px, "timeInForce": "GTC", "orderLinkId": link, "positionIdx": 0,
                 "stopLoss": sl, "slTriggerBy": "LastPrice", "tpslMode": "Full", "slOrderType": "Market"}
        attempted = True
        try:
            res = api.ok("POST", "/v5/order/create", entry)
            made_orders.append(link)
            r.add("zlecenie limit ze stop-lossem (format jak u bota)", "PASS", f"orderId {res.get('orderId')}")
        except Exception as e:  # noqa: BLE001
            r.add("zlecenie limit ze stop-lossem (format jak u bota)", "FAIL", e)
            return r
        sleep(0.8)
        got = [o for o in api.ok("GET", "/v5/order/realtime", {**cat}).get("list", []) if o.get("orderLinkId") == link]
        if got:
            ok_sl = abs(fnum(got[0].get("stopLoss")) - fnum(sl)) < tick * 2
            r.add("odczyt zlecenia: stop-loss dołączony", "PASS" if ok_sl else "FAIL", f"stopLoss={got[0].get('stopLoss')} oczekiwano {sl}")
        else:
            r.add("odczyt zlecenia po orderLinkId", "FAIL", "zlecenia nie widać w /v5/order/realtime")
        try:
            api.ok("POST", "/v5/order/create", entry)
            r.add("duplikat orderLinkId jest odrzucany (idempotencja bota)", "FAIL", "Bybit przyjął drugie zlecenie z tym samym identyfikatorem")
        except Exception as e:  # noqa: BLE001
            r.add("duplikat orderLinkId jest odrzucany (idempotencja bota)", "PASS" if code_of(e) == 110072 or "duplicate" in str(e).lower() else "WARN", e)
        if got:
            api.ok("POST", "/v5/order/cancel", {**cat, "orderId": got[0]["orderId"]})
            sleep(0.6)
            left = [o for o in api.ok("GET", "/v5/order/realtime", {**cat}).get("list", []) if o.get("orderLinkId") == link]
            r.add("anulowanie zlecenia", "PASS" if not left else "FAIL", "zlecenie zniknęło" if not left else "nadal aktywne")
        # --- 4. pozycja: wejście rynkowe z stop-lossem, cele reduce-only, stop na wejście, zamknięcie
        q2 = round(math.ceil(max(2 * mn, min_notional * 1.3 / last) / step - 1e-9) * step, 8)
        if q2 * last > equity * 0.2:
            r.add("pozycja kontrolna", "SKIP", f"za małe saldo konta testowego ({equity:.0f} USDT) na wielkość {q2}")
            return r
        try:
            api.ok("POST", "/v5/position/set-leverage", {**cat, "buyLeverage": "1", "sellLeverage": "1"}, allow=(110043,))
            r.add("ustawienie dźwigni 1", "PASS")
        except Exception as e:  # noqa: BLE001
            r.add("ustawienie dźwigni 1", "FAIL", e)
        mk = lid("m")
        try:
            api.ok("POST", "/v5/order/create", {**cat, "side": "Buy", "orderType": "Market", "qty": f"{q2}", "orderLinkId": mk, "positionIdx": 0, "stopLoss": rnd(last * 0.90, tick),
                                                "slTriggerBy": "LastPrice", "tpslMode": "Full", "slOrderType": "Market"})
            opened = True
            r.add("wejście rynkowe z stop-lossem", "PASS", f"qty {q2}")
        except Exception as e:  # noqa: BLE001
            r.add("wejście rynkowe z stop-lossem", "FAIL", e)
            return r
        pos = None
        for _ in range(8):
            sleep(0.8)
            lst = [p for p in api.ok("GET", "/v5/position/list", {**cat}).get("list", []) if fnum(p.get("size")) > 0]
            if lst:
                pos = lst[0]
                break
        if not pos:
            r.add("pozycja pojawia się na koncie", "FAIL", "brak pozycji po 6 s")
            return r
        r.add("pozycja pojawia się na koncie", "PASS", f"size {pos['size']}, avgPrice {pos.get('avgPrice')}")
        r.add("pozycja ma stop-loss (nadzór bota tego wymaga)", "PASS" if fnum(pos.get("stopLoss")) > 0 else "FAIL", f"stopLoss={pos.get('stopLoss')}")
        r.add("dźwignia pozycji ≤ 2", "PASS" if fnum(pos.get("leverage")) <= 2 else "FAIL", f"leverage={pos.get('leverage')}")
        r.add("pola pozycji używane przez bota", "PASS" if all(k in pos for k in ("positionValue", "avgPrice", "unrealisedPnl", "createdTime", "side")) else "WARN", ", ".join(k for k in ("positionValue", "avgPrice", "unrealisedPnl", "createdTime", "side") if k not in pos) or "komplet")
        size = fnum(pos["size"])
        q1 = round(math.floor(size / 2 / step + 1e-9) * step, 8)
        tps = []
        for n, (qq, mult) in enumerate(((q1, 1.15), (round(size - q1, 8), 1.25)), 1):
            tl = lid(f"t{n}")
            try:
                api.ok("POST", "/v5/order/create", {**cat, "side": "Sell", "orderType": "Limit", "qty": f"{qq}", "price": rnd(last * mult, tick), "reduceOnly": True, "timeInForce": "GTC", "positionIdx": 0, "orderLinkId": tl})
                made_orders.append(tl)
                tps.append(tl)
            except Exception as e:  # noqa: BLE001
                r.add(f"cel TP{n} (limit reduce-only, jak u bota)", "FAIL", e)
        sleep(0.8)
        open_ = {o.get("orderLinkId"): o for o in api.ok("GET", "/v5/order/realtime", {**cat}).get("list", [])}
        if len(tps) == 2:
            both = all(t in open_ and open_[t].get("reduceOnly") in (True, "true") for t in tps)
            r.add("dwa cele częściowe widoczne jako reduce-only", "PASS" if both else "FAIL", f"{sum(t in open_ for t in tps)}/2 widocznych")
        try:
            api.ok("POST", "/v5/position/trading-stop", {**cat, "tpslMode": "Full", "positionIdx": 0, "stopLoss": rnd(last * 0.96, tick), "slTriggerBy": "LastPrice"})
            sleep(0.8)
            p2 = [p for p in api.ok("GET", "/v5/position/list", {**cat}).get("list", []) if fnum(p.get("size")) > 0][0]
            r.add("przesunięcie stopa (trading-stop)", "PASS" if abs(fnum(p2.get("stopLoss")) - last * 0.96) < tick * 3 else "WARN", f"stopLoss={p2.get('stopLoss')}")
        except Exception as e:  # noqa: BLE001
            r.add("przesunięcie stopa (trading-stop)", "FAIL", e)
    except Exception as e:  # noqa: BLE001 — nieoczekiwany błąd: raportujemy i sprzątamy
        r.add("nieoczekiwany błąd testu", "FAIL", f"{type(e).__name__}: {e}")
    finally:
        if attempted:
            _cleanup(api, cat, made_orders, opened, r, sleep)
    return r


def _cleanup(api, cat, made, opened, r, sleep):
    """Zawsze: anuluj zlecenia kontrolne i zamknij pozycję kontrolną."""
    try:
        for o in api.ok("GET", "/v5/order/realtime", {**cat}).get("list", []):
            if str(o.get("orderLinkId", "")).startswith(PREFIX):
                api.ok("POST", "/v5/order/cancel", {**cat, "orderId": o["orderId"]})
        if opened:
            for p in api.ok("GET", "/v5/position/list", {**cat}).get("list", []):
                if fnum(p.get("size")) > 0:
                    api.ok("POST", "/v5/order/create", {**cat, "side": "Sell" if p.get("side") == "Buy" else "Buy", "orderType": "Market", "qty": p["size"], "reduceOnly": True, "positionIdx": 0, "orderLinkId": f"{PREFIX}{uuid.uuid4().hex[:14]}x"})
            for _ in range(8):
                sleep(0.8)
                if not [p for p in api.ok("GET", "/v5/position/list", {**cat}).get("list", []) if fnum(p.get("size")) > 0]:
                    r.add("sprzątanie: pozycja zamknięta, zlecenia anulowane", "PASS")
                    return
            r.add("sprzątanie: pozycja zamknięta", "FAIL", "POZYCJA KONTROLNA NADAL OTWARTA: zamknij ją ręcznie w aplikacji")
        else:
            r.add("sprzątanie: zlecenia anulowane", "PASS")
    except Exception as e:  # noqa: BLE001
        r.add("sprzątanie", "FAIL", f"{e} (sprawdź ręcznie zlecenia i pozycję na koncie testowym)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("demo", "testnet"), help="konto testowe (domyślnie z TRADER_MODE)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--read-only", action="store_true", help="tylko odczyty (bezpieczne także na koncie live)")
    a = ap.parse_args()
    mode = a.mode or (os.environ.get("TRADER_MODE") or "").lower()
    if a.mode:
        os.environ["TRADER_MODE"] = a.mode
    if a.read_only:        # odczyt nie składa żadnych zleceń, więc nie wymaga potwierdzeń trybu live
        os.environ.setdefault("TRADER_CONFIRM_LIVE", "I_ACCEPT_THE_RISK")
        os.environ.setdefault("TRADER_MAX_CAPITAL_USDT", "1")
    bt.load_env()
    mode = a.mode or (os.environ.get("TRADER_MODE") or "").lower()
    if mode not in ("demo", "testnet") and not a.read_only:
        sys.exit("Pełny test składa zlecenia, więc działa tylko na koncie demo lub testnet (--mode demo|testnet). Do konta live użyj --read-only.")
    os.environ["TRADER_MODE"] = mode if mode in ("demo", "testnet", "live") else "demo"
    c = bt.cfg()
    if not a.read_only and not ("api-demo" in c["base"] or "testnet" in c["base"]):
        sys.exit("Adres API nie jest testowy: przerywam.")
    r = run(bt.Api(c), bt.make_pub(c), c, a.symbol.upper(), a.read_only)
    rep = {"ts": int(time.time()), "mode": c["mode"], "base": c["base"], "symbol": a.symbol.upper(), "read_only": a.read_only, "results": r.rows}
    bb._atomic_write(os.path.join(HERE, "selftest_report.json"), json.dumps(rep, ensure_ascii=False))
    n = {s: sum(1 for x in r.rows if x["status"] == s) for s in ("PASS", "WARN", "FAIL", "SKIP")}
    print(f"\nPodsumowanie: {n['PASS']} PASS, {n['WARN']} WARN, {n['FAIL']} FAIL, {n['SKIP']} SKIP. Raport: {os.path.join(HERE, 'selftest_report.json')}")
    sys.exit(1 if r.failed else 0)


if __name__ == "__main__":
    main()
