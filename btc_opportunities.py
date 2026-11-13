#!/usr/bin/env python3
"""Skaner okazji zakupowych (krypto, surowce, waluty, akcje) z raportem pozycji i pętlą uczenia się.

Rdzeń używany przez btc_api.py (usługa) oraz z linii poleceń:
    python btc_opportunities.py scan                 # lista okazji teraz
    python btc_opportunities.py report XAUUSD        # raport dla jednego aktywa
    python btc_opportunities.py replay               # symulacja na historii (walk-forward): startowa skuteczność systemu
Klucze aktywów = symbole w bazie: BTCUSDT, XAUUSD, EURUSD, NVDA.US, CDR.PL ...

Metoda (tylko pozycje długie, "kupuj dołek w dobrej strukturze"):
  Dołek: cena daleko od 60-dniowego szczytu (w jednostkach ATR), wyprzedanie (RSI/Bollinger), oznaki stabilizacji (min. 2 z 4)
         i nienaruszona struktura długoterminowa; "spadający nóż" (nowe minima bez stabilizacji pod spadającą SMA200) jest odrzucany.
  Poziomy: stop za lokalnym minimum (1,2-3,5 ATR), cel 1 = 1,5R, dalsze cele z zniesień Fibonacciego i szczytu.
  Prawdopodobieństwa i czas: z historii TEGO aktywa (analogi dołków, bariery stop/cel w 20 sesjach, tylko wyniki już znane w danym dniu),
         a gdy próba jest za mała, z puli aktywów tej samej klasy.
  Uczenie się: każda okazja trafia do dziennika (signals); wyniki (wejście/stop/cel/czas) korygują prawdopodobieństwa (skurczenie do
         zrealizowanej skuteczności), szerokość stopów i próg wyniku osobno dla każdej klasy aktywów.
To narzędzie analityczne, nie porada inwestycyjna; nie składa żadnych zleceń.
"""
import argparse
import bisect
import json
import math
import os
import sqlite3
import statistics
import sys
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_markets as bm  # noqa: E402

DAY = 86400
NAN = math.nan
H_BARS = 20                 # horyzont oceny okazji (sesje)
TP1_R = 1.5
RECENT = 420                # liczba ostatnich świec do wskaźników "na żywo"
CLASS_COST = {"crypto": 0.0015, "commodity": 0.0008, "fx": 0.0003, "stock": 0.0010}   # koszt wejścia+wyjścia (część ceny)
CLASS_PL = {"crypto": "Krypto", "commodity": "Surowce", "fx": "Waluty", "stock": "Akcje"}
DEFAULT_PARAMS = {"stop_mult": {c: 1.0 for c in CLASS_COST}, "min_score": {c: 65 for c in CLASS_COST}, "calib_k": 20}
EVENT_PL = {"cpi": "CPI", "nfp": "NFP", "fomc": "FOMC", "pce": "PCE", "gdp": "PKB USA", "retail": "sprzedaż detaliczna", "jolts": "JOLTS", "ppi": "PPI",
            "opex_m": "wygasanie opcji", "opex_q": "kwartalne wygasanie opcji"}
BIG_EVENTS = ("cpi", "nfp", "fomc", "pce", "opex_q")

SIGNALS_SQL = """
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, symbol TEXT, cls TEXT, source TEXT,
    entry_lo REAL, entry_hi REAL, stop REAL, tp1 REAL, score REAL, p_model REAL, horizon INTEGER, atr REAL,
    status TEXT, fill_ts INTEGER, fill_price REAL, close_ts INTEGER, close_price REAL, outcome TEXT, r_mult REAL, recovered INTEGER);
CREATE INDEX IF NOT EXISTS signals_sym ON signals(symbol, ts);
CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, symbol TEXT, entry REAL, stop REAL, tp1 REAL, tp2 REAL,
    qty REAL, note TEXT, status TEXT DEFAULT 'open');
"""


# ----------------------------------------------------------------------------
# Wskaźniki (listy tej samej długości co świece; NaN przed rozgrzewką)
# ----------------------------------------------------------------------------
def indicators(o, h, l, c):
    n = len(c)

    def sma(x, k):
        out, s = [NAN] * n, 0.0
        for i in range(n):
            s += x[i]
            if i >= k:
                s -= x[i - k]
            if i >= k - 1:
                out[i] = s / k
        return out
    s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
    sd20, s, s2 = [NAN] * n, 0.0, 0.0
    for i in range(n):
        s, s2 = s + c[i], s2 + c[i] * c[i]
        if i >= 20:
            s, s2 = s - c[i - 20], s2 - c[i - 20] ** 2
        if i >= 19:
            m = s / 20
            sd20[i] = math.sqrt(max(s2 / 20 - m * m, 0.0))
    pctb = [(c[i] - (s20[i] - 2 * sd20[i])) / (4 * sd20[i]) if sd20[i] == sd20[i] and sd20[i] > 0 else NAN for i in range(n)]
    rsi, ag, al = [NAN] * n, 0.0, 0.0
    atr, a_prev = [NAN] * n, 0.0
    for i in range(1, n):
        d = c[i] - c[i - 1]
        g, ls = max(d, 0.0), max(-d, 0.0)
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if i <= 14:
            ag, al, a_prev = ag + g / 14, al + ls / 14, a_prev + tr / 14
            if i == 14:
                rsi[i] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
                atr[i] = a_prev
        else:
            ag, al = (ag * 13 + g) / 14, (al * 13 + ls) / 14
            a_prev = (a_prev * 13 + tr) / 14
            rsi[i] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
            atr[i] = a_prev
    hi60, low10, new20low = [NAN] * n, [NAN] * n, [False] * n
    for i in range(n):
        if i >= 59:
            hi60[i] = max(c[i - 59:i + 1])
        if i >= 9:
            low10[i] = min(l[i - 9:i + 1])
        if i >= 20:
            new20low[i] = l[i] <= min(l[i - 20:i])
    stab, depth, mom, slope200, trend_ok, knife, setup, oversold = [0] * n, [NAN] * n, [NAN] * n, [NAN] * n, [False] * n, [False] * n, [False] * n, [False] * n
    for i in range(n):
        if i >= 20 and rsi[i - 3] == rsi[i - 3] and rsi[i] == rsi[i]:
            stab[i] = int(c[i] > o[i]) + int(l[i] > l[i - 1]) + int(rsi[i] > rsi[i - 3]) + int(c[i] > c[i - 1])
        if atr[i] == atr[i] and atr[i] > 0 and hi60[i] == hi60[i]:
            depth[i] = (hi60[i] - c[i]) / atr[i]
        if i >= 252:
            mom[i] = c[i - 21] / c[i - 252] - 1
        if i >= 220 and s200[i] == s200[i] and s200[i - 20] == s200[i - 20]:
            slope200[i] = s200[i] / s200[i - 20] - 1
        if s200[i] == s200[i]:
            trend_ok[i] = c[i] >= 0.85 * s200[i] or (mom[i] == mom[i] and mom[i] > 0)
        oversold[i] = (rsi[i] == rsi[i] and rsi[i] <= 40) or (pctb[i] == pctb[i] and pctb[i] <= 0.15)
        if i >= 221 and s200[i] == s200[i] and slope200[i] == slope200[i]:
            knife[i] = oversold[i] and stab[i] <= 1 and (new20low[i] or new20low[i - 1]) and c[i] < 0.9 * s200[i] and slope200[i] < 0
            setup[i] = oversold[i] and depth[i] == depth[i] and depth[i] >= 2.5 and stab[i] >= 2 and trend_ok[i] and not knife[i]
    return {"s20": s20, "s50": s50, "s200": s200, "sd20": sd20, "pctb": pctb, "rsi": rsi, "atr": atr, "hi60": hi60, "low10": low10, "stab": stab, "depth": depth,
            "mom": mom, "slope200": slope200, "trend_ok": trend_ok, "knife": knife, "setup": setup, "oversold": oversold, "new20low": new20low}


def stop_distance(entry, atr, low10, mult):
    raw = entry - (low10 - 0.3 * atr)
    d = min(max(raw, 1.2 * atr), 3.5 * atr) * mult
    return min(d, 4.5 * atr)


def run_barrier(h, l, c, i, entry, stop, tp, horizon=H_BARS):
    """Pierwsza bariera w horyzoncie: stop (przy remisie w jednej świecy zakładamy stop), cel 1 albo czas. None = jeszcze nierozstrzygnięte."""
    n, risk = len(c), entry - stop
    if risk <= 0:
        return None
    for j in range(i + 1, min(i + horizon, n - 1) + 1):
        if l[j] <= stop:
            return "stop", j - i, -1.0
        if h[j] >= tp:
            return "tp1", j - i, (tp - entry) / risk
    if i + horizon <= n - 1:
        return "timeout", horizon, (c[i + horizon] - entry) / risk
    return None


def outcomes(S, mult, cost, start=215):
    """Wyniki barier dla wszystkich świec: dołki (setup=True) i tło (co 3. świeca). Wpis: (i, rozstrzygnięcie_i, wynik, dni, R, setup)."""
    I, h, l, c, o = S["ind"], S["h"], S["l"], S["c"], S["o"]
    out = []
    for i in range(start, len(c) - 1):
        is_setup = I["setup"][i]
        if not is_setup and i % 3:
            continue
        atr = I["atr"][i]
        if not (atr == atr and I["low10"][i] == I["low10"][i]):
            continue
        entry = c[i]
        d = stop_distance(entry, atr, I["low10"][i], mult)
        r = run_barrier(h, l, c, i, entry, entry - d, entry + TP1_R * d)
        if r is None:
            continue
        net = r[2] - cost * entry / d
        out.append((i, i + r[1], r[0], r[1], net, is_setup))
    return out


def stats_from(out, upto=None, spacing=5):
    """Statystyki dołków (setup) i tła z wyników znanych do świecy 'upto'. Wystąpienia dołków rozdzielone o >= spacing świec."""
    setups, base, last = [], [], -10 ** 9
    for i, res, kind, days, r, is_setup in out:
        if upto is not None and res > upto:
            continue
        if is_setup:
            if i - last >= spacing:
                setups.append((kind, days, r))
                last = i
        else:
            base.append((kind, days, r))
    def agg(rows):
        n = len(rows)
        if not n:
            return None
        tp = [d for k, d, _ in rows if k == "tp1"]
        st = [d for k, d, _ in rows if k == "stop"]
        return {"n": n, "p_tp1": sum(1 for k, _, _ in rows if k == "tp1") / n, "p_stop": sum(1 for k, _, _ in rows if k == "stop") / n,
                "p_timeout": sum(1 for k, _, _ in rows if k == "timeout") / n, "avg_r": statistics.fmean(r for _, _, r in rows),
                "days_tp1": _q(tp), "days_stop": _q(st)}
    return {"setup": agg(setups), "base": agg(base), "rows": setups}


def _q(xs):
    if not xs:
        return None
    xs = sorted(xs)
    pick = lambda q: xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]
    return {"p25": pick(0.25), "median": pick(0.5), "p75": pick(0.75), "n": len(xs)}


# ----------------------------------------------------------------------------
# Oceny
# ----------------------------------------------------------------------------
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def levels(S, i):
    """Wsparcia i opory z ostatnich 120 świec (pivoty k=3), średnie i Fibonacci zniesienia spadku od 60-dniowego szczytu."""
    h, l, c, I = S["h"], S["l"], S["c"], S["ind"]
    sup, res = [], []
    for j in range(max(3, i - 120), i - 2):
        if l[j] == min(l[j - 3:j + 4]):
            sup.append(l[j])
        if h[j] == max(h[j - 3:j + 4]):
            res.append(h[j])
    price = c[i]
    for name in ("s20", "s50", "s200"):
        v = I[name][i]
        if v == v:
            (sup if v < price else res).append(v)
    if I["sd20"][i] == I["sd20"][i]:
        sup.append(I["s20"][i] - 2 * I["sd20"][i])
    return sorted(x for x in sup if x < price), sorted(x for x in res if x > price)


def build_plan(S, i, mult, cost):
    I, c, l = S["ind"], S["c"], S["l"]
    price, atr = c[i], I["atr"][i]
    sup, res = levels(S, i)
    near = [x for x in sup if price - 1.0 * atr <= x <= price - 0.15 * atr]
    pref = max(near) if near else price - 0.4 * atr
    d = stop_distance(price, atr, I["low10"][i], mult)
    stop = price - d
    tp1 = price + TP1_R * d
    hi, lo = I["hi60"][i], min(l[max(0, i - 19):i + 1])
    fib618 = lo + 0.618 * (hi - lo)
    tp2 = fib618 if fib618 > tp1 + 0.25 * d else price + 2.5 * d
    tp3 = max(hi, tp2 + d)
    cost_r = cost * price / d
    return {"entry_hi": price, "entry_lo": pref, "entry_pref": pref, "stop": stop, "tp1": tp1, "tp2": tp2, "tp3": tp3, "stop_dist": d, "atr": atr,
            "stop_pct": d / price * 100, "rr1": TP1_R, "rr2": (tp2 - price) / d, "rr3": (tp3 - price) / d, "rr1_at_pref": (tp1 - pref) / (pref - stop) if pref > stop else None,
            "cost_r": cost_r, "supports": sup[-3:][::-1], "resistances": res[:3], "fib618": fib618}


def calibrate(p_model, n_model, realized, k):
    """Skurczenie prawdopodobieństwa modelu do zrealizowanej skuteczności: (n*real + k*model)/(n+k)."""
    if not realized or realized["n"] == 0:
        return p_model
    return (realized["n"] * realized["wins"] / realized["n"] + k * p_model) / (realized["n"] + k)


class Engine:
    def __init__(self, db_path, now_fn=time.time):
        self.db_path, self.now = db_path, now_fn
        self.lock = threading.RLock()
        self.con = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SIGNALS_SQL)
        self.uni, self.data, self.live, self.params = [], {}, {}, json.loads(json.dumps(DEFAULT_PARAMS))
        self.events, self.pool, self.macro_cache = {}, {}, None
        self.loaded_at = 0

    # --- uniwersum i dane
    def universe(self):
        u = [{"key": s + "USDT", "name": n, "cls": "crypto", "stooq": None, "bybit": s + "USDT"} for s, n in bm.CRYPTO]
        u += [{"key": k, "name": lab.split(" (")[0], "cls": "commodity", "stooq": code, "bybit": None} for k, (code, lab) in bm.COMMODITIES.items() if k in ("XAUUSD", "XAGUSD", "XPTUSD", "COPPER", "WTI")]
        u += [{"key": k, "name": lab, "cls": "fx", "stooq": code, "bybit": None} for k, (code, lab) in bm.FX_PAIRS.items()]
        u += [{"key": bm.db_symbol(s, m), "name": nm, "cls": "stock", "stooq": bm.stooq_code(s, m), "bybit": None, "market": m, "sector": sec} for s, m, nm, sec in bm.load_watchlist()]
        return u

    def load(self):
        with self.lock:
            self.uni = self.universe()
            data = {}
            for a in self.uni:
                rows = self._q("SELECT ts, o, h, l, c, v FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts", (a["key"],), "fetchall")
                rows = [r for r in rows if r[4] and r[2] and r[3] and r[1]]
                if len(rows) < 260:
                    continue
                S = {"d": [r[0] // DAY * DAY for r in rows], "o": [r[1] for r in rows], "h": [r[2] for r in rows], "l": [r[3] for r in rows], "c": [r[4] for r in rows], "v": [r[5] or 0 for r in rows]}
                S["ind"] = indicators(S["o"], S["h"], S["l"], S["c"])
                data[a["key"]] = {"a": a, "S": S}
            self.data = data
            self._load_params()
            self.events = {}
            for kind, ts in self.con.execute("SELECT kind, ts FROM events ORDER BY ts"):
                self.events.setdefault(kind, []).append(ts // DAY * DAY)
            self._build_stats()
            self.macro_cache = None
            self.vix = self._last_close("VIX")
            self.loaded_at = self.now()

    def _build_stats(self):
        """Wyniki barier dla każdego aktywa (cache do następnego load) oraz pule analogów per klasa."""
        pool = {}
        for k, e in self.data.items():
            cls = e["a"]["cls"]
            e["out"] = outcomes(e["S"], self.params["stop_mult"][cls], CLASS_COST[cls])
            e["stats"] = stats_from(e["out"])
            pool.setdefault(cls, []).extend(e["stats"]["rows"])
        self.pool = {}
        for cls, sets in pool.items():
            self.pool[cls] = {"n": len(sets), "p_tp1": (sum(1 for k, _, _ in sets if k == "tp1") / len(sets)) if sets else None,
                              "avg_r": statistics.fmean(r for _, _, r in sets) if sets else None,
                              "p_stop": (sum(1 for k, _, _ in sets if k == "stop") / len(sets)) if sets else None,
                              "p_timeout": (sum(1 for k, _, _ in sets if k == "timeout") / len(sets)) if sets else None,
                              "days_tp1": _q([d for k, d, _ in sets if k == "tp1"]), "days_stop": _q([d for k, d, _ in sets if k == "stop"])}

    def _load_params(self):
        r = self._q("SELECT v FROM meta WHERE k='opp_params'", "fetchone") if self._has("meta") else None
        if r:
            try:
                p = json.loads(r[0])
                for key in ("stop_mult", "min_score"):
                    self.params[key].update(p.get(key, {}))
                self.params["calib_k"] = p.get("calib_k", 20)
            except ValueError:
                pass

    def _q(self, sql, args=(), mode="fetchall"):
        """Odczyt przez wspólną blokadę: połączenie SQLite jest współdzielone przez wątki API."""
        if isinstance(args, str):
            args, mode = (), args
        with self.lock:
            cur = self.con.execute(sql, args)
            return cur.fetchone() if mode == "fetchone" else cur.fetchall()

    def _has(self, table):
        return bool(self._q("SELECT 1 FROM sqlite_master WHERE name=?", (table,), "fetchone"))

    # --- notowania na żywo i seria z nakładką
    def set_live(self, quotes, ts=None):
        with self.lock:
            self.live = {k: {"price": q["price"], "ts": ts or self.now(), "src": q.get("src")} for k, q in quotes.items() if q.get("price")}

    def live_series(self, key):
        """Ostatnie RECENT świec z bieżącą ceną jako (częściowa) dzisiejsza świeca i wskaźniki z niej."""
        e = self.data[key]
        S0 = e["S"]
        a, k0 = e["a"], max(0, len(S0["c"]) - RECENT)
        S = {x: S0[x][k0:] for x in ("d", "o", "h", "l", "c", "v")}
        q, today = self.live.get(key), int(self.now()) // DAY * DAY
        age = None
        if q and q["price"] > 0:
            p = q["price"]
            age = self.now() - q["ts"]
            if S["d"][-1] == today:
                S["c"][-1], S["h"][-1], S["l"][-1] = p, max(S["h"][-1], p), min(S["l"][-1], p)
            elif today > S["d"][-1] and (a["cls"] == "crypto" or abs(p / S["c"][-1] - 1) > 1e-9):
                S["d"].append(today)
                S["o"].append(S["c"][-1])
                S["h"].append(max(p, S["c"][-1]))
                S["l"].append(min(p, S["c"][-1]))
                S["c"].append(p)
                S["v"].append(0)
        S["ind"] = indicators(S["o"], S["h"], S["l"], S["c"])
        S["age"] = age
        return S

    # --- ocena
    def _stats_for(self, key):
        e = self.data[key]
        st, cls = e["stats"], e["a"]["cls"]
        own = st["setup"]
        if own and own["n"] >= 12:
            return own, st["base"], "to aktywo"
        pl = self.pool.get(cls)
        if pl and pl["n"] >= 12:
            return pl, st["base"] or pl, "pula klasy: " + CLASS_PL[cls]
        return None, st["base"], "brak próby"

    def realized(self, cls=None):
        q = "SELECT outcome, r_mult FROM signals WHERE source='live' AND outcome IN ('tp1','stop','timeout')" + (" AND cls=?" if cls else "")
        rows = self._q(q, (cls,) if cls else (), "fetchall")
        n = len(rows)
        return {"n": n, "wins": sum(1 for o, _ in rows if o == "tp1"), "avg_r": statistics.fmean(r for _, r in rows if r is not None) if n else None} if n else None

    def evaluate(self, key, full=False):
        e = self.data[key]
        a, cls = e["a"], e["a"]["cls"]
        S = self.live_series(key)
        i = len(S["c"]) - 1
        I = S["ind"]
        mult = self.params["stop_mult"][cls]
        stats, base, src = self._stats_for(key)
        k = self.params["calib_k"]
        realized = self.realized(cls)
        p_model = stats["p_tp1"] if stats else None
        p_cal = calibrate(p_model, stats["n"] if stats else 0, realized, k) if p_model is not None else None
        price, atr = S["c"][i], I["atr"][i]
        depth, rsi, stab = I["depth"][i], I["rsi"][i], I["stab"][i]
        flags, reasons, risks = [], [], []
        in_dip = bool(I["oversold"][i] and depth == depth and depth >= 2.5)
        knife = bool(I["knife"][i])
        setup = bool(I["setup"][i])
        plan = build_plan(S, i, mult, CLASS_COST[cls]) if atr == atr and I["low10"][i] == I["low10"][i] else None
        # składniki wyniku
        s_dip = 30 * (0.6 * clamp((depth if depth == depth else 0) / 6) + 0.4 * (1 if I["oversold"][i] else 0))
        s_stab = 15 * stab / 4
        s_trend = 20 * (0.4 * (1 if I["s200"][i] == I["s200"][i] and price > I["s200"][i] else 0) + 0.3 * (1 if I["slope200"][i] == I["slope200"][i] and I["slope200"][i] > 0 else 0) +
                        0.3 * (1 if I["mom"][i] == I["mom"][i] and I["mom"][i] > 0 else 0))
        exp_r = None
        if stats:
            exp_r = stats["avg_r"]
            if p_cal is not None and p_model is not None:
                exp_r = stats["avg_r"] + (p_cal - p_model) * (TP1_R + 1)     # korekta oczekiwanej R o różnicę prawdopodobieństw
            s_edge = 20 * clamp(exp_r / 0.5)
        else:
            s_edge = 6
        near_sup = plan and any(abs(price - x) <= atr for x in plan["supports"][:2])
        s_loc = 10 * ((1 if near_sup else 0.4) if plan else 0)
        score = s_dip + s_stab + s_trend + s_edge + s_loc
        ev_soon = self.upcoming_events(2)
        big = [x for x in ev_soon if x["kind"] in BIG_EVENTS and x["days"] <= 1]
        if big:
            score -= 5
            risks.append(f"W ciągu 1-2 dni: {', '.join(x['label'] for x in big)}. Rynki bywają wtedy gwałtowne; rozważ wejście po publikacji.")
        vix = getattr(self, "vix", None)
        if vix and vix > 30 and cls != "fx":
            score -= 5
            risks.append(f"VIX {vix:.0f}: podwyższony strach na rynku, zwiększ margines bezpieczeństwa.")
        if knife:
            score = min(score, 25)
            flags.append("spadający nóż")
        if not in_dip:
            score = min(score, 45)
        thr = self.params["min_score"][cls]
        if knife:
            status = "spadający nóż"
        elif setup and score >= thr and plan and (stats is None or (exp_r or 0) > 0):
            status = "okazja"
        elif in_dip and (stab >= 1):
            status = "obserwuj"
        elif in_dip:
            status = "za wcześnie (brak stabilizacji)"
        else:
            status = "brak dołka"
        cat = {"key": key, "name": a["name"], "cls": cls, "cls_pl": CLASS_PL[cls], "price": price, "age_s": S["age"], "market": a.get("market"),
               "chg_1d": price / S["c"][i - 1] - 1 if i > 0 else None, "dd60": price / I["hi60"][i] - 1 if I["hi60"][i] == I["hi60"][i] else None,
               "depth_atr": depth if depth == depth else None, "rsi": rsi if rsi == rsi else None, "stab": stab, "score": round(score, 1), "status": status,
               "p_tp1": p_cal, "p_tp1_model": p_model, "exp_r": exp_r, "stats_src": src, "n_analogs": stats["n"] if stats else 0,
               "days_tp1": (stats or {}).get("days_tp1"), "in_dip": in_dip, "spark": [round(x, 8) for x in S["c"][-30:]],
               "plan": {k2: plan[k2] for k2 in ("entry_lo", "entry_hi", "stop", "tp1", "tp2", "stop_pct", "rr1")} if plan else None, "delayed": cls == "stock"}
        if not full:
            return cat
        # --- pełny raport
        if depth == depth:
            reasons.append(f"Cena jest {abs(cat['dd60'] or 0) * 100:.1f}% ({depth:.1f} ATR) poniżej 60-sesyjnego szczytu.")
        if rsi == rsi:
            reasons.append(f"RSI(14) = {rsi:.0f}" + (": wyprzedanie." if rsi <= 40 else ": bez wyprzedania."))
        if I["pctb"][i] == I["pctb"][i]:
            reasons.append(f"Pozycja w pasmach Bollingera: {I['pctb'][i] * 100:.0f}% (poniżej 15% = przy dolnej wstędze).")
        marks = [n for n, f in (("zielona świeca", price > S["o"][i]), ("wyższe minimum niż wczoraj", S["l"][i] > S["l"][i - 1]), ("RSI rośnie", I["rsi"][i] == I["rsi"][i] and I["rsi"][i - 3] == I["rsi"][i - 3] and I["rsi"][i] > I["rsi"][i - 3]), ("zamknięcie powyżej wczorajszego", price > S["c"][i - 1])) if f]
        reasons.append(f"Stabilizacja: {stab} z 4 oznak" + (f" ({', '.join(marks)})." if marks else "."))
        if I["s200"][i] == I["s200"][i]:
            reasons.append(f"Struktura długoterminowa: cena {'powyżej' if price > I['s200'][i] else 'poniżej'} SMA200 o {abs(price / I['s200'][i] - 1) * 100:.1f}%" + (", SMA200 rośnie." if I["slope200"][i] == I["slope200"][i] and I["slope200"][i] > 0 else ", SMA200 spada." if I["slope200"][i] == I["slope200"][i] else "."))
        if knife:
            risks.append("Wzorzec 'spadającego noża': nowe minima bez stabilizacji pod spadającą SMA200. Nie łap, poczekaj na wyższe minimum i zamknięcie powyżej wczorajszego.")
        if stats:
            b = base["p_tp1"] if base else None
            reasons.append(f"Po podobnych dołkach ({src}, n={stats['n']}) cel 1 osiągano w {stats['p_tp1'] * 100:.0f}% przypadków" + (f" (zwykle {b * 100:.0f}%)" if b else "") +
                           f", stop w {stats['p_stop'] * 100:.0f}%, bez rozstrzygnięcia w {stats['p_timeout'] * 100:.0f}% w {H_BARS} sesjach.")
        else:
            risks.append("Za mało podobnych dołków w historii, żeby oszacować prawdopodobieństwo: wynik jest wyłącznie techniczny.")
        # ile czekać
        touch = self.touch_probs(key, (price - plan["entry_lo"]) / atr) if plan else None
        sizing = None
        timing = {"horizon_sessions": H_BARS, "wait_for_entry": touch, "to_tp1": (stats or {}).get("days_tp1"), "to_stop": (stats or {}).get("days_stop"),
                  "time_stop": ((stats or {}).get("days_tp1") or {}).get("p75") or H_BARS}
        ctx = {"events": self.upcoming_events(10), "macro": self.macro(), "levels": {"supports": plan["supports"] if plan else [], "resistances": plan["resistances"] if plan else []},
               "indicators": {"sma20": I["s20"][i], "sma50": I["s50"][i], "sma200": I["s200"][i], "atr": atr, "atr_pct": atr / price * 100 if atr == atr else None,
                              "mom_12_1": I["mom"][i] if I["mom"][i] == I["mom"][i] else None}}
        p = self.params
        adj = [f"Skurczenie prawdopodobieństwa do zrealizowanej skuteczności: " + (f"{realized['wins']}/{realized['n']} trafionych celów w klasie {CLASS_PL[cls]} (k={p['calib_k']})." if realized else f"brak jeszcze rozstrzygniętych okazji w klasie {CLASS_PL[cls]} (używam modelu historycznego).")]
        if abs(p["stop_mult"][cls] - 1) > 1e-6:
            adj.append(f"Szerokość stopu skorygowana ×{p['stop_mult'][cls]:.2f} po obserwacji reakcji rynku (stopy zbyt często wybijane przed ruchem w górę).")
        if p["min_score"][cls] != 65:
            adj.append(f"Próg wyniku dla klasy {CLASS_PL[cls]}: {p['min_score'][cls]} (domyślnie 65), dostosowany do wyników okazji.")
        verdict = {"status": status, "score": cat["score"], "threshold": thr, "headline": {
            "okazja": "Okazja zakupowa wg modelu: dołek ze stabilizacją i dodatnim oczekiwaniem.",
            "obserwuj": "Dołek, ale sygnał niepełny: obserwuj, nie spiesz się z wejściem.",
            "za wcześnie (brak stabilizacji)": "Wyprzedanie bez stabilizacji: poczekaj na wyższe minimum i zamknięcie powyżej wczorajszego.",
            "spadający nóż": "Spadający nóż: nie kupuj, dopóki struktura się nie poprawi.",
            "brak dołka": "Brak dołka: to aktywo nie jest teraz w strefie wyprzedania."}[status]}
        scen = None
        if plan:
            scen = [{"name": "Bazowy", "text": f"Odbicie do celu 1 ({plan['tp1']:.4g}) w typowo {((stats or {}).get('days_tp1') or {}).get('median', '?')} sesji; po osiągnięciu zrealizuj 50%, stop na wejście."},
                    {"name": "Wzrostowy", "text": f"Kontynuacja do zniesienia 61,8% ({plan['tp2']:.4g}) i dalej do szczytu ({plan['tp3']:.4g}); stop podciągaj pod SMA20 lub 2 ATR."},
                    {"name": "Negatywny", "text": f"Zamknięcie poniżej {plan['stop']:.4g} unieważnia układ: strata 1R. Nie uśredniaj."}]
        return {**cat, "closes90": [round(x, 8) for x in S["c"][-90:]], "verdict": verdict, "plan_full": plan, "reasons": reasons, "risks": risks, "timing": timing, "context": ctx, "adjustments": adj, "scenarios": scen,
                "probabilities": {"model": stats, "baseline": base, "calibrated_tp1": p_cal, "source": src, "realized_class": realized},
                "as_of": datetime.fromtimestamp(S["d"][-1], timezone.utc).strftime("%Y-%m-%d"), "disclaimer": "Analiza statystyczna, nie porada inwestycyjna. Notowania akcji bywają opóźnione ok. 15 min."}

    # --- pomocnicze
    def _last_close(self, key):
        r = self._q("SELECT c FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts DESC LIMIT 1", (key,), "fetchone")
        return r[0] if r else None

    def upcoming_events(self, days):
        base = int(self.now()) // DAY * DAY
        out = []
        for kind, lst in self.events.items():
            if kind == "halving":
                continue
            for t in lst:
                if base <= t <= base + days * DAY:
                    out.append({"kind": kind, "label": EVENT_PL.get(kind, kind), "date": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), "days": (t - base) // DAY})
        return sorted(out, key=lambda x: x["days"])

    def macro(self):
        if self.macro_cache is not None:
            return self.macro_cache
        out = []
        for key, label in (("VIX", "VIX"), ("DXY", "Indeks dolara")):
            r = self._q("SELECT c FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts DESC LIMIT 22", (key,), "fetchall")
            if len(r) >= 2:
                out.append({"label": label, "value": r[0][0], "chg_20d": r[0][0] / r[-1][0] - 1})
        self.macro_cache = out
        return out

    def touch_probs(self, key, k_atr):
        """Jak często w historii minimum z N następnych sesji schodziło o co najmniej k_atr ATR poniżej zamknięcia (szansa na wejście ze strefy)."""
        S = self.data[key]["S"]
        c, l, atr = S["c"], S["l"], S["ind"]["atr"]
        n, out = len(c), []
        lo = max(30, n - 750)
        for N in (1, 3, 5, 10):
            hit = tot = 0
            for i in range(lo, n - N):
                if atr[i] == atr[i]:
                    tot += 1
                    hit += min(l[i + 1:i + N + 1]) <= c[i] - k_atr * atr[i]
            out.append({"sessions": N, "prob": hit / tot if tot else None})
        return out

    def scan(self):
        res = []
        for key in self.data:
            try:
                res.append(self.evaluate(key))
            except Exception as e:  # noqa: BLE001 — jedno aktywo nie blokuje listy
                res.append({"key": key, "error": str(e), "status": "błąd", "score": 0, "cls": self.data[key]["a"]["cls"], "name": self.data[key]["a"]["name"]})
        order = {"okazja": 0, "obserwuj": 1, "za wcześnie (brak stabilizacji)": 2, "spadający nóż": 3, "brak dołka": 4, "błąd": 5}
        res.sort(key=lambda x: (order.get(x["status"], 9), -x["score"]))
        return res

    def sizing(self, report, capital, risk_pct):
        plan = report["plan_full"]
        if not plan or capital <= 0 or risk_pct <= 0:
            return None
        risk_amt = capital * risk_pct / 100
        d = plan["stop_dist"]
        qty = risk_amt / d
        value = qty * plan["entry_hi"]
        return {"capital": capital, "risk_pct": risk_pct, "risk_amount": risk_amt, "qty": qty, "position_value": value, "pct_of_capital": value / capital * 100,
                "needs_leverage": value > capital, "loss_at_stop": risk_amt, "gain_tp1": qty * (plan["tp1"] - plan["entry_hi"]), "gain_tp2": qty * (plan["tp2"] - plan["entry_hi"])}

    def report(self, key, capital=0.0, risk_pct=1.0):
        if key not in self.data:
            raise KeyError(key)
        r = self.evaluate(key, full=True)
        r["sizing"] = self.sizing(r, capital, risk_pct)
        return r

    # --- dziennik okazji i uczenie się
    def log_signals(self, scan):
        now = int(self.now())
        n = 0
        with self.lock:
            for it in scan:
                if it.get("status") != "okazja" or not it.get("plan"):
                    continue
                if self._q("SELECT 1 FROM signals WHERE symbol=? AND source='live' AND ts>?", (it["key"], now - 5 * DAY), "fetchone"):
                    continue
                p = it["plan"]
                atr = self.data[it["key"]]["S"]["ind"]["atr"][-1]
                self.con.execute("INSERT INTO signals(ts,symbol,cls,source,entry_lo,entry_hi,stop,tp1,score,p_model,horizon,atr,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (now, it["key"], it["cls"], "live", p["entry_lo"], p["entry_hi"], p["stop"], p["tp1"], it["score"], it["p_tp1"], H_BARS, atr, "open"))
                n += 1
            self.con.commit()
        return n

    def update_signals(self):
        """Rozlicza otwarte okazje na świecach po ich utworzeniu (z bieżącą ceną jako ostatnią świecą) i dopełnia flagę 'recovered' po stopach."""
        changed = 0
        rows = self._q("SELECT id, ts, symbol, entry_hi, stop, tp1, status, fill_ts, fill_price, horizon FROM signals WHERE source='live' AND status IN ('open','filled')", "fetchall")
        for sid, ts, sym, entry_hi, stop, tp1, status, fill_ts, fill_price, horizon in rows:
            if sym not in self.data:
                continue
            new = eval_signal(self.live_series(sym), ts, entry_hi, stop, tp1, horizon, fill_ts, fill_price)
            if new:
                self.con.execute("UPDATE signals SET status=?, fill_ts=COALESCE(?, fill_ts), fill_price=COALESCE(?, fill_price), close_ts=?, close_price=?, outcome=?, r_mult=?, recovered=? WHERE id=?",
                                 (new["status"], new.get("fill_ts"), new.get("fill_price"), new.get("close_ts"), new.get("close_price"), new.get("outcome"), new.get("r"), new.get("recovered"), sid))
                changed += 1
        for sid, sym, tp1, horizon, close_ts in self._q("SELECT id, symbol, tp1, horizon, close_ts FROM signals WHERE source='live' AND outcome='stop' AND recovered IS NULL", "fetchall"):
            if sym in self.data:
                S = self.live_series(sym)
                j = bisect.bisect_left(S["d"], (close_ts or 0) // DAY * DAY)
                rec = recovery(S, j, tp1, horizon)
                if rec is not None:
                    self.con.execute("UPDATE signals SET recovered=? WHERE id=?", (rec, sid))
                    changed += 1
        self.con.commit()
        if changed:
            self.adapt()
        return changed

    def adapt(self):
        """Korekty systemu wynikające z reakcji rynku na poprzednie okazje (osobno dla każdej klasy)."""
        notes = []
        P = json.loads(json.dumps(self.params))
        for cls in CLASS_COST:
            rows = self._q("SELECT outcome, recovered, r_mult FROM signals WHERE source='live' AND cls=? AND outcome IS NOT NULL AND outcome!='missed'", (cls,), "fetchall")
            n = len(rows)
            if n < 8:
                continue
            wins = sum(1 for o, _, _ in rows if o == "tp1")
            stops = [(o, rec) for o, rec, _ in rows if o == "stop"]
            rev = sum(1 for _, rec in stops if rec) / len(stops) if stops else 0
            if len(stops) >= 6 and rev > 0.4:
                P["stop_mult"][cls] = min(1.6, round(P["stop_mult"][cls] * 1.1, 3))
                notes.append(f"{CLASS_PL[cls]}: {rev * 100:.0f}% wybitych stopów kończyło się później celem, więc stop poszerzony do ×{P['stop_mult'][cls]}.")
            if n >= 12 and wins / n < 0.30:
                P["min_score"][cls] = min(80, P["min_score"][cls] + 5)
                notes.append(f"{CLASS_PL[cls]}: skuteczność {wins / n * 100:.0f}% (n={n}); próg wyniku podniesiony do {P['min_score'][cls]}.")
            elif n >= 12 and wins / n > 0.55 and P["min_score"][cls] > 55:
                P["min_score"][cls] -= 2
                notes.append(f"{CLASS_PL[cls]}: skuteczność {wins / n * 100:.0f}% (n={n}); próg wyniku obniżony do {P['min_score'][cls]}.")
        with self.lock:
            self.params = P
            self.con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('opp_params', ?)", (json.dumps({"stop_mult": P["stop_mult"], "min_score": P["min_score"], "calib_k": P["calib_k"], "notes": notes, "ts": int(self.now())}),))
            self.con.commit()
        return notes

    def performance(self):
        out = {}
        for src in ("live", "replay"):
            rows = self._q("SELECT cls, outcome, r_mult, p_model FROM signals WHERE source=? AND outcome IS NOT NULL", (src,), "fetchall")
            by = {}
            for cls, oc, r, p in rows:
                if oc == "missed":
                    by.setdefault(cls, {"missed": 0, "n": 0, "wins": 0, "r": [], "p": []})["missed"] += 1
                    continue
                d = by.setdefault(cls, {"missed": 0, "n": 0, "wins": 0, "r": [], "p": []})
                d["n"] += 1
                d["wins"] += oc == "tp1"
                d["r"].append(r or 0)
                if p is not None:
                    d["p"].append(p)
            out[src] = {CLASS_PL[c]: {"n": v["n"], "win_rate": v["wins"] / v["n"] if v["n"] else None, "avg_r": statistics.fmean(v["r"]) if v["r"] else None,
                                      "avg_p_model": statistics.fmean(v["p"]) if v["p"] else None, "missed": v["missed"]} for c, v in by.items() if c in CLASS_PL}
            open_n = self._q("SELECT COUNT(*) FROM signals WHERE source=? AND outcome IS NULL", (src,), "fetchone")[0]
            out[src]["_open"] = open_n
        r = self._q("SELECT v FROM meta WHERE k='opp_params'", "fetchone")
        out["params"] = json.loads(r[0]) if r else {"stop_mult": self.params["stop_mult"], "min_score": self.params["min_score"], "notes": []}
        return out

    # --- pozycje użytkownika
    def add_position(self, key, entry, stop, tp1=None, tp2=None, qty=None, note=""):
        if key not in self.data:
            raise KeyError(key)
        if not (entry > 0 and 0 < stop < entry):
            raise ValueError("stop musi być dodatni i poniżej ceny wejścia")
        d = entry - stop
        tp1 = tp1 or entry + TP1_R * d
        tp2 = tp2 or entry + 2.5 * d
        if self._q("SELECT COUNT(*) FROM positions WHERE status='open'", "fetchone")[0] >= 50:
            raise ValueError("limit 50 otwartych pozycji")
        with self.lock:
            cur = self.con.execute("INSERT INTO positions(ts,symbol,entry,stop,tp1,tp2,qty,note,status) VALUES(?,?,?,?,?,?,?,?, 'open')", (int(self.now()), key, entry, stop, tp1, tp2, qty, (note or "")[:200]))
            self.con.commit()
        return cur.lastrowid

    def close_position(self, pid):
        with self.lock:
            self.con.execute("UPDATE positions SET status='closed' WHERE id=?", (pid,))
            self.con.commit()

    def positions(self):
        rows = self._q("SELECT id, ts, symbol, entry, stop, tp1, tp2, qty, note FROM positions WHERE status='open' ORDER BY id", "fetchall")
        return [self.advise(*r) for r in rows if r[2] in self.data]

    def advise(self, pid, ts, key, entry, stop, tp1, tp2, qty, note):
        S = self.live_series(key)
        i = len(S["c"]) - 1
        I, price = S["ind"], S["c"][i]
        risk = entry - stop
        r_now = (price - entry) / risk if risk > 0 else 0
        days = max(0, (int(self.now()) - ts) // DAY)
        stats, _, _ = self._stats_for(key)
        t_stop = ((stats or {}).get("days_tp1") or {}).get("p75") or H_BARS
        atr, s20 = I["atr"][i], I["s20"][i]
        actions, new_stop = [], None
        status = "trzymaj"
        if price <= stop:
            status = "STOP"
            actions.append("Cena poniżej stopu: zamknij pozycję zgodnie z planem. Nie uśredniaj w dół.")
        elif price >= tp2:
            status = "CEL 2"
            actions.append("Cel 2 osiągnięty: zamknij większość lub całość; resztę prowadź trailing-stopem.")
            new_stop = max(tp1, price - 2 * atr) if atr == atr else tp1
        elif price >= tp1:
            status = "CEL 1"
            actions.append("Cel 1 osiągnięty: zrealizuj ok. 50% pozycji i przesuń stop na wejście.")
            new_stop = max(entry, (s20 if s20 == s20 else entry) - 0.5 * (atr if atr == atr else 0))
        elif r_now >= 1.0:
            actions.append("Zysk ≥ 1R: przesuń stop na poziom wejścia (pozycja bez ryzyka straty).")
            new_stop = entry
        if days > t_stop and r_now < 0.5 and status == "trzymaj":
            status = "za długo"
            actions.append(f"Pozycja trwa {days} sesji (75% podobnych okazji rozstrzygało się w {t_stop}) bez postępu: rozważ zamknięcie lub zmniejszenie.")
        if r_now <= -0.7 and status == "trzymaj":
            status = "pod presją"
            actions.append("Rynek reaguje negatywnie (strata ≥ 0,7R): trzymaj się stopu, rozważ zmniejszenie połowy pozycji.")
        if I["knife"][i] and status == "trzymaj":
            actions.append("Pojawił się wzorzec spadającego noża: unieważnia pierwotny układ, rozważ wcześniejsze wyjście.")
        if not actions:
            actions.append("Układ bez zmian: trzymaj plan, stop " + f"{stop:.4g}.")
        return {"id": pid, "key": key, "name": self.data[key]["a"]["name"], "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "qty": qty, "note": note, "price": price,
                "r_now": r_now, "pnl_pct": (price / entry - 1) * 100, "pnl_amount": (price - entry) * qty if qty else None, "days": days, "status": status, "actions": actions, "suggested_stop": new_stop,
                "rsi": I["rsi"][i] if I["rsi"][i] == I["rsi"][i] else None}


def eval_signal(S, ts, entry_hi, stop, tp1, horizon, fill_ts, fill_price):
    """Rozlicza jedną okazję na świecach po jej utworzeniu. Zwraca nowy stan (słownik) albo None, gdy nic się nie zmieniło.
    Wejście: cena dotyka strefy (limit w entry_hi) w ciągu 5 sesji; stop i cel liczone od świecy wejścia (remis w jednej świecy = stop);
    po 'horizon' sesjach od wejścia pozycja jest zamykana po cenie zamknięcia (timeout)."""
    d, o, h, l, c = S["d"], S["o"], S["h"], S["l"], S["c"]
    n = len(d)
    first = bisect.bisect_left(d, fill_ts // DAY * DAY) if fill_ts else bisect.bisect_right(d, ts // DAY * DAY)
    fp, filled_at, j_fill, waited = fill_price, fill_ts, (first if fill_ts else None), 0
    for j in range(first, n):
        if fp is None:
            waited += 1
            if l[j] <= entry_hi:
                fp, filled_at, j_fill = min(entry_hi, o[j]), d[j], j
                if stop >= fp:
                    return {"status": "closed", "outcome": "missed", "close_ts": d[j]}
            elif waited >= 5:
                return {"status": "closed", "outcome": "missed", "close_ts": d[j]}
            else:
                continue
        risk = fp - stop
        if risk <= 0:
            return {"status": "closed", "outcome": "missed", "close_ts": d[j]}
        if l[j] <= stop:
            return {"status": "closed", "fill_ts": filled_at, "fill_price": fp, "close_ts": d[j], "close_price": stop, "outcome": "stop", "r": (stop - fp) / risk,
                    "recovered": recovery(S, j, tp1, horizon)}
        if h[j] >= tp1:
            return {"status": "closed", "fill_ts": filled_at, "fill_price": fp, "close_ts": d[j], "close_price": tp1, "outcome": "tp1", "r": (tp1 - fp) / risk}
        if j - j_fill >= horizon:
            return {"status": "closed", "fill_ts": filled_at, "fill_price": fp, "close_ts": d[j], "close_price": c[j], "outcome": "timeout", "r": (c[j] - fp) / risk}
    if fp is not None and not fill_ts:
        return {"status": "filled", "fill_ts": filled_at, "fill_price": fp}
    return None


def recovery(S, j_stop, tp1, horizon):
    """Czy po wybiciu stopu cena i tak sięgnęła celu 1 (1), nie sięgnęła mimo pełnego horyzontu (0), albo jeszcze nie wiadomo (None)."""
    h = S["h"]
    end = j_stop + horizon
    if any(h[k] >= tp1 for k in range(j_stop + 1, min(len(h), end + 1))):
        return 1
    return 0 if end <= len(h) - 1 else None


# ----------------------------------------------------------------------------
# Symulacja na historii (walk-forward): startowa skuteczność systemu
# ----------------------------------------------------------------------------
def replay(engine, step=3, years=3):
    """Dla każdego aktywa i co 'step' sesji liczy ocenę wyłącznie z danych do tej sesji (statystyki analogów tylko z już rozstrzygniętych wyników)
    i zapisuje wirtualne okazje (source='replay') z wynikami barier."""
    con, n_added = engine.con, 0
    con.execute("DELETE FROM signals WHERE source='replay'")
    for key, e in engine.data.items():
        S, cls = e["S"], e["a"]["cls"]
        I = S["ind"]
        mult, cost = engine.params["stop_mult"][cls], CLASS_COST[cls]
        out = e["out"]
        n = len(S["c"])
        start = max(260, n - years * 252)
        for t in range(start, n - 2, step):
            if not I["setup"][t]:
                continue
            st = stats_from(out, upto=t)["setup"]
            if not st or st["n"] < 12:
                continue
            atr = I["atr"][t]
            d = stop_distance(S["c"][t], atr, I["low10"][t], mult)
            entry = S["c"][t]
            exp_r = st["avg_r"]
            depth = I["depth"][t]
            score = 30 * (0.6 * clamp(depth / 6) + 0.4) + 15 * I["stab"][t] / 4 + 20 * (0.4 * (S["c"][t] > I["s200"][t]) + 0.3 * (I["slope200"][t] > 0) + 0.3 * (I["mom"][t] == I["mom"][t] and I["mom"][t] > 0)) + 20 * clamp(exp_r / 0.5) + 6
            if score < engine.params["min_score"][cls] or exp_r <= 0:
                continue
            res = run_barrier(S["h"], S["l"], S["c"], t, entry, entry - d, entry + TP1_R * d)
            if res is None:
                continue
            kind, days, r = res
            rec = None
            if kind == "stop":
                tp = entry + TP1_R * d
                rec = int(any(S["h"][k] >= tp for k in range(t + days + 1, min(n, t + days + 1 + H_BARS))))
            con.execute("INSERT INTO signals(ts,symbol,cls,source,entry_lo,entry_hi,stop,tp1,score,p_model,horizon,atr,status,fill_ts,fill_price,close_ts,close_price,outcome,r_mult,recovered) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (S["d"][t], key, cls, "replay", entry, entry, entry - d, entry + TP1_R * d, score, st["p_tp1"], H_BARS, atr, "closed", S["d"][t], entry, S["d"][min(n - 1, t + days)], None, kind, r - cost * entry / d, rec))
            n_added += 1
    con.commit()
    return n_added


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["scan", "report", "replay"])
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--capital", type=float, default=0.0)
    ap.add_argument("--risk", type=float, default=1.0)
    a = ap.parse_args()
    eng = Engine(a.db)
    eng.load()
    if a.cmd == "scan":
        for it in eng.scan()[:25]:
            print(f"{it['status']:30} {it['key']:10} {it['name'][:22]:22} wynik {it['score']:5.1f}  cena {it.get('price', 0):<12.5g} RSI {it.get('rsi') or 0:4.0f}")
    elif a.cmd == "report":
        print(json.dumps(eng.report(a.symbol, a.capital, a.risk), ensure_ascii=False, indent=1, default=float))
    else:
        print("zapisano wirtualnych okazji:", replay(eng))
        print(json.dumps(eng.performance()["replay"], ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
