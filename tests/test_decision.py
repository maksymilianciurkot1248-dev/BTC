import sys, math, random, time
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_analysis as ba, btc_decision as dc, btc_edge as be
random.seed(11)
def series(n, drift, vol, start=100.0, volu=1000.0, t0=1_500_000_000_000):
    p, out = start, []
    for i in range(n):
        o = p; p *= math.exp(random.gauss(drift, vol)); h = max(o, p) * (1 + abs(random.gauss(0, vol / 3))); l = min(o, p) * (1 - abs(random.gauss(0, vol / 3)))
        out.append([t0 + i * 86400000, o, h, l, p, volu * (1 + abs(random.gauss(0, .4)))])
    return out
up, down, flat = series(500, 0.004, 0.015), series(500, -0.004, 0.015), series(500, 0.0, 0.012)
def run(candles, **kw):
    ind = ba.build_indicators(candles); eng = dc.Engine(**kw); plans = [eng.plan_at(ind, t, 1.0) for t in range(dc.WARMUP, len(candles))]
    return ind, eng, plans
for name, cs, want in (("up", up, "long"), ("down", down, "short")):
    ind, eng, plans = run(cs)
    acts = [p["action"] for p in plans]; assert want in acts and acts.count(want) > acts.count("long" if want == "short" else "short"), (name, {a: acts.count(a) for a in set(acts)})
    for p in plans:
        if p["action"] == "wait": assert p["entry"] is None and p["decision"]["reasons"]; continue
        sg = 1 if p["action"] == "long" else -1
        assert sg * (p["stop"] - p["entry"]) < 0 < sg * (p["tp1"] - p["entry"]) < sg * (p["tp2"] - p["entry"]) and p["valid_until_ms"] > p["close_ms"], p
        assert 0.2 <= p["size_mult"] <= 1.0 and p["confidence"] >= dc.MIN_CONF and p["risk_px_pct"] <= dc.MAX_RISK_PX_PCT and p["entry_type"] == "limit" and p["decision"]["families"]
    print(name, {a: acts.count(a) for a in set(acts)})
# brak podglądania przyszłości: decyzja z dnia t jest taka sama po dopisaniu późniejszych świec
t = 400; i1, e1, _ = run(up[:t + 1]); i2 = ba.build_indicators(up); e2 = dc.Engine()
a, b = e1.plan_at(i1, t), e2.plan_at(i2, t); assert a["action"] == b["action"] and a["entry"] == b["entry"] and a["decision"]["score"] == b["decision"]["score"]
# wagi sumują się do 1 w każdym reżimie
assert all(abs(sum(w.values()) - 1) < 1e-9 for w in dc.WEIGHTS.values())
# weto na dzień publikacji makro i filtr BTC
ind = ba.build_indicators(up); base = dc.Engine(); t = next(t for t in range(dc.WARMUP, len(up)) if base.plan_at(ind, t)["action"] == "long")
ev = [(up[t][0] + 86400000) / 1000 + 3600]; p = dc.Engine(events_s=ev).plan_at(ind, t); assert p["action"] == "wait" and any("publikacji" in v for v in p["decision"]["vetoes"])
btc = series(500, -0.004, 0.015, t0=up[0][0]); alt = dc.Engine(symbol="SOLUSDT", btc_candles=btc)
t2 = next(t for t in range(dc.WARMUP, len(up)) if alt.decide(ind, t)["S"] > 0.3 and alt.btc_down[alt._btc_idx(up[t][0])])
assert any("BTC pod SMA50" in n for n in alt.plan_at(ind, t2)["decision"]["notes"])
nobtc = dc.Engine(symbol="SOLUSDT", btc_candles=[x[:] for x in btc[:40]])       # za krótka historia BTC: brak filtra
assert not any("BTC pod" in n for n in nobtc.plan_at(ind, t2)["decision"]["notes"])
# funding: skrajny blokuje, wysoki zmniejsza
assert dc.Engine(overlay={"funding": 0.0015}).plan_at(ind, t)["action"] == "wait"
pf = dc.Engine(overlay={"funding": 0.0008}).plan_at(ind, t); pb = base.plan_at(ind, t); assert pf["action"] == "long" and pf["size_mult"] < pb["size_mult"]
# test wsteczny przez ten sam dziennik co klasyczny plan (koszty, TP1/TP2, czas)
eng = dc.Engine(); props = be.backtest(up, planner=eng.plan_at)
assert props and all(p["r"] is not None and p["outcome"] in ba.PLAN_FILLED for p in props), len(props or [])
rs = [p["r"] for p in props]; print("backtest trend wzrostowy: transakcji", len(rs), "średnio R", round(sum(rs) / len(rs), 3))
fl = be.backtest(flat, planner=dc.Engine().plan_at); print("backtest boczny: transakcji", len(fl or []), "średnio R", round(sum(p["r"] for p in fl) / len(fl), 3) if fl else None)
# plan przechodzi przez decorate_plan jak klasyczny
pl = dc.Engine().plan_at(ind, t); ba.decorate_plan(pl, pl["price"]); assert "live_state" in pl
print("decision ok")
