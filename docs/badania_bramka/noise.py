#!/usr/bin/env python3
"""Jak często bramka scalpera przepuszcza przypadek: stara reguła (sama średnia) kontra obecna (średnia + t z btc_scalp.tstat).

Część 1: prawdziwe transakcje z cienia strategii mr i bo (obie tracą), okna kroczące różnej długości.
Część 2: strategie o zadanej prawdziwej średniej (losowanie ze zwracaniem z wyników mr przesuniętych do tej średniej; czasy co 2 h,
         czyli bez skupisk: to górna granica mocy bramki).

Użycie: python3 noise.py TRADES.json     (lista [czas_zamknięcia_s, strategia, aktywo, strona, R], np. z replay.py --dump)
"""
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import btc_scalp as sc  # noqa: E402


def passes(rows):
    """(stara reguła, obecna reguła) dla okna rows = [(ts, r)]."""
    n = len(rows)
    m = sum(r for _, r in rows) / n
    return (n >= 30 and m >= 0.05), (n >= sc.GATE_MIN_N and m >= sc.GATE_MIN_MEAN and sc.tstat(rows) >= sc.GATE_MIN_T)


def main(path):
    trades = json.load(open(path))
    print("Część 1: prawdziwe wyniki z cienia (30 dni, 5 aktywów)")
    for s in ("mr", "bo"):
        rows = sorted((t[0], t[4]) for t in trades if t[1] == s)
        m = sum(r for _, r in rows) / len(rows)
        print(f"  {s}: {len(rows)} transakcji, średnia {m:+.3f}R, t (z poprawką na skupiska) {sc.tstat(rows):+.1f}")
        for k in (30, 50, 100, 150, 300):
            n = old = new = 0
            for i in range(k, len(rows), max(1, k // 10)):
                a, b = passes(rows[i - k:i])
                n, old, new = n + 1, old + a, new + b
            print(f"     okno {k:3} transakcji: stara reguła przepuszcza {old / n * 100:5.1f}% okien, obecna {new / n * 100:5.1f}%   (okien: {n})")
    print("Część 2: strategie o znanej prawdziwej średniej (4000 losowań)")
    random.seed(7)
    base = [t[4] for t in trades if t[1] == "mr"]
    m0 = sum(base) / len(base)
    print(f"  odchylenie standardowe wyniku jednej transakcji: {math.sqrt(sum((r - m0) ** 2 for r in base) / len(base)):.2f}R")
    for target in (-0.10, 0.0, 0.10, 0.20, 0.30):
        pool = [r - m0 + target for r in base]
        for k in (30, 150, 300):
            old = new = 0
            for _ in range(4000):
                a, b = passes([(i * 7200, random.choice(pool)) for i in range(k)])
                old, new = old + a, new + b
            print(f"  prawdziwa średnia {target:+.2f}R, okno {k:3}: stara reguła {old / 40:5.1f}%, obecna {new / 40:5.1f}%")


if __name__ == "__main__":
    main(sys.argv[1])
