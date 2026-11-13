#!/usr/bin/env python3
"""Demon strumieniowy: dane na żywo przez WebSockety (opóźnienie ułamka sekundy zamiast odpytywania co 10-60 s). Wszystko darmowe.

Źródła (każde w osobnym zadaniu z automatycznym ponawianiem połączenia):
  bybit_spot    ceny spot krypto (12 par z listy skanera) i token złota XAUT   (bez klucza)
  bybit_linear  BTC: cena marki, open interest, funding, TRANSAKCJE (CVD, duże zlecenia), LIKWIDACJE pozycji   (bez klucza)
  deribit       DVOL (zmienność implikowana) i indeks ceny BTC na żywo        (bez klucza)
  mempool       nowe bloki BTC i opłaty                                          (bez klucza)
  okx           zapas cen, gdy Bybit jest zablokowany w regionie                  (bez klucza)
  finnhub       akcje USA i waluty/złoto w czasie rzeczywistym                    (darmowy klucz FINNHUB_API_KEY, do 50 symboli)
  alpaca        akcje USA (IEX) w czasie rzeczywistym                             (darmowy klucz ALPACA_KEY_ID + ALPACA_SECRET)
Wyjście: stream.json (co sekundę, zapis atomowy) i co minutę wiersze w bazie (obs): likwidacje, CVD, DVOL.
Użycie:  python btc_stream.py --out /var/www/btc/stream.json --db /opt/btc/btc_data.db
Zależność: pip install websockets
"""
import argparse
import asyncio
import json
import math
import os
import signal
import sqlite3
import sys
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_markets as bm  # noqa: E402

try:
    import websockets
except ImportError:  # moduł logiki działa też bez biblioteki (testy)
    websockets = None

URLS = {"bybit_spot": "wss://stream.bybit.com/v5/public/spot", "bybit_linear": "wss://stream.bybit.com/v5/public/linear", "deribit": "wss://www.deribit.com/ws/api/v2",
        "mempool": "wss://mempool.space/api/v1/ws", "okx": "wss://ws.okx.com:8443/ws/v5/public", "finnhub": "wss://ws.finnhub.io", "alpaca": "wss://stream.data.alpaca.markets/v2/iex"}
CRYPTO = [s + "USDT" for s, _ in bm.CRYPTO]
LINEAR_LIQ = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
WHALE_USD = float(os.environ.get("STREAM_WHALE_USD", "250000"))
FINNHUB_MAP = {"OANDA:XAU_USD": "XAUUSD", "OANDA:XAG_USD": "XAGUSD", "OANDA:EUR_USD": "EURUSD", "OANDA:GBP_USD": "GBPUSD", "OANDA:USD_JPY": "USDJPY", "OANDA:USD_CHF": "USDCHF",
               "OANDA:AUD_USD": "AUDUSD", "OANDA:USD_CAD": "USDCAD", "OANDA:USD_PLN": "USDPLN", "OANDA:EUR_PLN": "EURPLN"}


class Agg:
    """Stan strumienia: ostatnie ceny oraz okna 1 h dla transakcji i likwidacji."""

    def __init__(self, now=time.time):
        self.now = now
        self.q, self.trades, self.liqs = {}, deque(), deque()
        self.dvol = self.index = self.block = self.fees = self.mark = None
        self.status = {}

    # --- ceny
    def price(self, key, price, src, chg24=None, **extra):
        if not (price and price > 0 and math.isfinite(price)):
            return
        cur = self.q.get(key, {})
        if cur.get("src") and cur["src"] != src and self.now() - cur.get("ts", 0) < 5 and cur["src"].startswith("ws:bybit"):
            return                                               # nie nadpisuj świeżego Bybit zapasem
        item = {"price": price, "ts": self.now(), "src": src, "chg24": chg24 if chg24 is not None else cur.get("chg24")}
        item.update({k: v for k, v in extra.items() if v is not None})
        self.q[key] = {**cur, **item}

    # --- przepływ
    def trade(self, taker_side, usd, ts=None):
        self.trades.append((ts or self.now(), 1 if taker_side.lower().startswith("b") else -1, usd))
        self._prune()

    def liquidation(self, pos_side, usd, symbol, ts=None):
        self.liqs.append((ts or self.now(), "long" if pos_side.lower().startswith("b") else "short", usd, symbol))
        self._prune()

    def _prune(self):
        lim = self.now() - 3600
        while self.trades and self.trades[0][0] < lim:
            self.trades.popleft()
        while self.liqs and self.liqs[0][0] < lim:
            self.liqs.popleft()

    def flow(self):
        t = self.now()
        self._prune()

        def win(sec):
            tr = [x for x in self.trades if x[0] >= t - sec]
            buy = sum(u for _, s, u in tr if s > 0)
            sell = sum(u for _, s, u in tr if s < 0)
            big = [x for x in tr if x[2] >= WHALE_USD]
            lq = [x for x in self.liqs if x[0] >= t - sec]
            return {"buy_usd": buy, "sell_usd": sell, "cvd_usd": buy - sell, "trades": len(tr), "whale_trades": len(big), "whale_net_usd": sum(s * u for _, s, u in big),
                    "liq_long_usd": sum(u for _, side, u, _ in lq if side == "long"), "liq_short_usd": sum(u for _, side, u, _ in lq if side == "short"), "liq_count": len(lq)}
        return {"1m": win(60), "5m": win(300), "1h": win(3600), "whale_threshold_usd": WHALE_USD}

    def snapshot(self):
        t = self.now()
        for k, v in self.q.items():
            v["age"] = round(t - v["ts"], 2)
        return {"ts": t, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), "quotes": self.q, "flow": {"btc": self.flow()}, "dvol": self.dvol, "deribit_index": self.index,
                "bybit_mark": self.mark, "block": self.block, "fees": self.fees, "providers": {k: {**v, "age": round(t - v["last"], 1) if v.get("last") else None} for k, v in self.status.items()}}


# ----------------------------------------------------------------------------
# Obsługa komunikatów (czyste funkcje, testowalne bez sieci)
# ----------------------------------------------------------------------------
def f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def handle_bybit_spot(agg, m, state):
    if not str(m.get("topic", "")).startswith("tickers."):
        return
    d = m.get("data") or {}
    sym = d.get("symbol") or m["topic"].split(".", 1)[1]
    st = state.setdefault(sym, {})
    st.update({k: v for k, v in d.items() if v not in (None, "")})            # delty zawierają tylko zmienione pola
    p = f(st.get("lastPrice"))
    chg = f(st.get("price24hPcnt"))
    agg.price(sym, p, "ws:bybit", chg * 100 if chg is not None else None)


def handle_bybit_linear(agg, m, state):
    topic = str(m.get("topic", ""))
    data = m.get("data")
    if topic.startswith("tickers."):
        sym = topic.split(".", 1)[1]
        st = state.setdefault(sym, {})
        st.update({k: v for k, v in (data or {}).items() if v not in (None, "")})
        if sym == "BTCUSDT":
            agg.mark = {"mark": f(st.get("markPrice")), "index": f(st.get("indexPrice")), "oi_usd": f(st.get("openInterestValue")), "oi_btc": f(st.get("openInterest")),
                        "funding_pct": (f(st.get("fundingRate")) or 0) * 100 if st.get("fundingRate") is not None else None, "ts": agg.now()}
    elif topic.startswith("publicTrade."):
        for t in data if isinstance(data, list) else []:
            p, v = f(t.get("p")), f(t.get("v"))
            if p and v and str(t.get("s", "")).startswith("BTC"):
                agg.trade(str(t.get("S", "")), p * v, (f(t.get("T")) or agg.now() * 1000) / 1000)
    elif topic.startswith("allLiquidation."):
        for t in data if isinstance(data, list) else [data] if isinstance(data, dict) else []:
            p, v = f(t.get("p")), f(t.get("v"))
            if p and v:
                agg.liquidation(str(t.get("S", "")), p * v, t.get("s", ""), (f(t.get("T")) or agg.now() * 1000) / 1000)


def handle_deribit(agg, m):
    p = m.get("params") or {}
    ch, d = p.get("channel", ""), p.get("data") or {}
    if ch.startswith("deribit_volatility_index") and f(d.get("volatility")):
        agg.dvol = {"value": f(d["volatility"]), "ts": agg.now()}
    elif ch.startswith("deribit_price_index") and f(d.get("price")):
        agg.index = {"price": f(d["price"]), "ts": agg.now()}


def handle_mempool(agg, m):
    b = m.get("block")
    blocks = m.get("blocks")
    if not b and isinstance(blocks, list) and blocks:
        b = blocks[-1]
    if isinstance(b, dict) and b.get("height"):
        agg.block = {"height": b["height"], "tx_count": b.get("tx_count"), "ts": b.get("timestamp"), "seen": agg.now()}
    if isinstance(m.get("fees"), dict):
        agg.fees = {k: m["fees"].get(k) for k in ("fastestFee", "halfHourFee", "hourFee", "economyFee")}


def handle_okx(agg, m):
    if (m.get("arg") or {}).get("channel") != "tickers":
        return
    for d in m.get("data") or []:
        inst = d.get("instId", "")
        last, o = f(d.get("last")), f(d.get("open24h"))
        if inst.endswith("-USDT") and last:
            agg.price(inst.replace("-", ""), last, "ws:okx", (last / o - 1) * 100 if o else None)


def handle_finnhub(agg, m):
    if m.get("type") != "trade":
        return
    for t in m.get("data") or []:
        s = t.get("s", "")
        key = FINNHUB_MAP.get(s) or (s + ".US" if ":" not in s else None)
        if key:
            agg.price(key, f(t.get("p")), "ws:finnhub")


def handle_alpaca(agg, m):
    for t in m if isinstance(m, list) else []:
        if t.get("T") == "t" and t.get("S"):
            agg.price(t["S"] + ".US", f(t.get("p")), "ws:alpaca")


# ----------------------------------------------------------------------------
# Zadania sieciowe
# ----------------------------------------------------------------------------
async def run_provider(name, agg, url, on_open, on_msg, stop, ping=None, max_backoff=30.0, gate=None):
    """Łączy się, subskrybuje i czyta komunikaty; po błędzie czeka (wykładniczo) i łączy ponownie."""
    st = agg.status.setdefault(name, {"connected": False, "msgs": 0, "reconnects": 0, "error": None, "last": None})
    n = 0
    while not stop.is_set():
        if gate is not None and not gate():                       # zapas: czekaj, aż główne źródło zamilknie
            st.update(connected=False, error="oczekuje (główne źródło działa)")
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            async with websockets.connect(url, open_timeout=12, ping_interval=None, max_size=2 ** 22) as ws:
                st.update(connected=True, error=None)
                n = 0
                await on_open(ws)
                pinger = asyncio.create_task(_pinger(ws, ping)) if ping else None
                try:
                    async for raw in ws:
                        st["msgs"] += 1
                        st["last"] = agg.now()
                        try:
                            m = json.loads(raw)
                        except ValueError:
                            continue
                        on_msg(m)
                        if stop.is_set():
                            return
                finally:
                    if pinger:
                        pinger.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            st["error"] = f"{type(e).__name__}: {str(e)[:80]}"
        st["connected"] = False
        st["reconnects"] += 1
        n += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=min(max_backoff, 1.5 ** n))
        except asyncio.TimeoutError:
            pass


async def _pinger(ws, msg, every=20):
    while True:
        await asyncio.sleep(every)
        await ws.send(msg)


def chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def make_tasks(agg, stop, urls=None, env=os.environ):
    urls = {**URLS, **(urls or {})}
    st_spot, st_lin = {}, {}
    tasks = []

    async def bybit_spot_open(ws):
        for part in chunks([f"tickers.{s}" for s in CRYPTO + ["XAUTUSDT"]], 10):             # limit 10 tematów na zapytanie (spot)
            await ws.send(json.dumps({"op": "subscribe", "args": part}))

    async def bybit_linear_open(ws):
        for args in (["tickers.BTCUSDT", "publicTrade.BTCUSDT"], [f"allLiquidation.{s}" for s in LINEAR_LIQ]):    # osobno: błędny temat unieważnia całe zapytanie
            await ws.send(json.dumps({"op": "subscribe", "args": args}))

    async def deribit_open(ws):
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "public/subscribe", "params": {"channels": ["deribit_volatility_index.btc_usd", "deribit_price_index.btc_usd"]}}))

    async def mempool_open(ws):
        await ws.send(json.dumps({"action": "want", "data": ["blocks", "stats"]}))

    async def okx_open(ws):
        await ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "tickers", "instId": s.replace("USDT", "-USDT")} for s in CRYPTO + ["XAUTUSDT"]]}))

    started = agg.now()
    okx_after = float(env.get("STREAM_OKX_AFTER", "15"))

    def okx_gate():
        """OKX łączy się dopiero, gdy Bybit nie dostarcza danych od co najmniej okx_after sekund (od startu albo od ostatniego komunikatu)."""
        last = max(agg.status.get("bybit_spot", {}).get("last") or 0, started)
        return agg.now() - last >= okx_after

    tasks += [run_provider("bybit_spot", agg, urls["bybit_spot"], bybit_spot_open, lambda m: handle_bybit_spot(agg, m, st_spot), stop, ping='{"op":"ping"}'),
              run_provider("bybit_linear", agg, urls["bybit_linear"], bybit_linear_open, lambda m: handle_bybit_linear(agg, m, st_lin), stop, ping='{"op":"ping"}'),
              run_provider("deribit", agg, urls["deribit"], deribit_open, lambda m: handle_deribit(agg, m), stop),
              run_provider("mempool", agg, urls["mempool"], mempool_open, lambda m: handle_mempool(agg, m), stop),
              run_provider("okx", agg, urls["okx"], okx_open, lambda m: handle_okx(agg, m), stop, ping="ping",
                           gate=okx_gate)]
    key = env.get("FINNHUB_API_KEY")
    if key:
        async def finnhub_open(ws):
            syms = list(FINNHUB_MAP) + [s for s, m, _, _ in bm.load_watchlist() if m == "US"]
            for s in syms[:50]:                                                              # limit planu darmowego: 50 symboli
                await ws.send(json.dumps({"type": "subscribe", "symbol": s}))
        tasks.append(run_provider("finnhub", agg, f"{urls['finnhub']}?token={key}", finnhub_open, lambda m: handle_finnhub(agg, m), stop))
    if env.get("ALPACA_KEY_ID") and env.get("ALPACA_SECRET"):
        async def alpaca_open(ws):
            await ws.send(json.dumps({"action": "auth", "key": env["ALPACA_KEY_ID"], "secret": env["ALPACA_SECRET"]}))
            await ws.send(json.dumps({"action": "subscribe", "trades": [s for s, m, _, _ in bm.load_watchlist() if m == "US"][:30]}))
        tasks.append(run_provider("alpaca", agg, urls["alpaca"], alpaca_open, lambda m: handle_alpaca(agg, m), stop))
    return tasks


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"), default=float)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def log_db(db_path, snap):
    """Co minutę zapisuje do bazy metryki przepływu (historia do badań)."""
    fl = snap["flow"]["btc"]
    rows = [("liq_long_usd_1h", fl["1h"]["liq_long_usd"]), ("liq_short_usd_1h", fl["1h"]["liq_short_usd"]), ("cvd_btc_5m_usd", fl["5m"]["cvd_usd"]), ("whale_net_5m_usd", fl["5m"]["whale_net_usd"])]
    if snap.get("dvol"):
        rows.append(("dvol_live", snap["dvol"]["value"]))
    if snap.get("bybit_mark") and snap["bybit_mark"].get("oi_usd"):
        rows.append(("oi_bybit_usd_live", snap["bybit_mark"]["oi_usd"]))
    if snap.get("bybit_mark") and snap["bybit_mark"].get("funding_pct") is not None:
        rows.append(("funding_bybit_live", snap["bybit_mark"]["funding_pct"]))
    ts = int(snap["ts"])
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS obs(metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL, source TEXT, PRIMARY KEY(metric, ts)) WITHOUT ROWID")
        con.executemany("INSERT OR REPLACE INTO obs(metric, ts, value, source) VALUES(?,?,?,?)", [(m, ts, v, "stream") for m, v in rows if v is not None])
        con.commit()
    finally:
        con.close()


async def writer(agg, out, db, stop, every=1.0, db_every=60.0):
    last_db = 0.0
    while not stop.is_set():
        snap = agg.snapshot()
        try:
            write_json(out, snap)
            if db and time.time() - last_db >= db_every and any(v.get("connected") for v in agg.status.values()):
                log_db(db, snap)
                last_db = time.time()
        except Exception as e:  # noqa: BLE001
            agg.status.setdefault("writer", {})["error"] = str(e)[:80]
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass


async def amain(out, db, urls=None, run_seconds=None):
    agg, stop = Agg(), asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    tasks = [asyncio.create_task(t) for t in make_tasks(agg, stop, urls)] + [asyncio.create_task(writer(agg, out, db, stop))]
    if run_seconds:
        await asyncio.sleep(run_seconds)
        stop.set()
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return agg


def main():
    if websockets is None:
        sys.exit("Brak biblioteki: pip install websockets")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "stream.json"))
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--seconds", type=float, help="zakończ po tylu sekundach (test)")
    a = ap.parse_args()
    try:
        import btc_analysis as ba
        ba.load_dotenv([os.path.join(HERE, ".env")])
    except Exception:  # noqa: BLE001
        pass
    agg = asyncio.run(amain(a.out, a.db if os.path.exists(a.db) else None, run_seconds=a.seconds))
    if a.seconds:
        print(json.dumps(agg.snapshot(), indent=1, default=float)[:3000])


if __name__ == "__main__":
    main()
