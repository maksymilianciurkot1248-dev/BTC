import sys, os, time, math, random, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as t, btc_botcfg as bb, btc_analysis as ba, btc_decision as dc
random.seed(11)
now_day = int(time.time() * 1000) // 86400000 * 86400000
def series(n, drift, vol, start=100.0):
    p, out = start, []
    for i in range(n):
        o = p; p *= math.exp(random.gauss(drift, vol)); h = max(o, p) * 1.004; l = min(o, p) * 0.996
        out.append([now_day - (n - i) * 86400000, o, h, l, p, 1000.0 * (1 + abs(random.gauss(0, .4)))])
    return out
UP = series(500, 0.004, 0.015)
class Pub:
    def klines(s, sym, tf, n, end=None): return UP
    def ticker(s, sym): return {"lastPrice": str(UP[-1][4]), "turnover24h": "5e9", "fundingRate": "0.0001"}
    def _request(s, path, params): return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"}, "priceFilter": {"tickSize": "0.01"}}]}
# get_plan: zespół vs klasyczny
c = {"mode": "dryrun", "risk_pct": 1.0, "strategy": "ensemble"}
plan, price, _ = t.get_plan(c, "SOLUSDT", Pub(), UP, {"events": []})
assert plan["strategy"] == "ensemble" and plan["decision"]["families"] and "live_state" in plan or plan["action"] == "wait", plan.get("action")
c["strategy"] = "plan"; p2, _, _ = t.get_plan(c, "SOLUSDT", Pub()); assert "decision" not in p2
# pełny przebieg bota z zespołem sygnałów (dryrun): decyzje trafiają do statusu
os.environ.update(TRADER_MODE="dryrun"); t.single_instance = lambda: None; t.load_env = lambda: None; t.make_pub = lambda c: Pub()
d = tempfile.mkdtemp(); t.HERE = d; t.LOGBUF.clear(); bb.write_settings(d, {"strategy": "ensemble", "symbols": ["SOLUSDT"], "max_orders_day": 3})
t.run(); st = bb.read_status(d)
assert st["strategy"] == "ensemble" and "SOLUSDT" in st["decisions"] and st["decisions"]["SOLUSDT"]["families"], st.get("decisions")
print("działanie:", st["decisions"]["SOLUSDT"]["action"], "ocena", st["decisions"]["SOLUSDT"]["score"], "| plan:", st["plans"].get("SOLUSDT", {}).get("action"))
# walidacja ustawienia
assert bb.validate({"strategy": "ensemble"}) == {"strategy": "ensemble"}
for bad in ("zly", None, 3):
    try: bb.validate({"strategy": bad}); raise SystemExit("powinno odrzucić")
    except ValueError: pass
# wybór aktywów według strategii
import btc_edge as be
edge = {"ts": time.time(), "allowed": ["ETHUSDT"], "allowed_ens": ["XRPUSDT"], "assets": {"ETHUSDT": {"filters": [{"flag": "btc_down"}]}}, "assets_ens": {"XRPUSDT": {"filters": [{"flag": "near_event"}]}}}
assert be.select_symbols(edge, ["BTCUSDT"], {}, time.time(), True, strategy="plan")[0] == ["BTCUSDT", "ETHUSDT"]
assert be.select_symbols(edge, ["BTCUSDT"], {}, time.time(), True, strategy="ensemble")[0] == ["BTCUSDT", "XRPUSDT"]
assert be.blocked_by_filters(edge, "XRPUSDT", {"near_event": True}, "ensemble") == ["near_event"] and be.blocked_by_filters(edge, "XRPUSDT", {"near_event": True}, "plan") == []
print("ensemble bot ok")
