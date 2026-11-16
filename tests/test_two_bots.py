"""Współpraca bota dziennego i scalpera na jednym koncie: wspólne limity konta, osobne serie strat, format zleceń."""
import sys, os, time, json, tempfile, itertools
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as t, btc_botcfg as bb, btc_risk as rk, btc_scalp as sc
import atexit; atexit.unregister(t.flush_notify)
t.NOTIFY_STATE = os.path.join(tempfile.mkdtemp(), "n.json")
os.environ.update(TRADER_MODE="demo", TRADER_API_KEY="k", TRADER_API_SECRET="s", TRADER_KEY_CREATED=time.strftime("%Y-%m-%d"))
NOW = time.time()
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1

# 1. ilość w zleceniu: zawsze zapis dziesiętny z dokładnością kroku giełdy
check(t.fmt_qty(0.007, 0.001) == "0.007" and t.fmt_qty(0.0069999999, 0.001) == "0.007", "krok 0,001")
check(t.fmt_qty(3333333.0, 1.0) == "3333333" and t.fmt_qty(123456.7, 0.1) == "123456.7", "duże ilości bez notacji naukowej i bez ucięcia do 6 cyfr")
check(t.fmt_qty(1e-05, 1e-05) == "0.00001" and t.fmt_qty(120.0, 10.0) == "120" and t.fmt_qty(2.5, 0.5) == "2.5", "skrajne kroki")
check(f"{3333333.0:g}" == "3.33333e+06", "dawny format: notacja naukowa, którą giełda odrzuca")

# 2. okresy scalpera i podział zamkniętych pozycji
d = tempfile.mkdtemp()
row = lambda sym, ago_s, pnl: {"symbol": sym, "closedPnl": str(pnl), "updatedTime": str(int((NOW - ago_s) * 1000))}
with open(os.path.join(d, "scalp_trades.jsonl"), "w") as f:
    f.write(json.dumps({"ts": int(NOW - 3600), "sym": "BTCUSDT", "held_s": 600, "r": -1.0}) + "\n")
    f.write("uszkodzona linia\n")
    f.write(json.dumps({"ts": int(NOW - 600), "sym": "ETHUSDT", "held_s": 300, "r": 0.5}) + "\n")
json.dump({"live": {"open": {"SOLUSDT": {"ts": NOW - 120}}}}, open(os.path.join(d, "scalp_state.json"), "w"))
w = t.scalp_windows(d)
check(len(w) == 3 and ("SOLUSDT", NOW - 130, None) in w, f"okresy: 2 zamknięte + 1 otwarta, uszkodzona linia pominięta {w}")
rows = [row("BTCUSDT", 3700, -5), row("BTCUSDT", 3 * 3600, -7), row("ETHUSDT", 610, 2), row("ETHUSDT", 30, 9), row("SOLUSDT", 60, -1), row("SOLUSDT", 900, -3), row("XRPUSDT", 3700, -4)]
own, scalp = t.split_scalp(rows, w)
check([r["closedPnl"] for r in scalp] == ["-5", "2", "-1"], f"scalper: pozycje zamknięte w jego okresach {scalp}")
check([r["closedPnl"] for r in own] == ["-7", "9", "-3", "-4"], "reszta: to samo aktywo poza okresem scalpera i inne aktywa")
check(t.split_scalp(rows, []) == (rows, []) and t.scalp_windows(tempfile.mkdtemp()) == [], "bez scalpera wszystko należy do bota dziennego")

# 3. wyłączniki: seria strat bez scalpera, strata tygodniowa z całego konta
cfg = {"streak_losses": 4, "streak_pause_h": 24, "max_weekly_loss_pct": 6.0, "max_drawdown_pct": 12.0}
ws = rk.week_start(NOW)
at = lambda k: max(ws + 60 + k, NOW - 3600 + k)
losses = [{"symbol": "BTCUSDT", "closedPnl": "-20", "updatedTime": str(int(at(k) * 1000))} for k in range(4)]
br, _, m = rk.evaluate(NOW, losses, 1000, 1000, cfg, {})
check([b["key"] for b in br] == ["streak", "weekly"] and m["streak"] == 4, "cztery straty z rzędu: seria i strata tygodniowa (80 z 1000 to 8%)")
br, _, m = rk.evaluate(NOW, losses, 1000, 1000, cfg, {}, streak_rows=[])
check([b["key"] for b in br] == ["weekly"] and m["streak"] == 0 and m["week_pnl"] == -80, "te same straty jako transakcje scalpera: bez serii, ale tydzień konta liczy wszystko")

# 4. cały przebieg bota dziennego: straty scalpera nie wstrzymują go serią strat
def mk(positions=(), rows=()):
    calls = []
    class FA:
        def __init__(s, c): pass
        def ok(s, m, path, params=None, allow=()):
            calls.append((m, path, dict(params or {})))
            if path == "/v5/position/closed-pnl": return {"list": list(rows), "nextPageCursor": ""} if (params or {}).get("startTime", 0) > (NOW - 8 * 86400) * 1000 else {"list": [], "nextPageCursor": ""}
            if path == "/v5/account/wallet-balance": return {"list": [{"totalEquity": "100000"}]}
            if path == "/v5/position/list": return {"list": list(positions)}
            if path == "/v5/order/realtime": return {"list": []}
            if path == "/v5/order/create": return {"orderId": "OID"}
            return {}
    return FA, calls
class P2:
    def klines(s, *a, **k): return []
    def ticker(s, sym): return {"lastPrice": "100", "turnover24h": "5e9"}
    def _request(s, path, params): return {"list": [{"status": "Trading", "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01"}, "priceFilter": {"tickSize": "0.01"}}]}
_n = itertools.count(1)
PLAN = lambda: {"action": "long", "setup": "x", "entry": 100.0, "stop": 95.0, "tp1": 110.0, "tp2": 120.0, "date": f"2026-11-{next(_n):02d}", "valid_until_ms": 2**60, "live_state": "waiting"}
def setup():
    dd = tempfile.mkdtemp(); t.HERE = dd; t.single_instance = lambda: None; t.load_env = lambda: None; t.make_pub = lambda c: P2()
    bb.write_settings(dd, {"symbols": ["ETHUSDT"], "max_orders_day": 5, "max_positions": 3, "max_lev": 2.0, "risk_pct": 1.0})
    return dd
def run(dd, FA):
    t.Api = FA; t.get_plan = lambda c, sym=None, pub=None, *a, **k: (PLAN(), 100.0, P2()); t.LOGBUF.clear(); t.run()
creates = lambda calls: [x[2] for x in calls if x[1] == "/v5/order/create" and not x[2].get("reduceOnly")]
srows = [{"symbol": "BTCUSDT", "closedPnl": str(-20 - k), "updatedTime": str(int((NOW - 3600 * (k + 1)) * 1000))} for k in range(5)]
dd = setup(); FA, calls = mk(rows=srows); run(dd, FA)
check(not creates(calls) and [b["key"] for b in bb.read_status(dd)["risk"]["breakers"]] == ["streak"], "kontrola: pięć strat bota dziennego wstrzymuje nowe pozycje")
dd = setup()
with open(os.path.join(dd, "scalp_trades.jsonl"), "w") as f:
    for k in range(5):
        f.write(json.dumps({"ts": int(NOW - 3600 * (k + 1)) + 5, "sym": "BTCUSDT", "held_s": 900, "r": -1.0, "strat": "mr"}) + "\n")
FA, calls = mk(rows=srows); run(dd, FA)
stt = bb.read_status(dd)
check(len(creates(calls)) == 1 and not stt["risk"]["breakers"] and stt["risk"]["metrics"]["streak"] == 0, f"te same straty z dziennika scalpera: bot dzienny handluje dalej {stt['risk']['breakers']}")
check(stt["pnl_today"] is not None and stt["risk"]["metrics"]["week_pnl"] <= 0, "strata konta (dzienna, tygodniowa) nadal obejmuje scalpera")

# 4b. pozycję scalpera prowadzi scalper: bot dzienny jej nie zamyka, nawet gdy panel obniżył dźwignię po jej otwarciu
spos = {"symbol": "BTCUSDT", "size": "0.5", "side": "Buy", "avgPrice": "100", "stopLoss": "99.7", "positionValue": "50", "leverage": "2", "unrealisedPnl": "0"}
def managed_state(dd):
    json.dump({"day": time.strftime("%Y-%m-%d", time.gmtime()), "orders": 0, "placed": [], "halted": False, "managed": {"BTCUSDT": int(NOW)}}, open(os.path.join(dd, "trader_state.json"), "w"))
closes = lambda calls: [x[2] for x in calls if x[1] == "/v5/order/create" and x[2].get("reduceOnly")]
dd = setup(); bb.write_settings(dd, {"max_lev": 1.0}); managed_state(dd)
FA, calls = mk(positions=[spos]); run(dd, FA)
check(len(closes(calls)) == 1 and bb.read_status(dd)["halted"], "kontrola: własna pozycja bota z dźwignią ponad limit jest zamykana")
dd = setup(); bb.write_settings(dd, {"max_lev": 1.0}); managed_state(dd)
json.dump({"live": {"open": {"BTCUSDT": {"ts": NOW - 60}}}}, open(os.path.join(dd, "scalp_state.json"), "w"))
FA, calls = mk(positions=[spos]); run(dd, FA)
stt = bb.read_status(dd)
check(not closes(calls) and not stt["halted"] and stt["positions"][0]["symbol"] == "BTCUSDT", "ta sama pozycja jako pozycja scalpera: widoczna w stanie konta, ale nietknięta")
check(len(creates(calls)) == 1 and creates(calls)[0]["symbol"] == "ETHUSDT", "bot dzienny handluje dalej na swoich aktywach")

# 5. scalper respektuje limity konta z panelu i wyłączniki konta
class Pub:
    def _request(s, path, p=None, **k): return {"list": [{"lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}, "priceFilter": {"tickSize": "0.01"}}]}
class Api:
    def __init__(s): s.calls, s.pos, s.closed, s.down = [], [], [], False
    def ok(s, m, path, p=None, allow=()):
        s.calls.append((m, path, p))
        if s.down: raise RuntimeError("giełda nie odpowiada")
        if "wallet" in path: return {"list": [{"totalEquity": "10000"}]}
        if "position/list" in path: return {"list": s.pos}
        if "closed-pnl" in path: return {"list": s.closed}
        return {"list": []}
ds = tempfile.mkdtemp()
c = {"mode": "demo", "max_capital": 0, "max_lev": 2.0, "symbols": ["BTCUSDT"]}
scf = sc.scalp_cfg(c, {}.get); scf["symbols"] = ["BTCUSDT"]
check(sc.account_limits(c, ds) == c, "bez ustawień z panelu: limity z trader.env")
bb.write_settings(ds, {"max_lev": 1.0, "max_capital": 4000.0})
lim = sc.account_limits(c, ds)
check(lim["max_lev"] == 1.0 and lim["max_capital"] == 4000.0 and c["max_lev"] == 2.0, "panel: dźwignia x1 i kapitał 4000 obowiązują też scalpera")
live_c = sc.account_limits({"mode": "live", "max_capital": 500.0, "max_lev": 1.5, "symbols": ["BTCUSDT"]}, ds)
check(live_c["max_lev"] == 1.0 and live_c["max_capital"] == 500.0, "live: panel może limity tylko zmniejszyć")
api = Api(); live = sc.Live(ds, c, scf, api, Pub()); s = sc.load_state(ds)
sc.cycle(ds, c, scf, Pub(), live, s, NOW, False)
check(live.c["max_lev"] == 1.0, "obrót wczytuje limity konta")
cap, _ = live.account()
check(cap == 4000.0, "kapitał do ryzyka ograniczony z panelu")
plan = {"side": 1, "stop_dist": 0.3, "tp_dist": 0.6, "hold": 10}
check(live.enter(s["live"], "BTCUSDT", "bo", plan, 1.0, 100.0, cap, [], NOW) is None, "wejście")
lev = [x[2] for x in api.calls if "set-leverage" in x[1]][-1]; o = [x[2] for x in api.calls if "order/create" in x[1]][-1]
check(lev["buyLeverage"] == "1" and float(o["qty"]) * 100.0 <= 4000 * 1.0 / 2 + 1e-6 and o["qty"] == "20.000", f"dźwignia x1, nominał najwyżej połowa limitu, ilość w formacie kroku {lev} {o['qty']}")
s["live"]["open"].clear()
check(live.blocked(s["live"], {}, NOW) is None and sc.account_block(ds, NOW) is None, "bez wyłączników")
bb.write_risk(ds, {"ts": int(NOW), "breakers": [{"key": "streak", "label": "seria 4 strat"}]})
check(sc.account_block(ds, NOW) is None, "seria strat bota dziennego nie dotyczy scalpera")
bb.write_risk(ds, {"ts": int(NOW), "breakers": [{"key": "weekly", "label": "strata tygodniowa -700 USDT"}]})
check("strata tygodniowa" in live.blocked(s["live"], {}, NOW), "strata tygodniowa konta wstrzymuje scalpera")
bb.write_risk(ds, {"ts": int(NOW), "breakers": [{"key": "drawdown", "label": "spadek kapitału 13% od szczytu"}]})
check("spadek kapitału" in live.blocked(s["live"], {}, NOW), "spadek kapitału wstrzymuje scalpera")
bb.write_risk(ds, {"ts": int(NOW) - 7200, "breakers": [{"key": "drawdown", "label": "x"}]})
check(sc.account_block(ds, NOW) is None, "nieaktualny stan ryzyka (bot dzienny nie działa) nie blokuje")
bb.write_status(ds, {"ts": int(NOW), "halted": True})
check("dzienny limit" in sc.account_block(ds, NOW), "dzienny limit straty konta wstrzymuje scalpera")
bb.write_status(ds, {"ts": int(NOW), "halted": False})
open(os.path.join(ds, "STOP"), "w").close()
check("STOP" in live.blocked(s["live"], {}, NOW), "plik STOP (wyłącznik awaryjny) wstrzymuje też scalpera")
os.remove(os.path.join(ds, "STOP"))
check(live.blocked(s["live"], {}, NOW) is None, "po usunięciu pliku STOP scalper wraca")

# 6. zamknięta pozycja bez wyniku z giełdy: czekamy, nie gubimy straty
s["live"]["open"]["BTCUSDT"] = {"strat": "bo", "side": 1, "qty": 20.0, "entry": 100.0, "sd": 0.3, "ts": NOW - 300, "hold_s": 3000, "link": "x"}
api.pos, api.closed = [], []
sc.cycle(ds, c, scf, Pub(), live, s, NOW, False)
check("BTCUSDT" in s["live"]["open"] and s["live"]["open"]["BTCUSDT"]["gone"] == NOW and sc.read_ledger(ds) == [], "pozycja zniknęła, wyniku jeszcze nie ma: czekamy")
api.closed = [{"closedPnl": "-7.2"}]
sc.cycle(ds, c, scf, Pub(), live, s, NOW + 40, False)
led = sc.read_ledger(ds)
check(len(led) == 1 and abs(led[0]["r"] + 1.2) < 1e-6 and abs(s["live"]["day_r"] + 1.2) < 1e-6 and "BTCUSDT" not in s["live"]["open"], f"wynik dotarł po 40 s: strata policzona w dziennym limicie {led}")
s["live"]["open"]["BTCUSDT"] = {"strat": "bo", "side": 1, "qty": 20.0, "entry": 100.0, "sd": 0.3, "ts": NOW - 300, "hold_s": 3000, "link": "x"}
api.closed = []
sc.cycle(ds, c, scf, Pub(), live, s, NOW + 100, False); sc.cycle(ds, c, scf, Pub(), live, s, NOW + 100 + sc.CLOSE_WAIT_S - 1, False)
check("BTCUSDT" in s["live"]["open"], "czekamy do 3 minut")
sc.cycle(ds, c, scf, Pub(), live, s, NOW + 101 + sc.CLOSE_WAIT_S, False)
led = sc.read_ledger(ds)
check(len(led) == 2 and led[1]["r"] is None and "BTCUSDT" not in s["live"]["open"], "po 3 minutach bez wyniku: wpis bez R (np. zlecenie niewypełnione)")

# 7. awaria konta nie zatrzymuje cienia
class PubBars(Pub):
    def __init__(s, bars): s.bars = bars
    def _request(s, path, p=None, **k):
        if "kline" in path:
            rows = [[str(x[0] * 1000), *map(str, x[1:]), "0"] for x in s.bars]
            return {"list": [r for r in rows if not p.get("end") or int(r[0]) <= p["end"]][::-1][:p["limit"]]}
        if "orderbook" in path: return {"b": [["100", "1"]], "a": [["100.02", "1"]]}
        return Pub._request(s, path, p)
T0 = int(NOW) // 300 * 300 - 80 * 300
bars = [[T0 + i * 300, 100.0, 100.05, 99.95, 100.0 + (0.01 if i % 2 else -0.01), 10.0] for i in range(80)]
d7 = tempfile.mkdtemp(); api7 = Api(); api7.down = True
live7 = sc.Live(d7, c, scf, api7, PubBars(bars)); s7 = sc.load_state(d7)
notes = sc.cycle(d7, c, scf, PubBars(bars), live7, s7, bars[-1][0] + 305, True)
out = sc.read_out(d7)
check(s7["shadow"]["last"].get("BTCUSDT") == bars[-1][0] and s7["seeded"] == ["BTCUSDT"], "giełda nie odpowiada na zapytania o konto: cień policzony")
check("konto niedostępne" in (out["blocked"] or "") and any("konto niedostępne" in n for n in notes), f"powód wstrzymania zleceń widoczny w panelu {out['blocked']}")
check(out["criteria"]["min_t"] == sc.GATE_MIN_T and out["brake"] == {} and out["gate_on"] == [] and "t" in out["strategies"]["mr"], "scalp.json: nowe pola dla panelu")
print("two bots ok", ok)
