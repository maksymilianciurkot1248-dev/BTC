#!/usr/bin/env python3
"""Tryb cienia: wynik obu strategii na żywo, bez składania zleceń.

Codziennie (po zamknięciu świecy dziennej, w ramach btc_learn) dla każdego aktywa i KAŻDEJ strategii (klasyczny plan oraz wszystkie
warianty zespołu sygnałów zarejestrowane w laboratorium, btc_lab) prowadzimy dziennik propozycji tym samym silnikiem, którego używa test wsteczny (btc_analysis.journal_step z kosztami, TP1/TP2, stop na wejście,
stop czasowy). Dziennik startuje od dnia pierwszego uruchomienia, więc każdy wynik jest uczciwie POZA próbą: żadna strategia nie mogła być
dopasowana do tych dni. To jedyny dowód skuteczności, który nie pochodzi z historii. Wyniki: shadow_stats.json (serwuje /api/shadow).
"""
import json
import os
import time

import btc_analysis as ba
import btc_botcfg as bb
import btc_edge as be

JOURNAL = "shadow_journal.json"
STATS = "shadow_stats.json"
STRATEGIES = ("plan", "ensemble")
NAMES = {"plan": "Klasyczna (plan)", "ensemble": "Zespół sygnałów"}


def strategies(d=None):
    """[(klucz dziennika, nazwa)]: plan klasyczny i wszystkie warianty zespołu sygnałów z laboratorium.
    Wariant v1 zachowuje dawny klucz 'ensemble', żeby jego dziennik miał ciągłość."""
    import btc_lab
    reg = btc_lab.variants(d)
    return [("plan", NAMES["plan"])] + [(btc_lab.skey(v), reg[v]["name"]) for v in reg]


def _planner(strategy, symbol, btc, events, reg=None):
    if strategy == "plan":
        return ba.plan_at
    import btc_lab
    return btc_lab.engine("v1" if strategy == "ensemble" else strategy, symbol, btc, events, reg=reg).plan_at


def load(d):
    return bb._read_json(os.path.join(d, JOURNAL)).get("journals") or {}


def save(d, journals):
    bb._atomic_write(os.path.join(d, JOURNAL), json.dumps({"journals": journals}, default=float))


def step(journals, strategy, symbol, candles, btc, events, ind=None, reg=None):
    """Jeden dzień dla jednego aktywa i jednej strategii. Idempotentne w obrębie tej samej świecy.
    ind: gotowe wskaźniki tych świec (wspólne dla wszystkich strategii na tym aktywie)."""
    if len(candles) <= ba.PLAN_WARMUP:         # za mało historii na pierwszy plan
        return False
    key = f"{strategy}:{symbol}"
    j = journals.setdefault(key, ba.new_journal(key))
    ind = ind or ba.build_indicators(candles)
    plan = _planner(strategy, symbol, btc, events, reg)(ind, len(candles) - 1, 1.0)
    ba.journal_step(j, plan, candles, ba.DAY_MS, candles[-1][0] + ba.DAY_MS)
    return True


def stats(journals, now_s=None, strats=None):
    now_s = now_s or int(time.time())
    out = {"ts": now_s, "strategies": {}}
    for strategy, name in strats or [(s, NAMES[s]) for s in STRATEGIES]:
        rs, curve, by_sym, opened, pending, first = [], [], [], 0, 0, None
        rows = []
        for key, j in journals.items():
            if not key.startswith(strategy + ":"):
                continue
            sym = key.split(":", 1)[1]
            props = j.get("proposals") or []
            done = sorted((p for p in props if p.get("outcome") in ba.PLAN_FILLED and p.get("r") is not None), key=lambda p: p["closed_ms"])
            opened += sum(1 for p in props if p.get("status") == "open")
            pending += sum(1 for p in props if p.get("status") == "pending")
            if props:
                first = min(first or 1e18, min(p["created_ms"] for p in props))
            for p in done:
                rows.append((p["closed_ms"], p["r"]))
            if done:
                s = be.summarize([p["r"] for p in done])
                by_sym.append({"symbol": sym, "n": s["n"], "mean_r": s["mean_r"], "win_rate": s["win_rate"], "total_r": round(sum(p["r"] for p in done), 3)})
        rows.sort()
        cum = 0.0
        for ts, r in rows:
            cum += r
            curve.append([int(ts / 1000), round(cum, 3)])
        rs = [r for _, r in rows]
        s = be.summarize(rs) if rs else {"n": 0}
        out["strategies"][strategy] = {"name": name, "n": s["n"], "mean_r": s.get("mean_r"), "win_rate": s.get("win_rate"), "p": s.get("p"), "lb": s.get("lb"),
                                       "total_r": round(sum(rs), 3) if rs else 0.0, "open": opened, "pending": pending, "since": int(first / 1000) if first else None,
                                       "curve": curve[-200:], "by_symbol": sorted(by_sym, key=lambda x: -x["total_r"])}
    return out


def run(d, db, symbols, now_s=None):
    """Krok dzienny dla wszystkich aktywów i wszystkich strategii (plan klasyczny + warianty zespołu); zapisuje dziennik i statystyki.
    Zwraca (liczba kroków, statystyki)."""
    import btc_lab
    now_s = now_s or int(time.time())
    journals = load(d)
    btc = be.load_candles(db, "BTCUSDT")
    events = be.load_events(db)
    reg, strats = btc_lab.variants(d), strategies(d)
    n = 0
    for sym in dict.fromkeys(symbols):
        candles = be.load_candles(db, sym)
        if len(candles) <= ba.PLAN_WARMUP:
            continue
        ind = ba.build_indicators(candles)
        for strategy, _ in strats:
            try:
                n += bool(step(journals, strategy, sym, candles, btc, events, ind, reg))
            except Exception:  # noqa: BLE001 — jedno aktywo nie zatrzymuje reszty
                continue
    save(d, journals)
    st = stats(journals, now_s, strats)
    bb._atomic_write(os.path.join(d, STATS), json.dumps(st, ensure_ascii=False))
    return n, st


def read(d):
    return bb._read_json(os.path.join(d, STATS))
