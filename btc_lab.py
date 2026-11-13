#!/usr/bin/env python3
"""Laboratorium wariantów: testowanie i dopracowywanie algorytmu w czasie działania systemu.

Pętla działa codziennie razem z nauką (btc_learn), bez Twojego udziału aż do ostatniego kroku:
  1. DIAGNOZA   dla każdej rodziny sygnałów sprawdzamy na ostatnich 2 latach, czy jej ocena naprawdę poprzedza ruch ceny
                (korelacja rang z wynikiem 10 dni w przód, liczona w czasie dla każdego aktywa; istotność grupowana po blokach czasu).
                Rodzina działająca odwrotnie, niż zakłada algorytm, jest zgłaszana, a system sam tworzy pretendenta,
                w którym ta rodzina jest odwrócona.
  2. HISTORIA   każdy wariant przechodzi ten sam test wsteczny co mistrz (ten sam silnik i koszty).
  3. CIEŃ       każdy wariant prowadzi dziennik propozycji na żywo od dnia rejestracji (wyniki poza próbą; btc_shadow).
  4. PORÓWNANIE pretendent jest porównywany z mistrzem na wynikach z cienia. Jednostką jest tydzień (suma R w tygodniu),
                żeby transakcje na skorelowanych aktywach nie zawyżały istotności; próg jest ostry, bo sprawdzamy często.
  5. AWANS      gdy pretendent wygrywa, w panelu pojawia się wniosek. Zmianę zatwierdzasz kliknięciem: system nigdy sam
                nie podmienia algorytmu handlującego Twoimi pieniędzmi. Gdy mistrz traci na żywo, dostajesz alarm.
  6. WYKONANIE  rzeczywiste transakcje bota są zestawiane z tym, co dla tych samych planów policzył symulator. Różnica
                (poślizg, niewypełnienia, błędy) pokazuje, czy testom można ufać.
Laboratorium nie zmienia dźwigni, ryzyka, trybu ani kluczy. Wynik: lab.json (serwuje /api/lab).
"""
import copy
import hashlib
import json
import math
import os
import re
import statistics
import time

import btc_analysis as ba
import btc_botcfg as bb
import btc_decision as dc
import btc_edge as be

DAY_MS = ba.DAY_MS
WEEK_MS = 7 * DAY_MS
VERSION = 1
LAB_FILE = "lab.json"                 # wynik dla panelu
HIST_FILE = "lab_hist.json"           # pamięć podręczna testu historycznego wariantów
CUSTOM_FILE = "lab_variants.json"     # warianty utworzone automatycznie przez diagnozę
LEDGER = "bot_trades.jsonl"           # zamknięte transakcje bota (zapisuje trader)
ID_RE = re.compile(r"^[a-z][a-z0-9]{0,11}$")

CHAMPION_DEFAULT = "v2"
MIN_FWD_TRADES = 30                   # tyle transakcji z cienia potrzeba, żeby w ogóle oceniać wariant
MIN_FWD_WEEKS = 12
ALPHA = 0.025                         # próg istotności awansu (dzielony przez liczbę pretendentów)
MIN_MEAN_R = 0.05
HIST_EVERY_S = 6 * 86400              # test historyczny odświeżamy raz w tygodniu i po każdej zmianie wariantów
IC_H = 10                             # horyzont diagnozy sygnałów (dni)
IC_BLOCK_DAYS = 60                    # blok czasu = jedna obserwacja do testu istotności
IC_WINDOW_DAYS = 730
IC_T_FLIP = 2.5                       # tak silna sprzeczność w całej historii...
IC_T_RECENT = 1.0                     # ...potwierdzona tym samym znakiem w ostatnich 2 latach tworzy pretendenta
MAX_AUTO = 2


def _weights(**kw):
    w = copy.deepcopy(dc.WEIGHTS)
    for reg in w:
        w[reg].update(kw)
    return w


BUILTIN = {
    "v1": {"name": "Zespół v1", "origin": "wbudowany", "params": {},
           "desc": "Pierwotny zespół sygnałów: aktywo mocniejsze od BTC w ostatnich 30 dniach dostaje plus (siła względna zgodna z trendem)."},
    "v2": {"name": "Zespół v2", "origin": "wbudowany", "params": {"fam_sign": {"sila": -1}},
           "desc": "Jedna zmiana względem v1: siła względem BTC liczona kontrariańsko. Aktywo, które przez 30 dni było słabsze od BTC, dostaje plus (nadrabianie), "
                   "a mocniejsze minus. W danych 2018–2026 ta rodzina działała odwrotnie niż w v1 w każdym badanym okresie."},
    "v1n": {"name": "Zespół bez siły względnej", "origin": "wbudowany", "params": {"weights": _weights(sila=0.0)},
            "desc": "Kontrola: rodzina „siła względem BTC” wyłączona. Pokazuje, czy liczy się samo usunięcie błędnego sygnału, czy jego odwrócenie."},
}


# ----------------------------------------------------------------- rejestr wariantów
def variants(d=None):
    """Wbudowane warianty + utworzone automatycznie (lab_variants.json). Wadliwe wpisy są pomijane."""
    out = {k: dict(v, id=k) for k, v in BUILTIN.items()}
    if d:
        for k, v in (bb._read_json(os.path.join(d, CUSTOM_FILE)).get("variants") or {}).items():
            if k in out or not ID_RE.match(str(k)) or not isinstance(v, dict):
                continue
            try:
                dc.make_params(v.get("params") or {})
            except (ValueError, TypeError):
                continue
            out[k] = {"id": k, "name": str(v.get("name") or k)[:60], "desc": str(v.get("desc") or "")[:400], "params": v.get("params") or {},
                      "origin": "automatyczny", "created": v.get("created")}
    return out


def champion(d):
    """Wariant zespołu sygnałów, którym handluje bot (ustawienie ens_variant z panelu; domyślnie CHAMPION_DEFAULT)."""
    vid = bb.read_settings(d).get("ens_variant") if d else None
    return vid if vid in variants(d) else CHAMPION_DEFAULT


def engine(vid, symbol, btc=None, events=None, d=None, overlay=None, explain=False, reg=None):
    v = (reg or variants(d)).get(vid) or dict(BUILTIN[CHAMPION_DEFAULT], id=CHAMPION_DEFAULT)
    return dc.Engine(symbol=symbol, btc_candles=btc, events_s=events, overlay=overlay, params=v["params"], variant=v["id"], explain=explain)


def skey(vid):
    """Klucz strategii w dzienniku cienia: v1 zachowuje dawną nazwę 'ensemble' (ciągłość danych)."""
    return "ensemble" if vid == "v1" else vid


# ----------------------------------------------------------------- statystyka
def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, dd = 1.0, 1.0 - qab * x / qap
    dd = 1.0 / (dd if abs(dd) > 1e-30 else 1e-30)
    h = dd
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        dd = 1.0 + aa * dd
        dd = 1.0 / (dd if abs(dd) > 1e-30 else 1e-30)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-30 else 1e-30
        h *= dd * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        dd = 1.0 + aa * dd
        dd = 1.0 / (dd if abs(dd) > 1e-30 else 1e-30)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-30 else 1e-30
        de = dd * c
        h *= de
        if abs(de - 1.0) < 1e-12:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    return bt * _betacf(a, b, x) / a if x < (a + 1.0) / (a + b + 2.0) else 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_sf(t, df):
    """P(T > t) dla rozkładu t-Studenta (przy małej liczbie tygodni rozkład normalny zawyżałby istotność)."""
    if df <= 0:
        return 0.5
    p = 0.5 * _betai(df / 2.0, 0.5, df / (df + t * t))
    return p if t > 0 else 1.0 - p


def t_test(xs):
    """Jednostronny test średniej > 0: (średnia, t, p, n). Za mało danych albo brak zmienności: p = 1 (albo 0,5 przy samych zerach)."""
    n = len(xs)
    if n < 3:
        return (sum(xs) / n if n else 0.0), 0.0, 1.0, n
    m, sd = sum(xs) / n, statistics.stdev(xs)
    if sd <= 0:
        return m, 0.0, (0.0 if m > 0 else 1.0), n
    t = m / (sd / math.sqrt(n))
    return m, t, t_sf(t, n - 1), n


def week_sums(trades, lo_ms=0):
    """Suma R w tygodniu zamknięcia: {numer tygodnia: suma}. trades: [(closed_ms, r, side, sym)]."""
    out = {}
    for t in trades:
        if t[0] >= lo_ms:
            out[t[0] // WEEK_MS] = out.get(t[0] // WEEK_MS, 0.0) + t[1]
    return out


def week_test(trades, lo_ms=0):
    m, t, p, n = t_test(list(week_sums(trades, lo_ms).values()))
    return {"weeks": n, "mean_week": round(m, 4), "t": round(t, 2), "p": round(p, 4)}


def compare(tr_a, tr_b, lo_ms=0):
    """Czy wariant A daje więcej R niż B: różnica sum tygodniowych na wspólnych tygodniach (A − B), test jednostronny."""
    a, b = week_sums(tr_a, lo_ms), week_sums(tr_b, lo_ms)
    keys = sorted(set(a) | set(b))
    m, t, p, n = t_test([a.get(k, 0.0) - b.get(k, 0.0) for k in keys])
    return {"weeks": n, "diff_week": round(m, 4), "t": round(t, 2), "p": round(p, 4), "total_a": round(sum(a.values()), 2), "total_b": round(sum(b.values()), 2)}


def summarize(trades, now_ms):
    rs = [t[1] for t in trades]
    s = be.summarize(rs) if rs else {"n": 0, "mean_r": 0.0, "win_rate": 0.0, "p": 1.0, "lb": None}
    wk = week_test(trades)
    out = {"n": s["n"], "mean_r": s["mean_r"], "win_rate": s["win_rate"], "total_r": round(sum(rs), 2), "p": s["p"], "weeks": wk["weeks"], "t_week": wk["t"], "p_week": wk["p"]}
    for name, days in (("y1", 365), ("y3", 1095)):
        part = [t[1] for t in trades if t[0] >= now_ms - days * DAY_MS]
        out[name] = {"n": len(part), "mean_r": round(sum(part) / len(part), 4) if part else None, "total_r": round(sum(part), 2)}
    out["sides"] = {sd: (lambda q: {"n": len(q), "mean_r": round(sum(q) / len(q), 4) if q else None})([t[1] for t in trades if t[2] == sd]) for sd in ("long", "short")}
    by_y = {}
    for t in trades:
        by_y.setdefault(time.gmtime(t[0] / 1000).tm_year, []).append(t[1])
    out["years"] = [[y, len(v), round(sum(v) / len(v), 3), round(sum(v), 2)] for y, v in sorted(by_y.items())]
    by_s = {}
    for t in trades:
        by_s[t[3]] = by_s.get(t[3], 0.0) + t[1]
    out["symbols"], out["symbols_pos"] = len(by_s), sum(1 for v in by_s.values() if v > 0)
    return out


# ----------------------------------------------------------------- test historyczny wariantów
def signature(reg, symbols):
    blob = json.dumps([VERSION, {k: v["params"] for k, v in sorted(reg.items())}, sorted(symbols), ba.PLAN_FEE_PCT, ba.PLAN_FUNDING_PCT_DAY, ba.PLAN_STOP_SLIP_PCT,
                       ba.BT_VERSION], sort_keys=True, default=list)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def hist_trades(db, symbols, reg, btc, events):
    """Test wsteczny wszystkich wariantów i planu klasycznego: {id: [[closed_ms, r, side, sym], ...]}.
    Wskaźniki liczone raz na aktywo; oceny rodzin sygnałów są wspólne dla wariantów (pamięć podręczna w silniku)."""
    out = {vid: [] for vid in ("plan", *reg)}
    for sym in dict.fromkeys(symbols):
        candles = be.load_candles(db, sym)
        if len(candles) < be.MIN_HISTORY:
            continue
        ind = ba.build_indicators(candles)
        for vid in out:
            planner = ba.plan_at if vid == "plan" else engine(vid, sym, btc, events, reg=reg).plan_at
            for p in be.backtest(candles, planner=planner, ind=ind) or []:
                out[vid].append([p["closed_ms"], p["r"], p["side"], sym])
    for v in out.values():
        v.sort()
    return out


# ----------------------------------------------------------------- diagnoza rodzin sygnałów
def _rank(xs):
    idx = sorted(range(len(xs)), key=xs.__getitem__)
    r, i = [0.0] * len(xs), 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        for k in range(i, j + 1):
            r[idx[k]] = (i + j) / 2.0
        i = j + 1
    return r


def signal_samples(db, symbols, btc, events, lo_ms=0, hi_ms=None, h=IC_H):
    """Próbki do diagnozy: (ts_ms, aktywo, {rodzina: surowa ocena}, zwrot h dni w przód w jednostkach zmienności).
    Co h-ty dzień, żeby okna zwrotów się nie nakładały; tylko dni, dla których wynik jest już znany."""
    out = []
    for sym in dict.fromkeys(symbols):
        candles = be.load_candles(db, sym)
        if len(candles) < be.MIN_HISTORY:
            continue
        ind = ba.build_indicators(candles)
        eng = dc.Engine(symbol=sym, btc_candles=btc, events_s=events, explain=False)      # surowe oceny rodzin (bez odwróceń wariantu)
        cl, rv = ind["cl"], dc._prep(ind)["rv"]
        for t in range(ba.PLAN_WARMUP, len(candles) - h, h):
            ts = candles[t][0]
            if ts < lo_ms or (hi_ms is not None and ts > hi_ms):
                continue
            dec = eng.decide(ind, t)
            if dec is None or not cl[t]:
                continue
            fwd = (cl[t + h] / cl[t] - 1) / ((rv[t] or 0.03) * math.sqrt(h))
            out.append((ts, sym, {k: (dec["fam"][k][0] if dec["fam"][k] is not None else None) for k in dc.FAMILIES}, fwd))
    return out


def _zrank(xs):
    """Rangi sprowadzone do średniej 0 i odchylenia 1 (iloczyn dwóch takich szeregów uśredniony po próbkach to korelacja Spearmana)."""
    r = _rank(xs)
    m = sum(r) / len(r)
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / len(r))
    return [(x - m) / sd for x in r] if sd > 0 else None


def cluster_ic(samples, key, block_days=IC_BLOCK_DAYS, min_asset=20):
    """IC rodziny: korelacja rang (ocena dziś → wynik za h dni) liczona W CZASIE osobno dla każdego aktywa i łączona.
    Istotność z rozrzutu między blokami czasu (grupowanie): aktywa poruszają się razem, więc jeden okres to w praktyce
    jedna obserwacja, a nie tyle, ile jest aktywów."""
    by = {}
    for ts, sym, fam, fwd in samples:
        if fam.get(key) is not None:
            by.setdefault(sym, []).append((ts, fam[key], fwd))
    blocks = {}                                    # blok czasu -> [suma iloczynów, liczba próbek]
    for rows in by.values():
        if len(rows) < min_asset:
            continue
        zx, zy = _zrank([r[1] for r in rows]), _zrank([r[2] for r in rows])
        if zx is None or zy is None:
            continue
        for (ts, _, _), a, b in zip(rows, zx, zy):
            acc = blocks.setdefault(ts // (block_days * DAY_MS), [0.0, 0])
            acc[0] += a * b
            acc[1] += 1
    n, k = sum(b[1] for b in blocks.values()), len(blocks)
    if not n:
        return {"ic": None, "t": 0.0, "p": 1.0, "blocks": 0, "n": 0}
    ic = sum(b[0] for b in blocks.values()) / n
    if k < 3:
        return {"ic": round(ic, 4), "t": 0.0, "p": 1.0, "blocks": k, "n": n}
    var = sum((b[0] - ic * b[1]) ** 2 for b in blocks.values()) / (n * n) * k / (k - 1)
    t = ic / math.sqrt(var) if var > 0 else 0.0
    return {"ic": round(ic, 4), "t": round(t, 2), "p": round(2 * t_sf(abs(t), k - 1), 4), "blocks": k, "n": n}


def usage(params):
    """Jak wariant używa każdej rodziny: znak (+1 zgodnie, −1 kontrariańsko) i średnia waga w reżimach."""
    p = dc.make_params(params)
    return {k: {"sign": -1 if p["fam_sign"].get(k, 1) < 0 else 1, "weight": round(sum(p["weights"][r].get(k, 0.0) for r in p["weights"]) / len(p["weights"]), 3)} for k in dc.FAMILIES}


def diagnose(win, full, params):
    """Zestawienie diagnozy: dla każdej rodziny IC z okna i z całej historii oraz ocena względem sposobu użycia w wariancie.
    Stany: ok (potwierdzona), faded (działała, ostatnio bez mocy), flat (bez mocy), warn (ostatnio odwrotnie),
    contra (odwrotnie w całej historii i w oknie: powstaje pretendent), off (wyłączona)."""
    use, rows = usage(params), []
    for k in dc.FAMILIES:
        w, a, u = win.get(k) or {}, full.get(k) or {}, use[k]
        tw, ta = (w.get("t") or 0.0) * u["sign"], (a.get("t") or 0.0) * u["sign"]
        if u["weight"] <= 0:
            state, note = "off", "wyłączona w tym wariancie"
        elif ta <= -IC_T_FLIP and tw <= -IC_T_RECENT:
            state, note = "contra", "działa odwrotnie, niż zakłada algorytm (w całej historii i w ostatnich 2 latach)"
        elif tw <= -2.0:
            state, note = "warn", "w ostatnich 2 latach działa odwrotnie, niż zakłada algorytm"
        elif ta >= 2.0 and tw >= IC_T_RECENT:
            state, note = "ok", "potwierdzona w całej historii i w ostatnich 2 latach"
        elif ta >= 2.0:
            state, note = "faded", "działała w historii, ale w ostatnich 2 latach nie widać mocy prognostycznej"
        else:
            state, note = "flat", "brak wykrywalnej mocy prognostycznej"
        rows.append({"key": k, "label": dc.LABEL[k], "sign": u["sign"], "weight": u["weight"], "ic": w.get("ic"), "t": w.get("t"), "blocks": w.get("blocks"),
                     "ic_all": a.get("ic"), "t_all": a.get("t"), "blocks_all": a.get("blocks"), "state": state, "note": note})
    return rows


def auto_challengers(d, diag, reg, champ, now):
    """Dla rodzin działających odwrotnie (stan 'contra') tworzy pretendenta: mistrz z odwróconą rodziną. Zwraca listę nowych id."""
    autos = [k for k, v in reg.items() if v.get("origin") == "automatyczny"]
    new, base = [], reg[champ]["params"]
    for row in diag:
        if row["state"] != "contra" or len(autos) + len(new) >= MAX_AUTO:
            continue
        params = copy.deepcopy(base)
        params.setdefault("fam_sign", {})[row["key"]] = -row["sign"]
        if any(json.dumps(v["params"], sort_keys=True) == json.dumps(params, sort_keys=True) for v in reg.values()):
            continue
        vid = next(f"a{i}" for i in range(1, 99) if f"a{i}" not in reg)
        reg[vid] = {"id": vid, "name": f"Auto: {row['label']} odwrócona", "origin": "automatyczny", "params": params, "created": now,
                    "desc": f"Utworzony automatycznie {time.strftime('%d.%m.%Y', time.gmtime(now))}: rodzina „{row['label']}” w wariancie {champ} działała odwrotnie "
                            f"(cała historia: IC {row['ic_all']:+.3f}, t {row['t_all']:+.1f}; ostatnie 2 lata: IC {row['ic']:+.3f}, t {row['t']:+.1f}), więc tu jest liczona przeciwnie."}
        new.append(vid)
    if new and d:
        cur = bb._read_json(os.path.join(d, CUSTOM_FILE)).get("variants") or {}
        cur.update({k: {x: reg[k][x] for x in ("name", "desc", "params", "created")} for k in new})
        bb._atomic_write(os.path.join(d, CUSTOM_FILE), json.dumps({"version": 1, "variants": cur}, ensure_ascii=False))
    return new


# ----------------------------------------------------------------- wyniki z cienia (poza próbą)
def fwd_trades(journals, key):
    """Zamknięte transakcje wariantu z dzienników cienia: ([(closed_ms, r, side, sym)], start_ms, otwarte, oczekujące)."""
    tr, since, opened, pending = [], None, 0, 0
    for jk, j in journals.items():
        if not jk.startswith(key + ":"):
            continue
        sym = jk.split(":", 1)[1]
        for p in j.get("proposals") or []:
            since = min(since or p["created_ms"], p["created_ms"])
            opened += p.get("status") == "open"
            pending += p.get("status") == "pending"
            if p.get("outcome") in ba.PLAN_FILLED and p.get("r") is not None:
                tr.append((p["closed_ms"], p["r"], p["side"], sym))
    tr.sort()
    return tr, since, opened, pending


def verdict(fwd, champ_fwd, n_challengers, since_ms, now_ms):
    """Ocena pretendenta względem mistrza na wynikach z cienia: (stan, opis, porównanie)."""
    weeks = max(0.0, (now_ms - since_ms) / WEEK_MS) if since_ms else 0.0
    n = len(fwd)
    if n < MIN_FWD_TRADES or weeks < MIN_FWD_WEEKS:
        return "wait", f"za mało danych z cienia: {n} z {MIN_FWD_TRADES} transakcji, {weeks:.0f} z {MIN_FWD_WEEKS} tygodni", None
    cmp_ = compare(fwd, champ_fwd, since_ms)
    own = week_test(fwd, since_ms)
    mean = sum(t[1] for t in fwd) / n
    alpha = ALPHA / max(1, n_challengers)
    if cmp_["p"] <= alpha and own["p"] <= 0.10 and mean >= MIN_MEAN_R:
        return "promote", f"wygrywa z mistrzem na żywo: +{cmp_['diff_week']:.2f}R tygodniowo (p {cmp_['p']:.3f} przy progu {alpha:.3f}), własny wynik {mean:+.2f}R na transakcję", cmp_
    if cmp_["t"] <= -2.0:
        return "worse", f"słabszy od mistrza na żywo: {cmp_['diff_week']:+.2f}R tygodniowo (t {cmp_['t']:+.1f})", cmp_
    return "open", f"bez rozstrzygnięcia: {cmp_['diff_week']:+.2f}R tygodniowo względem mistrza (p {cmp_['p']:.2f}, potrzeba ≤ {alpha:.3f})", cmp_


# ----------------------------------------------------------------- wykonanie na żywo kontra symulator
def read_ledger(d, n=400):
    rows = []
    try:
        with open(os.path.join(d, LEDGER), encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    if isinstance(r, dict) and r.get("symbol"):
                        rows.append(r)
                except ValueError:
                    continue
    except OSError:
        pass
    return rows[-n:]


def execution(ledger, journals):
    """Rzeczywiste transakcje bota zestawione z propozycjami symulatora dla tych samych planów (aktywo, data planu, strona)."""
    real = [e for e in ledger if isinstance(e.get("r"), (int, float))]
    pairs, only_real = [], 0
    for e in real:
        key = ("plan" if e.get("strategy") == "plan" else skey(e.get("variant") or "v1")) + ":" + str(e["symbol"])
        p = next((p for p in (journals.get(key) or {}).get("proposals") or [] if p.get("plan_date") == e.get("plan_date") and p.get("side") == e.get("side")), None)
        if p is not None and p.get("outcome") in ba.PLAN_FILLED and p.get("r") is not None:
            pairs.append((e["r"], p["r"]))
        elif p is not None and p.get("status") == "closed":
            only_real += 1
    out = {"n": len(real), "mean_real": round(sum(e["r"] for e in real) / len(real), 3) if real else None, "matched": len(pairs), "only_real": only_real}
    if pairs:
        diff = [a - b for a, b in pairs]
        m, t, _, _ = t_test(diff)
        out.update(mean_real_m=round(sum(a for a, _ in pairs) / len(pairs), 3), mean_sim_m=round(sum(b for _, b in pairs) / len(pairs), 3), diff=round(m, 3), t=round(t, 2))
    return out


# ----------------------------------------------------------------- przebieg dzienny
def load_hist(d):
    return bb._read_json(os.path.join(d, HIST_FILE))


def read(d):
    return bb._read_json(os.path.join(d, LAB_FILE))


def run(d, db, symbols, now=None, journals=None):
    """Dzienny przebieg laboratorium. Zwraca (wynik dla panelu, komunikaty [(rodzaj, tekst)] do dziennika nauki)."""
    now = int(now or time.time())
    now_ms, msgs = now * 1000, []
    reg, champ = variants(d), champion(d)
    btc, events = be.load_candles(db, "BTCUSDT"), be.load_events(db)
    prev = read(d)

    # 1) diagnoza rodzin sygnałów: okno 2 lat codziennie, cała historia razem z testem historycznym
    hist = load_hist(d)
    sig = signature(reg, symbols)
    refresh = hist.get("sig") != sig or now - (hist.get("ts") or 0) > HIST_EVERY_S or now < (hist.get("ts") or 0)
    if refresh:
        allsamples = signal_samples(db, symbols, btc, events)
        ic_all = {k: cluster_ic(allsamples, k) for k in dc.FAMILIES}
    else:
        ic_all = hist.get("ic_all") or {}
    win = signal_samples(db, symbols, btc, events, now_ms - IC_WINDOW_DAYS * DAY_MS)
    diag = diagnose({k: cluster_ic(win, k) for k in dc.FAMILIES}, ic_all, reg[champ]["params"])
    for vid in auto_challengers(d, diag, reg, champ, now):
        msgs.append(("learn", f"Laboratorium: utworzono pretendenta {vid} ({reg[vid]['name']}). Od dziś prowadzi dziennik w cieniu; {reg[vid]['desc']}"))
        refresh = True

    # 2) test historyczny wszystkich wariantów (pamięć podręczna)
    if refresh:
        sig = signature(reg, symbols)
        hist = {"ts": now, "sig": sig, "trades": hist_trades(db, symbols, reg, btc, events), "ic_all": ic_all}
        bb._atomic_write(os.path.join(d, HIST_FILE), json.dumps(hist, default=float))
        msgs.append(("info", "Laboratorium: test historyczny wariantów odświeżony (" + ", ".join(f"{k} {len(v)}" for k, v in hist["trades"].items()) + " transakcji)"))
    htr = hist.get("trades") or {}

    # 3) wyniki z cienia i porównanie z mistrzem
    if journals is None:
        import btc_shadow
        journals = btc_shadow.load(d)
    fwd = {vid: fwd_trades(journals, "plan" if vid == "plan" else skey(vid)) for vid in ("plan", *reg)}
    champ_fwd, champ_since = fwd[champ][0], fwd[champ][1]
    challengers = [v for v in reg if v != champ]
    rows, proposals, alerts = [], [], []
    for vid in ("plan", *reg):
        tr, since, opened, pending = fwd[vid]
        v = reg.get(vid) or {"name": "Klasyczna (plan)", "desc": "Plan klasyczny z analizatora: cofnięcia do poziomów i wybicia w trendzie.", "origin": "wbudowany", "params": None}
        row = {"id": vid, "name": v["name"], "desc": v["desc"], "origin": v["origin"], "role": "champion" if vid == champ else "classic" if vid == "plan" else "challenger",
               "hist": summarize([tuple(x) for x in htr.get(vid) or []], now_ms), "fwd": dict(summarize(tr, now_ms), since=int(since / 1000) if since else None, open=opened, pending=pending)}
        if vid in challengers:
            both = max(since or now_ms, champ_since or now_ms)
            st, txt, cmp_ = verdict([t for t in tr if t[0] >= both], [t for t in champ_fwd if t[0] >= both], len(challengers), both if since and champ_since else None, now_ms)
            if st == "promote" and not row["hist"]["mean_r"] > 0:      # sprzeczne dowody: na żywo wygrywa, ale historia jest ujemna
                st, txt = "hold", txt + f"; bez wniosku o awans, bo w teście historycznym wariant ma wynik {row['hist']['mean_r']:+.2f}R"
            row.update(status=st, verdict=txt, vs_champion=cmp_)
            row["hist_vs_champion"] = compare([tuple(x) for x in htr.get(vid) or []], [tuple(x) for x in htr.get(champ) or []])
            if st == "promote":
                proposals.append({"variant": vid, "name": v["name"], "text": txt, "ts": next((p["ts"] for p in prev.get("proposals") or [] if p.get("variant") == vid), now)})
        rows.append(row)
    cf = summarize(champ_fwd, now_ms)
    if cf["n"] >= MIN_FWD_TRADES:
        m, t, _, _ = t_test(list(week_sums(champ_fwd).values()))
        if t_sf(-t, max(1, cf["weeks"] - 1)) <= 0.05 and cf["mean_r"] < 0:
            alerts.append(f"Mistrz ({champ}) traci na żywo: {cf['mean_r']:+.2f}R na transakcję w {cf['n']} transakcjach z cienia. Rozważ pauzę bota albo zmianę wariantu.")
    known = {p.get("variant") for p in prev.get("proposals") or []}
    for p in proposals:
        if p["variant"] not in known:
            msgs.append(("learn", f"Laboratorium: wniosek o awans wariantu {p['variant']} ({p['name']}): {p['text']}. Zatwierdź w zakładce Bot, jeśli chcesz, żeby bot go używał."))
    for a in alerts:
        if a not in (prev.get("alerts") or []):
            msgs.append(("alert", "Laboratorium: " + a))
    for row in diag:
        if row["state"] in ("contra", "warn") and not any(x.get("key") == row["key"] and x.get("state") == row["state"] for x in (prev.get("signals") or {}).get("families") or []):
            msgs.append(("alert" if row["state"] == "contra" else "info", f"Laboratorium: rodzina „{row['label']}” {row['note']} (ostatnie 2 lata: IC {row['ic'] or 0:+.3f}, t {row['t'] or 0:+.1f})."))

    out = {"ts": now, "version": VERSION, "champion": champ, "variants": rows, "proposals": proposals, "alerts": alerts,
           "signals": {"window_days": IC_WINDOW_DAYS, "horizon": IC_H, "block_days": IC_BLOCK_DAYS, "samples": len(win), "families": diag},
           "execution": execution(read_ledger(d), journals), "hist_ts": hist.get("ts"), "symbols": len(set(symbols)),
           "rules": {"min_trades": MIN_FWD_TRADES, "min_weeks": MIN_FWD_WEEKS, "alpha": ALPHA, "min_mean_r": MIN_MEAN_R, "t_flip": IC_T_FLIP, "t_recent": IC_T_RECENT},
           "costs": {"fee_pct": ba.PLAN_FEE_PCT, "funding_pct_day": ba.PLAN_FUNDING_PCT_DAY, "stop_slip_pct": ba.PLAN_STOP_SLIP_PCT}}
    bb._atomic_write(os.path.join(d, LAB_FILE), json.dumps(out, ensure_ascii=False, default=float))
    return out, msgs
