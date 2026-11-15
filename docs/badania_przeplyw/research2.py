"""1) Wskaźnik łączny z danych o przepływie: test poza próbą (znaki z 1. połowy, wynik w 2. połowie), po kosztach.
   2) Czy te dane poprawiają strategie scalpera (powrót do średniej, wybicie) jako filtr."""
import json, os, sys, math, io, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
with contextlib.redirect_stdout(io.StringIO()):
    import research as R
import btc_scalp as sc, btc_lab as lab
COST = 13.0                                   # pb: wejście i wyjście rynkowe + poślizg
H = 3600_000
def comp(f, parts):
    xs = [sg * f[k] for k, sg in parts if f.get(k) is not None]
    return sum(xs) / len(xs) if len(xs) == len(parts) else None
PARTS = {"A: tk4+ls-prem-ret4": [("tk4_z", 1), ("ls_z", 1), ("prem_z", -1), ("ret4", -1)],
         "B: tk4+ls": [("tk4_z", 1), ("ls_z", 1)],
         "C: ls": [("ls_z", 1)],
         "D: tk4-ret4 (absorpcja)": [("tk4_minus_ret", 1)],
         "E: -ret4-ret24 (samo odwrócenie)": [("ret4", -1), ("ret24", -1)]}
mid = R.mid
def trades(parts, theta, h, lo=None, hi=None):
    out = []
    for s_, rows in R.BY.items():
        busy = -1
        for i, r in enumerate(rows):
            t = r[0]
            if i < 170 or (lo is not None and t < lo) or (hi is not None and t >= hi) or t < busy or r[3][h] is None: continue
            c = comp({k: R.Z[s_][k][i] for k in R.FEATS}, parts)
            if c is None or abs(c) < theta: continue
            sg = 1 if c > 0 else -1
            out.append((t, s_, sg, sg * r[3][h] * 1e4 - COST)); busy = t + h * H
    return out
def summ(tr):
    if not tr: return "brak"
    wk = {}
    for t, _, _, r in tr: wk.setdefault(t // (86400_000 * 2), []).append(r)
    m, tt, p, n = lab.t_test([sum(v) for v in wk.values()])
    rs = [x[3] for x in tr]; L = [x[3] for x in tr if x[2] > 0]; S_ = [x[3] for x in tr if x[2] < 0]
    return f"n={len(rs):4d} śr {sum(rs)/len(rs):+6.1f} pb, trafność {sum(1 for r in rs if r > 0)/len(rs)*100:4.1f}%, long {len(L)}: {sum(L)/max(1,len(L)):+6.1f}, short {len(S_)}: {sum(S_)/max(1,len(S_)):+6.1f}, t(2-dniowe) {tt:+.1f}"
print("Wskaźnik łączny: średni wynik NETTO (po kosztach 13 pb) na transakcję; 1. połowa = dopasowanie znaków, 2. połowa = poza próbą")
for name, parts in PARTS.items():
    for h in (4, 12, 24):
        for th in (0.75, 1.25):
            print(f"{name:34} h={h:>2} próg {th}: H1 {summ(trades(parts, th, h, hi=mid))}\n{'':51}H2 {summ(trades(parts, th, h, lo=mid))}")
# ---------- 2) filtr dla strategii scalpera
OLD = os.environ.get("SCALP_5M_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "scalp5m"))     # świece 5 min: {SYMBOL}-USDT-SWAP.json
rows = []
for s_ in ["BTC", "ETH", "SOL", "XRP", "DOGE"]:
    p = os.path.join(OLD, f"{s_}-USDT-SWAP.json")
    if not os.path.exists(p) or s_ not in R.BY: continue
    bars = json.load(open(p)); idx = {r[0]: i for i, r in enumerate(R.BY[s_])}
    open_ = {}
    for i, bar in enumerate(bars):
        for k in list(open_):
            r = sc.step_shadow(open_[k], bar)
            if r is not None:
                t = open_.pop(k); rows.append((t["ts"], k, s_, t["side"], r))
        for k, plan in sc.signals(bars, i).items():
            if k not in open_: open_[k] = sc.open_shadow(k, s_, bar, plan)
def feat_at(s_, entry_s, k):
    t = ((entry_s + 300) * 1000 - H) // H * H
    i = {r[0]: j for j, r in enumerate(R.BY[s_])}.get(t) if False else IDX[s_].get(t)
    return R.Z[s_][k][i] if i is not None else None
IDX = {s_: {r[0]: j for j, r in enumerate(v)} for s_, v in R.BY.items()}
print(f"\nStrategie scalpera (5 min, 30 dni): {len(rows)} transakcji. Średni wynik w R po kosztach, zależnie od danych o przepływie w chwili wejścia")
print("(cecha liczona w kierunku transakcji: dodatnia = dane sprzyjają kierunkowi pozycji)")
for strat in ("mr", "bo"):
    base = [r for r in rows if r[1] == strat]
    print(f"{strat}: wszystkie n={len(base)} śr {sum(r[4] for r in base)/len(base):+.3f}R")
    for k in ("tk4_z", "tk1_z", "ls_z", "prem_z", "ret4", "ret24", "oi4_z", "lsp_z", "tk4_minus_ret"):
        v = [(feat_at(r[2], r[0], k), r) for r in base]; v = [(x * r[3], r) for x, r in v if x is not None]
        if len(v) < 100: continue
        v.sort(key=lambda z: z[0]); n3 = len(v) // 3
        parts_ = [v[:n3], v[n3:2 * n3], v[2 * n3:]]
        print(f"   {k:14} przeciw {sum(r[4] for _, r in parts_[0])/n3:+.3f}R | neutralnie {sum(r[4] for _, r in parts_[1])/n3:+.3f}R | zgodnie {sum(r[4] for _, r in parts_[2])/n3:+.3f}R   (po {n3})")
