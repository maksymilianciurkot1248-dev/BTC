"""Czy dane o przepływie (OI, wolumen agresywny, long/short, premia, funding) poprzedzają ruch ceny? Dane godzinowe OKX, 60 dni, 10 aktywów."""
import json, os, sys, math, statistics
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import btc_lab as lab
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SYMS = [s for s in ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "LTC"] if os.path.exists(os.path.join(D, f"ix_{s}-USDT_1H.json"))]
H = 3600_000
def load(name, s, per="1H", idx=False):
    inst = f"{s}-USDT" if idx else f"{s}-USDT-SWAP"
    p = os.path.join(D, f"{name}_{inst}_{per}.json")
    return {r[0]: r[1:] for r in json.load(open(p))} if os.path.exists(p) else {}
def zs(vals, i, n):
    w = [v for v in vals[max(0, i - n + 1):i + 1] if v is not None]
    if len(w) < n // 2 or vals[i] is None: return None
    m = sum(w) / len(w); sd = math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
    return (vals[i] - m) / sd if sd > 0 else None
def build(s):
    px, oi, tk, ls, lst, lsp, mk = (load(n, s) for n in ("px", "oi", "tk", "ls", "lst", "lsp", "mk")); ix = load("ix", s, idx=True)
    fr = sorted(json.load(open(os.path.join(D, f"fr_{s}.json")))) if os.path.exists(os.path.join(D, f"fr_{s}.json")) else []
    ts = sorted(t for t in px if t in oi and t in tk and t in ls)
    ts = [t for i, t in enumerate(ts)]
    c = [px[t][3] for t in ts]; n = len(ts)
    oic = [oi[t][1] for t in ts]                           # OI w monetach
    buy = [tk[t][1] for t in ts]; sell = [tk[t][0] for t in ts]
    lsr = [math.log(ls[t][0]) if ls[t][0] and ls[t][0] > 0 else None for t in ts]
    lstr = [math.log(lst[t][0]) if t in lst and lst[t][0] and lst[t][0] > 0 else None for t in ts]
    lspr = [math.log(lsp[t][0]) if t in lsp and lsp[t][0] and lsp[t][0] > 0 else None for t in ts]
    prem = [(mk[t][3] / ix[t][3] - 1) * 1e4 if t in mk and t in ix and ix[t][3] else None for t in ts]
    def frate(t):
        v = None
        for ft, r in fr:
            if ft <= t: v = r
            else: break
        return v
    fund = [frate(t) for t in ts]
    rows = []
    def chg(v, i, k): return (v[i] / v[i - k] - 1) if i >= k and v[i - k] and v[i] is not None and v[i - k] is not None else None
    def tkr(i, k):
        if i < k - 1: return None
        b, s_ = sum(buy[i - k + 1:i + 1]), sum(sell[i - k + 1:i + 1])
        return b / (b + s_) - 0.5 if b + s_ > 0 else None
    tk1 = [tkr(i, 1) for i in range(n)]; tk4 = [tkr(i, 4) for i in range(n)]; tk24 = [tkr(i, 24) for i in range(n)]
    oi1 = [chg(oic, i, 1) for i in range(n)]; oi4 = [chg(oic, i, 4) for i in range(n)]; oi24 = [chg(oic, i, 24) for i in range(n)]
    r1 = [chg(c, i, 1) for i in range(n)]; r4 = [chg(c, i, 4) for i in range(n)]; r24 = [chg(c, i, 24) for i in range(n)]
    for i in range(n):
        if i < 170 or ts[i] - ts[i - 24] != 24 * H: continue
        f = {"ret1": r1[i], "ret4": r4[i], "ret24": r24[i],
             "oi1": oi1[i], "oi4": oi4[i], "oi24": oi24[i],
             "oi4_z": zs(oi4, i, 168), "oi24_z": zs(oi24, i, 168),
             "tk1": tk1[i], "tk4": tk4[i], "tk24": tk24[i], "tk4_z": zs(tk4, i, 168), "tk1_z": zs(tk1, i, 168),
             "ls_z": zs(lsr, i, 168), "ls_chg24": (lsr[i] - lsr[i - 24]) if lsr[i] is not None and lsr[i - 24] is not None else None, "ls_chg4": (lsr[i] - lsr[i - 4]) if lsr[i] is not None and lsr[i - 4] is not None else None,
             "lst_z": zs(lstr, i, 168), "lsp_z": zs(lspr, i, 168), "lsp_chg24": (lspr[i] - lspr[i - 24]) if lspr[i] is not None and lspr[i - 24] is not None else None,
             "smart_minus_crowd": (zs(lspr, i, 168) - zs(lsr, i, 168)) if zs(lspr, i, 168) is not None and zs(lsr, i, 168) is not None else None,
             "prem": prem[i], "prem_z": zs(prem, i, 168), "fund": fund[i], "fund_z": zs(fund, i, 168),
             # interakcje: OI rośnie razem z ceną (nowe pozycje w kierunku ruchu) / OI spada przy spadku (wymuszone zamknięcia longów)
             "oi4_x_ret4": (oi4[i] * (1 if r4[i] > 0 else -1)) if oi4[i] is not None and r4[i] is not None else None,
             "flush": (-oi4[i] * (1 if r4[i] < 0 else -1)) if oi4[i] is not None and r4[i] is not None and oi4[i] < 0 else None,
             "tk4_minus_ret": None}
        if f["tk4_z"] is not None and r4[i] is not None:
            f["tk4_minus_ret"] = f["tk4_z"] - (zs(r4, i, 168) or 0)          # agresywne kupno bez wzrostu ceny = absorpcja przez zlecenia z limitem
        fw = {h: (c[i + h] / c[i] - 1) if i + h < n and ts[i + h] - ts[i] == h * H else None for h in (1, 4, 12, 24)}
        rows.append((ts[i], s, f, fw))
    return rows
ALL = [r for s in SYMS for r in build(s)]
print("aktywa", len(SYMS), "obserwacje", len(ALL), "dni", round((max(r[0] for r in ALL) - min(r[0] for r in ALL)) / 86400e3, 1))
FEATS = list(ALL[0][2].keys())
import numpy as np
# wszystkie cechy jako z-score z okna wstecznego 168 h (bez informacji z przyszłości), obcięte do +-3
def trailing_z(rows_sym, feat):
    v = [r[2][feat] for r in rows_sym]; out = []
    for i in range(len(v)):
        if feat.endswith("_z") or feat in ("smart_minus_crowd", "tk4_minus_ret"): out.append(v[i]); continue
        out.append(zs(v, i, 168))
    return [None if x is None else max(-3.0, min(3.0, x)) for x in out]
Z = {}                                    # (sym) -> {feat: [z]}, wiersze w kolejności czasu
BY = {}
for r in ALL: BY.setdefault(r[1], []).append(r)
for s_, rows in BY.items():
    rows.sort(key=lambda r: r[0]); Z[s_] = {f: trailing_z(rows, f) for f in FEATS}
def panel(h):
    """Wiersze: (blok, {cecha: z}, zwrot w przód minus średnia aktywa)."""
    out = []
    blk = max(1, math.ceil(2 * h / 24)) * 86400_000
    for s_, rows in BY.items():
        fws = [r[3][h] for r in rows]; mu = sum(x for x in fws if x is not None) / max(1, sum(1 for x in fws if x is not None))
        for i, r in enumerate(rows):
            if i < 170 or fws[i] is None: continue          # rozbieg na z-score
            out.append((r[0] // blk, {f: Z[s_][f][i] for f in FEATS}, fws[i] - mu, r[0]))
    return out
def timing(feat, h, P=None, lo=None, hi=None):
    """Średni zwrot w kierunku sygnału: z * zwrot, uśredniony w blokach czasu; test t po blokach. Wynik w pb na jednostkę z."""
    P = P or panel(h); g = {}
    for b, f, y, t in P:
        if f[feat] is None or (lo is not None and t < lo) or (hi is not None and t >= hi): continue
        g.setdefault(b, []).append(f[feat] * y)
    xs = [sum(v) / len(v) for v in g.values() if len(v) >= 10]
    m, t, p, n = lab.t_test(xs)
    return m * 1e4, t, 2 * min(p, 1 - p), n
res = []
PAN = {h: panel(h) for h in (1, 4, 12, 24)}
for feat in FEATS:
    for h in (1, 4, 12, 24):
        m, t, p, n = timing(feat, h, PAN[h]); res.append((feat, h, m, t, p, n))
order = sorted(range(len(res)), key=lambda i: res[i][4]); adj = [1.0] * len(res); prev = 1.0
for rk in range(len(res), 0, -1):
    i = order[rk - 1]; prev = min(prev, res[i][4] * len(res) / rk); adj[i] = prev
mid = sorted(r[0] for r in ALL)[len(ALL) // 2]
print(f"{'cecha':18} {'h':>3} {'pb/z':>7} {'t':>6} {'p_adj':>6} {'bloki':>5}  pb/z w 1. połowie / w 2. połowie")
for i in sorted(range(len(res)), key=lambda i: -abs(res[i][3])):
    feat, h, m, t, p, n = res[i]
    if abs(t) < 2.0: continue
    a = timing(feat, h, PAN[h], hi=mid); b = timing(feat, h, PAN[h], lo=mid)
    print(f"{feat:18} {h:>3} {m:+7.2f} {t:+6.2f} {adj[i]:6.3f} {n:5d}  {a[0]:+.2f} (t {a[1]:+.1f}) / {b[0]:+.2f} (t {b[1]:+.1f})")
# regresja łączna: czy dane o przepływie dodają coś ponad odwrócenie ceny (błędy grupowane po blokach)
def ols(h, feats):
    feats = [k for k in feats if sum(1 for _, f, _, _ in PAN[h] if f[k] is not None) > 1000]
    P = [(b, f, y) for b, f, y, _ in PAN[h] if all(f[k] is not None for k in feats)]
    X = np.array([[1.0] + [f[k] for k in feats] for _, f, _ in P]); y = np.array([yy for _, _, yy in P]) * 1e4
    beta, *_ = np.linalg.lstsq(X, y, rcond=None); e = y - X @ beta
    XtXi = np.linalg.inv(X.T @ X); meat = np.zeros((X.shape[1], X.shape[1]))
    bl = np.array([b for b, _, _ in P])
    for b in np.unique(bl):
        idx = bl == b; g = X[idx].T @ e[idx]; meat += np.outer(g, g)
    V = XtXi @ meat @ XtXi; se = np.sqrt(np.diag(V))
    r2 = 1 - (e @ e) / ((y - y.mean()) @ (y - y.mean()))
    return [(k, beta[j + 1], beta[j + 1] / se[j + 1]) for j, k in enumerate(feats)], r2, len(P)
for h in (4, 12, 24):
    feats = ["ret1", "ret4", "ret24", "tk4_z", "tk24", "oi4_z", "oi24_z", "ls_z", "ls_chg24", "lsp_z", "lst_z", "prem_z", "fund_z"]
    co, r2, n = ols(h, feats)
    print(f"\nRegresja łączna, horyzont {h} h (n={n}, R2={r2:.4f}): współczynnik w pb na 1 odch. std., t grupowane")
    print("  " + "  ".join(f"{k} {b:+.1f} (t {t:+.1f})" for k, b, t in co))
