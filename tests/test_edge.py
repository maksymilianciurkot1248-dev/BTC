import sys, os, time, random, math, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_edge as be, btc_botcfg as bb, btc_learn as bl, btc_collect as bc, btc_analysis as ba
random.seed(5)
# --- statystyka
assert be.p_mean_positive([1.0] * 40 + [-0.5] * 10) < 0.001 and be.p_mean_positive([-1.0] * 40 + [0.5] * 10) > 0.99
s = be.summarize([1, -1, 2, -0.5] * 10); assert s["n"] == 40 and s["win_rate"] == 50.0 and s["lb"] < s["mean_r"]
assert be.p_less([-1.0, -0.8, -1.2] * 6, [1.0, 0.9, 1.1] * 6) < 0.001 and be.p_less([1.0] * 12, [-1.0] * 12) > 0.99
# --- werdykt: kolejne powody odrzucenia
base = {"n": 60, "mean_r": 0.20, "p_adj": 0.01, "halves": [0.2, 0.2], "recent": {"n": 20, "mean_r": 0.1}}
assert be.verdict(base) == []
assert any("za mało" in x for x in be.verdict(dict(base, n=10)))
assert any("poniżej progu" in x for x in be.verdict(dict(base, mean_r=0.01)))
assert any("istotności" in x for x in be.verdict(dict(base, p_adj=0.5)))
assert any("połówek" in x for x in be.verdict(dict(base, halves=[0.4, -0.1])))
assert any("wygasa" in x for x in be.verdict(dict(base, recent={"n": 12, "mean_r": -0.2})))
# --- mapa: sztuczne transakcje (backtest podmieniony): jedno aktywo z przewagą i filtrem BTC, jedno bez przewagi
def fake_props(good):
    out = []
    t0 = 1_600_000_000_000
    for i in range(80):
        down = i % 4 == 0
        r = (-0.8 if down else 0.45) + random.gauss(0, 0.2) if good else random.gauss(-0.1, 1.0)
        out.append({"r": r, "filled_ms": t0 + i * 86400000, "closed_ms": t0 + i * 86400000 + 3 * 86400000, "outcome": "tp2"})
    return out
db = bc.DB(os.path.join(tempfile.mkdtemp(), "e.db"))
def mk_candles(n=900, drift=0.0):
    p, rows = 100.0, []
    for i in range(n):
        p *= math.exp(random.gauss(drift, 0.03)); o = p * (1 + random.gauss(0, .004))
        rows.append((1_500_000_000 + i * 86400, o, max(o, p) * 1.01, min(o, p) * 0.99, p, 1000.0))
    return rows
for sym in ("BTCUSDT", "GOODUSDT", "BADUSDT"): db.put_candles(be.key(sym), "1d", mk_candles(), "t")
orig = be.backtest
be.backtest = lambda candles, fee_pct=0, planner=None: fake_props(candles[-1][4] == GOOD_CLOSE) if False else None
# podmiana per aktywo przez zamknięcie ostatniej świecy
last = {s: be.load_candles(db, s)[-1][4] for s in ("GOODUSDT", "BADUSDT")}
be.backtest = lambda candles, fee_pct=0, planner=None: (fake_props(candles[-1][4] == last["GOODUSDT"]) if len(candles) >= be.MIN_HISTORY else None)
m = be.build_map(db, ["GOODUSDT", "BADUSDT", "NODATAUSDT"], now_s=1_700_000_000)
g, b, nd = m["assets"]["GOODUSDT"], m["assets"]["BADUSDT"], m["assets"]["NODATAUSDT"]
assert nd["status"] == "no_data" and g["status"] == "allowed" and b["status"] == "rejected" and m["allowed"] == ["GOODUSDT"], (g, b)
assert b["reasons"] and g["n"] == 80 and g["p_adj"] < 0.10
be.backtest = orig
# --- dziennik zmian
old = {"allowed": ["BADUSDT"], "assets": {"BADUSDT": {"reasons": ["x"], "filters": []}}}
d = be.diff_maps(old, m); kinds = {k: msg for k, msg in d}
assert any("GOODUSDT: dopuszczone" in x[1] for x in d) and any(x[0] == "alert" and "BADUSDT: wycofane" in x[1] for x in d)
# --- filtr kontekstu: transakcje w kontekście (flaga) gorsze
props = [(-0.8 + random.gauss(0, .2), True, False) for _ in range(20)] + [(0.5 + random.gauss(0, .2), False, False) for _ in range(40)]
f = be.find_filters(props); assert [x["flag"] for x in f] == ["btc_down"], f
assert be.find_filters([(0.4, True, False)] * 20 + [(0.5, False, False)] * 20) == []                    # brak różnicy: brak filtra
# --- wybór aktywów, kwarantanna, kontekst
now = 1_700_000_000
edge = {"ts": now - 3600, "allowed": ["ETHUSDT", "SOLUSDT"], "assets": {"ETHUSDT": {"filters": [{"flag": "btc_down"}]}}, "events": [now + 3600]}
assert be.select_symbols(edge, ["BTCUSDT"], {}, now, True)[0] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
assert be.select_symbols(edge, ["BTCUSDT"], {}, now, False)[0] == ["BTCUSDT"]
sel, notes = be.select_symbols(dict(edge, ts=now - 10 * 86400), ["BTCUSDT"], {}, now, True); assert sel == ["BTCUSDT"] and "nieaktualna" in notes[0]
sel, notes = be.select_symbols(edge, ["BTCUSDT"], {"until": {"ETHUSDT": now + 100}}, now, True); assert "ETHUSDT" not in sel and any("kwarantanna" in n for n in notes)
sel, notes = be.select_symbols({"ts": now, "allowed": []}, [], {}, now, True); assert sel == [] and "nie handluje" in notes[-1]
rows = [{"symbol": "SOLUSDT", "closedPnl": "-5", "updatedTime": str((now - i * 60) * 1000)} for i in range(6)]
q, new = be.quarantine_update(rows, {}, now, unit_usd=5.0); assert new == ["SOLUSDT"] and q["until"]["SOLUSDT"] > now
q2, new2 = be.quarantine_update(rows, q, now + 8 * 86400, unit_usd=5.0); assert new2 == [] and not q2["until"]       # po wygaśnięciu stare straty się nie liczą
assert be.quarantine_update([dict(r, closedPnl="-0.5") for r in rows], {}, now, 5.0)[1] == []                    # drobne straty: bez kwarantanny
btc = [[(now - (130 - i) * 86400) * 1000, 100, 101, 99, 100.0 - i * 0.5 if i > 60 else 100.0, 1] for i in range(130)]
ctx = be.context_now(btc, edge, now); assert ctx["btc_down"] and ctx["near_event"]
assert be.blocked_by_filters(edge, "ETHUSDT", ctx) == ["btc_down"] and be.blocked_by_filters(edge, "ETHUSDT", {"btc_down": False}) == [] and be.blocked_by_filters(edge, "SOLUSDT", ctx) == []
# --- btc_learn end-to-end z udawanym Bybit
class Pub:
    def __init__(s): s.calls = 0
    def _request(s, path, params=None):
        return {"list": [{"symbol": "ETHUSDT", "turnover24h": "9e9"}, {"symbol": "USDCUSDT", "turnover24h": "9e9"}, {"symbol": "TINYUSDT", "turnover24h": "1e6"}, {"symbol": "BTCUSDT", "turnover24h": "2e10"}, {"symbol": "BTC-27DEC24", "turnover24h": "9e9"}]}
    def klines(s, sym, tf, n, end=None):
        if end: return []
        now_ms = int(time.time() * 1000) // 86400000 * 86400000
        random.seed(hash(sym) % 1000); p = 100.0; out = []
        for i in range(900):
            p *= math.exp(random.gauss(0, 0.03)); out.append([now_ms - (900 - i) * 86400000, p, p * 1.01, p * 0.99, p, 1000.0])
        return out
assert bl.fetch_universe(Pub()) == ["BTCUSDT", "ETHUSDT"]
d = tempfile.mkdtemp(); bb.write_settings(d, {"symbols": ["SUIUSDT"]})
db2 = bc.DB(os.path.join(d, "x.db"))
m2 = bl.run(d, db2, Pub(), say=lambda *_: None)
assert set(m2["assets"]) == {"BTCUSDT", "ETHUSDT", "SUIUSDT"} and bb.read_edge(d)["ts"] == m2["ts"]
log = bb.read_events(d, 20, bb.LEARN_LOG); assert any("Mapa przewagi" in e["msg"] for e in log) and any("Dane:" in e["msg"] for e in log), log
cat = bb.read_catalog(d); assert ["ETHUSDT", 9000000000] in cat and not any(x[0] in ("USDCUSDT", "BTC-27DEC24") for x in cat), cat
print("edge ok", {s: (a["status"], a["n"]) for s, a in m2["assets"].items()})
