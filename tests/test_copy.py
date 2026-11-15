import os, sys, tempfile, random, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_copy as bc
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
DAY = 86400000
# 1. round_trips: Close Long, pnl = sz*(exit-entry)
f = [[1000000, "BTC", "Close Long", 110.0, 2.0, 20.0, 0.1, "a"], [1000000+1000, "BTC", "Close Long", 110.0, 3.0, 30.0, 0.15, "b"],
     [2000000, "ETH", "Close Short", 90.0, 1.0, 10.0, 0.05, "c"], [3000000, "ETH", "Long > Short", 90.0, 1.0, 5.0, 0.05, "d"], [4000000, "X", "Open Long", 1, 1, 0, 0, "e"]]
tr = bc.round_trips(f)
check(len(tr) == 2, "dwie transakcje (odwrócenie i otwarcie pominięte)")
check(tr[0][1] == "BTC" and tr[0][2] == "long", "strona")
check(abs(tr[0][4] - 5 * 100.0) < 1e-6, "nominał z ceny wejścia 100")
check(abs(tr[0][3] - (50 - 0.25 - 0.25 * 100 / 110) / 500) < 1e-9, "wynik netto")
check(abs(tr[1][4] - 100.0) < 1e-6 and tr[1][2] == "short", "short: wejście 100")
# 2. BH
a = bc.bh([0.01, 0.04, 0.03, 0.5])
check(abs(a[0] - 0.04) < 1e-9 and abs(a[1] - 0.04 * 4 / 3) < 1e-9 and abs(a[2] - 0.04 * 4 / 3) < 1e-9 and a[3] == 0.5, "BH")
check(bc.bh([]) == [], "BH pusty")
# 3. adres
d = tempfile.mkdtemp()
try:
    bc.add_wallet(d, "0x123"); check(False, "zły adres")
except ValueError: check(True, "zły adres")
A, B, C = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40
now = 200 * DAY
check(bc.add_wallet(d, A, "zysk", now_ms=now - 100 * DAY), "dodano A")
check(not bc.add_wallet(d, A.upper().replace("0X", "0x")), "duplikat bez względu na wielkość liter")
bc.add_wallet(d, B, "los", now_ms=now - 100 * DAY)
bc.add_wallet(d, C, "szczęściarz", now_ms=now - 100 * DAY)
# 4. syntetyczne fille: A ma realną przewagę (+0.8% brutto na transakcję), B szum zerowy, C jedna wielka wygrana
rnd = random.Random(7)
def mk(i, t, pct, coin="BTC", notional=1000.0):
    entry = 100.0; exit_ = entry * (1 + pct); sz = notional / entry
    return {"time": t, "coin": coin, "px": str(exit_), "sz": str(sz), "dir": "Close Long", "closedPnl": str(sz * (exit_ - entry)), "fee": str(notional * 0.00045), "tid": f"{t}-{i}"}
fills = {A: [], B: [], C: []}
for i in range(300):
    t = (now - 100 * DAY) + i * (DAY // 3) + 1
    fills[A].append(mk(i, t, 0.008 + rnd.gauss(0, 0.006)))
    fills[B].append(mk(i, t, rnd.gauss(0.0, 0.01)))
    fills[C].append(mk(i, t, (0.5 if i == 150 else -0.0002 + rnd.gauss(0, 0.002))))
calls = []
def fake_post(body):
    calls.append(body)
    rows = [r for r in fills[body["user"]] if r["time"] >= body["startTime"]]
    return rows[:2000]
res, msgs = bc.run(d, now_ms=now, post_fn=fake_post, sleep=lambda s: None)
st = {r["label"]: r for r in res["wallets"]}
check(st["zysk"]["status"] == "kandydat", "A kandydat: " + st["zysk"]["why"])
check(st["los"]["status"] != "kandydat", "B nie: " + st["los"]["why"])
check(st["szczęściarz"]["status"] != "kandydat", "C nie: " + st["szczęściarz"]["why"])
check(st["zysk"]["fwd"]["n"] > 200 and st["zysk"]["hist"]["n"] == 0, "historia sprzed dodania pusta, reszta poza próbą")
# 5. przyrostowość i dedup
n1 = len(calls)
res2, _ = bc.run(d, now_ms=now, post_fn=fake_post, sleep=lambda s: None)
data = json.load(open(os.path.join(d, bc.DATA)))
check(len(data[A]["fills"]) == 300, "bez duplikatów po drugim przebiegu")
# 6. portfel dodany PO historii: wynik sprzed dodania nie liczy się do kandydatury
d2 = tempfile.mkdtemp()
bc.add_wallet(d2, A, "późno", now_ms=now + 1)          # wszystko, co się wydarzyło, jest „historią”
res3, _ = bc.run(d2, now_ms=now + 1, post_fn=fake_post, sleep=lambda s: None)
w = res3["wallets"][0]
check(w["status"] != "kandydat" and w["hist"]["n"] > 200, "sama historia nie daje kandydatury: " + w["why"])
# 7. błąd sieci nie przerywa
def bad(body):
    raise OSError("brak sieci")
d3 = tempfile.mkdtemp(); bc.add_wallet(d3, A)
r4, m4 = bc.run(d3, post_fn=bad, sleep=lambda s: None)
check(any(k == "alert" for k, _ in m4) and r4 is not None, "błąd zgłoszony, program działa")
check(bc.run(tempfile.mkdtemp())[0] is None, "bez portfeli nic nie robi")
# 8. ranking
rows = [{"ethAddress": A, "accountValue": "50000", "displayName": "x", "windowPerformances": [["day", {}], ["month", {"pnl": "5000", "roi": "0.4", "vlm": "1000000"}]]},
        {"ethAddress": B, "accountValue": "500", "windowPerformances": [["month", {"pnl": "5000", "roi": "9", "vlm": "1000000"}]]},
        {"ethAddress": C, "accountValue": "50000", "windowPerformances": [["month", {"pnl": "-5", "roi": "0.9", "vlm": "1000000"}]]}]
check([x[1] for x in bc.top_from_leaderboard(rows)] == [A], "ranking odsiewa małe konta i stratne")
# 9. remove
check(bc.remove_wallet(d, B) and len(bc.read_wallets(d)) == 2, "usuwanie")
print("OK", ok)
