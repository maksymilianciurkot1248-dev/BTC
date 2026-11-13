#!/usr/bin/env python3
"""Strategie portfelowe o różnym poziomie ryzyka (ochronna, bezpieczna, zbalansowana, wzrostowa, agresywna) ze statystykami.

Algorytm (miesięczne równoważenie, tylko dane znane w dniu decyzji, bez dźwigni):
  1. Uniwersum z bazy: krypto, złoto/surowce, obligacje (TLT), indeksy (SPX, NDX), spółki z watchlisty. Aktywo wchodzi dopiero po 252 sesjach historii.
  2. Kwalifikacja: trend (cena > SMA200) i momentum 12-1 > 0 (filtry zależne od profilu).
  3. Wybór: ranking = momentum skorygowane o zmienność (12-1 i 6 mies.) zmieszane z rankingiem niskiej zmienności (udział zależy od profilu); top N.
  4. Wagi: odwrotnie do zmienności (90 dni), limity na aktywo i na klasę (krypto, akcje, surowce, obligacje), nadwyżka rozdzielana na pozostałe aktywa,
     skala do docelowej zmienności portfela (macierz kowariancji z 120 sesji); reszta w gotówce (stopa Fed, gdy jest w bazie).
  5. Koszty transakcyjne od obrotu, wagi dryfują między równoważeniami.
Statystyki: CAGR, zmienność, Sharpe (z przedziałem bootstrapowym), Sortino, maks. obsunięcie i jego czas, Calmar, VaR/CVaR miesięczny, miesiące dodatnie,
wyniki roczne, okresy stresu, porównanie z benchmarkami (BTC, S&P 500, 60/40, złoto, równe wagi) oraz symulacja przyszłości (bootstrap blokowy).
Ważne: lista aktywów to Twoje dzisiejsze wybory (błąd przetrwania zawyża wyniki), a profile są ustalone z góry (bez dopasowywania parametrów do historii).
To opis historii i narzędzie analityczne, nie porada inwestycyjna.
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
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_markets as bm  # noqa: E402

DAY = 86400
TD = 252
CLASS_COST = {"crypto": 0.0015, "stock": 0.0010, "commodity": 0.0008, "bond": 0.0005, "index": 0.0005}
CLASS_PL = {"crypto": "Krypto", "stock": "Akcje", "commodity": "Surowce", "bond": "Obligacje", "index": "Indeksy"}
PROFILES = {
    "protect": {"name": "Ochronny", "icon": "shield", "risk": 1, "target_vol": 0.04, "max_assets": 5, "max_w": 0.30, "min_cash": 0.30, "trend": True, "abs_mom": True, "vol_pref": 0.80,
                "caps": {"crypto": 0.00, "stock": 0.15, "commodity": 0.40, "bond": 0.60, "index": 0.20},
                "desc": "Ochrona kapitału: dużo gotówki, obligacje i złoto, bez krypto, tylko aktywa w trendzie wzrostowym."},
    "safe": {"name": "Bezpieczny", "icon": "shield", "risk": 2, "target_vol": 0.07, "max_assets": 6, "max_w": 0.30, "min_cash": 0.15, "trend": True, "abs_mom": True, "vol_pref": 0.60,
             "caps": {"crypto": 0.03, "stock": 0.30, "commodity": 0.45, "bond": 0.50, "index": 0.30},
             "desc": "Niska zmienność: spokojne aktywa, krypto co najwyżej symbolicznie, filtr trendu."},
    "balanced": {"name": "Zbalansowany", "icon": "layers", "risk": 3, "target_vol": 0.12, "max_assets": 8, "max_w": 0.25, "min_cash": 0.05, "trend": True, "abs_mom": True, "vol_pref": 0.40,
                 "caps": {"crypto": 0.10, "stock": 0.50, "commodity": 0.40, "bond": 0.40, "index": 0.40},
                 "desc": "Równowaga wzrostu i bezpieczeństwa: mieszanka klas aktywów, umiarkowane krypto."},
    "growth": {"name": "Wzrostowy", "icon": "up", "risk": 4, "target_vol": 0.18, "max_assets": 10, "max_w": 0.20, "min_cash": 0.0, "trend": True, "abs_mom": True, "vol_pref": 0.20,
               "caps": {"crypto": 0.25, "stock": 0.70, "commodity": 0.30, "bond": 0.25, "index": 0.50},
               "desc": "Nastawienie na wzrost: więcej akcji i krypto, silniejsze momentum, większe wahania."},
    "aggressive": {"name": "Agresywny", "icon": "flame", "risk": 5, "target_vol": 0.30, "max_assets": 6, "max_w": 0.35, "min_cash": 0.0, "trend": True, "abs_mom": False, "vol_pref": 0.0,
                   "caps": {"crypto": 0.60, "stock": 0.80, "commodity": 0.25, "bond": 0.10, "index": 0.40},
                   "desc": "Skoncentrowany portfel liderów momentum, duże krypto i akcje, bez gwarancji ochrony kapitału."},
}


# ----------------------------------------------------------------------------
# Dane
# ----------------------------------------------------------------------------
def universe(con):
    def n(sym):
        return con.execute("SELECT COUNT(*) FROM candles WHERE symbol=? AND interval='1d'", (sym,)).fetchone()[0]
    u = [{"key": s + "USDT", "name": nm, "cls": "crypto"} for s, nm in bm.CRYPTO]
    gold = n("XAUUSD") >= 500
    u += [{"key": "XAUUSD" if gold else "PAXGUSDT", "name": "Złoto" + ("" if gold else " (token PAXG)"), "cls": "commodity"}]
    u += [{"key": k, "name": lab.split(" (")[0], "cls": "commodity"} for k, (_, lab) in bm.COMMODITIES.items() if k in ("XAGUSD", "XPTUSD", "COPPER", "WTI")]
    u += [{"key": "TLT", "name": "Obligacje USA 20+ (TLT)", "cls": "bond"}, {"key": "SPX", "name": "S&P 500", "cls": "index"}, {"key": "NDX", "name": "Nasdaq 100", "cls": "index"}]
    u += [{"key": bm.db_symbol(s, m), "name": nm, "cls": "stock"} for s, m, nm, _ in bm.load_watchlist()]
    return [a for a in u if n(a["key"]) >= 300]


def load_prices(con, uni):
    """Siatka dni roboczych i ceny wyrównane (ostatnia znana cena do 5 dni); None gdy brak notowania."""
    raw = {}
    for a in uni:
        rows = con.execute("SELECT ts, c FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts", (a["key"],)).fetchall()
        raw[a["key"]] = [(t // DAY * DAY, c) for t, c in rows if c and c > 0]
    start = min(r[0][0] for r in raw.values())
    end = max(r[-1][0] for r in raw.values())
    days = [d for d in range(start, end + 1, DAY) if datetime.fromtimestamp(d, timezone.utc).weekday() < 5]
    px = {}
    for k, rows in raw.items():
        ts = [r[0] for r in rows]
        col = []
        for d in days:
            j = bisect.bisect_right(ts, d) - 1
            col.append(rows[j][1] if j >= 0 and d - ts[j] <= 5 * DAY and d <= ts[-1] + 5 * DAY else None)
        px[k] = col
    cash = con.execute("SELECT ts, value FROM obs WHERE metric='fred_DFF' ORDER BY ts").fetchall()
    cts = [r[0] // DAY * DAY for r in cash]
    rf = []
    for d in days:
        j = bisect.bisect_right(cts, d) - 1
        rf.append(cash[j][1] / 100 / TD if j >= 0 and d - cts[j] < 40 * DAY else 0.0)
    return days, px, rf


# ----------------------------------------------------------------------------
# Wybór i wagi
# ----------------------------------------------------------------------------
def pct_rank(vals):
    pairs = sorted((v, i) for i, v in enumerate(vals))
    out = [0.0] * len(vals)
    for r, (_, i) in enumerate(pairs):
        out[i] = r / max(1, len(vals) - 1)
    return out


def features(px, t, min_hist=TD):
    """Cechy aktywa na dzień t (indeks) wyłącznie z danych do t. None, gdy za krótka historia."""
    col = px
    if t < min_hist or col[t] is None or col[t - 21] is None or col[t - 252 + 0] is None:
        return None
    win = col[t - 199:t + 1]
    if any(v is None for v in win):
        return None
    sma200 = sum(win) / 200
    r90 = []
    for i in range(t - 89, t + 1):
        a, b = col[i - 1], col[i]
        if a is None or b is None:
            return None
        r90.append(b / a - 1)
    vol = statistics.pstdev(r90) * math.sqrt(TD)
    if vol <= 1e-6:
        return None
    m12 = col[t - 21] / col[t - 252] - 1
    m6 = col[t] / col[t - 126] - 1 if col[t - 126] else None
    if m6 is None:
        return None
    return {"vol": vol, "m12": m12, "m6": m6, "trend": col[t] > sma200, "dist200": col[t] / sma200 - 1}


def cov_matrix(px, keys, t, n=120):
    rets = {}
    for k in keys:
        col = px[k]
        r = []
        for i in range(t - n + 1, t + 1):
            a, b = col[i - 1], col[i]
            r.append(b / a - 1 if a and b else 0.0)
        rets[k] = r
    mean = {k: sum(v) / n for k, v in rets.items()}
    cov = {}
    for a in keys:
        for b in keys:
            cov[(a, b)] = sum((x - mean[a]) * (y - mean[b]) for x, y in zip(rets[a], rets[b])) / (n - 1) * TD
    return cov


def port_vol(w, cov):
    keys = list(w)
    return math.sqrt(max(0.0, sum(w[a] * w[b] * cov[(a, b)] for a in keys for b in keys)))


def cap_weights(w, cls_of, max_w, caps, inv):
    """Limity na aktywo i klasę; nadwyżka trafia do aktywów, które jeszcze mają miejsce (proporcjonalnie do 1/vol)."""
    w = dict(w)
    for _ in range(40):
        for k in w:
            w[k] = min(w[k], max_w)
        for c, cap in caps.items():
            keys = [k for k in w if cls_of[k] == c]
            s = sum(w[k] for k in keys)
            if s > cap + 1e-12:
                for k in keys:
                    w[k] *= cap / s if s > 0 else 0
        total = sum(w.values())
        free = [k for k in w if w[k] < max_w - 1e-9 and sum(w[j] for j in w if cls_of[j] == cls_of[k]) < caps.get(cls_of[k], 1) - 1e-9]
        missing = 1.0 - total
        if missing < 1e-6 or not free:
            break
        base = sum(inv[k] for k in free)
        for k in free:
            w[k] += missing * inv[k] / base
    return w


def target_weights(px, uni, t, prof):
    """Wagi docelowe profilu na dzień t. Zwraca (wagi, szczegóły_aktywów, vol_portfela)."""
    cls_of = {a["key"]: a["cls"] for a in uni}
    feats = {}
    for a in uni:
        f = features(px[a["key"]], t)
        if not f:
            continue
        if prof["trend"] and not f["trend"]:
            continue
        if prof["abs_mom"] and f["m12"] <= 0:
            continue
        if prof["caps"].get(a["cls"], 1) <= 0:
            continue
        feats[a["key"]] = f
    if not feats:
        return {}, {}, 0.0
    keys = list(feats)
    adj12 = pct_rank([feats[k]["m12"] / feats[k]["vol"] for k in keys])
    adj6 = pct_rank([feats[k]["m6"] / feats[k]["vol"] for k in keys])
    lv = pct_rank([-feats[k]["vol"] for k in keys])
    vp = prof["vol_pref"]
    score = {k: (1 - vp) * (0.5 * adj12[i] + 0.5 * adj6[i]) + vp * lv[i] for i, k in enumerate(keys)}
    top = sorted(keys, key=lambda k: -score[k])[:prof["max_assets"]]
    inv = {k: 1 / feats[k]["vol"] for k in top}
    s = sum(inv.values())
    w = {k: inv[k] / s for k in top}
    w = cap_weights(w, cls_of, prof["max_w"], prof["caps"], inv)
    cov = cov_matrix(px, top, t)
    sigma = port_vol(w, cov)
    scale = 1.0
    if sigma > 1e-9:
        scale = min(1.0, prof["target_vol"] / sigma)
    gross = sum(w.values()) * scale
    if gross > 1 - prof["min_cash"]:
        scale *= (1 - prof["min_cash"]) / gross
    w = {k: v * scale for k, v in w.items() if v * scale > 1e-4}
    info = {k: {**feats[k], "score": score[k], "cls": cls_of[k]} for k in w}
    return w, info, port_vol(w, cov) if w else 0.0


# ----------------------------------------------------------------------------
# Test historyczny
# ----------------------------------------------------------------------------
def month_ends(days):
    out = []
    for i in range(len(days) - 1):
        a, b = datetime.fromtimestamp(days[i], timezone.utc), datetime.fromtimestamp(days[i + 1], timezone.utc)
        if a.month != b.month:
            out.append(i)
    return out


def backtest(days, px, rf, uni, prof, start_idx=None, last=None):
    n = len(days)
    cls_of = {a["key"]: a["cls"] for a in uni}
    reb = set(month_ends(days))
    start = start_idx if start_idx is not None else TD + 1
    end = last if last is not None else n - 1
    w, eq, rets, hold_hist, trades = {}, 1.0, [], [], 0
    cash_w = 1.0
    out_dates, out_eq, out_ret, turn_sum, nreb = [], [], [], 0.0, 0
    for t in range(start, end + 1):
        # dzienny wynik z wag sprzed dnia
        pr = cash_w * rf[t]
        for k, wk in w.items():
            a, b = px[k][t - 1], px[k][t]
            pr += wk * (b / a - 1 if a and b else 0.0)
        eq *= 1 + pr
        if w:
            nw = {}
            for k, wk in w.items():
                a, b = px[k][t - 1], px[k][t]
                nw[k] = wk * (b / a if a and b else 1.0) / (1 + pr)
            w = nw
            cash_w = max(0.0, 1.0 - sum(w.values()))
        cost = 0.0
        if t in reb and t < n - 1 or (t == start):
            new, _, _ = target_weights(px, uni, t, prof)
            keys = set(new) | set(w)
            turn = 0.0
            for k in keys:
                d = abs(new.get(k, 0.0) - w.get(k, 0.0))
                cost += d * CLASS_COST[cls_of[k]]
                turn += d
            turn_sum, nreb = turn_sum + turn, nreb + 1
            w = dict(new)
            cash_w = max(0.0, 1.0 - sum(w.values()))
            eq *= 1 - cost
            pr = (1 + pr) * (1 - cost) - 1
        rets.append(pr)
        out_dates.append(days[t])
        out_eq.append(eq)
        hold_hist.append((len(w), cash_w))
    return {"dates": out_dates, "equity": out_eq, "rets": rets, "avg_hold": statistics.fmean(h[0] for h in hold_hist) if hold_hist else 0,
            "avg_cash": statistics.fmean(h[1] for h in hold_hist) if hold_hist else 1, "turnover_year": (turn_sum / nreb * 12) if nreb else 0}


def buy_hold(days, px, key, start_idx):
    col = px[key]
    if col[start_idx - 1] is None or col[start_idx] is None:
        return None                     # aktywo nie miało notowań na starcie testu: brak uczciwego benchmarku
    eq, out = 1.0, []
    for t in range(start_idx, len(days)):
        a, b = col[t - 1], col[t]
        eq *= (b / a) if a and b else 1.0
        out.append(eq)
    return out


def equal_weight(days, px, keys, start_idx):
    reb, eq, out, w = set(month_ends(days)), 1.0, [], {}
    for t in range(start_idx, len(days)):
        pr = sum(wk * (px[k][t] / px[k][t - 1] - 1 if px[k][t] and px[k][t - 1] else 0.0) for k, wk in w.items())
        eq *= 1 + pr
        if w:
            w = {k: wk * (px[k][t] / px[k][t - 1] if px[k][t] and px[k][t - 1] else 1.0) / (1 + pr) for k, wk in w.items()}
        if t in reb or not w:
            av = [k for k in keys if features(px[k], t)]
            w = {k: 1 / len(av) for k in av} if av else {}
        out.append(eq)
    return out


def static_mix(days, px, mix, start_idx):
    """Stałe wagi z miesięcznym równoważeniem (np. 60/40)."""
    reb, eq, out = set(month_ends(days)), 1.0, []
    w = dict(mix)
    for t in range(start_idx, len(days)):
        pr = sum(wk * (px[k][t] / px[k][t - 1] - 1 if px[k][t] and px[k][t - 1] else 0.0) for k, wk in w.items())
        eq *= 1 + pr
        w = {k: wk * (px[k][t] / px[k][t - 1] if px[k][t] and px[k][t - 1] else 1.0) / (1 + pr) for k, wk in w.items()}
        if t in reb:
            w = dict(mix)
        out.append(eq)
    return out


# ----------------------------------------------------------------------------
# Statystyki
# ----------------------------------------------------------------------------
def max_dd(eq):
    peak, dd, start, worst_len, cur_peak_i, w_start, w_end = eq[0], 0.0, 0, 0, 0, 0, 0
    for i, v in enumerate(eq):
        if v > peak:
            peak, cur_peak_i = v, i
        d = v / peak - 1
        if d < dd:
            dd, w_start, w_end = d, cur_peak_i, i
    # czas do odrobienia
    rec = None
    for j in range(w_end, len(eq)):
        if eq[j] >= eq[w_start]:
            rec = j
            break
    return dd, w_end - w_start, (rec - w_start) if rec is not None else None


def drawdown_series(eq):
    peak, out = eq[0], []
    for v in eq:
        peak = max(peak, v)
        out.append(v / peak - 1)
    return out


def stats(dates, eq, rets, rf_daily=0.0):
    n = len(rets)
    yrs = n / TD
    cagr = eq[-1] ** (1 / yrs) - 1 if yrs > 0 and eq[-1] > 0 else None
    sd = statistics.pstdev(rets) if n > 2 else 0
    vol = sd * math.sqrt(TD)
    mean_ex = statistics.fmean(r - rf_daily for r in rets)
    sharpe = mean_ex / sd * math.sqrt(TD) if sd > 0 else None
    dn = [min(0.0, r - rf_daily) for r in rets]
    dd_sd = math.sqrt(sum(x * x for x in dn) / n) if n else 0
    sortino = mean_ex / dd_sd * math.sqrt(TD) if dd_sd > 0 else None
    mdd, mdd_len, rec = max_dd(eq)
    # miesięczne
    mon, last_m, base = [], None, 1.0
    for d, v in zip(dates, eq):
        m = datetime.fromtimestamp(d, timezone.utc).strftime("%Y-%m")
        if last_m is not None and m != last_m:
            mon.append((last_m, v_prev / base - 1))
            base = v_prev
        last_m, v_prev = m, v
    mon.append((last_m, v_prev / base - 1))
    mr = [x for _, x in mon[1:]] or [0.0]
    srt = sorted(mr)
    var95 = srt[max(0, int(0.05 * len(srt)) - 0)] if srt else 0
    tail = [x for x in srt if x <= var95] or [var95]
    year = {}
    prev, last_y = 1.0, None
    for d, v in zip(dates, eq):
        y = datetime.fromtimestamp(d, timezone.utc).year
        if last_y is not None and y != last_y:
            year[last_y] = year_end / prev - 1
            prev = year_end
        last_y, year_end = y, v
    year[last_y] = year_end / prev - 1
    w12 = [eq[i] / eq[i - TD] - 1 for i in range(TD, n)] if n > TD else []
    return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "sortino": sortino, "max_dd": mdd, "max_dd_days": mdd_len, "recovery_days": rec, "calmar": (cagr / abs(mdd)) if cagr is not None and mdd < 0 else None,
            "total_return": eq[-1] - 1, "months": len(mr), "pos_months": sum(1 for x in mr if x > 0) / len(mr), "best_month": max(mr), "worst_month": min(mr), "var95_month": var95, "cvar95_month": statistics.fmean(tail),
            "worst_12m": min(w12) if w12 else None, "best_12m": max(w12) if w12 else None, "years": {str(k): v for k, v in year.items()}, "yrs": yrs}


def sharpe_ci(rets, rf_daily=0.0, B=300, block=21, seed=11):
    rnd = random.Random(seed)
    n = len(rets)
    if n < 200:
        return None
    vals = []
    for _ in range(B):
        s, i = [], rnd.randrange(n)
        while len(s) < n:
            s.append(rets[i])
            i = rnd.randrange(n) if rnd.random() < 1 / block else (i + 1) % n
        sd = statistics.pstdev(s)
        if sd > 0:
            vals.append((statistics.fmean(s) - rf_daily) / sd * math.sqrt(TD))
    vals.sort()
    return [vals[int(0.05 * len(vals))], vals[int(0.95 * len(vals))]] if vals else None


def monte_carlo(rets, horizon=TD, paths=1500, block=21, seed=5):
    """Symulacja następnego roku: losowanie bloków dziennych wyników strategii (zachowuje autokorelację zmienności)."""
    rnd = random.Random(seed)
    n = len(rets)
    cps = [21 * k for k in range(1, 13)]
    snaps = {c: [] for c in cps}
    term, dd_hit, loss10 = [], 0, 0
    for _ in range(paths):
        eq, peak, i, worst = 1.0, 1.0, rnd.randrange(n), 0.0
        for d in range(1, horizon + 1):
            eq *= 1 + rets[i]
            peak = max(peak, eq)
            worst = min(worst, eq / peak - 1)
            if d in snaps:
                snaps[d].append(eq - 1)
            i = rnd.randrange(n) if rnd.random() < 1 / block else (i + 1) % n
        term.append(eq - 1)
        dd_hit += worst <= -0.20
        loss10 += eq - 1 <= -0.10
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]
    return {"horizon_days": horizon, "paths": paths, "fan": {str(c): [q(v, p) for p in (0.05, 0.25, 0.5, 0.75, 0.95)] for c, v in snaps.items()},
            "median": q(term, 0.5), "p5": q(term, 0.05), "p95": q(term, 0.95), "prob_loss": sum(1 for x in term if x < 0) / paths, "prob_loss_10": loss10 / paths, "prob_dd_20": dd_hit / paths}


STRESS = [("Bessa krypto 2018", "2018-01-01", "2018-12-31"), ("Krach COVID 2020", "2020-02-19", "2020-03-23"), ("Hossa 2021", "2021-01-01", "2021-11-10"), ("Bessa 2022", "2022-01-01", "2022-12-31")]


def stress_table(dates, eq):
    out = []
    for name, a, b in STRESS:
        ta, tb = int(datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()), int(datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        i, j = bisect.bisect_left(dates, ta), bisect.bisect_right(dates, tb) - 1
        if i < 1 or j <= i or dates[0] > ta + 7 * DAY:
            continue
        seg = eq[i - 1:j + 1]
        out.append({"name": name, "from": a, "to": b, "ret": seg[-1] / seg[0] - 1, "max_dd": max_dd(seg)[0]})
    return out


def weekly(dates, series, step=5):
    idx = list(range(0, len(dates), step))
    if idx[-1] != len(dates) - 1:
        idx.append(len(dates) - 1)
    return [[datetime.fromtimestamp(dates[i], timezone.utc).strftime("%Y-%m-%d"), round(series[i], 5)] for i in idx]


# ----------------------------------------------------------------------------
def analyze(con, now_ts=None):
    uni = universe(con)
    if len(uni) < 4:
        raise SystemExit("Za mało aktywów w bazie (min. 4 z 300+ sesjami): uruchom btc_collect.py backfill.")
    days, px, rf = load_prices(con, uni)
    n = len(days)
    # pierwszy dzień, od którego da się zbudować portfel z min. 4 aktywami (po 252 sesjach historii)
    start = next((t for t in range(TD + 1, n) if sum(1 for a in uni if features(px[a["key"]], t)) >= 4), None)
    if start is None:
        raise SystemExit("Za krótka historia: potrzeba co najmniej 4 aktywów z 1,5 rokiem notowań.")
    start_dates = days[start:]
    keys = [a["key"] for a in uni]
    gold_key = next((a["key"] for a in uni if a["key"] in ("XAUUSD", "PAXGUSDT")), None)
    bench = {"Bitcoin (kup i trzymaj)": buy_hold(days, px, "BTCUSDT", start) if "BTCUSDT" in px else None,
             "S&P 500": buy_hold(days, px, "SPX", start) if "SPX" in px else None,
             "Złoto": buy_hold(days, px, gold_key, start) if gold_key else None,
             "60/40 (S&P 500 + obligacje)": static_mix(days, px, {"SPX": 0.6, "TLT": 0.4}, start) if "SPX" in px and "TLT" in px else None,
             "Równe wagi (całe uniwersum)": equal_weight(days, px, keys, start)}
    bench = {k: v for k, v in bench.items() if v}
    rf_mean = statistics.fmean(rf[start:])
    out_p = []
    for pid, prof in PROFILES.items():
        bt = backtest(days, px, rf, uni, prof, start_idx=start)
        st = stats(bt["dates"], bt["equity"], bt["rets"], rf_mean)
        st["sharpe_ci"] = sharpe_ci(bt["rets"], rf_mean)
        w, info, vol_now = target_weights(px, uni, n - 1, prof)
        cur = [{"key": k, "name": next(a["name"] for a in uni if a["key"] == k), "cls": info[k]["cls"], "cls_pl": CLASS_PL[info[k]["cls"]], "weight": wv, "vol": info[k]["vol"], "mom12": info[k]["m12"],
                "mom6": info[k]["m6"], "dist200": info[k]["dist200"], "score": info[k]["score"], "price": px[k][n - 1]} for k, wv in sorted(w.items(), key=lambda kv: -kv[1])]
        by_cls = {}
        for c in cur:
            by_cls[c["cls_pl"]] = by_cls.get(c["cls_pl"], 0) + c["weight"]
        out_p.append({"id": pid, "name": prof["name"], "icon": prof["icon"], "risk": prof["risk"], "desc": prof["desc"], "params": {k: prof[k] for k in ("target_vol", "max_assets", "max_w", "min_cash", "trend", "abs_mom", "caps")},
                      "stats": st, "avg_holdings": bt["avg_hold"], "avg_cash": bt["avg_cash"], "turnover_year": bt["turnover_year"], "equity": weekly(bt["dates"], bt["equity"]), "drawdown": weekly(bt["dates"], drawdown_series(bt["equity"])),
                      "stress": stress_table(bt["dates"], bt["equity"]), "montecarlo": monte_carlo(bt["rets"]),
                      "current": {"weights": cur, "cash": max(0.0, 1 - sum(w.values())), "by_class": by_cls, "expected_vol": vol_now, "var95_month": 1.645 * vol_now / math.sqrt(12), "n": len(cur)}})
    bstats = {}
    for name, eq in bench.items():
        r = [eq[0] - 1] + [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
        bstats[name] = {"stats": stats(start_dates, eq, r, rf_mean), "equity": weekly(start_dates, eq)}
    cov = {}
    for a in uni:
        cov[CLASS_PL[a["cls"]]] = cov.get(CLASS_PL[a["cls"]], 0) + 1
    warns = []
    for c, label in (("bond", "obligacji"), ("commodity", "surowców"), ("stock", "akcji"), ("crypto", "krypto")):
        if CLASS_PL[c] not in cov:
            warns.append(f"Brak aktywów klasy {CLASS_PL[c]} w bazie (nie ma {label}): profile defensywne mają mniej możliwości i częściej zostają w gotówce.")
    if "Obligacje" in cov and cov.get("Surowce", 0) < 2:
        warns.append("Mało surowców w bazie: dywersyfikacja jest ograniczona.")
    now = datetime.fromtimestamp(days[-1], timezone.utc)
    nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "data": {"from": datetime.fromtimestamp(days[start], timezone.utc).strftime("%Y-%m-%d"), "to": now.strftime("%Y-%m-%d"),
            "years": round((n - start) / TD, 1), "assets": [{"key": a["key"], "name": a["name"], "cls": CLASS_PL[a["cls"]]} for a in uni], "coverage": cov, "warnings": warns, "next_rebalance": nxt.strftime("%Y-%m-%d"), "cash_rate_avg": rf_mean * TD},
            "profiles": out_p, "benchmarks": bstats,
            "notes": ["Test historyczny z miesięcznym równoważeniem: w każdym dniu decyzji użyto wyłącznie danych z przeszłości (aktywo wchodzi po 252 sesjach historii).",
                      "Lista aktywów to Twój watchlist, więc wynik jest zawyżony błędem przetrwania. Parametry profili ustalono z góry, nie dopasowywano ich do historii.",
                      "Koszty: od 0,05% (obligacje, indeksy) do 0,15% (krypto) wartości obrotu. Podatki, poślizg w stresie i różnice w dostępności instrumentów nie są uwzględnione.",
                      "Przedział Sharpe'a to bootstrap blokowy (5-95%). Symulacja przyszłości losuje bloki dziennych wyników strategii: nie przewiduje, tylko pokazuje rozrzut przy powtórzeniu historii.",
                      "Historia obejmuje kilka cykli rynkowych. Wysoki wynik w przeszłości nie gwarantuje zysku. To narzędzie analityczne, nie porada inwestycyjna."]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--out", default=os.path.join(HERE, "btc_strategies.json"))
    a = ap.parse_args()
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rep = analyze(con)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, separators=(",", ":"), default=float)
    os.replace(tmp, a.out)
    print(f"Dane {rep['data']['from']} → {rep['data']['to']} ({rep['data']['years']} lat, {len(rep['data']['assets'])} aktywów)")
    print(f"{'profil':14}{'CAGR':>8}{'zmienn.':>9}{'Sharpe':>8}{'maxDD':>8}{'mies.+':>8}")
    for p in rep["profiles"]:
        s = p["stats"]
        print(f"{p['name']:14}{s['cagr'] * 100:7.1f}%{s['vol'] * 100:8.1f}%{s['sharpe'] or 0:8.2f}{s['max_dd'] * 100:7.1f}%{s['pos_months'] * 100:7.0f}%")
    for k, v in rep["benchmarks"].items():
        s = v["stats"]
        print(f"{k[:26]:28}{s['cagr'] * 100:5.1f}%{s['vol'] * 100:8.1f}%{s['sharpe'] or 0:8.2f}{s['max_dd'] * 100:7.1f}%")


if __name__ == "__main__":
    main()
