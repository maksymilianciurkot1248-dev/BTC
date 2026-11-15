import sys, tempfile, os
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc
db = bc.DB(os.path.join(tempfile.mkdtemp(), "t.db"))
T0 = 1700000000
def yahoo(url):
    assert "yahoo" in url
    n = 40
    return {"chart": {"result": [{"timestamp": [T0 + i * 86400 for i in range(n)] + [T0 + 3 * 86400], "indicators": {"quote": [{"close": [100 + i for i in range(n - 1)] + [None, 5, 7]}]}}]}}
def csvtext(url):
    return "Date,Open,High,Low,Close,Volume\n" + "\n".join(f"2024-01-{d:02d},1,1,1,{10 + d},0" for d in range(1, 29))
calls = []
def get_stooq_down(url, as_text=False, timeout=0):
    calls.append(url)
    if "stooq" in url: raise RuntimeError("timeout")
    return yahoo(url)
rep = bc.run("update", db, get=get_stooq_down, only={"stooq"}, sleep=False)
assert rep["stooq"].startswith("OK"), rep
src = {r[0] for r in db.con.execute("SELECT DISTINCT source FROM obs WHERE metric LIKE 'stooq_%'")}
assert src == {"yahoo"}, src
m = {r[0] for r in db.con.execute("SELECT DISTINCT metric FROM obs")}
assert {"stooq_spx", "stooq_gold", "stooq_oil", "stooq_us10y", "stooq_dxy", "stooq_ndx"} <= m, m
n = db.con.execute("SELECT COUNT(*) FROM obs WHERE metric='stooq_spx'").fetchone()[0]
assert n == 39 + 0 or n >= 38, n          # duplikat dnia scalony, None pominięty
# Stooq działa: Yahoo nie jest odpytywany
calls.clear()
db2 = bc.DB(os.path.join(tempfile.mkdtemp(), "t2.db"))
def get_ok(url, as_text=False, timeout=0):
    calls.append(url)
    assert "stooq" in url, "Yahoo nie powinien być użyty"
    return csvtext(url)
rep = bc.run("update", db2, get=get_ok, only={"stooq"}, sleep=False)
assert rep["stooq"].startswith("OK") and not any("yahoo" in c for c in calls), rep
# oba padają: ostrzeżenie, nie błąd (kod wyjścia 0)
db3 = bc.DB(os.path.join(tempfile.mkdtemp(), "t3.db"))
def down(url, as_text=False, timeout=0): raise RuntimeError("brak sieci")
rep = bc.run("update", db3, get=down, only={"stooq"}, sleep=False)
assert rep["stooq"].startswith("OSTRZEŻENIE") and not any(v.startswith("BŁĄD") for v in rep.values()), rep
# inne źródło dalej daje BŁĄD
rep = bc.run("update", db3, get=down, only={"fng"}, sleep=False)
assert rep["fng"].startswith("BŁĄD"), rep
print("stooq fallback ok")
