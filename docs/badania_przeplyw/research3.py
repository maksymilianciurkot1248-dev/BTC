"""Wskaźnik łączny jako strategia ze stopem: symulacja na świecach godzinowych, wynik w R po kosztach; podział na połowy i aktywa."""
import json, os, sys, math, io, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
with contextlib.redirect_stdout(io.StringIO()):
    import research as R
import btc_lab as lab
PARTS = [("tk4_z", 1), ("ls_z", 1), ("prem_z", -1), ("ret4", -1)]
H = 3600_000
def bars(s): return {r[0]: r[1:] for r in json.load(open(os.path.join(R.D, f"px_{s}-USDT-SWAP_1H.json")))}
def sim(theta, hold, k_atr, tp_mult, lo=None, hi=None, syms=None, min_stop=0.006):
    out = []
    for s_, rows in R.BY.items():
        if syms and s_ not in syms: continue
        px = bars(s_); ts_all = sorted(px); pos = {t: i for i, t in enumerate(ts_all)}
        busy = -1
        for i, r in enumerate(rows):
            t = r[0]
            if i < 170 or (lo is not None and t < lo) or (hi is not None and t >= hi) or t <= busy: continue
            xs = [sg * R.Z[s_][k][i] for k, sg in PARTS if R.Z[s_][k][i] is not None]
            if len(xs) < len(PARTS): continue
            c = sum(xs) / len(xs)
            if abs(c) < theta: continue
            j = pos[t]
            if j < 15 or j + hold >= len(ts_all): continue
            atr = sum(max(px[ts_all[q]][1] - px[ts_all[q]][2], abs(px[ts_all[q]][1] - px[ts_all[q - 1]][3]), abs(px[ts_all[q]][2] - px[ts_all[q - 1]][3])) for q in range(j - 13, j + 1)) / 14
            e = px[t][3]; sg = 1 if c > 0 else -1
            sd = max(k_atr * atr, min_stop * e); stop = e - sg * sd; tp = e + sg * sd * tp_mult if tp_mult else None
            cost = 0.0013 * e / sd; res = None
            for q in range(j + 1, j + hold + 1):
                o, h, l, cl = px[ts_all[q]][:4]
                if (sg == 1 and l <= stop) or (sg == -1 and h >= stop): res = -1 - cost; break
                if tp and ((sg == 1 and h >= tp) or (sg == -1 and l <= tp)): res = tp_mult - cost; break
            if res is None: res = (px[ts_all[j + hold]][3] - e) * sg / sd - cost; q = j + hold
            out.append((ts_all[q], s_, sg, res, sd / e)); busy = ts_all[q]
    return out
def summ(tr):
    if not tr: return "brak"
    rs = [x[3] for x in tr]; blk = {}
    for x in tr: blk.setdefault(x[0] // (2 * 86400_000), []).append(x[3])
    m, t, p, n = lab.t_test([sum(v) for v in blk.values()])
    return f"n={len(rs):3d} śr {sum(rs)/len(rs):+.3f}R trafność {sum(1 for r in rs if r > 0)/len(rs)*100:4.1f}% suma {sum(rs):+6.1f}R stop śr {sum(x[4] for x in tr)/len(tr)*100:.2f}% t {t:+.1f}"
mid = R.mid
print("aktywa:", sorted(R.BY))
for theta in (1.0, 1.25):
    for hold in (12, 24):
        for k in (2.0, 3.0, 4.0):
            for tp in (None, 2.0):
                a, b = sim(theta, hold, k, tp, hi=mid), sim(theta, hold, k, tp, lo=mid)
                print(f"próg {theta} trzymanie {hold:>2}h stop {k}xATR cel {tp or 'brak':>4}: H1 {summ(a)} | H2 {summ(b)}")
print("\nWedług aktywa (próg 1.0, 12 h, stop 3xATR, bez celu), cały okres:")
for s_ in sorted(R.BY): print(f"  {s_:5}", summ(sim(1.0, 12, 3.0, None, syms=[s_])))
print("\nLong i short osobno (ta sama konfiguracja):")
al = sim(1.0, 12, 3.0, None); print("  long ", summ([x for x in al if x[2] > 0])); print("  short", summ([x for x in al if x[2] < 0]))
