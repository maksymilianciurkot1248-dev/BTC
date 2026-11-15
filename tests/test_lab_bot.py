"""Trader a laboratorium: wybór wariantu zespołu, czas trzymania z planu, dziennik zamkniętych transakcji, ostrzeżenie o strategii klasycznej."""
import sys, os, time, json, math, random, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as t, btc_botcfg as bb, btc_lab as lab, btc_analysis as ba
os.environ.update(TRADER_MODE="demo", TRADER_API_KEY="k", TRADER_API_SECRET="s", TRADER_KEY_CREATED=time.strftime("%Y-%m-%d"))
os.environ.pop("TRADER_EXIT_MODE", None)

class Exch:
    def __init__(s, equity=1000.0): s.pos, s.orders, s.calls, s.n, s.equity, s.closed = {}, [], [], 0, equity, []
    def api(s):
        ex = s
        class A:
            def __init__(a, c): pass
            def ok(a, m, path, params=None, allow=()):
                p = dict(params or {}); ex.calls.append((m, path, p))
                if path == "/v5/account/wallet-balance": return {"list": [{"totalEquity": str(ex.equity)}]}
                if path == "/v5/position/closed-pnl":
                    lo, hi = int(p.get("startTime", 0)), int(p.get("endTime", 2**62))
                    return {"list": [r for r in ex.closed if lo <= int(r["updatedTime"]) <= hi], "nextPageCursor": ""}
                if path == "/v5/position/list": return {"list": [dict(v, symbol=k, positionValue=str(float(v["size"]) * float(v["avgPrice"])), leverage="1") for k, v in ex.pos.items() if float(v["size"]) > 0]}
                if path == "/v5/order/realtime": return {"list": list(ex.orders)}
                if path in ("/v5/position/set-leverage", "/v5/position/trading-stop"): return {}
                if path == "/v5/order/cancel": ex.orders = [o for o in ex.orders if o["orderId"] != p["orderId"]]; return {}
                if path == "/v5/order/create":
                    ex.n += 1
                    if p.get("reduceOnly") and p["orderType"] == "Market":
                        ex.pos[p["symbol"]]["size"] = "0"; return {"orderId": f"M{ex.n}"}
                    o = {"orderId": f"O{ex.n}", "symbol": p["symbol"], "orderLinkId": p.get("orderLinkId", ""), "createdTime": str(int(time.time() * 1000)), "side": p["side"], "qty": p["qty"], "price": p.get("price"),
                         "reduceOnly": bool(p.get("reduceOnly")), "stopLoss": p.get("stopLoss"), "takeProfit": p.get("takeProfit")}
                    ex.orders.append(o); return {"orderId": o["orderId"]}
                raise AssertionError(path)
        return A
    def fill_entry(s, sym):
        o = next(o for o in s.orders if o["symbol"] == sym and not o["reduceOnly"]); s.orders.remove(o)
        s.pos[sym] = {"size": o["qty"], "avgPrice": o["price"], "side": "Buy" if o["side"] == "Buy" else "Sell", "stopLoss": o["stopLoss"], "takeProfit": o["takeProfit"] or "0"}
    def close_pos(s, sym, pnl, at=None):
        s.pos[sym]["size"] = "0"
        s.closed.append({"symbol": sym, "closedPnl": str(pnl), "updatedTime": str(int((at or time.time()) * 1000)), "createdTime": str(int((at or time.time()) * 1000))})

class P2:
    def klines(s, *a, **k): return []
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": "5e9"}
    def _request(s, path, params): return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1"}, "priceFilter": {"tickSize": "0.1"}}]}
PLAN = {"action": "long", "setup": "x", "entry": 100.0, "stop": 95.0, "tp1": 108.0, "tp2": 116.0, "date": "2026-11-08", "valid_until_ms": 2**60, "live_state": "waiting"}
def setup(settings=None, plan=None, ex=None):
    d = tempfile.mkdtemp(); t.HERE = d; t.single_instance = lambda: None; t.load_env = lambda: None; t.make_pub = lambda c: P2()
    bb.write_settings(d, {"symbols": ["SOLUSDT"], "max_orders_day": 5, "max_positions": 2, **(settings or {})})
    ex = ex or Exch(); t.Api = ex.api(); t.get_plan = lambda c, sym=None, pub=None, *a, **k: (dict(plan or PLAN), 100.0, P2())
    return d, ex
def run(d): t.LOGBUF.clear(); t.run()
def state(d): return json.load(open(os.path.join(d, "trader_state.json")))
def set_state(d, st): json.dump(st, open(os.path.join(d, "trader_state.json"), "w"))
def events(d): return " | ".join(e["msg"] for e in bb.read_events(d, 80))

# ---------------------------------------------------------------- 1) rekord transakcji niesie dane planu
ORIG_GET_PLAN = t.get_plan
assert t.LEDGER_SETTLE_AFTER_S == 120
t.LEDGER_SETTLE_AFTER_S = 0          # w scenariuszach 2–3 rozliczamy od razu; zwłokę sprawdza scenariusz 3b
d, ex = setup(); run(d); lid, tr = next(iter(state(d)["trades"].items()))
assert tr["stop"] == 95.0 and tr["plan_date"] == "2026-11-08" and tr["strategy"] == "plan" and tr["variant"] is None and tr["max_hold_s"] == 7 * 86400, tr
ens_plan = dict(PLAN, strategy="ensemble", variant="v2", max_hold_bars=3)
d, ex = setup(plan=ens_plan); run(d); lid, tr = next(iter(state(d)["trades"].items()))
assert tr["strategy"] == "ensemble" and tr["variant"] == "v2" and tr["max_hold_s"] == 3 * 86400, tr

# ---------------------------------------------------------------- 2) stop czasowy według planu wariantu (3 dni), a nie stałe 7 dni
ex.fill_entry("SOLUSDT"); run(d)
st = state(d); st["trades"][lid]["opened"] = int(time.time()) - 2 * 86400; set_state(d, st); ex.calls.clear(); run(d)
assert lid in state(d)["trades"] and float(ex.pos["SOLUSDT"]["size"]) > 0, "po 2 dniach pozycja zostaje"
st = state(d); st["trades"][lid]["opened"] = int(time.time()) - 4 * 86400; set_state(d, st); ex.calls.clear(); run(d)
mk = [x[2] for x in ex.calls if x[1] == "/v5/order/create" and x[2].get("orderType") == "Market"]
assert len(mk) == 1 and mk[0]["reduceOnly"] and lid not in state(d)["trades"] and "zamknięcie po 3 dniach" in events(d), events(d)[-300:]
q = state(d)["ledger_pending"]; assert len(q) == 1 and q[0]["reason"] == "stop czasowy" and q[0]["variant"] == "v2" and q[0]["plan_date"] == "2026-11-08", q
# giełda jeszcze nie podała wyniku: wpis czeka; gdy poda, trafia do dziennika z wynikiem w R
run(d); assert len(state(d)["ledger_pending"]) == 1 and lab.read_ledger(d) == []
qty = q[0]["fill_qty"]; ex.closed.append({"symbol": "SOLUSDT", "closedPnl": str(round(2.5 * qty, 4)), "updatedTime": str(int(time.time() * 1000))})
run(d); led = lab.read_ledger(d)
assert state(d)["ledger_pending"] == [] and len(led) == 1 and led[0]["symbol"] == "SOLUSDT" and abs(led[0]["r"] - 2.5 / 5.0) < 1e-3 and led[0]["mode"] == "demo" and led[0]["fills"] == 1, led
assert led[0]["strategy"] == "ensemble" and led[0]["variant"] == "v2" and led[0]["side"] == "long" and led[0]["entry"] == 100.0 and led[0]["stop"] == 95.0
run(d); assert len(lab.read_ledger(d)) == 1, "bez podwójnego zapisu"
# stan przenosi kolejkę przez zmianę doby
st = state(d); st["day"] = "2000-01-01"; st["ledger_pending"] = [{"symbol": "X"}]; set_state(d, st); assert t.load_state()["ledger_pending"] == [{"symbol": "X"}]

# ---------------------------------------------------------------- 3) zamknięcie stopem: kilka wpisów closed-pnl (cel częściowy + reszta) sumuje się
d, ex = setup(plan=ens_plan); run(d); lid = next(iter(state(d)["trades"])); ex.fill_entry("SOLUSDT"); run(d)
qty = state(d)["trades"][lid]["fill_qty"]; now = time.time()
ex.closed.append({"symbol": "SOLUSDT", "closedPnl": "99", "updatedTime": str(int((now - 5 * 86400) * 1000))})       # stara transakcja na tym aktywie: poza oknem
ex.closed.append({"symbol": "ETHUSDT", "closedPnl": "50", "updatedTime": str(int(now * 1000))})                     # inne aktywo
ex.closed.append({"symbol": "SOLUSDT", "closedPnl": str(4.0 * qty / 2), "updatedTime": str(int(now * 1000))})       # połowa na TP1
ex.close_pos("SOLUSDT", 0.0)                                                                                        # reszta na stopie przesuniętym na wejście
run(d); led = lab.read_ledger(d)
assert len(led) == 1 and led[0]["fills"] == 2 and abs(led[0]["pnl"] - 2.0 * qty) < 1e-6 and abs(led[0]["r"] - 0.4) < 1e-3 and led[0]["reason"] == "zamknięta", led
assert "pozycja zamknięta" in events(d)
# 3b) cel częściowy, potem stop czasowy: w przebiegu, który zamyka pozycję, giełda zna dopiero wynik TP1.
#     Rozliczenie musi poczekać do następnego przebiegu, inaczej do dziennika trafiłaby połowa wyniku.
t.LEDGER_SETTLE_AFTER_S = 120
d, ex = setup(plan=ens_plan); run(d); lid = next(iter(state(d)["trades"])); ex.fill_entry("SOLUSDT"); run(d)
qty = state(d)["trades"][lid]["fill_qty"]
ex.closed.append({"symbol": "SOLUSDT", "closedPnl": str(4.0 * qty / 2), "updatedTime": str(int(time.time() * 1000))})        # TP1 na połowie pozycji
st = state(d); st["trades"][lid]["opened"] = int(time.time()) - 4 * 86400; set_state(d, st); run(d)                           # stop czasowy (3 dni) zamyka resztę rynkowo
assert lid not in state(d)["trades"] and len(state(d)["ledger_pending"]) == 1 and lab.read_ledger(d) == [], "w tym samym przebiegu bez rozliczenia"
ex.closed.append({"symbol": "SOLUSDT", "closedPnl": str(-1.0 * qty / 2), "updatedTime": str(int(time.time() * 1000))})       # wynik zamknięcia reszty pojawia się po chwili
st = state(d); st["ledger_pending"][0]["closed"] -= 300; set_state(d, st); run(d)                                             # następny przebieg (5 minut później)
led = lab.read_ledger(d); assert len(led) == 1 and led[0]["fills"] == 2 and abs(led[0]["pnl"] - 1.5 * qty) < 1e-6 and abs(led[0]["r"] - 0.3) < 1e-3, led
t.LEDGER_SETTLE_AFTER_S = 0
# brak wyniku z giełdy przez ponad 2 godziny: wpis bez wyniku (nie blokuje kolejki)
d, ex = setup(plan=ens_plan); run(d); lid = next(iter(state(d)["trades"])); ex.fill_entry("SOLUSDT"); run(d); ex.pos["SOLUSDT"]["size"] = "0"; run(d)
st = state(d); assert len(st["ledger_pending"]) == 1; st["ledger_pending"][0]["closed"] -= 3 * 3600; st["ledger_pending"][0]["opened"] -= 3 * 3600; set_state(d, st); run(d)
led = lab.read_ledger(d); assert len(led) == 1 and led[0]["pnl"] is None and led[0]["r"] is None and state(d)["ledger_pending"] == []
assert lab.execution(led, {})["n"] == 0, "wpis bez wyniku nie liczy się do porównania"
# błąd zapisu dziennika nie zatrzymuje handlu
d, ex = setup(plan=ens_plan); run(d); ex.fill_entry("SOLUSDT"); run(d); ex.close_pos("SOLUSDT", -3.0)
orig = bb.append_ledger; bb.append_ledger = lambda *a, **k: (_ for _ in ()).throw(OSError("dysk pełny"))
run(d); bb.append_ledger = orig
assert bb.read_status(d)["ok"] and "dziennik transakcji: dysk pełny" in events(d) and state(d)["ledger_pending"] == []
# tryb bez prowadzenia transakcji (single): brak rekordu, brak dziennika, bez błędów
os.environ["TRADER_EXIT_MODE"] = "single"; d, ex = setup(); run(d); os.environ.pop("TRADER_EXIT_MODE")
assert not state(d).get("trades") and not state(d).get("ledger_pending") and bb.read_status(d)["ok"]

# ---------------------------------------------------------------- 4) wariant zespołu w planie tradera
t.get_plan = ORIG_GET_PLAN
random.seed(11); now_day = int(time.time() * 1000) // 86400000 * 86400000
def series(n, drift, vol, start=100.0):
    p, out = start, []
    for i in range(n):
        o = p; p *= math.exp(random.gauss(drift, vol)); out.append([now_day - (n - i) * 86400000, o, max(o, p) * 1.004, min(o, p) * 0.996, p, 1000.0 * (1 + abs(random.gauss(0, .4)))])
    return out
UP, BT = series(500, 0.004, 0.015), series(500, 0.001, 0.02, 30000.0)
class Pub:
    def klines(s, sym, tf, n, end=None): return BT if sym == "BTCUSDT" else UP
    def ticker(s, sym): return {"lastPrice": str(UP[-1][4]), "turnover24h": "5e9", "fundingRate": "0.0001"}
    def _request(s, path, params): return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"}, "priceFilter": {"tickSize": "0.01"}}]}
d = tempfile.mkdtemp(); t.HERE = d
c = {"mode": "dryrun", "risk_pct": 1.0, "strategy": "ensemble"}
p_def, _, _ = t.get_plan(c, "SOLUSDT", Pub(), BT, {"events": []}); assert p_def["variant"] == "v2" and p_def["decision"]["variant"] == "v2", p_def.get("variant")
sila = lambda p: next(f for f in p["decision"]["families"] if f["key"] == "sila")
p_v1, _, _ = t.get_plan(dict(c, ens_variant="v1"), "SOLUSDT", Pub(), BT, {"events": []})
assert p_v1["variant"] == "v1" and abs(sila(p_v1)["score"] + sila(p_def)["score"]) < 1e-9 and sila(p_def)["score"] != 0, (sila(p_v1), sila(p_def))     # ta sama rodzina, przeciwny znak
assert "kontrariańsko" in sila(p_def)["text"] and "kontrariańsko" not in sila(p_v1)["text"]
assert t.get_plan(dict(c, ens_variant="nie-ma"), "SOLUSDT", Pub(), BT, {"events": []})[0]["variant"] == "v2", "nieznany wariant = domyślny"
open(os.path.join(d, lab.CUSTOM_FILE), "w").write(json.dumps({"variants": {"a1": {"name": "Auto", "desc": "", "params": {"max_hold": 4, "fam_sign": {"sila": -1}}}}}))
p_a1 = t.get_plan(dict(c, ens_variant="a1"), "SOLUSDT", Pub(), BT, {"events": []})[0]; assert p_a1["variant"] == "a1"
assert "variant" not in t.get_plan(dict(c, strategy="plan"), "SOLUSDT", Pub())[0], "plan klasyczny bez wariantu"
# pełny przebieg (dryrun): wariant z ustawień trafia do statusu
os.environ.update(TRADER_MODE="dryrun"); t.make_pub = lambda c: Pub()
bb.write_settings(d, {"strategy": "ensemble", "symbols": ["SOLUSDT"], "ens_variant": "v1"}); run(d)
assert bb.read_status(d)["variant"] == "v1" and bb.read_status(d)["strategy"] == "ensemble" and "SOLUSDT" in bb.read_status(d)["decisions"]
bb.write_settings(d, {"ens_variant": "a1"}); run(d); assert bb.read_status(d)["variant"] == "a1"
os.remove(os.path.join(d, lab.CUSTOM_FILE)); run(d); assert bb.read_status(d)["variant"] == "v2", "wariant usunięty z rejestru: powrót do domyślnego"

# ---------------------------------------------------------------- 5) ostrzeżenie o strategii klasycznej (raz na dobę, tylko gdy laboratorium ma mocny wynik ujemny)
def lab_json(d, n, mean, tw):
    bb._atomic_write(os.path.join(d, lab.LAB_FILE), json.dumps({"ts": int(time.time()), "champion": "v2", "variants": [{"id": "plan", "hist": {"n": n, "mean_r": mean, "t_week": tw}}]}))
d = tempfile.mkdtemp(); t.HERE = d; bb.write_settings(d, {"strategy": "plan", "symbols": ["SOLUSDT"]})
run(d); assert "UWAGA: strategia klasyczna" not in events(d), "bez danych laboratorium: bez ostrzeżenia"
lab_json(d, 723, -0.134, -2.37); run(d); assert events(d).count("UWAGA: strategia klasyczna") == 1 and "-0.13R" in events(d), events(d)[-400:]
run(d); run(d); assert events(d).count("UWAGA: strategia klasyczna") == 1, "raz na dobę"
for n, mean, tw in ((50, -0.3, -3.0), (723, 0.05, 1.0), (723, -0.02, -0.5)):
    dd = tempfile.mkdtemp(); t.HERE = dd; bb.write_settings(dd, {"strategy": "plan", "symbols": ["SOLUSDT"]}); lab_json(dd, n, mean, tw); run(dd)
    assert "UWAGA: strategia klasyczna" not in events(dd), (n, mean, tw)
dd = tempfile.mkdtemp(); t.HERE = dd; bb.write_settings(dd, {"strategy": "ensemble", "symbols": ["SOLUSDT"]}); lab_json(dd, 723, -0.134, -2.37); run(dd)
assert "UWAGA: strategia klasyczna" not in events(dd), "przy zespole sygnałów bez ostrzeżenia"
# uszkodzony plik laboratorium nie zatrzymuje bota
open(os.path.join(dd, lab.LAB_FILE), "w").write('{"variants": [{"id": "plan"}]}'); run(dd); assert bb.read_status(dd)["ok"]
print("lab bot ok")
