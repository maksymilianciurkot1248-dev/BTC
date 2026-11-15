import sys, os, json, tempfile, time
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
d = tempfile.mkdtemp()
import btc_botcfg as bb
# walidacja
assert bb.validate({"risk_pct": "0.5", "max_lev": 2, "directions": ["long"], "paused": True}) == {"risk_pct": 0.5, "max_lev": 2.0, "directions": ["long"], "paused": True}
for bad in ({"max_lev": 3}, {"risk_pct": 0}, {"max_orders_day": 9}, {"directions": []}, {"directions": ["flat"]}, {"paused": "tak"}, {"evil": 1}, {"max_capital": "abc"}, {"risk_pct": float("nan")}, "x"):
    try: bb.validate(bad); raise SystemExit(f"powinno odrzucić {bad}")
    except ValueError: pass
# zapis łączy ustawienia
bb.write_settings(d, {"risk_pct": 0.3}); s = bb.write_settings(d, {"paused": True})
assert s["risk_pct"] == 0.3 and s["paused"] is True and "updated" in s
assert bb.read_settings(d)["risk_pct"] == 0.3
open(os.path.join(d, bb.SETTINGS), "w").write("{zepsuty"); assert bb.read_settings(d) == {}
# live może tylko zmniejszać
c = {"mode": "live", "risk_pct": 0.5, "max_lev": 2.0, "max_daily_loss_pct": 2.0, "max_orders_day": 2, "max_capital": 300.0}
bb.apply_settings(c, {"risk_pct": 1.5, "max_capital": 5000.0, "max_lev": 1.0}); assert c["risk_pct"] == 0.5 and c["max_capital"] == 300.0 and c["max_lev"] == 1.0
c2 = dict(c, mode="demo"); bb.apply_settings(c2, {"risk_pct": 1.5}); assert c2["risk_pct"] == 1.5
# zdarzenia: dedupe
assert bb.append_event(d, "decision", "plan: czekaj") and not bb.append_event(d, "decision", "plan: czekaj") and bb.append_event(d, "alert", "x")
assert [e["kind"] for e in bb.read_events(d)] == ["decision", "alert"]
# statystyki
rows = [{"closedPnl": "10", "updatedTime": "1000"}, {"closedPnl": "-4", "updatedTime": "2000"}, {"closedPnl": "6", "updatedTime": "3000"}, {"closedPnl": "-8", "updatedTime": "4000"}]
st = bb.stats_from_closed(rows)
assert st["trades"] == 4 and st["win_rate"] == 50.0 and st["total_pnl"] == 4.0 and st["max_drawdown"] == 8.0 and st["profit_factor"] == 1.33 and st["best"] == 10.0
assert bb.stats_from_closed([]) == {"trades": 0}
# trader: przebieg dryrun zapisuje status i zdarzenia; pauza i kierunek
os.environ.update(TRADER_MODE="dryrun"); os.environ.pop("TRADER_API_KEY", None)
import btc_trader as t
t.HERE = d
t.load_env = lambda: None
class FakePub:
    def __init__(s, base_url=None): pass
    def klines(s, sym, tf, n): return [[i * 86400000, 100, 101, 99, 100, 1] for i in range(400)]
    def ticker(s, sym): return {"lastPrice": "100"}
t.ba.BybitClient = FakePub
t.get_plan = lambda c, sym=None, pub=None, *a, **k: ({"action": "long", "setup": "x", "entry": 100, "stop": 95, "tp1": 110, "date": "2026-11-08", "valid_until_ms": 2**60, "live_state": "waiting"}, 100.0, None)
bb.write_settings(d, {"directions": ["short"]}); open(os.path.join(d, bb.SETTINGS)).close()
t.LOGBUF.clear(); t.run()
s = bb.read_status(d); assert s["ok"] and s["mode"] == "dryrun" and "wyłączony" in s["decision"] and s["symbols"] == ["BTCUSDT"] and s["effective"]["directions"] == ["short"], s
bb.write_settings(d, {"paused": True}); t.LOGBUF.clear(); t.run()
assert "pauza" in bb.read_status(d)["decision"]
assert any("pauza" in e["msg"] for e in bb.read_events(d))
print("bot ok")

# ===== wiele aktywów, demo z udawanym Bybit =====
import math
os.environ.update(TRADER_MODE="demo", TRADER_API_KEY="k", TRADER_API_SECRET="s", TRADER_KEY_CREATED=time.strftime("%Y-%m-%d"))
t.single_instance = lambda: None
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
    turnover = {"BTCUSDT": 5e9, "ETHUSDT": 2e9, "SOLUSDT": 1e6}
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": str(s.turnover.get(sym, 5e9))}
    def _request(s, path, params):
        return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"}, "priceFilter": {"tickSize": "0.1"}}]}
PLAN = lambda a="long": {"action": a, "setup": "x", "entry": 100, "stop": 95, "tp1": 110, "date": "2026-11-08", "valid_until_ms": 2**60, "live_state": "waiting"}
t.get_plan = lambda c, sym=None, pub=None, *a, **k: (PLAN(), 100.0, P2())
t.make_pub = lambda c: P2()
def go(settings, positions=(), orders=(), equity=1000.0, plan=PLAN, edge=None, quar=None):
    d = tempfile.mkdtemp(); t.HERE = d; t.LOGBUF.clear()
    bb.write_settings(d, {"max_heat_pct": 10.0, **settings})
    if edge: bb.write_edge(d, edge)
    if quar: bb.write_quar(d, quar)
    FA, calls = mk(positions, orders, equity); t.Api = FA
    t.get_plan = lambda c, sym=None, pub=None, *a, **k: (plan(), 100.0, P2())
    t.run()
    return d, calls
def creates(calls): return [x[2] for x in calls if x[1] == "/v5/order/create" and not x[2].get("reduceOnly")]

# 1) dwa aktywa, limit 1 pozycji: tylko jedno zlecenie, stop-loss ZAWSZE w zleceniu
d, calls = go({"symbols": ["BTCUSDT", "ETHUSDT"], "max_orders_day": 5, "max_positions": 1})
o = creates(calls); assert len(o) == 1 and o[0]["symbol"] == "BTCUSDT" and o[0]["stopLoss"] and o[0]["takeProfit"], o
assert any("limit jednoczesnych pozycji" in e["msg"] for e in bb.read_events(d))
# 2) dwie pozycje naraz; suma nominałów <= x2 kapitału (1000 USDT -> 2000), ryzyko 2% -> qty ograniczone limitem
d, calls = go({"symbols": ["BTCUSDT", "ETHUSDT"], "max_orders_day": 5, "max_positions": 2, "risk_pct": 2.0, "max_lev": 2.0})
o = creates(calls); assert len(o) == 2, o
tot = sum(float(x["qty"]) * 100 for x in o); assert tot <= 2000 * 1.001, tot
# 3) brak take-profit na życzenie, stop nadal jest
d, calls = go({"use_take_profit": False}); o = creates(calls)[0]; assert "takeProfit" not in o and o["stopLoss"] == "95.0", o
# 4) mnożnik stopa 1.5: stop dalej od wejścia (92.5), rozmiar mniejszy
d, calls = go({"stop_mult": 1.5, "risk_pct": 1.0}); o = creates(calls)[0]; assert o["stopLoss"] == "92.5", o
# 5) istniejąca pozycja na innym aktywie wlicza się do limitów
pos = [{"symbol": "ETHUSDT", "size": "10", "side": "Buy", "avgPrice": "100", "stopLoss": "95", "takeProfit": "110", "leverage": "2", "positionValue": "1500", "unrealisedPnl": "1"}]
d, calls = go({"symbols": ["BTCUSDT", "ETHUSDT"], "max_positions": 2, "risk_pct": 2.0}, positions=pos)
o = creates(calls); assert len(o) == 1 and o[0]["symbol"] == "BTCUSDT" and float(o[0]["qty"]) * 100 <= 500 * 1.001, o     # zostało 500 USDT limitu
st = bb.read_status(d); assert st["positions"][0]["symbol"] == "ETHUSDT" and "BTCUSDT" in st["plans"]
# 6) pozycja bez SL jest zamykana (na dowolnym aktywie)
bad = [dict(pos[0], stopLoss="0")]
d, calls = go({"symbols": ["BTCUSDT"]}, positions=bad)
assert not any(x[1] == "/v5/order/create" and x[2].get("reduceOnly") for x in calls)            # ręczna pozycja bez SL: bot jej nie zamyka
t.SUPERVISE_ALL = True; d, calls = go({"symbols": ["BTCUSDT"]}, positions=bad); t.SUPERVISE_ALL = False
assert any(x[1] == "/v5/order/create" and x[2].get("reduceOnly") for x in calls) and not creates(calls)       # TRADER_SUPERVISE=all: zamyka każdą
# 7) mała płynność pomija aktywo, kierunek wyłączony pomija, pauza pomija
d, calls = go({"symbols": ["SOLUSDT"]}); assert not creates(calls) and any("płynność" in e["msg"] for e in bb.read_events(d))
d, calls = go({"directions": ["short"]}); assert not creates(calls)
d, calls = go({"paused": True}); assert not creates(calls)
# 8) limit zleceń na dobę
d, calls = go({"symbols": ["BTCUSDT", "ETHUSDT"], "max_positions": 3, "max_orders_day": 1}); assert len(creates(calls)) == 1
# 9) walidacja i tryb live
assert bb.validate({"symbols": ["btcusdt", "ethusdt", "btcusdt"], "use_take_profit": False, "stop_mult": 1.2, "max_positions": 2}) == {"symbols": ["BTCUSDT", "ETHUSDT"], "use_take_profit": False, "stop_mult": 1.2, "max_positions": 2}
for bad_ in ({"symbols": []}, {"symbols": ["BTC"]}, {"symbols": ["A"*20+"USDT"]}, {"symbols": [1]}, {"symbols": ["BTCUSDT"]*0}, {"symbols": [f"X{i}USDT" for i in range(9)]}, {"stop_mult": 3}, {"max_positions": 9}, {"use_take_profit": "tak"}, {"stop_loss": False}, {"stop_loss_enabled": False}):
    try: bb.validate(bad_); raise SystemExit(f"powinno odrzucić {bad_}")
    except ValueError: pass
cl = {"mode": "live", "symbols": ["BTCUSDT", "ETHUSDT"], "risk_pct": .5, "max_lev": 2.0, "max_daily_loss_pct": 2.0, "max_orders_day": 2, "max_capital": 300.0, "max_positions": 1, "stop_mult": 1.0}
bb.apply_settings(cl, {"symbols": ["ETHUSDT", "DOGEUSDT"], "max_positions": 3}); assert cl["symbols"] == ["ETHUSDT"] and cl["max_positions"] == 1
cl2 = dict(cl, symbols=["BTCUSDT"]); bb.apply_settings(cl2, {"symbols": ["DOGEUSDT"]}); assert cl2["symbols"] == ["BTCUSDT"]       # brak wspólnych -> zostaje lista z serwera
# 10) limit dźwigni jest wiążący: ciasny stop => ryzykowny rozmiar ograniczony do x2 kapitału łącznie
TIGHT = lambda a="long": dict(PLAN(a), stop=99)
d, calls = go({"symbols": ["BTCUSDT", "ETHUSDT"], "max_orders_day": 5, "max_positions": 2, "risk_pct": 2.0, "max_lev": 2.0}, plan=TIGHT)
o = creates(calls); assert len(o) == 1 and abs(float(o[0]["qty"]) * 100 - 2000) < 1, o          # pierwsze zajmuje cały limit x2, drugie się nie mieści
assert any("brak wolnego limitu" in e["msg"] for e in bb.read_events(d))
d, calls = go({"symbols": ["BTCUSDT"], "max_positions": 2, "risk_pct": 2.0}, positions=pos, plan=TIGHT)
o = creates(calls); assert len(o) == 1 and abs(float(o[0]["qty"]) * 100 - 500) < 1, o            # po pozycji 1500 zostaje 500 USDT nominału
# 11) automatyczny wybór: mapa przewagi + filtry kontekstu + kwarantanna + zgoda w live
nowt = time.time()
EDGE = {"ts": nowt - 600, "allowed": ["ETHUSDT", "XRPUSDT"], "events": [], "assets": {"ETHUSDT": {"filters": [{"flag": "btc_down"}]}, "XRPUSDT": {"filters": []}}}
d, calls = go({"symbols": ["BTCUSDT"], "auto_select": True, "max_positions": 3, "max_orders_day": 5}, edge=EDGE)
st = bb.read_status(d); assert st["auto"]["enabled"] and st["auto"]["scan"] == ["BTCUSDT", "ETHUSDT", "XRPUSDT"], st["auto"]
syms = [x["symbol"] for x in creates(calls)]; assert "ETHUSDT" not in syms and "XRPUSDT" in syms and "BTCUSDT" in syms, syms          # ETH odrzucone przez filtr (BTC pod SMA50)
assert any("filtr kontekstu" in e["msg"] and "ETHUSDT" in e["msg"] for e in bb.read_events(d))
d, calls = go({"symbols": ["BTCUSDT"], "auto_select": False, "max_positions": 3, "max_orders_day": 5}, edge=EDGE); assert [x["symbol"] for x in creates(calls)] == ["BTCUSDT"]       # bez automatu tylko lista ręczna
d, calls = go({"symbols": ["BTCUSDT"], "auto_select": True, "max_positions": 3, "max_orders_day": 5}, edge=dict(EDGE, ts=nowt - 9 * 86400)); assert [x["symbol"] for x in creates(calls)] == ["BTCUSDT"]   # stara mapa: automat stoi
d, calls = go({"symbols": ["BTCUSDT"], "auto_select": True, "max_positions": 3, "max_orders_day": 5}, edge=EDGE, quar={"until": {"XRPUSDT": nowt + 3600}, "reset": {}})
assert "XRPUSDT" not in [x["symbol"] for x in creates(calls)] and any("kwarantanna" in e["msg"] for e in bb.read_events(d))
d, calls = go({"symbols": ["BTCUSDT"], "auto_select": True}, edge={"ts": nowt, "allowed": [], "assets": {}}); assert any("nie handluje" in e["msg"] for e in bb.read_events(d))
# live: automat ignorowany bez TRADER_AUTO_LIVE=yes
os.environ.update(TRADER_MODE="live", TRADER_CONFIRM_LIVE="I_ACCEPT_THE_RISK", TRADER_MAX_CAPITAL_USDT="500"); os.environ.pop("TRADER_AUTO_LIVE", None)
d, calls = go({"symbols": ["BTCUSDT"], "auto_select": True, "max_positions": 3, "max_orders_day": 5}, edge=EDGE); assert bb.read_status(d)["auto"]["scan"] == ["BTCUSDT"] and any("live" in e["msg"] for e in bb.read_events(d))
os.environ["TRADER_AUTO_LIVE"] = "yes"; d, calls = go({"symbols": ["BTCUSDT"], "auto_select": True}, edge=EDGE); assert "XRPUSDT" in bb.read_status(d)["auto"]["scan"]
os.environ.update(TRADER_MODE="demo"); os.environ.pop("TRADER_AUTO_LIVE", None)
# kwarantanna po serii strat na koncie: trader zapisuje stan po odświeżeniu statystyk
d = tempfile.mkdtemp(); t.HERE = d; t.LOGBUF.clear(); FA, calls = mk(); 
class FA2(FA):
    def ok(s, m, path, params=None, allow=()):
        if path == "/v5/position/closed-pnl": return {"list": [{"symbol": "XRPUSDT", "closedPnl": "-4", "updatedTime": str(int(time.time() * 1000) - i * 1000)} for i in range(6)], "nextPageCursor": ""}
        return FA.ok(s, m, path, params, allow)
t.Api = FA2; t.run()
assert bb.read_quar(d)["until"].get("XRPUSDT", 0) > time.time(), bb.read_quar(d)
print("bot api ok")
# 12) stała, mała wartość pozycji bez dźwigni (long)
d, calls = go({"fixed_notional": 15.0, "max_lev": 1.0, "directions": ["long"], "risk_pct": 2.0, "max_orders_day": 5}); o = creates(calls)[0]
assert abs(float(o["qty"]) * 100 - 15) < 1 and o["stopLoss"], o
assert [x for x in calls if x[1] == "/v5/position/set-leverage"][0][2]["buyLeverage"] == "1"
d, calls = go({"fixed_notional": 0.5, "max_lev": 1.0}); assert not creates(calls) and any("minimum giełdy" in e["msg"] and "USDT" in e["msg"] for e in bb.read_events(d))      # poniżej minimum giełdy: pominięte z wyjaśnieniem
d, calls = go({"fixed_notional": 5000.0, "max_lev": 1.0, "risk_pct": 2.0}); assert float(creates(calls)[0]["qty"]) * 100 <= 1000 * 1.001      # stała wartość nie przekroczy kapitału x dźwignia
print("fixed ok")
# 13) tryb zatwierdzania: bot zgłasza wniosek, po Twoim kliknięciu sam stawia pozycję
def run_in(d, plan=PLAN):
    FA, calls = mk(); t.Api = FA; t.HERE = d; t.LOGBUF.clear()
    t.get_plan = lambda c, sym=None, pub=None, *a, **k: (plan(), 100.0, P2()); t.run(); return calls
d = tempfile.mkdtemp(); bb.write_settings(d, {"require_approval": True, "max_orders_day": 5})
c1 = run_in(d); assert not creates(c1)
pr = bb.read_proposals(d); assert len(pr) == 1 and pr[0]["status"] == "pending" and pr[0]["symbol"] == "BTCUSDT" and pr[0]["stop"] == 95.0 and pr[0]["id"].startswith("p")
pid = pr[0]["id"]
c2 = run_in(d); assert not creates(c2) and len(bb.read_proposals(d)) == 1                          # kolejny przebieg: bez duplikatu i bez zlecenia
assert bb.decide(d, pid, "approve") == (200, "zatwierdzono")
assert bb.decide(d, pid, "reject")[0] == 409                                                         # drugi raz nie da się zmienić decyzji
c3 = run_in(d); o = creates(c3); assert len(o) == 1 and o[0]["stopLoss"] == "95.0" and o[0]["orderLinkId"] == pid, o
assert bb.find_proposal(d, pid)["status"] == "placed" and bb.find_proposal(d, pid)["order_id"] == "OID"
c4 = run_in(d); assert not creates(c4)                                                               # nie stawia drugi raz
# odrzucenie
d = tempfile.mkdtemp(); bb.write_settings(d, {"require_approval": True}); run_in(d); pid = bb.read_proposals(d)[0]["id"]
assert bb.decide(d, pid, "reject")[0] == 200; assert not creates(run_in(d)) and bb.find_proposal(d, pid)["status"] == "rejected"
# wygaśnięcie niezatwierdzonego i spóźnione zatwierdzenie
d = tempfile.mkdtemp(); bb.write_settings(d, {"require_approval": True}); run_in(d); pid = bb.read_proposals(d)[0]["id"]
assert bb.decide(d, pid, "approve", now=time.time() + 13 * 3600)[0] == 409
bb.expire_proposals(d, now=time.time() + 13 * 3600); assert bb.find_proposal(d, pid)["status"] == "expired"
# zatwierdzony, ale plan zmienił się (inna data planu = inny identyfikator): stary wniosek nie uprawnia do nowej pozycji
d = tempfile.mkdtemp(); bb.write_settings(d, {"require_approval": True}); run_in(d); pid = bb.read_proposals(d)[0]["id"]; bb.decide(d, pid, "approve")
newplan = lambda a="long": dict(PLAN(a), date="2026-11-09")
c5 = run_in(d, newplan); assert not creates(c5) and len(bb.read_proposals(d)) == 2 and bb.read_proposals(d)[-1]["status"] == "pending"
# walidacja identyfikatora i decyzji
for bad_ in (("x", "approve"), (pid, "ok"), (None, "approve"), ("p" + "z" * 22, "approve")):
    try: bb.decide(d, *bad_); raise SystemExit(f"powinno odrzucić {bad_}")
    except ValueError: pass
assert bb.decide(d, "p" + "0" * 24, "approve")[0] == 404
# tryb dryrun też obsługuje wnioski (do testów bez giełdy)
os.environ["TRADER_MODE"] = "dryrun"; d = tempfile.mkdtemp(); bb.write_settings(d, {"require_approval": True}); run_in(d); pid = bb.read_proposals(d)[0]["id"]; bb.decide(d, pid, "approve"); run_in(d)
assert bb.find_proposal(d, pid)["status"] == "placed" and "dryrun" in bb.find_proposal(d, pid)["note"]
os.environ["TRADER_MODE"] = "demo"
print("approval ok")
# 14) nadzór tylko nad własnymi pozycjami i zleceniami bota
import json as _j
d = tempfile.mkdtemp(); t.HERE = d; t.LOGBUF.clear(); bb.write_settings(d, {"max_orders_day": 5})
FA, calls = mk(); t.Api = FA; t.get_plan = lambda c, sym=None, pub=None, *a, **k: (PLAN(), 100.0, P2()); t.run()
assert _j.load(open(os.path.join(d, "trader_state.json")))["managed"].get("BTCUSDT")                         # bot zapisał, że zarządza BTCUSDT
FA2, calls2 = mk(positions=[dict(pos[0], symbol="BTCUSDT", stopLoss="0")]); t.Api = FA2; t.LOGBUF.clear(); t.run()
assert any(x[1] == "/v5/order/create" and x[2].get("reduceOnly") for x in calls2)                             # po przebiegu z własną pozycją: nadzór działa
old = int((time.time() - 4 * 86400) * 1000)
orders = [{"orderId": "1", "symbol": "ETHUSDT", "orderLinkId": "", "createdTime": str(old)}, {"orderId": "2", "symbol": "ETHUSDT", "orderLinkId": "p" + "a" * 24, "createdTime": str(old)}, {"orderId": "3", "symbol": "SOLUSDT", "orderLinkId": "o" + "b" * 20, "createdTime": str(old)}]
FA3, calls3 = mk(orders=orders); t.Api = FA3; t.LOGBUF.clear(); t.run()
canc = [x[2]["orderId"] for x in calls3 if x[1] == "/v5/order/cancel"]; assert sorted(canc) == ["2", "3"], canc          # ręczne zlecenie (bez ID bota) zostaje
print("supervise scope ok")
