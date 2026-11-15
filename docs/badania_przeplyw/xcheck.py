import json, os, sys, io, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
with contextlib.redirect_stdout(io.StringIO()):
    import research as R
import btc_flow as bf
D = R.D
def load(name, inst): return {r[0] // 1000: r[1:] for r in json.load(open(os.path.join(D, f"{name}_{inst}_1H.json")))}
worst = 0; n = 0
for s in ["BTC", "SOL", "DOGE"]:
    px, mk, ix, tk, ls = load("px", f"{s}-USDT-SWAP"), load("mk", f"{s}-USDT-SWAP"), load("ix", f"{s}-USDT"), load("tk", f"{s}-USDT-SWAP"), load("ls", f"{s}-USDT-SWAP")
    rows = {}
    for t in px:
        rows[t] = [px[t][3], mk.get(t, [None] * 4)[3], ix.get(t, [None] * 4)[3], tk.get(t, [None, None])[1], tk.get(t, [None, None])[0], ls.get(t, [None])[0], None, None]
    ser = {x["t"] - 3600: x for x in bf.series(rows)}
    for i, r in enumerate(R.BY[s]):
        if i < 400: continue
        z = {k: R.Z[s][k][i] for k in ("tk4_z", "ls_z", "prem_z", "ret4")}
        if any(v is None for v in z.values()): continue
        ref = (z["tk4_z"] + z["ls_z"] - z["prem_z"] - z["ret4"]) / 4
        got = ser[r[0] // 1000]["score"]
        if got is None: print("brak", s, r[0]); continue
        worst = max(worst, abs(ref - got)); n += 1
print("porównane godziny:", n, "największa różnica wskaźnika:", round(worst, 4))
