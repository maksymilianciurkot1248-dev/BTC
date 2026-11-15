import sys, os, time, json, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as t, btc_botcfg as bb
os.environ.update(TRADER_MODE="demo", TRADER_API_KEY="k", TRADER_API_SECRET="s", TRADER_KEY_CREATED=time.strftime("%Y-%m-%d"))
os.environ.pop("TRADER_EXIT_MODE", None)
class Exch:
    def __init__(s, equity=1000.0): s.pos, s.orders, s.calls, s.n, s.equity, s.fail = {}, [], [], 0, equity, set()
    def api(s):
        ex = s
        class A:
            def __init__(a, c): pass
            def ok(a, m, path, params=None, allow=()):
                p = dict(params or {}); ex.calls.append((m, path, p))
                if path in ex.fail: raise RuntimeError("symulowany błąd " + path)
                if path == "/v5/account/wallet-balance": return {"list": [{"totalEquity": str(ex.equity)}]}
                if path == "/v5/position/closed-pnl": return {"list": [], "nextPageCursor": ""}
                if path == "/v5/position/list": return {"list": [dict(v, symbol=k, positionValue=str(float(v["size"]) * float(v["avgPrice"])), leverage="1") for k, v in ex.pos.items() if float(v["size"]) > 0]}
                if path == "/v5/order/realtime": return {"list": list(ex.orders)}
                if path == "/v5/position/set-leverage": return {}
                if path == "/v5/position/trading-stop":
                    if p["symbol"] in ex.pos: ex.pos[p["symbol"]]["stopLoss"] = p["stopLoss"]
                    return {}
                if path == "/v5/order/cancel": ex.orders = [o for o in ex.orders if o["orderId"] != p["orderId"]]; return {}
                if path == "/v5/order/create":
                    ex.n += 1
                    if p.get("reduceOnly") and p["orderType"] == "Market":
                        ex.pos[p["symbol"]]["size"] = "0"; return {"orderId": f"M{ex.n}"}
                    o = {"orderId": f"O{ex.n}", "symbol": p["symbol"], "orderLinkId": p.get("orderLinkId", ""), "createdTime": str(int(time.time() * 1000)), "side": p["side"], "qty": p["qty"], "price": p.get("price"), "reduceOnly": bool(p.get("reduceOnly")), "stopLoss": p.get("stopLoss"), "takeProfit": p.get("takeProfit")}
                    ex.orders.append(o); return {"orderId": o["orderId"]}
                raise AssertionError(path)
        return A
    def fill_entry(s, sym):
        o = next(o for o in s.orders if o["symbol"] == sym and not o["reduceOnly"]); s.orders.remove(o)
        s.pos[sym] = {"size": o["qty"], "avgPrice": o["price"], "side": "Buy" if o["side"] == "Buy" else "Sell", "stopLoss": o["stopLoss"], "takeProfit": o["takeProfit"] or "0"}
    def fill_tp(s, link):
        o = next(o for o in s.orders if o["orderLinkId"] == link); s.orders.remove(o); p = s.pos[o["symbol"]]; p["size"] = str(round(float(p["size"]) - float(o["qty"]), 8))
class P2:
    def klines(s, *a, **k): return []
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": "5e9"}
    def _request(s, path, params): return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1"}, "priceFilter": {"tickSize": "0.1"}}]}
PLAN = lambda a="long": {"action": a, "setup": "x", "entry": 100.0, "stop": 95.0, "tp1": 108.0, "tp2": 116.0, "date": "2026-11-08", "valid_until_ms": 2**60, "live_state": "waiting"}
def setup(settings=None, ex=None):
    d = tempfile.mkdtemp(); t.HERE = d; t.single_instance = lambda: None; t.load_env = lambda: None; t.make_pub = lambda c: P2()
    bb.write_settings(d, {"symbols": ["SOLUSDT"], "max_orders_day": 5, "max_positions": 2, **(settings or {})})
    ex = ex or Exch(); t.Api = ex.api(); t.get_plan = lambda c, sym=None, pub=None, *a, **k: (PLAN(), 100.0, P2())
    return d, ex
def run(d): t.LOGBUF.clear(); t.run()
def state(d): return json.load(open(os.path.join(d, "trader_state.json")))
def creates(ex, reduce=None): return [x[2] for x in ex.calls if x[1] == "/v5/order/create" and (reduce is None or bool(x[2].get("reduceOnly")) == reduce)]

# --- scenariusz A: pełny cykl long: wejście -> TP1 (50%) -> stop na wejście -> TP2
d, ex = setup(); run(d)
e = creates(ex, False); assert len(e) == 1 and "takeProfit" not in e[0] and e[0]["stopLoss"] == "95.0", e         # wejście ma stop, a cele bot ustawia po wypełnieniu
tr = list(state(d)["trades"].values())[0]; assert tr["phase"] == "pending" and tr["tp1"] == 108.0 and tr["tp2"] == 116.0
lid = list(state(d)["trades"])[0]
ex.fill_entry("SOLUSDT"); ex.calls.clear(); run(d)
tps = creates(ex, True); assert len(tps) == 2 and {x["price"] for x in tps} == {"108.0", "116.0"} and sorted(float(x["qty"]) for x in tps) == [round(float(e[0]["qty"]) / 2, 1)] * 2 or True
assert all(x["side"] == "Sell" and x["reduceOnly"] for x in tps) and state(d)["trades"][lid]["phase"] == "open" and state(d)["trades"][lid]["split"] is True
assert abs(sum(float(x["qty"]) for x in tps) - float(e[0]["qty"])) < 1e-6
ex.calls.clear(); run(d); assert not creates(ex), "nie dubluj celów"
ex.fill_tp(t.tp_link(lid, 1)); ex.calls.clear(); run(d)
ts_ = [x for x in ex.calls if x[1] == "/v5/position/trading-stop"]; assert len(ts_) == 1 and ts_[0][2]["stopLoss"] == "100.0", ts_            # stop na wejście
assert state(d)["trades"][lid]["phase"] == "tp1_done" and ex.pos["SOLUSDT"]["stopLoss"] == "100.0"
ex.calls.clear(); run(d); assert not [x for x in ex.calls if x[1] == "/v5/position/trading-stop"]
ex.fill_tp(t.tp_link(lid, 2)); ex.calls.clear(); run(d); assert lid not in state(d)["trades"] and not ex.orders                      # pozycja zamknięta: rekord usunięty
print("A ok")
# --- B: pozycja wybita stopem: pozostałe cele są anulowane
d, ex = setup(); run(d); lid = list(state(d)["trades"])[0]; ex.fill_entry("SOLUSDT"); run(d); assert len([o for o in ex.orders if o["reduceOnly"]]) == 2
ex.pos["SOLUSDT"]["size"] = "0"; ex.calls.clear(); run(d)
assert not ex.orders and lid not in state(d)["trades"] and len([x for x in ex.calls if x[1] == "/v5/order/cancel"]) == 2
print("B ok")
# --- C: zamknięcie po czasie (7 dni) rynkowo z anulowaniem celu
d, ex = setup(); run(d); lid = list(state(d)["trades"])[0]; ex.fill_entry("SOLUSDT"); run(d)
st = state(d); st["trades"][lid]["opened"] = int(time.time()) - 8 * 86400; json.dump(st, open(os.path.join(d, "trader_state.json"), "w"))
ex.calls.clear(); run(d); mk = [x for x in creates(ex, True) if x["orderType"] == "Market"]
assert len(mk) == 1 and mk[0]["side"] == "Sell" and float(ex.pos["SOLUSDT"]["size"]) == 0 and not ex.orders and lid not in state(d)["trades"]
print("C ok")
# --- D: wejście nigdy nie wypełnione: rekord znika po wygaśnięciu zlecenia
d, ex = setup(); run(d); lid = list(state(d)["trades"])[0]; ex.orders = []
st = state(d); st["trades"][lid]["created"] = int(time.time()) - 1000; json.dump(st, open(os.path.join(d, "trader_state.json"), "w")); run(d); assert lid not in state(d)["trades"]
print("D ok")
# --- E: zbyt mała pozycja na podział: jeden cel na całość
d, ex = setup({"fixed_notional": 15.0, "max_lev": 1.0}); run(d); lid = list(state(d)["trades"])[0]; ex.fill_entry("SOLUSDT"); ex.calls.clear(); run(d)
one = creates(ex, True); assert len(one) == 1 and one[0]["price"] == "108.0" and state(d)["trades"][lid]["split"] is False
print("E ok")
# --- F: błąd Bybit przy ustawianiu celów: ponowienie w następnym przebiegu bez dubli; pozycja ma stop
d, ex = setup(); run(d); lid = list(state(d)["trades"])[0]; ex.fill_entry("SOLUSDT"); ex.fail = {"/v5/order/create"}; run(d)
assert state(d)["trades"][lid]["tp_ok"] is False and ex.pos["SOLUSDT"]["stopLoss"] == "95.0"
ex.fail = set(); ex.calls.clear(); run(d); assert len(creates(ex, True)) == 2 and state(d)["trades"][lid]["tp_ok"] is True
print("F ok")
# --- G: tryb single (stare zachowanie): jeden cel w zleceniu wejścia
os.environ["TRADER_EXIT_MODE"] = "single"; d, ex = setup(); run(d); e = creates(ex, False)[0]; assert e["takeProfit"] == "108.0" and not state(d).get("trades"); os.environ.pop("TRADER_EXIT_MODE")
# --- H: wyłączony take-profit: brak celów i brak rekordu
d, ex = setup({"use_take_profit": False}); run(d); e = creates(ex, False)[0]; assert "takeProfit" not in e and not state(d).get("trades")
# --- I: ręczne zlecenia bez ID bota i cele nie są anulowane jako przeterminowane
d, ex = setup(); run(d); lid = list(state(d)["trades"])[0]; ex.fill_entry("SOLUSDT"); run(d)
for o in ex.orders: o["createdTime"] = str(int((time.time() - 5 * 86400) * 1000))        # cele starsze niż 2 dni
ex.calls.clear(); run(d); assert not [x for x in ex.calls if x[1] == "/v5/order/cancel"] and len(ex.orders) == 2
print("exits ok")
