"""Regresja po analizie algorytmu: zaokrąglenia cen w planie klasycznym, propozycje bez ryzyka, pełniejsze koszty, czas trzymania z planu, równoważność optymalizacji."""
import sys, os, math, random, json, time, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_analysis as ba, btc_edge as be, btc_decision as dc

DAY = ba.DAY_MS

# ---------------------------------------------------------------- 1) dokładność cen zależna od skali
assert [ba.px_digits(p) for p in (77698.6, 2299.6, 1000.0, 999.9, 93.76, 11.155, 8.577, 1.28, 0.9431, 0.07553)] == [1, 1, 1, 2, 3, 3, 4, 4, 5, 6]
assert ba.px_digits(0) == 1 and ba.px_digits(None) == 1 and ba.px_digits(-5) == 1

def series(n, start, drift, vol, seed, t0=1_500_000_000):
    rnd = random.Random(seed); p, out = start, []
    for i in range(n):
        o = p; p *= math.exp(rnd.gauss(drift, vol)); h = max(o, p) * (1 + abs(rnd.gauss(0, vol / 3))); l = min(o, p) * (1 - abs(rnd.gauss(0, vol / 3)))
        out.append([(t0 + i * 86400) * 1000, o, h, l, p, 1000.0 * (1 + abs(rnd.gauss(0, .4)))])
    return out

def run_bt(candles, planner=None, **kw):
    ind, j = ba.build_indicators(candles), ba.new_journal("t")
    for t in range(ba.PLAN_WARMUP, len(candles)):
        ba.journal_step(j, (planner or ba.plan_at)(ind, t, 1.0), candles[:t + 1], DAY, candles[t][0] + DAY, **kw)
    return j["proposals"]

# tanie aktywo (ok. 0,3 USD): wcześniej zaokrąglenie do 0,1 zrównywało wejście ze stopem i wywracało test dzieleniem przez zero
cheap = series(1500, 0.30, 0.0015, 0.035, 3)
props = run_bt(cheap)
filled = [p for p in props if p.get("outcome") in ba.PLAN_FILLED]
assert len(props) > 50 and len(filled) > 10, (len(props), len(filled))
assert all(abs(p["entry"] - p["stop"]) > 0 for p in props)
assert all(0.008 <= abs(p["entry"] - p["stop"]) / p["entry"] <= 0.12 + 1e-9 for p in props), "ryzyko ceny w dozwolonym zakresie liczone po zaokrągleniu"
assert any(round(p["entry"], 1) != p["entry"] for p in props), "poziomy mają dokładność lepszą niż 0,1"
assert len(be.backtest(cheap)) == len(filled)
# plan na żywo dla taniego aktywa: spójne poziomy i opis z właściwą liczbą miejsc
ind = ba.build_indicators(cheap)
pl = next(p for p in (ba.plan_at(ind, t) for t in range(ba.PLAN_WARMUP, len(cheap))) if p and p["action"] != "wait")
sg = 1 if pl["action"] == "long" else -1
assert pl["stop"] * sg < pl["entry"] * sg < pl["tp1"] * sg < pl["tp2"] * sg, pl
assert "0,0" not in pl["reasons"][1] and any(ch.isdigit() for ch in pl["reasons"][1])
# skala BTC bez zmian: poziomy nadal co 0,1
big = series(900, 60000.0, 0.001, 0.03, 4)
pb = [p for p in run_bt(big)]
assert pb and all(round(p["entry"], 1) == p["entry"] and round(p["stop"], 1) == p["stop"] for p in pb)

# ---------------------------------------------------------------- 2) propozycja bez odległości do stopa
bad = {"side": "long", "entry": 0.2, "stop": 0.2, "tp1": 0.3, "tp2": 0.4, "created_ms": 0, "valid_until_ms": 10 * DAY, "max_hold_ms": 7 * DAY, "status": "pending"}
st = ba.simulate_trade(bad, [[DAY, 0.2, 0.25, 0.15, 0.22, 1]], DAY)
assert st["status"] == "closed" and st["outcome"] == "invalidated" and st["r"] is None
j = ba.new_journal("x"); j["proposals"].append(dict(bad, id="b", plan_date="2020-01-01"))
ba.journal_step(j, None, [[DAY, 0.2, 0.25, 0.15, 0.22, 1]], DAY, 2 * DAY)                     # stary dziennik z wadliwą propozycją już się nie wywraca
assert j["proposals"][0]["status"] == "closed" and j["proposals"][0]["closed_ms"] == 2 * DAY
zero = {"action": "long", "date": "2020-01-02", "candle_ms": DAY, "entry": 5.0, "stop": 5.0, "tp1": 6.0, "tp2": 7.0, "rr": 2, "confidence": 60, "atr": 0.2, "valid_until_ms": 9 * DAY, "price": 5.1}
ba.journal_step(j, zero, [[DAY, 5, 5.2, 4.9, 5.1, 1]], DAY, 2 * DAY); assert len(j["proposals"]) == 1, "plan bez ryzyka nie tworzy propozycji"

# ---------------------------------------------------------------- 3) koszty: prowizja, poślizg stopa, funding, luka
def prop(side="long", **kw):
    b = {"side": side, "entry": 100.0, "stop": 95.0 if side == "long" else 105.0, "tp1": 110.0 if side == "long" else 90.0, "tp2": 120.0 if side == "long" else 80.0,
         "created_ms": 0, "valid_until_ms": 5 * DAY, "max_hold_ms": 7 * DAY, "status": "pending"}
    b.update(kw); return b
C = lambda i, o, h, l, c: [i * DAY, o, h, l, c, 1]
k = 100.0 / 5.0 / 100                                   # 1% nominału w R przy ryzyku 5% ceny
# stop w świecy wypełnienia: -1R - prowizja - poślizg; funding 0 (pozycja nie przetrwała doby)
r = ba.simulate_trade(prop(), [C(1, 101, 101, 94, 96)], DAY)
assert r["outcome"] == "sl" and abs(r["r"] - (-1 - 0.075 * k - 0.05 * k - 0.03 * k * 1)) < 2e-3, r["r"]      # świeca wypełnienia liczy się jako 1 doba
# stop po dwóch dobach: funding longa za 2 doby
r = ba.simulate_trade(prop(), [C(1, 101, 101, 99, 100), C(2, 100, 101, 94, 96)], DAY)
assert r["outcome"] == "sl" and abs(r["r"] - (-1 - (0.075 + 0.05 + 0.03 * 2) * k)) < 2e-3, r["r"]
# short nie płaci fundingu (i nic nie dostaje)
r = ba.simulate_trade(prop("short"), [C(1, 99, 101, 99, 100), C(2, 100, 106, 99, 104)], DAY)
assert r["outcome"] == "sl" and abs(r["r"] - (-1 - (0.075 + 0.05) * k)) < 2e-3, r["r"]
# luka: świeca otwarta już pod stopem wypełnia go po cenie otwarcia (gorzej niż -1R)
r = ba.simulate_trade(prop(), [C(1, 101, 101, 99, 100), C(2, 92, 93, 90, 91)], DAY)
assert r["outcome"] == "sl" and abs(r["r"] - ((92 - 100) / 5 - (0.075 + 0.05 + 0.03 * 2) * k)) < 2e-3, r["r"]
# TP1, potem powrót na wejście: połowa zysku, poślizg tylko od pozostałej połowy
r = ba.simulate_trade(prop(), [C(1, 101, 101, 99, 100), C(2, 100, 111, 100.5, 108), C(3, 108, 109, 99, 100)], DAY)
assert r["outcome"] == "tp1_be" and abs(r["r"] - (0.5 * 2 - (0.075 + 0.5 * 0.05 + 0.03 * 3) * k)) < 2e-3, r["r"]
# TP2 i stop czasowy: bez poślizgu stopa
r = ba.simulate_trade(prop(), [C(1, 101, 101, 99, 100), C(2, 100, 111, 100.5, 108), C(3, 108, 121, 107, 120)], DAY)
assert r["outcome"] == "tp2" and abs(r["r"] - (0.5 * 2 + 0.5 * 4 - (0.075 + 0.03 * 3) * k)) < 2e-3, r["r"]
r = ba.simulate_trade(prop(max_hold_ms=2 * DAY), [C(1, 101, 101, 99, 100), C(2, 100, 104, 99.5, 103)], DAY)
assert r["outcome"] == "time_stop" and abs(r["r"] - (3 / 5 - (0.075 + 0.03 * 2) * k)) < 2e-3, r["r"]
# wyłączone koszty dodatkowe = dawny wynik (sama prowizja)
r = ba.simulate_trade(prop(), [C(1, 101, 101, 94, 96)], DAY, funding_pct_day=0, stop_slip_pct=0)
assert abs(r["r"] - (-1 - 0.075 * k)) < 2e-3
# niewypełnione propozycje nadal bez wyniku
assert ba.simulate_trade(prop(), [C(1, 101, 111, 100.5, 108)], DAY)["outcome"] == "missed"
assert ba.simulate_trade(prop(valid_until_ms=DAY), [C(1, 101, 102, 100.5, 101)], DAY)["outcome"] == "expired"

# ---------------------------------------------------------------- 4) czas trzymania pozycji z planu (warianty)
up = series(700, 100.0, 0.003, 0.02, 9)
e7, e3 = dc.Engine("XUSDT", explain=False), dc.Engine("XUSDT", explain=False, params={"max_hold": 3})
p7, p3 = run_bt(up, e7.plan_at), run_bt(up, e3.plan_at)
assert p7 and all(p["max_hold_ms"] == 7 * DAY for p in p7) and all(p["max_hold_ms"] == 3 * DAY for p in p3)
ts3 = [p for p in p3 if p.get("outcome") == "time_stop"]
assert ts3 and all(p["closed_ms"] - p["filled_ms"] == 3 * DAY for p in ts3)
assert all(p["max_hold_ms"] == 7 * DAY for p in run_bt(up)[:5]), "plan klasyczny: nadal 7 dni"

# ---------------------------------------------------------------- 5) optymalizacje nie zmieniają wyników
ind = ba.build_indicators(up); pre = dc._prep(ind)
assert all(pre["vp"][t] == dc._vol_pct(pre["rv"], t) for t in range(len(up))), "percentyl zmienności: okno kroczące = liczenie od nowa"
for t in (220, 300, 455, 699):
    for name in ("piv_lo", "piv_hi"):
        assert ba._recent_pivots(ind, name, t, 3) == [p for i, p in ind[name] if i + 3 <= t and t - i <= 120]
ind_old = {k: v for k, v in ind.items() if not k.endswith("_i")}                       # wskaźniki bez indeksu swingów (np. ze starszego kodu) nadal działają
assert ba._recent_pivots(ind_old, "piv_lo", 455, 3) == ba._recent_pivots(ind, "piv_lo", 455, 3)
btc = series(700, 30000.0, 0.002, 0.02, 10)
def plans(explain, params=None):
    eng = dc.Engine("XUSDT", btc_candles=btc, events_s=[1_500_000_000 + i * 86400 * 30 for i in range(30)], explain=explain, params=params)
    i2 = ba.build_indicators(up)
    return [(lambda p: (p["action"], p["entry"], p["stop"], p["tp1"], p["tp2"], p["confidence"], p.get("size_mult")))(eng.plan_at(i2, t, 1.0)) for t in range(ba.PLAN_WARMUP, len(up))]
assert plans(True) == plans(False) and plans(True, {"fam_sign": {"sila": -1}}) == plans(False, {"fam_sign": {"sila": -1}})
assert plans(False) != plans(False, {"fam_sign": {"sila": -1}}), "wariant kontrariański różni się od v1 na alcie"
# wspólne wskaźniki dla wielu wariantów: wynik jak przy osobnych
shared = ba.build_indicators(up)
a = [be.backtest(up, planner=dc.Engine("XUSDT", btc_candles=btc, explain=False, params=prm).plan_at, ind=shared) for prm in ({}, {"fam_sign": {"sila": -1}}, {"max_hold": 3})]
b = [be.backtest(up, planner=dc.Engine("XUSDT", btc_candles=btc, explain=False, params=prm).plan_at) for prm in ({}, {"fam_sign": {"sila": -1}}, {"max_hold": 3})]
key = lambda ps: [(p["id"], p["side"], p["r"], p["closed_ms"]) for p in ps]
assert [key(x) for x in a] == [key(x) for x in b]
# objaśnienia: w trybie testu brak, na żywo komplet (z informacją o wariancie)
pe = dc.Engine("XUSDT", btc_candles=btc, variant="v2", params={"fam_sign": {"sila": -1}}).plan_at(ba.build_indicators(up), len(up) - 1)
assert pe["variant"] == "v2" and pe["decision"]["variant"] == "v2" and len(pe["decision"]["families"]) == 6
assert any("kontrariańsko" in f["text"] for f in pe["decision"]["families"] if f["key"] == "sila")
assert "decision" not in dc.Engine("XUSDT", btc_candles=btc, explain=False).plan_at(ba.build_indicators(up), len(up) - 1)

# ---------------------------------------------------------------- 6) parametry wariantu
try: dc.make_params({"long_tresh": 0.3}); raise SystemExit("literówka w parametrze powinna być błędem")
except ValueError: pass
assert dc.make_params()["max_hold"] == ba.PLAN_MAX_HOLD_BARS and dc.make_params({"max_hold": 10})["max_hold"] == 10
w0 = {reg: dict(v, sila=0.0) for reg, v in dc.WEIGHTS.items()}
d0 = dc.Engine("XUSDT", btc_candles=btc, params={"weights": w0}).plan_at(ba.build_indicators(up), len(up) - 1)["decision"]
assert next(f for f in d0["families"] if f["key"] == "sila")["weight"] == 0 and abs(sum(f["weight"] for f in d0["families"]) - 1) < 0.01
lo = dc.Engine("XUSDT", btc_candles=btc, explain=False, params={"sides": ["long"]})
assert all(p["action"] != "short" for p in (lo.plan_at(ba.build_indicators(up), t) for t in range(ba.PLAN_WARMUP, len(up))))
print("algofix ok", len(props), "propozycji na tanim aktywie,", len(filled), "wypełnionych")
