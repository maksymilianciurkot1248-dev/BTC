#!/usr/bin/env python3
"""Dodatkowe dane rynkowe do analizy BTC — wyłącznie darmowe, publiczne API bez kluczy.

Źródła (każde niezależnie: błąd jednego nie psuje reszty):
  sentiment    alternative.me          Fear & Greed Index (30 dni)
  onchain      mempool.space           opłaty, mempool, hashrate, trudność i retarget
  derivs       Binance + OKX           funding, open interest (zmiana 24h), long/short, taker buy/sell
  options      Deribit                 DVOL (zmienność implikowana), put/call, max pain najbliższych wygasań
  premium      Coinbase/Kraken/Binance rozbieżność cen spot (premia USA)
  macro        Stooq                   S&P 500, Nasdaq 100, złoto, dolar, rentowność 10Y, ropa + korelacje z BTC
  stablecoins  DefiLlama               podaż stablecoinów i jej zmiany (suchy proch)
Historia: każdy przebieg dopisuje migawkę do btc_extras_history.jsonl (własne dane rosną z czasem).

UWAGA: te dane są TYLKO opisowe. Nie zmieniają planu ani zleceń (btc_trader.py ich nie czyta).
"""
import csv
import io
import json
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

UA = "btc-extras/1.0"
TIMEOUT = 12
HISTORY_MAX = 6000


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _http_get_basic(url, as_text=False, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/csv,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"połączenie: {getattr(e, 'reason', e)}") from None
    text = raw.decode("utf-8", "replace")
    if as_text:
        return text
    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError("niepoprawny JSON") from None


try:
    import btc_net
except ImportError:  # działa też bez wspólnej warstwy
    btc_net = None


def http_get(url, as_text=False, timeout=TIMEOUT):
    return btc_net.get(url, as_text=as_text, timeout=timeout) if btc_net else _http_get_basic(url, as_text, timeout)


def f(x, default=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def pct_change(new, old):
    new, old = f(new), f(old)
    return (new / old - 1) * 100 if new is not None and old not in (None, 0) else None


def pearson(a, b):
    n = min(len(a), len(b))
    if n < 8:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


# ----------------------------------------------------------------------------
# Źródła
# ----------------------------------------------------------------------------
def src_sentiment(get):
    rows = get("https://api.alternative.me/fng/?limit=30&format=json").get("data") or []
    vals = [(int(r["timestamp"]), int(float(r["value"])), r.get("value_classification")) for r in rows if r.get("value") is not None]
    vals.sort()  # od najstarszego
    if not vals:
        raise RuntimeError("pusta odpowiedź")
    v = [x[1] for x in vals]
    return {"value": v[-1], "label": vals[-1][2], "prev_day": v[-2] if len(v) > 1 else None,
            "prev_week": v[-8] if len(v) > 7 else None, "prev_month": v[0],
            "avg_30d": round(sum(v) / len(v), 1), "series": v}


def src_onchain(get):
    out = {}
    fees = get("https://mempool.space/api/v1/fees/recommended")
    out["fees_sat_vb"] = {"fast": f(fees.get("fastestFee")), "half_hour": f(fees.get("halfHourFee")),
                          "hour": f(fees.get("hourFee")), "economy": f(fees.get("economyFee"))}
    try:
        mp = get("https://mempool.space/api/mempool")
        out["mempool"] = {"tx_count": f(mp.get("count")), "vsize_mb": (f(mp.get("vsize")) or 0) / 1e6}
    except RuntimeError:
        pass
    try:
        da = get("https://mempool.space/api/v1/difficulty-adjustment")
        out["difficulty"] = {"progress_pct": f(da.get("progressPercent")), "change_pct": f(da.get("difficultyChange")),
                             "remaining_blocks": f(da.get("remainingBlocks")),
                             "retarget_ts": f(da.get("estimatedRetargetDate"))}
    except RuntimeError:
        pass
    try:
        hr = get("https://mempool.space/api/v1/mining/hashrate/3m")
        series = [(f(x.get("timestamp")), f(x.get("avgHashrate"))) for x in hr.get("hashrates") or []]
        series = [x for x in series if x[0] and x[1]]
        cur = f(hr.get("currentHashrate")) or (series[-1][1] if series else None)
        ago30 = series[-31][1] if len(series) > 30 else (series[0][1] if series else None)
        out["hashrate"] = {"current_eh": cur / 1e18 if cur else None, "change_30d_pct": pct_change(cur, ago30),
                           "series_eh": [round(s[1] / 1e18, 1) for s in series[-90:]]}
    except RuntimeError:
        pass
    try:
        out["height"] = int(get("https://mempool.space/api/blocks/tip/height", as_text=True).strip())
    except (RuntimeError, ValueError):
        pass
    return out


def _last(rows, key):
    return f(rows[-1].get(key)) if rows else None


def src_derivs(get):
    out, errors = {}, []
    # --- Binance futures USDT-M
    try:
        b = "https://fapi.binance.com"
        pi = get(f"{b}/fapi/v1/premiumIndex?symbol=BTCUSDT")
        mark, index = f(pi.get("markPrice")), f(pi.get("indexPrice"))
        fr = f(pi.get("lastFundingRate"))
        oi = f(get(f"{b}/fapi/v1/openInterest?symbol=BTCUSDT").get("openInterest"))
        binance = {"funding_pct": fr * 100 if fr is not None else None,
                   "funding_annual_pct": fr * 3 * 365 * 100 if fr is not None else None,
                   "basis_pct": pct_change(mark, index), "oi_btc": oi, "oi_usd": oi * mark if oi and mark else None}
        try:
            h = get(f"{b}/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=25")
            vals = [f(x.get("sumOpenInterestValue")) for x in h if f(x.get("sumOpenInterestValue"))]
            binance["oi_change_24h_pct"] = pct_change(vals[-1], vals[0]) if len(vals) > 1 else None
        except RuntimeError as e:
            errors.append(f"Binance OI hist: {e}")
        for key, path in (("ls_accounts", "globalLongShortAccountRatio"), ("ls_top_positions", "topLongShortPositionRatio")):
            try:
                r = get(f"{b}/futures/data/{path}?symbol=BTCUSDT&period=1h&limit=24")
                vals = [f(x.get("longShortRatio")) for x in r if f(x.get("longShortRatio"))]
                binance[key] = {"now": vals[-1], "avg_24h": sum(vals) / len(vals), "long_pct": (f(r[-1].get("longAccount")) or 0) * 100} if vals else None
            except RuntimeError as e:
                errors.append(f"Binance {path}: {e}")
        try:
            r = get(f"{b}/futures/data/takerlongshortRatio?symbol=BTCUSDT&period=1h&limit=24")
            vals = [f(x.get("buySellRatio")) for x in r if f(x.get("buySellRatio"))]
            binance["taker_buy_sell"] = {"now": vals[-1], "avg_24h": sum(vals) / len(vals)} if vals else None
        except RuntimeError as e:
            errors.append(f"Binance taker: {e}")
        out["binance"] = binance
    except RuntimeError as e:
        errors.append(f"Binance: {e}")
    # --- OKX swap
    try:
        o = "https://www.okx.com/api/v5"
        fr = f(((get(f"{o}/public/funding-rate?instId=BTC-USDT-SWAP").get("data") or [{}])[0]).get("fundingRate"))
        oi = (get(f"{o}/public/open-interest?instType=SWAP&instId=BTC-USDT-SWAP").get("data") or [{}])[0]
        okx = {"funding_pct": fr * 100 if fr is not None else None,
               "funding_annual_pct": fr * 3 * 365 * 100 if fr is not None else None, "oi_usd": f(oi.get("oiUsd"))}
        try:
            ls = get(f"{o}/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1H").get("data") or []
            vals = [f(x[1]) for x in ls if len(x) > 1 and f(x[1])]
            okx["ls_accounts"] = {"now": vals[0], "avg_24h": sum(vals[:24]) / len(vals[:24])} if vals else None
        except RuntimeError as e:
            errors.append(f"OKX L/S: {e}")
        out["okx"] = okx
    except RuntimeError as e:
        errors.append(f"OKX: {e}")
    if not out:
        raise RuntimeError("; ".join(errors) or "brak danych")
    fr = [v["funding_pct"] for v in out.values() if v.get("funding_pct") is not None]
    out["funding_avg_pct"] = sum(fr) / len(fr) if fr else None
    out["funding_avg_annual_pct"] = out["funding_avg_pct"] * 3 * 365 if fr else None
    out["warnings"] = errors
    return out


def _parse_instrument(name):
    # BTC-27DEC24-60000-C
    try:
        _, d, k, t = name.split("-")
        return datetime.strptime(d, "%d%b%y").replace(hour=8, tzinfo=timezone.utc), float(k), t
    except (ValueError, AttributeError):
        return None


def max_pain(strikes):
    """strikes: {strike: [call_oi, put_oi]} -> strike minimalizujący wypłatę kupujących opcje."""
    if not strikes:
        return None
    best, best_val = None, None
    for k in sorted(strikes):
        pain = sum(c * max(0.0, k - s) + p * max(0.0, s - k) for s, (c, p) in strikes.items())
        if best_val is None or pain < best_val:
            best, best_val = k, pain
    return best


def src_options(get, now=None):
    now = now or datetime.now(timezone.utc)
    out = {}
    try:
        end = int(now.timestamp() * 1000)
        rows = get(f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC&resolution=1D"
                   f"&start_timestamp={end - 45 * 86400_000}&end_timestamp={end}").get("result", {}).get("data") or []
        closes = [f(r[4]) for r in rows if len(r) > 4 and f(r[4])]
        if closes:
            cur = closes[-1]
            out["dvol"] = {"now": cur, "min_30d": min(closes[-30:]), "max_30d": max(closes[-30:]),
                           "percentile_30d": sum(1 for c in closes[-30:] if c <= cur) / len(closes[-30:]) * 100,
                           "change_7d": cur - closes[-8] if len(closes) > 7 else None, "series": [round(c, 1) for c in closes[-30:]]}
    except RuntimeError as e:
        out["dvol_error"] = str(e)
    summ = get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option").get("result") or []
    calls_oi = puts_oi = calls_vol = puts_vol = 0.0
    by_exp = {}
    for s in summ:
        p = _parse_instrument(s.get("instrument_name"))
        if not p:
            continue
        exp, strike, typ = p
        oi, vol = f(s.get("open_interest"), 0.0), f(s.get("volume"), 0.0)
        if typ == "C":
            calls_oi += oi; calls_vol += vol
        else:
            puts_oi += oi; puts_vol += vol
        e = by_exp.setdefault(exp, {})
        cell = e.setdefault(strike, [0.0, 0.0])
        cell[0 if typ == "C" else 1] += oi
    if calls_oi <= 0 and puts_oi <= 0:
        raise RuntimeError("brak opcji w odpowiedzi")
    out["put_call_oi"] = puts_oi / calls_oi if calls_oi else None
    out["put_call_volume"] = puts_vol / calls_vol if calls_vol else None
    out["oi_btc"] = calls_oi + puts_oi
    exps = []
    for exp in sorted(e for e in by_exp if e > now):
        st = by_exp[exp]
        c, p = sum(v[0] for v in st.values()), sum(v[1] for v in st.values())
        if c + p <= 0:
            continue
        top = sorted(st.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:3]
        exps.append({"expiry": exp.date().isoformat(), "days": round((exp - now).total_seconds() / 86400, 1),
                     "call_oi": c, "put_oi": p, "put_call": p / c if c else None, "max_pain": max_pain(st),
                     "top_strikes": [k for k, _ in top]})
    out["expiries"] = exps[:4]
    return out


def src_premium(get):
    px = {}
    for name, fn in (
        ("coinbase", lambda: f(get("https://api.exchange.coinbase.com/products/BTC-USD/ticker").get("price"))),
        ("kraken", lambda: f((get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD").get("result") or {}).get(
            "XXBTZUSD", {}).get("c", [None])[0])),
        ("binance", lambda: f(get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT").get("price"))),
    ):
        try:
            v = fn()
            if v:
                px[name] = v
        except (RuntimeError, AttributeError, IndexError, TypeError):
            pass
    if len(px) < 2:
        raise RuntimeError("za mało giełd odpowiedziało")
    out = {"prices": px}
    if "coinbase" in px and "binance" in px:
        out["coinbase_vs_binance_pct"] = pct_change(px["coinbase"], px["binance"])
    if "coinbase" in px and "kraken" in px:
        out["coinbase_vs_kraken_pct"] = pct_change(px["coinbase"], px["kraken"])
    out["spread_pct"] = (max(px.values()) / min(px.values()) - 1) * 100
    out["note"] = "Binance notuje w USDT, Coinbase i Kraken w USD: część różnicy to kurs USDT/USD."
    return out


MACRO = [("spx", "S&P 500", ["^spx"]), ("ndx", "Nasdaq 100", ["^ndx"]), ("gold", "Złoto (USD/oz)", ["xauusd"]),
         ("dxy", "Indeks dolara", ["^dxy", "dx.f"]), ("us10y", "Rentowność USA 10Y (%)", ["10usy.b"]),
         ("oil", "Ropa WTI", ["cl.f"])]


def _stooq(get, symbol):
    text = get(f"https://stooq.com/q/d/l/?s={symbol}&i=d", as_text=True)
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        c = f(r.get("Close"))
        if r.get("Date") and c:
            out.append((r["Date"], c))
    if len(out) < 10:
        raise RuntimeError("za mało danych")
    return out[-130:]


def _returns_on_common_dates(btc, other):
    """Zwroty obu serii na wspólnych datach (piątek->poniedziałek liczony tak samo dla obu)."""
    common = sorted(set(btc) & set(other))
    rb, ro = [], []
    for a, b in zip(common, common[1:]):
        if btc[a] and other[a]:
            rb.append(btc[b] / btc[a] - 1)
            ro.append(other[b] / other[a] - 1)
    return rb, ro


def src_macro(get, btc_map=None):
    out = {}
    for key, label, symbols in MACRO:
        series, last_err = None, None
        for sym in symbols:
            try:
                series = _stooq(get, sym)
                break
            except RuntimeError as e:
                last_err = e
        if not series:
            out[key] = {"label": label, "error": str(last_err)}
            continue
        cl = [c for _, c in series]
        item = {"label": label, "last": cl[-1], "date": series[-1][0],
                "change_1d_pct": pct_change(cl[-1], cl[-2]), "change_7d_pct": pct_change(cl[-1], cl[-6]) if len(cl) > 5 else None,
                "change_30d_pct": pct_change(cl[-1], cl[-22]) if len(cl) > 21 else None}
        if btc_map:
            other = dict(series)
            for n in (30, 90):
                cutoff = sorted(other)[-n:] if len(other) >= n else sorted(other)
                rb, ro = _returns_on_common_dates({d: btc_map[d] for d in cutoff if d in btc_map}, {d: other[d] for d in cutoff})
                item[f"corr_{n}d"] = pearson(rb, ro)
        out[key] = item
    if all("error" in v for v in out.values()):
        raise RuntimeError("Stooq nie odpowiada")
    return out


def src_stablecoins(get):
    rows = get("https://stablecoins.llama.fi/stablecoins?includePrices=false").get("peggedAssets") or []
    usd = [r for r in rows if r.get("pegType") == "peggedUSD"]

    def val(r, k):
        return f((r.get(k) or {}).get("peggedUSD"), 0.0)
    tot = {k: sum(val(r, k) for r in usd) for k in ("circulating", "circulatingPrevDay", "circulatingPrevWeek", "circulatingPrevMonth")}
    if tot["circulating"] <= 0:
        raise RuntimeError("pusta odpowiedź")
    top = sorted(usd, key=lambda r: -val(r, "circulating"))[:5]
    return {"total_usd": tot["circulating"], "change_1d_pct": pct_change(tot["circulating"], tot["circulatingPrevDay"]),
            "change_7d_pct": pct_change(tot["circulating"], tot["circulatingPrevWeek"]),
            "change_30d_pct": pct_change(tot["circulating"], tot["circulatingPrevMonth"]),
            "top": [{"symbol": r.get("symbol"), "usd": val(r, "circulating"), "share_pct": val(r, "circulating") / tot["circulating"] * 100,
                     "change_30d_pct": pct_change(val(r, "circulating"), val(r, "circulatingPrevMonth"))} for r in top]}


# ----------------------------------------------------------------------------
# Sygnały opisowe (nie wpływają na plan ani na zlecenia)
# ----------------------------------------------------------------------------
def build_signals(x):
    sig = []

    def add(id_, label, state, text):
        sig.append({"id": id_, "label": label, "state": state, "text": text})
    s = x.get("sentiment")
    if s:
        v = s["value"]
        add("fng", "Fear & Greed", "bull" if v <= 25 else "bear" if v >= 75 else "neutral",
            f"Indeks {v} ({s.get('label')}). " + ("Skrajny strach bywa kontrariańsko korzystny dla kupujących, ale sam nie jest sygnałem wejścia." if v <= 25
                                                  else "Chciwość podnosi ryzyko korekty." if v >= 75 else "Nastroje bez skrajności."))
    d = x.get("derivs")
    if d:
        fa = d.get("funding_avg_annual_pct")
        if fa is not None:
            add("funding", "Funding (Binance+OKX)", "bear" if fa > 30 else "bull" if fa < -10 else "neutral",
                f"Średnio {fa:.1f}% w skali roku. " + ("Długie pozycje są drogie i zatłoczone." if fa > 30 else
                                                      "Ujemny funding: krótkie płacą, możliwy short squeeze." if fa < -10 else "Poziom normalny."))
        b = d.get("binance") or {}
        ls = (b.get("ls_accounts") or {}).get("now")
        if ls:
            add("ls", "Long/Short (konta, Binance)", "bear" if ls > 2 else "bull" if ls < 0.8 else "neutral",
                f"Stosunek {ls:.2f}. " + ("Przewaga długich: ryzyko likwidacji w dół." if ls > 2 else "Przewaga krótkich: paliwo do odbicia." if ls < 0.8 else "Bez skrajności."))
        oc = b.get("oi_change_24h_pct")
        if oc is not None and abs(oc) >= 3:
            add("oi", "Open interest 24 godz.", "neutral", f"OI zmienił się o {oc:+.1f}% — rośnie lewar, ruchy ceny mogą być gwałtowniejsze." if oc > 0 else f"OI spadł o {abs(oc):.1f}% — pozycje są zamykane.")
    o = x.get("options") or {}
    if o.get("dvol"):
        p = o["dvol"]["percentile_30d"]
        add("dvol", "DVOL (zmienność implikowana)", "neutral", f"{o['dvol']['now']:.1f}, percentyl 30 dni: {p:.0f}. " + ("Rynek wycenia dużą zmienność." if p >= 80 else "Zmienność wycenia nisko, ruchy bywają po ciszy gwałtowne." if p <= 20 else "Zmienność w normie."))
    pc = o.get("put_call_oi")
    if pc is not None:
        add("pc", "Put/Call (OI, Deribit)", "bear" if pc > 1 else "bull" if pc < 0.5 else "neutral",
            f"{pc:.2f}. " + ("Przewaga zabezpieczeń/zakładów na spadki." if pc > 1 else "Przewaga zakładów na wzrosty." if pc < 0.5 else "Układ zrównoważony."))
    pr = x.get("premium") or {}
    cb = pr.get("coinbase_vs_binance_pct")
    if cb is not None:
        add("cb", "Premia Coinbase", "bull" if cb > 0.15 else "bear" if cb < -0.15 else "neutral",
            f"{cb:+.3f}% vs Binance. " + ("Silniejszy popyt z USA (część to kurs USDT)." if cb > 0.15 else "Słabszy popyt z USA." if cb < -0.15 else "Ceny zbliżone."))
    st = x.get("stablecoins")
    if st and st.get("change_30d_pct") is not None:
        c = st["change_30d_pct"]
        add("stable", "Podaż stablecoinów", "bull" if c > 2 else "bear" if c < -1 else "neutral",
            f"{c:+.2f}% w 30 dni. " + ("Napływ kapitału na rynek." if c > 2 else "Kapitał ucieka z rynku." if c < -1 else "Bez wyraźnej zmiany."))
    h = (x.get("onchain") or {}).get("hashrate")
    if h and h.get("change_30d_pct") is not None:
        c = h["change_30d_pct"]
        add("hash", "Hashrate", "bear" if c < -10 else "neutral", f"{c:+.1f}% w 30 dni. " + ("Spadek presuje górników." if c < -10 else "Sieć stabilna."))
    m = x.get("macro") or {}
    spx = m.get("spx") or {}
    if spx.get("corr_30d") is not None:
        add("macro", "Korelacja z S&P 500 (30 dni)", "neutral", f"{spx['corr_30d']:+.2f}. " + ("BTC porusza się razem z akcjami." if spx["corr_30d"] > 0.5 else "Słaba zależność od akcji." if abs(spx["corr_30d"]) < 0.2 else "Umiarkowana zależność."))
    bulls, bears = sum(1 for s in sig if s["state"] == "bull"), sum(1 for s in sig if s["state"] == "bear")
    return sig, {"bull": bulls, "bear": bears, "neutral": len(sig) - bulls - bears}


# ----------------------------------------------------------------------------
# Historia własna
# ----------------------------------------------------------------------------
def snapshot(x):
    g = lambda *p: _dig(x, p)
    return {"t": int(time.time()), "fng": g("sentiment", "value"), "funding_annual": g("derivs", "funding_avg_annual_pct"),
            "oi_usd": g("derivs", "binance", "oi_usd"), "ls": g("derivs", "binance", "ls_accounts", "now"),
            "dvol": g("options", "dvol", "now"), "pc": g("options", "put_call_oi"), "cb_prem": g("premium", "coinbase_vs_binance_pct"),
            "stable": g("stablecoins", "total_usd"), "hash_eh": g("onchain", "hashrate", "current_eh"),
            "fee": g("onchain", "fees_sat_vb", "half_hour")}


def _dig(d, path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def update_history(path, snap, min_gap_s=3000):
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        rows = []
    if not rows or snap["t"] - rows[-1].get("t", 0) >= min_gap_s:
        rows.append(snap)
    rows = rows[-HISTORY_MAX:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
    os.replace(tmp, path)
    return rows


# ----------------------------------------------------------------------------
def collect_extras(btc_dates=None, btc_closes=None, history_path=None, get=None, now=None):
    get = get or http_get
    btc_map = dict(zip(btc_dates, btc_closes)) if btc_dates and btc_closes else None
    jobs = {"sentiment": lambda: src_sentiment(get), "onchain": lambda: src_onchain(get), "derivs": lambda: src_derivs(get),
            "options": lambda: src_options(get, now), "premium": lambda: src_premium(get),
            "macro": lambda: src_macro(get, btc_map), "stablecoins": lambda: src_stablecoins(get)}
    out, status = {}, {}

    def run(name):
        t0 = time.time()
        try:
            return name, jobs[name](), None, time.time() - t0
        except Exception as e:  # noqa: BLE001 — jedno źródło nigdy nie psuje reszty
            return name, None, f"{type(e).__name__}: {e}", time.time() - t0
    with ThreadPoolExecutor(max_workers=7) as ex:
        for name, res, err, dt in ex.map(run, list(jobs)):
            status[name] = {"ok": res is not None, "error": err, "seconds": round(dt, 1)}
            if res is not None:
                out[name] = res
    out["sources"] = status
    out["signals"], out["tally"] = build_signals(out)
    out["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if history_path:
        try:
            rows = update_history(history_path, snapshot(out))
            keys = ("t", "fng", "funding_annual", "oi_usd", "ls", "dvol", "pc", "cb_prem", "stable", "hash_eh", "fee")
            out["history"] = {"points": len(rows), "since": rows[0]["t"] if rows else None,
                              "series": [{k: r.get(k) for k in keys} for r in rows[-240:]]}
        except OSError as e:
            status["history"] = {"ok": False, "error": str(e), "seconds": 0}
    return out


if __name__ == "__main__":
    import sys
    print(json.dumps(collect_extras(history_path=None), indent=1, ensure_ascii=False)[:6000], file=sys.stdout)
