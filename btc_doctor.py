#!/usr/bin/env python3
"""Diagnostyka źródeł: sprawdza, czy każde API odpowiada i ma format, którego oczekuje kod.

Użycie:  python btc_doctor.py            (na serwerze; źródła z kluczami tylko gdy klucz jest w .env)
         python btc_doctor.py --only stooq,binance
Kod wyjścia 0 = wszystko działa, 1 = są błędy (lista z opisem, co poprawić).
"""
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_net  # noqa: E402

TODAY = datetime.now(timezone.utc)
D = lambda n: (TODAY - timedelta(days=n)).strftime("%Y-%m-%d")


def need(cond, msg):
    if not cond:
        raise AssertionError(msg)


def csv_header(text, *cols):
    head = text.splitlines()[0] if text else ""
    need(all(c in head for c in cols), f"nagłówek CSV: '{head[:80]}' (brak {cols})")
    return f"{len(text.splitlines()) - 1} wierszy"


def checks():
    k = os.environ.get
    c = [
        ("bybit_tickers", "bybit", "https://api.bybit.com/v5/market/tickers?category=spot", False,
         lambda d: (need(any(t["symbol"] == "XAUTUSDT" for t in d["result"]["list"]), "brak XAUTUSDT (złoto) na Bybit spot"), "BTC/ETH/XAUT")[1]),
        ("bybit_kline", "bybit", "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=D&limit=5", False, lambda d: f"{len(d['result']['list'])} świec"),
        ("binance_klines", "binance", "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=3", False, lambda d: (need(len(d[0]) >= 10, "krótki wiersz klines"), "ok")[1]),
        ("binance_funding", "binance", "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=2", False, lambda d: (need("fundingRate" in d[0], "brak fundingRate"), "ok")[1]),
        ("binance_ls", "binance", "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=1d&limit=2", False, lambda d: (need("longShortRatio" in d[0], "brak longShortRatio"), "ok")[1]),
        ("cboe_vix", "cboe", "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv", True, lambda t: csv_header(t, "DATE", "CLOSE")),
        ("cboe_skew", "cboe", "https://cdn.cboe.com/api/global/us_indices/daily_prices/SKEW_History.csv", True, lambda t: csv_header(t, "DATE", "SKEW")),
        ("gdelt", "gdelt", "https://api.gdeltproject.org/api/v2/doc/doc?query=bitcoin&mode=timelinetone&format=json&timespan=2d", False,
         lambda d: (need(d["timeline"][0]["data"][0]["value"] is not None, "brak pola value"), f"{len(d['timeline'][0]['data'])} pkt")[1]),
        ("treasury_yields", "treasury", f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{TODAY.year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={TODAY.year}&page&_format=csv", True, lambda t: csv_header(t, "Date", "10 Yr")),
        ("nyfed_sofr", "nyfed", "https://markets.newyorkfed.org/api/rates/secured/sofr/last/2.json", False, lambda d: str(d["refRates"][0]["percentRate"])),
        ("hyperliquid", "hyperliquid", "https://api.hyperliquid.xyz/info", False, lambda d: (need(len(d[0]["universe"]) == len(d[1]), "niezgodna długość universe/ctx"), f"{len(d[0]['universe'])} rynków")[1], {"type": "metaAndAssetCtxs"}),
        ("okx_candles", "okx", "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1D&limit=3", False, lambda d: (need(len(d["data"][0]) > 8, "krótki wiersz świecy"), f"{len(d['data'])} świec")[1]),
        ("okx_tickers", "okx", "https://www.okx.com/api/v5/market/tickers?instType=SPOT", False, lambda d: (need(any(t["instId"] == "XAUT-USDT" for t in d["data"]), "brak XAUT-USDT"), f"{len(d['data'])} par")[1]),
        ("okx_xaut", "okx", "https://www.okx.com/api/v5/market/history-candles?instId=XAUT-USDT&bar=1D&limit=2", False, lambda d: f"złoto (token): {d['data'][0][4]}"),
        ("ecb_fx", "fx", "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml", True, lambda t: (need("<Cube time=" in t, "to nie jest plik EBC"), f"{t.count('<Cube time=')} dni")[1]),
        ("frankfurter_latest", "fx", "https://api.frankfurter.dev/v1/latest?base=USD&symbols=PLN", False, lambda d: str(d["rates"]["PLN"])),
        ("okx_funding", "okx", "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP", False, lambda d: (need(d["data"][0]["fundingRate"] != "", "pusty funding"), "ok")[1]),
        ("deribit_dvol", "deribit", f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1D&start_timestamp={int((TODAY - timedelta(days=5)).timestamp() * 1000)}&end_timestamp={int(TODAY.timestamp() * 1000)}", False,
         lambda d: (need(len(d["result"]["data"][0]) >= 5, "wiersz DVOL <5 pól"), f"{len(d['result']['data'])} dni")[1]),
        ("deribit_options", "deribit", "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option", False, lambda d: f"{len(d['result'])} opcji"),
        ("coinbase", "coinbase", "https://api.exchange.coinbase.com/products/BTC-USD/ticker", False, lambda d: (need("price" in d, "brak price"), d["price"])[1]),
        ("coinbase_candles", "coinbase", f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400&start={D(5)}T00:00:00Z&end={D(0)}T00:00:00Z", False, lambda d: f"{len(d)} świec"),
        ("kraken", "kraken", "https://api.kraken.com/0/public/Ticker?pair=XBTUSD", False, lambda d: (need("XXBTZUSD" in d["result"], "brak XXBTZUSD"), "ok")[1]),
        ("fng", "alt", "https://api.alternative.me/fng/?limit=2&format=json", False, lambda d: d["data"][0]["value"]),
        ("mempool", "mempool", "https://mempool.space/api/v1/fees/recommended", False, lambda d: (need("halfHourFee" in d, "brak halfHourFee"), "ok")[1]),
        ("mempool_hashrate", "mempool", "https://mempool.space/api/v1/mining/hashrate/3y", False, lambda d: (need(d["hashrates"][0]["avgHashrate"] > 0, "hashrate"), f"{len(d['hashrates'])} pkt")[1]),
        ("llama_stable", "llama", "https://stablecoins.llama.fi/stablecoincharts/all", False,
         lambda d: (need(("totalCirculatingUSD" in d[-1]) or ("totalCirculating" in d[-1]), f"pola: {list(d[-1])[:5]}"), f"{len(d)} dni")[1]),
        ("llama_tvl", "llama", "https://api.llama.fi/v2/historicalChainTvl", False, lambda d: f"{len(d)} dni"),
        ("wikimedia", "wiki", f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Bitcoin/daily/{(TODAY - timedelta(days=6)):%Y%m%d}/{(TODAY - timedelta(days=2)):%Y%m%d}", False, lambda d: f"{len(d['items'])} dni"),
        ("frankfurter", "fx", f"https://api.frankfurter.dev/v1/{D(10)}..{D(1)}?base=USD&symbols=EUR,PLN", False, lambda d: (need("PLN" in list(d["rates"].values())[0], "brak PLN"), f"{len(d['rates'])} dni")[1]),
        ("cftc_cot", "cftc", "https://publicreporting.cftc.gov/resource/6dca-aqww.json?cftc_contract_market_code=088691&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=2", False,
         lambda d: (need(all(x in d[0] for x in ("noncomm_positions_long_all", "comm_positions_short_all", "open_interest_all")), f"pola COT: {sorted(d[0])[:6]}"), d[0]["report_date_as_yyyy_mm_dd"][:10])[1]),
    ]
    stooq = [("stooq_gold", "xauusd"), ("stooq_spx", "^spx"), ("stooq_dxy", "^dxy"), ("stooq_vix", "^vix"), ("stooq_wti", "cl.f"), ("stooq_gld", "gld.us"), ("stooq_nvda", "nvda.us"),
             ("stooq_cdr_pl", "cdr.pl"), ("stooq_sap_de", "sap.de"), ("stooq_wig20", "wig20")]
    for name, code in stooq:
        c.append((name, "stooq", f"https://stooq.com/q/d/l/?s={code}&i=d&d1={(TODAY - timedelta(days=20)):%Y%m%d}&d2={(TODAY + timedelta(days=1)):%Y%m%d}", True, lambda t: csv_header(t, "Date", "Close")))
    c.append(("stooq_live_multi", "stooq", "https://stooq.com/q/l/?s=xauusd+eurusd+usdpln&f=sd2t2ohlcv&h&e=csv", True,
              lambda t: (need(len([x for x in t.splitlines()[1:] if x.strip()]) >= 3, "zbiorcze notowanie zwróciło <3 wierszy (btc_live użyje zapasu)"), "3 wiersze")[1]))
    if k("FRED_API_KEY"):
        c.append(("fred", "fred", f"https://api.stlouisfed.org/fred/series/observations?series_id=DFII10&api_key={k('FRED_API_KEY')}&file_type=json&observation_start={D(10)}", False, lambda d: f"{len(d['observations'])} obs"))
        c.append(("fred_calendar", "fred", f"https://api.stlouisfed.org/fred/release/dates?release_id=10&api_key={k('FRED_API_KEY')}&file_type=json&include_release_dates_with_no_data=true&realtime_start={D(5)}&realtime_end={TODAY.year + 1}-12-31&limit=5",
                  False, lambda d: f"{len(d['release_dates'])} dat CPI"))
    if k("FINNHUB_API_KEY"):
        c.append(("finnhub", "finnhub", f"https://finnhub.io/api/v1/stock/metric?symbol=AAPL&metric=all&token={k('FINNHUB_API_KEY')}", False,
                  lambda d: (need(any(x in d["metric"] for x in ("revenueGrowthTTMYoy", "peTTM")), "brak pól fundamentów"), f"{len(d['metric'])} metryk")[1]))
    if k("TWELVEDATA_API_KEY"):
        c.append(("twelvedata", "twelvedata", f"https://api.twelvedata.com/time_series?symbol=XAU%2FUSD&interval=1day&outputsize=3&apikey={k('TWELVEDATA_API_KEY')}", False,
                  lambda d: (need("values" in d, str(d.get("message", d))[:80]), f"{len(d['values'])} dni")[1]))
    if k("POLYGON_API_KEY"):
        c.append(("polygon", "polygon", f"https://api.polygon.io/v2/aggs/ticker/C:XAUUSD/range/1/day/{D(10)}/{D(1)}?adjusted=true&sort=asc&apiKey={k('POLYGON_API_KEY')}", False,
                  lambda d: (need(d.get("results"), str(d.get("error") or d.get("message") or d.get("status"))[:80]), f"{len(d['results'])} dni złota")[1]))
    if k("TIINGO_API_KEY"):
        c.append(("tiingo", "tiingo", f"https://api.tiingo.com/tiingo/daily/aapl/prices?startDate={D(10)}&token={k('TIINGO_API_KEY')}", False, lambda d: f"{len(d)} dni"))
    if k("GLASSNODE_API_KEY"):
        c.append(("glassnode", "glassnode", f"https://api.glassnode.com/v1/metrics/addresses/active_count?a=BTC&i=24h&api_key={k('GLASSNODE_API_KEY')}", False, lambda d: f"{len(d)} pkt"))
    return c


def run_checks(get=btc_net.get, only=None):
    out = []

    def one(item):
        name, grp, url, as_text, validate = item[:5]
        payload = item[5] if len(item) > 5 else None
        t0 = time.time()
        try:
            data = btc_net.post_json(url, payload, timeout=15, retries=1) if payload is not None else get(url, as_text=as_text, timeout=12, retries=1)
            return name, True, str(validate(data)), time.time() - t0
        except AssertionError as e:
            return name, False, "ZŁY FORMAT: " + str(e), time.time() - t0
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            for kname in ("FRED_API_KEY", "FINNHUB_API_KEY", "TIINGO_API_KEY", "GLASSNODE_API_KEY", "TWELVEDATA_API_KEY", "POLYGON_API_KEY"):
                if os.environ.get(kname):
                    msg = msg.replace(os.environ[kname], "***")
            return name, False, f"{type(e).__name__}: {msg}"[:140], time.time() - t0
    items = [i for i in checks() if not only or any(i[0].startswith(o) or i[1] == o for o in only)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(one, items))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="prefiksy nazw lub grupy po przecinku")
    a = ap.parse_args()
    try:
        import btc_analysis as ba
        ba.load_dotenv([os.path.join(HERE, ".env")])
    except Exception:  # noqa: BLE001
        pass
    res = run_checks(only=a.only.split(",") if a.only else None)
    bad = 0
    for name, ok, detail, dt in res:
        print(f"{'OK  ' if ok else 'BŁĄD'} {name:20} {dt:4.1f}s  {detail}")
        bad += not ok
    print(f"\n{len(res) - bad}/{len(res)} źródeł działa." + ("" if not bad else " Błędy opisują, które pole lub adres wymaga poprawki w kodzie (podaj mi tę listę)."))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
