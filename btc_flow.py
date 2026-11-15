#!/usr/bin/env python3
"""Rynek na żywo: przepływ zleceń i pozycjonowanie na kontraktach (publiczne API OKX, bez klucza), odświeżane co 10 minut.

Dla każdego aktywa, w rozdzielczości godzinowej:
  wolumen agresywny   jaka część zleceń rynkowych z ostatnich 4 h to kupno (kto „goni cenę”)
  konta long/short    ilu uczestników ma pozycję długą względem krótkiej (pozycjonowanie tłumu)
  premia kontraktu    cena kontraktu względem indeksu spot, w punktach bazowych (pb)
  ruch ceny           zmiana z 4 h i 24 h
  informacyjnie       open interest i jego zmiana z 24 h, pozycje największych graczy, funding
Każda wielkość jest porównywana z własnym poziomem z ostatnich 7 dni (z-score, obcięty do ±3). Wskaźnik łączny:
    wskaźnik = średnia( wolumen agresywny, konta long/short, −premia, −ruch 4 h )
Dodatni = przewaga czynników wzrostowych, ujemny = spadkowych. |wskaźnik| >= 1 uznajemy za wyraźne wskazanie.

Na ile można temu ufać (stan badań z 15.11.2026):
  - 53 dni danych godzinowych, 10 aktywów: wskazania >= 1 dawały po 12 h +0,06 do +0,22R po kosztach we wszystkich 24 sprawdzonych
    konfiguracjach i w obu połowach okresu, ale bez istotności statystycznej (t <= 1,8), a zysk pochodził głównie z pozycji krótkich
    na spadającym rynku;
  - 2 lata danych godzinowych (BTC, ETH, SOL, XRP; 72 tys. obserwacji): dwa składniki, które mają długą historię (premia i ruch z 4 h), NIE wykazały przewagi
    (premia: +1,5 pb na odchylenie po 4 h, t 1,3; ruch z 4 h: zero), a strategia zbudowana z nich samych traciła po kosztach 14 pb na transakcję;
  - wolumen agresywny i konta long/short mają tylko 60 dni historii godzinowej, a na danych dziennych znak „kont long/short” wychodzi odwrotnie.
Wniosek: wskaźnik jest EKSPERYMENTEM bez potwierdzonej skuteczności, a wynik z 53 dni był najpewniej cechą jednego okresu. Dane w tabeli są
kontekstem. Moduł sam liczy trafność wskazań (osobno dla okresu po zbudowaniu wskaźnika, czyli uczciwie poza próbą), a scalper prowadzi
strategię z tego wskaźnika tylko w cieniu, dopóki nie przejdzie bramki.

Pliki: flow_hist.json (historia godzinowa, 45 dni), flow.json (stan dla panelu, /api/flow).
Użycie:  python btc_flow.py [--symbols BTCUSDT,ETHUSDT]     jedno odświeżenie i wydruk (diagnostyka)
"""
import calendar
import json
import math
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_net  # noqa: E402

OKX = "https://www.okx.com/api/v5/"
HOUR = 3600
Z_WIN = 168                         # z-score z 7 dni
KEEP_H = 45 * 24                    # tyle historii trzymamy
SEED_H = 20 * 24                    # tyle pobieramy przy pierwszym uruchomieniu
THETA = 1.0                         # próg wyraźnego wskazania
HOLD_H = 12                         # horyzont, na którym oceniamy wskazanie
STALE_S = int(2.5 * HOUR)           # starsze dane nie są wskazaniem
OOS_START = calendar.timegm((2026, 11, 16, 0, 0, 0))      # wskazania od tej chwili są poza próbą badania
HIST, OUT = "flow_hist.json", "flow.json"
C, MARK, INDEX, BUY, SELL, LS, LSP, OI = range(8)          # układ wiersza historii
RUBIK_GAP, MKT_GAP = 0.45, 0.12                           # limity OKX: statystyki 5 zapytań / 2 s, świece 20 / 2 s
PARTS = (("tk", "Wolumen agresywny (4 h)", 1), ("ls", "Konta long/short", 1), ("prem", "Premia kontraktu", -1), ("ret4", "Ruch ceny z 4 h", -1))


def inst(sym):
    """BTCUSDT -> (BTC-USDT-SWAP, BTC-USDT)."""
    base = sym[:-4]
    return f"{base}-USDT-SWAP", f"{base}-USDT"


class Pacer:
    """Odstępy między zapytaniami (limity giełdy)."""

    def __init__(self, sleep=time.sleep, clock=time.time):
        self.sleep, self.clock, self.last = sleep, clock, 0.0

    def wait(self, gap):
        w = self.last + gap - self.clock()
        if w > 0:
            self.sleep(w)
        self.last = self.clock()


def _okx(get, path, params, pace, gap):
    pace.wait(gap)
    d = get(OKX + path + "?" + "&".join(f"{k}={v}" for k, v in params.items()), timeout=12, retries=1)
    if not isinstance(d, dict) or str(d.get("code")) != "0":
        raise RuntimeError(f"OKX {path.split('/')[-1]}: {(d or {}).get('msg') or (d or {}).get('code') or 'zła odpowiedź'}")
    return d.get("data") or []


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# kind -> (ścieżka świeża, ścieżka historii, True = świece (stronicowanie „after”), kolumny źródła -> kolumny wiersza)
KINDS = {
    "px": ("market/candles", "market/history-candles", True, {4: C}),
    "mk": ("market/mark-price-candles", "market/history-mark-price-candles", True, {4: MARK}),
    "ix": ("market/index-candles", "market/history-index-candles", True, {4: INDEX}),
    "tk": ("rubik/stat/taker-volume-contract", None, False, {1: SELL, 2: BUY}),
    "ls": ("rubik/stat/contracts/long-short-account-ratio-contract", None, False, {1: LS}),
    "lsp": ("rubik/stat/contracts/long-short-position-ratio-contract-top-trader", None, False, {1: LSP}),
    "oi": ("rubik/stat/contracts/open-interest-history", None, False, {2: OI}),
}


def fetch_kind(get, kind, sym, since_s, now_s, pace, max_pages=8):
    """{ts_s: {kolumna: wartość}} dla pełnych godzin z przedziału [since_s, now_s - 1 h]. Wiersz o czasie t opisuje godzinę [t, t + 1 h)."""
    fresh, hist, candles, cols = KINDS[kind]
    swap, spot = inst(sym)
    out, cursor = {}, None
    for page in range(max_pages):
        if candles:
            p = {"instId": spot if kind == "ix" else swap, "bar": "1H", "limit": 100}
            if cursor:
                p["after"] = cursor
            rows = _okx(get, hist if page else fresh, p, pace, MKT_GAP)
        else:
            p = {"instId": swap, "period": "1H", "limit": 100}
            if cursor:
                p["end"] = cursor
            rows = _okx(get, fresh, p, pace, RUBIK_GAP)
        if not rows:
            break
        oldest = None
        for r in rows:
            try:
                t = int(r[0]) // 1000
            except (TypeError, ValueError, IndexError):
                continue
            oldest = t if oldest is None else min(oldest, t)
            if t + HOUR > now_s or t < since_s or t % HOUR:
                continue                                    # godzina jeszcze trwa albo poza zakresem
            vals = {dst: _f(r[src]) for src, dst in cols.items() if src < len(r)}
            if vals:
                out[t] = vals
        if oldest is None or oldest <= since_s or len(rows) < 100:
            break
        cursor = oldest * 1000
    return out


def refresh_symbol(get, rows, sym, now_s, pace):
    """Uzupełnia historię aktywa (rows: {ts_s: [8 wartości]}) o brakujące godziny. Zwraca listę błędów (teksty)."""
    newest = max(rows) if rows else None
    since = (newest - 3 * HOUR) if newest and now_s - newest < SEED_H * HOUR else now_s - SEED_H * HOUR
    errs = []
    for kind in KINDS:
        try:
            for t, vals in fetch_kind(get, kind, sym, since, now_s, pace).items():
                row = rows.setdefault(t, [None] * 8)
                for col, v in vals.items():
                    if v is not None:
                        row[col] = v
        except Exception as e:  # noqa: BLE001 — jedno źródło nie psuje reszty
            errs.append(f"{sym} {kind}: {str(e)[:70]}")
    lo = now_s - KEEP_H * HOUR
    for t in [t for t in rows if t < lo]:
        del rows[t]
    return errs


def funding_now(get, sym, pace):
    try:
        d = _okx(get, "public/funding-rate", {"instId": inst(sym)[0]}, pace, MKT_GAP)
        return _f(d[0].get("fundingRate")) * 100 if d and _f(d[0].get("fundingRate")) is not None else None
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------- cechy i wskaźnik (czysta logika)
def _z(vals, i, n=Z_WIN):
    """z-score wartości i względem okna wstecznego n (bez danych z przyszłości), obcięty do ±3."""
    if vals[i] is None:
        return None
    w = [v for v in vals[max(0, i - n + 1):i + 1] if v is not None]
    if len(w) < n // 2:
        return None
    m = sum(w) / len(w)
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
    return max(-3.0, min(3.0, (vals[i] - m) / sd)) if sd > 0 else None


def series(rows):
    """Historia aktywa -> lista godzin na ciągłej siatce: {t (czas dostępności danych), c, score, z:{}, raw:{}}."""
    if not rows:
        return []
    t0, t1 = min(rows), max(rows)
    grid = [rows.get(t) or [None] * 8 for t in range(t0, t1 + HOUR, HOUR)]
    n = len(grid)
    c = [g[C] for g in grid]

    def chg(v, i, k):
        return v[i] / v[i - k] - 1 if i >= k and v[i] and v[i - k] else None

    def tkr(i, k=4):
        if i < k - 1:
            return None
        w = grid[i - k + 1:i + 1]
        if any(g[BUY] is None or g[SELL] is None for g in w):
            return None
        b, s = sum(g[BUY] for g in w), sum(g[SELL] for g in w)
        return b / (b + s) - 0.5 if b + s > 0 else None
    tk = [tkr(i) for i in range(n)]
    ls = [math.log(g[LS]) if g[LS] and g[LS] > 0 else None for g in grid]
    prem = [(g[MARK] / g[INDEX] - 1) * 1e4 if g[MARK] and g[INDEX] else None for g in grid]
    ret4 = [chg(c, i, 4) for i in range(n)]
    src = {"tk": tk, "ls": ls, "prem": prem, "ret4": ret4}
    out = []
    for i in range(n):
        z = {k: _z(v, i) for k, v in src.items()}
        votes = [sg * z[k] for k, _, sg in PARTS if z[k] is not None]
        g = grid[i]
        out.append({"t": t0 + i * HOUR + HOUR, "c": c[i], "score": round(sum(votes) / len(votes), 3) if len(votes) == len(PARTS) else None, "z": z,
                    "raw": {"tk_buy_pct": (tk[i] + 0.5) * 100 if tk[i] is not None else None, "long_pct": g[LS] / (1 + g[LS]) * 100 if g[LS] else None, "prem_bp": prem[i],
                            "ret4_pct": ret4[i] * 100 if ret4[i] is not None else None, "ret24_pct": (chg(c, i, 24) or 0) * 100 if chg(c, i, 24) is not None else None,
                            "oi_chg24_pct": (g[OI] / grid[i - 24][OI] - 1) * 100 if i >= 24 and g[OI] and grid[i - 24][OI] else None,
                            "top_long_pct": g[LSP] / (1 + g[LSP]) * 100 if g[LSP] else None}})
    return out


def accuracy(ser, theta=THETA, hold=HOLD_H, since=None):
    """Trafność wyraźnych wskazań: czy po `hold` godzinach cena poszła w ich kierunku. Wskazania nie nakładają się (jak pozycje)."""
    n = hits = 0
    total = 0.0
    i = 0
    while i + hold < len(ser):
        x = ser[i]
        if x["score"] is None or abs(x["score"]) < theta or not x["c"] or not ser[i + hold]["c"] or (since and x["t"] < since):
            i += 1
            continue
        mv = (ser[i + hold]["c"] / x["c"] - 1) * (1 if x["score"] > 0 else -1) * 1e4
        n, hits, total = n + 1, hits + (mv > 0), total + mv
        i += hold
    return {"n": n, "hit_pct": round(hits / n * 100, 1) if n else None, "avg_bp": round(total / n, 1) if n else None}


def _merge(stats):
    n = sum(s["n"] for s in stats)
    if not n:
        return {"n": 0, "hit_pct": None, "avg_bp": None}
    return {"n": n, "hit_pct": round(sum(s["hit_pct"] * s["n"] for s in stats if s["n"]) / n, 1), "avg_bp": round(sum(s["avg_bp"] * s["n"] for s in stats if s["n"]) / n, 1)}


def describe(sym, ser, now_s, funding=None):
    """Stan aktywa dla panelu: wskaźnik, składniki z opisem, dane informacyjne."""
    last = ser[-1] if ser else None
    if not last:
        return {"score": None, "lean": "brak danych", "stale": True, "parts": [], "info": {}}
    stale = now_s - last["t"] > STALE_S
    s, raw, z = last["score"], last["raw"], last["z"]
    units = {"tk": (raw["tk_buy_pct"], "% kupna"), "ls": (raw["long_pct"], "% kont z pozycją długą"), "prem": (raw["prem_bp"], "pb"), "ret4": (raw["ret4_pct"], "%")}
    parts = [{"key": k, "name": name, "value": None if units[k][0] is None else round(units[k][0], 2), "unit": units[k][1], "z": None if z[k] is None else round(z[k], 2),
              "vote": None if z[k] is None else round(sg * z[k], 2)} for k, name, sg in PARTS]
    lean = "brak danych" if s is None or stale else "wzrostowe" if s >= THETA else "spadkowe" if s <= -THETA else "lekko wzrostowe" if s >= THETA / 2 else "lekko spadkowe" if s <= -THETA / 2 else "neutralne"
    return {"t": last["t"], "stale": stale, "score": s, "lean": lean, "strong": bool(s is not None and not stale and abs(s) >= THETA), "price": last["c"], "parts": parts,
            "info": {"ret24_pct": raw["ret24_pct"], "oi_chg24_pct": raw["oi_chg24_pct"], "top_long_pct": raw["top_long_pct"], "funding_pct": funding}}


# ----------------------------------------------------------------- pliki
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, obj):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(path + ".tmp", path)


def load_hist(d):
    raw = _read(os.path.join(d, HIST), {}).get("symbols") or {}
    out = {}
    for sym, rows in raw.items():
        try:
            out[sym] = {int(t): (list(v) + [None] * 8)[:8] for t, v in rows.items()}
        except (TypeError, ValueError):
            continue
    return out


def save_hist(d, hist):
    _write(os.path.join(d, HIST), {"v": 1, "symbols": {s: {str(t): v for t, v in sorted(r.items())} for s, r in hist.items()}})


def read_out(d):
    return _read(os.path.join(d, OUT), {"ts": 0, "symbols": {}})


class Flow:
    """Stan danych o przepływie. step() odświeża wszystko; lean() podaje wskaźnik aktywa na dany moment."""

    def __init__(self, d, symbols, get=None, pace=None):
        self.d, self.symbols, self.get, self.pace = d, list(symbols), get or btc_net.get, pace or Pacer()
        self.hist = {s: r for s, r in load_hist(d).items() if s in self.symbols}
        self.ser = {s: series(r) for s, r in self.hist.items()}
        self.out, self.lock = read_out(d), threading.Lock()

    def step(self, now_s=None):
        now_s = int(now_s if now_s is not None else time.time())
        errs, fund = [], {}
        for sym in self.symbols:
            rows = self.hist.setdefault(sym, {})
            errs += refresh_symbol(self.get, rows, sym, now_s, self.pace)
            fund[sym] = funding_now(self.get, sym, self.pace)
        ser = {s: series(self.hist[s]) for s in self.symbols}
        syms = {s: describe(s, ser[s], now_s, fund.get(s)) for s in self.symbols}
        acc_all = [accuracy(v) for v in ser.values()]
        acc_oos = [accuracy(v, since=OOS_START) for v in ser.values()]
        out = {"ts": now_s, "source": "OKX (kontrakty USDT)", "theta": THETA, "hold_h": HOLD_H, "oos_start": OOS_START, "symbols": syms,
               "accuracy": {"all": _merge(acc_all), "oos": _merge(acc_oos), "days": round(max((len(v) for v in ser.values()), default=0) / 24, 1),
                            "by_symbol": {s: a for s, a in zip(ser, acc_all)}}, "errors": errs[:10]}
        with self.lock:
            self.ser, self.out = ser, out
        save_hist(self.d, self.hist)
        _write(os.path.join(self.d, OUT), out)
        return out

    def lean(self, sym, at_s):
        """Wskaźnik aktywa znany w chwili at_s (ostatnia pełna godzina), albo None gdy brak lub dane za stare."""
        with self.lock:
            ser = self.ser.get(sym) or []
        for x in reversed(ser[-6:]):
            if x["t"] <= at_s:
                return x["score"] if at_s - x["t"] <= STALE_S else None
        return None


class Worker(threading.Thread):
    """Odświeżanie w tle (domyślnie co 10 minut), żeby pobieranie danych nie opóźniało decyzji scalpera."""

    def __init__(self, flow, every=600, log=print):
        super().__init__(daemon=True)
        self.flow, self.every, self.log = flow, every, log

    def run(self):
        while True:
            t0 = time.time()
            try:
                out = self.flow.step()
                if out["errors"]:
                    self.log("przepływ: " + "; ".join(out["errors"][:3]))
            except Exception as e:  # noqa: BLE001
                self.log(f"przepływ: {type(e).__name__}: {str(e)[:120]}")
            time.sleep(max(30.0, self.every - (time.time() - t0)))


def main(argv):
    syms = (argv[argv.index("--symbols") + 1] if "--symbols" in argv else "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT").split(",")
    out = Flow(os.environ.get("BTC_BOT_DIR", HERE), syms).step()
    for s, v in out["symbols"].items():
        print(f"{s:9} wskaźnik {v['score'] if v['score'] is not None else 'n/d':>6}  {v['lean']:16} " + "  ".join(f"{p['key']} {p['value']}{'' if p['z'] is None else f' (z {p[chr(122)]:+.1f})'}" for p in v["parts"]))
    a = out["accuracy"]
    print(f"trafność wskazań (|wskaźnik| >= {THETA}, po {HOLD_H} h): wszystkie {a['all']}, po zbudowaniu wskaźnika {a['oos']}; błędy: {out['errors'] or 'brak'}")


if __name__ == "__main__":
    main(sys.argv)
