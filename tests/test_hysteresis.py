import sys, os, json, time, math, random, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_edge as be, btc_botcfg as bb, btc_learn as bl, btc_collect as bc, btc_analysis as ba

NOW = 1_700_000_000
DAY = 86400

# ---------------------------------------------------------------- 1) wejście vs wyjście (decide)
base = {"n": 60, "mean_r": 0.20, "p_adj": 0.01, "halves": [0.2, 0.2], "recent": {"n": 20, "mean_r": 0.1}}
assert be.decide(base, False) == ("allowed", [], False, []) and be.decide(base, True) == ("allowed", [], False, [])
assert be.entry_failures(base) == be.verdict(base) == []                           # nazwa z wcześniejszych wersji działa

weak = dict(base, p_adj=0.12)                                                      # między progiem wejścia (0,05) a wyjścia (0,20)
st, why, held, wk = be.decide(weak, False)
assert st == "rejected" and not held and any("istotności" in x for x in why), (st, why)
st, why, held, wk = be.decide(weak, True)
assert st == "allowed" and why == [] and held and any("istotności" in x for x in wk), (st, why, held, wk)
st, why, held, _ = be.decide(dict(base, p_adj=0.25), True)
assert st == "rejected" and not held and any("progu wyjścia" in x for x in why), why
# granice: wejście przy dokładnie 0,05; utrzymanie przy dokładnie 0,20
assert be.decide(dict(base, p_adj=be.Q_ENTER), False)[0] == "allowed" and be.decide(dict(base, p_adj=be.Q_ENTER + 1e-6), False)[0] == "rejected"
assert be.decide(dict(base, p_adj=be.Q_EXIT), True)[:3] == ("allowed", [], True) and be.decide(dict(base, p_adj=be.Q_EXIT + 1e-6), True)[0] == "rejected"
# średni wynik: pod progiem wejścia, ale dodatni = utrzymane; niedodatni = wyjście
assert be.decide(dict(base, mean_r=0.03), False)[0] == "rejected" and be.decide(dict(base, mean_r=0.03), True)[2] is True
st, why, *_ = be.decide(dict(base, mean_r=-0.01), True); assert st == "rejected" and any("już nie dodatni" in x for x in why)
# połówki historii i ostatni rok: lekko ujemne = utrzymane, wyraźnie ujemne = wyjście
assert be.decide(dict(base, halves=[0.3, -0.05]), True)[2] is True and be.decide(dict(base, halves=[0.3, -0.05]), False)[0] == "rejected"
st, why, *_ = be.decide(dict(base, halves=[0.3, -0.15]), True); assert st == "rejected" and any("połówek" in x for x in why)
assert be.decide(dict(base, recent={"n": 12, "mean_r": -0.05}), True)[2] is True
st, why, *_ = be.decide(dict(base, recent={"n": 12, "mean_r": -0.2}), True); assert st == "rejected" and any("wygasła" in x for x in why)
assert be.decide(dict(base, recent={"n": 3, "mean_r": -0.9}), True)[0] == "allowed"          # za mało świeżych transakcji, by oceniać
st, why, *_ = be.decide(dict(base, n=20), True); assert st == "rejected" and any("za mało" in x for x in why)   # histereza nie zastępuje minimum próby

# ---------------------------------------------------------------- 2) migotanie: ta sama ścieżka p, stara reguła kontra histereza
rnd = random.Random(7)
q, path = 0.10, []
for _ in range(250):
    q = abs(q + rnd.gauss(0, 0.025)); q = q if q < 0.40 else 0.80 - q; path.append(max(q, 0.002))
def flips(states): return sum(1 for a, b in zip(states, states[1:]) if a != b)
old_rule = [x <= 0.10 for x in path]                                              # dotychczas: jeden próg 0,10
hyst, was = [], False
for x in path:
    was = be.decide(dict(base, p_adj=x), was)[0] == "allowed"; hyst.append(was)
assert flips(old_rule) >= 12, flips(old_rule)                                     # scenariusz naprawdę migocze
assert flips(hyst) * 2 <= flips(old_rule), (flips(hyst), flips(old_rule))        # histereza: co najmniej dwa razy mniej zmian
print("migotanie: stara reguła", flips(old_rule), "zmian, histereza", flips(hyst), "zmian")

# ---------------------------------------------------------------- 3) wspólna korekta FDR dla dwóch strategii
def raw_asset(p, mean=0.3, n=60):
    return {"status": "pending", "n": n, "mean_r": mean, "win_rate": 55.0, "p": p, "lb": 0.1, "halves": [mean, mean], "recent": {"n": 20, "mean_r": mean}, "_props": []}
def raw_family(ps):          # {strategia: {symbol: p}}
    return {st: {s: raw_asset(p) for s, p in d.items()} for st, d in ps.items()}
# jedno trafienie (p = 0,004) wśród 10 aktywów klasycznej strategii; zespół sygnałów nic nie znajduje (10 aktywów z p = 0,5)
ps = {"plan": {"AUSDT": 0.004, **{f"X{i}USDT": 0.5 for i in range(9)}}, "ensemble": {f"Y{i}USDT": 0.5 for i in range(10)}}
maps = be.finalize(raw_family(ps), None, NOW, [])
a = maps["plan"]["assets"]["AUSDT"]
assert a["p_adj_solo"] == 0.04 and a["p_adj"] == 0.08, (a["p_adj_solo"], a["p_adj"])  # 10 testów osobno kontra 20 we wspólnej rodzinie
assert maps["plan"]["allowed"] == [] and maps["ensemble"]["allowed"] == [] and maps["plan"]["params"]["family"] == 20
assert maps["plan"]["hysteresis"]["entry_pass_joint"] == 0 and maps["plan"]["hysteresis"]["entry_pass_solo"] == 1
only = be.finalize(raw_family({"plan": ps["plan"]}), None, NOW, [])                # jedna strategia: dawna korekta, przechodzi
assert only["plan"]["allowed"] == ["AUSDT"] and only["plan"]["assets"]["AUSDT"]["p_adj"] == 0.04
# to samo trafienie w obu strategiach: BH liczy dwa odkrycia, więc q nie rośnie (korekta nie karze za powtórzone, spójne wyniki)
both = be.finalize(raw_family({"plan": {"AUSDT": 0.004, **{f"X{i}USDT": 0.5 for i in range(9)}}, "ensemble": {"AUSDT": 0.004, **{f"Y{i}USDT": 0.5 for i in range(9)}}}), None, NOW, [])
assert both["plan"]["assets"]["AUSDT"]["p_adj"] == 0.04 and both["plan"]["allowed"] == ["AUSDT"] and both["ensemble"]["allowed"] == ["AUSDT"]

# ---------------------------------------------------------------- 4) histereza względem poprzedniej mapy, since, held
prev = {"ts": NOW - DAY, "allowed": ["AUSDT"], "assets": {"AUSDT": {"status": "allowed", "since": NOW - 5 * DAY}, "BUSDT": {"status": "rejected", "since": NOW - 9 * DAY}}}
mid = {"plan": {"AUSDT": 0.12, "BUSDT": 0.12}}                                     # oba w strefie 0,05–0,20 (rodzina 2, więc q = p)
m = be.finalize(raw_family(mid), prev, NOW, [])["plan"]
A, B = m["assets"]["AUSDT"], m["assets"]["BUSDT"]
assert A["status"] == "allowed" and A["held"] and A["held_why"] and A["since"] == NOW - 5 * DAY, A       # utrzymane, data zmiany stanu zachowana
assert B["status"] == "rejected" and not B["held"] and B["since"] == NOW - 9 * DAY and B["reasons"], B   # nowe aktywo w tej strefie nie wchodzi
assert m["allowed"] == ["AUSDT"] and m["held"] == ["AUSDT"] and m["hysteresis"]["prior_used"] and m["version"] == 2
old_prev = dict(prev, ts=NOW - 11 * DAY)                                           # poprzednia mapa za stara: wszystko od nowa
m = be.finalize(raw_family(mid), old_prev, NOW, [])["plan"]
assert m["allowed"] == [] and not m["hysteresis"]["prior_used"] and m["assets"]["AUSDT"]["since"] == NOW
m = be.finalize(raw_family(mid), None, NOW, [])["plan"]; assert m["allowed"] == [] and m["assets"]["AUSDT"]["since"] == NOW
out = be.finalize(raw_family({"plan": {"AUSDT": 0.6, "BUSDT": 0.6}}), prev, NOW, [])["plan"]      # wyraźnie za słabe: wycofane, since = teraz
assert out["allowed"] == [] and out["assets"]["AUSDT"]["status"] == "rejected" and out["assets"]["AUSDT"]["since"] == NOW and any("progu wyjścia" in x for x in out["assets"]["AUSDT"]["reasons"])
strong = be.finalize(raw_family({"plan": {"AUSDT": 1e-4, "BUSDT": 0.6}}), prev, NOW, [])["plan"]
assert strong["allowed"] == ["AUSDT"] and not strong["assets"]["AUSDT"]["held"] and strong["assets"]["AUSDT"]["since"] == NOW - 5 * DAY
nd = {"plan": {"AUSDT": raw_asset(0.001)}}; nd["plan"]["NODATA"] = {"status": "no_data", "reasons": ["za krótka historia"], "days": 10, "n": 0}
m = be.finalize(nd, None, NOW, [])["plan"]; assert m["assets"]["NODATA"]["status"] == "no_data" and m["assets"]["NODATA"]["held"] is False and m["allowed"] == ["AUSDT"]
# mapa w starym formacie (bez held/since) nie psuje histerezy ani wyboru aktywów
legacy = {"ts": NOW - DAY, "allowed": ["AUSDT"], "assets": {"AUSDT": {"status": "allowed", "filters": []}}}
m = be.finalize(raw_family({"plan": {"AUSDT": 0.12}}), legacy, NOW, [])["plan"]
assert m["assets"]["AUSDT"]["held"] and m["assets"]["AUSDT"]["since"] == legacy["ts"]

# ---------------------------------------------------------------- 5) przejścia i historia decyzji
chain, prevm, trs = [("allow", 1e-4), ("hold", 0.06), ("confirm", 1e-4), ("withdraw", 0.6)], None, []      # rodzina 2 aktywów: q najlepszego = 2p, więc 0,06 daje q = 0,12
for i, (act, p) in enumerate(chain):
    nowi = NOW + i * DAY
    cur = be.finalize(raw_family({"plan": {"AUSDT": p, "ZUSDT": 0.9}}), prevm, nowi, [])["plan"]
    tr = be.transitions(prevm, cur)
    assert [t["action"] for t in tr] == [act], (i, tr)
    trs += [(nowi, tr)]
    prevm = cur
hist = []
for nowi, tr in trs: hist = be.update_history(hist, {"plan": tr}, nowi)
assert [h["action"] for h in hist] == ["allow", "hold", "confirm", "withdraw"] and all(h["symbol"] == "AUSDT" and h["strategy"] == "plan" for h in hist)
assert hist[1]["reasons"] and hist[3]["reasons"] and hist[0]["reasons"] == [] and hist[0]["n"] == 60 and hist[1]["q"] is not None
assert be.churn(hist, NOW + 4 * DAY) == {"plan:AUSDT": 2}                          # wejście + wycofanie; hold i confirm nie liczą się jako zmiana
assert be.churn(hist, NOW + 40 * DAY) == {}                                        # starsze niż 30 dni nie liczą się
big = be.update_history([{"ts": 1, "strategy": "plan", "symbol": "X", "action": "allow"}] * 700, {"plan": [{"symbol": "Y", "action": "allow"}]}, NOW, keep=600)
assert len(big) == 600 and big[-1]["symbol"] == "Y"
d_hist = tempfile.mkdtemp(); assert bb.read_edge_history(d_hist) == []
bb.write_edge_history(d_hist, hist); assert bb.read_edge_history(d_hist) == hist
open(os.path.join(d_hist, bb.EDGE_HISTORY), "w").write("{zepsuty"); assert bb.read_edge_history(d_hist) == []
open(os.path.join(d_hist, bb.EDGE_HISTORY), "w").write(json.dumps({"events": [1, {"a": 1}, "x"]})); assert bb.read_edge_history(d_hist) == [{"a": 1}]

# komunikaty dziennika nauki dla utrzymania i potwierdzenia
held_map = be.finalize(raw_family({"plan": {"AUSDT": 0.12}}), {"ts": NOW - DAY, "allowed": ["AUSDT"], "assets": {"AUSDT": {"status": "allowed"}}}, NOW, [])["plan"]
msgs = be.diff_maps({"allowed": ["AUSDT"], "assets": {"AUSDT": {"status": "allowed"}}}, held_map)
assert len(msgs) == 1 and "zostaje dopuszczone" in msgs[0][1] and "50% mniejsze" in msgs[0][1], msgs
conf = be.finalize(raw_family({"plan": {"AUSDT": 1e-4}}), dict(held_map, ts=NOW - DAY), NOW, [])["plan"]
assert any("znów spełniają" in m[1] for m in be.diff_maps(held_map, conf))
assert not be.diff_maps(held_map, held_map)                                          # brak zmian: brak komunikatów

# ---------------------------------------------------------------- 6) użycie w traderze: mnożnik rozmiaru, lista utrzymanych, kompatybilność
edge = {"ts": NOW, "allowed": ["AUSDT", "BUSDT"], "allowed_ens": ["BUSDT"], "assets": {"AUSDT": {"held": True, "filters": []}, "BUSDT": {"held": False}}, "assets_ens": {"BUSDT": {"held": True}}}
assert be.size_factor(edge, "AUSDT") == be.HELD_SIZE_MULT == 0.5 and be.size_factor(edge, "BUSDT") == 1.0 and be.size_factor(edge, "NOPE") == 1.0
assert be.size_factor(edge, "BUSDT", "ensemble") == 0.5 and be.size_factor(edge, "AUSDT", "ensemble") == 1.0
assert be.size_factor({}, "AUSDT") == 1.0 and be.size_factor(None, "AUSDT") == 1.0 and be.size_factor({"assets": {"AUSDT": {"status": "allowed"}}}, "AUSDT") == 1.0   # stary format
assert be.held_symbols(edge) == ["AUSDT"] and be.held_symbols(edge, "ensemble") == ["BUSDT"] and be.held_symbols({}) == []
assert be.select_symbols(edge, [], {}, NOW, True)[0] == ["AUSDT", "BUSDT"]          # utrzymane nadal jest skanowane

# ---------------------------------------------------------------- 7) pełny obieg btc_learn na kilka dni (backtest podmieniony na kontrolowane p)
def rs_with(n, mean, sd=1.0):
    u = [(1 if i % 2 == 0 else -1) * math.sqrt((n - 1) / n) for i in range(n)]
    return [mean + sd * x for x in u]
def mean_for_p(p, n=40):
    lo, hi = -2.0, 2.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if be.p_mean_positive(rs_with(n, mid)) > p: lo = mid
        else: hi = mid
    return (lo + hi) / 2
assert abs(be.p_mean_positive(rs_with(40, mean_for_p(0.03))) - 0.03) < 1e-4
SCEN, CUR = {}, {}
orig_load, orig_bt = be.load_candles, be.backtest
def load_spy(db, sym): CUR["sym"] = sym; return orig_load(db, sym)
def bt_fake(candles, fee_pct=0, planner=None):
    if len(candles) < be.MIN_HISTORY: return None
    mean = SCEN.get(CUR["sym"], -0.05)
    t0 = 1_500_000_000_000
    return [{"r": r, "filled_ms": t0 + i * 86400000, "closed_ms": t0 + i * 86400000 + 3 * 86400000, "outcome": "tp2"} for i, r in enumerate(rs_with(40, mean))]
be.load_candles, be.backtest = load_spy, bt_fake
class Pub:
    def _request(s, path, params=None):
        return {"list": [{"symbol": "ETHUSDT", "turnover24h": "9e9"}, {"symbol": "BTCUSDT", "turnover24h": "2e10"}]}
    def klines(s, sym, tf, n, end=None):
        if end: return []
        now_ms = int(time.time() * 1000) // 86400000 * 86400000
        rr = random.Random(sum(map(ord, sym))); p = 100.0; out = []
        for i in range(900):
            p *= math.exp(rr.gauss(0, 0.03)); out.append([now_ms - (900 - i) * 86400000, p, p * 1.01, p * 0.99, p, 1000.0])
        return out
d = tempfile.mkdtemp(); bb.write_settings(d, {"symbols": ["AAAUSDT", "BBBUSDT"]})
db = bc.DB(os.path.join(d, "x.db"))
base_now = int(time.time())
days = [("allow", 1e-4), ("hold", 0.03), ("confirm", 1e-4), ("withdraw", 0.30)]
for i, (act, p) in enumerate(days):
    SCEN["AAAUSDT"] = mean_for_p(p)
    m = bl.run(d, db, Pub(), now=base_now + i * DAY, say=lambda *_: None)
    A, E = m["assets"]["AAAUSDT"], m["assets_ens"]["AAAUSDT"]
    if act == "allow":
        assert A["status"] == E["status"] == "allowed" and not A["held"] and m["allowed"] == ["AAAUSDT"] and m["allowed_ens"] == ["AAAUSDT"], (A, E)
        assert m["version"] == 2 and m["params"]["family"] == 8 and m["params"]["fdr_q_enter"] == 0.05 and m["params"]["fdr_q_exit"] == 0.20
        assert A["p_adj"] < 0.01 and A["p_adj_solo"] <= A["p_adj"]
    elif act == "hold":       # dowody słabsze niż przy wejściu, ale nie poniżej progu wyjścia: zostaje, oznaczone, pozycje mniejsze
        assert 0.05 < A["p_adj"] <= 0.20, A["p_adj"]
        assert A["status"] == E["status"] == "allowed" and A["held"] and E["held"], (A, E)
        assert m["held"] == ["AAAUSDT"] and m["held_ens"] == ["AAAUSDT"] and A["since"] == base_now, (m["held"], A["since"])
        assert be.size_factor(bb.read_edge(d), "AAAUSDT") == 0.5 and be.size_factor(bb.read_edge(d), "AAAUSDT", "ensemble") == 0.5
    elif act == "confirm":
        assert A["status"] == "allowed" and not A["held"] and m["held"] == [] and A["since"] == base_now
    else:
        assert A["status"] == "rejected" and m["allowed"] == [] and m["allowed_ens"] == [] and any("progu wyjścia" in x for x in A["reasons"]), A
        assert A["since"] == base_now + 3 * DAY
hist = bb.read_edge_history(d)
seq = [(h["strategy"], h["action"]) for h in hist if h["symbol"] == "AAAUSDT"]
assert seq == [(s, a) for a, _ in days for s in ("plan", "ensemble")], seq
assert all(h["symbol"] == "AAAUSDT" for h in hist), [h for h in hist if h["symbol"] != "AAAUSDT"]
learn = " | ".join(e["msg"] for e in bb.read_events(d, 100, bb.LEARN_LOG))
for must in ("dopuszczone do handlu", "zostaje dopuszczone", "znów spełniają", "wycofane z handlu", "Korekta FDR wspólna", "[zespół]"):
    assert must in learn, must
# poprzednia mapa starsza niż 10 dni: histereza nie działa, aktywo w strefie utrzymania nie wchodzi od nowa
SCEN["AAAUSDT"] = mean_for_p(0.03)
m = bl.run(d, db, Pub(), now=base_now + 20 * DAY, say=lambda *_: None)
assert not m["hysteresis"]["prior_used"] and m["assets"]["AAAUSDT"]["status"] == "rejected" and "Brak świeżej poprzedniej mapy" in bb.read_events(d, 100, bb.LEARN_LOG)[-1]["msg"] + " ".join(e["msg"] for e in bb.read_events(d, 100, bb.LEARN_LOG)[-6:])
# mapa zapisana na dysku jest poprawnym JSON bez pól roboczych
raw_txt = open(os.path.join(d, bb.EDGE)).read(); assert "_props" not in raw_txt and json.loads(raw_txt)["version"] == 2
be.load_candles, be.backtest = orig_load, orig_bt

# ---------------------------------------------------------------- 8) awaria zespołu sygnałów nie psuje klasycznej mapy; awaria klasycznej przerywa
db3 = bc.DB(os.path.join(tempfile.mkdtemp(), "e.db"))
def mk_candles(n=900):
    p, rows = 100.0, []
    for i in range(n):
        p *= math.exp(random.gauss(0, 0.03)); o = p
        rows.append((1_500_000_000 + i * 86400, o, max(o, p) * 1.01, min(o, p) * 0.99, p, 1000.0))
    return rows
for sym in ("BTCUSDT", "AUSDT"): db3.put_candles(be.key(sym), "1d", mk_candles(), "t")
orig_as = be.analyze_strategy
def bad_ens(db, symbols, now_s, strategy, *a):
    if strategy == "ensemble": raise RuntimeError("awaria zespołu")
    return orig_as(db, symbols, now_s, strategy, *a)
be.analyze_strategy = bad_ens
maps, errs = be.build_maps(db3, ["AUSDT"], NOW)
assert set(maps) == {"plan"} and "awaria zespołu" in errs["ensemble"]
def bad_plan(db, symbols, now_s, strategy, *a): raise RuntimeError("awaria planu")
be.analyze_strategy = bad_plan
try: be.build_maps(db3, ["AUSDT"], NOW); raise SystemExit("powinno przerwać")
except RuntimeError as e: assert "awaria planu" in str(e)
be.analyze_strategy = orig_as
# w pełnym obiegu (bez podmiany backtestu) mapa powstaje, a jej struktura ma nowe pola
mm = be.build_map(db3, ["AUSDT"], NOW)
assert mm["version"] == 2 and "held" in mm and "hysteresis" in mm and all("since" in a and "held" in a for a in mm["assets"].values())

print("hysteresis edge ok")
