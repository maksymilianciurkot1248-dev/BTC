"""Laboratorium wariantów: rejestr, statystyka, diagnoza sygnałów, automatyczni pretendenci, ocena na żywo, wykonanie, pełny przebieg."""
import sys, os, math, random, json, time, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_lab as lab, btc_botcfg as bb, btc_analysis as ba, btc_edge as be, btc_decision as dc, btc_collect as bc, btc_shadow as sh, btc_learn as bl

DAY, WEEK = lab.DAY_MS, lab.WEEK_MS
NOW = 1_780_000_000
NOW_MS = NOW * 1000

# ---------------------------------------------------------------- 1) statystyka
assert abs(lab.t_sf(2.201, 11) - 0.025) < 5e-4 and abs(lab.t_sf(1.96, 100000) - 0.025) < 5e-4 and abs(lab.t_sf(0, 7) - 0.5) < 1e-9
assert abs(lab.t_sf(-2.201, 11) - 0.975) < 5e-4 and abs(lab.t_sf(12.706, 1) - 0.025) < 5e-4 and lab.t_sf(3, 0) == 0.5
m, t, p, n = lab.t_test([1.0, 2.0, 3.0, 4.0]); assert n == 4 and m == 2.5 and abs(t - 2.5 / (math.sqrt(5 / 3) / 2)) < 1e-9 and abs(p - lab.t_sf(t, 3)) < 1e-12
assert lab.t_test([1.0, 1.0])[2] == 1.0 and lab.t_test([])[3] == 0 and lab.t_test([2.0, 2.0, 2.0])[2] == 0.0 and lab.t_test([0.0, 0.0, 0.0])[2] == 1.0
T = lambda week, r, side="long", sym="A": (week * WEEK + DAY, r, side, sym)
tr = [T(1, 1.0), T(1, 0.5, sym="B"), T(2, -1.0), T(4, 2.0, "short")]
assert lab.week_sums(tr) == {1: 1.5, 2: -1.0, 4: 2.0} and lab.week_sums(tr, 2 * WEEK) == {2: -1.0, 4: 2.0}
c = lab.compare(tr, [T(1, 0.5), T(3, 1.0)]); assert c["weeks"] == 4 and abs(c["diff_week"] - (1.0 - 1.0 - 1.0 + 2.0) / 4) < 1e-9 and c["total_a"] == 2.5 and c["total_b"] == 1.5
s = lab.summarize(tr, 5 * WEEK)
assert s["n"] == 4 and s["total_r"] == 2.5 and s["weeks"] == 3 and s["sides"]["short"] == {"n": 1, "mean_r": 2.0} and s["symbols"] == 2 and s["symbols_pos"] == 2 and s["y1"]["n"] == 4
assert lab.summarize([], NOW_MS)["n"] == 0 and lab.summarize([], NOW_MS)["p_week"] == 1.0
# grupowanie tygodniowe: 20 transakcji w jednym tygodniu to jedna obserwacja, nie dwadzieścia
same_week = [T(1, 0.5, sym=f"S{i}") for i in range(20)] + [T(2, -0.4), T(3, 0.1)]
assert lab.summarize(same_week, NOW_MS)["p"] < 0.01 < lab.summarize(same_week, NOW_MS)["p_week"], "naiwne p zawyża istotność skorelowanych transakcji"

# ---------------------------------------------------------------- 2) rejestr wariantów
assert list(lab.variants()) == ["v1", "v2", "v1n"] and lab.CHAMPION_DEFAULT == "v2"
assert lab.BUILTIN["v1"]["params"] == {} and lab.BUILTIN["v2"]["params"] == {"fam_sign": {"sila": -1}}
assert all(w["sila"] == 0.0 for w in lab.BUILTIN["v1n"]["params"]["weights"].values()) and dc.WEIGHTS["trend"]["sila"] == .18, "wagi wbudowane nienaruszone"
assert lab.usage(lab.BUILTIN["v2"]["params"])["sila"]["sign"] == -1 and lab.usage({})["sila"]["sign"] == 1 and lab.usage(lab.BUILTIN["v1n"]["params"])["sila"]["weight"] == 0
assert lab.skey("v1") == "ensemble" and lab.skey("v2") == "v2"
d = tempfile.mkdtemp()
assert lab.champion(d) == "v2" and lab.champion(None) == "v2"
bb.write_settings(d, {"ens_variant": "v1"}); assert lab.champion(d) == "v1"
bb.write_settings(d, {"ens_variant": "zzz"}); assert lab.champion(d) == "v2", "nieznany wariant = domyślny"
for bad in ({"ens_variant": "V2"}, {"ens_variant": 2}, {"ens_variant": "a" * 13}, {"ens_variant": "../x"}):
    try: bb.validate(bad); raise SystemExit(f"powinno odrzucić {bad}")
    except ValueError: pass
open(os.path.join(d, lab.CUSTOM_FILE), "w").write(json.dumps({"variants": {
    "a1": {"name": "Auto: test", "desc": "x", "params": {"fam_sign": {"powrot": -1}}, "created": 1},
    "v2": {"name": "podmiana wbudowanego", "params": {}},                    # nie nadpisuje wbudowanych
    "zly id": {"params": {}}, "a2": {"params": {"nieznany": 1}}, "a3": "nie słownik"}}))
reg = lab.variants(d)
assert list(reg) == ["v1", "v2", "v1n", "a1"] and reg["a1"]["origin"] == "automatyczny" and reg["v2"]["params"] == {"fam_sign": {"sila": -1}}, list(reg)
eng = lab.engine("a1", "XUSDT", d=d); assert eng.variant == "a1" and eng.p["fam_sign"] == {"powrot": -1} and eng.txt is False
assert lab.engine("nie-ma", "XUSDT", d=d).variant == "v2"
open(os.path.join(d, lab.CUSTOM_FILE), "w").write("{zepsuty"); assert list(lab.variants(d)) == ["v1", "v2", "v1n"]

# ---------------------------------------------------------------- 3) IC z grupowaniem po blokach czasu
rnd = random.Random(5)
def samples(n_assets, n_per, beta, same=False, noise=1.0):
    out = []
    base = [(rnd.gauss(0, 1), rnd.gauss(0, 1)) for _ in range(n_per)]
    for a in range(n_assets):
        for i in range(n_per):
            x, e = base[i] if same else (rnd.gauss(0, 1), rnd.gauss(0, 1))
            out.append((i * 10 * DAY, f"S{a}", {"trend": x, "sila": -x, "momentum": rnd.gauss(0, 1), "wybicie": None}, beta * x + noise * e))
    return out
S = samples(8, 150, 0.35)
good, anti, noise_, none = (lab.cluster_ic(S, k) for k in ("trend", "sila", "momentum", "wybicie"))
assert good["ic"] > 0.2 and good["t"] > 4 and abs(anti["ic"] + good["ic"]) < 1e-9 and anti["t"] < -4, (good, anti)
assert abs(noise_["ic"]) < 0.08 and abs(noise_["t"]) < 2.6 and none == {"ic": None, "t": 0.0, "p": 1.0, "blocks": 0, "n": 0}, noise_
assert good["blocks"] == 25 and good["n"] == 1200
# te same dane powielone na 10 identycznych aktywach nie zwiększają istotności (naiwnie t wzrosłoby ~3 razy)
one, ten = lab.cluster_ic(samples(1, 150, 0.2, same=True), "trend"), None
rnd = random.Random(5); base1 = samples(1, 150, 0.2, same=True); rnd = random.Random(5); ten = lab.cluster_ic(samples(10, 150, 0.2, same=True), "trend")
assert ten["n"] == 1500 and abs(ten["ic"] - lab.cluster_ic(base1, "trend")["ic"]) < 1e-9 and abs(ten["t"] - lab.cluster_ic(base1, "trend")["t"]) < 0.06, (ten, lab.cluster_ic(base1, "trend"))
assert lab.cluster_ic([(0, "A", {"trend": 1.0}, 1.0)] * 5, "trend")["n"] == 0, "za mało próbek aktywa: pomijane"
two = lab.cluster_ic(samples(3, 25, 0.5), "trend", block_days=200); assert two["blocks"] == 2 and two["t"] == 0.0 and two["p"] == 1.0 and two["ic"] > 0, "mniej niż 3 bloki: bez testu istotności"

# ---------------------------------------------------------------- 4) diagnoza i automatyczni pretendenci
R = lambda t: {"ic": round(t / 30, 3), "t": t, "blocks": 12}
win = {"trend": R(0.3), "momentum": R(2.4), "wybicie": R(-2.2), "powrot": R(-1.4), "przeplyw": R(0.2), "sila": R(-1.5)}
full = {"trend": R(0.5), "momentum": R(3.1), "wybicie": R(0.4), "powrot": R(-2.9), "przeplyw": R(2.3), "sila": R(-2.7)}
st1 = {r["key"]: r["state"] for r in lab.diagnose(win, full, {})}
assert st1 == {"trend": "flat", "momentum": "ok", "wybicie": "warn", "powrot": "contra", "przeplyw": "faded", "sila": "contra"}, st1
st2 = {r["key"]: r["state"] for r in lab.diagnose(win, full, lab.BUILTIN["v2"]["params"])}
assert st2["sila"] == "ok" and st2["powrot"] == "contra", st2                               # w v2 siła jest już liczona kontrariańsko: dane to potwierdzają
assert {r["key"]: r["state"] for r in lab.diagnose(win, full, lab.BUILTIN["v1n"]["params"])}["sila"] == "off"
assert lab.diagnose({}, {}, {})[0]["state"] == "flat"
d = tempfile.mkdtemp(); reg = lab.variants(d)
new = lab.auto_challengers(d, lab.diagnose(win, full, {}), reg, "v1", NOW)
# siła odwrócona względem v1 to dokładnie v2 (już jest), więc powstaje tylko pretendent z odwróconym powrotem do średniej
assert new == ["a1"] and reg["a1"]["params"] == {"fam_sign": {"powrot": -1}} and "Powrót do średniej" in reg["a1"]["name"], (new, reg.get("a1"))
assert list(lab.variants(d)) == ["v1", "v2", "v1n", "a1"] and lab.variants(d)["a1"]["created"] == NOW
assert lab.auto_challengers(d, lab.diagnose(win, full, {}), lab.variants(d), "v1", NOW) == [], "bez duplikatów"
d = tempfile.mkdtemp(); reg = lab.variants(d)
new = lab.auto_challengers(d, lab.diagnose(win, full, lab.BUILTIN["v2"]["params"]), reg, "v2", NOW)
assert new == ["a1"] and reg["a1"]["params"] == {"fam_sign": {"sila": -1, "powrot": -1}}, reg.get("a1")            # pretendent = mistrz + jedna zmiana
allbad = {k: R(-3.5) for k in dc.FAMILIES}
d = tempfile.mkdtemp(); reg = lab.variants(d); new = lab.auto_challengers(d, lab.diagnose(allbad, allbad, {}), reg, "v1", NOW)
assert len(new) == lab.MAX_AUTO == 2, new                                                    # limit automatycznych pretendentów
assert lab.auto_challengers(d, lab.diagnose(allbad, allbad, {}), lab.variants(d), "v1", NOW) == []
# rodzina odwrócona, która znów działa odwrotnie, wraca do znaku dodatniego
back = lab.diagnose({"sila": R(1.6)}, {"sila": R(2.8)}, lab.BUILTIN["v2"]["params"]); d = tempfile.mkdtemp(); reg = lab.variants(d)
assert next(r for r in back if r["key"] == "sila")["state"] == "contra" and lab.auto_challengers(d, back, reg, "v2", NOW) == ["a1"] and reg["a1"]["params"]["fam_sign"]["sila"] == 1

# ---------------------------------------------------------------- 5) ocena pretendenta na wynikach z cienia
B = 100 * WEEK                       # początek okresu wspólnego (prawdziwy znacznik czasu, nie zero)
SG = {"c": lambda w: 1 if (w // 2) % 2 == 0 else -1, "x": lambda w: 1 if w % 2 == 0 else -1}      # dwa różne, ustalone wzorce wahań tygodniowych
def fwd(weeks, mean, kind="x", start=B, per_week=2):
    """Transakcje z cienia o zadanej średniej i ustalonym wzorcu wahań: wynik = średnia ± 0,5 zależnie od tygodnia."""
    return [(start + w * WEEK + i * DAY, mean + 0.5 * SG[kind](w), "long", f"S{i}") for w in range(weeks) for i in range(per_week)]
END = B + 30 * WEEK
champ = fwd(30, 0.0, "c")
st, txt, cmp_ = lab.verdict(fwd(5, 1.0), champ, 2, B, B + 5 * WEEK); assert st == "wait" and "10 z 30" in txt and cmp_ is None, txt
st, txt, cmp_ = lab.verdict(fwd(30, 1.0, per_week=1)[:20], champ, 2, B, END); assert st == "wait" and "20 z 30" in txt, txt
st, txt, cmp_ = lab.verdict(fwd(30, 1.0), champ, 2, None, END); assert st == "wait" and "0 z 12 tygodni" in txt, txt          # brak wspólnego okresu
st, txt, cmp_ = lab.verdict(fwd(30, 0.6), champ, 2, B, END); assert st == "promote" and cmp_["p"] <= lab.ALPHA / 2 and cmp_["weeks"] == 30 and "wygrywa" in txt, (st, txt)
st, txt, cmp_ = lab.verdict(fwd(30, -0.6), champ, 2, B, END); assert st == "worse" and cmp_["t"] < -2, (st, txt)
st, txt, cmp_ = lab.verdict(fwd(30, 0.05), champ, 2, B, END); assert st == "open" and "bez rozstrzygnięcia" in txt, (st, txt)
# wygrywa z mistrzem, ale sam jest na minusie (mistrz traci jeszcze bardziej): bez awansu
st, txt, cmp_ = lab.verdict(fwd(30, -0.1), fwd(30, -1.2, "c"), 1, B, END); assert st == "open" and cmp_["p"] < 0.001, (st, txt)
# im więcej pretendentów, tym ostrzejszy próg (ten sam wynik: awans przy jednym pretendencie, brak przy czterdziestu)
mid = fwd(30, 0.37)
pm = lab.verdict(mid, champ, 1, B, END)[2]["p"]; assert lab.ALPHA / 40 < pm <= lab.ALPHA, pm
assert lab.verdict(mid, champ, 1, B, END)[0] == "promote" and lab.verdict(mid, champ, 40, B, END)[0] == "open"
# transakcje sprzed wspólnego okresu nie liczą się do porównania
old_wins = fwd(30, 3.0, start=B - 40 * WEEK) + fwd(30, 0.05)
assert lab.verdict([t for t in old_wins if t[0] >= B], champ, 2, B, END)[0] == "open"
# dzienniki cienia -> transakcje
J = {"v2:AUSDT": {"proposals": [{"created_ms": 5, "status": "closed", "outcome": "tp2", "r": 1.5, "closed_ms": 900, "side": "long"}, {"created_ms": 7, "status": "open", "outcome": None, "r": None, "side": "long"},
                                {"created_ms": 9, "status": "closed", "outcome": "missed", "r": None, "closed_ms": 50, "side": "short"}, {"created_ms": 11, "status": "pending", "side": "long"}]},
     "v2:BUSDT": {"proposals": [{"created_ms": 3, "status": "closed", "outcome": "sl", "r": -1.0, "closed_ms": 400, "side": "short"}]}, "v2x:AUSDT": {"proposals": [{"created_ms": 1, "status": "closed", "outcome": "sl", "r": -1, "closed_ms": 2, "side": "long"}]}}
assert lab.fwd_trades(J, "v2") == ([(400, -1.0, "short", "BUSDT"), (900, 1.5, "long", "AUSDT")], 3, 1, 1) and lab.fwd_trades(J, "plan") == ([], None, 0, 0)

# ---------------------------------------------------------------- 6) wykonanie na żywo kontra symulator
JJ = {"v2:SOLUSDT": {"proposals": [{"plan_date": "2026-11-01", "side": "long", "status": "closed", "outcome": "tp2", "r": 1.8, "closed_ms": 1}, {"plan_date": "2026-11-03", "side": "long", "status": "closed", "outcome": "missed", "r": None}]},
      "plan:BTCUSDT": {"proposals": [{"plan_date": "2026-11-02", "side": "short", "status": "closed", "outcome": "sl", "r": -1.02, "closed_ms": 2}]},
      "ensemble:ETHUSDT": {"proposals": [{"plan_date": "2026-11-04", "side": "long", "status": "closed", "outcome": "time_stop", "r": 0.4, "closed_ms": 3}]}}
led = [{"symbol": "SOLUSDT", "strategy": "ensemble", "variant": "v2", "plan_date": "2026-11-01", "side": "long", "r": 1.5}, {"symbol": "BTCUSDT", "strategy": "plan", "variant": None, "plan_date": "2026-11-02", "side": "short", "r": -1.1},
       {"symbol": "ETHUSDT", "strategy": "ensemble", "variant": "v1", "plan_date": "2026-11-04", "side": "long", "r": 0.1}, {"symbol": "SOLUSDT", "strategy": "ensemble", "variant": "v2", "plan_date": "2026-11-03", "side": "long", "r": -1.0},
       {"symbol": "XRPUSDT", "strategy": "ensemble", "variant": "v2", "plan_date": "2026-11-05", "side": "long", "r": None}, {"symbol": "ADAUSDT", "strategy": "ensemble", "variant": "v2", "plan_date": "2026-11-05", "side": "long", "r": 0.3}]
e = lab.execution(led, JJ)
assert e["n"] == 5 and e["matched"] == 3 and e["only_real"] == 1 and abs(e["mean_real_m"] - (1.5 - 1.1 + 0.1) / 3) < 1e-3 and abs(e["mean_sim_m"] - (1.8 - 1.02 + 0.4) / 3) < 1e-3 and abs(e["diff"] - ((1.5 - 1.8) + (-1.1 + 1.02) + (0.1 - 0.4)) / 3) < 1e-3, e
assert lab.execution([], JJ) == {"n": 0, "mean_real": None, "matched": 0, "only_real": 0}
d = tempfile.mkdtemp(); assert lab.read_ledger(d) == []
bb.append_ledger(d, led[0]); bb.append_ledger(d, led[1]); open(os.path.join(d, lab.LEDGER), "a").write("zepsuta linia\n{\"bez\": \"symbolu\"}\n")
assert lab.read_ledger(d) == led[:2]

# ---------------------------------------------------------------- 7) pełny przebieg na bazie z syntetycznymi świecami
def series(n, start, drift, vol, seed, t0=NOW - 1200 * 86400):
    r = random.Random(seed); p, out = start, []
    for i in range(n):
        o = p; p *= math.exp(r.gauss(drift, vol)); h = max(o, p) * (1 + abs(r.gauss(0, vol / 3))); l = min(o, p) * (1 - abs(r.gauss(0, vol / 3)))
        out.append((t0 + i * 86400, o, h, l, p, 1000.0 * (1 + abs(r.gauss(0, .4)))))
    return out
d = tempfile.mkdtemp(); db = bc.DB(os.path.join(d, "x.db"))
SY = ["BTCUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT"]
for i, sym in enumerate(SY): db.put_candles(be.key(sym), "1d", series(1200, [30000, 2.0, 0.4, 150][i], 0.0008, 0.03, 40 + i), "t")
out, msgs = lab.run(d, db, SY + ["NODATA"], NOW)
ids = [v["id"] for v in out["variants"]]
assert ids[:4] == ["plan", "v1", "v2", "v1n"] and out["champion"] == "v2" and out["version"] == 1, ids
roles = {v["id"]: v["role"] for v in out["variants"]}; assert roles["plan"] == "classic" and roles["v2"] == "champion" and roles["v1"] == "challenger"
for v in out["variants"]:
    assert v["hist"]["n"] > 0 and v["fwd"]["n"] == 0 and set(v["hist"]) >= {"mean_r", "total_r", "p_week", "t_week", "y1", "y3", "years", "sides", "symbols_pos"}, v["id"]
    assert (v["role"] != "challenger") or (v["status"] == "wait" and "za mało danych z cienia" in v["verdict"] and "hist_vs_champion" in v)
assert len(out["signals"]["families"]) == 6 and out["signals"]["samples"] > 100 and all(r["state"] in ("ok", "faded", "flat", "warn", "contra", "off") for r in out["signals"]["families"])
assert out["proposals"] == [] and out["execution"]["n"] == 0 and out["costs"] == {"fee_pct": ba.PLAN_FEE_PCT, "funding_pct_day": ba.PLAN_FUNDING_PCT_DAY, "stop_slip_pct": ba.PLAN_STOP_SLIP_PCT}
assert any("test historyczny wariantów odświeżony" in m for _, m in msgs) and lab.read(d)["ts"] == NOW
hist1 = lab.load_hist(d); assert set(hist1["trades"]) >= {"plan", "v1", "v2", "v1n"} and hist1["ts"] == NOW
# wynik historyczny wariantu = zwykły test wsteczny tej samej logiki (ten sam silnik, te same koszty)
btc = be.load_candles(db, "BTCUSDT")
ref = sorted([p["closed_ms"], p["r"], p["side"], "AAAUSDT"] for p in be.backtest(be.load_candles(db, "AAAUSDT"), planner=dc.Engine("AAAUSDT", btc_candles=btc, events_s=[], params={"fam_sign": {"sila": -1}}).plan_at))
assert [x for x in hist1["trades"]["v2"] if x[3] == "AAAUSDT"] == ref and len(ref) > 0
assert sorted([p["closed_ms"], p["r"], p["side"], "BBBUSDT"] for p in be.backtest(be.load_candles(db, "BBBUSDT"))) == [x for x in hist1["trades"]["plan"] if x[3] == "BBBUSDT"]
# drugi przebieg następnego dnia: test historyczny z pamięci podręcznej, bez powtarzania komunikatów
out2, msgs2 = lab.run(d, db, SY + ["NODATA"], NOW + 86400)
assert lab.load_hist(d)["ts"] == NOW and msgs2 == [] and out2["hist_ts"] == NOW, msgs2
# po tygodniu albo po zmianie wariantów: odświeżenie
out3, msgs3 = lab.run(d, db, SY, NOW + 8 * 86400); assert lab.load_hist(d)["ts"] == NOW + 8 * 86400 and any("odświeżony" in m for _, m in msgs3)
# zmiana mistrza w ustawieniach
bb.write_settings(d, {"ens_variant": "v1"}); out4, _ = lab.run(d, db, SY, NOW + 9 * 86400)
assert out4["champion"] == "v1" and {v["id"]: v["role"] for v in out4["variants"]}["v2"] == "challenger"
bb.write_settings(d, {"ens_variant": "v2"})

# ---------------------------------------------------------------- 8) wniosek o awans i alarm z wyników cienia (dzienniki wstrzyknięte)
def journal(vid, mean, kind="x", weeks=30, per_week=2):
    props = []
    for w in range(weeks):
        for i in range(per_week):
            ms = NOW_MS - (weeks - w) * WEEK + i * DAY
            props.append({"created_ms": ms - 3 * DAY, "status": "closed", "outcome": "time_stop", "r": round(mean + 0.5 * SG[kind](w), 3), "closed_ms": ms, "side": "long", "plan_date": "x"})
    return {f"{lab.skey(vid)}:AAAUSDT": {"proposals": props}}
# na losowych świecach wszystkie warianty mają ujemną historię; pretendentowi v1 podstawiamy dodatnią (wniosek wymaga nieujemnej historii)
hh = lab.load_hist(d); assert all(lab.summarize([tuple(x) for x in v], NOW_MS)["mean_r"] < 0 for v in hh["trades"].values())
hh["trades"]["v1"] = [[NOW_MS - i * 5 * DAY, 0.4 if i % 3 else -0.5, "long", "AAAUSDT"] for i in range(60)]
bb._atomic_write(os.path.join(d, lab.HIST_FILE), json.dumps(hh))
jj = {**journal("v2", 0.0, "c"), **journal("v1", 0.7), **journal("v1n", 0.02)}
o, m = lab.run(d, db, SY, NOW + 10 * 86400, journals=jj)
assert lab.load_hist(d)["trades"]["v1"] == hh["trades"]["v1"], "pamięć podręczna historii użyta (bez przeliczania)"
row = {v["id"]: v for v in o["variants"]}
assert row["v1"]["status"] == "promote" and row["v1n"]["status"] == "open" and row["v2"]["fwd"]["n"] == 60 and row["v1"]["fwd"]["since"], (row["v1"]["status"], row["v1"]["verdict"], row["v1n"]["verdict"])
assert row["v1"]["vs_champion"]["weeks"] >= 30 and abs(row["v1"]["fwd"]["mean_r"] - 0.7) < 1e-6
assert [p["variant"] for p in o["proposals"]] == ["v1"] and any("wniosek o awans wariantu v1" in x for _, x in m), m
o2, m2 = lab.run(d, db, SY, NOW + 11 * 86400, journals=jj)
assert [p["variant"] for p in o2["proposals"]] == ["v1"] and o2["proposals"][0]["ts"] == o["proposals"][0]["ts"] and not any("wniosek" in x for _, x in m2), "wniosek zgłaszany raz"
# pretendent wygrywa na żywo, ale ma ujemną historię: sprzeczne dowody, bez wniosku (zmienić wariant możesz ręcznie)
oh, mh = lab.run(d, db, SY, NOW + 11 * 86400 + 60, journals={**journal("v2", 0.0, "c"), **journal("v1n", 0.7)})
rh = {v["id"]: v for v in oh["variants"]}["v1n"]
assert rh["status"] == "hold" and "bez wniosku o awans" in rh["verdict"] and "wygrywa z mistrzem" in rh["verdict"] and oh["proposals"] == [], rh["verdict"]
# mistrz wyraźnie traci na żywo: alarm (raz)
jl = {**journal("v2", -0.6, "c"), **journal("v1", -0.5)}
o3, m3 = lab.run(d, db, SY, NOW + 12 * 86400, journals=jl)
assert o3["alerts"] and "traci na żywo" in o3["alerts"][0] and any(k == "alert" for k, _ in m3) and o3["proposals"] == [], (o3["alerts"], m3)
o4, m4 = lab.run(d, db, SY, NOW + 13 * 86400, journals=jl); assert o4["alerts"] and not any(k == "alert" and "traci" in x for k, x in m4)
# pretendent zarejestrowany później: porównanie tylko na wspólnym okresie (6 tygodni to za mało)
late = {**journal("v2", 0.0, "c"), **journal("v1", 0.9, weeks=6)}
o5, _ = lab.run(d, db, SY, NOW + 14 * 86400, journals=late); assert {v["id"]: v for v in o5["variants"]}["v1"]["status"] == "wait"

# ---------------------------------------------------------------- 9) dzienna nauka: mapa przewagi w wariancie mistrza, cień wariantów, laboratorium
class Pub:
    def _request(s, path, params=None): return {"list": [{"symbol": x, "turnover24h": "9e9"} for x in SY]}
    def klines(s, sym, tf, n, end=None): return []
d2 = tempfile.mkdtemp(); db2 = bc.DB(os.path.join(d2, "y.db"))
now2 = int(time.time())
for i, sym in enumerate(SY): db2.put_candles(be.key(sym), "1d", series(900, [30000, 2.0, 0.4, 150][i], 0.0008, 0.03, 50 + i, t0=now2 // 86400 * 86400 - 900 * 86400), "t")
bb.write_settings(d2, {"symbols": ["AAAUSDT"]})
mp = bl.run(d2, db2, Pub(), now=now2, say=lambda *_: None)
assert mp["ens_variant"] == "v2" and bb.read_edge(d2)["ens_variant"] == "v2"
L = lab.read(d2); assert L["champion"] == "v2" and len(L["variants"]) == 4
keys = {k.split(":")[0] for k in sh.load(d2)}; assert keys == {"plan", "ensemble", "v2", "v1n"}, keys
log = " | ".join(e["msg"] for e in bb.read_events(d2, 60, bb.LEARN_LOG))
assert "Laboratorium: mistrz v2" in log and "Zespół sygnałów (wariant v2)" in log and "Tryb cienia: krok dla 16 par" in log, log[-600:]
# zmiana wariantu: poprzednie oceny zespołu nie dają histerezy
prev = bb.read_edge(d2); prev["assets_ens"]["AAAUSDT"].update(status="allowed", since=1); prev["allowed_ens"] = ["AAAUSDT"]; bb.write_edge(d2, prev)
bb.write_settings(d2, {"ens_variant": "v1"})
mp2 = bl.run(d2, db2, Pub(), now=now2 + 3600, say=lambda *_: None)
assert mp2["ens_variant"] == "v1" and mp2["assets_ens"]["AAAUSDT"]["since"] == now2 + 3600 and "Zmienił się wariant zespołu sygnałów (v2 → v1)" in " | ".join(e["msg"] for e in bb.read_events(d2, 60, bb.LEARN_LOG))
# awaria laboratorium nie zatrzymuje nauki
orig = lab.run; lab.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("awaria lab"))
mp3 = bl.run(d2, db2, Pub(), now=now2 + 7200, say=lambda *_: None); lab.run = orig
assert mp3["ts"] == now2 + 7200 and any("laboratorium: RuntimeError: awaria lab" in e["msg"] for e in bb.read_events(d2, 20, bb.LEARN_LOG))
print("lab ok", {v["id"]: (v["hist"]["n"], v["hist"]["mean_r"]) for v in out["variants"]})
