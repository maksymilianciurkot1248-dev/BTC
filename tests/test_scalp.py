import sys, os, json, tempfile, math, random
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_scalp as sc
import btc_trader as bt
import atexit; atexit.unregister(bt.flush_notify)
bt.NOTIFY_STATE = os.path.join(tempfile.mkdtemp(), "n.json")
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
T0 = 1_700_000_100 // 300 * 300
def flat(n, px=100.0, vol=10.0, noise=0.02, seed=1):
    r = random.Random(seed); out = []
    for i in range(n):
        o = px + r.uniform(-noise, noise); c = px + r.uniform(-noise, noise)
        out.append([T0 + i * 300, o, max(o, c) + noise, min(o, c) - noise, c, vol])
    return out
# 1. wskaźniki
b = flat(60)
check(sc.atr(b, 59) > 0 and sc.atr(b, 5) is None, "atr")
up = [[T0 + i * 300, 100 + i, 101 + i, 99.5 + i, 100.5 + i, 1] for i in range(20)]
check(sc.rsi(up, 19) == 100.0, "rsi rosnący = 100")
# 2. powrót do średniej: gwałtowny spadek daje long z celem przy średniej, stop >= 0,30%
b = flat(60)
for k, px in enumerate((99.6, 99.1, 98.5, 97.8)):
    t = b[-1][0] + 300; b.append([t, b[-1][4], b[-1][4], px - 0.05, px, 10])
p = sc.sig_mr(b, len(b) - 1)
check(p and p["side"] == 1 and p["stop_dist"] >= sc.MIN_STOP_PCT * 97.8 - 1e-9 and p["stop_dist"] <= p["tp_dist"] <= 2 * p["stop_dist"] + 1e-9, f"mr long {p}")
check(sc.sig_mr(flat(60), 59) is None, "mr: brak sygnału w spokoju")
# 3. wybicie: wąski zakres + skok z obrotem
b = flat(60)
b.append([b[-1][0] + 300, 100.0, 100.5, 99.99, 100.45, 40.0])
p = sc.sig_bo(b, len(b) - 1)
check(p and p["side"] == 1 and abs(p["tp_dist"] - 2 * p["stop_dist"]) < 1e-9, f"bo long {p}")
b[-1][5] = 12.0
check(sc.sig_bo(b, len(b) - 1) is None, "bo: bez obrotu brak sygnału")
# 4. arkusz
book = {"b": [["100.00", "30"], ["99.95", "20"], ["99.0", "500"]], "a": [["100.02", "5"], ["100.05", "5"], ["101.5", "900"]]}
x = sc.book_imbalance(book)
check(abs(x - (50 - 10) / 60) < 1e-9, f"nierównowaga tylko blisko środka {x}")
check(sc.book_imbalance({}) is None, "pusty arkusz")
b = flat(40); b[-1][1], b[-1][4] = 99.99, 100.06
check(sc.sig_ob(b, 39, [0.5, 0.6])["side"] == 1 and sc.sig_ob(b, 39, [0.5]) is None and sc.sig_ob(b, 39, [0.1, 0.9]) is None and sc.sig_ob(b, 39, None) is None, "ob")
# 5. cień: stop, cel, czas, koszty
t = {"side": 1, "entry": 100.0, "stop": 99.5, "tp": 101.0, "sd": 0.5, "left": 3}
cost = sc.COST_PCT * 100 / 0.5
check(abs(sc.step_shadow(dict(t), [0, 100, 100.2, 99.4, 99.6, 1]) - (-1 - cost)) < 1e-9, "stop")
check(abs(sc.step_shadow(dict(t), [0, 100, 101.2, 99.9, 101, 1]) - (2 - cost)) < 1e-9, "cel")
check(abs(sc.step_shadow(dict(t), [0, 100, 101.2, 99.4, 101, 1]) - (-1 - cost)) < 1e-9, "świeca ze stopem i celem = stop")
tt = dict(t); check(sc.step_shadow(tt, [0, 100, 100.3, 99.8, 100.1, 1]) is None and tt["left"] == 2, "w toku")
tt["left"] = 1; check(abs(sc.step_shadow(tt, [0, 100, 100.3, 99.8, 100.25, 1]) - (0.5 - cost)) < 1e-9, "limit czasu")
ts = {"side": -1, "entry": 100.0, "stop": 100.5, "tp": 99.0, "sd": 0.5, "left": 3}
check(abs(sc.step_shadow(dict(ts), [0, 100, 100.1, 98.9, 99, 1]) - (2 - cost)) < 1e-9, "short cel")
# 6. feed: przyrostowo, bez podwójnego liczenia
b = flat(60)
b.append([b[-1][0] + 300, 100.0, 100.5, 99.99, 100.45, 40.0])
sh = {"open": {}, "trades": [], "last": {}}
sig = sc.feed(sh, "BTCUSDT", b)
check("bo" in sig and "bo:BTCUSDT" in sh["open"] and sh["last"]["BTCUSDT"] == b[-1][0], "feed otwiera")
n_open, n_tr = len(sh["open"]), len(sh["trades"])
check(sc.feed(sh, "BTCUSDT", b) == {} and len(sh["open"]) == n_open and len(sh["trades"]) == n_tr, "ta sama świeca drugi raz: nic")
e = sh["open"]["bo:BTCUSDT"]
b.append([b[-1][0] + 300, 100.45, e["tp"] + 0.1, 100.4, e["tp"], 10])
sc.feed(sh, "BTCUSDT", b)
bo_tr = [t for t in sh["trades"] if t[1] == "bo"]
check(len(bo_tr) == 1 and bo_tr[0][4] > 1 and "bo:BTCUSDT" not in sh["open"], f"feed zamyka {sh['trades']}")
# 6b. przerwa w danych: otwarte transakcje z cienia znikają bez wyniku, krótka przerwa ich nie rusza
bg = flat(60); bg.append([bg[-1][0] + 300, 100.0, 100.5, 99.99, 100.45, 40.0])
shg = {"open": {}, "trades": [], "last": {}}
sc.feed(shg, "BTCUSDT", bg)
check("bo:BTCUSDT" in shg["open"], "pozycja w cieniu przed przerwą")
small = bg + [[bg[-1][0] + 3 * 300, 100.45, 100.5, 100.4, 100.45, 10.0]]
shs = json.loads(json.dumps(shg)); sc.feed(shs, "BTCUSDT", small)
check("bo:BTCUSDT" in shs["open"], "przerwa 10 minut: transakcja trwa")
late = bg + [[bg[-1][0] + 40 * 300, 100.45, 100.5, 100.4, 100.45, 10.0]]
n_before = len(shg["trades"]); sc.feed(shg, "BTCUSDT", late)
check("bo:BTCUSDT" not in shg["open"] and len(shg["trades"]) == n_before, "przerwa ponad 15 minut: transakcja zamknięta bez wyniku")
# 7. bramka
now = b[-1][0] + 1000
# transakcje co 2 h (osobne bloki godzinowe), wynik r ± sp na przemian: średnia r, odchylenie sp
mk = lambda strat, n, r, sym="BTCUSDT", age=0, sp=0.5, gap=7200: [[now - age - i * gap, strat, sym, 1, r + (sp if i % 2 else -sp)] for i in range(n)]
# 7a. t odporne na skupiska
spread, bunched = [(now - i * 7200, 0.3 + (0.5 if i % 2 else -0.5)) for i in range(40)], [(now - (i // 10) * 7200, 0.3 + (0.5 if (i // 10) % 2 else -0.5)) for i in range(40)]
check(abs(sc.tstat(spread) - 0.3 / (0.5 / math.sqrt(40)) * math.sqrt(39 / 40)) < 0.01, f"t dla niezależnych transakcji = zwykłe t ({sc.tstat(spread):.2f})")
check(0 < sc.tstat(bunched) < sc.tstat(spread) / 2.5, f"40 transakcji w 4 godzinach to 4 obserwacje, nie 40 ({sc.tstat(bunched):.2f})")
check(sc.tstat([]) == 0 and sc.tstat([(now, 1.0)]) == 0 and sc.tstat([(now, 1.0), (now + 5, 2.0)]) == 0, "za mało danych albo jeden blok: t = 0")
st = sc.stats(mk("mr", 40, 0.2) + mk("bo", 40, -0.2) + mk("ob", 5, 3.0), now)
check(st["mr"]["t"] >= 2 and st["bo"]["t"] <= -2, f"t w statystykach {st['mr']['t']} {st['bo']['t']}")
check(sc.gate(st, False) == ({"mr": 1.0}, "bramka") and sc.gate(st, True)[0] == {"mr": 1.0}, "bramka przepuszcza tylko mr")
# 7b. dodatnia średnia bez istotności nie otwiera bramki (to był błąd: przy 30 transakcjach przechodził przypadek)
noisy = sc.stats(mk("mr", 30, 0.2, sp=1.5), now)
check(noisy["mr"]["mean_r"] >= 0.05 and noisy["mr"]["t"] < 2 and sc.gate(noisy, False) == ({}, "brak"), f"średnia +0,2R przy t {noisy['mr']['t']} nie wystarcza")
lucky = sc.stats(mk("mr", 30, 0.2, sp=0.4, gap=60), now)
check(lucky["mr"]["mean_r"] >= 0.05 and sc.gate(lucky, False) == ({}, "brak"), f"30 transakcji z jednej godziny to jedna obserwacja (t {lucky['mr']['t']})")
# 7c. histereza: otwarta bramka trzyma się, dopóki średnia >= 0; zamyka się poniżej zera; nie otwiera się bez pamięci
mem = {}
check(sc.gate(st, False, mem) == ({"mr": 1.0}, "bramka") and mem == {"mr": 1}, "bramka zapamiętana")
weak = sc.stats(mk("mr", 40, 0.02, sp=1.0), now)
check(sc.gate(weak, False, mem) == ({"mr": 1.0}, "bramka") and sc.gate(weak, False, {}) == ({}, "brak"), "słabszy okres: otwarta bramka zostaje, zamknięta się nie otwiera")
neg = sc.stats(mk("mr", 40, -0.03, sp=1.0), now)
check(sc.gate(neg, False, mem) == ({}, "brak") and mem == {}, "średnia poniżej zera zamyka bramkę")
check(sc.gate(weak, False, mem) == ({}, "brak"), "po zamknięciu potrzebny jest pełny dowód od nowa")
few = sc.stats(mk("mr", 20, 0.5), now); mem2 = {"mr": 1}
check(sc.gate(few, False, mem2) == ({}, "brak") and mem2 == {}, "za mało transakcji w oknie zamyka bramkę")
# 7d. hamulec z prawdziwych transakcji
led = [{"ts": now - i * 7200, "strat": "mr", "r": -0.6 + (0.5 if i % 2 else -0.5)} for i in range(12)] + [{"ts": now - i * 7200, "strat": "bo", "r": 0.1 + (0.5 if i % 2 else -0.5)} for i in range(12)] + [{"ts": now, "strat": "mr", "r": None}]
br = sc.real_brake(led, now)
check(set(br) == {"mr"} and br["mr"]["n"] == 12 and br["mr"]["mean_r"] < 0 and br["mr"]["t"] <= -2, f"hamulec łapie istotnie ujemne realne wyniki {br}")
check(sc.gate(st, False, {}, br) == ({}, "brak") and sc.gate(st, True, {}, br)[0] != {"mr": 1.0}, "zahamowana strategia nie handluje mimo dodatniego cienia")
check(sc.real_brake(led[:8], now) == {} and sc.real_brake([dict(x, ts=now - 40 * 86400) for x in led], now) == {}, "za mało transakcji albo stare: brak hamulca")
check(sc.real_brake([{"ts": now - i * 7200, "strat": "mr", "r": -0.1 + (1.5 if i % 2 else -1.5)} for i in range(12)], now) == {}, "ujemna średnia bez istotności nie hamuje")
st2 = sc.stats(mk("mr", 40, -0.1) + mk("bo", 40, -0.3) + mk("ob", 5, 3.0), now)
check(sc.gate(st2, False) == ({}, "brak") and sc.gate(st2, True) == ({"mr": 0.5}, "rozpoznanie"), "rozpoznanie: najlepsza z min. próbą, połowa ryzyka")
check(sc.gate(sc.stats([], now), True) == ({}, "brak"), "bez danych nic")
st_bad = sc.stats(mk("mr", 40, -0.36) + mk("bo", 40, -0.49), now)
check(sc.gate(st_bad, True) == ({}, "brak"), "rozpoznanie nie handluje strategiami, które w cieniu wyraźnie tracą")
st_mix = sc.stats(mk("mr", 40, -0.36) + mk("fl", 12, 0.02), now)
check(sc.gate(st_mix, True) == ({"fl": 0.5}, "rozpoznanie"), "rozpoznanie wybiera strategię, która nie traci")
st3 = sc.stats(mk("mr", 40, 0.2, age=31 * 86400), now)
check(st3["mr"]["n"] == 0 and sc.GATE_DAYS == 30, "stare transakcje poza oknem 30 dni")
check(sc.stats(mk("mr", 400, 0.2, gap=600), now)["mr"]["n"] == sc.GATE_LAST == 300, "okno: najwyżej 300 ostatnich transakcji")
st4 = sc.stats(mk("mr", 40, 0.3) + mk("mr", 25, -0.4, "DOGEUSDT"), now)
check(sc.vetoed(st4, "mr", "DOGEUSDT") and not sc.vetoed(st4, "mr", "BTCUSDT") and not sc.vetoed(st4, "mr", "ETHUSDT"), "weto pary")
# 8. zlecenia z udawaną giełdą
class Pub:
    def __init__(s): s.bars = {}; s.book = {"b": [["100", "1"]], "a": [["100.02", "1"]]}
    def _request(s, path, p=None, **k):
        if "instruments" in path: return {"list": [{"lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}, "priceFilter": {"tickSize": "0.01"}}]}
        if "orderbook" in path: return s.book
        if "kline" in path:
            rows = [[str(x[0] * 1000), *map(str, x[1:]), "0"] for x in s.bars[p["symbol"]]]
            if p.get("end"): rows = [r for r in rows if int(r[0]) <= p["end"]]
            return {"list": rows[::-1][:p["limit"]]}
class Api:
    def __init__(s): s.calls = []; s.pos = []; s.orders = []; s.closed = []; s.fail = False
    def ok(s, m, path, p=None, allow=()):
        s.calls.append((m, path, p))
        if "wallet" in path: return {"list": [{"totalEquity": "10000"}]}
        if "position/list" in path: return {"list": s.pos}
        if "order/realtime" in path: return {"list": s.orders}
        if "closed-pnl" in path: return {"list": s.closed}
        if "order/create" in path and s.fail: raise RuntimeError("odrzucone")
        return {}
d = tempfile.mkdtemp()
c = {"mode": "demo", "max_capital": 0, "max_lev": 2.0}
env = {}.get
scf = sc.scalp_cfg(c, env)
check(scf["can_trade"] and scf["explore"] and scf["symbols"][:3] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"] and len(scf["symbols"]) == 5, "konfiguracja demo")
check(not sc.scalp_cfg({"mode": "live", "max_capital": 100, "max_lev": 2.0}, env)["can_trade"] and not sc.scalp_cfg({"mode": "live", "max_capital": 100, "max_lev": 2.0}, env)["explore"], "live bez potwierdzenia: tylko cień, bez rozpoznania")
check(sc.scalp_cfg({"mode": "live", "max_capital": 100, "max_lev": 2.0}, {"SCALP_CONFIRM_LIVE": "I_ACCEPT_THE_RISK"}.get)["can_trade"], "live z potwierdzeniem")
scf["symbols"] = ["BTCUSDT"]
pub, api = Pub(), Api()
live = sc.Live(d, c, scf, api, pub)
base = flat(80)
pub.bars["BTCUSDT"] = base
s = sc.load_state(d)
now = base[-1][0] + 300 + 5
sc.cycle(d, c, scf, pub, live, s, now, True)                      # rozbieg: bez zleceń
check(s["seeded"] == ["BTCUSDT"] and not any("order/create" in x[1] for x in api.calls), "rozbieg nie handluje")
s["shadow"]["trades"] += mk("bo", 40, 0.3, age=0)
for t in s["shadow"]["trades"]: t[0] = min(t[0], now)
brk = [base[-1][0] + 300, 100.0, 100.5, 99.99, 100.45, 40.0]
pub.bars["BTCUSDT"] = base + [brk]
now = brk[0] + 300 + 5
notes = sc.cycle(d, c, scf, pub, live, s, now, True)
orders = [x for x in api.calls if "order/create" in x[1]]
check(len(orders) == 1, f"jedno zlecenie {notes}")
o = orders[0][2]
qty = float(o["qty"]); sd = s["live"]["open"]["BTCUSDT"]["sd"]
check(o["side"] == "Buy" and o["orderType"] == "Market" and float(o["stopLoss"]) < 100.45 < float(o["takeProfit"]) and o["orderLinkId"].startswith("sc-bo-"), f"zlecenie ze stopem i celem {o}")
check(qty * sd <= 10000 * 0.2 / 100 + 1e-6 and 4900 < qty * 100.45 <= 10000 * 1.0 / 2 + 1e-6, f"rozmiar: ryzyko 0,2% kapitału, nominał <= połowa limitu ({qty})")
out = sc.read_out(d)
check(out["gate"] == "bramka" and out["allowed"] == {"bo": 1.0} and out["open"][0]["sym"] == "BTCUSDT" and out["blocked"] is None, "scalp.json")
check(sc.open_symbols(d) == set(), "stan zapisuje pętla główna (jeszcze nie na dysku)")
sc._write(os.path.join(d, sc.STATE), s); check(sc.open_symbols(d) == {"BTCUSDT"}, "open_symbols")
# pozycja istnieje -> brak drugiego wejścia; zamknięcie -> dziennik z R
api.pos = [{"symbol": "BTCUSDT", "size": str(qty), "side": "Buy"}]
sc.cycle(d, c, scf, pub, live, s, now + 60, False)
check(len([x for x in api.calls if "order/create" in x[1]]) == 1 and "BTCUSDT" in s["live"]["open"], "pozycja trwa")
api.pos, api.closed = [], [{"closedPnl": str(-qty * sd * 1.2)}]
sc.cycle(d, c, scf, pub, live, s, now + 120, False)
led = sc.read_ledger(d)
check(len(led) == 1 and abs(led[0]["r"] + 1.2) < 0.01 and "BTCUSDT" not in s["live"]["open"] and s["live"]["cool"]["BTCUSDT"] > now and abs(s["live"]["day_r"] + 1.2) < 0.01, f"zamknięcie w dzienniku {led}")
# limit czasu
s["live"]["open"]["BTCUSDT"] = {"strat": "bo", "side": 1, "qty": qty, "entry": 100.0, "sd": sd, "ts": now, "hold_s": 600, "link": "x"}
api.pos = [{"symbol": "BTCUSDT", "size": str(qty), "side": "Buy"}]
n0 = len(api.calls); sc.cycle(d, c, scf, pub, live, s, now + 700, False)
cl = [x for x in api.calls[n0:] if "order/create" in x[1]]
check(len(cl) == 1 and cl[0][2]["reduceOnly"] is True and cl[0][2]["side"] == "Sell", "limit czasu zamyka rynkowo")
# blokady
s["live"]["open"].clear(); api.pos = []
check(live.blocked(s["live"], {}, now) is None, "bez blokady")
check("pauza" in live.blocked(s["live"], {"paused": True}, now), "pauza z panelu")
sc.write_settings(d, {"enabled": False}); check("panelu" in live.blocked(s["live"], {}, now), "wyłącznik"); sc.write_settings(d, {"enabled": True})
s["live"]["day_r"] = -5.0; check("strata" in live.blocked(s["live"], {}, now), "dzienna strata"); s["live"]["day_r"] = 0
s["live"]["day_n"] = 30; check("limit" in live.blocked(s["live"], {}, now), "limit transakcji"); s["live"]["day_n"] = 0
try: sc.write_settings(d, {"enabled": "tak"}); check(False, "walidacja")
except ValueError: check(True, "walidacja ustawień")
# cudza pozycja / zlecenia na aktywie -> nie wchodzimy
plan = {"side": 1, "stop_dist": 0.5, "tp_dist": 1.0, "hold": 10}
check("istnieje" in live.enter(s["live"], "BTCUSDT", "bo", plan, 1.0, 100.0, 10000, [{"symbol": "BTCUSDT"}], now + 5000), "cudza pozycja")
api.orders = [{"x": 1}]; check("zlecenia" in live.enter(s["live"], "BTCUSDT", "bo", plan, 1.0, 100.0, 10000, [], now + 5000), "otwarte zlecenia"); api.orders = []
check(live.enter(s["live"], "BTCUSDT", "bo", plan, 0.5, 100.0, 10000, [], now + 5000) is None and abs(s["live"]["open"]["BTCUSDT"]["qty"] * 0.5 - 10) < 0.1 and s["live"]["open"]["BTCUSDT"]["explore"], "rozpoznanie: połowa ryzyka")
# dryrun: tylko cień
d2 = tempfile.mkdtemp(); s2 = sc.load_state(d2)
sc.cycle(d2, {"mode": "dryrun", "max_capital": 0, "max_lev": 2.0}, scf, pub, None, s2, now, True)
check(sc.read_out(d2)["blocked"] == "tryb bez zleceń" and s2["seeded"] == ["BTCUSDT"], "dryrun")
print("scalp ok", ok)
