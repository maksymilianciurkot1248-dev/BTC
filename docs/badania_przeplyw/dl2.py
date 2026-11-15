import json, os, time, urllib.request
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "long"); os.makedirs(D, exist_ok=True)
SYMS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]
last = [0.0]
def get(u, gap=0.11):
    for k in range(6):
        w = last[0] + gap - time.time()
        if w > 0: time.sleep(w)
        last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20) as r: d = json.load(r)
            if d.get("code") == "0": return d.get("data") or []
            time.sleep(1.5 + k)
        except Exception: time.sleep(1.5 + k)
    return []
def series(name, path, inst, days):
    fn = os.path.join(D, f"{name}_{inst}.json")
    if os.path.exists(fn): return
    out, after, lo = {}, "", int((time.time() - days * 86400) * 1000)
    while True:
        rows = get(f"https://www.okx.com/api/v5/{path}?instId={inst}&bar=1H&limit=100" + (f"&after={after}" if after else ""))
        if not rows: break
        for r in rows: out[int(r[0])] = [float(x) for x in r[1:5]]
        after = min(int(r[0]) for r in rows)
        if after <= lo or len(rows) < 100: break
    json.dump(sorted([[k] + v for k, v in out.items()]), open(fn, "w")); print(name, inst, len(out), flush=True)
for s in SYMS:
    series("px", "market/history-candles", f"{s}-USDT-SWAP", 760)
    series("mk", "market/history-mark-price-candles", f"{s}-USDT-SWAP", 760)
    series("ix", "market/history-index-candles", f"{s}-USDT", 760)
print("DONE", flush=True)
