#!/usr/bin/env python3
"""Program badawczy: co w zgromadzonych danych (btc_data.db) faktycznie coś mówi o przyszłych zwrotach BTC.

Użycie:
    python btc_research.py                       # raport tekstowy + btc_research.json obok skryptu
    python btc_research.py --out /var/www/btc/btc_research.json --md raport.md

Metody (same standardowe biblioteki Pythona):
  1. Zestaw cech dziennych: poziomy (percentyl z ostatnich 365 dni), zmiany 7/30 dni, cechy techniczne i płynnościowe.
     Brak wycieku z przyszłości: publikacje z opóźnieniem (FRED, Glassnode) są przesuwane; percentyle liczone tylko z przeszłości.
  2. Siła predykcyjna: korelacja rang Spearmana (IC) cechy z przyszłym zwrotem 1/3/7/14/30 dni.
     Zwroty nakładają się, a cechy są trwałe, więc p-value liczone dla efektywnej liczby próbek (korekta Bartletta o autokorelację).
     Korekta na wielokrotne testy: Benjamini-Hochberg (q). Stabilność: znak IC w 3 podokresach.
  3. Kwintyle: średni/mediana zwrot i odsetek dodatnich po wartościach cechy z dolnego...górnego kwintyla.
  4. Reżimy (strach/chciwość, trend, zmienność implikowana) i sezonowość (dni tygodnia, miesiące).
  5. Test 'walk-forward': kompozyt cech wybranych WYŁĄCZNIE na danych treningowych, ocena na kolejnych 90 dniach
     poza próbą, z kosztami i testem permutacyjnym (czy wynik różni się od losowego przesunięcia sygnału).
  6. 'Dziś': bieżące wartości najistotniejszych cech i historyczne zwroty z podobnych momentów.
To narzędzie badawcze: wynik jest opisem historii, a nie prognozą ani poradą inwestycyjną.
"""
import argparse
import bisect
import json
import math
import os
import random
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 86400
HORIZONS = (1, 3, 7, 14, 30)
WINDOW = 365
COST = 0.001            # koszt jednej zmiany pozycji (prowizja + poślizg), 0,1%
TEST_DAYS = 90
MIN_TRAIN = 730
# opóźnienie publikacji (w dniach): cecha z dnia t jest znana dopiero po t+lag
LAGS = {"fred_M2SL": 45, "fred_CPIAUCSL": 45, "fred_UNRATE": 35, "fred_PAYEMS": 35, "fred_ICSA": 5, "fred_WALCL": 2, "fred_RRPONTSYD": 1, "fred_": 1, "gn_": 1, "wiki_": 1, "cot_": 4, "ust_": 1, "sofr": 1, "gdelt_": 1}
MAX_FFILL = {"fred_M2SL": 70, "fred_CPIAUCSL": 70, "fred_UNRATE": 70, "fred_PAYEMS": 70, "fred_WALCL": 10, "fred_": 6, "stooq_": 6, "cot_": 10, "fx_": 5}
SUM_METRICS = ("whale_", "trades_count", "quote_volume_usd")
SKIP_FEATURES = ("difficulty",)


# ----------------------------------------------------------------------------
# Statystyka
# ----------------------------------------------------------------------------
def ranks(xs):
    order = sorted(range(len(xs)), key=xs.__getitem__)
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def spearman(a, b):
    return pearson(ranks(a), ranks(b))


def p_from_r(r, n_eff):
    """Dwustronne p-value korelacji (przybliżenie normalne dla t)."""
    if r is None or n_eff < 8:
        return 1.0
    r = max(-0.9999, min(0.9999, r))
    t = r * math.sqrt((n_eff - 2) / (1 - r * r))
    return math.erfc(abs(t) / math.sqrt(2))


def bh_fdr(ps):
    """Benjamini-Hochberg: zwraca wartości q w kolejności wejściowej."""
    m = len(ps)
    order = sorted(range(m), key=ps.__getitem__)
    q, prev = [1.0] * m, 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, ps[i] * m / rank)
        q[i] = prev
    return q


def sharpe(rets):
    if len(rets) < 20:
        return None
    sd = statistics.pstdev(rets)
    return statistics.fmean(rets) / sd * math.sqrt(365) if sd > 0 else None


def max_dd(rets):
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return dd


# ----------------------------------------------------------------------------
# Dane
# ----------------------------------------------------------------------------
def prefix_lookup(table, metric, default):
    best = None
    for k in table:
        if metric == k or (k.endswith("_") and metric.startswith(k)):
            if best is None or len(k) > len(best):
                best = k
    return table[best] if best else default


def load(con):
    rows = con.execute("SELECT ts, c FROM candles WHERE symbol='BTCUSDT' AND interval='1d' ORDER BY ts").fetchall()
    if not rows:
        raise SystemExit("Brak świec BTC w bazie: uruchom najpierw 'btc_collect.py backfill'.")
    days = [r[0] // DAY * DAY for r in rows]
    close = [r[1] for r in rows]
    raw = {}
    for m, ts, v in con.execute("SELECT metric, ts, value FROM obs ORDER BY metric, ts"):
        raw.setdefault(m, []).append((ts, v))
    return days, close, raw


def daily_series(m, pts, days):
    """Agreguje obserwacje do dni (średnia lub suma), przesuwa o opóźnienie publikacji i wypełnia luki."""
    agg = {}
    for ts, v in pts:
        agg.setdefault(ts // DAY * DAY, []).append(v)
    f = sum if any(m.startswith(p) if p.endswith("_") else m == p for p in SUM_METRICS) else (lambda x: sum(x) / len(x))
    daily = {d: f(v) for d, v in agg.items()}
    lag = prefix_lookup(LAGS, m, 0) * DAY
    daily = {d + lag: v for d, v in daily.items()}
    ff = prefix_lookup(MAX_FFILL, m, 3) * DAY
    out, last_d, last_v = [], None, None
    for d in days:
        if d in daily:
            last_d, last_v = d, daily[d]
            out.append(last_v)
        elif last_d is not None and d - last_d <= ff:
            out.append(last_v)
        else:
            out.append(None)
    return out


def trailing_pctl(xs, window=WINDOW, min_obs=60):
    """Percentyl bieżącej wartości w oknie ostatnich 'window' obserwacji (tylko przeszłość i dziś)."""
    out, srt, hist = [], [], []
    for x in xs:
        if x is None:
            out.append(None)
            continue
        bisect.insort(srt, x)
        hist.append(x)
        if len(hist) > window:
            old = hist[len(hist) - window - 1]
            del srt[bisect.bisect_left(srt, old)]
        if len(srt) < min_obs:
            out.append(None)
        else:
            lo, hi = bisect.bisect_left(srt, x), bisect.bisect_right(srt, x)
            out.append((lo + hi) / 2 / len(srt))
    return out


def change(xs, k):
    out = []
    pos = all(x is None or x > 0 for x in xs)
    for i, x in enumerate(xs):
        y = xs[i - k] if i >= k else None
        if x is None or y is None:
            out.append(None)
        else:
            out.append(x / y - 1 if pos and y != 0 else x - y)
    return out


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def rsi(xs, n=14):
    out, ag, al = [None], 0.0, 0.0
    for i in range(1, len(xs)):
        d = xs[i] - xs[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            ag, al = ag + g / n, al + l / n
            out.append(None if i < n else 100 - 100 / (1 + ag / al) if al else 100.0)
        else:
            ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
            out.append(100 - 100 / (1 + ag / al) if al else 100.0)
    return out


def realized_vol(rets, n=30):
    out = []
    for i in range(len(rets)):
        w = rets[i - n + 1:i + 1] if i >= n - 1 else None
        out.append(statistics.pstdev(w) * math.sqrt(365) * 100 if w and None not in w else None)
    return out


def build_features(days, close, raw):
    """Zwraca (levels, features): levels = surowe szeregi dzienne, features = percentyle z ostatnich 365 dni."""
    n = len(days)
    ret1 = [None] + [close[i] / close[i - 1] - 1 for i in range(1, n)]
    lv = {}
    for m, pts in raw.items():
        if m in SKIP_FEATURES:
            continue
        s = daily_series(m, pts, days)
        if sum(x is not None for x in s) >= 120:
            lv[m] = s
    s50, s200 = sma(close, 50), sma(close, 200)
    lv["tech_ret_7d"] = change(close, 7)
    lv["tech_ret_30d"] = change(close, 30)
    lv["tech_rsi14"] = rsi(close)
    lv["tech_dist_sma50"] = [c / s - 1 if s else None for c, s in zip(close, s50)]
    lv["tech_dist_sma200"] = [c / s - 1 if s else None for c, s in zip(close, s200)]
    lv["tech_vol30"] = realized_vol(ret1)
    peak, dd = 0, []
    for c in close:
        peak = max(peak, c)
        dd.append(c / peak - 1)
    lv["tech_drawdown"] = dd
    if "dvol" in lv:
        lv["dvol_minus_rv30"] = [a - b if a is not None and b is not None else None for a, b in zip(lv["dvol"], lv["tech_vol30"])]
    feats = {}
    for m, s in lv.items():
        feats[m] = trailing_pctl(s)
        if not m.startswith("tech_ret"):
            for k in (7, 30):
                feats[f"{m}__chg{k}"] = trailing_pctl(change(s, k))
    return lv, feats, ret1


def forward_returns(close, h):
    n = len(close)
    return [close[i + h] / close[i] - 1 if i + h < n else None for i in range(n)]


# ----------------------------------------------------------------------------
# Analizy
# ----------------------------------------------------------------------------
def autocorr_list(xs, max_lag):
    """Autokorelacja rang dla lagów 1..max_lag (na kolejnych dostępnych wartościach)."""
    rk = ranks(xs)
    n = len(rk)
    m = sum(rk) / n
    den = sum((v - m) ** 2 for v in rk)
    if den <= 0:
        return [0.0] * max_lag
    return [sum((rk[i] - m) * (rk[i + k] - m) for i in range(n - k)) / den for k in range(1, max_lag + 1)]


def effective_n(n, acx, acy):
    """Efektywna liczba niezależnych próbek korelacji dwóch autokorelowanych szeregów (korekta Bartletta):
    N / (1 + 2 * sum rho_x(k) * rho_y(k)). Obejmuje nakładanie się zwrotów i trwałość cechy."""
    k = min(len(acx), len(acy))
    factor = 1 + 2 * sum(acx[i] * acy[i] for i in range(k))
    return n / max(1.0, factor)


def information_coefficients(feats, fwd, days):
    res = []
    L = 90
    acy_cache = {}
    for h, y in fwd.items():
        acy_cache[h] = autocorr_list([v for v in y if v is not None], L)
    n_days = len(days)
    cuts = [days[0] + (days[-1] - days[0]) * k // 3 for k in range(1, 3)]
    for name, x in feats.items():
        acx = autocorr_list([v for v in x if v is not None], L) if sum(v is not None for v in x) >= 150 else None
        if acx is None:
            continue
        for h, y in fwd.items():
            idx = [i for i in range(n_days) if x[i] is not None and y[i] is not None]
            if len(idx) < 150:
                continue
            xs, ys = [x[i] for i in idx], [y[i] for i in idx]
            r = spearman(xs, ys)
            if r is None:
                continue
            n_eff = effective_n(len(idx), acx, acy_cache[h])
            parts = []
            for lo, hi in ((days[0], cuts[0]), (cuts[0], cuts[1]), (cuts[1], days[-1] + 1)):
                sel = [j for j, i in enumerate(idx) if lo <= days[i] < hi]
                parts.append(spearman([xs[j] for j in sel], [ys[j] for j in sel]) if len(sel) > 60 else None)
            res.append({"feature": name, "h": h, "ic": r, "n": len(idx), "n_eff": round(n_eff), "p": p_from_r(r, n_eff), "periods": parts})
    qs = bh_fdr([r["p"] for r in res])
    for r, q in zip(res, qs):
        r["q"] = q
        signs = [p for p in r["periods"] if p is not None]
        r["stable"] = len(signs) == 3 and all((p > 0) == (r["ic"] > 0) for p in signs)
    return sorted(res, key=lambda r: r["p"])


def quintiles(x, y):
    idx = [i for i in range(len(x)) if x[i] is not None and y[i] is not None]
    idx.sort(key=lambda i: x[i])
    out = []
    for q in range(5):
        part = [y[i] for i in idx[q * len(idx) // 5:(q + 1) * len(idx) // 5]]
        if not part:
            continue
        out.append({"q": q + 1, "n": len(part), "mean": statistics.fmean(part) * 100, "median": statistics.median(part) * 100,
                    "hit": sum(1 for v in part if v > 0) / len(part) * 100})
    return out


def regime_table(days, close, lv, fwd):
    tbl = {}

    def bucket(name, labels, series, edges):
        rows = []
        for li, lab in enumerate(labels):
            lo, hi = edges[li], edges[li + 1]
            sel = [i for i, v in enumerate(series) if v is not None and lo <= v < hi]
            row = {"label": lab, "n": len(sel)}
            for h in (7, 30):
                vals = [fwd[h][i] for i in sel if fwd[h][i] is not None]
                row[f"mean_{h}d"] = statistics.fmean(vals) * 100 if len(vals) > 20 else None
                row[f"hit_{h}d"] = sum(1 for v in vals if v > 0) / len(vals) * 100 if len(vals) > 20 else None
            rows.append(row)
        tbl[name] = rows
    if "fng" in lv:
        bucket("Fear & Greed", ["skrajny strach (<25)", "strach (25-45)", "neutralnie (45-55)", "chciwość (55-75)", "skrajna chciwość (>75)"], lv["fng"], [0, 25, 45, 55, 75, 101])
    s200 = lv["tech_dist_sma200"]
    bucket("Trend (cena vs SMA200)", ["poniżej SMA200 o >20%", "poniżej SMA200", "powyżej SMA200", "powyżej SMA200 o >20%"], s200, [-9, -0.2, 0, 0.2, 9])
    if "dvol" in lv:
        pc = trailing_pctl(lv["dvol"])
        bucket("DVOL (percentyl 365 dni)", ["niski (<20%)", "średni", "wysoki (>80%)"], pc, [0, 0.2, 0.8, 1.01])
    bucket("Zmienność zrealizowana 30d", ["<35%", "35-60%", ">60%"], lv["tech_vol30"], [0, 35, 60, 999])
    return tbl


def seasonality(days, close):
    dow, mon = {i: [] for i in range(7)}, {i: [] for i in range(1, 13)}
    for i in range(1, len(close)):
        d = datetime.fromtimestamp(days[i], timezone.utc)
        r = close[i] / close[i - 1] - 1
        dow[d.weekday()].append(r)
        mon[d.month].append(r)
    names = ["pon", "wt", "śr", "czw", "pt", "sob", "nie"]
    f = lambda v: {"mean": statistics.fmean(v) * 100, "hit": sum(1 for x in v if x > 0) / len(v) * 100, "n": len(v)} if len(v) > 20 else None
    return {"dow": [dict(label=names[k], **(f(v) or {})) for k, v in dow.items()], "month": [dict(label=str(k), **(f(v) or {})) for k, v in mon.items()]}


def walk_forward(days, close, feats, h=7, rng=None):
    n = len(days)
    ret1 = [None] + [close[i] / close[i - 1] - 1 for i in range(1, n)]
    fwd = forward_returns(close, h)
    names = list(feats)
    oos_pos, oos_ret, windows = [], [], []
    start = MIN_TRAIN
    while start + 10 < n:
        end = min(start + TEST_DAYS, n)
        chosen = []
        for nm in names:
            idx = [i for i in range(start - h) if feats[nm][i] is not None and fwd[i] is not None]
            if len(idx) < 250:
                continue
            r = spearman([feats[nm][i] for i in idx], [fwd[i] for i in idx])
            if r is None:
                continue
            t = r * math.sqrt(max(len(idx) / h - 2, 1) / max(1 - r * r, 1e-9))
            if abs(t) >= 2.5:
                chosen.append((nm, 1 if r > 0 else -1, abs(t)))
        chosen.sort(key=lambda c: -c[2])
        chosen = chosen[:12]
        for i in range(start, end):
            vals = [s * (feats[nm][i] - 0.5) for nm, s, _ in chosen if feats[nm][i] is not None]
            comp = sum(vals) / len(vals) if vals else 0.0
            oos_pos.append(1 if comp > 0 else 0)
            oos_ret.append(ret1[i + 1] if i + 1 < n else None)
        windows.append({"from": datetime.fromtimestamp(days[start], timezone.utc).strftime("%Y-%m-%d"), "features": [c[0] for c in chosen[:5]], "n_features": len(chosen)})
        start = end
    pairs = [(p, r) for p, r in zip(oos_pos, oos_ret) if r is not None]
    if len(pairs) < 100:
        return None
    pos, rets = [p for p, _ in pairs], [r for _, r in pairs]

    def evaluate(pos_):
        out, prev = [], 0
        for p, r in zip(pos_, rets):
            out.append(p * r - (COST if p != prev else 0))
            prev = p
        return out
    strat, hold = evaluate(pos), rets
    s_sh = sharpe(strat)
    rng = rng or random.Random(7)
    better, trials = 0, 300
    for _ in range(trials):
        shift = rng.randrange(30, len(pos) - 30)
        if (sharpe(evaluate(pos[shift:] + pos[:shift])) or -9) >= (s_sh or -9):
            better += 1
    tot = lambda r: math.prod(1 + x for x in r) - 1
    return {"horizon_days": h, "oos_days": len(pairs), "oos_from": windows[0]["from"],
            "strategy": {"total_return_pct": tot(strat) * 100, "sharpe": s_sh, "max_dd_pct": max_dd(strat) * 100,
                         "time_in_market_pct": sum(pos) / len(pos) * 100, "switches": sum(1 for a, b in zip(pos, pos[1:]) if a != b)},
            "buy_hold": {"total_return_pct": tot(hold) * 100, "sharpe": sharpe(hold), "max_dd_pct": max_dd(hold) * 100},
            "permutation_p": (better + 1) / (trials + 1), "cost_per_switch_pct": COST * 100, "windows": windows[-8:]}


def today_view(feats, lv, fwd, days, top):
    out = []
    i = len(days) - 1
    for r in top:
        x = feats[r["feature"]]
        idx = max((j for j in range(i + 1) if x[j] is not None), default=None)
        if idx is None or days[i] - days[idx] > 5 * DAY:
            continue
        p = x[idx]
        y = fwd[r["h"]]
        sel = [y[j] for j in range(i) if x[j] is not None and y[j] is not None and abs(x[j] - p) <= 0.15]
        base = [v for v in y if v is not None]
        base_name = r["feature"].split("__")[0]
        out.append({"feature": r["feature"], "h": r["h"], "percentile": p * 100, "level": lv.get(base_name, [None])[idx] if "__" not in r["feature"] else None,
                    "ic": r["ic"], "similar_n": len(sel), "similar_mean_pct": statistics.fmean(sel) * 100 if len(sel) > 20 else None,
                    "base_mean_pct": statistics.fmean(base) * 100 if base else None})
    return out


def inventory(raw, days):
    inv = []
    for m, pts in sorted(raw.items()):
        inv.append({"metric": m, "rows": len(pts), "from": datetime.fromtimestamp(pts[0][0], timezone.utc).strftime("%Y-%m-%d"),
                    "to": datetime.fromtimestamp(pts[-1][0], timezone.utc).strftime("%Y-%m-%d")})
    return inv


def analyze(con):
    days, close, raw = load(con)
    lv, feats, ret1 = build_features(days, close, raw)
    fwd = {h: forward_returns(close, h) for h in HORIZONS}
    ics = information_coefficients({k: v for k, v in feats.items() if not k.startswith("tech_ret") or "__" in k}, fwd, days)
    sig = [r for r in ics if r["q"] < 0.10]
    top = (sig or ics)[:25]
    best = {}
    for r in ics:                      # najlepszy horyzont dla każdej cechy
        if r["feature"] not in best:
            best[r["feature"]] = r
    top_features = list(best.values())[:10]
    qt = {}
    for r in top_features:
        qt[f"{r['feature']}|{r['h']}d"] = quintiles(feats[r["feature"]], fwd[r["h"]])
    notes = [
        f"Przetestowano {len(ics)} par cecha×horyzont; przy takiej liczbie testów część wyników wyjdzie 'istotna' przypadkiem — dlatego podajemy q (korekta Benjamini-Hochberg).",
        "Zwroty nakładają się, a cechy są trwałe w czasie, więc p-value liczono dla efektywnej liczby próbek (korekta Bartletta o autokorelację obu szeregów), nie dla surowego N.",
        "Wynik 'stabilny' = ten sam znak korelacji we wszystkich 3 podokresach historii.",
        "Kwintyle liczone na całej próbie służą opisowi; nie są strategią.",
        "To historia jednego aktywa i krótkiej próby (kilka cykli rynkowych). Nie jest to prognoza ani porada inwestycyjna.",
    ]
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": {"days": len(days), "from": datetime.fromtimestamp(days[0], timezone.utc).strftime("%Y-%m-%d"),
                     "to": datetime.fromtimestamp(days[-1], timezone.utc).strftime("%Y-%m-%d"), "metrics": len(raw), "features": len(feats),
                     "inventory": inventory(raw, days)},
            "tests": len(ics), "significant": len(sig), "ic": top, "quintiles": qt,
            "regimes": regime_table(days, close, lv, fwd), "seasonality": seasonality(days, close),
            "walkforward": walk_forward(days, close, feats), "today": today_view(feats, lv, fwd, days, top_features), "notes": notes}


def to_text(r):
    L = [f"BADANIA BTC  ({r['data']['from']} → {r['data']['to']}, {r['data']['days']} dni, {r['data']['metrics']} metryk, {r['data']['features']} cech)",
         f"Testów: {r['tests']}, istotnych po korekcie (q<0.10): {r['significant']}", "", "NAJSILNIEJSZE ZALEŻNOŚCI (Spearman IC z przyszłym zwrotem)"]
    for x in r["ic"][:15]:
        L.append(f"  {x['feature']:46} {x['h']:>2}d  IC {x['ic']:+.3f}  p={x['p']:.4f}  q={x['q']:.3f}  {'stabilna' if x['stable'] else 'niestabilna'}")
    wf = r.get("walkforward")
    if wf:
        L += ["", f"WALK-FORWARD (poza próbą od {wf['oos_from']}, {wf['oos_days']} dni, koszt {wf['cost_per_switch_pct']:.2f}% za zmianę)",
              f"  strategia: {wf['strategy']['total_return_pct']:+.1f}%  Sharpe {wf['strategy']['sharpe'] or 0:.2f}  maxDD {wf['strategy']['max_dd_pct']:.1f}%  w rynku {wf['strategy']['time_in_market_pct']:.0f}%",
              f"  kup i trzymaj: {wf['buy_hold']['total_return_pct']:+.1f}%  Sharpe {wf['buy_hold']['sharpe'] or 0:.2f}  maxDD {wf['buy_hold']['max_dd_pct']:.1f}%",
              f"  test permutacyjny p={wf['permutation_p']:.3f} (małe p = sygnał lepszy niż losowe przesunięcie)"]
    L += [""] + ["! " + n for n in r["notes"]]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--out", default=os.path.join(HERE, "btc_research.json"))
    ap.add_argument("--md", help="zapisz raport tekstowy do pliku")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rep = analyze(con)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, separators=(",", ":"), default=float)
    os.replace(tmp, a.out)
    txt = to_text(rep)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")
    if not a.quiet:
        print(txt)


if __name__ == "__main__":
    main()
