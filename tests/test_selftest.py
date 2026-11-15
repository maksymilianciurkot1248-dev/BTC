import sys, os, time, json, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_selftest as st, btc_trader as t
class Ex:
    def __init__(s, **kw): s.pos, s.orders, s.n, s.links, s.kw, s.calls = {}, [], 0, set(), kw, []
    def ok(s, m, path, params=None, allow=()):
        p = dict(params or {}); s.calls.append((m, path, p)); k = s.kw
        if path == "/v5/account/wallet-balance":
            if k.get("badauth"): raise RuntimeError("/v5/account/wallet-balance: API key is invalid. (retCode 10003)")
            return {"list": [{"totalEquity": str(k.get("equity", 50000.0))}]}
        if path == "/v5/position/closed-pnl":
            if "symbol" not in p and k.get("pnl_needs_symbol"): raise RuntimeError("symbol required (retCode 10001)")
            return {"list": [], "nextPageCursor": ""}
        if path == "/v5/position/list":
            pl = [dict(v, symbol=n) for n, v in s.pos.items() if float(v["size"]) > 0]
            return {"list": pl if not (p.get("symbol") and p["symbol"] not in s.pos) else []}
        if path == "/v5/order/realtime": return {"list": [o for o in s.orders if not p.get("symbol") or o["symbol"] == p["symbol"]]}
        if path == "/v5/position/set-leverage": return {}
        if path == "/v5/position/trading-stop": s.pos[p["symbol"]]["stopLoss"] = p["stopLoss"]; return {}
        if path == "/v5/order/cancel": s.orders = [o for o in s.orders if o["orderId"] != p["orderId"]]; return {}
        if path == "/v5/order/create":
            if p.get("orderLinkId") in s.links and not k.get("accept_dup"): raise RuntimeError("/v5/order/create: Duplicate orderLinkId (retCode 110072)")
            s.links.add(p.get("orderLinkId")); s.n += 1
            if k.get("fail_reduce") and p.get("reduceOnly") and p["orderType"] == "Limit": raise RuntimeError("/v5/order/create: reduce-only rejected (retCode 110017)")
            if p["orderType"] == "Market" and p.get("reduceOnly"): s.pos[p["symbol"]]["size"] = "0"; return {"orderId": f"M{s.n}"}
            if p["orderType"] == "Market": s.pos[p["symbol"]] = {"size": p["qty"], "avgPrice": "100", "side": "Buy", "stopLoss": p.get("stopLoss", "0"), "leverage": "1", "positionValue": "1", "unrealisedPnl": "0", "createdTime": "1"}; return {"orderId": f"M{s.n}"}
            o = {"orderId": f"O{s.n}", "symbol": p["symbol"], "orderLinkId": p.get("orderLinkId"), "reduceOnly": bool(p.get("reduceOnly")), "stopLoss": p.get("stopLoss", "0")}; s.orders.append(o); return {"orderId": o["orderId"]}
        raise AssertionError(path)
class Pub:
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": "5e9", "fundingRate": "0.0001"}
    def _request(s, path, params): return {"list": [{"lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1", "minNotionalValue": "5"}, "priceFilter": {"tickSize": "0.1"}}]}
c = {"mode": "demo"}; nos = lambda *_: None
def status(r, name): return [x["status"] for x in r.rows if name in x["name"]]
ex = Ex(); r = st.run(ex, Pub(), c, "BTCUSDT", sleep=nos)
assert not r.failed and all(x["status"] in ("PASS",) for x in r.rows), [x for x in r.rows if x["status"] != "PASS"]
assert not ex.orders and all(float(v["size"]) == 0 for v in ex.pos.values()), "sprzątanie"
assert any("sprzątanie" in x["name"] for x in r.rows) and len(r.rows) >= 20
# odczyt: bez zleceń
ex = Ex(); r = st.run(ex, Pub(), c, "BTCUSDT", read_only=True, sleep=nos); assert not [x for x in ex.calls if x[0] == "POST"] and not r.failed and status(r, "składanie")[0] == "SKIP"
# closed-pnl wymaga symbolu: ostrzeżenie (bot ma wariant awaryjny), nie błąd
ex = Ex(pnl_needs_symbol=True); r = st.run(ex, Pub(), c, "BTCUSDT", sleep=nos); assert status(r, "BEZ symbolu") == ["WARN"] and status(r, "z symbolem") == ["PASS"] and not r.failed
# Bybit przyjmuje duplikat identyfikatora: FAIL (bot polega na idempotencji)
ex = Ex(accept_dup=True); r = st.run(ex, Pub(), c, "BTCUSDT", sleep=nos); assert status(r, "duplikat") == ["FAIL"] and r.failed
# błąd w środku: raport + sprzątanie pozycji i zleceń
ex = Ex(fail_reduce=True); r = st.run(ex, Pub(), c, "BTCUSDT", sleep=nos); assert r.failed and not ex.orders and all(float(v["size"]) == 0 for v in ex.pos.values())
# zły klucz: czytelny FAIL i brak dalszych kroków
ex = Ex(badauth=True); r = st.run(ex, Pub(), c, "BTCUSDT", sleep=nos); assert r.failed and len(r.rows) == 1 and not [x for x in ex.calls if x[0] == "POST"]
# za małe saldo: pominięcie pozycji kontrolnej
ex = Ex(equity=5.0); r = st.run(ex, Pub(), c, "BTCUSDT", sleep=nos); assert status(r, "pozycja kontrolna") == ["SKIP"] and not ex.pos
# main odmawia live i dryrun bez --read-only
import subprocess
for mode in ("live", "dryrun"):
    p = subprocess.run([sys.executable, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))) + '/btc_selftest.py'], env={**os.environ, "TRADER_MODE": mode, "PATH": os.environ["PATH"]}, capture_output=True, text=True)
    assert p.returncode != 0 and ("tylko na koncie demo" in p.stderr + p.stdout or "trader.env" in p.stderr + p.stdout or "Brak" in p.stderr + p.stdout), (mode, p.stderr[-200:], p.stdout[-200:])
print("selftest ok", len(r.rows))
