#!/usr/bin/env python3
"""Odtworzenie pracy bramki scalpera na historii świec 5 min (strategie mr i bo; ob i fl nie mają historii wejść).

Pytanie: czy bramka (i tryb rozpoznania) wpuszcza do handlu strategie, które potem tracą?
Dla każdej świecy, w kolejności czasu: cień jak w btc_scalp.feed, potem statystyki i bramka dokładnie tak, jak liczy je bot.
Transakcja z cienia otwarta w chwili, gdy strategia miała pozwolenie, jest przybliżeniem transakcji prawdziwej.

Użycie: python3 replay.py KATALOG_Z_DANYMI [--dump TRADES.json]   (pliki BTC-USDT-SWAP.json itd.: [[ts_s, o, h, l, c, obrót], ...], świece 5 min)
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import btc_scalp as sc  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]


def run(data_dir, rule, warm_days=10):
    bars = {s + "USDT": json.load(open(os.path.join(data_dir, s + "-USDT-SWAP.json"))) for s in SYMS}
    idx = {s: {b[0]: i for i, b in enumerate(v)} for s, v in bars.items()}
    times = sorted({b[0] for v in bars.values() for b in v})
    t_live = times[0] + warm_days * 86400            # jak w bocie: pierwsze dni to rozbieg (sygnałów z rozbiegu nie handlujemy)
    trades, opened, tagged, mem = [], {}, {}, {}
    taken, active = [], {"bramka": 0, "rozpoznanie": 0, "brak": 0}
    for t in times:
        now, fresh = t + sc.BAR_S, []
        for sym, v in bars.items():
            i = idx[sym].get(t)
            if i is None:
                continue
            bar = v[i]
            for strat in ("mr", "bo"):
                k = strat + ":" + sym
                tr = opened.get(k)
                if tr:
                    r = sc.step_shadow(tr, bar)
                    if r is not None:
                        trades.append([now, strat, sym, tr["side"], round(r, 3)])
                        if k in tagged:
                            taken.append((now, strat, sym, tagged.pop(k), round(r, 3)))
                        del opened[k]
            for strat, plan in sc.signals(v, i).items():
                k = strat + ":" + sym
                if k not in opened:
                    opened[k] = sc.open_shadow(strat, sym, bar, plan)
                    fresh.append((strat, sym, k))
        if t < t_live:
            continue
        st = sc.stats(trades, now)
        allowed, how = rule(st, mem)
        active[how] += 1
        for strat, sym, k in fresh:
            if strat in allowed and not sc.vetoed(st, strat, sym):
                tagged[k] = (how, allowed[strat])
    return trades, taken, active, (times[-1] - t_live) / 86400


def rule_old(st, mem):
    """Przed 15.11: rozpoznanie najlepszą strategią bez względu na wynik."""
    ok = {s: 1.0 for s, v in st.items() if v["n"] >= 30 and v["mean_r"] >= 0.05}
    if ok:
        return ok, "bramka"
    cand = [(v["mean_r"], s) for s, v in st.items() if v["n"] >= 10]
    return ({max(cand)[1]: 0.5}, "rozpoznanie") if cand else ({}, "brak")


def rule_now(st, mem):
    return sc.gate(st, True, mem)


def show(name, res):
    trades, taken, active, days = res
    tot = sum(active.values())
    print(f"\n== {name} ({days:.1f} dni po rozbiegu)")
    print("   czas: " + ", ".join(f"{k} {v / tot * 100:.1f}%" for k, v in active.items()))
    for how in ("bramka", "rozpoznanie"):
        rs = [x[4] for x in taken if x[3][0] == how]
        if rs:
            m = sum(rs) / len(rs)
            sd = math.sqrt(sum((r - m) ** 2 for r in rs) / len(rs))
            print(f"   transakcje przez „{how}”: {len(rs)}, średnio {m:+.3f}R, suma {sum(rs):+.1f}R, ważone ryzykiem {sum(x[4] * x[3][1] for x in taken if x[3][0] == how):+.1f}R (t {m / sd * math.sqrt(len(rs)) if sd else 0:+.1f})")
        else:
            print(f"   transakcje przez „{how}”: 0")
    for s in ("mr", "bo"):
        rs = [x[4] for x in trades if x[1] == s]
        print(f"   cień {s}: {len(rs)} transakcji, średnio {sum(rs) / len(rs):+.3f}R")


if __name__ == "__main__":
    d = sys.argv[1]
    res = run(d, rule_old)
    show("reguła sprzed 15.11 (rozpoznanie zawsze najlepszą)", res)
    show("reguła obecna (btc_scalp.gate)", run(d, rule_now))
    if "--dump" in sys.argv:
        json.dump(res[0], open(sys.argv[sys.argv.index("--dump") + 1], "w"))
