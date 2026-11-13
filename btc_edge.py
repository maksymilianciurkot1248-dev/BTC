#!/usr/bin/env python3
"""Mapa przewagi: które aktywa bot może handlować i kiedy ma ich NIE handlować, na podstawie dowodów z historii.

Dla każdego aktywa uruchamiamy dokładnie tę samą funkcję planu, której używa trader (btc_analysis.plan_at), na świecach
dziennych z Bybit, rozliczamy transakcje po kosztach (R = wielokrotność ryzyka do stopa) i zezwalamy na handel tylko, gdy:
  - jest dość transakcji, a średni wynik po kosztach jest dodatni i statystycznie istotny po korekcie na wiele aktywów (BH-FDR),
  - obie połówki historii i ostatnie 12 miesięcy nie są ujemne (przewaga nie wygasła).
Dodatkowo szukamy filtrów kontekstu (BTC pod SMA50, dzień wokół ważnych publikacji makro): filtr włączamy tylko, gdy
transakcje w danym kontekście są istotnie gorsze. Wynik: edge_map.json. Moduł tylko ZAWĘŻA to, co wolno botowi.

Mapa jest liczona codziennie, więc bez zabezpieczeń aktywo wchodziłoby i wypadało z powodu drobnych zmian p-wartości.
  - Histereza: WEJŚCIE wymaga pełnych kryteriów (p skorygowane <= Q_ENTER), ale aktywo już dopuszczone zostaje, dopóki nie spełni
    łagodniejszych warunków WYJŚCIA (p skorygowane > Q_EXIT, średnia <= 0, wyraźnie ujemna połówka historii albo ostatni rok).
    Takie aktywo ma znacznik "held" (dowody słabsze niż przy wejściu) i trader handluje nim mniejszą pozycją (HELD_SIZE_MULT).
  - Wspólna korekta FDR: obie strategie (klasyczna i zespół) testują te same aktywa, więc rodziną testów jest każda para
    (strategia, aktywo), a nie osobno każda strategia.
  - Historia decyzji (edge_history.json): każde dopuszczenie, wycofanie, utrzymanie i potwierdzenie z datą, wynikiem i powodem.
Histereza ogranicza migotanie, ale nie usuwa problemu wielokrotnego testowania w czasie: rzetelnym dowodem pozostaje tryb cienia.
"""
import bisect
import math
import statistics
import time

import btc_analysis as ba
from btc_research import bh_fdr

DAY = 86_400
MIN_TRADES = 30          # minimum rozliczonych transakcji w historii
MIN_MEAN_R = 0.05        # WEJŚCIE: minimalny średni wynik po kosztach (w R)
Q_ENTER = 0.05           # WEJŚCIE: p skorygowane (BH-FDR) co najwyżej tyle
Q_EXIT = 0.20            # WYJŚCIE: dopiero p skorygowane powyżej tego wycofuje aktywo już dopuszczone
Q_FDR = Q_ENTER          # nazwa z wcześniejszych wersji
EXIT_MEAN_R = 0.0        # WYJŚCIE: średni wynik nie jest już dodatni
EXIT_HALF_R = -0.10      # WYJŚCIE: któraś połówka historii wyraźnie ujemna
EXIT_RECENT_R = -0.10    # WYJŚCIE: ostatnie 12 miesięcy wyraźnie ujemne
HELD_SIZE_MULT = 0.5     # rozmiar pozycji na aktywie utrzymanym przez histerezę (1.0 = bez zmniejszania)
PRIOR_MAX_AGE_S = 10 * 86_400   # poprzednia mapa starsza niż to nie daje histerezy (wszystko wchodzi od nowa)
HISTORY_KEEP = 600
RECENT_DAYS = 365
MIN_RECENT = 8           # ile transakcji z ostatnich 12 miesięcy potrzeba, żeby ocenić wygasanie przewagi
MIN_HISTORY = ba.PLAN_WARMUP + 60
STALE_S = 4 * DAY        # starsza mapa przewagi nie pozwala na automatyczny wybór aktywów
FILTER_MIN_N = 12
FILTER_P = 0.10
MACRO_EXCLUDE = ("opex_m", "opex_q", "halving")
NEAR_EVENT_S = 36 * 3600


def key(sym):
    return "BYBIT:" + sym


# ---------------------------------------------------------------- dane
def load_candles(db, sym):
    with db.lock:
        rows = db.con.execute("SELECT ts,o,h,l,c,v FROM candles WHERE symbol=? AND interval='1d' ORDER BY ts", (key(sym),)).fetchall()
    return [[r[0] * 1000, r[1], r[2], r[3], r[4], r[5] or 0.0] for r in rows]


def refresh_candles(db, pub, sym, now_ms=None, pages=3):
    """Dociąga zamknięte świece dzienne z Bybit (przy pierwszym pobraniu do ~8 lat wstecz)."""
    now_ms = now_ms or int(time.time() * 1000)
    have = db._one("SELECT COUNT(*) FROM candles WHERE symbol=? AND interval='1d'", (key(sym),))
    rows, end = [], None
    for _ in range(1 if have >= 1500 else pages):
        batch = [x for x in pub.klines(sym, "D", 1000, end=end) if x[0] + ba.DAY_MS <= now_ms]
        if not batch:
            break
        rows += batch
        end = batch[0][0] - 1
        if len(batch) < 1000:
            break
    if rows:
        db.put_candles(key(sym), "1d", [(r[0] // 1000, *r[1:6]) for r in rows], "bybit")
    return len(rows)


def load_events(db):
    """Daty ważnych publikacji makro (FOMC, CPI itp.; bez wygaśnięć opcji i halvingu), w sekundach."""
    with db.lock:
        rows = db.con.execute(f"SELECT ts FROM events WHERE kind NOT IN ({','.join('?' * len(MACRO_EXCLUDE))}) ORDER BY ts", MACRO_EXCLUDE).fetchall()
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------------- statystyka
def mean_sd(xs):
    n = len(xs)
    m = sum(xs) / n if n else 0.0
    sd = statistics.stdev(xs) if n > 1 else 0.0
    return m, sd


def p_mean_positive(xs):
    """Jednostronne p dla H1: średnia > 0 (przybliżenie normalne; wymagamy n >= 30)."""
    n = len(xs)
    m, sd = mean_sd(xs)
    if n < 2 or sd <= 0:
        return 1.0 if m <= 0 else 0.0
    z = m / (sd / math.sqrt(n))
    return 0.5 * math.erfc(z / math.sqrt(2))


def p_less(a, b):
    """Jednostronne p (Welch) dla H1: średnia(a) < średnia(b)."""
    (ma, sa), (mb, sb) = mean_sd(a), mean_sd(b)
    se = math.sqrt(sa * sa / len(a) + sb * sb / len(b))
    if se <= 0:
        return 1.0 if ma >= mb else 0.0
    return 0.5 * math.erfc(-((ma - mb) / se) / math.sqrt(2))


def summarize(rs):
    n = len(rs)
    m, sd = mean_sd(rs)
    return {"n": n, "mean_r": round(m, 4), "win_rate": round(sum(1 for r in rs if r > 0) / n * 100, 1) if n else 0.0,
            "p": round(p_mean_positive(rs), 5) if n >= 2 else 1.0,
            "lb": round(m - 1.645 * sd / math.sqrt(n), 4) if n > 1 else None}


# ---------------------------------------------------------------- backtest tej samej logiki co na żywo
def backtest(candles, fee_pct=ba.PLAN_FEE_PCT, planner=None, ind=None):
    """Zwraca rozliczone propozycje planu (walk-forward: każdego dnia plan widzi tylko świece do tego dnia).
    planner: funkcja plan_at(ind, t, risk_pct); domyślnie klasyczny plan z btc_analysis.
    ind: gotowe wskaźniki tych świec (gdy testujemy kilka wariantów na tym samym aktywie, liczymy je raz)."""
    if len(candles) < MIN_HISTORY:
        return None
    plan_fn = planner or ba.plan_at
    ind, j = ind or ba.build_indicators(candles), ba.new_journal("edge")
    for t in range(ba.PLAN_WARMUP, len(candles)):
        ba.journal_step(j, plan_fn(ind, t, 1.0), candles[:t + 1], ba.DAY_MS, candles[t][0] + ba.DAY_MS, fee_pct=fee_pct)
    return [p for p in j["proposals"] if p.get("outcome") in ba.PLAN_FILLED and p.get("r") is not None and p.get("filled_ms") and p.get("closed_ms")]


def btc_down_flags(btc):
    """(lista ts_ms, lista flag): czy zamknięcie BTC było pod SMA50 w danym dniu."""
    closes = [x[4] for x in btc]
    ts, fl = [], []
    for i in range(49, len(btc)):
        ts.append(btc[i][0])
        fl.append(closes[i] < sum(closes[i - 49:i + 1]) / 50)
    return ts, fl


def flag_btc_down(btc_flags, filled_ms):
    ts, fl = btc_flags
    i = bisect.bisect_right(ts, filled_ms - ba.DAY_MS) - 1     # ostatnia ZAMKNIĘTA świeca przed wejściem
    return bool(fl[i]) if i >= 0 else False


def flag_near_event(events_s, at_s):
    i = bisect.bisect_left(events_s, at_s - NEAR_EVENT_S)
    return i < len(events_s) and events_s[i] <= at_s + NEAR_EVENT_S


# ---------------------------------------------------------------- mapa przewagi
def analyze_asset(candles, btc_flags, events_s, now_s, planner=None):
    props = backtest(candles, planner=planner)
    if props is None:
        return {"status": "no_data", "reasons": [f"za krótka historia (mniej niż {MIN_HISTORY} dni)"], "days": len(candles), "n": 0}
    props.sort(key=lambda p: p["closed_ms"])
    rs = [p["r"] for p in props]
    out = summarize(rs)
    out["status"] = "pending"
    h = len(rs) // 2
    out["halves"] = [round(mean_sd(rs[:h])[0], 4), round(mean_sd(rs[h:])[0], 4)] if len(rs) >= 2 * 8 else None
    rec = [p["r"] for p in props if p["closed_ms"] / 1000 >= now_s - RECENT_DAYS * DAY]
    out["recent"] = {"n": len(rec), "mean_r": round(mean_sd(rec)[0], 4) if rec else None}
    out["from"] = time.strftime("%Y-%m-%d", time.gmtime(candles[ba.PLAN_WARMUP][0] / 1000))
    out["to"] = time.strftime("%Y-%m-%d", time.gmtime(candles[-1][0] / 1000))
    out["days"] = len(candles) - ba.PLAN_WARMUP
    out["_props"] = [(p["r"], bool(btc_flags and flag_btc_down(btc_flags, p["filled_ms"])), flag_near_event(events_s, p["filled_ms"] / 1000)) for p in props]
    return out


def find_filters(props):
    """Filtry kontekstu: włączamy tylko, gdy transakcje w kontekście są istotnie gorsze i ujemne, a poza nim dodatnie."""
    res = []
    for idx, name in ((1, "btc_down"), (2, "near_event")):
        a = [p[0] for p in props if p[idx]]
        b = [p[0] for p in props if not p[idx]]
        if len(a) >= FILTER_MIN_N and len(b) >= FILTER_MIN_N and mean_sd(a)[0] < 0 < mean_sd(b)[0]:
            p = p_less(a, b)
            if p < FILTER_P:
                res.append({"flag": name, "n_flag": len(a), "mean_flag": round(mean_sd(a)[0], 3), "n_other": len(b), "mean_other": round(mean_sd(b)[0], 3), "p": round(p, 4)})
    return res


def entry_failures(a):
    """Powody, dla których aktywo NIE spełnia pełnych kryteriów wejścia (pusta lista = wolno wejść)."""
    why = []
    if a["n"] < MIN_TRADES:
        why.append(f"za mało transakcji w historii ({a['n']} z wymaganych {MIN_TRADES})")
    if a["mean_r"] < MIN_MEAN_R:
        why.append(f"średni wynik po kosztach {a['mean_r']:+.2f}R, poniżej progu +{MIN_MEAN_R:.2f}R")
    if a["n"] >= MIN_TRADES and a.get("p_adj", 1.0) > Q_ENTER:
        why.append(f"brak istotności statystycznej (p skorygowane {a.get('p_adj', 1.0):.2f} > {Q_ENTER})")
    if a.get("halves") and min(a["halves"]) <= 0:
        why.append("niestabilne: jedna z połówek historii ma wynik ujemny")
    r = a.get("recent") or {}
    if r.get("n", 0) >= MIN_RECENT and r.get("mean_r") is not None and r["mean_r"] <= 0:
        why.append(f"przewaga wygasa: ostatnie 12 miesięcy {r['mean_r']:+.2f}R ({r['n']} transakcji)")
    return why


verdict = entry_failures        # nazwa z wcześniejszych wersji


def exit_failures(a):
    """Powody, dla których aktywo już dopuszczone trzeba WYCOFAĆ (histereza: znacznie łagodniejsze niż przy wejściu)."""
    why = []
    if a["n"] < MIN_TRADES:
        why.append(f"za mało transakcji w historii ({a['n']} z wymaganych {MIN_TRADES})")
    elif a.get("p_adj", 1.0) > Q_EXIT:
        why.append(f"istotność spadła poniżej progu wyjścia (p skorygowane {a.get('p_adj', 1.0):.2f} > {Q_EXIT})")
    if a["mean_r"] <= EXIT_MEAN_R:
        why.append(f"średni wynik po kosztach {a['mean_r']:+.2f}R, już nie dodatni")
    if a.get("halves") and min(a["halves"]) < EXIT_HALF_R:
        why.append(f"jedna z połówek historii wyraźnie ujemna ({min(a['halves']):+.2f}R)")
    r = a.get("recent") or {}
    if r.get("n", 0) >= MIN_RECENT and r.get("mean_r") is not None and r["mean_r"] < EXIT_RECENT_R:
        why.append(f"przewaga wygasła: ostatnie 12 miesięcy {r['mean_r']:+.2f}R ({r['n']} transakcji)")
    return why


def decide(a, was_allowed):
    """Ocena aktywa z histerezą: (status, powody odrzucenia, utrzymane, powody słabszych dowodów).
    Wejście wymaga pełnych kryteriów; aktywo już dopuszczone zostaje, dopóki nie spełni warunku wyjścia (wtedy utrzymane=True)."""
    strict = entry_failures(a)
    if not strict:
        return "allowed", [], False, []
    if was_allowed:
        out = exit_failures(a)
        if not out:
            return "allowed", [], True, strict
        return "rejected", out, False, []
    return "rejected", strict, False, []


def analyze_strategy(db, symbols, now_s, strategy, btc, btc_flags, events, ens=None):
    """Surowa analiza wszystkich aktywów jedną strategią ("plan" albo "ensemble"); bez korekty FDR i bez werdyktu.
    ens: (id wariantu, parametry) zespołu sygnałów, którym handluje bot; None = wariant v1."""
    out = {}
    for sym in dict.fromkeys(symbols):
        planner = None
        if strategy == "ensemble":
            import btc_decision
            planner = btc_decision.Engine(symbol=sym, btc_candles=btc, events_s=events, explain=False, params=(ens or (None, None))[1], variant=(ens or ("v1",))[0]).plan_at
        out[sym] = analyze_asset(load_candles(db, sym), btc_flags, events, now_s, planner)
    return out


def finalize(raw, prev, now_s, events, ens_variant="v1"):
    """Z surowych analiz ({strategia: {aktywo: analiza}}) robi mapy przewagi: wspólna korekta FDR dla wszystkich par (strategia, aktywo),
    werdykt z histerezą względem poprzedniej mapy `prev` i filtry kontekstu. Zwraca {strategia: mapa}.
    Gdy zmienił się wariant zespołu sygnałów, jego poprzednie oceny nie dają histerezy (to już inna strategia)."""
    prior_ok = bool(prev) and bool(prev.get("ts")) and 0 <= now_s - prev["ts"] <= PRIOR_MAX_AGE_S
    same_ens = (prev or {}).get("ens_variant", "v1") == ens_variant
    fam = [(st, s) for st, assets in raw.items() for s, a in assets.items() if a["n"] >= MIN_TRADES]
    for (st, s), q in zip(fam, bh_fdr([raw[st][s]["p"] for st, s in fam])):
        raw[st][s]["p_adj"] = round(q, 5)
    for assets in raw.values():                 # dla porównania: korekta osobno w obrębie każdej strategii
        solo = [s for s, a in assets.items() if a["n"] >= MIN_TRADES]
        for s, q in zip(solo, bh_fdr([assets[s]["p"] for s in solo])):
            assets[s]["p_adj_solo"] = round(q, 5)
    passes = {"joint": 0, "solo": 0}            # ile par spełnia pełne kryteria wejścia przy wspólnej i przy osobnej korekcie
    for assets in raw.values():
        for a in assets.values():
            if a["status"] != "no_data":
                passes["joint"] += not entry_failures(a)
                passes["solo"] += not entry_failures({**a, "p_adj": a.get("p_adj_solo", 1.0)})
    upcoming = [t for t in events if now_s - 2 * DAY <= t <= now_s + 10 * DAY]
    params = {"min_trades": MIN_TRADES, "min_mean_r": MIN_MEAN_R, "fdr_q": Q_ENTER, "fdr_q_enter": Q_ENTER, "fdr_q_exit": Q_EXIT, "exit_mean_r": EXIT_MEAN_R,
              "held_size_mult": HELD_SIZE_MULT, "family": len(fam), "strategies": list(raw), "fee_pct": ba.PLAN_FEE_PCT,
              "funding_pct_day": ba.PLAN_FUNDING_PCT_DAY, "stop_slip_pct": ba.PLAN_STOP_SLIP_PCT}
    maps = {}
    for st, assets in raw.items():
        prior = ((prev or {}).get("assets_ens" if st == "ensemble" else "assets") or {}) if prior_ok and (st != "ensemble" or same_ens) else {}
        allowed = []
        for s, a in assets.items():
            before = prior.get(s) or {}
            was = before.get("status") == "allowed"
            props = a.pop("_props", [])
            a["held"] = False
            if a["status"] != "no_data":
                a["status"], a["reasons"], a["held"], weak = decide(a, was)
                if a["held"]:
                    a["held_why"] = weak
            a["filters"] = find_filters(props) if a["status"] == "allowed" else []
            now_allowed = a["status"] == "allowed"
            a["since"] = (before.get("since") or prev.get("ts") or now_s) if before and was == now_allowed else now_s
            if now_allowed:
                allowed.append(s)
        allowed.sort(key=lambda s: -assets[s]["mean_r"])
        maps[st] = {"ts": now_s, "version": 2, "strategy": st, "ens_variant": ens_variant, "params": params, "assets": assets, "allowed": allowed,
                    "held": [s for s in allowed if assets[s]["held"]], "events": upcoming,
                    "hysteresis": {"prior_used": bool(prior), "entry_pass_joint": passes["joint"], "entry_pass_solo": passes["solo"]}}
    return maps


def build_maps(db, symbols, now_s=None, strategies=("plan", "ensemble"), prev=None, ens=None):
    """Mapy przewagi dla kilku strategii naraz: (mapy {strategia: mapa}, błędy {strategia: komunikat}).
    Pierwsza strategia jest obowiązkowa (jej błąd przerywa), pozostałe mogą się nie udać bez wpływu na resztę.
    prev: poprzednia mapa (edge_map.json), od której zależy histereza. ens: (id, parametry) wariantu zespołu sygnałów."""
    now_s = now_s or int(time.time())
    btc = load_candles(db, "BTCUSDT")
    btc_flags = btc_down_flags(btc) if len(btc) > 60 else None
    events = load_events(db)
    raw, errors = {}, {}
    for i, st in enumerate(strategies):
        try:
            raw[st] = analyze_strategy(db, symbols, now_s, st, btc, btc_flags, events, ens)
        except Exception as e:  # noqa: BLE001
            if i == 0:
                raise
            errors[st] = f"{type(e).__name__}: {str(e)[:100]}"
    return finalize(raw, prev, now_s, events, (ens or ("v1",))[0]), errors


def build_map(db, symbols, now_s=None, strategy="plan", prev=None, ens=None):
    """Mapa przewagi jednej strategii: "plan" (klasyczny plan z btc_analysis) albo "ensemble" (zespół sygnałów z btc_decision)."""
    return build_maps(db, symbols, now_s, (strategy,), prev, ens)[0][strategy]


def transitions(old, new):
    """Zmiany stanu aktywów między dwiema mapami jednej strategii ({"allowed": [...], "assets": {...}}):
    allow (wejście), withdraw (wycofanie), hold (dowody osłabły, utrzymane przez histerezę), confirm (utrzymane znów spełnia pełne kryteria)."""
    oa, na = set((old or {}).get("allowed") or []), set(new.get("allowed") or [])
    olds = (old or {}).get("assets") or {}
    out = []
    for s in sorted(na | oa):
        a = new["assets"].get(s) or {}
        if s in na and s not in oa:
            act, why = "allow", []
        elif s in oa and s not in na:
            act, why = "withdraw", list(a.get("reasons") or ["brak danych"])
        elif a.get("held") and not (olds.get(s) or {}).get("held"):
            act, why = "hold", list(a.get("held_why") or [])
        elif not a.get("held") and (olds.get(s) or {}).get("held"):
            act, why = "confirm", []
        else:
            continue
        out.append({"symbol": s, "action": act, "n": a.get("n"), "mean_r": a.get("mean_r"), "q": a.get("p_adj"), "reasons": why})
    return out


def update_history(events, trans_by_strategy, now_s, keep=HISTORY_KEEP):
    """Dopisuje do historii decyzji zmiany stanu ({strategia: transitions(...)}); trzyma ostatnie `keep` wpisów."""
    out = list(events or [])
    for strategy, trans in trans_by_strategy.items():
        out += [{"ts": int(now_s), "strategy": strategy, **t} for t in trans]
    return out[-keep:]


def churn(events, now_s, days=30):
    """Ile razy w ostatnich `days` dniach aktywo wchodziło lub było wycofywane: {"strategia:aktywo": liczba}."""
    out = {}
    for e in events or []:
        if e.get("action") in ("allow", "withdraw") and now_s - (e.get("ts") or 0) <= days * DAY:
            k = f"{e.get('strategy')}:{e.get('symbol')}"
            out[k] = out.get(k, 0) + 1
    return out


def diff_maps(old, new):
    """Komunikaty o zmianach dla dziennika nauki."""
    msgs = []
    oa, na = set((old or {}).get("allowed") or []), set(new.get("allowed") or [])
    for t in transitions(old, new):
        s, a = t["symbol"], new["assets"].get(t["symbol"]) or {}
        if t["action"] == "allow":
            msgs.append(("learn", f"{s}: dopuszczone do handlu (średnio {a['mean_r']:+.2f}R po kosztach, {a['n']} transakcji, p skoryg. {a.get('p_adj')})"))
        elif t["action"] == "withdraw":
            msgs.append(("alert", f"{s}: wycofane z handlu ({'; '.join(t['reasons'])})"))
        elif t["action"] == "hold":
            smaller = f"; pozycje o {round((1 - HELD_SIZE_MULT) * 100)}% mniejsze" if HELD_SIZE_MULT < 1 else ""
            msgs.append(("learn", f"{s}: dowody osłabły, ale zostaje dopuszczone (histereza: wycofanie dopiero przy p skoryg. > {Q_EXIT}){smaller}. Nie spełnia już: {'; '.join(t['reasons'])}"))
        else:
            msgs.append(("learn", f"{s}: dowody znów spełniają pełne kryteria wejścia, pozycje w normalnym rozmiarze"))
    for s in sorted(na & oa):
        f_new = {x["flag"] for x in new["assets"][s]["filters"]}
        f_old = {x["flag"] for x in ((old or {}).get("assets", {}).get(s, {}).get("filters") or [])}
        for f in sorted(f_new - f_old):
            msgs.append(("learn", f"{s}: nowy filtr kontekstu „{f}” (w tym kontekście wynik istotnie gorszy)"))
        for f in sorted(f_old - f_new):
            msgs.append(("learn", f"{s}: filtr „{f}” przestał być potrzebny"))
    return msgs


# ---------------------------------------------------------------- użycie w traderze
def select_symbols(edge, manual, quar, now, auto, limit=12, strategy="plan"):
    """Lista aktywów do oceny w tym przebiegu: ręczne (z panelu) + dopuszczone przez mapę przewagi (gdy auto), bez kwarantanny."""
    notes, out = [], list(manual)
    if auto:
        if not edge or not edge.get("ts") or now - edge["ts"] > STALE_S:
            notes.append("mapa przewagi jest nieaktualna lub jej brak: automatyczny wybór wstrzymany")
        else:
            allowed = (edge.get("allowed_ens") if strategy == "ensemble" else edge.get("allowed")) or []
            for s in allowed:
                if s not in out:
                    out.append(s)
            if not allowed:
                notes.append("żadne aktywo nie ma obecnie udowodnionej przewagi: automat nie handluje")
    until = (quar or {}).get("until", {})
    keep = []
    for s in out:
        if until.get(s, 0) > now:
            notes.append(f"{s}: kwarantanna po serii strat do {time.strftime('%d.%m %H:%M', time.gmtime(until[s]))} UTC")
        else:
            keep.append(s)
    return keep[:limit], notes


def quarantine_update(rows, quar, now, unit_usd, days=7, k=6, loss_units=2.0, min_trades=4):
    """Kwarantanna aktywa po serii strat na koncie: suma ostatnich k transakcji gorsza niż -loss_units * ryzyko na pozycję.
    Liczą się tylko transakcje zamknięte po ostatnim zresetowaniu. Zwraca (nowy stan, lista nowych kwarantann)."""
    until = {s: t for s, t in (quar or {}).get("until", {}).items() if t > now}
    reset = dict((quar or {}).get("reset", {}))
    by = {}
    for r in rows:
        try:
            ts = int(r.get("updatedTime") or r.get("createdTime"))
            by.setdefault(r["symbol"], []).append((ts, float(r["closedPnl"])))
        except (KeyError, TypeError, ValueError):
            continue
    new = []
    for sym, lst in by.items():
        lst = sorted(x for x in lst if x[0] > reset.get(sym, 0))[-k:]
        if len(lst) >= min_trades and unit_usd > 0 and sum(p for _, p in lst) < -loss_units * unit_usd and sym not in until:
            until[sym] = now + days * DAY
            reset[sym] = now * 1000
            new.append(sym)
    return {"until": until, "reset": reset}, new


def context_now(btc_candles, edge, now):
    """Aktualny kontekst do filtrów: BTC pod SMA50 (ostatnia zamknięta świeca) i bliskość publikacji makro."""
    closed = [x for x in btc_candles if x[0] + ba.DAY_MS <= now * 1000]
    down = len(closed) >= 50 and closed[-1][4] < sum(x[4] for x in closed[-50:]) / 50
    near = flag_near_event(sorted((edge or {}).get("events") or []), now)
    return {"btc_down": bool(down), "near_event": bool(near)}


def assets_of(edge, strategy="plan"):
    return ((edge or {}).get("assets_ens" if strategy == "ensemble" else "assets")) or {}


def blocked_by_filters(edge, sym, ctx, strategy="plan"):
    flags = [f["flag"] for f in (assets_of(edge, strategy).get(sym) or {}).get("filters") or []]
    return [f for f in flags if ctx.get(f)]


def size_factor(edge, sym, strategy="plan"):
    """Mnożnik rozmiaru pozycji: aktywo utrzymane przez histerezę (dowody słabsze niż przy wejściu) dostaje mniejszą pozycję."""
    return HELD_SIZE_MULT if (assets_of(edge, strategy).get(sym) or {}).get("held") else 1.0


def held_symbols(edge, strategy="plan"):
    allowed = (edge or {}).get("allowed_ens" if strategy == "ensemble" else "allowed") or []
    return [s for s in allowed if (assets_of(edge, strategy).get(s) or {}).get("held")]
