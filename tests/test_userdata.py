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
check(set(v) == {"idea", "kind", "amt", "date", "note", "t"}, "nieznane pola są wyrzucane")
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
print("userdata ok", ok)
