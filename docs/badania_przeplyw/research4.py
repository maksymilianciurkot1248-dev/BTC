"""Ten sam pomysł na danych DZIENNYCH (do 500 dni): czy wolumen agresywny, long/short, premia i odwrócenie poprzedzają ruch następnego dnia."""
import json, os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import btc_lab as lab
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"); DAY = 86400_000
SYMS = [s for s in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "LTC"] if os.path.exists(os.path.join(D, f"ix_{s}-USDT_1Dutc.json"))]
def load(name, inst, per): 
    p = os.path.join(D, f"{name}_{inst}_{per}.json"); return {r[0] // DAY: r[1:] for r in json.load(open(p))} if os.path.exists(p) else {}
def zs(v, i, n):
    w = [x for x in v[max(0, i - n + 1):i + 1] if x is not None]
    if len(w) < n * 0.6 or v[i] is None: return None
    m = sum(w) / len(w); sd = math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
    return max(-3, min(3, (v[i] - m) / sd)) if sd > 0 else None
rows = []
for s in SYMS:
    px, tk, ls, lsp, oi, mk, ix = load("px", f"{s}-USDT-SWAP", "1Dutc"), load("tk", f"{s}-USDT-SWAP", "1D"), load("ls", f"{s}-USDT-SWAP", "1D"), load("lsp", f"{s}-USDT-SWAP", "1D"), load("oi", f"{s}-USDT-SWAP", "1D"), load("mk", f"{s}-USDT-SWAP", "1Dutc"), load("ix", f"{s}-USDT", "1Dutc")
    days = sorted(d for d in px if d in tk and d in ls)
    c = [px[d][3] for d in days]
    tkr = [tk[d][1] / (tk[d][0] + tk[d][1]) - 0.5 if tk[d][0] + tk[d][1] > 0 else None for d in days]
    lsr = [math.log(ls[d][0]) if ls[d][0] and ls[d][0] > 0 else None for d in days]
    prem = [(mk[d][3] / ix[d][3] - 1) * 1e4 if d in mk and d in ix and ix[d][3] else None for d in days]
    ret = [None] + [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
    oic = [oi[d][1] if d in oi else None for d in days]
    oich = [None] * 3 + [(oic[i] / oic[i - 3] - 1) if oic[i] and oic[i - 3] else None for i in range(3, len(c))]
    N = 30
    for i in range(N + 5, len(days) - 3):
        if days[i + 3] - days[i] != 3: continue
        f = {"tk": zs(tkr, i, N), "ls": zs(lsr, i, N), "prem": zs(prem, i, N), "ret1": zs(ret, i, N), "oi3": zs(oich, i, N)}
        rows.append((days[i], s, f, {1: c[i + 1] / c[i] - 1, 3: c[i + 3] / c[i] - 1}))
print("aktywa", SYMS, "obserwacje", len(rows), "dni", max(r[0] for r in rows) - min(r[0] for r in rows))
mu = {s: {h: sum(r[3][h] for r in rows if r[1] == s) / sum(1 for r in rows if r[1] == s) for h in (1, 3)} for s in SYMS}
def timing(fn, h, lo=None, hi=None, blk=7):
    g = {}
    for d, s, f, fw in rows:
        x = fn(f)
        if x is None or (lo is not None and d < lo) or (hi is not None and d >= hi): continue
        g.setdefault(d // blk, []).append(x * (fw[h] - mu[s][h]))
    m, t, p, n = lab.t_test([sum(v) / len(v) for v in g.values() if len(v) >= 5]); return m * 1e4, t, n
mid = sorted(r[0] for r in rows)[len(rows) // 2]
def A(f):
    xs = [f["tk"], f["ls"], None if f["prem"] is None else -f["prem"], None if f["ret1"] is None else -f["ret1"]]
    return sum(xs) / 4 if all(x is not None for x in xs) else None
tests = {"tk (wolumen agresywny)": lambda f: f["tk"], "ls (konta long/short)": lambda f: f["ls"], "prem (premia)": lambda f: f["prem"], "ret1 (ruch dnia)": lambda f: f["ret1"], "oi3 (zmiana OI 3 dni)": lambda f: f["oi3"], "ŁĄCZNY tk+ls-prem-ret1": A}
print(f"{'cecha':28} h  pb/z      t  tyg.   1. połowa / 2. połowa")
for name, fn in tests.items():
    for h in (1, 3):
        m, t, n = timing(fn, h); a = timing(fn, h, hi=mid); b = timing(fn, h, lo=mid)
        print(f"{name:28} {h} {m:+7.1f} {t:+6.2f} {n:5d}   {a[0]:+.1f} (t {a[1]:+.1f}) / {b[0]:+.1f} (t {b[1]:+.1f})")
# strategia dzienna: wejście na zamknięciu dnia, trzymanie 1 dzień, koszt 13 pb
for th in (0.75, 1.0, 1.25):
    tr = [(d, (1 if A(f) > 0 else -1) * fw[1] * 1e4 - 13, 1 if A(f) > 0 else -1) for d, s, f, fw in rows if A(f) is not None and abs(A(f)) >= th]
    wk = {}
    for d, r, _ in tr: wk.setdefault(d // 7, []).append(r)
    m, t, p, n = lab.t_test([sum(v) for v in wk.values()])
    L = [r for _, r, sg in tr if sg > 0]; S_ = [r for _, r, sg in tr if sg < 0]
    h1 = [r for d, r, _ in tr if d < mid]; h2 = [r for d, r, _ in tr if d >= mid]
    print(f"próg {th}: n={len(tr)} śr netto {sum(r for _, r, _ in tr)/len(tr):+.1f} pb, trafność {sum(1 for _, r, _ in tr if r > 0)/len(tr)*100:.1f}%, long {len(L)}: {sum(L)/max(1,len(L)):+.1f}, short {len(S_)}: {sum(S_)/max(1,len(S_)):+.1f}, t tyg. {t:+.1f}; 1. połowa {sum(h1)/max(1,len(h1)):+.1f} ({len(h1)}), 2. połowa {sum(h2)/max(1,len(h2)):+.1f} ({len(h2)})")
