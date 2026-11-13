#!/usr/bin/env python3
"""Rynki poza kryptowalutami: waluty fiat (FX) i ranking spółek z kilku giełd.

Użycie:
    python btc_markets.py analyze [--out btc_markets.json]      # analiza z bazy btc_data.db
    python btc_markets.py watchlist                              # zapisuje domyślny watchlist.json do edycji
Dane zbiera btc_collect.py (źródła: fx, stocks, fundamentals). Lista spółek: watchlist.json obok skryptu.

FX (Frankfurter = kursy referencyjne EBC, bez klucza): siła walut względem USD, trend, RSI, zmienność, indeks dolara,
kursy w PLN, korelacja z BTC.
Spółki: ceny dzienne ze Stooq (USA, GPW, Niemcy; bez klucza) lub Tiingo (klucz, tylko USA); fundamenty z Finnhub (klucz).
Ranking 0-100 to PRZEJRZYSTY model czynnikowy (momentum 12-1, momentum 6m, siła względem indeksu, trend, bliskość szczytu,
zmienność, wolumen + opcjonalnie wzrost przychodów/zysku, marże, ROE, PEG, zadłużenie). To NIE jest prognoza: wskazuje spółki,
które historycznie wyróżniały się wg tych cech. Backtest czynnika cenowego jest obciążony 'błędem przetrwania' (lista = dzisiejsi
zwycięzcy) i mówi tylko, czy sam model nie jest pusty.
"""
import argparse
import bisect
import json
import math
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86400

# ----------------------------------------------------------------------------
# Konfiguracja
# ----------------------------------------------------------------------------
FX_CCY = ["EUR", "GBP", "CHF", "JPY", "PLN", "CZK", "HUF", "CAD", "AUD", "NZD", "NOK", "SEK", "CNY", "TRY", "MXN", "BRL", "ZAR", "INR"]
G10 = ["EUR", "GBP", "JPY", "CHF", "CAD", "SEK", "AUD", "NZD", "NOK"]
COMMODITIES = {"XAUUSD": ("xauusd", "Złoto (USD/oz)"), "XAGUSD": ("xagusd", "Srebro (USD/oz)"), "XPTUSD": ("xptusd", "Platyna (USD/oz)"),
               "COPPER": ("hg.f", "Miedź"), "WTI": ("cl.f", "Ropa WTI"), "GLD": ("gld.us", "GLD (ETF złota)"), "IAU": ("iau.us", "IAU (ETF złota)"),
               "GDX": ("gdx.us", "GDX (spółki wydobywcze złota)"), "SLV": ("slv.us", "SLV (ETF srebra)"), "TLT": ("tlt.us", "TLT (obligacje USA 20+)"),
               "UUP": ("uup.us", "UUP (ETF dolara)")}
INDICES = {"SPX": ["^spx"], "NDX": ["^ndx"], "DXY": ["^dxy", "dx.f"], "VIX": ["^vix"]}
CRYPTO = [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana"), ("BNB", "BNB"), ("XRP", "XRP"), ("ADA", "Cardano"), ("DOGE", "Dogecoin"),
          ("AVAX", "Avalanche"), ("LINK", "Chainlink"), ("LTC", "Litecoin"), ("DOT", "Polkadot"), ("TRX", "TRON")]
FX_PAIRS = {"EURUSD": ("eurusd", "EUR/USD"), "GBPUSD": ("gbpusd", "GBP/USD"), "USDPLN": ("usdpln", "USD/PLN"), "EURPLN": ("eurpln", "EUR/PLN"),
            "USDJPY": ("usdjpy", "USD/JPY"), "USDCHF": ("usdchf", "USD/CHF"), "AUDUSD": ("audusd", "AUD/USD"), "USDCAD": ("usdcad", "USD/CAD")}
SUFFIX = {"US": ".us", "PL": ".pl", "DE": ".de"}
BENCH = {"US": ["^spx"], "PL": ["wig20", "^wig20", "wig"], "DE": ["^dax", "^spx"]}

DEFAULT_WATCHLIST = [
    # USA: duże spółki wzrostowe, AI i ekspozycja na krypto
    ("NVDA", "US", "NVIDIA", "AI / półprzewodniki"), ("MSFT", "US", "Microsoft", "Oprogramowanie / chmura"), ("AMZN", "US", "Amazon", "E-commerce / chmura"),
    ("GOOGL", "US", "Alphabet", "Internet / AI"), ("META", "US", "Meta Platforms", "Internet / AI"), ("AAPL", "US", "Apple", "Sprzęt / usługi"),
    ("TSLA", "US", "Tesla", "Motoryzacja / energia"), ("AMD", "US", "AMD", "Półprzewodniki"), ("AVGO", "US", "Broadcom", "Półprzewodniki"),
    ("PLTR", "US", "Palantir", "Oprogramowanie / AI"), ("CRWD", "US", "CrowdStrike", "Cyberbezpieczeństwo"), ("NET", "US", "Cloudflare", "Infrastruktura internetu"),
    ("SHOP", "US", "Shopify", "E-commerce"), ("COIN", "US", "Coinbase", "Krypto"), ("MSTR", "US", "Strategy (MicroStrategy)", "Krypto / BTC"),
    ("HOOD", "US", "Robinhood", "Fintech / krypto"), ("MARA", "US", "MARA Holdings", "Górnictwo BTC"), ("NFLX", "US", "Netflix", "Media"),
    ("LLY", "US", "Eli Lilly", "Farmacja"), ("V", "US", "Visa", "Płatności"),
    # GPW
    ("CDR", "PL", "CD Projekt", "Gry"), ("ALE", "PL", "Allegro", "E-commerce"), ("LPP", "PL", "LPP", "Moda"), ("DNP", "PL", "Dino Polska", "Handel"),
    ("KGH", "PL", "KGHM", "Surowce"), ("PKO", "PL", "PKO BP", "Banki"), ("PZU", "PL", "PZU", "Ubezpieczenia"), ("XTB", "PL", "XTB", "Brokerzy"),
    ("KTY", "PL", "Grupa Kęty", "Przemysł"), ("PEO", "PL", "Bank Pekao", "Banki"),
    # Niemcy
    ("SAP", "DE", "SAP", "Oprogramowanie"), ("SIE", "DE", "Siemens", "Przemysł"), ("RHM", "DE", "Rheinmetall", "Obronność"),
]

W_PRICE = {"mom_12_1": .25, "mom_6": .15, "rel_6": .15, "trend": .15, "prox_high": .15, "low_vol": .10, "vol_trend": .05}
W_FUND = {"rev_growth": .30, "eps_growth": .20, "net_margin": .15, "roe": .10, "peg_inv": .15, "debt_inv": .10}
FUND_FIELDS = {"revenueGrowthTTMYoy": "rev_growth", "epsGrowthTTMYoy": "eps_growth", "netProfitMarginTTM": "net_margin",
               "roeTTM": "roe", "peTTM": "pe", "totalDebt/totalEquityQuarterly": "debt_equity"}


def stooq_map():
    """{kod Stooq: klucz w bazie} dla notowań na bieżąco: surowce, pary walutowe, indeksy i spółki z watchlisty."""
    m = {code: key for key, (code, _) in COMMODITIES.items() if key in ("XAUUSD", "XAGUSD", "XPTUSD", "COPPER", "WTI")}
    m.update({code: key for key, (code, _) in FX_PAIRS.items()})
    m.update({"^spx": "SPX", "^dxy": "DXY", "^ndx": "NDX", "^vix": "VIX"})
    for sym, mkt, _, _ in load_watchlist():
        m[stooq_code(sym, mkt)] = db_symbol(sym, mkt)
    return m


def load_watchlist(path=None):
    path = path or os.path.join(HERE, "watchlist.json")
    try:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh).get("tickers", [])
        out = [(t["symbol"].upper(), t.get("market", "US").upper(), t.get("name", t["symbol"]), t.get("sector", "")) for t in items]
        if out:
            return out
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    return list(DEFAULT_WATCHLIST)


def stooq_code(symbol, market):
    return symbol.lower() + SUFFIX.get(market, ".us")


def db_symbol(symbol, market):
    return f"{symbol}.{market}"


# ----------------------------------------------------------------------------
# Pobieranie (używane przez btc_collect.py)
# ----------------------------------------------------------------------------
def fetch_fx_ecb(get, start, end):
    """Oficjalny plik EBC (EUR jako baza, od 1999 r.) przeliczony na USD jako bazę: {waluta: [(ts, kurs za 1 USD)]}."""
    import re
    recent = (datetime.now(timezone.utc).date() - start).days <= 85
    text = get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist" + ("-90d" if recent else "") + ".xml", as_text=True, timeout=30)
    out = {c: [] for c in FX_CCY}
    for day, body in re.findall(r'<Cube time="(\d{4}-\d{2}-\d{2})">(.*?)</Cube>', text, re.S):
        d = datetime.strptime(day, "%Y-%m-%d").date()
        if d < start or d > end:
            continue
        rates = {c: float(v) for c, v in re.findall(r'currency="([A-Z]{3})" rate="([0-9.]+)"', body)}
        usd = rates.get("USD")
        if not usd:
            continue
        ts = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
        rates["EUR"] = 1.0
        for c in FX_CCY:
            if c == "USD" or c not in rates:
                continue
            out[c].append((ts, rates[c] / usd))
    if not any(out.values()):
        raise RuntimeError("EBC: brak kursów w zakresie")
    for v in out.values():
        v.sort()
    return out


def fetch_fx(get, start, end, chunk_days=90):
    """Frankfurter (EBC): {waluta: [(ts, kurs za 1 USD)]}. Paczki 90-dniowe; błąd jednej paczki nie przerywa reszty.
    Gdy zakresy nie działają, bierze bieżący kurs ('latest'), który buduje własną historię z dnia na dzień."""
    out = {c: [] for c in FX_CCY}
    errors, a = [], start
    while a <= end:
        b = min(a + timedelta(days=chunk_days - 1), end)
        try:
            data = get(f"https://api.frankfurter.dev/v1/{a:%Y-%m-%d}..{b:%Y-%m-%d}?base=USD&symbols={','.join(FX_CCY)}")
            for d, rates in sorted((data.get("rates") or {}).items()):
                ts = int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
                for c, v in rates.items():
                    if c in out and v:
                        out[c].append((ts, float(v)))
        except RuntimeError as e:
            errors.append(str(e))
            if len(errors) >= 3 and not any(out.values()):
                break
        a = b + timedelta(days=1)
    if not any(out.values()):
        try:
            return fetch_fx_ecb(get, start, end)           # drugie źródło: oficjalny plik EBC
        except RuntimeError as e:
            errors.append(str(e))
    if not any(out.values()):
        d = get(f"https://api.frankfurter.dev/v1/latest?base=USD&symbols={','.join(FX_CCY)}")
        ts = int(datetime.strptime(d["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        for c, v in (d.get("rates") or {}).items():
            if c in out and v:
                out[c].append((ts, float(v)))
    return out


def fetch_stooq_ohlcv(get, code, since_ts=None):
    """Świece dzienne ze Stooq. Z since_ts pobiera tylko nowe dane (d1/d2), co skraca transfer kilkukrotnie."""
    import csv
    import io
    url = f"https://stooq.com/q/d/l/?s={code}&i=d"
    if since_ts:
        d1 = datetime.fromtimestamp(since_ts, timezone.utc) - timedelta(days=5)
        url += f"&d1={d1:%Y%m%d}&d2={datetime.now(timezone.utc) + timedelta(days=1):%Y%m%d}"
    text = get(url, as_text=True)
    if text.lstrip()[:1] == "<":           # HTML zamiast CSV: blokada lub wymóg weryfikacji po stronie Stooq
        try:
            import btc_net
            btc_net.trip("stooq.com")
        except ImportError:
            pass
    rd = csv.DictReader(io.StringIO(text))
    if not rd.fieldnames or "Date" not in rd.fieldnames:
        if since_ts:
            return []                      # brak nowych danych w oknie
        raise RuntimeError("Stooq: brak danych (limit zapytań lub zły symbol)")
    rows = []
    for r in rd:
        try:
            ts = int(datetime.strptime(r["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            rows.append((ts, float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]), float(r.get("Volume") or 0)))
        except (ValueError, KeyError):
            continue
    if len(rows) < (1 if since_ts else 30):
        if since_ts:
            return rows
        raise RuntimeError("Stooq: za mało danych")
    return rows


def fetch_okx_daily(get, inst, since_ts=None, max_pages=60):
    """Dzienne świece z OKX (bez klucza): [(ts_s, o, h, l, c, v)] tylko zamknięte. Działa też dla tokenów złota (XAUT-USDT, PAXG-USDT)."""
    rows, after = {}, None
    for _ in range(max_pages):
        d = (get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1D&limit=100" + (f"&after={after}" if after else "")).get("data")) or []
        if not d:
            break
        for r in d:
            if len(r) > 8 and r[8] == "1":
                rows[int(r[0]) // 1000] = (int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        after = d[-1][0]
        if len(d) < 100 or (since_ts and int(after) // 1000 <= since_ts - 5 * DAY):
            break
    if not rows:
        raise RuntimeError("OKX: brak świec dla " + inst)
    return [rows[k] for k in sorted(rows)]


TD_SYMBOLS = {"XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD", "XPTUSD": "XPT/USD", "WTI": "WTI/USD", "COPPER": "XCU/USD", "SPX": "SPX", "NDX": "NDX"}
TD_SYMBOLS.update({k: k[:3] + "/" + k[3:] for k in FX_PAIRS})


def fetch_twelvedata_ohlcv(get, td_symbol, key, since_ts=None):
    """Zapas ze Stooq: Twelve Data (darmowy klucz, 8 zapytań/min). Surowce, waluty, indeksy i akcje USA."""
    from urllib.parse import quote
    size = 12 if since_ts else 5000
    d = get(f"https://api.twelvedata.com/time_series?symbol={quote(td_symbol, safe='')}&interval=1day&outputsize={size}&apikey={key}")
    if d.get("status") == "error" or "values" not in d:
        raise RuntimeError("Twelve Data: " + str(d.get("message", "brak danych"))[:80].replace(key, "***"))
    rows = []
    for r in reversed(d["values"]):
        ts = int(datetime.strptime(r["datetime"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        rows.append((ts, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0)))
    if not rows:
        raise RuntimeError("Twelve Data: pusta odpowiedź")
    return rows


POLY_SYMBOLS = {"XAUUSD": "C:XAUUSD", "XAGUSD": "C:XAGUSD", "XPTUSD": "C:XPTUSD", "TLT": "TLT"}
POLY_SYMBOLS.update({k: "C:" + k for k in FX_PAIRS})


def fetch_polygon_ohlcv(get, ticker, key, since_ts=None):
    """Zapas ze Stooq: Polygon.io (darmowy klucz: 5 zapytań/min, ok. 2 lata dziennej historii, notowania po zamknięciu).
    Tickery: C:XAUUSD, C:EURUSD (waluty i metale), AAPL (akcje USA), TLT (ETF)."""
    from urllib.parse import quote
    end = datetime.now(timezone.utc).date()
    start = (datetime.fromtimestamp(since_ts, timezone.utc) - timedelta(days=5)).date() if since_ts else end - timedelta(days=720)
    d = get(f"https://api.polygon.io/v2/aggs/ticker/{quote(ticker, safe=':')}/range/1/day/{start:%Y-%m-%d}/{end:%Y-%m-%d}?adjusted=true&sort=asc&limit=50000&apiKey={key}")
    res = d.get("results") or []
    if d.get("status") not in ("OK", "DELAYED") or not res:
        raise RuntimeError("Polygon: " + str(d.get("error") or d.get("message") or d.get("status") or "brak danych")[:80].replace(key, "***"))
    return [(int(r["t"]) // 1000 // DAY * DAY, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r.get("v") or 0)) for r in res]


def fetch_tiingo_ohlcv(get, symbol, key, start="2015-01-01"):
    data = get(f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices?startDate={start}&token={key}")
    rows = []
    for r in data:
        ts = int(datetime.strptime(r["date"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        rows.append((ts, r["adjOpen"], r["adjHigh"], r["adjLow"], r["adjClose"], r.get("adjVolume") or r.get("volume") or 0))
    if len(rows) < 30:
        raise RuntimeError("Tiingo: za mało danych")
    return rows


def fetch_finnhub_fundamentals(get, symbol, key):
    m = (get(f"https://finnhub.io/api/v1/stock/metric?symbol={symbol}&metric=all&token={key}").get("metric")) or {}
    out = {name: float(m[k]) for k, name in FUND_FIELDS.items() if isinstance(m.get(k), (int, float))}
    try:
        rec = get(f"https://finnhub.io/api/v1/stock/recommendation?symbol={symbol}&token={key}")
        if rec:
            r = rec[0]
            tot = sum(r.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
            if tot:
                out["analyst"] = (2 * r.get("strongBuy", 0) + r.get("buy", 0) - r.get("sell", 0) - 2 * r.get("strongSell", 0)) / tot
    except RuntimeError:
        pass
    return out


# ----------------------------------------------------------------------------
# Narzędzia
# ----------------------------------------------------------------------------
def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def rsi14(xs, n=14):
    if len(xs) < n + 1:
        return None
    ag = al = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        ag, al = ag + max(d, 0) / n, al + max(-d, 0) / n
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        ag, al = (ag * (n - 1) + max(d, 0)) / n, (al * (n - 1) + max(-d, 0)) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def stdev_ann(closes, n, per_year):
    w = closes[-n - 1:]
    if len(w) < n // 2:
        return None
    r = [w[i] / w[i - 1] - 1 for i in range(1, len(w))]
    return statistics.pstdev(r) * math.sqrt(per_year)


def ret_back(dates, closes, i, days):
    """Zwrot od ostatniej obserwacji sprzed 'days' dni kalendarzowych do indeksu i (None gdy brak historii)."""
    target = dates[i] - days * DAY
    j = bisect.bisect_right(dates, target, 0, i + 1) - 1
    if j < 0 or dates[0] > target + 7 * DAY:
        return None
    return closes[i] / closes[j] - 1


def pearson(a, b):
    n = min(len(a), len(b))
    if n < 15:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def pct_rank(values):
    """Percentyl (0..1) każdej wartości w przekroju; None zostaje None."""
    pairs = sorted((v, i) for i, v in enumerate(values) if v is not None)
    out = [None] * len(values)
    n = len(pairs)
    if n < 2:
        return out
    k = 0
    while k < n:
        j = k
        while j + 1 < n and pairs[j + 1][0] == pairs[k][0]:
            j += 1
        for t in range(k, j + 1):
            out[pairs[t][1]] = ((k + j) / 2) / (n - 1)
        k = j + 1
    return out


def aligned_returns(sa, sb, lookback):
    """Zwroty dwóch serii {ts: wartość} na wspólnych datach z ostatnich 'lookback' wspólnych obserwacji."""
    common = sorted(set(sa) & set(sb))[-lookback - 1:]
    ra = [sa[b] / sa[a] - 1 for a, b in zip(common, common[1:]) if sa[a] and sb[a]]
    rb = [sb[b] / sb[a] - 1 for a, b in zip(common, common[1:]) if sa[a] and sb[a]]
    return ra, rb


# ----------------------------------------------------------------------------
# FX
# ----------------------------------------------------------------------------
def analyze_fx(con, btc_close):
    raw = {}
    for m, ts, v in con.execute("SELECT metric, ts, value FROM obs WHERE metric LIKE 'fx_USD%' ORDER BY metric, ts"):
        raw.setdefault(m[6:], []).append((ts, v))
    if not raw:
        return None
    strength = {c: {ts: 1 / v for ts, v in pts if v} for c, pts in raw.items()}   # USD za 1 jednostkę waluty (rośnie = waluta silniejsza)
    last_ts = max(max(s) for s in strength.values())
    rows = []
    for c, s in sorted(strength.items()):
        dates = sorted(s)
        cl = [s[d] for d in dates]
        if len(cl) < 60:
            continue
        i = len(cl) - 1
        ytd_start = int(datetime(datetime.fromtimestamp(dates[-1], timezone.utc).year, 1, 1, tzinfo=timezone.utc).timestamp())
        y = {k: ret_back(dates, cl, i, d) for k, d in (("d1", 1), ("d7", 7), ("d30", 30), ("d90", 90), ("y1", 365))}
        j = bisect.bisect_left(dates, ytd_start)
        ytd = cl[i] / cl[j - 1] - 1 if 0 < j <= i else None
        s50, s200 = sma(cl, 50), sma(cl, 200)
        window = cl[-1260:]
        pctl = sum(1 for v in window if v <= cl[-1]) / len(window) * 100
        trend = "wzrostowy" if s50 and s200 and cl[-1] > s50 > s200 else "spadkowy" if s50 and s200 and cl[-1] < s50 < s200 else "boczny"
        ra, rb = aligned_returns(s, btc_close, 90) if btc_close else ([], [])
        rows.append({"ccy": c, "usd_per_unit": cl[-1], "per_usd": 1 / cl[-1], "chg_1d": y["d1"], "chg_7d": y["d7"], "chg_30d": y["d30"], "chg_90d": y["d90"],
                     "chg_ytd": ytd, "chg_1y": y["y1"], "vol_30d": stdev_ann(cl, 30, 252), "rsi14": rsi14(cl), "trend": trend,
                     "pctl_5y": pctl, "corr_btc_90d": pearson(ra, rb), "last_date": datetime.fromtimestamp(dates[-1], timezone.utc).strftime("%Y-%m-%d")})
    # indeks dolara: średnia geometryczna kursów G10 (rośnie, gdy USD się umacnia)
    g10 = [c for c in G10 if c in strength]
    common = sorted(set.intersection(*[set(strength[c]) for c in g10])) if g10 else []
    usd_idx = {d: math.exp(sum(math.log(1 / strength[c][d]) for c in g10) / len(g10)) for d in common}
    idx_info = None
    if len(common) > 60:
        cl = [usd_idx[d] for d in common]
        i = len(cl) - 1
        ra, rb = aligned_returns(usd_idx, btc_close, 90) if btc_close else ([], [])
        ra180, rb180 = aligned_returns(usd_idx, btc_close, 180) if btc_close else ([], [])
        idx_info = {"basket": g10, "level_vs_start": cl[-1] / cl[0], "chg_30d": ret_back(common, cl, i, 30), "chg_90d": ret_back(common, cl, i, 90), "chg_1y": ret_back(common, cl, i, 365),
                    "rsi14": rsi14(cl), "pctl_5y": sum(1 for v in cl[-1260:] if v <= cl[-1]) / len(cl[-1260:]) * 100,
                    "trend": "USD się umacnia" if cl[-1] > (sma(cl, 50) or cl[-1]) > (sma(cl, 200) or 0) else "USD słabnie" if cl[-1] < (sma(cl, 50) or cl[-1]) < (sma(cl, 200) or 1e18) else "bez wyraźnego trendu",
                    "corr_btc_90d": pearson(ra, rb), "corr_btc_180d": pearson(ra180, rb180)}
    # kursy w PLN (ile PLN kosztuje jednostka waluty)
    pln = []
    if "PLN" in raw:
        pl = {ts: v for ts, v in raw["PLN"]}
        for c in ["USD"] + [x for x in ("EUR", "GBP", "CHF", "JPY", "CZK", "HUF", "CAD", "AUD", "NOK", "SEK", "CNY", "TRY") if x in raw]:
            if c == "USD":
                ser = dict(pl)
            else:
                rc = dict(raw[c])
                ser = {d: pl[d] / rc[d] for d in sorted(pl) if d in rc and rc[d]}
            dates = sorted(ser)
            if len(dates) < 60:
                continue
            cl = [ser[d] for d in dates]
            i = len(cl) - 1
            pln.append({"ccy": c, "pln": cl[-1] * (100 if c in ("JPY", "HUF") else 1), "unit": 100 if c in ("JPY", "HUF") else 1, "chg_7d": ret_back(dates, cl, i, 7), "chg_30d": ret_back(dates, cl, i, 30),
                        "chg_90d": ret_back(dates, cl, i, 90), "chg_1y": ret_back(dates, cl, i, 365), "pctl_5y": sum(1 for v in cl[-1260:] if v <= cl[-1]) / len(cl[-1260:]) * 100})
    movers = sorted((r for r in rows if r["chg_30d"] is not None), key=lambda r: -r["chg_30d"])
    return {"as_of": datetime.fromtimestamp(last_ts, timezone.utc).strftime("%Y-%m-%d"), "currencies": rows, "usd_index": idx_info, "pln_cross": pln,
            "strongest_30d": [r["ccy"] for r in movers[:3]], "weakest_30d": [r["ccy"] for r in movers[-3:]],
            "note": "Kursy referencyjne EBC (jeden odczyt dziennie). Zmiany liczone jako siła waluty względem USD: dodatnia = waluta się umacnia."}


# ----------------------------------------------------------------------------
# Spółki
# ----------------------------------------------------------------------------
def price_features(dates, closes, vols, i, bdates, bcloses):
    """Cechy cenowe na dzień i (wyłącznie dane do i włącznie)."""
    if i < 260:
        return None
    d = dates[i]
    m121 = None
    j1 = bisect.bisect_right(dates, d - 30 * DAY, 0, i + 1) - 1
    j12 = bisect.bisect_right(dates, d - 365 * DAY, 0, i + 1) - 1
    if j12 >= 0 and j1 > j12 and dates[0] <= d - 365 * DAY + 7 * DAY:
        m121 = closes[j1] / closes[j12] - 1
    m6 = ret_back(dates, closes, i, 182)
    bi = bisect.bisect_right(bdates, d) - 1
    bm6 = ret_back(bdates, bcloses, bi, 182) if bi > 0 else None
    c = closes[i]
    s50, s200 = sma(closes[:i + 1], 50), sma(closes[:i + 1], 200)
    hi252 = max(closes[i - 251:i + 1])
    v20, v100 = sma(vols[:i + 1], 20), sma(vols[:i + 1], 100)
    return {"mom_12_1": m121, "mom_6": m6, "rel_6": (m6 - bm6) if m6 is not None and bm6 is not None else None,
            "trend": (int(c > s50) + int(s50 > s200) + int(c > s200)) if s50 and s200 else None, "prox_high": c / hi252,
            "vol90": stdev_ann(closes[:i + 1], 90, 252), "vol_trend": (v20 / v100) if v20 and v100 else None,
            "rsi14": rsi14(closes[max(0, i - 120):i + 1]), "dist_sma200": c / s200 - 1 if s200 else None, "dd_1y": c / hi252 - 1}


def score_universe(feats, fund=None):
    """feats: {sym: price_features}; fund: {sym: {pole: wartość}} -> {sym: {score, price_score, fund_score, ranks}}."""
    syms = list(feats)
    ranks = {}
    ranks["mom_12_1"] = pct_rank([feats[s]["mom_12_1"] for s in syms])
    ranks["mom_6"] = pct_rank([feats[s]["mom_6"] for s in syms])
    ranks["rel_6"] = pct_rank([feats[s]["rel_6"] for s in syms])
    ranks["trend"] = pct_rank([feats[s]["trend"] for s in syms])
    ranks["prox_high"] = pct_rank([feats[s]["prox_high"] for s in syms])
    ranks["low_vol"] = [None if r is None else 1 - r for r in pct_rank([feats[s]["vol90"] for s in syms])]
    ranks["vol_trend"] = pct_rank([feats[s]["vol_trend"] for s in syms])
    fr = {}
    if fund:
        def g(s, k):
            return (fund.get(s) or {}).get(k)
        peg = [(g(s, "pe") / (g(s, "eps_growth") * 100)) if g(s, "pe") and g(s, "pe") > 0 and g(s, "eps_growth") and g(s, "eps_growth") > 0 else None for s in syms]
        fr["rev_growth"] = pct_rank([g(s, "rev_growth") for s in syms])
        fr["eps_growth"] = pct_rank([g(s, "eps_growth") for s in syms])
        fr["net_margin"] = pct_rank([g(s, "net_margin") for s in syms])
        fr["roe"] = pct_rank([g(s, "roe") for s in syms])
        fr["peg_inv"] = [None if r is None else 1 - r for r in pct_rank(peg)]
        fr["debt_inv"] = [None if r is None else 1 - r for r in pct_rank([g(s, "debt_equity") for s in syms])]
    out = {}
    for k, s in enumerate(syms):
        def wavg(weights, rk):
            num = den = 0.0
            for name, w in weights.items():
                v = rk.get(name, [None] * len(syms))[k]
                if v is not None:
                    num, den = num + w * v, den + w
            return (num / den if den >= 0.5 * sum(weights.values()) else None), den / sum(weights.values())
        ps, _ = wavg(W_PRICE, ranks)
        fs, fcov = wavg(W_FUND, fr) if fr else (None, 0)
        if ps is None:
            continue
        has_f = fs is not None and fcov >= 0.5
        out[s] = {"score": (0.6 * ps + 0.4 * fs if has_f else ps) * 100, "price_score": ps * 100, "fund_score": fs * 100 if has_f else None,
                  "ranks": {n: (None if v[k] is None else v[k] * 100) for n, v in {**ranks, **fr}.items()}}
    return out


def flags_for(f):
    out = []
    if f["rsi14"] and f["rsi14"] > 75 or (f["dist_sma200"] or 0) > 0.6:
        out.append("przegrzana")
    if (f["vol90"] or 0) > 0.8:
        out.append("bardzo zmienna")
    if f["dd_1y"] < -0.4:
        out.append("głęboka korekta")
    if f["trend"] == 3 and -0.2 <= f["dd_1y"] <= -0.08 and (f["mom_12_1"] or 0) > 0.2:
        out.append("korekta w trendzie wzrostowym")
    if f["trend"] == 0:
        out.append("trend spadkowy")
    return out


def load_stock_series(con, wl):
    out = {}
    for sym, mkt, name, sector in wl:
        rows = con.execute("SELECT ts, c, v FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts", (db_symbol(sym, mkt),)).fetchall()
        if len(rows) >= 120:
            out[db_symbol(sym, mkt)] = {"market": mkt, "name": name, "sector": sector, "dates": [r[0] for r in rows], "close": [r[1] for r in rows], "vol": [r[2] or 0 for r in rows]}
    return out


def load_fundamentals(con, wl):
    out = {}
    for sym, mkt, _, _ in wl:
        rows = con.execute("SELECT metric, value FROM obs WHERE metric LIKE ? AND ts=(SELECT MAX(ts) FROM obs o2 WHERE o2.metric=obs.metric)", (f"fund_{sym}_%",)).fetchall()
        if rows:
            out[db_symbol(sym, mkt)] = {m[len(f"fund_{sym}_"):]: v for m, v in rows}
    return out


def benchmark(con, market):
    for code in BENCH.get(market, ["^spx"]):
        rows = con.execute("SELECT ts, value FROM obs WHERE metric=? ORDER BY ts", (f"stooq_idx_{code}",)).fetchall()
        if len(rows) > 300:
            return [r[0] for r in rows], [r[1] for r in rows], code
    return None, None, None


def backtest(series, benches, step_days=30):
    """Co ~30 dni: ranking wyłącznie z cen z przeszłości; porównanie górnego kwintyla z całym uniwersum (równe wagi)."""
    syms = list(series)
    all_dates = sorted(set(d for s in series.values() for d in s["dates"]))
    if len(all_dates) < 700:
        return None
    start = all_dates[0] + 400 * DAY
    t, periods = start, []
    last = all_dates[-1]
    while t + 95 * DAY <= last:
        feats, idx = {}, {}
        for s in syms:
            x = series[s]
            i = bisect.bisect_right(x["dates"], t) - 1
            if i < 260 or t - x["dates"][i] > 7 * DAY:
                continue
            bd, bc = benches.get(x["market"], (None, None))
            f = price_features(x["dates"], x["close"], x["vol"], i, bd or x["dates"], bc or x["close"])
            if f:
                feats[s], idx[s] = f, i
        if len(feats) >= 12:
            sc = score_universe(feats)
            ranked = sorted(sc, key=lambda s: -sc[s]["score"])
            k = max(3, len(ranked) // 5)
            fwd = {}
            for s in ranked:
                x = series[s]
                res = []
                for horizon in (30, 91):
                    j = bisect.bisect_right(x["dates"], t + horizon * DAY) - 1
                    res.append(x["close"][j] / x["close"][idx[s]] - 1 if j > idx[s] else None)
                fwd[s] = res
            for h in (0, 1):
                allv = [fwd[s][h] for s in ranked if fwd[s][h] is not None]
                top = [fwd[s][h] for s in ranked[:k] if fwd[s][h] is not None]
                bot = [fwd[s][h] for s in ranked[-k:] if fwd[s][h] is not None]
                if allv and top and bot:
                    periods.append((h, t, statistics.fmean(top), statistics.fmean(bot), statistics.fmean(allv), len(ranked)))
        t += step_days * DAY
    out = {}
    for h, label in ((0, "30 dni"), (1, "91 dni")):
        ps = [p for p in periods if p[0] == h]
        if len(ps) < 8:
            continue
        step = 1 if h == 0 else 3                      # nienakładające się okresy do testu t
        ex = [p[2] - p[4] for p in ps[::step]]
        sd = statistics.pstdev(ex) if len(ex) > 2 else 0
        out[label] = {"periods": len(ps), "top_mean_pct": statistics.fmean(p[2] for p in ps) * 100, "universe_mean_pct": statistics.fmean(p[4] for p in ps) * 100,
                      "bottom_mean_pct": statistics.fmean(p[3] for p in ps) * 100, "excess_mean_pct": statistics.fmean(p[2] - p[4] for p in ps) * 100,
                      "beat_universe_pct": sum(1 for p in ps if p[2] > p[4]) / len(ps) * 100,
                      "t_stat": (statistics.fmean(ex) / (sd / math.sqrt(len(ex)))) if sd > 0 else None, "universe_size": round(statistics.fmean(p[5] for p in ps))}
    return out or None


def analyze_stocks(con, wl):
    series = load_stock_series(con, wl)
    if not series:
        return None
    benches = {}
    for m in {s["market"] for s in series.values()}:
        bd, bc, code = benchmark(con, m)
        benches[m] = (bd, bc)
    fund = load_fundamentals(con, wl)
    feats, missing = {}, [db_symbol(s, m) for s, m, _, _ in wl if db_symbol(s, m) not in series]
    for sym, x in series.items():
        bd, bc = benches.get(x["market"], (None, None))
        f = price_features(x["dates"], x["close"], x["vol"], len(x["dates"]) - 1, bd or x["dates"], bc or x["close"])
        if f and datetime.now(timezone.utc).timestamp() - x["dates"][-1] < 14 * DAY:
            feats[sym] = f
        else:
            missing.append(sym + " (za mało lub nieaktualne dane)")
    scores = score_universe(feats, fund)
    ranking = []
    for sym, sc in sorted(scores.items(), key=lambda kv: -kv[1]["score"]):
        x, f = series[sym], feats[sym]
        ranking.append({"symbol": sym, "name": x["name"], "market": x["market"], "sector": x["sector"], "price": x["close"][-1], "as_of": datetime.fromtimestamp(x["dates"][-1], timezone.utc).strftime("%Y-%m-%d"),
                        "score": sc["score"], "price_score": sc["price_score"], "fund_score": sc["fund_score"], "ranks": sc["ranks"], "flags": flags_for(f),
                        "mom_12_1": f["mom_12_1"], "mom_6": f["mom_6"], "rel_6": f["rel_6"], "dd_1y": f["dd_1y"], "vol90": f["vol90"], "rsi14": f["rsi14"], "trend": f["trend"],
                        "fundamentals": fund.get(sym)})
    dips = [r for r in ranking if "korekta w trendzie wzrostowym" in r["flags"]][:8]
    return {"universe": len(series), "ranked": len(ranking), "with_fundamentals": sum(1 for r in ranking if r["fund_score"] is not None), "ranking": ranking, "dips": dips,
            "missing": missing, "backtest": backtest(series, benches), "weights": {"price": W_PRICE, "fundamentals": W_FUND}}


def _perf(dates, cl):
    i = len(cl) - 1
    y = datetime.fromtimestamp(dates[-1], timezone.utc).year
    j = bisect.bisect_left(dates, int(datetime(y, 1, 1, tzinfo=timezone.utc).timestamp()))
    s50, s200 = sma(cl, 50), sma(cl, 200)
    w = cl[-1260:]
    return {"price": cl[-1], "last_date": datetime.fromtimestamp(dates[-1], timezone.utc).strftime("%Y-%m-%d"),
            "chg_1d": ret_back(dates, cl, i, 1), "chg_7d": ret_back(dates, cl, i, 7), "chg_30d": ret_back(dates, cl, i, 30), "chg_90d": ret_back(dates, cl, i, 90),
            "chg_ytd": cl[i] / cl[j - 1] - 1 if 0 < j <= i else None, "chg_1y": ret_back(dates, cl, i, 365),
            "vol_30d": stdev_ann(cl, 30, 252), "rsi14": rsi14(cl), "dd_52w": cl[i] / max(cl[-252:]) - 1, "from_ath_in_data": cl[i] / max(cl) - 1,
            "pctl_5y": sum(1 for v in w if v <= cl[-1]) / len(w) * 100,
            "trend": "wzrostowy" if s50 and s200 and cl[-1] > s50 > s200 else "spadkowy" if s50 and s200 and cl[-1] < s50 < s200 else "boczny"}


def _series(con, symbol):
    r = con.execute("SELECT ts, c FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts", (symbol,)).fetchall()
    return ([x[0] // DAY * DAY for x in r], [x[1] for x in r]) if len(r) >= 260 else None


def _obs(con, metric):
    r = con.execute("SELECT ts, value FROM obs WHERE metric=? ORDER BY ts", (metric,)).fetchall()
    return ([x[0] // DAY * DAY for x in r], [x[1] for x in r]) if len(r) >= 30 else None


def _corr(sa, sb, n, diff_b=False):
    da, db_ = dict(zip(*sa)), dict(zip(*sb))
    common = sorted(set(da) & set(db_))[-n - 1:]
    ra = [da[b] / da[a] - 1 for a, b in zip(common, common[1:]) if da[a]]
    rb = [(db_[b] - db_[a]) if diff_b else (db_[b] / db_[a] - 1) for a, b in zip(common, common[1:]) if db_[a] or diff_b]
    return pearson(ra, rb) if len(ra) == len(rb) else None


def analyze_commodities(con, btc_dates_close=None):
    assets = []
    ser = {}
    for key, (_, label) in COMMODITIES.items():
        sr = _series(con, key)
        if not sr:
            continue
        ser[key] = sr
        assets.append({"key": key, "label": label, **_perf(*sr)})
    if "XAUUSD" not in ser:
        return None
    gd, gc = ser["XAUUSD"]
    out = {"assets": assets, "gold": {}}
    g = out["gold"]
    readings = []

    def tone(label, value, t, note):
        readings.append({"label": label, "value": value, "tone": t, "note": note})
    if "XAGUSD" in ser:
        sd, sc = ser["XAGUSD"]
        sdict = dict(zip(sd, sc))
        gs = [(d, c / sdict[d]) for d, c in zip(gd, gc) if d in sdict and sdict[d]]
        if len(gs) > 260:
            v = [x[1] for x in gs]
            g["gold_silver_ratio"] = {"now": v[-1], "chg_90d": v[-1] / v[-91] - 1 if len(v) > 90 else None, "pctl_5y": sum(1 for x in v[-1260:] if x <= v[-1]) / len(v[-1260:]) * 100}
            tone("Stosunek złoto/srebro", f"{v[-1]:.1f}", "neutral", "wysoki stosunek (percentyl > 80) bywa sygnałem relatywnej słabości srebra i apetytu na bezpieczne aktywa")
    for ccy in ("PLN", "EUR"):
        fx = _obs(con, f"fx_USD{ccy}")
        if fx:
            fd = dict(zip(*fx))
            pts = [(d, c * fd[d]) for d, c in zip(gd, gc) if d in fd]
            if len(pts) > 260:
                dates, v = [p[0] for p in pts], [p[1] for p in pts]
                g[f"gold_{ccy.lower()}"] = {"price": v[-1], "chg_30d": ret_back(dates, v, len(v) - 1, 30), "chg_90d": ret_back(dates, v, len(v) - 1, 90), "chg_1y": ret_back(dates, v, len(v) - 1, 365)}
    if btc_dates_close and btc_dates_close[0]:
        bd = dict(zip(*btc_dates_close))
        pts = [(d, bd[d] / c) for d, c in zip(gd, gc) if d in bd]
        if len(pts) > 260:
            v = [p[1] for p in pts]
            g["btc_in_gold_oz"] = {"now": v[-1], "chg_90d": v[-1] / v[-91] - 1 if len(v) > 90 else None, "pctl_all": sum(1 for x in v if x <= v[-1]) / len(v) * 100}
        g["corr_btc_60d"], g["corr_btc_180d"] = _corr((gd, gc), btc_dates_close, 60), _corr((gd, gc), btc_dates_close, 180)
    dxy = _series(con, "DXY")
    if dxy:
        g["corr_dxy_60d"], g["corr_dxy_180d"] = _corr((gd, gc), dxy, 60), _corr((gd, gc), dxy, 180)
        ch = ret_back(dxy[0], dxy[1], len(dxy[1]) - 1, 90)
        tone("Dolar (DXY), 90 dni", pct_txt(ch), "tailwind" if ch is not None and ch < -0.01 else "headwind" if ch is not None and ch > 0.01 else "neutral", "słabszy dolar zwykle sprzyja złotu")
    spx = _series(con, "SPX")
    if spx:
        g["corr_spx_60d"] = _corr((gd, gc), spx, 60)
    ry = _obs(con, "fred_DFII10")
    if ry:
        i = len(ry[1]) - 1
        d90 = ry[1][i] - ry[1][i - 63] if i >= 63 else None
        g["real_yield"] = {"now": ry[1][i], "chg_90d_pp": d90, "date": datetime.fromtimestamp(ry[0][-1], timezone.utc).strftime("%Y-%m-%d")}
        g["corr_realyield_180d"] = _corr((gd, gc), ry, 180, diff_b=True)
        tone("Realna rentowność USA (10Y)", f"{ry[1][i]:.2f}%" + (f" ({d90:+.2f} pp / 90 dni)" if d90 is not None else ""), "tailwind" if d90 is not None and d90 < -0.15 else "headwind" if d90 is not None and d90 > 0.15 else "neutral", "spadek realnych stóp zwykle sprzyja złotu (niższy koszt alternatywny)")
    gld, gdx = _series(con, "GLD"), _series(con, "GDX")
    if gld and gdx:
        gx = dict(zip(*gdx))
        pts = [(d, gx[d] / c) for d, c in zip(*gld) if d in gx]
        if len(pts) > 100:
            v = [p[1] for p in pts]
            ch = v[-1] / v[-64] - 1 if len(v) > 63 else None
            g["miners_vs_gold"] = {"chg_90d": ch}
            tone("Spółki wydobywcze vs złoto (GDX/GLD), 90 dni", pct_txt(ch), "tailwind" if ch and ch > 0.05 else "headwind" if ch and ch < -0.05 else "neutral", "górnicy zwykle wyprzedzają ruch złota; ich słabość bywa ostrzeżeniem")
    cot = _obs(con, "cot_gold_noncomm_net")
    if cot:
        v = cot[1]
        pct = sum(1 for x in v[-156:] if x <= v[-1]) / len(v[-156:]) * 100
        g["cot_noncomm_net"] = {"now": v[-1], "chg_4w": v[-1] - v[-5] if len(v) > 4 else None, "pctl_3y": pct, "date": datetime.fromtimestamp(cot[0][-1], timezone.utc).strftime("%Y-%m-%d")}
        tone("Pozycje spekulantów (COT)", f"percentyl 3 lat: {pct:.0f}", "headwind" if pct > 90 else "tailwind" if pct < 15 else "neutral", "skrajnie wysokie pozycjonowanie długie oznacza zatłoczony rynek (ryzyko realizacji zysków)")
    p = _perf(gd, gc)
    tone("Trend złota", p["trend"], "tailwind" if p["trend"] == "wzrostowy" else "headwind" if p["trend"] == "spadkowy" else "neutral", "cena względem SMA50 i SMA200")
    g["readings"] = readings
    return out


def pct_txt(v):
    return "n/d" if v is None else f"{v * 100:+.1f}%"


def analyze(con):
    btc = {r[0]: r[1] for r in con.execute("SELECT ts, c FROM candles WHERE symbol='BTCUSDT' AND interval='1d' ORDER BY ts")}
    wl = load_watchlist()
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fx": analyze_fx(con, btc), "commodities": analyze_commodities(con, (sorted(btc), [btc[k] for k in sorted(btc)]) if btc else None), "stocks": analyze_stocks(con, wl),
            "notes": ["Ranking spółek to model czynnikowy oparty na historii cen (i fundamentach, gdy masz klucz Finnhub). Nie jest prognozą ani poradą inwestycyjną.",
                      "Lista spółek to Twój watchlist, więc backtest ma błąd przetrwania: wybierane są dzisiejsze znane nazwy, wynik jest zawyżony. Traktuj go jako test poprawności modelu, nie obietnicę zysku.",
                      "Dane cenowe ze Stooq/Tiingo bywają opóźnione lub niepełne; spółki bez świeżych danych są pomijane (sekcja 'Pominięte').",
                      "Momentum bywa skuteczne w średnim terminie, ale gwałtownie się odwraca (tzw. momentum crash). Sprawdzaj też wycenę, wyniki finansowe i ryzyko."]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["analyze", "watchlist"])
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--out", default=os.path.join(HERE, "btc_markets.json"))
    a = ap.parse_args()
    if a.cmd == "watchlist":
        p = os.path.join(HERE, "watchlist.json")
        if os.path.exists(p):
            sys.exit(p + " już istnieje")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"_info": "market: US, PL lub DE; symbol bez końcówki giełdy", "tickers": [dict(symbol=s, market=m, name=n, sector=sec) for s, m, n, sec in DEFAULT_WATCHLIST]}, fh, ensure_ascii=False, indent=1)
        return print("zapisano", p)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rep = analyze(con)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, separators=(",", ":"), default=float)
    os.replace(tmp, a.out)
    fx, st = rep["fx"], rep["stocks"]
    print(f"FX: {len(fx['currencies']) if fx else 0} walut | spółki: {st['ranked'] if st else 0} w rankingu z {st['universe'] if st else 0}")
    if st:
        for r in st["ranking"][:10]:
            print(f"  {r['symbol']:10} {r['score']:5.1f}  {r['name']}  {', '.join(r['flags'])}")


if __name__ == "__main__":
    main()
