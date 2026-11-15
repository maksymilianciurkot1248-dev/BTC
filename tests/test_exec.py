import sys, os, time, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_botcfg as bb
# --- walidacja próśb
ok = bb.validate_order({"type": "open", "symbol": "solusdt", "side": "long", "notional": "20", "stop_pct": 3, "tp_pct": 6})
assert ok == {"type": "open", "symbol": "SOLUSDT", "side": "long", "order_type": "market", "notional": 20.0, "stop_pct": 3.0, "tp_pct": 6.0}, ok
assert bb.validate_order({"type": "close", "symbol": "BTCUSDT"}) == {"type": "close", "symbol": "BTCUSDT"}
assert bb.validate_order({"type": "open", "symbol": "BTCUSDT", "side": "short", "notional": 50, "stop_pct": 2, "order_type": "limit", "price": 80000})["price"] == 80000
for bad in ({"type": "open", "symbol": "SOLUSDT", "side": "long", "notional": 20},                       # brak stop-lossa
            {"type": "open", "symbol": "SOLUSDT", "side": "long", "notional": 20, "stop_pct": 0},
            {"type": "open", "symbol": "SOLUSDT", "side": "long", "notional": 1, "stop_pct": 3},
            {"type": "open", "symbol": "SOLUSDT", "side": "long", "notional": 2e6, "stop_pct": 3},
            {"type": "open", "symbol": "SOLUSDT", "side": "flat", "notional": 20, "stop_pct": 3},
            {"type": "open", "symbol": "SOL", "side": "long", "notional": 20, "stop_pct": 3},
            {"type": "open", "symbol": "SOLUSDT", "side": "long", "notional": 20, "stop_pct": 3, "order_type": "limit"},      # limit bez ceny
            {"type": "open", "symbol": "SOLUSDT", "side": "long", "notional": 20, "stop_pct": 3, "leverage": 50},
            {"type": "close", "symbol": "SOLUSDT", "qty": 1}, {"type": "x"}, [], {"type": "open", "symbol": "SOLUSDT", "side": "long", "notional": float("nan"), "stop_pct": 3}):
    try: bb.validate_order(bad); raise SystemExit(f"powinno odrzucić {bad}")
    except ValueError: pass
d = tempfile.mkdtemp()
ids = [bb.add_order(d, ok) for _ in range(10)]
try: bb.add_order(d, ok); raise SystemExit("kolejka powinna mieć limit")
except ValueError: pass
assert all(bb.ORDER_ID_RE.match(i) for i in ids)
# --- wykonawca z udawanym Bybit
import btc_exec as ex, btc_trader as t
os.environ.update(TRADER_MODE="demo", TRADER_API_KEY="k", TRADER_API_SECRET="s", TRADER_WEB_ORDERS="yes", TRADER_MAX_LEVERAGE="1")
def mk(positions=(), orders=(), equity=1000.0):
    calls = []
    class FA:
        def __init__(s, c): pass
        def ok(s, m, path, params=None, allow=()):
            calls.append((m, path, dict(params or {})))
            if path == "/v5/account/wallet-balance": return {"list": [{"totalEquity": str(equity)}]}
            if path == "/v5/position/list": return {"list": list(positions)}
            if path == "/v5/order/realtime": return {"list": list(orders)}
            if path in ("/v5/order/create", ): return {"orderId": "OID"}
            return {}
    return FA, calls
class Pub:
    turn = {"SOLUSDT": 5e9, "TINYUSDT": 1e6}
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": str(s.turn.get(sym, 5e9))} if sym != "NOPEUSDT" else {}
    def _request(s, path, params): return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1"}, "priceFilter": {"tickSize": "0.1"}}]}
def run(req, positions=(), orders=(), equity=1000.0, mode="demo", env=None, stop=False):
    d = tempfile.mkdtemp(); t.HERE = d; ex.HERE = d
    if stop: open(os.path.join(d, "STOP"), "w").close()
    FA, calls = mk(positions, orders, equity); t.Api = FA; t.make_pub = lambda c: Pub()
    os.environ["TRADER_MODE"] = mode
    for k, v in (env or {}).items(): os.environ[k] = v
    oid = bb.add_order(d, req)
    c = t.cfg(); bb.apply_settings(c, bb.read_settings(d)); c["max_capital"] = 1000.0
    ex.process(d, c, t.Api(c) if mode != "dryrun" else None, Pub())
    o = [x for x in bb.read_orders(d) if x["id"] == oid][0]
    return o, [x for x in calls if x[1] == "/v5/order/create"], calls
OPEN = {"type": "open", "symbol": "SOLUSDT", "side": "long", "order_type": "market", "notional": 50.0, "stop_pct": 3.0, "tp_pct": 6.0}
o, cr, calls = run(OPEN)
assert o["status"] == "done" and len(cr) == 1, o
p = cr[0][2]; assert p["side"] == "Buy" and p["orderType"] == "Market" and p["stopLoss"] == "97.0" and p["takeProfit"] == "106.0" and p["qty"] == "0.5" and p["orderLinkId"] == o["id"], p
assert [x for x in calls if x[1] == "/v5/position/set-leverage"][0][2]["buyLeverage"] == "1"
o, cr, _ = run(dict(OPEN, side="short", order_type="limit", price=110.0, tp_pct=None) if False else {**OPEN, "side": "short", "order_type": "limit", "price": 110.0}); p = cr[0][2]
assert p["side"] == "Sell" and p["orderType"] == "Limit" and p["price"] == "110.0" and p["stopLoss"] == "113.3" and p["takeProfit"] == "103.4", p
# odrzucenia
assert run(OPEN, env={"TRADER_WEB_ORDERS": "no"})[0]["status"] == "rejected"
env_ok = {"TRADER_WEB_ORDERS": "yes"}
assert "STOP" in run(OPEN, stop=True, env=env_ok)[0]["result"]
o, cr, _ = run({**OPEN, "notional": 5000.0}); assert o["status"] == "rejected" and "wolny limit dźwigni" in o["result"] and not cr     # nigdy cicho nie zmniejszamy zlecenia
o, cr, _ = run({**OPEN, "notional": 900.0, "stop_pct": 40.0}); assert o["status"] == "rejected" and "ryzyko" in o["result"] and not cr          # ryzyko 360 USDT > 5% kapitału
pos = [{"symbol": "ETHUSDT", "size": "1", "side": "Buy", "positionValue": "980"}]
o, cr, _ = run({**OPEN, "notional": 50.0}, positions=pos); assert o["status"] == "rejected" and "limit dźwigni" in o["result"] and not cr           # brak wolnego limitu
o, cr, _ = run(OPEN, positions=[{"symbol": "SOLUSDT", "size": "1", "side": "Buy", "positionValue": "100"}]); assert o["status"] == "rejected" and "już pozycja" in o["result"]
assert run({**OPEN, "symbol": "TINYUSDT"})[0]["result"].startswith("za mała płynność")
assert "nieznany symbol" in run({**OPEN, "symbol": "NOPEUSDT"})[0]["result"]
assert "poniżej minimum" in run({**OPEN, "notional": 5.0})[0]["result"]
# zamknięcie pozycji działa także przy pliku STOP
o, cr, _ = run({"type": "close", "symbol": "SOLUSDT"}, positions=[{"symbol": "SOLUSDT", "size": "2.5", "side": "Buy", "positionValue": "250"}], stop=True)
assert o["status"] == "done" and cr[0][2]["reduceOnly"] is True and cr[0][2]["side"] == "Sell" and cr[0][2]["qty"] == "2.5", (o, cr)
assert run({"type": "close", "symbol": "SOLUSDT"})[0]["status"] == "rejected"
# dryrun: cała ścieżka bez wysyłania
o, cr, _ = run(OPEN, mode="dryrun"); assert o["status"] == "done" and "DRYRUN" in o["result"] and not cr
# stare zlecenie w kolejce wygasa
d = tempfile.mkdtemp(); oid = bb.add_order(d, OPEN, now=time.time() - 700); c = t.cfg(); ex.process(d, c, None, Pub()); assert bb.read_orders(d)[0]["status"] == "rejected"
print("exec ok")
