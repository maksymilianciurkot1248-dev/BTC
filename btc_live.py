#!/usr/bin/env python3
"""Notowania na bieżąco do live.json (czytane przez strony co kilkanaście sekund).

Źródła: Bybit (spot, jedno zapytanie na wszystkie pary: BTC, ETH, XAUT = złoto 24/7) co cykl,
Stooq (jedno zapytanie zbiorcze: złoto, srebro, EUR/USD, USD/PLN, S&P 500, DXY, ropa) co 60 s.
Użycie:  python btc_live.py --out /var/www/btc/live.json [--interval 10] [--once]
"""
import argparse
import csv
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_net  # noqa: E402
import btc_markets as bm  # noqa: E402

BYBIT = {sym + "USDT" for sym, _ in bm.CRYPTO} | {"XAUTUSDT"}


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def okx_quotes(get):
    """Zapas dla Bybit: zbiorcze notowania spot z OKX (pary USDT z listy skanera oraz XAUT = token złota)."""
    out = {}
    for t in (get("https://www.okx.com/api/v5/market/tickers?instType=SPOT", timeout=8, retries=1).get("data")) or []:
        inst = t.get("instId", "")
        key = inst.replace("-", "")
        if inst.endswith("-USDT") and key in BYBIT and f(t.get("last")):
            o = f(t.get("open24h"))
            out[key] = {"price": f(t["last"]), "chg24": (f(t["last"]) / o - 1) * 100 if o else None, "src": "okx"}
    return out


def bybit_quotes(get):
    res = get("https://api.bybit.com/v5/market/tickers?category=spot", timeout=6, retries=1)
    out = {}
    for t in (res.get("result") or {}).get("list") or []:
        if t.get("symbol") in BYBIT and f(t.get("lastPrice")):
            out[t["symbol"]] = {"price": f(t["lastPrice"]), "chg24": (f(t.get("price24hPcnt")) or 0) * 100, "src": "bybit"}
    return out


def parse_stooq(text, mapping=None):
    mapping = mapping or bm.stooq_map()
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        sym = (r.get("Symbol") or "").lower()
        if sym in mapping and f(r.get("Close")):
            o = f(r.get("Open"))
            out[mapping[sym]] = {"price": f(r["Close"]), "chg_open": (f(r["Close"]) / o - 1) * 100 if o else None, "t": f"{r.get('Date', '')} {r.get('Time', '')}".strip(), "src": "stooq"}
    return out


def stooq_quotes(get, chunk=12):
    """Notowania zbiorcze (po 'chunk' symboli w zapytaniu, równolegle). Gdy zbiorczy format zawiedzie, pojedyncze zapytania."""
    mapping = bm.stooq_map()
    codes = list(mapping)
    def part(cs):
        try:
            got = parse_stooq(get("https://stooq.com/q/l/?s=" + "+".join(cs) + "&f=sd2t2ohlcv&h&e=csv", as_text=True, timeout=8, retries=1), mapping)
        except RuntimeError:
            got = {}
        if len(got) < max(1, len(cs) // 3):                  # zapas: pojedyncze zapytania
            for c1 in cs:
                try:
                    got.update(parse_stooq(get(f"https://stooq.com/q/l/?s={c1}&f=sd2t2ohlcv&h&e=csv", as_text=True, timeout=8, retries=0), mapping))
                except RuntimeError:
                    pass
        return got
    out = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        for got in ex.map(part, [codes[i:i + chunk] for i in range(0, len(codes), chunk)]):
            out.update(got)
    return out


def read_stream(path, max_age=15.0):
    """Notowania ze stream.json (WebSocket), jeśli plik jest świeży."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if time.time() - d.get("ts", 0) <= max_age and d.get("quotes"):
            return {k: {"price": v["price"], "chg24": v.get("chg24"), "src": v.get("src", "ws")} for k, v in d["quotes"].items() if v.get("price")}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def snapshot(get, prev=None, do_stooq=True, extra=None, stream=None):
    q = dict((prev or {}).get("quotes", {}))
    errs = []
    got = {}
    if stream:
        q.update(stream)                                  # WebSocket: świeże, REST dla krypto niepotrzebny
        got = stream
    for name, fn in (("bybit", bybit_quotes), ("okx", okx_quotes)):
        if stream or (name == "okx" and got):
            continue                                     # strumień działa albo Bybit odpowiedział: bez zapasu
        try:
            got = fn(get)
            q.update(got)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {e}")
    if do_stooq:
        try:
            q.update(stooq_quotes(get))
        except Exception as e:  # noqa: BLE001
            errs.append(f"stooq: {e}")
    if extra:
        q.update(extra)                                  # notowania Stooq z osobnego wątku
    xaut = q.get("XAUTUSDT")                             # złoto na żywo z tokenu, gdy Stooq nie dał XAU/USD
    if xaut and (("XAUUSD" not in q) or q["XAUUSD"].get("src") == "xaut"):
        q["XAUUSD"] = {"price": xaut["price"], "chg_open": xaut.get("chg24"), "src": "xaut"}
    return {"ts": int(time.time()), "iso": datetime.now(timezone.utc).isoformat(timespec="seconds"), "quotes": q, "errors": errs}


def write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


class StooqWorker(threading.Thread):
    """Stooq w osobnym wątku: jego powolne lub odrzucane zapytania nie blokują odświeżania krypto.
    Po 3 nieudanych cyklach z rzędu pauza 10 minut (wyłącznik awaryjny)."""

    def __init__(self, get, every):
        super().__init__(daemon=True)
        self.get, self.every, self.quotes, self.fail, self.status = get, every, {}, 0, "start"

    def run(self):
        while True:
            t0 = time.time()
            try:
                q = stooq_quotes(self.get)
                if q:
                    self.quotes, self.fail, self.status = {**self.quotes, **q}, 0, f"ok ({len(q)})"
                else:
                    self.fail, self.status = self.fail + 1, "pusto"
            except Exception as e:  # noqa: BLE001
                self.fail, self.status = self.fail + 1, str(e)[:80]
            wait = 600.0 if self.fail >= 3 else self.every
            time.sleep(max(1.0, wait - (time.time() - t0)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "live.json"))
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--stooq-every", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stream", default=None, help="plik stream.json z btc_stream.py (domyślnie obok --out)")
    ap.add_argument("--with-stooq", action="store_true", help="w trybie --once pobierz też Stooq")
    a = ap.parse_args()
    worker = None
    if not a.once or a.with_stooq:
        worker = StooqWorker(btc_net.get, a.stooq_every)
        worker.start()
        if a.once:
            worker.join(timeout=60)
    prev = None
    stream_path = a.stream or os.path.join(os.path.dirname(os.path.abspath(a.out)), "stream.json")
    while True:
        t0 = time.time()
        prev = snapshot(btc_net.get, prev, do_stooq=False, extra=worker.quotes if worker else None, stream=read_stream(stream_path))
        if worker:
            prev["stooq"] = worker.status
        write(a.out, prev)
        if a.once:
            print(json.dumps(prev)[:600])
            return
        time.sleep(max(1.0, a.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
