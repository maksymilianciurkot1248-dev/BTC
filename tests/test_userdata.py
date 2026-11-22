"""Dane użytkownika na serwerze: walidacja, konflikty między urządzeniami, odporność na uszkodzony plik."""
import sys, os, json, tempfile, threading
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_userdata as ud
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
def raises(fn, exc=ValueError):
    try:
        fn()
    except exc as e:
        return e
    return None

d = tempfile.mkdtemp()
Z = lambda *es, goal=1000: {"entries": list(es), "goal": goal}
E = lambda idea="rowery", kind="in", amt=450.0, date="2026-11-20", note="", t=1: {"idea": idea, "kind": kind, "amt": amt, "date": date, "note": note, "t": t}

# 1. pusty stan, zapis, odczyt
check(ud.read(d, "zarobki") == {"found": False, "value": None, "ts": 0}, "pusty stan")
ts1 = ud.write(d, "zarobki", Z(E()), base_ts=0, now_ms=1000)
r = ud.read(d, "zarobki")
check(r["found"] and r["ts"] == ts1 == 1000 and r["value"]["entries"][0]["amt"] == 450.0 and r["value"]["goal"] == 1000, f"zapis i odczyt {r}")
check(oct(os.stat(os.path.join(d, ud.FILE)).st_mode & 0o777) == "0o640", "plik z prawami 640 (bez dostępu dla innych)")

# 2. konflikty: drugie urządzenie ze starą wersją nie nadpisuje cudzego zapisu
ts2 = ud.write(d, "zarobki", Z(E(), E(kind="out", amt=300, t=2)), base_ts=ts1, now_ms=2000)
check(ts2 == 2000, "zapis na podstawie aktualnej wersji")
e = raises(lambda: ud.write(d, "zarobki", Z(E(amt=1, t=9)), base_ts=ts1), ud.Conflict)
check(e is not None and e.ts == ts2 and len(e.value["entries"]) == 2, "zapis na podstawie starej wersji = konflikt z wersją z serwera")
check(len(ud.read(d, "zarobki")["value"]["entries"]) == 2, "konflikt nie zmienia danych")
e = raises(lambda: ud.write(d, "zarobki", Z(E()), base_ts=None), ud.Conflict)
check(e is not None, "klient, który niczego nie wczytał (base 0), nie nadpisze istniejących danych")
ts3 = ud.write(d, "zarobki", Z(E()), base_ts=ts2, now_ms=1500)
check(ts3 == ts2 + 1, "znacznik czasu zawsze rośnie, także przy cofniętym zegarze")

# 3. walidacja: nieznany klucz, złe pola, rozmiary
check(raises(lambda: ud.read(d, "hasla")) and raises(lambda: ud.write(d, "../etc", {})), "tylko znane klucze")
for bad in (Z(E(kind="x")), Z(E(amt=-1)), Z(E(amt=float("inf"))), Z(E(amt="12")), Z(E(date="2026-13-40")), Z(E(date="jutro")), {"entries": "x"}, [], {"entries": [E()] * 5001}):
    check(raises(lambda b=bad: ud.write(d, "zarobki", b, base_ts=ts3)), f"odrzucone: {str(bad)[:50]}")
check(raises(lambda: ud.write(d, "zarobki", Z(E(amt=True), ), base_ts=ts3)), "wartość logiczna nie jest liczbą")
ts4 = ud.write(d, "zarobki", Z(E(idea="x" * 500, note="n" * 500, amt=1.23456789)), base_ts=ts3)
v = ud.read(d, "zarobki")["value"]["entries"][0]
check(len(v["idea"]) == 60 and len(v["note"]) == 100 and v["amt"] == 1.2346, "teksty przycięte, liczby zaokrąglone")
check(set(v) == {"idea", "kind", "amt", "date", "note", "t", "ref"}, "nieznane pola są wyrzucane")
ts5 = ud.write(d, "zarobki", {"entries": [dict(E(), ukryte="<script>")], "goal": None, "obce": 1}, base_ts=ts4)
check("ukryte" not in ud.read(d, "zarobki")["value"]["entries"][0] and ud.read(d, "zarobki")["value"]["goal"] is None, "obce pola wycięte, pusty cel = None")

# 4. lista z Produktów i profil mentora
L = [{"name": "Rower", "buy": 300, "sell": 450, "profit": 150, "roi": 50, "hr": None, "t": 5, "obce": "x"}]
tl = ud.write(d, "produkty_lista", L, base_ts=0)
check(ud.read(d, "produkty_lista")["value"][0]["profit"] == 150 and "obce" not in ud.read(d, "produkty_lista")["value"][0], "lista produktów")
check(raises(lambda: ud.write(d, "produkty_lista", {"a": 1}, base_ts=tl)) and raises(lambda: ud.write(d, "produkty_lista", [1], base_ts=tl)), "zła lista odrzucona")
P = {"p_cap": 1000, "p_time": "5", "p_risk": "małe", "p_skills": "mam busa" * 100, "p_goal": "+1500", "obce": "x"}
ud.write(d, "mentor_profile", P, base_ts=0)
pv = ud.read(d, "mentor_profile")["value"]
check(pv["p_cap"] == "1000" and len(pv["p_skills"]) == 500 and "obce" not in pv, "profil: teksty, przycięcie")
check(ud.read(d, "zarobki")["ts"] == ts5, "zapis jednego klucza nie rusza pozostałych")

# 5. uszkodzony plik: odłożony obok, nie kasujemy, nie wywracamy API
open(os.path.join(d, ud.FILE), "w").write("{ to nie jest json")
check(ud.read(d, "zarobki")["found"] is False and any(n.startswith(ud.FILE + ".bad-") for n in os.listdir(d)), "uszkodzony plik odłożony jako .bad-*")
ud.write(d, "zarobki", Z(E()), base_ts=0)
check(ud.read(d, "zarobki")["found"], "po uszkodzeniu można pisać od nowa")

# 6. równoległe zapisy: dokładnie jeden wygrywa na tej samej wersji bazowej
d2 = tempfile.mkdtemp(); base = ud.write(d2, "zarobki", Z(), base_ts=0)
res = []
def w(i):
    try:
        ud.write(d2, "zarobki", Z(E(idea=f"i{i}", t=i)), base_ts=base); res.append("ok")
    except ud.Conflict:
        res.append("conflict")
ths = [threading.Thread(target=w, args=(i,)) for i in range(8)]
[t.start() for t in ths]; [t.join() for t in ths]
check(res.count("ok") == 1 and res.count("conflict") == 7, f"równoległe zapisy: jeden wygrywa {res}")
# 7. potok ofert: nowe pola i statusy, stare wpisy bez statusu = kandydat, odrzucone adresy spoza http(s)
d3 = tempfile.mkdtemp()
D = lambda **k: dict({"t": 1, "name": "Rower", "buy": 300, "sell": 450}, **k)
t0 = ud.write(d3, "produkty_lista", [D(), D(t=2, status="kupione", url="https://www.olx.pl/d/oferta/x.html", bought_price=300, costs=20, d_bought="2026-11-20"), D(t=3, url="javascript:alert(1)"), D(t=4, url="ftp://x")], base_ts=0)
L2 = ud.read(d3, "produkty_lista")["value"]
check(L2[0]["status"] == "kandydat" and L2[1]["status"] == "kupione" and L2[1]["costs"] == 20 and L2[1]["d_bought"] == "2026-11-20" and L2[1]["url"].startswith("https://www.olx.pl"), "nowe pola oferty")
check(L2[2]["url"] == "" and L2[3]["url"] == "" and L2[0]["costs"] is None and L2[0]["sold_price"] is None, "adresy inne niż http(s) wycięte, puste pola = None")
for bad in (D(status="gotowe"), D(d_sold="wczoraj"), D(fee=150), D(costs=-5), D(sold_price=-1)):
    check(raises(lambda b=bad: ud.write(d3, "produkty_lista", [b], base_ts=t0)), f"zła oferta odrzucona: {str(bad)[-40:]}")
ud.write(d3, "alfred_tasks", [{"t": 1, "text": "Wystawić: Rower", "date": "2026-11-21", "done": False, "top": False, "min": 20, "ref": "d1"}], base_ts=0)
tk = ud.read(d3, "alfred_tasks")["value"][0]
check(tk["min"] == 20 and tk["ref"] == "d1" and ud.write(d3, "alfred_tasks", [dict(tk, min=None)], base_ts=ud.read(d3, "alfred_tasks")["ts"]) and ud.read(d3, "alfred_tasks")["value"][0]["min"] is None, "zadanie: minuty i odwołanie do oferty")
check(raises(lambda: ud.write(d3, "alfred_tasks", [dict(tk, min=0)], base_ts=ud.read(d3, "alfred_tasks")["ts"])) and raises(lambda: ud.write(d3, "alfred_tasks", [dict(tk, min=9999)], base_ts=ud.read(d3, "alfred_tasks")["ts"])), "minuty poza zakresem odrzucone")

# 8. mutate: atomowy zapis kilku kluczy, odczyt domyślnych, brak zapisu przy błędzie
d4 = tempfile.mkdtemp()
def fn_ok(v):
    check(v["zarobki"] == {"entries": [], "goal": None} and v["produkty_lista"] == [], "brakujące klucze mają wartości domyślne")
    return {"zarobki": {"entries": [E(t=5)], "goal": 100}, "produkty_lista": [D()]}
ts, vals = ud.mutate(d4, ["zarobki", "produkty_lista", "alfred_tasks"], fn_ok, now_ms=5000)
check(ts == {"zarobki": 5000, "produkty_lista": 5000} and vals["alfred_tasks"] == [] and ud.read(d4, "zarobki")["value"]["goal"] == 100 and ud.read(d4, "produkty_lista")["ts"] == 5000, f"zapis dwóch kluczy naraz {ts}")
before = open(os.path.join(d4, ud.FILE)).read()
def fn_bad(v):
    return {"zarobki": {"entries": [E(t=6)], "goal": 1}, "produkty_lista": [D(status="zły")]}
check(raises(lambda: ud.mutate(d4, ["zarobki", "produkty_lista"], fn_bad)) and open(os.path.join(d4, ud.FILE)).read() == before, "błąd walidacji jednego klucza: nic się nie zapisało (atomowo)")
def fn_abort(v):
    raise ValueError("przerwane")
check(raises(lambda: ud.mutate(d4, ["zarobki"], fn_abort)) and open(os.path.join(d4, ud.FILE)).read() == before, "ValueError z funkcji: bez zapisu")
check(ud.mutate(d4, ["zarobki"], lambda v: {}) [0] == {} and open(os.path.join(d4, ud.FILE)).read() == before, "brak zmian: bez zapisu")
ts2, _ = ud.mutate(d4, ["zarobki"], lambda v: {"zarobki": dict(v["zarobki"], goal=2)}, now_ms=100)
check(ts2["zarobki"] == 5001 and raises(lambda: ud.mutate(d4, ["hasla"], lambda v: {})) and raises(lambda: ud.mutate(d4, ["zarobki"], lambda v: {"hasla": 1})), "ts zawsze rośnie; nieznane klucze odrzucone")
print("userdata ok", ok)
