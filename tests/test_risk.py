import sys, math, random, time
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_risk as rk
random.seed(4)
# --- korelacje
def cand(rets): 
    p, out = 100.0, []
    for i, r in enumerate(rets): p *= math.exp(r); out.append([i * 86400000, p, p, p, p, 1])
    return out
base = [random.gauss(0, .02) for _ in range(120)]
A, B = rk.returns_from_candles(cand(base)), rk.returns_from_candles(cand([x * 1.2 for x in base]))
C = rk.returns_from_candles(cand([random.gauss(0, .02) for _ in range(120)]))
cm = rk.corr_matrix({"A": A, "B": B, "C": C}); assert abs(cm[("A", "B")] - 0.8) < 0.01, cm          # idealna korelacja skurczona do 0,8
assert cm[("A", "C")] < 0.6 and rk.rho(cm, "B", "A") == cm[("A", "B")] and rk.rho(cm, "A", "A") == 1 and rk.rho({}, "X", "Y") == 0.6
assert rk.corr_matrix({"A": A, "Z": {1: 0.1}})[("A", "Z")] == 0.6                              # za mało wspólnych dni: priorytet
# --- ryzyko pozycji
pos = [{"symbol": "A", "side": "Buy", "size": "2", "avgPrice": "100", "stopLoss": "95", "positionValue": "200"},
       {"symbol": "B", "side": "Sell", "size": "1", "avgPrice": "50", "stopLoss": "55", "positionValue": "50"},
       {"symbol": "C", "side": "Buy", "size": "1", "avgPrice": "10", "stopLoss": "0", "positionValue": "10"},
       {"symbol": "D", "side": "Buy", "size": "1", "avgPrice": "100", "stopLoss": "100", "positionValue": "100"},      # stop na wejściu: brak ryzyka
       {"symbol": "E", "side": "Buy", "size": "0", "avgPrice": "1"}]
it = rk.position_items(pos); d = {i["symbol"]: i for i in it}
assert len(it) == 4 and d["A"]["risk"] == 10 and d["B"]["risk"] == 5 and d["C"]["risk"] == 1.0 and d["C"]["no_stop"] and d["D"]["risk"] == 0
two = [{"symbol": "A", "side": 1, "risk": 10}, {"symbol": "B", "side": 1, "risk": 10}]
assert abs(rk.corr_risk(two, {("A", "B"): 0.8}) - 10 * math.sqrt(2 + 2 * 0.8)) < 1e-9 and rk.gross_risk(two) == 20
assert rk.corr_risk([{"symbol": "A", "side": 1, "risk": 10}, {"symbol": "B", "side": -1, "risk": 10}], {("A", "B"): 1.0}) < 1e-9       # idealny hedge
assert abs(rk.corr_risk([{"symbol": "A", "side": 1, "risk": 10}, {"symbol": "A", "side": 1, "risk": 5}], {}) - 15) < 1e-9
# --- dopasowanie rozmiaru
new = {"symbol": "B", "side": 1, "risk": 30}
assert rk.fit_scale([], new, {}, 40) == 1.0
assert abs(rk.fit_scale([], new, {}, 15) - 0.5) < 1e-6                                                                           # sama nowa pozycja: limit/ryzyko
ex = [{"symbol": "A", "side": 1, "risk": 10}]; k = rk.fit_scale(ex, {"symbol": "B", "side": 1, "risk": 10}, {("A", "B"): 0.8}, 15)
assert 0 < k < 1 and rk.corr_risk(ex + [{"symbol": "B", "side": 1, "risk": 10 * k}], {("A", "B"): 0.8}) <= 15 + 1e-4 and rk.corr_risk(ex + [{"symbol": "B", "side": 1, "risk": 10 * min(1, k + .02)}], {("A", "B"): .8}) > 14.9
assert rk.fit_scale([{"symbol": "A", "side": 1, "risk": 20}], {"symbol": "B", "side": 1, "risk": 5}, {("A", "B"): 0.8}, 15) == 0.0   # już ponad limitem
ind = [{"symbol": "A", "side": 1, "risk": 12}, {"symbol": "B", "side": 1, "risk": 12}]; g = rk.fit_scale(ind, {"symbol": "C", "side": 1, "risk": 8}, {("A", "B"): 0, ("A", "C"): 0, ("B", "C"): 0}, 20)
assert abs(g - 0.75) < 1e-4                                                                  # niezależne, ale brutto ograniczone
hedge = rk.fit_scale([{"symbol": "A", "side": 1, "risk": 15}], {"symbol": "B", "side": -1, "risk": 7}, {("A", "B"): 0.8}, 15); assert hedge == 1.0   # przeciwny kierunek zmniejsza ryzyko
assert rk.fit_scale([{"symbol": "A", "side": 1, "risk": 15}], {"symbol": "B", "side": 1, "risk": 7}, {("A", "B"): 0.8}, 15) < 0.3   # ten sam kierunek tej samej wielkości się nie mieści
assert rk.fit_scale([{"symbol": "A", "side": 1, "risk": 15}], {"symbol": "B", "side": -1, "risk": 15}, {("A", "B"): 0.8}, 15) < 1   # limit brutto działa także przy hedgu (korelacje potrafią się rozpaść)
s = rk.summary(it, cm, 1000); assert s["positions"][2]["no_stop"] and s["gross_usdt"] == 16
# --- wyłączniki
now = 1_800_000_000.0   # środa? sprawdź niżej
ws = rk.week_start(now); assert time.gmtime(ws).tm_wday == 0 and time.gmtime(ws).tm_hour == 0 and ws <= now < ws + 7 * 86400
cfg = {"streak_losses": 4, "streak_pause_h": 24, "max_weekly_loss_pct": 6, "max_drawdown_pct": 12}
row = lambda ago_h, pnl: {"closedPnl": str(pnl), "updatedTime": str(int((now - ago_h * 3600) * 1000))}
rows = [row(1, -5), row(5, -4), row(9, -3), row(13, -2), row(30, 8)]
b, rs, m = rk.evaluate(now, rows, 1000, 1000, cfg, {}); assert [x["key"] for x in b] == ["streak"] and m["streak"] == 4 and b[0]["until"] == int(now - 3600 + 86400)
b, *_ = rk.evaluate(now, [row(1, 3)] + rows, 1000, 1000, cfg, {}); assert not [x for x in b if x["key"] == "streak"]               # zysk przerywa serię
b, *_ = rk.evaluate(now, [row(1, -1)] + rows[1:], 1000, 1000, cfg, {}); assert [x["key"] for x in b if x["key"] == "streak"]
b, *_ = rk.evaluate(now + 30 * 3600, rows, 1000, 1000, cfg, {}); assert not [x for x in b if x["key"] == "streak"]                  # pauza minęła
b, *_ = rk.evaluate(now, rows, 1000, 1000, cfg, {"reset_ts": now - 3000}); assert not [x for x in b if x["key"] == "streak"]          # po odblokowaniu stare straty się nie liczą
rowsw = [{"closedPnl": "-35", "updatedTime": str(int((ws + 3600) * 1000))}, {"closedPnl": "-30", "updatedTime": str(int((ws + 7200) * 1000))}]
b, *_ = rk.evaluate(ws + 86400, rowsw, 1000, 1000, cfg, {}); wk = [x for x in b if x["key"] == "weekly"]; assert wk and wk[0]["until"] == ws + 7 * 86400        # -65 przy limicie 60
b, *_ = rk.evaluate(ws + 86400, rowsw[:1], 1000, 1000, cfg, {}); assert not [x for x in b if x["key"] == "weekly"]
b, *_ = rk.evaluate(ws + 8 * 86400, rowsw, 1000, 1000, cfg, {}); assert not [x for x in b if x["key"] == "weekly"]                 # nowy tydzień
# spadek od szczytu: zapamiętany, wymaga odblokowania nawet po odrobieniu strat
b, rs, m = rk.evaluate(now, [], 1000, 1000, cfg, {}); assert not b and rs["peak"] == 1000
b, rs, m = rk.evaluate(now, [], 870, 1000, cfg, rs); assert [x["key"] for x in b] == ["drawdown"] and b[0]["manual"] and rs["dd_halt"]["dd"] == 13.0
b, rs2, m = rk.evaluate(now + 99999, [], 1100, 1000, cfg, rs); assert [x["key"] for x in b] == ["drawdown"]                           # odrobione, ale nadal zablokowane
rs3 = rk.apply_reset(rs2, 1100, now); b, rs4, _ = rk.evaluate(now + 1, rows, 1100, 1000, cfg, rs3); assert not b and rs4["peak"] == 1100 and "dd_halt" not in rs4
b, *_ = rk.evaluate(now, [], 899, 1000, cfg, {"peak": 1000}); assert not b                                                            # 10,1% < 12%
print("risk ok")
