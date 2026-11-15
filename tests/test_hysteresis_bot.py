import sys, os, json, tempfile, time
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_botcfg as bb
import btc_trader as t

os.environ.update(TRADER_MODE="demo", TRADER_API_KEY="k", TRADER_API_SECRET="s", TRADER_KEY_CREATED=time.strftime("%Y-%m-%d"))
os.environ.pop("TRADER_AUTO_LIVE", None)
t.single_instance = lambda: None
t.load_env = lambda: None

def mk(positions=(), orders=(), equity=1000.0):
    calls = []
    class FakeApi:
        def __init__(s, c): pass
        def ok(s, m, path, params=None, allow=()):
            calls.append((m, path, dict(params or {})))
            if path == "/v5/position/closed-pnl":
                return {"list": [{"closedPnl": "5", "updatedTime": str(int(time.time() * 1000))}], "nextPageCursor": ""}
            if path == "/v5/account/wallet-balance": return {"list": [{"totalEquity": str(equity)}]}
            if path == "/v5/position/list": return {"list": list(positions)}
            if path == "/v5/order/realtime": return {"list": list(orders)}
            if path == "/v5/order/create": return {"orderId": "OID"}
            if path in ("/v5/position/set-leverage", "/v5/order/cancel"): return {}
            raise AssertionError(path)
    return FakeApi, calls

class P2:
    def klines(s, sym, tf, n, end=None):
        now = int(time.time() * 1000) // 86400000 * 86400000
        return [[now - (120 - i) * 86400000, 100, 101, 99, (100.0 if i < 60 else 100.0 - (i - 60) * 0.5), 1.0] for i in range(120)]
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": "5e9"}
    def _request(s, path, params):
        return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"}, "priceFilter": {"tickSize": "0.1"}}]}

PLAN = lambda a="long": {"action": a, "setup": "x", "entry": 100, "stop": 95, "tp1": 110, "date": "2026-11-08", "valid_until_ms": 2**60, "live_state": "waiting"}
t.make_pub = lambda c: P2()

def go(settings, edge=None):
    d = tempfile.mkdtemp(); t.HERE = d; t.LOGBUF.clear()
    bb.write_settings(d, {"max_heat_pct": 10.0, **settings})
    if edge: bb.write_edge(d, edge)
    FA, calls = mk(); t.Api = FA
    t.get_plan = lambda c, sym=None, pub=None, *a, **k: (PLAN(), 100.0, P2())
    t.run()
    return d, calls

def creates(calls): return [x[2] for x in calls if x[1] == "/v5/order/create" and not x[2].get("reduceOnly")]
def edge_map(held):
    return {"ts": time.time(), "version": 2, "allowed": ["BTCUSDT"], "assets": {"BTCUSDT": {"status": "allowed", "held": held, "filters": []}}, "events": []}

# 1) rozmiar z ryzyka: aktywo utrzymane przez histerezę dostaje o połowę mniejszą pozycję
_, calls = go({"symbols": ["BTCUSDT"], "risk_pct": 0.2, "max_lev": 2.0}, edge_map(False)); base_qty = float(creates(calls)[0]["qty"])
d, calls = go({"symbols": ["BTCUSDT"], "risk_pct": 0.2, "max_lev": 2.0}, edge_map(True)); held_qty = float(creates(calls)[0]["qty"])
assert base_qty > 0 and abs(held_qty - base_qty * 0.5) <= 0.0101, (base_qty, held_qty)
assert any("utrzymane przez histerezę" in e["msg"] and "50%" in e["msg"] for e in bb.read_events(d)), bb.read_events(d)
# 2) stała wartość pozycji: też o połowę (15 USDT -> 7,5 USDT = 0,07 szt. przy kroku 0,01)
_, calls = go({"symbols": ["BTCUSDT"], "fixed_notional": 15.0, "max_lev": 1.0}, edge_map(False)); assert float(creates(calls)[0]["qty"]) == 0.15
_, calls = go({"symbols": ["BTCUSDT"], "fixed_notional": 15.0, "max_lev": 1.0}, edge_map(True)); assert float(creates(calls)[0]["qty"]) == 0.07, creates(calls)
# 3) poniżej minimum giełdy po zmniejszeniu: pominięte z wyjaśnieniem, bez zlecenia
d, calls = go({"symbols": ["BTCUSDT"], "fixed_notional": 1.5, "max_lev": 1.0}, edge_map(True))
assert not creates(calls) and any("minimum giełdy" in e["msg"] for e in bb.read_events(d))
# 4) stara mapa przewagi (bez pola held) albo brak mapy: bez zmiany rozmiaru
legacy = {"ts": time.time(), "allowed": ["BTCUSDT"], "assets": {"BTCUSDT": {"status": "allowed", "filters": []}}}
_, calls = go({"symbols": ["BTCUSDT"], "fixed_notional": 15.0, "max_lev": 1.0}, legacy); assert float(creates(calls)[0]["qty"]) == 0.15
_, calls = go({"symbols": ["BTCUSDT"], "fixed_notional": 15.0, "max_lev": 1.0}); assert float(creates(calls)[0]["qty"]) == 0.15
# 5) status: lista aktywów utrzymanych widoczna w panelu (automat włączony, mapa świeża); aktywo ręczne ETHUSDT bez adnotacji dostaje pełny rozmiar
by = lambda calls: {x["symbol"]: float(x["qty"]) for x in creates(calls)}
d, calls = go({"symbols": ["ETHUSDT"], "auto_select": True, "fixed_notional": 15.0, "max_lev": 1.0, "max_positions": 3, "max_orders_day": 5}, edge_map(True))
st = bb.read_status(d); assert st["auto"]["allowed"] == ["BTCUSDT"] and st["auto"]["held"] == ["BTCUSDT"], st["auto"]
assert by(calls) == {"ETHUSDT": 0.15, "BTCUSDT": 0.07}, by(calls)
d, calls = go({"symbols": ["ETHUSDT"], "auto_select": True, "fixed_notional": 15.0, "max_lev": 1.0, "max_positions": 3, "max_orders_day": 5}, edge_map(False))
assert bb.read_status(d)["auto"]["held"] == [] and by(calls) == {"ETHUSDT": 0.15, "BTCUSDT": 0.15}, by(calls)
print("hysteresis bot ok")
