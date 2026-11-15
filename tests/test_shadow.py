import sys, os, math, random, tempfile, time
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_shadow as sh, btc_edge as be, btc_analysis as ba, btc_collect as bc, btc_botcfg as bb, btc_decision as dc, btc_lab as lab
random.seed(21)
def series(n, drift, vol, t0=1_500_000_000):
    p, out = 100.0, []
    for i in range(n):
        o = p; p *= math.exp(random.gauss(drift, vol)); h = max(o, p) * 1.006; l = min(o, p) * 0.994
        out.append((t0 + i * 86400, o, h, l, p, 1000.0 * (1 + abs(random.gauss(0, .4)))))
    return out
UP, FL = series(420, 0.004, 0.015), series(420, 0.0, 0.012)
d = tempfile.mkdtemp(); db = bc.DB(os.path.join(d, "s.db"))
N0 = ba.PLAN_WARMUP            # od tego dnia dziennik prowadzimy codziennie
for sym, ser in (("BTCUSDT", UP), ("SOLUSDT", FL)):
    db.put_candles(be.key(sym), "1d", ser[:N0], "t")
# dzień po dniu, jak codzienna nauka: dopisujemy świecę i wykonujemy krok
for i in range(N0, 420):
    for sym, ser in (("BTCUSDT", UP), ("SOLUSDT", FL)): db.put_candles(be.key(sym), "1d", ser[i:i + 1], "t")
    n, st = sh.run(d, db, ["BTCUSDT", "SOLUSDT"], now_s=1_500_000_000 + i * 86400)
    if i == N0 + 5: sh.run(d, db, ["BTCUSDT", "SOLUSDT"])            # drugi raz tego samego dnia: bez zmian
STR = sh.strategies(d); assert [k for k, _ in STR] == ['plan', 'ensemble', 'v2', 'v1n'], STR      # plan klasyczny + wszystkie warianty laboratorium (v1 pod dawnym kluczem)
assert n == 2 * len(STR), n
# zgodność z testem wstecznym: dziennik prowadzony na żywo = backtest tego samego okresu (bez kosztów różnych i bez podglądania przyszłości)
def key_trades(props): return [(p["side"], round(p["r"], 6)) for p in sorted(props, key=lambda p: p["closed_ms"])]
for strategy, sym in (("plan", "BTCUSDT"), ("ensemble", "BTCUSDT"), ("v2", "SOLUSDT"), ("v1n", "SOLUSDT"), ("ensemble", "SOLUSDT")):
    live = [p for p in sh.load(d)[f"{strategy}:{sym}"]["proposals"] if p.get("outcome") in ba.PLAN_FILLED and p.get("r") is not None]
    candles = be.load_candles(db, sym)
    btc = be.load_candles(db, "BTCUSDT"); planner = ba.plan_at if strategy == "plan" else lab.engine("v1" if strategy == "ensemble" else strategy, sym, btc, []).plan_at
    # backtest od tego samego dnia startowego: dziennik zaczyna się w N0-1, więc porównujemy transakcje, które powstały od tego dnia
    ind = ba.build_indicators(candles); j = ba.new_journal("bt")
    for t in range(N0, len(candles)):
        ba.journal_step(j, planner(ind, t, 1.0), candles[:t + 1], ba.DAY_MS, candles[t][0] + ba.DAY_MS)
    ref = [p for p in j["proposals"] if p.get("outcome") in ba.PLAN_FILLED and p.get("r") is not None]
    assert key_trades(live) == key_trades(ref) and (len(live) > 0 or sym != "BTCUSDT"), (strategy, sym, len(live), len(ref))
    print(strategy, sym, "transakcji na żywo = backtest:", len(live))
st = sh.read(d); assert st["strategies"]["plan"]["n"] > 0 and st["strategies"]["ensemble"]["name"] == "Zespół v1" and set(st["strategies"]) == {"plan", "ensemble", "v2", "v1n"}
for s in st["strategies"].values():
    assert sum(x["n"] for x in s["by_symbol"]) == s["n"] and (s["n"] == 0 or (s["curve"] and abs(s["curve"][-1][1] - s["total_r"]) < 1e-6))
# stary sposób wywołania (bez rejestru i gotowych wskaźników) nadal działa
jj = {}; assert sh.step(jj, "ensemble", "SOLUSDT", be.load_candles(db, "SOLUSDT"), be.load_candles(db, "BTCUSDT"), []) and "ensemble:SOLUSDT" in jj
assert sh.stats(jj)["strategies"].keys() == {"plan", "ensemble"}
# aktywa bez historii są pomijane
n, _ = sh.run(d, db, ["NOPEUSDT"]); assert n == 0
# paczka zapisywania: dziennik nie rośnie bez granic
import json; assert len(json.dumps(sh.load(d))) < 3_000_000
print("shadow ok", {k: (v["n"], v["mean_r"]) for k, v in st["strategies"].items()})
