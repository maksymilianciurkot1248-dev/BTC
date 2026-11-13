#!/usr/bin/env python3
"""Kolektor danych do bazy SQLite (btc_data.db) — historia do analizy.

Użycie:
    python btc_collect.py backfill        # jednorazowo: pobiera całą dostępną historię (kilka minut)
    python btc_collect.py update          # cyklicznie (cron/systemd co godzinę): migawki + uzupełnianie luk
    python btc_collect.py status          # co jest w bazie
    python btc_collect.py update --only fng,dvol     # tylko wybrane źródła
Klucze API (opcjonalne, w .env obok skryptu; źródło bez klucza jest po prostu pomijane):
    FRED_API_KEY, CMC_API_KEY, GLASSNODE_API_KEY, WHALE_ALERT_API_KEY, FINNHUB_API_KEY (fundamenty spółek USA), TIINGO_API_KEY (zapas dla cen USA)
Baza ma 4 tabele: obs (szeregi czasowe metric/ts/value), candles (OHLCV), runs (dziennik zbierania), meta.
Wszystkie zapisy są idempotentne (INSERT OR REPLACE), więc ponowne uruchomienie niczego nie psuje.
"""
import argparse
import csv
import io
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_extras as bx  # noqa: E402
import btc_markets as bm  # noqa: E402

DAY = 86400
DB_DEFAULT = os.path.join(HERE, "btc_data.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS obs(metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL, source TEXT,
    PRIMARY KEY(metric, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS candles(symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL, v REAL, source TEXT, PRIMARY KEY(symbol, interval, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS events(kind TEXT NOT NULL, ts INTEGER NOT NULL, label TEXT, PRIMARY KEY(kind, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, source TEXT, ok INTEGER, rows INTEGER,
    seconds REAL, error TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS runs_src ON runs(source, ts);
"""


class DB:
    """SQLite z jedną blokadą: źródła pobierają dane równolegle, a zapisy są szeregowane."""

    def __init__(self, path=DB_DEFAULT):
        self.lock = threading.RLock()
        self.con = sqlite3.connect(path, timeout=60, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)

    def _many(self, sql, rows):
        with self.lock:
            self.con.executemany(sql, rows)
            self.con.commit()
        return len(rows)

    def put_obs(self, metric, rows, source):
        rows = [(metric, int(t), float(v), source) for t, v in rows if v is not None and v == v]
        return self._many("INSERT OR REPLACE INTO obs(metric, ts, value, source) VALUES(?,?,?,?)", rows)

    def put_candles(self, symbol, interval, rows, source):
        return self._many("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?)",
                          [(symbol, interval, int(r[0]), r[1], r[2], r[3], r[4], r[5], source) for r in rows])

    def put_events(self, kind, rows):
        return self._many("INSERT OR REPLACE INTO events(kind, ts, label) VALUES(?,?,?)", [(kind, int(t), lab) for t, lab in rows])

    def _one(self, sql, args=()):
        with self.lock:
            return self.con.execute(sql, args).fetchone()[0]

    def last_obs_ts(self, metric):
        return self._one("SELECT MAX(ts) FROM obs WHERE metric=?", (metric,))

    def last_candle_ts(self, symbol, interval):
        return self._one("SELECT MAX(ts) FROM candles WHERE symbol=? AND interval=?", (symbol, interval))

    def log_run(self, source, ok, rows, seconds, error=None):
        with self.lock:
            self.con.execute("INSERT INTO runs(ts, source, ok, rows, seconds, error) VALUES(?,?,?,?,?,?)",
                             (int(time.time()), source, int(ok), rows, round(seconds, 2), (error or "")[:300]))
            self.con.commit()

    def last_ok_run(self, source):
        return self._one("SELECT MAX(ts) FROM runs WHERE source=? AND ok=1", (source,))


class Ctx:
    def __init__(self, db, get, now, full, post=None):
        self.db, self.get, self.now, self.full = db, get, now, full
        import btc_net
        self.post = post or btc_net.post_json

    @property
    def now_s(self):
        return int(self.now.timestamp())


def day_floor(ts):
    return int(ts) // DAY * DAY


def _start_from(ctx, metric, default_ts, overlap_days=5):
    last = None if ctx.full else ctx.db.last_obs_ts(metric)
    return max(default_ts, last - overlap_days * DAY) if last else default_ts


# ----------------------------------------------------------------------------
# Źródła bez klucza
# ----------------------------------------------------------------------------
def _geo_blocked(e):
    t = str(e)
    return any(x in t for x in ("451", "403", "połączenie", "timed out", "reset"))


def _klines(c, sym, start_default_ms, flow_metrics=False):
    """Dzienne świece z Binance (z taker buy ratio dla BTC); gdy Binance blokuje region lub nie odpowiada, z OKX (bez metryk przepływu)."""
    try:
        return _klines_binance(c, sym, start_default_ms, flow_metrics)
    except RuntimeError as e:
        if not _geo_blocked(e):
            raise
        inst = sym.replace("USDT", "-USDT")
        last = None if c.full else c.db.last_candle_ts(sym, "1d")
        return c.db.put_candles(sym, "1d", bm.fetch_okx_daily(c.get, inst, last), "okx")


def _klines_binance(c, sym, start_default_ms, flow_metrics=False):
    n = 0
    last = None if c.full else c.db.last_candle_ts(sym, "1d")
    start = (last - 5 * DAY) * 1000 if last else start_default_ms
    now_ms = int(c.now.timestamp() * 1000)
    while True:
        rows = c.get(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&limit=1000&startTime={start}")
        if not rows:
            break
        done = [r for r in rows if int(r[6]) <= now_ms]      # tylko zamknięte świece
        c.db.put_candles(sym, "1d", [(int(r[0]) // 1000, *map(float, r[1:6])) for r in done], "binance")
        if flow_metrics:
            c.db.put_obs("taker_buy_ratio", [(int(r[0]) // 1000, float(r[9]) / float(r[5])) for r in done if float(r[5]) > 0], "binance")
            c.db.put_obs("trades_count", [(int(r[0]) // 1000, float(r[8])) for r in done], "binance")
            c.db.put_obs("quote_volume_usd", [(int(r[0]) // 1000, float(r[7])) for r in done], "binance")
        n += len(done)
        if len(rows) < 1000:
            break
        start = int(rows[-1][0]) + 1
    return n


def s_binance_klines(c):
    return _klines(c, "BTCUSDT", 1502928000000, True)       # 2017-08-17


def s_klines_eth(c):
    return _klines(c, "ETHUSDT", 1502928000000)


def s_klines_crypto(c):
    """Dzienne świece pozostałych kryptowalut z listy skanera (BTC i ETH mają własne źródła)."""
    n, errs = 0, []
    for sym, _ in bm.CRYPTO:
        if sym in ("BTC", "ETH"):
            continue
        try:
            n += _klines(c, sym + "USDT", 1502928000000)
        except RuntimeError as e:
            errs.append(f"{sym}: {e}")
    if not n:
        raise RuntimeError("; ".join(errs[:4]) or "brak danych")
    return n


def s_klines_paxg(c):
    """PAXG (token złota) handluje 24/7: pokazuje ruch złota także w weekendy."""
    return _klines(c, "PAXGUSDT", 1599000000000)


def s_binance_funding(c):
    last = None if c.full else c.db.last_obs_ts("funding_binance_8h")
    start = (last - DAY) * 1000 if last else 1567987200000  # 2019-09-09
    n = 0
    while True:
        rows = c.get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000&startTime={start}")
        if not rows:
            break
        n += c.db.put_obs("funding_binance_8h", [(int(r["fundingTime"]) // 1000, float(r["fundingRate"]) * 100) for r in rows], "binance")
        if len(rows) < 1000:
            break
        start = int(rows[-1]["fundingTime"]) + 1
        time.sleep(0.2)
    return n


def s_binance_futures_daily(c):
    """Binance udostępnia tylko ~30 dni historii tych serii: zbieramy je codziennie i budujemy własną historię."""
    b, n = "https://fapi.binance.com/futures/data", 0
    for metric, path, field in (("oi_binance_usd", "openInterestHist", "sumOpenInterestValue"),
                                ("oi_binance_btc", "openInterestHist", "sumOpenInterest"),
                                ("ls_binance_accounts", "globalLongShortAccountRatio", "longShortRatio"),
                                ("ls_binance_top_positions", "topLongShortPositionRatio", "longShortRatio"),
                                ("taker_ratio_binance_futures", "takerlongshortRatio", "buySellRatio")):
        try:
            rows = c.get(f"{b}/{path}?symbol=BTCUSDT&period=1d&limit=30")
            n += c.db.put_obs(metric, [(int(r["timestamp"]) // 1000, float(r[field])) for r in rows], "binance")
        except RuntimeError:
            continue
    if not n:
        raise RuntimeError("Binance futures data niedostępne")
    return n


def s_fng(c):
    rows = c.get("https://api.alternative.me/fng/?limit=0&format=json").get("data") or []
    return c.db.put_obs("fng", [(day_floor(int(r["timestamp"])), float(r["value"])) for r in rows], "alternative.me")


def s_dvol(c):
    start = _start_from(c, "dvol", 1616025600)  # 2021-03-18
    n, t = 0, start
    while t < c.now_s:
        end = min(t + 400 * DAY, c.now_s)
        res = c.get(f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1D"
                    f"&start_timestamp={t * 1000}&end_timestamp={end * 1000}")
        rows = (res.get("result") or {}).get("data") or []
        n += c.db.put_obs("dvol", [(day_floor(int(r[0]) // 1000), float(r[4])) for r in rows], "deribit")
        t = end
    return n


def s_stablecoins(c):
    rows = c.get("https://stablecoins.llama.fi/stablecoincharts/all")
    out = []
    for r in rows:
        v = (r.get("totalCirculatingUSD") or r.get("totalCirculating") or {}).get("peggedUSD")
        if v is not None:
            out.append((day_floor(int(r["date"])), float(v)))
    return c.db.put_obs("stable_total_usd", out, "defillama")


def s_hashrate(c):
    res = c.get("https://mempool.space/api/v1/mining/hashrate/3y")
    n = c.db.put_obs("hashrate_ehs", [(day_floor(int(r["timestamp"])), float(r["avgHashrate"]) / 1e18) for r in res.get("hashrates") or []], "mempool")
    n += c.db.put_obs("difficulty", [(day_floor(int(r["time"])), float(r["difficulty"])) for r in res.get("difficulty") or [] if "time" in r], "mempool")
    return n


def s_wikipedia(c):
    start = datetime.fromtimestamp(_start_from(c, "wiki_bitcoin_views", 1435708800), timezone.utc)  # 2015-07-01
    end = c.now - timedelta(days=1)
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Bitcoin/daily/"
           f"{start:%Y%m%d}/{end:%Y%m%d}")
    items = c.get(url).get("items") or []
    return c.db.put_obs("wiki_bitcoin_views", [(day_floor(int(datetime.strptime(i["timestamp"][:8], "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())),
                                                float(i["views"])) for i in items], "wikimedia")


STOOQ = {"spx": ["^spx"], "ndx": ["^ndx"], "gold": ["xauusd"], "dxy": ["^dxy", "dx.f"], "us10y": ["10usy.b"], "oil": ["cl.f"]}


# zapasowe źródło dla serii ze Stooq (Stooq bywa nieosiągalny z serwerów w chmurze): Yahoo Finance, wykres dzienny, bez klucza
YAHOO = {"spx": "^GSPC", "ndx": "^NDX", "gold": "GC=F", "dxy": "DX-Y.NYB", "us10y": "^TNX", "oil": "CL=F"}


def yahoo_daily(get, symbol, years=2):
    """Zamknięcia dzienne [(ts_0:00_UTC, cena)] z Yahoo Finance; puste, gdy odpowiedź nie ma serii."""
    from urllib.parse import quote
    d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range={years}y&interval=1d")
    res = ((d.get("chart") or {}).get("result") or [None])[0] or {}
    ts, cl = res.get("timestamp") or [], (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    out = {}
    for t, v in zip(ts, cl):
        v = bx.f(v)
        if v:
            out[day_floor(int(t))] = v
    return sorted(out.items())


def s_stooq(c):
    """Seria makro: najpierw Stooq, a gdy nie odpowiada, Yahoo Finance (te same nazwy metryk stooq_*, źródło zapisane w bazie)."""
    n = 0
    for key, symbols in STOOQ.items():
        done = False
        for sym in symbols:
            try:
                text = c.get(f"https://stooq.com/q/d/l/?s={sym}&i=d", as_text=True)
                rows = []
                for r in csv.DictReader(io.StringIO(text)):
                    cl = bx.f(r.get("Close"))
                    if r.get("Date") and cl:
                        rows.append((int(datetime.strptime(r["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()), cl))
                if len(rows) > 20:
                    n += c.db.put_obs(f"stooq_{key}", rows, "stooq")
                    done = True
                    break
            except (RuntimeError, ValueError):
                continue
        if not done and key in YAHOO:
            try:
                rows = yahoo_daily(c.get, YAHOO[key])
                if len(rows) > 20:
                    n += c.db.put_obs(f"stooq_{key}", rows, "yahoo")
            except (RuntimeError, ValueError, KeyError, TypeError, AttributeError):
                continue
    if not n:
        raise RuntimeError("Stooq ani Yahoo nie odpowiadają")
    return n


def s_snapshot(c):
    """Migawki 'teraz' (co godzinę): zapisują się z bieżącym czasem i budują własną historię."""
    now, n, errs = c.now_s, 0, []
    try:
        d = bx.src_derivs(c.get)
        b, o = d.get("binance") or {}, d.get("okx") or {}
        for m, v in (("funding_binance_now", b.get("funding_pct")), ("funding_okx_now", o.get("funding_pct")),
                     ("oi_binance_usd_now", b.get("oi_usd")), ("oi_okx_usd_now", o.get("oi_usd")), ("basis_binance_pct", b.get("basis_pct")),
                     ("ls_okx_accounts_now", (o.get("ls_accounts") or {}).get("now"))):
            if v is not None:
                n += c.db.put_obs(m, [(now, v)], "snapshot")
    except RuntimeError as e:
        errs.append(str(e))
    try:
        p = bx.src_premium(c.get)
        for m, v in (("prem_coinbase_binance_pct", p.get("coinbase_vs_binance_pct")), ("prem_coinbase_kraken_pct", p.get("coinbase_vs_kraken_pct")),
                     ("spot_spread_pct", p.get("spread_pct"))):
            if v is not None:
                n += c.db.put_obs(m, [(now, v)], "snapshot")
    except RuntimeError as e:
        errs.append(str(e))
    try:
        oc = bx.src_onchain(c.get)
        for m, v in (("fee_half_hour_sat_vb", (oc.get("fees_sat_vb") or {}).get("half_hour")), ("mempool_tx_count", (oc.get("mempool") or {}).get("tx_count")),
                     ("mempool_vsize_mb", (oc.get("mempool") or {}).get("vsize_mb"))):
            if v is not None:
                n += c.db.put_obs(m, [(now, v)], "snapshot")
    except RuntimeError as e:
        errs.append(str(e))
    try:
        op = bx.src_options(c.get, c.now)
        for m, v in (("put_call_oi", op.get("put_call_oi")), ("put_call_volume", op.get("put_call_volume")), ("options_oi_btc", op.get("oi_btc"))):
            if v is not None:
                n += c.db.put_obs(m, [(now, v)], "snapshot")
    except RuntimeError as e:
        errs.append(str(e))
    if not n:
        raise RuntimeError("; ".join(errs) or "brak danych")
    return n


# ----------------------------------------------------------------------------
# Źródła z kluczem (Twoje darmowe klucze; bez klucza źródło jest pomijane)
# ----------------------------------------------------------------------------
FRED_DEFAULT = ("DGS10,DGS2,DFII10,T10YIE,T10Y2Y,VIXCLS,DFF,DTWEXBGS,DEXUSEU,DCOILWTICO,M2SL,WALCL,RRPONTSYD,BAMLH0A0HYM2,"
                "ICSA,UNRATE,CPIAUCSL,PAYEMS")


def s_fred(c):
    key = os.environ.get("FRED_API_KEY")
    n, errs = 0, []
    for sid in (os.environ.get("FRED_SERIES") or FRED_DEFAULT).split(","):
        sid = sid.strip()
        if not sid:
            continue
        start = datetime.fromtimestamp(_start_from(c, f"fred_{sid}", 1420070400, 60), timezone.utc)  # od 2015
        try:
            obs = c.get(f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}&api_key={key}&file_type=json"
                        f"&observation_start={start:%Y-%m-%d}").get("observations") or []
            rows = [(int(datetime.strptime(o["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()), float(o["value"]))
                    for o in obs if o.get("value") not in (None, ".", "")]
            n += c.db.put_obs(f"fred_{sid}", rows, "fred")
        except (RuntimeError, ValueError) as e:
            errs.append(f"{sid}: {str(e).replace(str(key), '***')}")
    if not n:
        raise RuntimeError("; ".join(errs) or "brak danych")
    return n


def s_cmc(c):
    key = os.environ.get("CMC_API_KEY")
    import urllib.request, json as _j  # noqa: E401  (potrzebny nagłówek z kluczem)
    req = urllib.request.Request("https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest",
                                 headers={"X-CMC_PRO_API_KEY": key, "Accept": "application/json", "User-Agent": bx.UA})
    try:
        with urllib.request.urlopen(req, timeout=bx.TIMEOUT) as r:
            d = _j.load(r).get("data") or {}
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(str(e).replace(key, "***")) from None
    q = (d.get("quote") or {}).get("USD") or {}
    now, n = c.now_s, 0
    for m, v in (("cmc_btc_dominance", d.get("btc_dominance")), ("cmc_eth_dominance", d.get("eth_dominance")), ("cmc_total_mcap_usd", q.get("total_market_cap")),
                 ("cmc_total_volume_24h_usd", q.get("total_volume_24h")), ("cmc_stablecoin_mcap_usd", q.get("stablecoin_market_cap")),
                 ("cmc_derivatives_volume_24h_usd", q.get("derivatives_volume_24h")), ("cmc_defi_mcap_usd", q.get("defi_market_cap"))):
        if bx.f(v) is not None:
            n += c.db.put_obs(m, [(now, bx.f(v))], "coinmarketcap")
    if not n:
        raise RuntimeError("pusta odpowiedź CMC")
    return n


GLASSNODE_DEFAULT = "addresses/active_count,addresses/new_non_zero_count,transactions/count,market/mvrv,indicators/sopr,supply/profit_relative"


def s_glassnode(c):
    key = os.environ.get("GLASSNODE_API_KEY")
    n, errs = 0, []
    for path in (os.environ.get("GLASSNODE_METRICS") or GLASSNODE_DEFAULT).split(","):
        path = path.strip()
        metric = "gn_" + path.replace("/", "_")
        since = _start_from(c, metric, 1420070400, 5)
        try:
            rows = c.get(f"https://api.glassnode.com/v1/metrics/{path}?a=BTC&i=24h&s={since}&api_key={key}")
            n += c.db.put_obs(metric, [(day_floor(int(r["t"])), float(r["v"])) for r in rows if isinstance(r.get("v"), (int, float))], "glassnode")
        except (RuntimeError, KeyError, TypeError, ValueError) as e:
            errs.append(f"{path}: {str(e).replace(str(key), '***')}")
    if not n:
        raise RuntimeError("; ".join(errs) or "brak danych")
    return n


def s_whale(c):
    key = os.environ.get("WHALE_ALERT_API_KEY")
    start = c.now_s - 3500
    res = c.get(f"https://api.whale-alert.io/v1/transactions?api_key={key}&min_value=5000000&start={start}&currency=btc&limit=100")
    txs = res.get("transactions") or []
    to_ex = sum(bx.f(t.get("amount_usd"), 0) for t in txs if (t.get("to") or {}).get("owner_type") == "exchange" and (t.get("from") or {}).get("owner_type") != "exchange")
    from_ex = sum(bx.f(t.get("amount_usd"), 0) for t in txs if (t.get("from") or {}).get("owner_type") == "exchange" and (t.get("to") or {}).get("owner_type") != "exchange")
    now = c.now_s
    n = c.db.put_obs("whale_tx_count_1h", [(now, len(txs))], "whale-alert")
    n += c.db.put_obs("whale_to_exchange_usd_1h", [(now, to_ex)], "whale-alert")
    n += c.db.put_obs("whale_from_exchange_usd_1h", [(now, from_ex)], "whale-alert")
    return n


def s_fx(c):
    import btc_markets as bm
    last = None if c.full else c.db.last_obs_ts("fx_USDEUR")
    start = (datetime.fromtimestamp(last, timezone.utc) - timedelta(days=7)).date() if last else datetime(2015, 1, 1).date()
    data = bm.fetch_fx(c.get, start, c.now.date())      # paczki 90 dni, zapas: bieżący kurs
    n = sum(c.db.put_obs(f"fx_USD{ccy}", rows, "frankfurter") for ccy, rows in data.items() if rows)
    if not n:
        raise RuntimeError("Frankfurter: brak danych")
    return n


TD_PAUSE = 8.0     # darmowy plan Twelve Data: 8 zapytań na minutę
POLY_PAUSE = 12.5  # darmowy plan Polygon: 5 zapytań na minutę


def ohlcv_chain(c, db_key, stooq_code, since, td_symbol=None, tiingo=None, okx_inst=None, poly_symbol=None):
    """Świece dzienne z pierwszego działającego źródła: Stooq (bez klucza) -> Tiingo (USA) -> Polygon (klucz) -> Twelve Data (klucz) -> OKX (tokeny złota)."""
    errs = []
    try:
        return bm.fetch_stooq_ohlcv(c.get, stooq_code, since), "stooq"
    except RuntimeError as e:
        errs.append(f"stooq: {e}")
    if tiingo and os.environ.get("TIINGO_API_KEY"):
        try:
            return bm.fetch_tiingo_ohlcv(c.get, tiingo, os.environ["TIINGO_API_KEY"]), "tiingo"
        except (RuntimeError, KeyError, ValueError) as e:
            errs.append(f"tiingo: {scrub(str(e))}")
    if poly_symbol and os.environ.get("POLYGON_API_KEY"):
        try:
            time.sleep(POLY_PAUSE)
            return bm.fetch_polygon_ohlcv(c.get, poly_symbol, os.environ["POLYGON_API_KEY"], since), "polygon"
        except (RuntimeError, KeyError, ValueError) as e:
            errs.append(f"polygon: {scrub(str(e))}")
    if td_symbol and os.environ.get("TWELVEDATA_API_KEY"):
        try:
            time.sleep(TD_PAUSE)
            return bm.fetch_twelvedata_ohlcv(c.get, td_symbol, os.environ["TWELVEDATA_API_KEY"], since), "twelvedata"
        except (RuntimeError, KeyError, ValueError) as e:
            errs.append(f"twelvedata: {scrub(str(e))}")
    if okx_inst:
        try:
            return bm.fetch_okx_daily(c.get, okx_inst, since), "okx:" + okx_inst
        except RuntimeError as e:
            errs.append(f"okx: {e}")
    raise RuntimeError("; ".join(errs))


def _fetch_one_stock(c, sym, mkt, key):
    dbs = bm.db_symbol(sym, mkt)
    since = None if c.full else c.db.last_candle_ts(dbs, "1d")
    rows, src = ohlcv_chain(c, dbs, bm.stooq_code(sym, mkt), since, td_symbol=sym if mkt == "US" else None, tiingo=sym if mkt == "US" else None, poly_symbol=sym if mkt == "US" else None)
    return dbs, c.db.put_candles(dbs, "1d", rows, src)


def s_stocks(c):
    key = os.environ.get("TIINGO_API_KEY")
    wl = bm.load_watchlist()
    n, errs = 0, []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [(sym, mkt, ex.submit(_fetch_one_stock, c, sym, mkt, key)) for sym, mkt, _, _ in wl]
        for sym, mkt, f in futs:
            try:
                n += f.result()[1]
            except (RuntimeError, KeyError, ValueError) as e:
                errs.append(f"{bm.db_symbol(sym, mkt)}: {scrub(str(e))}")
    for mkt, codes in bm.BENCH.items():            # indeksy odniesienia do siły względnej
        for code in codes:
            try:
                rows = bm.fetch_stooq_ohlcv(c.get, code)
                n += c.db.put_obs(f"stooq_idx_{code}", [(r[0], r[4]) for r in rows], "stooq")
                break
            except RuntimeError:
                continue
    if not n:
        raise RuntimeError("; ".join(errs[:5]) or "brak danych")
    if errs:
        c.db.log_run("stocks_partial", False, 0, 0, scrub("; ".join(errs[:8])))
    return n


def s_commodities(c):
    """Złoto, srebro, platyna, miedź, ropa, ETF-y i indeksy (SPX, NDX, DXY, VIX) jako świece OHLCV."""
    jobs = [(name, [code]) for name, (code, _) in bm.COMMODITIES.items()] + [(k, [v[0]]) for k, v in bm.FX_PAIRS.items()] + list(bm.INDICES.items())

    okx = {"XAUUSD": "XAUT-USDT"}                      # złoto: token XAUT jako zapas (24/7, odchyłka od spotu zwykle < 1%)

    def one(name, codes):
        since = None if c.full else c.db.last_candle_ts(name, "1d")
        last_err = None
        for code in codes:
            try:
                rows, src = ohlcv_chain(c, name, code, since, td_symbol=bm.TD_SYMBOLS.get(name), okx_inst=okx.get(name), poly_symbol=bm.POLY_SYMBOLS.get(name))
                return c.db.put_candles(name, "1d", rows, src)
            except RuntimeError as e:
                last_err = e
        raise last_err
    n, errs = 0, []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [(name, ex.submit(one, name, codes)) for name, codes in jobs]
        for name, f in futs:
            try:
                n += f.result()
            except RuntimeError as e:
                errs.append(f"{name}: {e}")
    if not n:
        raise RuntimeError("; ".join(errs[:5]) or "brak danych")
    if errs:
        c.db.log_run("commodities_partial", False, 0, 0, "; ".join(errs[:8]))
    return n


COT = {"gold": "088691", "silver": "084691", "btc": "133741", "eur": "099741", "jpy": "097741", "oil": "067651", "sp500": "13874A"}


def s_cot(c):
    """CFTC Commitments of Traders (raport tygodniowy; dane za wtorek, publikacja w piątek)."""
    n, errs = 0, []

    def one(name, code):
        rows = c.get("https://publicreporting.cftc.gov/resource/6dca-aqww.json?cftc_contract_market_code=" + code +
                     "&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=" + ("700" if c.full else "12"))
        nc, cm, oi = [], [], []
        for r in rows:
            ts = int(datetime.strptime(r["report_date_as_yyyy_mm_dd"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            nc.append((ts, float(r["noncomm_positions_long_all"]) - float(r["noncomm_positions_short_all"])))
            cm.append((ts, float(r["comm_positions_long_all"]) - float(r["comm_positions_short_all"])))
            oi.append((ts, float(r["open_interest_all"])))
        return (c.db.put_obs(f"cot_{name}_noncomm_net", nc, "cftc") + c.db.put_obs(f"cot_{name}_comm_net", cm, "cftc") +
                c.db.put_obs(f"cot_{name}_oi", oi, "cftc"))
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [(k, ex.submit(one, k, v)) for k, v in COT.items()]
        for k, f in futs:
            try:
                n += f.result()
            except (RuntimeError, KeyError, ValueError) as e:
                errs.append(f"{k}: {e}")
    if not n:
        raise RuntimeError("; ".join(errs[:4]) or "brak danych")
    return n


def s_defi_tvl(c):
    rows = c.get("https://api.llama.fi/v2/historicalChainTvl")
    return c.db.put_obs("defi_tvl_usd", [(day_floor(int(r["date"])), float(r["tvl"])) for r in rows], "defillama")


def s_coinbase_daily(c):
    """Dzienne zamknięcia BTC-USD z Coinbase: razem z Binance daje historię premii USA."""
    start = _start_from(c, "coinbase_close", 1420070400, 5)
    n, t = 0, start
    while t < c.now_s:
        end = min(t + 290 * DAY, c.now_s)
        a, b = datetime.fromtimestamp(t, timezone.utc), datetime.fromtimestamp(end, timezone.utc)
        rows = c.get(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400&start={a:%Y-%m-%dT%H:%M:%SZ}&end={b:%Y-%m-%dT%H:%M:%SZ}")
        n += c.db.put_obs("coinbase_close", [(int(r[0]), float(r[4])) for r in rows], "coinbase")
        t = end
    return n


def last_friday(year, month):
    d = datetime(year + (month == 12), month % 12 + 1, 1, 8, tzinfo=timezone.utc) - timedelta(days=1)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


HALVINGS = [("2012-11-28", "halving 2012"), ("2016-07-09", "halving 2016"), ("2020-05-11", "halving 2020"), ("2024-04-20", "halving 2024")]
# Posiedzenia FOMC (drugi dzień): kalendarz wpisany ręcznie; zweryfikuj na federalreserve.gov. Pełniejszą historię daje FRED (FRED_API_KEY).
FOMC_STATIC = ["2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
               "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
               "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
FRED_RELEASES = {"cpi": 10, "nfp": 50, "fomc": 101, "gdp": 53, "pce": 54, "retail": 9, "jolts": 192, "ppi": 46}


def _ts(date_str):
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def s_events(c):
    """Kalendarz zdarzeń: wygasanie opcji (ostatni piątek), halvingi, FOMC, a z kluczem FRED także CPI/NFP/GDP/PCE itd."""
    mon, qtr = [], []
    for y in range(2016, c.now.year + 3):
        for m in range(1, 13):
            d = last_friday(y, m)
            (qtr if m in (3, 6, 9, 12) else mon).append((int(d.timestamp()), f"wygaśnięcie opcji {d:%Y-%m}"))
    n = c.db.put_events("opex_m", mon) + c.db.put_events("opex_q", qtr)
    n += c.db.put_events("halving", [(_ts(d), lab) for d, lab in HALVINGS])
    n += c.db.put_events("fomc", [(_ts(d), "FOMC") for d in FOMC_STATIC])
    key = os.environ.get("FRED_API_KEY")
    if key:
        for kind, rid in FRED_RELEASES.items():
            try:
                res = c.get(f"https://api.stlouisfed.org/fred/release/dates?release_id={rid}&api_key={key}&file_type=json&sort_order=asc"
                            f"&include_release_dates_with_no_data=true&realtime_start=2015-01-01&realtime_end={c.now.year + 1}-12-31&limit=2000")
                n += c.db.put_events(kind, [(_ts(r["date"]), kind.upper()) for r in res.get("release_dates") or []])
            except (RuntimeError, KeyError, ValueError):
                continue
    return n


def s_fundamentals(c):
    import btc_markets as bm
    key = os.environ.get("FINNHUB_API_KEY")
    n, errs = 0, []
    today = day_floor(c.now_s)
    for sym, mkt, _, _ in bm.load_watchlist():
        if mkt != "US":                              # darmowy Finnhub obejmuje głównie USA
            continue
        try:
            for field, val in bm.fetch_finnhub_fundamentals(c.get, sym, key).items():
                n += c.db.put_obs(f"fund_{sym}_{field}", [(today, val)], "finnhub")
        except RuntimeError as e:
            errs.append(f"{sym}: {scrub(str(e))}")
        time.sleep(1.1)                              # limit 60 zapytań/min (2 na spółkę)
    if not n:
        raise RuntimeError("; ".join(errs[:5]) or "brak danych")
    return n


def _mdy(text):
    return int(datetime.strptime(text.strip(), "%m/%d/%Y").replace(tzinfo=timezone.utc).timestamp())


CBOE = {"VIX": "cboe_vix", "VIX3M": "cboe_vix3m", "VIX9D": "cboe_vix9d", "SKEW": "cboe_skew", "VVIX": "cboe_vvix"}


def s_cboe(c):
    """Rodzina VIX z CBOE (bez klucza, od 1990): VIX, VIX3M, VIX9D (struktura terminowa), SKEW, VVIX. VIX trafia też do świec jako zapas dla Stooq."""
    n, errs = 0, []
    for name, metric in CBOE.items():
        try:
            text = c.get(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv", as_text=True, timeout=30)
            rd = csv.DictReader(io.StringIO(text))
            cols = [x.strip().upper() for x in (rd.fieldnames or [])]
            if "DATE" not in cols:
                raise RuntimeError("brak kolumny DATE")
            vals, ohlc = [], []
            for r in rd:
                r = {k.strip().upper(): v for k, v in r.items()}
                ts = _mdy(r["DATE"])
                if "CLOSE" in r:
                    o, h, l, cl = (bx.f(r.get(k)) for k in ("OPEN", "HIGH", "LOW", "CLOSE"))
                    if cl:
                        vals.append((ts, cl))
                        ohlc.append((ts, o or cl, h or cl, l or cl, cl, 0))
                else:
                    v = bx.f(r.get(name) or r.get(cols[-1]))
                    if v is not None:
                        vals.append((ts, v))
            n += c.db.put_obs(metric, vals, "cboe")
            if name == "VIX" and ohlc:
                c.db.put_candles("VIX", "1d", ohlc, "cboe")
        except (RuntimeError, KeyError, ValueError) as e:
            errs.append(f"{name}: {e}")
    if not n:
        raise RuntimeError("; ".join(errs[:3]) or "brak danych")
    return n


HL_COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")


def s_hyperliquid(c):
    """Hyperliquid (zdecentralizowana giełda perpetual, bez klucza): funding, open interest i premia co godzinę."""
    res = c.post("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    meta, ctxs = res[0]["universe"], res[1]
    now, n, tot_oi = c.now_s, 0, 0.0
    for m, x in zip(meta, ctxs):
        px, oi = bx.f(x.get("markPx")), bx.f(x.get("openInterest"))
        if px and oi:
            tot_oi += px * oi
        if m["name"] in HL_COINS and px:
            tag = m["name"].lower()
            fr = bx.f(x.get("funding"))
            for metric, v in ((f"hl_funding_ann_pct_{tag}", fr * 24 * 365 * 100 if fr is not None else None), (f"hl_oi_usd_{tag}", px * oi if oi else None),
                              (f"hl_premium_pct_{tag}", (bx.f(x.get("premium")) or 0) * 100)):
                if v is not None:
                    n += c.db.put_obs(metric, [(now, v)], "hyperliquid")
    if tot_oi:
        n += c.db.put_obs("hl_total_oi_usd", [(now, tot_oi)], "hyperliquid")
    if not n:
        raise RuntimeError("Hyperliquid: pusta odpowiedź")
    return n


GDELT_QUERIES = {"bitcoin": "bitcoin", "gold": '"gold price"', "fed": '"federal reserve"', "recession": "recession", "inflation": "inflation"}


def s_gdelt(c):
    """GDELT (bez klucza): natężenie i ton światowych wiadomości o wybranych tematach (próbkowanie co 15 min, tu średnie dzienne)."""
    from urllib.parse import quote
    n, errs = 0, []
    span = "1y" if c.full else "10d"
    for tag, q in GDELT_QUERIES.items():
        for mode, metric in (("timelinevolraw", f"gdelt_vol_{tag}"), ("timelinetone", f"gdelt_tone_{tag}")):
            try:
                d = c.get(f"https://api.gdeltproject.org/api/v2/doc/doc?query={quote(q)}&mode={mode}&format=json&timespan={span}", timeout=40)
                rows = ((d.get("timeline") or [{}])[0]).get("data") or []
                acc = {}
                for r in rows:
                    ts = int(datetime.strptime(r["date"][:8], "%Y%m%d").replace(tzinfo=timezone.utc).timestamp())
                    acc.setdefault(ts, []).append(float(r["value"]))
                agg = (lambda v: sum(v)) if mode == "timelinevolraw" else (lambda v: sum(v) / len(v))
                n += c.db.put_obs(metric, [(ts, agg(v)) for ts, v in acc.items()], "gdelt")
            except (RuntimeError, KeyError, ValueError, IndexError) as e:
                errs.append(f"{metric}: {e}")
            time.sleep(5.5)                                   # limit GDELT: 1 zapytanie na 5 s
    if not n:
        raise RuntimeError("; ".join(errs[:3]) or "brak danych")
    return n


UST_COLS = {"3 Mo": "ust_3m", "2 Yr": "ust_2y", "5 Yr": "ust_5y", "10 Yr": "ust_10y", "30 Yr": "ust_30y"}


def s_treasury(c):
    """Krzywa rentowności Skarbu USA (home.treasury.gov, bez klucza, dzienna) oraz SOFR z NY Fed."""
    n, errs = 0, []
    years = range(2015 if c.full else c.now.year, c.now.year + 1)
    for y in years:
        try:
            text = c.get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv", as_text=True, timeout=30)
            series = {m: [] for m in list(UST_COLS.values()) + ["ust_10y_2y"]}
            for r in csv.DictReader(io.StringIO(text)):
                ts = _mdy(r["Date"])
                for col, metric in UST_COLS.items():
                    v = bx.f(r.get(col))
                    if v is not None:
                        series[metric].append((ts, v))
                a, b = bx.f(r.get("10 Yr")), bx.f(r.get("2 Yr"))
                if a is not None and b is not None:
                    series["ust_10y_2y"].append((ts, a - b))
            n += sum(c.db.put_obs(m, rows, "treasury") for m, rows in series.items())
        except (RuntimeError, KeyError, ValueError) as e:
            errs.append(f"{y}: {e}")
    try:
        d = c.get(f"https://markets.newyorkfed.org/api/rates/secured/sofr/last/{500 if c.full else 10}.json")
        n += c.db.put_obs("sofr", [(int(datetime.strptime(r["effectiveDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()), float(r["percentRate"])) for r in d.get("refRates") or []], "nyfed")
    except (RuntimeError, KeyError, ValueError) as e:
        errs.append(f"SOFR: {e}")
    if not n:
        raise RuntimeError("; ".join(errs[:3]) or "brak danych")
    return n


# nazwa: (funkcja, harmonogram, zmienna klucza lub None)
# źródła, których chwilowy brak nie jest błędem kolektora (dane zewnętrzne, często blokowane z serwerów w chmurze)
OPTIONAL = {"stooq"}
SOURCES = {
    "binance_klines": (s_binance_klines, "daily", None), "binance_funding": (s_binance_funding, "daily", None),
    "binance_futures_daily": (s_binance_futures_daily, "daily", None), "fng": (s_fng, "daily", None), "dvol": (s_dvol, "daily", None),
    "stablecoins": (s_stablecoins, "daily", None), "hashrate": (s_hashrate, "daily", None), "wikipedia": (s_wikipedia, "daily", None),
    "stooq": (s_stooq, "daily", None), "snapshot": (s_snapshot, "hourly", None),
    "fred": (s_fred, "daily", "FRED_API_KEY"), "cmc": (s_cmc, "daily", "CMC_API_KEY"),
    "glassnode": (s_glassnode, "daily", "GLASSNODE_API_KEY"), "whale": (s_whale, "hourly", "WHALE_ALERT_API_KEY"),
    "fx": (s_fx, "daily", None), "stocks": (s_stocks, "daily", None), "fundamentals": (s_fundamentals, "daily", "FINNHUB_API_KEY"),
    "commodities": (s_commodities, "daily", None), "klines_eth": (s_klines_eth, "daily", None), "klines_paxg": (s_klines_paxg, "daily", None), "klines_crypto": (s_klines_crypto, "daily", None),
    "cot": (s_cot, "daily", None), "defi_tvl": (s_defi_tvl, "daily", None), "coinbase_daily": (s_coinbase_daily, "daily", None), "events": (s_events, "daily", None), "cboe": (s_cboe, "daily", None), "hyperliquid": (s_hyperliquid, "hourly", None), "gdelt": (s_gdelt, "daily", None), "treasury": (s_treasury, "daily", None),
}


def scrub(text):
    """Klucze API nigdy nie trafiają do dziennika ani do wyjścia programu."""
    for k, v in os.environ.items():
        if v and len(v) >= 8 and (k.endswith("_API_KEY") or k.endswith("_API_SECRET") or k.endswith("_TOKEN")):
            text = text.replace(v, "***")
    return text


def run(mode, db, get=None, only=None, now=None, sleep=True, workers=None, post=None):
    """Źródła działają równolegle (domyślnie 6 wątków); zapisy do bazy są szeregowane blokadą."""
    get = get or bx.http_get
    now = now or datetime.now(timezone.utc)
    ctx = Ctx(db, get, now, full=(mode == "backfill"), post=post)
    report, due = {}, []
    for name, (fn, sched, keyenv) in SOURCES.items():
        if only and name not in only:
            continue
        if keyenv and not os.environ.get(keyenv):
            report[name] = "pominięto (brak " + keyenv + ")"
            continue
        last = db.last_ok_run(name)
        if mode == "update" and sched == "daily" and last and now.timestamp() - last < 20 * 3600:
            report[name] = "aktualne"
            continue
        due.append(name)

    def one(name):
        t0 = time.time()
        try:
            n = SOURCES[name][0](ctx)
            db.log_run(name, True, n, time.time() - t0)
            return name, f"OK, {n} wierszy"
        except Exception as e:  # noqa: BLE001 — jedno źródło nie psuje reszty
            msg = scrub(f"{type(e).__name__}: {e}")
            db.log_run(name, False, 0, time.time() - t0, msg)
            return name, ("OSTRZEŻENIE " if name in OPTIONAL else "BŁĄD ") + msg[:160]
    with ThreadPoolExecutor(max_workers=workers or int(os.environ.get("BTC_COLLECT_WORKERS", "6"))) as ex:
        for name, msg in ex.map(one, due):
            report[name] = msg
    return {k: report[k] for k in SOURCES if k in report}


def status(db):
    print(f"{'metryka':34} {'od':10} {'do':10} {'wierszy':>8}")
    for m, a, b, n in db.con.execute("SELECT metric, MIN(ts), MAX(ts), COUNT(*) FROM obs GROUP BY metric ORDER BY metric"):
        print(f"{m:34} {datetime.fromtimestamp(a, timezone.utc):%Y-%m-%d} {datetime.fromtimestamp(b, timezone.utc):%Y-%m-%d} {n:8d}")
    for s, i, a, b, n in db.con.execute("SELECT symbol, interval, MIN(ts), MAX(ts), COUNT(*) FROM candles GROUP BY symbol, interval"):
        print(f"candles {s} {i:4} {datetime.fromtimestamp(a, timezone.utc):%Y-%m-%d} {datetime.fromtimestamp(b, timezone.utc):%Y-%m-%d} {n:8d}")
    print("\nOstatnie błędy:")
    for ts, s, e in db.con.execute("SELECT ts, source, error FROM runs WHERE ok=0 ORDER BY id DESC LIMIT 8"):
        print(f"  {datetime.fromtimestamp(ts, timezone.utc):%m-%d %H:%M} {s}: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["backfill", "update", "status"])
    ap.add_argument("--db", default=os.environ.get("BTC_DB", DB_DEFAULT))
    ap.add_argument("--only", help="lista źródeł po przecinku: " + ",".join(SOURCES))
    a = ap.parse_args()
    import btc_analysis as ba
    ba.load_dotenv([os.path.join(HERE, ".env")])
    db = DB(a.db)
    if a.mode == "status":
        return status(db)
    rep = run(a.mode, db, only=set(a.only.split(",")) if a.only else None)
    for k, v in rep.items():
        print(f"{k:24} {v}")
    sys.exit(0 if not any(v.startswith("BŁĄD") for v in rep.values()) else 1)


if __name__ == "__main__":
    main()
