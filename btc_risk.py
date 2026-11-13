#!/usr/bin/env python3
"""Ryzyko portfela i wyłączniki bezpieczeństwa (czyste funkcje, bez sieci i bez zapisów).

1. Ryzyko otwarte: dla każdej pozycji strata, jeśli zostanie uruchomiony jej stop-loss (|wejście - stop| × ilość). Pozycja bez stopa jest liczona
   konserwatywnie jako 10% jej wartości. Suma to "ryzyko brutto".
2. Ryzyko skorelowane: pierwiastek z sᵀΣs, gdzie s to ryzyka ze znakiem kierunku (long +, short −), a Σ to macierz korelacji dziennych zwrotów
   z ostatnich 90 dni, skurczona do priorytetu 0,6 (kryptowaluty poruszają się razem; próbka z 90 dni zaniża korelację w spokojnych okresach).
   Trzy longi na skorelowanych kryptowalutach to więc prawie jedna trzykrotnie większa pozycja, a nie trzy niezależne.
3. Nowa pozycja jest zmniejszana (najmniej do 30% planowanego rozmiaru), aby ryzyko skorelowane nie przekroczyło limitu, a ryzyko brutto
   nie przekroczyło 1,5 × limitu. Gdy nawet 30% się nie mieści, pozycji nie otwieramy.
4. Wyłączniki (blokują tylko NOWE pozycje; nadzór nad otwartymi trwa): seria N strat z rzędu (pauza H godzin od ostatniej straty), strata
   tygodniowa (do poniedziałku UTC) oraz spadek kapitału od szczytu (wymaga Twojego odblokowania, bo to sygnał, że coś jest nie tak).
"""
import math
import time
from datetime import datetime, timedelta, timezone

PRIOR_RHO = 0.6
SHRINK = 0.5                 # udział próbki w korelacji (reszta to priorytet)
GROSS_FACTOR = 1.5           # ryzyko brutto nie większe niż 1,5 × limit ryzyka skorelowanego
MIN_SCALE = 0.30
NO_STOP_RISK_PCT = 0.10      # pozycja bez stopa: 10% wartości


def f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


# ----------------------------------------------------------------- korelacje
def returns_from_candles(candles, n=90):
    """Dzienne zwroty logarytmiczne z ostatnich n świec: {ts_ms: zwrot}."""
    c = candles[-(n + 1):]
    out = {}
    for i in range(1, len(c)):
        if c[i - 1][4] and c[i][4] and c[i - 1][4] > 0 and c[i][4] > 0:
            out[c[i][0]] = math.log(c[i][4] / c[i - 1][4])
    return out


def corr_matrix(returns):
    """returns: {symbol: {ts: zwrot}} -> {(a, b): rho} dla a < b, skurczone do priorytetu. Za mało wspólnych dni: sam priorytet."""
    syms = sorted(returns)
    out = {}
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            common = sorted(set(returns[a]) & set(returns[b]))
            rho = PRIOR_RHO
            if len(common) >= 30:
                xs, ys = [returns[a][t] for t in common], [returns[b][t] for t in common]
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
                sy = math.sqrt(sum((y - my) ** 2 for y in ys))
                if sx > 0 and sy > 0:
                    sample = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)
                    rho = SHRINK * sample + (1 - SHRINK) * PRIOR_RHO
            out[(a, b)] = max(-1.0, min(1.0, rho))
    return out


def rho(corr, a, b):
    if a == b:
        return 1.0
    return corr.get((a, b) if a < b else (b, a), PRIOR_RHO)


# ----------------------------------------------------------------- ryzyko pozycji i portfela
def position_items(positions):
    """Pozycje z giełdy (surowe rekordy Bybit) -> [{symbol, side, risk}] w USDT. Stop po stronie zysku (np. po przesunięciu na wejście) = brak ryzyka."""
    out = []
    for p in positions:
        size = f(p.get("size"))
        if size <= 0:
            continue
        side = 1 if p.get("side") == "Buy" else -1
        avg, stop, value = f(p.get("avgPrice")), f(p.get("stopLoss")), f(p.get("positionValue")) or size * f(p.get("avgPrice"))
        risk = max(0.0, (avg - stop) * side) * size if stop > 0 and avg > 0 else value * NO_STOP_RISK_PCT
        out.append({"symbol": p.get("symbol"), "side": side, "risk": risk, "no_stop": not stop > 0})
    return out


def gross_risk(items):
    return sum(i["risk"] for i in items)


def corr_risk(items, corr):
    s = [(i["symbol"], i["side"] * i["risk"]) for i in items]
    var = sum(x * y * rho(corr, a, b) for a, x in s for b, y in s)
    return math.sqrt(max(var, 0.0))


def fit_scale(existing, new, corr, limit):
    """Największy mnożnik k (0..1) rozmiaru nowej pozycji, przy którym ryzyko skorelowane <= limit, a brutto <= GROSS_FACTOR × limit. 0 = nie mieści się."""
    def ok(k):
        its = existing + [{**new, "risk": new["risk"] * k}]
        return corr_risk(its, corr) <= limit * 1.0000001 and gross_risk(its) <= limit * GROSS_FACTOR * 1.0000001
    if ok(1.0):
        return 1.0
    if not ok(0.0):
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def summary(items, corr, capital):
    g, c = gross_risk(items), corr_risk(items, corr)
    return {"gross_usdt": round(g, 4), "corr_usdt": round(c, 4), "gross_pct": round(g / capital * 100, 3) if capital > 0 else None, "corr_pct": round(c / capital * 100, 3) if capital > 0 else None,
            "positions": [{"symbol": i["symbol"], "side": "long" if i["side"] > 0 else "short", "risk_usdt": round(i["risk"], 4), "no_stop": i["no_stop"]} for i in items]}


# ----------------------------------------------------------------- wyłączniki
def week_start(now_s):
    d = datetime.fromtimestamp(now_s, tz=timezone.utc)
    mon = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(mon.timestamp())


def loss_streak(rows, ignore_before_s=0):
    """(liczba strat z rzędu od najnowszej transakcji, czas ostatniej straty w s). Zysk przerywa serię; wynik zerowy jest pomijany."""
    pts = []
    for r in rows:
        ts = f(r.get("updatedTime") or r.get("createdTime")) / 1000
        if ts > ignore_before_s:
            pts.append((ts, f(r.get("closedPnl"))))
    pts.sort(reverse=True)
    n, last = 0, 0.0
    for ts, p in pts:
        if p > 0:
            break
        if p < 0:
            n += 1
            last = last or ts
    return n, last


def evaluate(now_s, rows, equity, base, cfg, risk_state):
    """Zwraca (lista aktywnych wyłączników, zaktualizowany stan ryzyka, metryki).
    rows: zamknięte pozycje (Bybit closed-pnl) co najmniej od początku tygodnia; base: kapitał do liczenia ryzyka; cfg: limity.
    risk_state: {"peak", "dd_halt", "reset_ts"} (zapisywany w trader_state.json)."""
    rs = dict(risk_state or {})
    reset = f(rs.get("reset_ts"))
    breakers = []
    # seria strat
    n, last = loss_streak(rows, reset)
    pause = cfg["streak_pause_h"] * 3600
    if n >= cfg["streak_losses"] and now_s < last + pause:
        breakers.append({"key": "streak", "label": f"seria {n} strat z rzędu", "until": int(last + pause), "detail": f"pauza {cfg['streak_pause_h']} h od ostatniej straty", "manual": False})
    # strata tygodniowa
    w0 = max(week_start(now_s), reset)
    week = sum(f(r.get("closedPnl")) for r in rows if f(r.get("updatedTime") or r.get("createdTime")) / 1000 >= w0)
    limit_w = base * cfg["max_weekly_loss_pct"] / 100
    if week < 0 and -week >= limit_w > 0:
        breakers.append({"key": "weekly", "label": f"strata tygodniowa {week:.2f} USDT", "until": week_start(now_s) + 7 * 86400, "detail": f"limit {cfg['max_weekly_loss_pct']}% kapitału, do poniedziałku UTC", "manual": False})
    # spadek od szczytu (wymaga odblokowania)
    peak = max(f(rs.get("peak")), equity) if equity > 0 else f(rs.get("peak"))
    dd = (peak - equity) / peak * 100 if peak > 0 and equity > 0 else 0.0
    if rs.get("dd_halt"):
        breakers.append({"key": "drawdown", "label": f"spadek kapitału {rs['dd_halt'].get('dd', dd):.1f}% od szczytu", "until": None, "detail": "wymaga Twojego odblokowania (panel lub plik RISK_RESET)", "manual": True})
    elif dd >= cfg["max_drawdown_pct"] > 0:
        rs["dd_halt"] = {"since": int(now_s), "peak": round(peak, 2), "equity": round(equity, 2), "dd": round(dd, 2)}
        breakers.append({"key": "drawdown", "label": f"spadek kapitału {dd:.1f}% od szczytu", "until": None, "detail": "wymaga Twojego odblokowania (panel lub plik RISK_RESET)", "manual": True})
    rs["peak"] = round(peak, 4)
    metrics = {"streak": n, "week_pnl": round(week, 4), "week_limit": round(-limit_w, 4), "drawdown_pct": round(dd, 3), "peak": round(peak, 2), "equity": round(equity, 2)}
    return breakers, rs, metrics


def apply_reset(risk_state, equity, now_s):
    """Odblokowanie: uznajemy dotychczasowe straty (seria i tydzień liczą się od teraz), nowy szczyt = bieżący kapitał."""
    rs = dict(risk_state or {})
    rs.pop("dd_halt", None)
    rs["reset_ts"] = int(now_s)
    rs["peak"] = round(equity, 4)
    return rs
