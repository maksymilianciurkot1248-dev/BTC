#!/usr/bin/env python3
"""Zespół sygnałów: rozbudowany algorytm decyzyjny (long / short / czekaj) dla świec dziennych.

Idea: kilka niezależnych rodzin sygnałów głosuje, a wagi głosów zależą od rozpoznanego reżimu rynku. Wynik to ocena -1..+1,
pewność, zgodność rodzin, rozmiar pozycji skorygowany o zmienność oraz kompletny plan (wejście, stop, cele) w TYM SAMYM formacie, co
btc_analysis.plan_at. Dzięki temu ta sama funkcja idzie przez test wsteczny (walk-forward, koszty, dziennik propozycji), mapę przewagi
i bota: wynik testu dotyczy dokładnie tej logiki, którą bot stosuje na żywo.

Rodziny sygnałów (każda daje ocenę -1..+1 i krótkie uzasadnienie):
  trend      pozycja ceny względem SMA20/50/200 i nachylenie SMA50
  momentum   stopy zwrotu 20/60/120 dni znormalizowane zmiennością + histogram MACD
  wybicie    kanały Donchiana 20 i 55 dni
  powrót     odchylenie od SMA20 (z-score) i RSI, ważne głównie w rynku bocznym
  przepływ   wolumen ponad normę po stronie ruchu i nachylenie OBV
  siła rel.  wynik aktywa względem BTC (dla altów)
Reżim (współczynnik efektywności ruchu): trend / mieszany / boczny, z innymi wagami rodzin.
Filtry: BTC pod SMA50 osłabia longi na altach, dzień publikacji makro blokuje wejścia, a wysoka zmienność zmniejsza pozycję.
Nakładki tylko na żywo (nie da się ich uczciwie przetestować wstecz, więc mogą WYŁĄCZNIE zmniejszać rozmiar lub blokować wejście):
funding skrajnie po stronie wejścia.
Algorytm nie jest wyuczony na danych (stałe, jawne wagi), więc test wsteczny jest uczciwym testem poza próbą, a nie dopasowaniem.
"""
import bisect
import math
from datetime import datetime, timezone

import btc_analysis as ba

DAY_MS = ba.DAY_MS
WARMUP = ba.PLAN_WARMUP
LONG_TH, SHORT_TH = 0.30, 0.38          # próg oceny; short jest ostrożniejszy
MIN_AGREE = 0.60                        # minimalna zgodność rodzin z kierunkiem
MIN_CONF = 55                           # minimalna pewność (jak PLAN_MIN_CONF w analizatorze)
MAX_RISK_PX_PCT = 12.0                  # stop szerszy niż 12% ceny: czekamy
FAMILIES = ("trend", "momentum", "wybicie", "powrot", "przeplyw", "sila")
LABEL = {"trend": "Trend", "momentum": "Momentum", "wybicie": "Wybicie", "powrot": "Powrót do średniej", "przeplyw": "Przepływ (wolumen)", "sila": "Siła względem BTC"}
WEIGHTS = {   # wagi rodzin w poszczególnych reżimach (sumują się do 1 po pominięciu niedostępnych)
    "trend": {"trend": .27, "momentum": .24, "wybicie": .18, "powrot": .03, "przeplyw": .10, "sila": .18},
    "mixed": {"trend": .20, "momentum": .20, "wybicie": .14, "powrot": .16, "przeplyw": .10, "sila": .20},
    "range": {"trend": .08, "momentum": .10, "wybicie": .08, "powrot": .46, "przeplyw": .08, "sila": .20},
}
REGIME_TXT = {"trend": "rynek w trendzie", "mixed": "rynek mieszany", "range": "rynek boczny"}
STOP_ATR = {"trend": 1.6, "mixed": 1.4, "range": 1.2}
R1 = {"trend": 1.6, "mixed": 1.4, "range": 1.2}
R2 = {"trend": 3.0, "mixed": 2.4, "range": 1.8}
# Parametry silnika. Wartości domyślne to dotychczasowy algorytm (wariant "v1"); inne warianty podmieniają wybrane pola
# (btc_lab.VARIANTS). Dzięki temu każdy wariant idzie przez TEN SAM kod w teście wstecznym, trybie cienia i u tradera.
DEFAULTS = {
    "long_th": LONG_TH, "short_th": SHORT_TH,   # progi oceny zespołu
    "min_agree": MIN_AGREE, "min_conf": MIN_CONF, "max_risk_px": MAX_RISK_PX_PCT,
    "weights": WEIGHTS,                         # wagi rodzin w reżimach; waga 0 wyłącza rodzinę
    "fam_sign": {},                             # np. {"sila": -1}: rodzina liczona kontrariańsko
    "stop_atr": STOP_ATR, "stop_cap": 3.2,      # stop: co najmniej stop_atr × ATR, "za swingiem" najwyżej stop_cap × ATR
    "r1": R1, "r2": R2,                         # cele w R
    "entry_pull": 0.25,                         # wejście limit: cofnięcie o tyle ATR od zamknięcia
    "max_hold": ba.PLAN_MAX_HOLD_BARS,          # dni w pozycji (stop czasowy)
    "sides": ("long", "short"),
    "btc_down_mult": 0.6,                       # BTC pod SMA50: ocena longa na altach × tyle
    "conf_size": True,                          # rozmiar pozycji zależny od pewności
}


def make_params(overrides=None):
    """Parametry silnika: domyślne + nadpisania wariantu (nieznane klucze są błędem, żeby literówka nie przeszła po cichu)."""
    p = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"nieznany parametr wariantu: {k}")
        p[k] = v
    return p


def sgn(x):
    return (x > 0) - (x < 0)


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _prep(ind):
    """Szeregi pomocnicze liczone raz na zestaw wskaźników."""
    if "_dec" in ind:
        return ind["_dec"]
    c = ind["c"]
    cl = ind["cl"]
    n = len(cl)
    ret = [0.0] + [math.log(cl[i] / cl[i - 1]) if cl[i - 1] and cl[i] else 0.0 for i in range(1, n)]
    rv = [None] * n                     # zmienność dzienna (odchylenie std. z 20 dni)
    for i in range(20, n):
        w = ret[i - 19:i + 1]
        m = sum(w) / 20
        rv[i] = math.sqrt(sum((x - m) ** 2 for x in w) / 19)
    obv, acc = [0.0] * n, 0.0
    for i in range(1, n):
        acc += sgn(cl[i] - cl[i - 1]) * (c[i][5] or 0.0)
        obv[i] = acc
    d = {"ret": ret, "rv": rv, "obv": obv, "vp": _vol_pct_series(rv), "fam": {}}
    ind["_dec"] = d
    return d


def _vol_pct(rv, t):
    """Percentyl bieżącej zmienności względem ostatnich ~250 dni."""
    hist = [x for x in rv[max(20, t - 250):t] if x]
    if len(hist) < 60 or not rv[t]:
        return 0.5
    return sum(1 for x in hist if x <= rv[t]) / len(hist)


def _vol_pct_series(rv):
    """_vol_pct dla każdego t naraz: posortowane okno kroczące zamiast liczenia 250 wartości od nowa (wynik identyczny)."""
    out, win = [0.5] * len(rv), []
    for t in range(len(rv)):
        if t - 1 >= 20 and rv[t - 1]:
            bisect.insort(win, rv[t - 1])
        if t - 251 >= 20 and rv[t - 251]:
            win.pop(bisect.bisect_left(win, rv[t - 251]))
        if len(win) >= 60 and rv[t]:
            out[t] = bisect.bisect_right(win, rv[t]) / len(win)
    return out


def _er(cl, t, n=20):
    path = sum(abs(cl[i] - cl[i - 1]) for i in range(t - n + 1, t + 1))
    return abs(cl[t] - cl[t - n]) / path if path else 0.0


# --------------------------------------------------------------- rodziny sygnałów
def f_trend(ind, t, txt=True):
    cl = ind["cl"]
    p, s20, s50, s200, s50p = cl[t], ind["sma20"][t], ind["sma50"][t], ind["sma200"][t], ind["sma50"][t - 10]
    parts = [(.30, sgn(p - s200)), (.20, sgn(s50 - s200)), (.20, sgn(p - s50)), (.15, sgn(s20 - s50)), (.15, sgn(s50 - s50p) if s50p else 0)]
    sc = sum(w * v for w, v in parts)
    if not txt:
        return sc, ""
    up = sum(1 for _, v in parts if v > 0)
    return sc, f"{up} z 5 sygnałów trendu po stronie wzrostu (cena vs SMA200/SMA50, SMA50 vs SMA200, SMA20 vs SMA50, nachylenie SMA50)"


def f_momentum(ind, t, txt=True):
    cl, rv = ind["cl"], _prep(ind)["rv"]
    sig = rv[t] or 0.03
    ss, parts = [], []
    for h, w in ((20, .40), (60, .35), (120, .25)):
        roc = cl[t] / cl[t - h] - 1 if t >= h and cl[t - h] else 0.0
        z = roc / (sig * math.sqrt(h))
        ss.append(w * math.tanh(z / 1.5))
        if txt:
            parts.append(f"{h} d {roc * 100:+.1f}%")
    A, mh = ind["atr"][t], ind["mh"][t]
    macd = math.tanh(mh / (A * 0.25)) if A and mh is not None else 0.0
    sc = 0.8 * sum(ss) + 0.2 * macd
    return sc, ("stopy zwrotu " + ", ".join(parts) + " (względem zmienności), MACD " + ("rośnie" if macd > 0 else "słabnie")) if txt else ""


def f_breakout(ind, t, txt=True):
    c, cl = ind["c"], ind["cl"]
    hi20, lo20 = max(x[2] for x in c[t - 20:t]), min(x[3] for x in c[t - 20:t])
    hi55, lo55 = max(x[2] for x in c[t - 55:t]), min(x[3] for x in c[t - 55:t])
    p = cl[t]
    if p > hi55:
        return 1.0, "zamknięcie powyżej 55-dniowego szczytu"
    if p > hi20:
        return 0.6, "zamknięcie powyżej 20-dniowego szczytu"
    if p < lo55:
        return -1.0, "zamknięcie poniżej 55-dniowego dołka"
    if p < lo20:
        return -0.6, "zamknięcie poniżej 20-dniowego dołka"
    half = (hi20 - lo20) / 2 or 1e-9
    pos = (p - (hi20 + lo20) / 2) / half
    return clamp(pos) * 0.35, f"cena w kanale 20 dni (pozycja {pos * 50 + 50:.0f}% zakresu)" if txt else ""


def f_meanrev(ind, t, txt=True):
    cl = ind["cl"]
    w = cl[t - 19:t + 1]
    m = sum(w) / 20
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / 19) or 1e-9
    z = (cl[t] - m) / sd
    r = ind["rsi"][t] if ind["rsi"][t] is not None else 50
    return -math.tanh(z / 1.6) * 0.6 + (50 - r) / 50 * 0.4, f"odchylenie od SMA20 {z:+.1f}σ, RSI {r:.0f}" if txt else ""


def f_flow(ind, t, txt=True):
    c, cl = ind["c"], ind["cl"]
    v = [x[5] or 0.0 for x in c[t - 20:t]]
    m = sum(v) / 20
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / 19) or 1e-9
    volz = ((c[t][5] or 0.0) - m) / sd
    ret = cl[t] / cl[t - 1] - 1
    part1 = max(0.0, math.tanh(volz / 2)) * sgn(ret) * 0.5
    obv = _prep(ind)["obv"]
    part2 = sgn(obv[t] - obv[t - 20]) * 0.5
    return part1 + part2, f"wolumen {volz:+.1f}σ wobec 20 dni, OBV {'rośnie' if part2 > 0 else 'spada' if part2 < 0 else 'płasko'}" if txt else ""


# --------------------------------------------------------------- silnik
class Engine:
    """Ma tę samą sygnaturę plan_at(ind, t, risk_pct) co btc_analysis.plan_at, więc działa w teście wstecznym i na żywo."""

    def __init__(self, symbol="", btc_candles=None, events_s=None, overlay=None, params=None, variant="v1", explain=True):
        self.p = make_params(params)
        self.variant = variant
        self.txt = bool(explain)              # False w testach wstecznych: bez tekstów objaśnień (te same decyzje, szybciej)
        self.symbol = symbol
        self.is_btc = symbol.upper() == "BTCUSDT"
        self.events = sorted(events_s or [])
        self.overlay = overlay or {}          # tylko na żywo: {"funding": ułamek za 8 h}
        self.btc_ts, self.btc_cl, self.btc_down = [], [], []
        if btc_candles and len(btc_candles) > 60:
            self.btc_ts = [x[0] for x in btc_candles]
            self.btc_cl = [x[4] for x in btc_candles]
            self.btc_down = [None] * len(btc_candles)
            for i in range(49, len(btc_candles)):
                self.btc_down[i] = self.btc_cl[i] < sum(self.btc_cl[i - 49:i + 1]) / 50

    # --- kontekst BTC
    def _btc_idx(self, ts):
        i = bisect.bisect_right(self.btc_ts, ts) - 1
        return i if i >= 0 else None

    def _btc_roc(self, ts, h):
        i = self._btc_idx(ts)
        return self.btc_cl[i] / self.btc_cl[i - h] - 1 if i is not None and i >= h and self.btc_cl[i - h] else None

    def f_strength(self, ind, t):
        if self.is_btc or not self.btc_ts:
            return None
        ts = ind["c"][t][0]
        rb = self._btc_roc(ts, 30)
        cl = ind["cl"]
        if rb is None or t < 30:
            return None
        ra = cl[t] / cl[t - 30] - 1
        sig = _prep(ind)["rv"][t] or 0.03
        return math.tanh((ra - rb) / (sig * math.sqrt(30) * 1.5)), f"{self.symbol or 'aktywo'} {ra * 100:+.1f}% vs BTC {rb * 100:+.1f}% (30 dni)" if self.txt else ""

    # --- decyzja
    def decide(self, ind, t):
        cl, c = ind["cl"], ind["c"]
        if t < WARMUP - 1 or not (ind["sma200"][t] and ind["atr"][t]) or ind["rsi"][t] is None:
            return None
        pre = _prep(ind)
        base = pre["fam"].get((t, self.txt))          # rodziny zależne tylko od świec aktywa: liczone raz, wspólne dla wszystkich wariantów
        if base is None:
            base = pre["fam"][(t, self.txt)] = (f_trend(ind, t, self.txt), f_momentum(ind, t, self.txt), f_breakout(ind, t, self.txt), f_meanrev(ind, t, self.txt),
                                               f_flow(ind, t, self.txt), _er(cl, t))
        er, vp = base[5], pre["vp"][t]
        regime = "trend" if er >= 0.35 else "range" if er <= 0.20 else "mixed"
        P = self.p
        fam = {"trend": base[0], "momentum": base[1], "wybicie": base[2], "powrot": base[3], "przeplyw": base[4], "sila": self.f_strength(ind, t)}
        for k, sign in P["fam_sign"].items():
            if sign < 0 and fam.get(k) is not None:
                fam[k] = (-fam[k][0], fam[k][1] + " (liczone kontrariańsko)" if self.txt else "")
        w = {k: v for k, v in P["weights"][regime].items() if fam[k] is not None and v > 0}
        tot = sum(w.values())
        S = sum(w[k] / tot * fam[k][0] for k in w)
        vetoes, notes, scal = [], [], 1.0
        ts = c[t][0]
        if self.btc_ts and not self.is_btc:
            i = self._btc_idx(ts)
            if i is not None and self.btc_down[i]:
                if S > 0 and P["btc_down_mult"] != 1:
                    S *= P["btc_down_mult"]
                    notes.append(f"filtr rynku: BTC pod SMA50 osłabia long na altach (ocena ×{P['btc_down_mult']:g})".replace(".", ","))
        if self.events:
            at = (ts + DAY_MS) / 1000
            j = bisect.bisect_left(self.events, at - 36 * 3600)
            if j < len(self.events) and self.events[j] <= at + 36 * 3600:
                vetoes.append("dzień ważnej publikacji makro: bez nowych wejść")
        if vp > 0.85:
            scal, notes = 0.6, notes + [f"zmienność w {vp * 100:.0f}. percentylu roku: pozycja ×0,6"]
        elif vp > 0.70:
            scal, notes = 0.8, notes + [f"podwyższona zmienność ({vp * 100:.0f}. percentyl): pozycja ×0,8"]
        side = "long" if S >= P["long_th"] else "short" if S <= -P["short_th"] else None
        if side and side not in P["sides"]:
            vetoes.append(f"kierunek {side} jest wyłączony w tym wariancie algorytmu")
        sg = 1 if side == "long" else -1
        active = [k for k in w if abs(fam[k][0]) > 0.15]
        agree = (sum(1 for k in active if sgn(fam[k][0]) == sgn(S)) / len(active)) if active else 0.0
        conf = int(clamp(120 * abs(S) * (0.6 + 0.4 * agree), 0, 95))
        fund = self.overlay.get("funding")
        if side and fund is not None:
            if fund * sg > 0.001:
                vetoes.append(f"funding skrajnie po stronie wejścia ({fund * 100:.3f}% za 8 h): tłok, bez wejścia")
            elif fund * sg > 0.0006:
                scal *= 0.6
                notes.append(f"funding wysoki po stronie wejścia ({fund * 100:.3f}% za 8 h): pozycja ×0,6")
        return {"S": S, "side": side, "agree": agree, "conf": conf, "regime": regime, "er": er, "vol_pct": vp, "fam": fam, "w": w, "tot": tot, "vetoes": vetoes, "notes": notes, "scal": scal}

    def plan_at(self, ind, t, risk_pct=1.0):
        d = self.decide(ind, t)
        if d is None:
            return None
        c, cl = ind["c"], ind["cl"]
        price, A = cl[t], ind["atr"][t]
        close_ms = c[t][0] + DAY_MS
        date = datetime.fromtimestamp(c[t][0] / 1000, tz=timezone.utc).date().isoformat()
        S, side, regime = d["S"], d["side"], d["regime"]
        P = self.p
        sg = 1 if side == "long" else -1
        plan = {"action": "wait", "date": date, "candle_ms": c[t][0], "close_ms": close_ms, "price": price, "atr": A, "atr_pct": A / price * 100,
                "trend_score": round(S * 5, 2), "trend": REGIME_TXT[regime], "rsi": ind["rsi"][t], "sma20": ind["sma20"][t], "sma50": ind["sma50"][t], "sma200": ind["sma200"][t],
                "macd_hist": ind["mh"][t], "reasons": [], "cautions": list(d["notes"]), "waiting_for": [], "entry_type": None, "setup": None, "entry": None, "stop": None,
                "tp1": None, "tp2": None, "rr": None, "confidence": None, "valid_until_ms": close_ms + ba.PLAN_VALID_BARS * DAY_MS, "strategy": "ensemble", "variant": self.variant}
        why = []
        if side is None:
            why.append(f"Ocena zespołu {S:+.2f} mieści się w strefie braku przewagi (long od +{P['long_th']:.2f}, short od −{P['short_th']:.2f}).")
        elif d["agree"] < P["min_agree"]:
            why.append(f"Kierunek {side} ({S:+.2f}), ale zgodność rodzin sygnałów tylko {d['agree'] * 100:.0f}% (wymagane {P['min_agree'] * 100:.0f}%).")
        elif d["conf"] < P["min_conf"]:
            why.append(f"Kierunek {side} ({S:+.2f}), ale pewność {d['conf']}% jest poniżej progu {P['min_conf']}%.")
        ok = side is not None and d["agree"] >= P["min_agree"] and d["conf"] >= P["min_conf"]
        if ok and d["vetoes"]:
            why += d["vetoes"]
            ok = False
        if ok:
            entry = price - sg * P["entry_pull"] * A                        # wejście limit z lekkim cofnięciem
            swing = (min(x[3] for x in c[t - 6:t + 1]) if side == "long" else max(x[2] for x in c[t - 6:t + 1]))
            k = P["stop_atr"][regime] * (1.25 if d["vol_pct"] > 0.85 else 1.0) * A
            dist = max(k, min(abs(entry - swing) + 0.15 * A, P["stop_cap"] * A))
            stop = entry - sg * dist
            risk_px = dist / entry * 100
            if risk_px > P["max_risk_px"]:
                why.append(f"Stop wypadłby {risk_px:.1f}% od wejścia (limit {P['max_risk_px']:.0f}%): czekamy na spokojniejszy układ.")
                ok = False
            else:
                r1, r2 = P["r1"][regime], P["r2"][regime]
                conf_f = clamp(0.6 + 0.4 * (d["conf"] - MIN_CONF) / 40, 0.6, 1.0) if P["conf_size"] else 1.0
                size_mult = round(clamp(d["scal"] * conf_f, 0.2, 1.0), 2)
                plan.update(action=side, entry=entry, stop=stop, tp1=entry + sg * r1 * dist, tp2=entry + sg * r2 * dist, rr=round(r1, 2), r1=round(r1, 2), r2=round(r2, 2),
                            confidence=d["conf"], risk_px_pct=round(risk_px, 2), entry_type="limit", setup="zespół", entry_note=[f"zespół sygnałów {S:+.2f}"], size_mult=size_mult,
                            notional_pct=round(risk_pct / risk_px * 100 * size_mult, 1), risk_budget_pct=risk_pct, max_hold_bars=P["max_hold"])
        if self.txt:
            plan["decision"] = self.explain(d, plan["action"], why)
            plan["reasons"] = plan["decision"]["reasons"]
        else:
            plan["reasons"] = why
        return plan

    def explain(self, d, action, why):
        fam = [{"key": k, "label": LABEL[k], "score": round(d["fam"][k][0], 3), "weight": round(d["w"].get(k, 0.0) / d["tot"], 3) if d["tot"] else 0.0, "text": d["fam"][k][1]} for k in FAMILIES if d["fam"][k] is not None]
        side = 1 if d["S"] > 0 else -1
        top = sorted(fam, key=lambda x: -abs(x["score"] * x["weight"]) * (1 if sgn(x["score"]) == side else 0.3))[:3]
        reasons = [f"{REGIME_TXT[d['regime']].capitalize()} (efektywność ruchu {d['er']:.2f}, zmienność w {d['vol_pct'] * 100:.0f}. percentylu): wagi rodzin dopasowane do reżimu.",
                   f"Ocena zespołu {d['S']:+.2f}, zgodność rodzin {d['agree'] * 100:.0f}%, pewność {d['conf']}%."]
        reasons += [f"{x['label']}: {x['text']} (ocena {x['score']:+.2f}, waga {x['weight'] * 100:.0f}%)." for x in top]
        reasons += why + d["notes"]
        return {"score": round(d["S"], 3), "confidence": d["conf"], "agree": round(d["agree"], 2), "regime": d["regime"], "regime_text": REGIME_TXT[d["regime"]], "action": action,
                "families": fam, "vetoes": d["vetoes"], "notes": d["notes"], "reasons": reasons, "thresholds": {"long": self.p["long_th"], "short": -self.p["short_th"]}, "size_scalar": round(d["scal"], 2),
                "variant": self.variant}
