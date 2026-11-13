#!/usr/bin/env python3
"""Mój Bybit: statystyki prywatnego konta (tylko ODCZYT). Co minutę (timer btc-account) zapisuje bybit_account.json,
który serwuje /api/account (za hasłem nginx). Używa klucza BYBIT_API_KEY / BYBIT_API_SECRET z .env: utwórz go jako
"Read-Only" (bez handlu i bez wypłat). Skrypt zna wyłącznie metody GET klienta BybitClient; niczego nie zleca.
Cięższa historia (zamknięte pozycje 90 dni, wypełnienia 30 dni) odświeża się co 30 minut, a stan konta co przebieg.
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_analysis as ba  # noqa: E402
import btc_botcfg as bb  # noqa: E402

OUT = "bybit_account.json"
HISTORY_EVERY_S = 1800
PERIODS = (7, 30, 90)
EQUITY_METRIC = "bybit_equity_usd"


def f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def day(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def period_stats(rows, since_ms):
    """Statystyki zamkniętych pozycji z okna czasowego (+ opłaty, podział po symbolu i kierunku, wynik dzienny)."""
    sel = [r for r in rows if f(r.get("updatedTime") or r.get("createdTime")) >= since_ms]
    st = bb.stats_from_closed(sel)
    out = {"trades": st.get("trades", 0)}
    if not out["trades"]:
        return out, [], {}
    out.update({k: st[k] for k in ("wins", "losses", "win_rate", "total_pnl", "avg_win", "avg_loss", "profit_factor", "best", "worst", "max_drawdown")})
    out["fees"] = round(sum(f(r.get("openFee")) + f(r.get("closeFee")) for r in sel), 4)
    sym = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    side = {"long": [0, 0.0], "short": [0, 0.0]}
    daily = defaultdict(float)
    for r in sel:
        p = f(r.get("closedPnl"))
        s = sym[r.get("symbol", "?")]
        s["trades"] += 1
        s["wins"] += p > 0
        s["pnl"] += p
        k = "long" if r.get("side") == "Sell" else "short"      # pozycję long zamyka zlecenie Sell
        side[k][0] += 1
        side[k][1] += p
        daily[day(f(r.get("updatedTime") or r.get("createdTime")))] += p
    out["by_side"] = {k: {"trades": v[0], "pnl": round(v[1], 4)} for k, v in side.items()}
    by_sym = sorted(({"symbol": k, "trades": v["trades"], "win_rate": round(v["wins"] / v["trades"] * 100, 1), "pnl": round(v["pnl"], 4)} for k, v in sym.items()), key=lambda x: -x["pnl"])
    return out, by_sym, dict(sorted(daily.items()))


def key_info(info):
    perms = info.get("permissions") or {}
    flat = sorted(f"{k}:{v}" for k, vs in perms.items() for v in (vs or []))
    withdraw = any(v == "Withdraw" for vs in perms.values() for v in (vs or []))
    ro = bool(int(info.get("readOnly", 0))) if str(info.get("readOnly", "")).lstrip("-").isdigit() else bool(info.get("readOnly"))
    ips = info.get("ips") or []
    warn, notes = [], []
    if withdraw:
        warn.append("Klucz ma uprawnienie do wypłat. Jeśli ktoś przejmie serwer, może wypłacić środki: wyłącz to uprawnienie w ustawieniach klucza na Bybit.")
    if not ips or ips == ["*"]:
        notes.append("Klucz nie ma ograniczenia adresu IP (zalecane: dodaj IP serwera w ustawieniach klucza na Bybit).")
    exp = info.get("expiredAt")
    days_left = None
    try:
        if exp and not str(exp).startswith("1970"):
            days_left = (datetime.fromisoformat(str(exp).replace("Z", "+00:00")) - datetime.now(timezone.utc)).days
            if days_left < 14:
                warn.append(f"Klucz wygasa za {days_left} dni.")
    except ValueError:
        pass
    return {"read_only": ro, "withdraw": withdraw, "permissions": flat, "ips": ips, "expires": exp or None, "days_left": days_left, "note": info.get("note") or "", "warnings": warn, "notes": notes}


def collect(client, prev=None, now_ms=None, db=None, account_type="UNIFIED"):
    now_ms = now_ms or int(time.time() * 1000)
    prev = prev or {}
    out = {"ts": int(now_ms / 1000), "ok": True, "account_type": account_type}
    try:
        out["key"] = key_info(client.api_key_info())
    except Exception as e:  # noqa: BLE001 — informacja o kluczu jest dodatkiem
        out["key"] = {"error": str(e)[:120]}
    w = client.wallet_balance(account_type)
    equity = f(w.get("totalEquity"))
    coins = sorted(({"coin": c.get("coin"), "balance": f(c.get("walletBalance")), "usd": f(c.get("usdValue")), "upl": f(c.get("unrealisedPnl"))} for c in (w.get("coin") or []) if f(c.get("usdValue")) > 0.01 or f(c.get("walletBalance")) > 0), key=lambda x: -x["usd"])
    out["wallet"] = {"equity": equity, "wallet_balance": f(w.get("totalWalletBalance")), "available": f(w.get("totalAvailableBalance")), "upl": f(w.get("totalPerpUPL")),
                     "im": f(w.get("totalInitialMargin")), "mm": f(w.get("totalMaintenanceMargin")), "im_rate": f(w.get("accountIMRate")) * 100, "mm_rate": f(w.get("accountMMRate")) * 100, "coins": coins}
    pos = []
    for p in client.positions():
        if f(p.get("size")) <= 0:
            continue
        mark, liq = f(p.get("markPrice")), f(p.get("liqPrice"))
        pos.append({"symbol": p.get("symbol"), "side": p.get("side"), "size": f(p.get("size")), "entry": f(p.get("avgPrice")), "mark": mark, "value": f(p.get("positionValue")),
                    "leverage": f(p.get("leverage")), "upnl": f(p.get("unrealisedPnl")), "stop": f(p.get("stopLoss")) or None, "tp": f(p.get("takeProfit")) or None,
                    "liq": liq or None, "liq_dist_pct": round(abs(mark - liq) / mark * 100, 2) if mark and liq else None, "created": int(f(p.get("createdTime")) / 1000) or None})
    out["positions"] = sorted(pos, key=lambda x: -x["value"])
    notional = sum(p["value"] for p in pos)
    out["exposure"] = {"notional": round(notional, 2), "x": round(notional / equity, 3) if equity > 0 else None, "positions": len(pos), "without_stop": [p["symbol"] for p in pos if not p["stop"]]}
    out["orders"] = [{"symbol": o.get("symbol"), "side": o.get("side"), "type": o.get("orderType"), "qty": f(o.get("qty")), "price": f(o.get("price")) or None, "trigger": f(o.get("triggerPrice")) or None,
                      "reduce_only": bool(o.get("reduceOnly")), "created": int(f(o.get("createdTime")) / 1000)} for o in client.open_orders()]

    if db is not None:
        db.put_obs(EQUITY_METRIC, [(out["ts"], equity)], "bybit")
        with db.lock:
            rows = db.con.execute("SELECT ts, value FROM obs WHERE metric=? AND ts>=? ORDER BY ts", (EQUITY_METRIC, out["ts"] - 120 * 86400)).fetchall()
        byday = {}
        for t, v in rows:
            byday[day(t * 1000)] = [t, v]
        out["equity_curve"] = sorted(byday.values())[-120:]
    else:
        out["equity_curve"] = prev.get("equity_curve") or [[out["ts"], equity]]

    fresh = now_ms - int(prev.get("history_ts", 0)) * 1000 < HISTORY_EVERY_S * 1000 and prev.get("stats")
    if fresh:
        for k in ("stats", "by_symbol", "daily", "costs", "history_ts", "history_truncated"):
            if k in prev:
                out[k] = prev[k]
    else:
        cp = client.closed_pnl(days=max(PERIODS))
        rows = cp["records"]
        stats, by_sym, daily_all = {}, {}, {}
        for d in PERIODS:
            s, bs, dl = period_stats(rows, now_ms - d * 86_400_000)
            stats[str(d)], by_sym[str(d)] = s, bs
            if d == max(PERIODS):
                daily_all = dl
        out["stats"], out["by_symbol"], out["daily"] = stats, by_sym, [[k, round(v, 4)] for k, v in daily_all.items()]
        out["history_truncated"] = bool(cp.get("truncated"))
        fees = fund = 0.0
        try:
            for e in client.executions(days=30):
                fee = f(e.get("execFee"))
                if e.get("execType") == "Funding":
                    fund += fee
                else:
                    fees += fee
            out["costs"] = {"trading_fees_30d": round(fees, 4), "funding_30d": round(fund, 4), "note": "dodatnie = zapłacone"}
        except Exception as e:  # noqa: BLE001
            out["costs"] = {"error": str(e)[:100]}
        out["history_ts"] = out["ts"]
    return out


def write(d, obj):
    bb._atomic_write(os.path.join(d, OUT), json.dumps(obj, ensure_ascii=False, default=float))


def read(d):
    return bb._read_json(os.path.join(d, OUT))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--dir", default=HERE)
    a = ap.parse_args()
    ba.load_dotenv([os.path.join(HERE, ".env")])
    key = os.environ.get("BYBIT_LIVE_API_KEY") or os.environ.get("BYBIT_API_KEY")
    secret = os.environ.get("BYBIT_LIVE_API_SECRET") or os.environ.get("BYBIT_API_SECRET")
    prev = read(a.dir)
    if not (key and secret):
        write(a.dir, {"ts": int(time.time()), "ok": False, "error": "no_key"})
        return
    import btc_collect as bc
    client = ba.BybitClient(key, secret, os.environ.get("BYBIT_BASE_URL"))
    try:
        out = collect(client, prev if prev.get("ok") else None, db=bc.DB(a.db), account_type=os.environ.get("BYBIT_ACCOUNT_TYPE", "UNIFIED"))
    except Exception as e:  # noqa: BLE001 — poprzednie dane zostają, dopisujemy błąd
        out = dict(prev) if prev.get("ok") else {}
        out.update(ok=bool(prev.get("ok")), error=str(e)[:200], error_ts=int(time.time()))
        if not prev.get("ok"):
            out["ts"] = int(time.time())
    write(a.dir, out)


if __name__ == "__main__":
    main()
