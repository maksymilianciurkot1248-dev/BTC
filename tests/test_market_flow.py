"""Testy btc_flow.py (dane o przepływie i pozycjonowaniu, wskaźnik łączny) oraz strategii „fl” w scalperze. Bez sieci."""
import json
import math
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import btc_flow as bf
import btc_scalp as sc
import btc_trader as bt
import atexit
atexit.unregister(bt.flush_notify)
bt.NOTIFY_STATE = os.path.join(tempfile.mkdtemp(), "n.json")

ok = 0


def check(c, m):
    global ok
    assert c, m
    ok += 1


H = 3600
NOW = 1_800_000_000 // H * H + 1500          # 25 minut po pełnej godzinie
LAST = NOW // H * H - H                      # ostatnia pełna godzina (jej wiersz ma czas startu LAST)


def market(n=400, seed=3, sym="BTCUSDT"):
    """Syntetyczny rynek godzinowy: {ts: (close, mark, index, buy, sell, ls, lsp, oi)} dla n godzin do LAST włącznie + bieżąca, niepełna."""
    r = random.Random(seed)
    out, px = {}, 100.0
    for i in range(n + 1):
        t = LAST - (n - 1 - i) * H
        px *= 1 + r.gauss(0, 0.004)
        buy = 1000 * (1 + r.gauss(0, 0.1))
        out[t] = (px, px * (1 + r.gauss(0, 0.0002)), px, buy, 1000 * (1 + r.gauss(0, 0.1)), 2.0 * math.exp(r.gauss(0, 0.05)), 1.1, 5000 + i)
    return out


class Okx:
    """Udawane API OKX: stronicowanie jak w prawdziwym (świece: after, statystyki: end), najnowsze wiersze pierwsze."""

    def __init__(self, data):
        self.data, self.calls, self.fail = data, [], set()

    def __call__(self, url, timeout=0, retries=0):
        path, q = url.split("/api/v5/")[1].split("?")
        p = dict(x.split("=") for x in q.split("&"))
        self.calls.append((path, p))
        if any(f in path for f in self.fail):
            raise RuntimeError("HTTP 503")
        if path == "public/funding-rate":
            return {"code": "0", "data": [{"fundingRate": "0.0001"}]}
        sym = p["instId"].split("-")[0] + "USDT"
        rows = sorted(self.data[sym].items(), reverse=True)
        cur = p.get("after") or p.get("end")
        if cur:
            rows = [x for x in rows if x[0] * 1000 < int(cur)]
        rows = rows[:int(p["limit"])]
        ms = lambda t: str(t * 1000)      # noqa: E731
        if "candles" in path:
            col = 1 if "mark-price" in path else 2 if "index-candles" in path else 0
            conf = lambda t: "0" if t > LAST else "1"      # noqa: E731
            if col == 0:
                return {"code": "0", "data": [[ms(t), "0", "0", "0", str(v[0]), "1", "1", "1", conf(t)] for t, v in rows]}
            return {"code": "0", "data": [[ms(t), "0", "0", "0", str(v[col]), conf(t)] for t, v in rows]}
        if "taker" in path:
            return {"code": "0", "data": [[ms(t), str(v[4]), str(v[3])] for t, v in rows]}
        if "position-ratio" in path:
            return {"code": "0", "data": [[ms(t), str(v[6])] for t, v in rows]}
        if "account-ratio" in path:
            return {"code": "0", "data": [[ms(t), str(v[5])] for t, v in rows]}
        if "open-interest" in path:
            return {"code": "0", "data": [[ms(t), "1", str(v[7]), "1"] for t, v in rows]}
        return {"code": "51000", "msg": "zły adres", "data": []}


pace = bf.Pacer(sleep=lambda s: None, clock=lambda: 0.0)
M = {"BTCUSDT": market(), "ETHUSDT": market(seed=5)}
api = Okx(M)

# 1. pobieranie jednej serii: pełne godziny, bez bieżącej, stronicowanie
got = bf.fetch_kind(api, "px", "BTCUSDT", NOW - 250 * H, NOW, pace)
check(max(got) == LAST and min(got) >= NOW - 250 * H and len(got) == 249, f"świece: {len(got)} godzin, ostatnia pełna")
check(abs(got[LAST][bf.C] - M["BTCUSDT"][LAST][0]) < 1e-6, "kolumna zamknięcia")
paths = [c[0] for c in api.calls]
check(paths[0] == "market/candles" and paths[1] == "market/history-candles" and "after" in api.calls[1][1] and "after" not in api.calls[0][1], "świeże, potem historia z kursorem after")
api.calls.clear()
tk = bf.fetch_kind(api, "tk", "BTCUSDT", NOW - 150 * H, NOW, pace)
check(len(tk) == 149 and abs(tk[LAST][bf.BUY] - M["BTCUSDT"][LAST][3]) < 1e-6 and abs(tk[LAST][bf.SELL] - M["BTCUSDT"][LAST][4]) < 1e-6, "wolumen agresywny: kupno i sprzedaż we właściwych kolumnach")
check(api.calls[0][1]["period"] == "1H" and "end" in api.calls[1][1] and api.calls[0][1]["instId"] == "BTC-USDT-SWAP", "statystyki: okres 1H, kursor end")
ix = bf.fetch_kind(api, "ix", "BTCUSDT", NOW - 10 * H, NOW, pace)
check(api.calls[-1][1]["instId"] == "BTC-USDT" and len(ix) == 9, "indeks pobierany z pary spot")

# 2. odświeżenie aktywa: pierwsze pobranie, potem tylko końcówka; błąd jednego źródła nie psuje reszty
rows = {}
errs = bf.refresh_symbol(api, rows, "BTCUSDT", NOW, pace)
check(not errs and len(rows) == 400 and all(v is not None for v in rows[LAST]), f"komplet 8 kolumn ({len(rows)} godzin)")
api.calls.clear()
bf.refresh_symbol(api, rows, "BTCUSDT", NOW, pace)
check(len(api.calls) == len(bf.KINDS), "kolejne odświeżenie: po jednym zapytaniu na serię")
api.fail = {"taker"}
r2 = {}
e2 = bf.refresh_symbol(api, r2, "BTCUSDT", NOW, pace)
check(len(e2) == 1 and "tk" in e2[0] and r2[LAST][bf.C] is not None and r2[LAST][bf.BUY] is None, "błąd jednej serii zgłoszony, reszta jest")
api.fail = set()
old = {NOW - (bf.KEEP_H + 5) * H // H * H: [1] * 8, **rows}
bf.refresh_symbol(api, old, "BTCUSDT", NOW, pace)
check(min(old) >= NOW - bf.KEEP_H * H, "stare godziny usuwane")

# 3. wskaźnik: znaki składników i brak danych z przyszłości
ser = bf.series(rows)
check(len(ser) == 400 and ser[-1]["t"] == LAST + H and ser[-1]["score"] is not None and ser[50]["score"] is None, "seria na ciągłej siatce, rozbieg bez wyniku")


def bumped(col, mult, hours=1):
    r = {t: list(v) for t, v in rows.items()}
    for k in range(hours):
        r[LAST - k * H][col] *= mult
    return bf.series(r)[-1]


base = ser[-1]
check(bumped(bf.BUY, 1.5, 4)["score"] > base["score"], "więcej agresywnego kupna podnosi wskaźnik")
check(bumped(bf.LS, 1.3)["score"] > base["score"], "więcej kont long podnosi wskaźnik")
check(bumped(bf.MARK, 1.002)["score"] < base["score"], "wyższa premia obniża wskaźnik")
up = {t: list(v) for t, v in rows.items()}
for c in (bf.C, bf.MARK, bf.INDEX):
    up[LAST][c] *= 1.03
check(bf.series(up)[-1]["score"] < base["score"] and abs(bf.series(up)[-1]["z"]["prem"] - base["z"]["prem"]) < 1e-9, "duży wzrost z 4 h obniża wskaźnik (premia bez zmian)")
check(all(abs(a["score"] - b["score"]) < 1e-12 for a, b in zip(ser[:-1], bf.series(up)[:-1]) if a["score"] is not None), "zmiana ostatniej godziny nie zmienia wcześniejszych wyników")
check(all(-3 <= v <= 3 for x in ser for v in x["z"].values() if v is not None), "z-score obcięty do ±3")
gap = {t: v for t, v in rows.items() if t != LAST - 2 * H}
g = bf.series(gap)
check(len(g) == 400 and g[-3]["score"] is None and g[-1]["z"]["tk"] is None and g[-1]["score"] is None, "luka w danych: brak wyniku zamiast błędnego")
raw = base["raw"]
check(abs(raw["long_pct"] - rows[LAST][bf.LS] / (1 + rows[LAST][bf.LS]) * 100) < 1e-9 and 40 < raw["tk_buy_pct"] < 60 and raw["oi_chg24_pct"] > 0, "wartości do wyświetlenia")

# 4. trafność: wskazania nie nakładają się, kierunek, filtr czasu
mk = lambda t, c, s: {"t": t, "c": c, "score": s}      # noqa: E731
s1 = [mk(i * H, 100 + i, 1.5 if i in (0, 1, 2, 20) else 0.0) for i in range(40)]
a = bf.accuracy(s1)
check(a == {"n": 2, "hit_pct": 100.0, "avg_bp": round(((112 / 100 - 1) + (132 / 120 - 1)) / 2 * 1e4, 1)}, f"dwa nienakładające się wskazania long trafione {a}")
s2 = [mk(i * H, 100 + i, -1.5 if i == 0 else 0.0) for i in range(40)]
check(bf.accuracy(s2)["hit_pct"] == 0.0 and bf.accuracy(s2)["avg_bp"] < 0, "wskazanie short przy wzroście = pudło")
check(bf.accuracy(s1, since=10 * H)["n"] == 1 and bf.accuracy(s1, theta=2.0)["n"] == 0 and bf.accuracy([])["n"] == 0, "filtr czasu, próg, pusta seria")
check(bf._merge([{"n": 2, "hit_pct": 100.0, "avg_bp": 10.0}, {"n": 0, "hit_pct": None, "avg_bp": None}, {"n": 2, "hit_pct": 50.0, "avg_bp": -10.0}]) == {"n": 4, "hit_pct": 75.0, "avg_bp": 0.0}, "łączenie statystyk aktywów")

# 5. opis dla panelu
d = bf.describe("BTCUSDT", ser, NOW, 0.01)
check(d["stale"] is False and len(d["parts"]) == 4 and d["info"]["funding_pct"] == 0.01 and d["parts"][2]["vote"] == round(-d["parts"][2]["z"], 2), "opis: 4 składniki, premia głosuje przeciwnie")
check(bf.describe("BTCUSDT", ser, NOW + 4 * H)["lean"] == "brak danych" and bf.describe("X", [], NOW)["lean"] == "brak danych", "stare lub puste dane = brak wskazania")
for sc_, name in ((1.2, "wzrostowe"), (-1.0, "spadkowe"), (0.6, "lekko wzrostowe"), (-0.7, "lekko spadkowe"), (0.1, "neutralne")):
    x = [dict(ser[-1], score=sc_)]
    dd = bf.describe("BTCUSDT", x, NOW)
    check(dd["lean"] == name and dd["strong"] == (abs(sc_) >= bf.THETA), f"wskazanie {sc_} -> {name}")

# 6. cały obieg: pliki, wskaźnik na dany moment
tmp = tempfile.mkdtemp()
fl = bf.Flow(tmp, ["BTCUSDT", "ETHUSDT"], get=api, pace=pace)
out = fl.step(NOW)
check(set(out["symbols"]) == {"BTCUSDT", "ETHUSDT"} and not out["errors"] and out["accuracy"]["days"] > 16, "krok: oba aktywa, bez błędów")
check(os.path.exists(os.path.join(tmp, bf.OUT)) and bf.read_out(tmp)["symbols"]["BTCUSDT"]["score"] == out["symbols"]["BTCUSDT"]["score"], "flow.json zapisany")
check(fl.lean("BTCUSDT", NOW) == ser[-1]["score"] and fl.lean("BTCUSDT", LAST + H - 1) == ser[-2]["score"], "wskaźnik znany w danej chwili (bez wyprzedzania)")
check(fl.lean("BTCUSDT", NOW + 3 * H) is None and fl.lean("DOGEUSDT", NOW) is None, "stare dane albo nieznane aktywo = brak")
check(fl.reading("BTCUSDT", NOW) == (ser[-1]["t"], ser[-1]["score"]) and fl.reading("BTCUSDT", NOW + 3 * H) is None, "odczyt z czasem (jeden odczyt = jedno wejście)")
fl2 = bf.Flow(tmp, ["BTCUSDT"], get=api, pace=pace)
check(len(fl2.hist["BTCUSDT"]) == 400 and fl2.lean("BTCUSDT", NOW) == ser[-1]["score"], "historia wczytana z pliku po restarcie")
api.fail = {"candles", "rubik", "funding"}
o3 = bf.Flow(tempfile.mkdtemp(), ["BTCUSDT"], get=api, pace=pace).step(NOW)
check(len(o3["errors"]) == 7 and o3["symbols"]["BTCUSDT"]["lean"] == "brak danych", "brak sieci: błędy w wyniku, program działa")
api.fail = set()
# luka w jednym źródle zasklepia się po jego powrocie (każde źródło uzupełniamy od jego własnej ostatniej godziny)
flg = bf.Flow(tempfile.mkdtemp(), ["BTCUSDT"], get=api, pace=pace)
flg.step(NOW - 30 * H)
api.fail = {"long-short-account"}
o_gap = flg.step(NOW)
gap = lambda: [t for t, r in flg.hist["BTCUSDT"].items() if r[bf.LS] is None and t > NOW - 28 * H]
check(len(gap()) >= 20 and len(o_gap["errors"]) == 1, f"awaria jednego źródła: luka tylko w nim ({len(gap())} h), reszta świeża")
api.fail = set()
flg.step(NOW)
check(not gap(), "po powrocie źródła luka uzupełniona")
# dziennik: ten sam błąd zgłaszany raz, potem jedna linia o powrocie
lines = []
api.fail = {"candles", "rubik", "funding"}
w = bf.Worker(bf.Flow(tempfile.mkdtemp(), ["BTCUSDT"], get=api, pace=pace), log=lines.append)
w.flow.step = lambda step=w.flow.step: step(NOW)
w.tick(); w.tick(); w.tick()
check(len(lines) == 1 and lines[0].startswith("przepływ: BTCUSDT"), f"powtarzający się błąd: jedna linia {lines}")
api.fail = set()
w.tick(); w.tick()
check(len(lines) == 2 and "znów kompletne" in lines[1], f"powrót danych: jedna linia {lines}")

# 7. scalper: ATR godzinowy i strategia „fl”
T0 = NOW // 300 * 300 - 400 * 300
rnd = random.Random(1)
bars, px = [], 100.0
for i in range(400):
    o = px
    px *= 1 + rnd.gauss(0, 0.0008)
    bars.append([T0 + i * 300, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 10.0])
ah = sc.atr_hour(bars, 399)
hrs = {}
for b in bars:
    hrs.setdefault(b[0] // 3600, []).append(b)
full = [v for k, v in sorted(hrs.items()) if len(v) == 12][-15:]
ohlc = [(max(b[2] for b in v), min(b[3] for b in v), v[-1][4]) for v in full]
ref = sum(max(ohlc[k][0] - ohlc[k][1], abs(ohlc[k][0] - ohlc[k - 1][2]), abs(ohlc[k][1] - ohlc[k - 1][2])) for k in range(1, 15)) / 14
check(abs(ah - ref) < 1e-9 and sc.atr_hour(bars, 100) is None, "ATR godzinowy ze świec 5 min")
check(sc.sig_fl(bars, 399, None) is None and sc.sig_fl(bars, 399, 0.9) is None, "poniżej progu brak sygnału")
p = sc.sig_fl(bars, 399, 1.3)
c = bars[399][4]
check(p["side"] == 1 and p["hold"] == 144 and abs(p["stop_dist"] - max(3 * ah, 0.006 * c)) < 1e-9 and abs(p["tp_dist"] - 2 * p["stop_dist"]) < 1e-9, f"long: stop 3 x ATR (min. 0,6%), cel 2R, 12 h {p}")
check(sc.sig_fl(bars, 399, -1.0)["side"] == -1, "wskaźnik ujemny = short")
sh = {"open": {}, "trades": [], "last": {}}
sc.feed(sh, "BTCUSDT", bars[:399], lean=2.0)
n_fl = sum(1 for k in sh["open"] if k.startswith("fl:"))
sig = sc.feed(sh, "BTCUSDT", bars, lean=2.0)
check("fl" in sig and "fl:BTCUSDT" in sh["open"] and n_fl == 1, "fl otwierana tylko na ostatniej (żywej) świecy każdego przebiegu")
check("fl" in sc.STRATS and "fl" in sc.stats([], NOW), "fl w statystykach cienia")

# 8. cykl scalpera ze wskaźnikiem: zlecenie tylko przez bramkę
class Pub:
    def __init__(s): s.bars = {}
    def _request(s, path, p=None, **k):
        if "instruments" in path: return {"list": [{"lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}, "priceFilter": {"tickSize": "0.01"}}]}
        if "orderbook" in path: return {"b": [["100", "1"]], "a": [["100.02", "1"]]}
        rows = [[str(x[0] * 1000), *map(str, x[1:]), "0"] for x in s.bars[p["symbol"]]]
        if p.get("end"): rows = [r for r in rows if int(r[0]) <= p["end"]]
        return {"list": rows[::-1][:p["limit"]]}


class Api:
    def __init__(s): s.calls = []
    def ok(s, m, path, p=None, allow=()):
        s.calls.append((m, path, p))
        if "wallet" in path: return {"list": [{"totalEquity": "10000"}]}
        return {"list": []}


class FlowStub:
    def __init__(s, v): s.v, s.asked, s.t = v, [], 1000
    def reading(s, sym, at): s.asked.append((sym, at)); return (s.t, s.v)


dd = tempfile.mkdtemp()
cfg = {"mode": "demo", "max_capital": 0, "max_lev": 2.0}
scf = sc.scalp_cfg(cfg, {}.get)
scf["symbols"] = ["BTCUSDT"]
pub, ap = Pub(), Api()
pub.bars["BTCUSDT"] = bars
live = sc.Live(dd, cfg, scf, ap, pub)
st = sc.load_state(dd)
now = bars[-1][0] + 305
stub = FlowStub(1.4)
sc.cycle(dd, cfg, scf, pub, live, st, now, True, stub)                    # rozbieg: bez wskaźnika i bez zleceń
check(not stub.asked and not any("order/create" in x[1] for x in ap.calls), "rozbieg nie używa wskaźnika i nie handluje")
nb = [bars[-1][0] + 300, bars[-1][4], bars[-1][4] * 1.0002, bars[-1][4] * 0.9998, bars[-1][4], 10.0]
pub.bars["BTCUSDT"] = bars + [nb]
now = nb[0] + 305
sc.cycle(dd, cfg, scf, pub, live, st, now, True, stub)
check(stub.asked == [("BTCUSDT", nb[0] + 300)] and "fl:BTCUSDT" in st["shadow"]["open"], "cykl pyta o wskaźnik na chwilę zamknięcia świecy; fl otwarta w cieniu")
check(not any("order/create" in x[1] for x in ap.calls) and sc.read_out(dd)["gate"] == "brak", "bez wyników w cieniu fl nie składa zleceń")
check(st["shadow"]["fl_t"] == {"BTCUSDT": 1000}, "zapamiętany odczyt wskaźnika, z którego powstało wejście")
st["shadow"]["trades"] += [[now - i * 7200, "fl", "BTCUSDT", 1, 0.3 + (0.5 if i % 2 else -0.5)] for i in range(40)]
del st["shadow"]["open"]["fl:BTCUSDT"]
nb2 = [nb[0] + 300, nb[4], nb[4] * 1.0002, nb[4] * 0.9998, nb[4], 10.0]
pub.bars["BTCUSDT"] = bars + [nb, nb2]
sc.cycle(dd, cfg, scf, pub, live, st, nb2[0] + 305, True, stub)
check("fl:BTCUSDT" not in st["shadow"]["open"] and not any("order/create" in x[1] for x in ap.calls), "ten sam godzinowy odczyt nie daje drugiego wejścia (po stopie nie wchodzimy ponownie na tym samym sygnale)")
stub.t = 4600                                                              # nowa godzina, nowy odczyt
nb3 = [nb2[0] + 300, nb2[4], nb2[4] * 1.0002, nb2[4] * 0.9998, nb2[4], 10.0]
pub.bars["BTCUSDT"] = bars + [nb, nb2, nb3]
nb2 = nb3
sc.cycle(dd, cfg, scf, pub, live, st, nb2[0] + 305, True, stub)
orders = [x[2] for x in ap.calls if "order/create" in x[1]]
check(len(orders) == 1 and orders[0]["side"] == "Buy" and orders[0]["orderLinkId"].startswith("sc-fl-") and st["live"]["open"]["BTCUSDT"]["hold_s"] == 144 * 300, f"po przejściu bramki: zlecenie fl ze stopem, limit czasu 12 h {orders}")
sd = st["live"]["open"]["BTCUSDT"]["sd"]
check(float(orders[0]["stopLoss"]) < nb2[4] < float(orders[0]["takeProfit"]) and abs((float(orders[0]["takeProfit"]) - nb2[4]) / sd - 2) < 0.02, "stop poniżej, cel 2R powyżej")
sc.cycle(dd, cfg, scf, pub, live, st, nb2[0] + 305, True, None)
check(True, "cykl bez modułu przepływu działa jak dotąd")
print("market flow ok", ok)
