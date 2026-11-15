"""Pobiera z OKX historię danych o przepływie (OI, wolumen agresywny, long/short) i świece, do badań. Cache w plikach JSON."""
import json, os, sys, time, urllib.request
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"); os.makedirs(D, exist_ok=True)
SYMS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "LTC"]
last = [0.0]
def get(u, gap):
    for k in range(6):
        w = last[0] + gap - time.time()
        if w > 0: time.sleep(w)
        last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20) as r:
                d = json.load(r)
            if d.get("code") == "0": return d.get("data") or []
            if d.get("code") in ("50011",): time.sleep(2 + k); continue
            return []
        except Exception as e:
            time.sleep(1.5 + k)
    return []
def series(name, path, inst, per, days, gap=0.45, key="period"):
    fn = os.path.join(D, f"{name}_{inst}_{per}.json")
    if os.path.exists(fn): return
    out, end, lo = {}, "", int((time.time() - days * 86400) * 1000)
    while True:
        rows = get(f"https://www.okx.com/api/v5/{path}?instId={inst}&{key}={per}&limit=100" + (f"&{'after' if key == 'bar' else 'end'}={end}" if end else ""), gap)
        if not rows: break
        for r in rows: out[int(r[0])] = [float(x) if x not in ("", None) else None for x in r[1:6]]
        end = min(int(r[0]) for r in rows)
        if end <= lo or len(rows) < 100: break
    json.dump(sorted([[k] + v for k, v in out.items()]), open(fn, "w"))
    print(name, inst, per, len(out), flush=True)
for per, days in (("1H", 62), ("1D", 400), ("5m", 5)):
    bar = {"1H": "1H", "1D": "1Dutc", "5m": "5m"}[per]
    for s in SYMS:
        sw = f"{s}-USDT-SWAP"
        series("px", "market/history-candles", sw, bar, days, 0.12, "bar")
        series("oi", "rubik/stat/contracts/open-interest-history", sw, per, days)
        series("tk", "rubik/stat/taker-volume-contract", sw, per, days)
        series("ls", "rubik/stat/contracts/long-short-account-ratio-contract", sw, per, days)
        series("lst", "rubik/stat/contracts/long-short-account-ratio-contract-top-trader", sw, per, days)
        series("lsp", "rubik/stat/contracts/long-short-position-ratio-contract-top-trader", sw, per, days)
        if per != "5m":
            series("mk", "market/history-mark-price-candles", sw, bar, days, 0.12, "bar")
            series("ix", "market/history-index-candles", f"{s}-USDT", bar, days, 0.12, "bar")
# funding (8 h)
for s in SYMS:
    fn = os.path.join(D, f"fr_{s}.json")
    if os.path.exists(fn): continue
    out, after = {}, ""
    for _ in range(14):
        rows = get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={s}-USDT-SWAP&limit=100" + (f"&after={after}" if after else ""), 0.25)
        if not rows: break
        for r in rows: out[int(r["fundingTime"])] = float(r.get("realizedRate") or r["fundingRate"])
        after = min(int(r["fundingTime"]) for r in rows)
    json.dump(sorted(out.items()), open(fn, "w")); print("fr", s, len(out), flush=True)
print("DONE", flush=True)
