import sys, os, time, json, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as t, btc_botcfg as bb, btc_exec as ex, btc_risk as rk
os.environ.update(TRADER_MODE="demo", TRADER_API_KEY="k", TRADER_API_SECRET="s", TRADER_KEY_CREATED=time.strftime("%Y-%m-%d"), TRADER_WEB_ORDERS="yes")
NOW = time.time()
def mk(positions=(), orders=(), equity=1000.0, rows=(), equity_seq=None):
    calls = []; eq = list(equity_seq or [equity])
    class FA:
        def __init__(s, c): pass
        def ok(s, m, path, params=None, allow=()):
            calls.append((m, path, dict(params or {})))
            if path == "/v5/position/closed-pnl": return {"list": list(rows), "nextPageCursor": ""} if (params or {}).get("startTime", 0) > (NOW - 8 * 86400) * 1000 else {"list": [], "nextPageCursor": ""}
            if path == "/v5/account/wallet-balance": return {"list": [{"totalEquity": str(eq.pop(0) if len(eq) > 1 else eq[0])}]}
            if path == "/v5/position/list": return {"list": list(positions)}
            if path == "/v5/order/realtime": return {"list": list(orders)}
            if path in ("/v5/order/create",): return {"orderId": "OID"}
            return {}
    return FA, calls
class P2:
    def klines(s, *a, **k): return []
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": "5e9"}
    def _request(s, path, params): return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"}, "priceFilter": {"tickSize": "0.01"}}]}
import itertools
_n = itertools.count(1)
PLAN = lambda stop=95.0: {"action": "long", "setup": "x", "entry": 100.0, "stop": stop, "tp1": 110.0, "tp2": 120.0, "date": f"2026-11-{next(_n):02d}", "valid_until_ms": 2**60, "live_state": "waiting"}      # każdy przebieg ma nowy plan (bez blokady duplikatu)
def pos(sym, size, avg, stop, side="Buy"): return {"symbol": sym, "size": str(size), "side": side, "avgPrice": str(avg), "stopLoss": str(stop), "positionValue": str(size * avg), "leverage": "1", "unrealisedPnl": "0"}
def setup(settings=None, d=None):
    d = d or tempfile.mkdtemp(); t.HERE = d; t.single_instance = lambda: None; t.load_env = lambda: None; t.make_pub = lambda c: P2()
    bb.write_settings(d, {"symbols": ["ETHUSDT"], "max_orders_day": 5, "max_positions": 3, "max_lev": 2.0, "risk_pct": 2.0, **(settings or {})})
    return d
def run(d, FA, plan=PLAN):
    t.Api = FA; t.get_plan = lambda c, sym=None, pub=None, *a, **k: (plan(), 100.0, P2()); t.LOGBUF.clear(); t.run()
def creates(calls): return [x[2] for x in calls if x[1] == "/v5/order/create" and not x[2].get("reduceOnly")]
row = lambda ago_h, pnl: {"symbol": "BTCUSDT", "closedPnl": str(pnl), "updatedTime": str(int((NOW - ago_h * 3600) * 1000))}
st_of = lambda d: bb.read_status(d)
# 1) limit ryzyka portfela: istniejące BTC long ryzykuje 20 USDT; nowa pozycja ETH (też 20) jest zmniejszana
d = setup({"max_heat_pct": 3.0}); FA, calls = mk(positions=[pos("BTCUSDT", 4, 100, 95)])                   # ryzyko 4*5 = 20 USDT
run(d, FA); o = creates(calls); assert len(o) == 1, calls
full = 1000 * 0.02 / 5; q = float(o[0]["qty"]); assert 0.5 * full < q < 0.8 * full, (q, full)                  # ~0,69 pełnego rozmiaru (qty 4 -> ~2,76)
assert any("zmniejszona" in e["msg"] for e in bb.read_events(d))
r = st_of(d)["risk"]; assert r["limit_usdt"] == 30.0 and r["heat"]["gross_usdt"] == 20.0 and not r["breakers"]
assert bb.read_risk(d)["ts"] > NOW - 5
# 2) portfel już przy limicie: nowa pozycja się nie mieści
d = setup({"max_heat_pct": 3.0}); FA, calls = mk(positions=[pos("BTCUSDT", 5.8, 100, 95)]); run(d, FA)         # ryzyko 29 USDT
assert not creates(calls) and any("się nie mieści" in e["msg"] for e in bb.read_events(d))
# 3) pozycja bez stopa liczona jako 10% wartości; pozycja po przesunięciu stopa na wejście: ryzyko 0
d = setup({"max_heat_pct": 3.0}); FA, calls = mk(positions=[pos("BTCUSDT", 3, 100, 100)]); run(d, FA); assert len(creates(calls)) == 1 and float(creates(calls)[0]["qty"]) == 4.0      # pełny rozmiar
# 4) seria strat -> wyłącznik; nowe pozycje wstrzymane, nadzór trwa (pozycja bota bez SL jest zamykana)
rows = [row(1, -5), row(3, -4), row(5, -3), row(7, -2)]
d = setup(); json.dump({"day": time.strftime("%Y-%m-%d", time.gmtime()), "orders": 0, "placed": [], "halted": False, "managed": {"BTCUSDT": int(NOW)}}, open(os.path.join(d, "trader_state.json"), "w"))
FA, calls = mk(rows=rows, positions=[pos("BTCUSDT", 1, 100, 0)]); run(d, FA)
assert not creates(calls) and [b["key"] for b in st_of(d)["risk"]["breakers"]] == ["streak"] and any(x[2].get("reduceOnly") for x in calls if x[1] == "/v5/order/create")
assert any("wyłącznik ryzyka" in e["msg"].lower() for e in bb.read_events(d)) and bb.read_risk(d)["breakers"][0]["key"] == "streak"
# po zysku seria się kończy
d = setup(); FA, calls = mk(rows=[row(0.5, 3)] + rows); run(d, FA); assert creates(calls) and not st_of(d)["risk"]["breakers"]
# stare straty (>pauza) nie blokują
d = setup(); FA, calls = mk(rows=[row(30, -5), row(31, -4), row(32, -3), row(33, -2)]); run(d, FA); assert creates(calls)
# 5) strata tygodniowa
ws = rk.week_start(NOW)
d = setup({"max_weekly_loss_pct": 5.0}); big = [{"symbol": "X", "closedPnl": "-60", "updatedTime": str(int(max(ws + 60, NOW - 3600) * 1000))}]; FA, calls = mk(rows=big); run(d, FA)
assert not creates(calls) and "weekly" in [b["key"] for b in st_of(d)["risk"]["breakers"]]
# 6) spadek od szczytu: wymaga odblokowania; odblokowanie plikiem RISK_RESET (zawsze), prośba z panelu (poza live)
d = setup(); FA, calls = mk(equity_seq=[1000.0]); run(d, FA)
FA, calls = mk(equity_seq=[860.0]); run(d, FA); assert not creates(calls) and st_of(d)["risk"]["breakers"][0]["key"] == "drawdown" and st_of(d)["risk"]["breakers"][0]["manual"]
FA, calls = mk(equity_seq=[1100.0]); run(d, FA); assert not creates(calls)                                          # odrobione, ale nadal zablokowane
open(os.path.join(d, "RISK_RESET"), "w").close(); FA, calls = mk(equity_seq=[1100.0]); run(d, FA)
assert creates(calls) and not st_of(d)["risk"]["breakers"] and not os.path.exists(os.path.join(d, "RISK_RESET"))
# prośba z panelu (demo): działa; w trybie live bez TRADER_WEB_ORDERS: ignorowana
d = setup(); FA, calls = mk(equity_seq=[1000.0]); run(d, FA); FA, calls = mk(equity_seq=[860.0]); run(d, FA); bb.request_risk_reset(d); FA, calls = mk(equity_seq=[860.0]); run(d, FA); assert creates(calls)
os.environ.update(TRADER_MODE="live", TRADER_CONFIRM_LIVE="I_ACCEPT_THE_RISK", TRADER_MAX_CAPITAL_USDT="1000"); os.environ.pop("TRADER_WEB_ORDERS")
d = setup(); FA, calls = mk(equity_seq=[1000.0]); run(d, FA); FA, calls = mk(equity_seq=[860.0]); run(d, FA); bb.request_risk_reset(d); FA, calls = mk(equity_seq=[860.0]); run(d, FA)
assert not creates(calls) and any("zignorowana w trybie live" in e["msg"] for e in bb.read_events(d))
# w live panel może tylko zaostrzyć limity: wyższy limit ryzyka z panelu jest ignorowany, dłuższa pauza dozwolona
c = {"mode": "live", "max_heat_pct": 3.0, "streak_losses": 4, "streak_pause_h": 24, "max_weekly_loss_pct": 6.0, "max_drawdown_pct": 12.0, "max_lev": 2.0, "symbols": ["BTCUSDT"]}
bb.apply_settings(c, {"max_heat_pct": 9.0, "streak_losses": 9, "streak_pause_h": 12, "max_weekly_loss_pct": 20.0, "max_drawdown_pct": 40.0}); assert c["max_heat_pct"] == 3.0 and c["streak_losses"] == 4 and c["streak_pause_h"] == 24 and c["max_weekly_loss_pct"] == 6.0 and c["max_drawdown_pct"] == 12.0
bb.apply_settings(c, {"max_heat_pct": 1.0, "streak_pause_h": 72}); assert c["max_heat_pct"] == 1.0 and c["streak_pause_h"] == 72
os.environ.update(TRADER_MODE="demo", TRADER_WEB_ORDERS="yes")
for bad in ({"max_heat_pct": 0.1}, {"streak_losses": 1}, {"max_drawdown_pct": 80}, {"streak_pause_h": 0}):
    try: bb.validate(bad); raise SystemExit("powinno odrzucić")
    except ValueError: pass
# 7) zlecenia ze strony: wyłącznik i limit ryzyka portfela
d = tempfile.mkdtemp(); ex.HERE = d; t.HERE = d
bb.write_risk(d, {"ts": int(time.time()), "breakers": [{"key": "streak", "label": "seria 4 strat z rzędu", "detail": "pauza 24 h"}]})
OPEN = {"type": "open", "symbol": "SOLUSDT", "side": "long", "order_type": "market", "notional": 50.0, "stop_pct": 3.0}
def exec_run(req, positions=(), env_heat=3.0):
    FA, calls = mk(positions=positions); t.Api = FA; c = t.cfg(); c["max_capital"] = 1000.0; c["max_heat_pct"] = env_heat
    oid = bb.add_order(d, req); ex.process(d, c, FA(c), P2()); return [x for x in bb.read_orders(d) if x["id"] == oid][0]
o = exec_run(OPEN); assert o["status"] == "rejected" and "wyłącznik ryzyka" in o["result"]
o = exec_run({"type": "close", "symbol": "SOLUSDT"}); assert o["status"] == "rejected" and "wyłącznik" not in o["result"]       # zamykanie nie jest blokowane (tu: brak pozycji)
bb.write_risk(d, {"ts": int(time.time()), "breakers": []})
assert exec_run(OPEN)["status"] == "done"
o = exec_run({**OPEN, "notional": 600.0, "stop_pct": 3.0}, positions=[pos("BTCUSDT", 4, 100, 95)]); assert o["status"] == "rejected" and "limit ryzyka portfela" in o["result"], o   # 18 + 20 przy limicie 30
print("riskbot ok")
