"""Długa historia (2 lata, świece godzinowe): czy składniki „−premia” i „−ruch 4 h” poprzedzają ruch ceny. To jedyne dwa składniki wskaźnika z długą historią."""
import json, os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import btc_lab as lab, btc_flow as bf
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "long"); H = 3600
SYMS = [s for s in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"] if os.path.exists(os.path.join(D, f"ix_{s}-USDT.json"))]
def load(n, inst): return {r[0] // 1000: r[4] for r in json.load(open(os.path.join(D, f"{n}_{inst}.json")))}
P = []          # (t, sym, z_prem, z_ret4, fwd4, fwd12, fwd24)
for s in SYMS:
    px, mk, ix = load("px", f"{s}-USDT-SWAP"), load("mk", f"{s}-USDT-SWAP"), load("ix", f"{s}-USDT")
    rows = {t: [px[t], mk.get(t), ix.get(t), None, None, None, None, None] for t in px}
    ser = bf.series(rows)
    mu = {}
    for h in (4, 12, 24):
        v = [ser[i + h]["c"] / ser[i]["c"] - 1 for i in range(len(ser) - h) if ser[i]["c"] and ser[i + h]["c"]]; mu[h] = sum(v) / len(v)
    for i, x in enumerate(ser):
        if i + 24 >= len(ser) or x["z"]["prem"] is None or x["z"]["ret4"] is None or not x["c"] or not all(ser[i + h]["c"] for h in (4, 12, 24)): continue
        P.append((x["t"], s, x["z"]["prem"], x["z"]["ret4"], *[ser[i + h]["c"] / x["c"] - 1 - mu[h] for h in (4, 12, 24)], *[ser[i + h]["c"] / x["c"] - 1 for h in (4, 12, 24)]))
t0, t1 = min(p[0] for p in P), max(p[0] for p in P)
print("aktywa", SYMS, "obserwacje", len(P), "dni", round((t1 - t0) / 86400))
def timing(fn, hi_, lo=None, hi=None):
    h = {4: 4, 12: 5, 24: 6}[hi_]; blk = max(1, math.ceil(2 * hi_ / 24)) * 86400; g = {}
    for p in P:
        if (lo and p[0] < lo) or (hi and p[0] >= hi): continue
        g.setdefault(p[0] // blk, []).append(fn(p) * p[h])
    m, t, pv, n = lab.t_test([sum(v) / len(v) for v in g.values()]); return m * 1e4, t, n
qs = [t0 + (t1 - t0) * k / 4 for k in range(5)]
print(f"{'składnik':22} h   pb/z      t bloki | po ćwiartkach okresu (pb/z)")
for name, fn in (("−premia", lambda p: -p[2]), ("−ruch 4 h", lambda p: -p[3]), ("oba (średnia)", lambda p: (-p[2] - p[3]) / 2)):
    for h in (4, 12, 24):
        m, t, n = timing(fn, h); q = [timing(fn, h, qs[k], qs[k + 1])[0] for k in range(4)]
        print(f"{name:22} {h:>2} {m:+6.2f} {t:+6.2f} {n:5d} | " + "  ".join(f"{x:+6.1f}" for x in q))
# strategia z samych dwóch składników: |średnia| >= próg, 12 h, bez nakładania, koszt 13 pb
for th in (1.0, 1.5, 2.0):
    tr = []; busy = {}
    for p in sorted(P):
        c = (-p[2] - p[3]) / 2
        if abs(c) < th or p[0] < busy.get(p[1], 0): continue
        sg = 1 if c > 0 else -1; tr.append((p[0], sg, sg * p[8] * 1e4 - 13)); busy[p[1]] = p[0] + 12 * H
    wk = {}
    for t, _, r in tr: wk.setdefault(t // (7 * 86400), []).append(r)
    m, tt, pv, n = lab.t_test([sum(v) for v in wk.values()])
    L = [r for _, sg, r in tr if sg > 0]; S_ = [r for _, sg, r in tr if sg < 0]
    print(f"próg {th}: n={len(tr)} śr netto {sum(r for *_, r in tr)/len(tr):+.1f} pb, trafność {sum(1 for *_, r in tr if r > 0)/len(tr)*100:.1f}%, long {len(L)}: {sum(L)/max(1,len(L)):+.1f}, short {len(S_)}: {sum(S_)/max(1,len(S_)):+.1f}, t tygodniowe {tt:+.2f} ({n} tyg.)")
