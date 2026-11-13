#!/usr/bin/env python3
"""Analiza sytuacji: co historycznie poprzedzało ruchy w górę/w dół danego aktywa i które z tych sytuacji właśnie się zbliżają.

Aktywa: BTC, ETH, złoto, srebro, S&P 500, Nasdaq 100, indeks dolara, ropa, EUR/USD, USD/PLN, USD/JPY.
Sytuacje:
  własne      RSI, ściśnięcie Bollingera, nowy szczyt 52 tyg., spadek 20% od szczytu, skrajne dni, krzyżyk SMA50/200, SMA200, skok zmienności
  zewnętrzne  dolar (DXY), VIX, realna rentowność USA, spadek S&P 500, złoto, ropa, Fear & Greed, funding BTC
  zdarzenia   CPI, NFP, FOMC, PCE, GDP, wygasanie opcji (miesięczne i kwartalne), faza po halvingu BTC
Dla każdej pary (aktywo, sytuacja, horyzont): średni zwrot po wystąpieniu vs zwykły zwrot, % dodatnich, test t, korekta FDR
(Benjamini-Hochberg), stabilność w obu połowach historii. Kolejne wystąpienia są rozdzielone o co najmniej horyzont (bez nakładania).
'Teraz': sytuacje aktywne (przekroczone w ostatnich 3 sesjach), zbliżające się (blisko progu) oraz zdarzenia w kalendarzu.
Użycie:  python btc_situations.py [--out btc_situations.json]
To opis historii, nie prognoza ani porada inwestycyjna.
"""
import argparse
import bisect
import json
import math
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86400
MIN_N = 12
STATE_H = (1, 5, 20)
EVENT_H = (0, 1, 5)
HALVING_H = (20, 60, 120)

ASSETS = {"BTC": ("c", "BTCUSDT", "Bitcoin"), "ETH": ("c", "ETHUSDT", "Ethereum"), "GOLD": ("c", "XAUUSD", "Złoto"), "SILVER": ("c", "XAGUSD", "Srebro"),
          "SPX": ("c", "SPX", "S&P 500"), "NDX": ("c", "NDX", "Nasdaq 100"), "DXY": ("c", "DXY", "Indeks dolara"), "OIL": ("c", "WTI", "Ropa WTI"),
          "EURUSD": ("fxinv", "EUR", "EUR/USD"), "USDPLN": ("fx", "PLN", "USD/PLN"), "USDJPY": ("fx", "JPY", "USD/JPY")}
FALLBACK = {"GOLD": "stooq_gold", "SPX": "stooq_spx", "NDX": "stooq_ndx", "DXY": "stooq_dxy", "OIL": "stooq_oil"}
EVENT_LABELS = {"cpi": "publikacja CPI (inflacja USA)", "nfp": "raport z rynku pracy USA (NFP)", "fomc": "decyzja FOMC", "pce": "inflacja PCE", "gdp": "PKB USA",
                "retail": "sprzedaż detaliczna USA", "jolts": "JOLTS (oferty pracy)", "ppi": "PPI (ceny producentów)", "opex_m": "miesięczne wygasanie opcji",
                "opex_q": "kwartalne wygasanie opcji"}


# ----------------------------------------------------------------------------
# Szeregi i wskaźniki
# ----------------------------------------------------------------------------
def sma(xs, n):
    out, s, cnt, q = [], 0.0, 0, []
    for x in xs:
        q.append(x)
        s += x
        if len(q) > n:
            s -= q.pop(0)
        out.append(s / n if len(q) == n else None)
    return out


def rolling_std(xs, n):
    out = []
    for i in range(len(xs)):
        w = xs[max(0, i - n + 1):i + 1]
        out.append(statistics.pstdev(w) if len(w) == n and None not in w else None)
    return out


def rets(cl):
    return [None] + [cl[i] / cl[i - 1] - 1 if cl[i - 1] else None for i in range(1, len(cl))]


def m_rsi(cl, n=14):
    out, ag, al = [None] * len(cl), 0.0, 0.0
    for i in range(1, len(cl)):
        d = cl[i] - cl[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            ag, al = ag + g / n, al + l / n
            if i == n:
                out[i] = 100 - 100 / (1 + ag / al) if al else 100.0
        else:
            ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
            out[i] = 100 - 100 / (1 + ag / al) if al else 100.0
    return out


def m_bw_pctl(cl, n=20, win=250):
    """Percentyl szerokości wstęg Bollingera (20 sesji) w ostatnich 250 sesjach: niski = ściśnięcie zmienności."""
    mid, sd = sma(cl, n), rolling_std(cl, n)
    bw = [(4 * s / m) if m and s is not None else None for m, s in zip(mid, sd)]
    out = []
    for i, b in enumerate(bw):
        hist = [x for x in bw[max(0, i - win + 1):i + 1] if x is not None]
        out.append(sum(1 for x in hist if x <= b) / len(hist) * 100 if b is not None and len(hist) > 100 else None)
    return out


def m_dd(cl, win=252):
    out = []
    for i in range(len(cl)):
        hi = max(cl[max(0, i - win + 1):i + 1])
        out.append(cl[i] / hi - 1 if i >= 60 else None)
    return out


def m_ret_z(cl, win=60):
    r = rets(cl)
    sd = rolling_std([x if x is not None else 0 for x in r], win)
    return [(r[i] / sd[i]) if r[i] is not None and sd[i] else None for i in range(len(cl))]


def m_ratio(cl, a=50, b=200):
    sa, sb = sma(cl, a), sma(cl, b)
    return [x / y - 1 if x and y else None for x, y in zip(sa, sb)]


def m_sma_dist(cl, n=200):
    s = sma(cl, n)
    return [c / x - 1 if x else None for c, x in zip(cl, s)]


def m_volratio(cl, a=10, b=90):
    r = [x or 0 for x in rets(cl)]
    sa, sb = rolling_std(r, a), rolling_std(r, b)
    return [x / y if x and y else None for x, y in zip(sa, sb)]


def m_chg(vals, k, diff=False):
    out = []
    for i, v in enumerate(vals):
        p = vals[i - k] if i >= k else None
        out.append(None if v is None or p is None or (not diff and p == 0) else (v - p if diff else v / p - 1))
    return out


def m_level(vals):
    return list(vals)


# ----------------------------------------------------------------------------
# Wczytywanie
# ----------------------------------------------------------------------------
def load_asset(con, key):
    kind, code, _ = ASSETS[key]
    if kind == "c":
        rows = con.execute("SELECT ts, c FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts", (code,)).fetchall()
        if len(rows) < 300 and key in FALLBACK:
            rows = con.execute("SELECT ts, value FROM obs WHERE metric=? ORDER BY ts", (FALLBACK[key],)).fetchall()
    else:
        pts = con.execute("SELECT ts, value FROM obs WHERE metric=? ORDER BY ts", (f"fx_USD{code}",)).fetchall()
        rows = [(t, (1 / v) if kind == "fxinv" else v) for t, v in pts if v]
    rows = [(t // DAY * DAY, v) for t, v in rows if v]
    return ([r[0] for r in rows], [r[1] for r in rows]) if len(rows) >= 300 else None


def load_obs(con, metric):
    rows = con.execute("SELECT ts, value FROM obs WHERE metric=? ORDER BY ts", (metric,)).fetchall()
    return ([r[0] // DAY * DAY for r in rows], [r[1] for r in rows]) if len(rows) >= 100 else None


def daily_mean(con, metric):
    acc = {}
    for ts, v in con.execute("SELECT ts, value FROM obs WHERE metric=? ORDER BY ts", (metric,)):
        acc.setdefault(ts // DAY * DAY, []).append(v)
    d = sorted(acc)
    return (d, [sum(acc[k]) for k in d]) if len(d) >= 100 else None


def asof(src_dates, src_vals, target_dates, lag_days=0, max_stale=10):
    out = []
    for t in target_dates:
        j = bisect.bisect_right(src_dates, t - lag_days * DAY) - 1
        out.append(src_vals[j] if j >= 0 and t - lag_days * DAY - src_dates[j] <= max_stale * DAY else None)
    return out


def load_events(con):
    ev = {}
    for kind, ts in con.execute("SELECT kind, ts FROM events ORDER BY ts"):
        ev.setdefault(kind, []).append(ts // DAY * DAY)
    return ev


# ----------------------------------------------------------------------------
# Definicje sytuacji
# ----------------------------------------------------------------------------
# (id, etykieta, metryka(close)->lista, próg, kierunek przekroczenia)
OWN = [
    ("rsi_low", "RSI spada poniżej 30 (wyprzedanie)", m_rsi, 30, "below"),
    ("rsi_high", "RSI rośnie powyżej 70 (wykupienie)", m_rsi, 70, "above"),
    ("squeeze", "ściśnięcie zmienności (wstęgi Bollingera w dolnych 10%)", m_bw_pctl, 10, "below"),
    ("new_high", "nowy szczyt 52 tygodni", m_dd, -0.0001, "above"),
    ("dd20", "spadek o 20% od szczytu 52 tyg.", m_dd, -0.20, "below"),
    ("big_down", "wyjątkowo słaby dzień (−3σ)", m_ret_z, -3, "below"),
    ("big_up", "wyjątkowo mocny dzień (+3σ)", m_ret_z, 3, "above"),
    ("golden", "krzyżyk wzrostowy (SMA50 przebija SMA200)", m_ratio, 0, "above"),
    ("death", "krzyżyk spadkowy (SMA50 spada pod SMA200)", m_ratio, 0, "below"),
    ("reclaim200", "cena odzyskuje SMA200", m_sma_dist, 0, "above"),
    ("lose200", "cena traci SMA200", m_sma_dist, 0, "below"),
    ("volspike", "skok zmienności (10 dni vs 90 dni > 1,8×)", m_volratio, 1.8, "above"),
]
# (id, etykieta, sterownik, metryka(values)->lista, próg, kierunek, aktywa wyłączone)
DRV = [
    ("dxy_up", "dolar gwałtownie się umacnia (DXY +2% w 20 sesji)", "DXY", lambda v: m_chg(v, 20), 0.02, "above", {"DXY", "EURUSD", "USDPLN", "USDJPY"}),
    ("dxy_down", "dolar gwałtownie słabnie (DXY −2% w 20 sesji)", "DXY", lambda v: m_chg(v, 20), -0.02, "below", {"DXY", "EURUSD", "USDPLN", "USDJPY"}),
    ("vix25", "VIX przekracza 25 (strach na rynku akcji)", "VIX", m_level, 25, "above", {"VIX"}),
    ("vix_jump", "VIX rośnie o 30% w 5 sesji", "VIX", lambda v: m_chg(v, 5), 0.30, "above", {"VIX"}),
    ("ry_up", "realna rentowność USA rośnie o 0,25 pp w 20 sesji", "REALY", lambda v: m_chg(v, 20, True), 0.25, "above", set()),
    ("ry_down", "realna rentowność USA spada o 0,25 pp w 20 sesji", "REALY", lambda v: m_chg(v, 20, True), -0.25, "below", set()),
    ("spx_dd10", "S&P 500 spada o 10% od szczytu", "SPX", m_dd, -0.10, "below", {"SPX", "NDX"}),
    ("gold_surge", "złoto +5% w 10 sesji", "GOLD", lambda v: m_chg(v, 10), 0.05, "above", {"GOLD", "SILVER"}),
    ("gold_drop", "złoto −5% w 10 sesji", "GOLD", lambda v: m_chg(v, 10), -0.05, "below", {"GOLD", "SILVER"}),
    ("oil_surge", "ropa +15% w 20 sesji", "OIL", lambda v: m_chg(v, 20), 0.15, "above", {"OIL"}),
    ("oil_drop", "ropa −15% w 20 sesji", "OIL", lambda v: m_chg(v, 20), -0.15, "below", {"OIL"}),
    ("fng_fear", "Fear & Greed poniżej 20 (skrajny strach)", "FNG", m_level, 20, "below", {"BTC_ONLY"}),
    ("fng_greed", "Fear & Greed powyżej 80 (skrajna chciwość)", "FNG", m_level, 80, "above", {"BTC_ONLY"}),
    ("fund_hot", "funding BTC powyżej 30% rocznie (zatłoczone longi)", "FUND", m_level, 30, "above", {"BTC_ONLY"}),
    ("fund_neg", "funding BTC poniżej −5% rocznie (zatłoczone shorty)", "FUND", m_level, -5, "below", {"BTC_ONLY"}),
]
DRIVER_LAG = {"REALY": 1, "VIX": 0, "FNG": 0, "FUND": 0}


def build_drivers(con, assets):
    """Szeregi sterowników na ich własnym kalendarzu: (daty, wartości, opóźnienie publikacji w dniach)."""
    drv = {}
    for k in ("DXY", "SPX", "GOLD", "OIL"):
        if k in assets:
            drv[k] = (*assets[k], 0)
    vix = con.execute("SELECT ts, c FROM candles WHERE symbol='VIX' AND interval='1d' ORDER BY ts").fetchall()
    if len(vix) > 100:
        drv["VIX"] = ([r[0] // DAY * DAY for r in vix], [r[1] for r in vix], 0)
    else:
        o = load_obs(con, "fred_VIXCLS")
        if o:
            drv["VIX"] = (*o, 1)
    o = load_obs(con, "fred_DFII10")
    if o:
        drv["REALY"] = (*o, 1)
    o = load_obs(con, "fng")
    if o:
        drv["FNG"] = (*o, 0)
    o = daily_mean(con, "funding_binance_8h")
    if o:
        drv["FUND"] = (o[0], [v * 365 for v in o[1]], 0)     # suma dobowa funding w % -> % rocznie
    return drv


# ----------------------------------------------------------------------------
# Statystyka
# ----------------------------------------------------------------------------
def crossings(m, thr, direction):
    out = []
    for i in range(1, len(m)):
        a, b = m[i - 1], m[i]
        if a is None or b is None:
            continue
        if (direction == "above" and a <= thr < b) or (direction == "below" and a >= thr > b):
            out.append(i)
    return out


def spaced(idx, gap):
    out, last = [], -10 ** 9
    for i in idx:
        if i - last >= gap:
            out.append(i)
            last = i
    return out


def fwd_ret(cl, t, h):
    if h == 0:
        return cl[t] / cl[t - 1] - 1 if t >= 1 and cl[t - 1] else None
    return cl[t + h] / cl[t] - 1 if t + h < len(cl) and cl[t] else None


def baseline(cl, h):
    hh = max(h, 1)
    r = [cl[i + hh] / cl[i] - 1 for i in range(len(cl) - hh) if cl[i]]
    ab = [abs(x) for x in r]
    return {"mean": statistics.fmean(r), "std": statistics.pstdev(r) or 1e-9, "hit": sum(1 for x in r if x > 0) / len(r),
            "abs": statistics.fmean(ab) if hh == 1 else None, "abs_std": (statistics.pstdev(ab) or 1e-9) if hh == 1 else None}


def evaluate(cl, trig, h, base):
    gap = max(h, 5)
    sel = spaced(trig, gap)
    pairs = [(t, fwd_ret(cl, t, h)) for t in sel]
    pairs = [(t, r) for t, r in pairs if r is not None]
    n = len(pairs)
    if n < MIN_N:
        return None
    rs = [r for _, r in pairs]
    m = statistics.fmean(rs)
    edge = m - base["mean"]
    z = edge / (base["std"] / math.sqrt(n))
    half = n // 2
    e1, e2 = statistics.fmean(rs[:half]) - base["mean"], statistics.fmean(rs[half:]) - base["mean"]
    out = {"n": n, "mean_pct": m * 100, "median_pct": statistics.median(rs) * 100, "hit_pct": sum(1 for r in rs if r > 0) / n * 100,
           "base_hit_pct": base["hit"] * 100, "edge_pct": edge * 100, "t": z, "p": math.erfc(abs(z) / math.sqrt(2)),
           "stable": n >= 16 and e1 * edge > 0 and e2 * edge > 0, "direction": "up" if edge > 0 else "down"}
    if h in (0, 1) and base.get("abs"):
        ma = statistics.fmean(abs(r) for r in rs)
        zv = (ma - base["abs"]) / (base["abs_std"] / math.sqrt(n))
        out["abs_lift"] = ma / base["abs"]
        out["vol"] = {"n": n, "abs_lift": ma / base["abs"], "t": zv, "p": math.erfc(abs(zv) / math.sqrt(2)), "mean_abs_pct": ma * 100, "base_abs_pct": base["abs"] * 100}
    return out


def bh_fdr(ps):
    m = len(ps)
    order = sorted(range(m), key=ps.__getitem__)
    q, prev = [1.0] * m, 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, ps[i] * m / rank)
        q[i] = prev
    return q


def add_tests(tests, sig, h, r):
    """Dodaje test kierunku zwrotu oraz (dla h=0/1) osobny test wpływu na zmienność."""
    if not r:
        return
    v = r.pop("vol", None)
    tests.append({**sig, "h": h, **r})
    if v:
        tests.append({**sig, "h": h, "metric": "vol", "direction": "vol", "stable": False, "edge_pct": 0.0, "mean_pct": v["mean_abs_pct"], "median_pct": 0.0,
                      "hit_pct": 0.0, "base_hit_pct": 0.0, **v})


def next_trading_index(dates, ts):
    i = bisect.bisect_left(dates, ts)
    return i if i < len(dates) and dates[i] - ts <= 4 * DAY else None


# ----------------------------------------------------------------------------
# Główna analiza
# ----------------------------------------------------------------------------
def analyze(con, now_ts=None):
    now_ts = now_ts or int(datetime.now(timezone.utc).timestamp())
    assets = {k: v for k in ASSETS if (v := load_asset(con, k))}
    if "BTC" not in assets:
        raise SystemExit("Brak danych BTC w bazie: uruchom btc_collect.py backfill.")
    drv = build_drivers(con, assets)
    events = load_events(con)
    tests, meta = [], {}
    for a, (dates, cl) in assets.items():
        bases = {h: baseline(cl, h) for h in {0, 1, 5, 20, 60, 120}}
        meta[a] = {"dates": dates, "close": cl, "now": []}
        # --- własne
        for sid, label, fn, thr, d in OWN:
            m = fn(cl)
            trig = crossings(m, thr, d)
            sig = {"id": sid, "label": label, "kind": "own", "asset": a}
            meta[a]["now"].append((sig, ("state", m, thr, d, trig)))
            for h in STATE_H:
                add_tests(tests, sig, h, evaluate(cl, trig, h, bases[h]))
        # --- zewnętrzne
        for sid, label, dname, fn, thr, d, excl in DRV:
            if a in excl or dname not in drv or ("BTC_ONLY" in excl and a != "BTC"):
                continue
            sd, sv, lag = drv[dname]
            m_src = fn(sv)
            m = asof(sd, m_src, dates, lag)
            trig = crossings(m, thr, d)
            sig = {"id": sid, "label": label, "kind": "driver", "asset": a}
            meta[a]["now"].append((sig, ("state", m, thr, d, trig)))
            for h in STATE_H:
                add_tests(tests, sig, h, evaluate(cl, trig, h, bases[h]))
        # --- zdarzenia
        for kind, lst in events.items():
            if kind == "halving":
                continue
            past = [t for t in lst if t <= dates[-1]]
            idx = sorted({i for t in past if (i := next_trading_index(dates, t)) is not None and i >= 1})
            sig = {"id": "ev_" + kind, "label": EVENT_LABELS.get(kind, kind), "kind": "event", "asset": a}
            meta[a]["now"].append((sig, ("event", kind, idx)))
            for h in EVENT_H:
                add_tests(tests, sig, h, evaluate(cl, idx, h, bases[h]))
        if a == "BTC" and events.get("halving"):
            hv = sorted(events["halving"])
            for lo, hi in ((0, 180), (180, 365), (365, 540), (540, 730), (730, 1100)):
                idx = []
                for t in hv:
                    i = next_trading_index(dates, t + lo * DAY)
                    if i is not None and t + hi * DAY <= dates[-1] + DAY:
                        idx.append(i)
                sig = {"id": f"halving_{lo}", "label": f"BTC: dni {lo}-{hi} po halvingu", "kind": "cycle", "asset": a}
                for h in HALVING_H:
                    add_tests(tests, sig, h, evaluate(cl, idx, h, bases[h]))
    qs = bh_fdr([t["p"] for t in tests])
    for t, q in zip(tests, qs):
        t["q"] = q
    result = {}
    for a, (dates, cl) in assets.items():
        mine = sorted([t for t in tests if t["asset"] == a and t["q"] < 0.20 and t["n"] >= 15], key=lambda t: t["p"])
        best, best_vol = {}, {}
        for t in mine:
            (best_vol if t.get("metric") == "vol" else best).setdefault(t["id"], t)
        now = []
        for sig, info in meta[a]["now"]:
            b = best.get(sig["id"]) or best_vol.get(sig["id"])
            if not b:
                continue
            item = None
            if info[0] == "state":
                _, m, thr, d, trig = info
                cur = m[-1]
                if cur is None:
                    continue
                recent = [i for i in trig if len(cl) - 1 - i <= 3]
                hist = [x for x in m[-250:] if x is not None]
                sd = statistics.pstdev(hist) if len(hist) > 30 else None
                dist = (thr - cur) if d == "above" else (cur - thr)
                if recent:
                    item = {"status": "active", "detail": f"próg przekroczony {len(cl) - 1 - recent[-1]} sesji temu"}
                elif sd and 0 < dist <= 0.5 * sd:
                    item = {"status": "approaching", "detail": f"do progu brakuje {dist:.3g} (0,5σ = {0.5 * sd:.3g}); bieżąca wartość {cur:.3g}, próg {thr}", "proximity": 1 - dist / (0.5 * sd)}
            else:
                _, kind, _ = info
                nxt = [t for t in events.get(kind, []) if t >= now_ts // DAY * DAY]
                if nxt and (nxt[0] - now_ts // DAY * DAY) <= 10 * DAY:
                    days = (nxt[0] - now_ts // DAY * DAY) // DAY
                    item = {"status": "event_soon", "detail": ("dziś" if days == 0 else f"za {days} dni") + f" ({datetime.fromtimestamp(nxt[0], timezone.utc):%Y-%m-%d})", "days_to": days}
            if item:
                now.append({**{k: sig[k] for k in ("id", "label", "kind")}, **item,
                            "stat": {k: b[k] for k in ("h", "n", "mean_pct", "hit_pct", "base_hit_pct", "edge_pct", "t", "q", "stable", "direction")}, **({"abs_lift": (best_vol.get(sig["id"]) or b).get("abs_lift")} if (best_vol.get(sig["id"]) or b).get("abs_lift") else {})})
        lean = {"up": 0.0, "down": 0.0}
        for it in now:
            if it["stat"]["direction"] in lean:
                lean[it["stat"]["direction"]] += min(abs(it["stat"]["t"]), 4.0) * (1.0 if it["stat"]["stable"] else 0.6)
        result[a] = {"name": ASSETS[a][2], "last_date": datetime.fromtimestamp(dates[-1], timezone.utc).strftime("%Y-%m-%d"), "price": cl[-1], "days": len(dates),
                     "situations": [{k: v for k, v in t.items() if k not in ("asset",)} for t in mine[:40]], "now": sorted(now, key=lambda x: x["stat"]["q"]), "lean": lean}
    upcoming = []
    base_day = now_ts // DAY * DAY
    for kind, lst in events.items():
        for t in lst:
            if base_day <= t <= base_day + 45 * DAY and kind != "halving":
                upcoming.append({"kind": kind, "label": EVENT_LABELS.get(kind, kind), "date": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), "days": (t - base_day) // DAY})
    upcoming.sort(key=lambda x: x["days"])
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tests": len(tests), "significant": sum(1 for t in tests if t["q"] < 0.10),
            "assets": result, "upcoming_events": upcoming, "sources": {"events": {k: len(v) for k, v in events.items()}, "drivers": sorted(drv)},
            "notes": ["Każda sytuacja jest testowana osobno i korygowana na wielokrotne testowanie (FDR). Wynik q < 0,10 jest mocny, q < 0,20 wstępny.",
                      "Kolejne wystąpienia są rozdzielone o co najmniej horyzont, żeby się nie nakładały. Zdarzenia: horyzont 0 = reakcja w dniu publikacji.",
                      "Kalendarz FOMC 2024-2026 jest wpisany ręcznie; CPI, NFP, PCE, PKB i inne daty wymagają FRED_API_KEY. Bez klucza ich testów nie będzie.",
                      "Sytuacje 'zbliżające się' to prosta odległość od progu, a nie prognoza, że próg zostanie przekroczony.",
                      "To opis historii kilku cykli rynkowych; zależności zmieniają się w czasie i nie są poradą inwestycyjną."]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--out", default=os.path.join(HERE, "btc_situations.json"))
    a = ap.parse_args()
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rep = analyze(con)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, separators=(",", ":"), default=float)
    os.replace(tmp, a.out)
    print(f"testów: {rep['tests']}, istotnych (q<0,10): {rep['significant']}")
    for k, v in rep["assets"].items():
        print(f"{k:7} {len(v['situations']):2} sytuacji | teraz: " + "; ".join(f"{n['label']} [{n['status']}]" for n in v["now"][:3]))


if __name__ == "__main__":
    main()
