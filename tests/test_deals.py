"""Potok ofert: zmiana statusu aktualizuje naraz ofertę, wpisy Zarobków i zadania planu (atomowo), cofanie sprząta tylko własne wpisy, alerty o stojących ofertach."""
import sys, os, json, tempfile, datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_deals as bd, btc_userdata as ud
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
def raises(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    return None

TZ = ZoneInfo("Europe/Warsaw")
NOW = datetime.datetime(2026, 11, 23, 10, 0, tzinfo=TZ).timestamp()
DAY = 86400
def fresh():
    d = tempfile.mkdtemp()
    deal = {"t": 17900000000001, "name": "Rower Kross", "status": "kandydat", "buy": 300, "sell": 450, "fee": 10, "url": "https://www.olx.pl/d/oferta/x.html"}
    ud.write(d, "produkty_lista", [deal, {"t": 17900000000002, "name": "Inny", "status": "kandydat", "buy": 50, "sell": 90}], base_ts=0)
    ud.write(d, "zarobki", {"entries": [{"idea": "naprawy", "kind": "in", "amt": 200, "date": "2026-11-20", "note": "ręczny wpis", "t": 5, "ref": ""}], "goal": 1500}, base_ts=0)
    ud.write(d, "alfred_tasks", [{"t": 9, "text": "Zakupy", "date": "2026-11-23", "done": False, "top": True, "done_ts": None, "min": 30, "ref": ""}], base_ts=0)
    return d, deal["t"]
tasks_of = lambda d: ud.read(d, "alfred_tasks")["value"]
ents_of = lambda d: ud.read(d, "zarobki")["value"]["entries"]
deal_of = lambda d, t: next(x for x in ud.read(d, "produkty_lista")["value"] if x["t"] == t)

# 1. kupione: koszt w Zarobkach + zadanie „Wystawić”, wszystko jednym zapisem
d, T = fresh()
r = bd.transition(d, T, "bought", {"price": 280, "costs": 20, "date": "2026-11-22"}, now=NOW)
x = deal_of(d, T)
check(x["status"] == "kupione" and x["bought_price"] == 280 and x["costs"] == 20 and x["d_bought"] == "2026-11-22" and r["deal"]["status"] == "kupione", "oferta po zakupie")
e = [e for e in ents_of(d) if e["ref"]]
check(len(e) == 1 and e[0] == {"idea": "Odsprzedaż", "kind": "out", "amt": 300.0, "date": "2026-11-22", "note": "kupno: Rower Kross (+ koszty 20)", "t": e[0]["t"], "ref": f"d{T}b"}, f"koszt w Zarobkach {e}")
tk = [t for t in tasks_of(d) if t["ref"]]
check(len(tk) == 1 and tk[0]["text"] == "Wystawić: Rower Kross" and tk[0]["date"] == "2026-11-24" and tk[0]["min"] == 20 and not tk[0]["done"] and not tk[0]["top"], f"zadanie w planie {tk}")
check(set(r["ts"]) == {"produkty_lista", "zarobki", "alfred_tasks"} and len(set(r["ts"].values())) == 1 and set(r["values"]) == set(r["ts"]), "trzy klucze zapisane razem (ten sam znacznik), odpowiedź zawiera nowe wartości")
check(any(t["text"] == "Zakupy" for t in tasks_of(d)) and any(e["note"] == "ręczny wpis" for e in ents_of(d)) and deal_of(d, 17900000000002)["status"] == "kandydat", "ręczne wpisy, zadania i inne oferty nietknięte")

# 2. wystawione: zamyka „Wystawić”, dodaje „Sprawdzić zainteresowanie” za 5 dni
bd.transition(d, T, "listed", {"price": 460}, now=NOW + DAY)
x = deal_of(d, T)
check(x["status"] == "wystawione" and x["list_price"] == 460 and x["d_listed"] == "2026-11-24", "oferta po wystawieniu (data domyślna = dziś)")
tk = {t["text"]: t for t in tasks_of(d) if t["ref"]}
check(tk["Wystawić: Rower Kross"]["done"] and tk["Wystawić: Rower Kross"]["done_ts"] == int((NOW + DAY) * 1000) and tk["Sprawdzić zainteresowanie: Rower Kross"]["date"] == "2026-11-29" and not tk["Sprawdzić zainteresowanie: Rower Kross"]["done"], f"zadania po wystawieniu {tk}")

# 3. sprzedane: przychód + prowizja (z % oferty), zadania zamknięte, zysk po kosztach
bd.transition(d, T, "sold", {"price": 450, "date": "2026-11-28"}, now=NOW + 5 * DAY)
x = deal_of(d, T)
check(x["status"] == "sprzedane" and x["sold_price"] == 450 and x["fee_paid"] == 45 and x["d_sold"] == "2026-11-28", f"sprzedaż, prowizja 10% = 45 zł {x['fee_paid']}")
refs = {e["ref"]: e for e in ents_of(d) if e["ref"]}
check(set(refs) == {f"d{T}b", f"d{T}s", f"d{T}f"} and refs[f"d{T}s"]["kind"] == "in" and refs[f"d{T}s"]["amt"] == 450 and refs[f"d{T}f"]["kind"] == "out" and refs[f"d{T}f"]["amt"] == 45, "wpisy: koszt, przychód, prowizja")
check(all(t["done"] for t in tasks_of(d) if t["ref"]), "zadania oferty zamknięte")
check(bd.realized(x) == 105.0, f"zysk po kosztach 450 − 300 − 45 = 105 ({bd.realized(x)})")
zar = ud.read(d, "zarobki")["value"]["entries"]
check(sum(e["amt"] if e["kind"] == "in" else -e["amt"] for e in zar if e["idea"] == "Odsprzedaż") == 105.0, "Zarobki sumują się do tego samego zysku")

# 4. cofanie krok po kroku sprząta tylko wpisy z tej oferty
bd.transition(d, T, "undo", now=NOW + 6 * DAY)
check(deal_of(d, T)["status"] == "wystawione" and deal_of(d, T)["sold_price"] is None and not [e for e in ents_of(d) if e["ref"] in (f"d{T}s", f"d{T}f")] and any(e["ref"] == f"d{T}b" for e in ents_of(d)), "cofnięcie sprzedaży usuwa przychód i prowizję, koszt zostaje")
bd.transition(d, T, "undo", now=NOW + 6 * DAY)
check(deal_of(d, T)["status"] == "kupione" and deal_of(d, T)["list_price"] is None, "cofnięcie wystawienia")
bd.transition(d, T, "undo", now=NOW + 6 * DAY)
check(deal_of(d, T)["status"] == "kandydat" and not [e for e in ents_of(d) if e["ref"]] and not [t for t in tasks_of(d) if t["ref"]] and any(t["text"] == "Zakupy" for t in tasks_of(d)) and len(ents_of(d)) == 1, "cofnięcie zakupu usuwa koszt i automatyczne zadania; ręczne zostają")
check(raises(lambda: bd.transition(d, T, "undo", now=NOW)), "z kandydata nie ma czego cofać")
bd.transition(d, T, "bought", {"price": 290}, now=NOW); bd.transition(d, T, "undo", now=NOW); bd.transition(d, T, "bought", {"price": 290}, now=NOW)
check(len([e for e in ents_of(d) if e["ref"] == f"d{T}b"]) == 1 and len([t for t in tasks_of(d) if t["ref"] == f"d{T}"]) == 1, "kupno, cofnięcie i ponowne kupno: dokładnie jeden wpis i jedno zadanie (bez duplikatów)")

# 5. sprzedaż prosto z „kupione”, cena domyślna z wystawienia, ręczna prowizja
d2, T2 = fresh()
bd.transition(d2, T2, "bought", {}, now=NOW)
check(deal_of(d2, T2)["bought_price"] == 300 and [e for e in ents_of(d2) if e["ref"]][0]["amt"] == 300, "cena kupna domyślnie z kalkulatora (300)")
bd.transition(d2, T2, "sold", {"price": 400, "fee": 12.5}, now=NOW)
check(deal_of(d2, T2)["fee_paid"] == 12.5 and bd.realized(deal_of(d2, T2)) == 87.5, "sprzedaż bez wystawiania, prowizja podana ręcznie")
d3, T3 = fresh()
bd.transition(d3, T3, "bought", {}, now=NOW); bd.transition(d3, T3, "listed", {}, now=NOW)
check(deal_of(d3, T3)["list_price"] == 450, "cena wystawienia domyślnie z kalkulatora (450)")
bd.transition(d3, T3, "sold", {}, now=NOW)
check(deal_of(d3, T3)["sold_price"] == 450 and deal_of(d3, T3)["fee_paid"] == 45, "cena sprzedaży domyślnie z wystawienia")

# 6. niedozwolone przejścia i złe dane: nic się nie zapisuje
d4, T4 = fresh(); snap = open(os.path.join(d4, ud.FILE)).read()
for act, p in (("listed", {}), ("sold", {}), ("undo", {}), ("bought", {"price": -5}), ("bought", {"price": "dużo"}), ("bought", {"date": "wczoraj"}), ("bought", {"price": 1e12}), ("zjedz", {})):
    check(raises(lambda a=act, pp=p: bd.transition(d4, T4, a, pp, now=NOW)), f"odrzucone: {act} {p}")
check(raises(lambda: bd.transition(d4, 123, "bought", {}, now=NOW)) and open(os.path.join(d4, ud.FILE)).read() == snap, "nieznana oferta; plik bez zmian po błędach")
ud.write(d4, "produkty_lista", [{"t": 77, "name": "Bez ceny", "status": "kandydat"}], base_ts=ud.read(d4, "produkty_lista")["ts"])
check("cenę kupna" in raises(lambda: bd.transition(d4, 77, "bought", {}, now=NOW)), "brak ceny: prośba o podanie")
bd.transition(d4, T4 if False else 77, "bought", {"price": 10}, now=NOW)
check(raises(lambda: bd.transition(d4, 77, "bought", {"price": 10}, now=NOW)) and raises(lambda: bd.transition(d4, 77, "reject", {}, now=NOW)), "kupione nie wraca do kupna ani odrzucenia")

# 7. odrzucenie i usunięcie
d5, T5 = fresh()
bd.transition(d5, T5, "reject", {}, now=NOW)
check(deal_of(d5, T5)["status"] == "odrzucone" and not ents_of(d5)[1:], "odrzucenie bez wpisów w Zarobkach")
bd.transition(d5, T5, "undo", {}, now=NOW)
check(deal_of(d5, T5)["status"] == "kandydat", "odrzucone można przywrócić")
bd.transition(d5, T5, "bought", {"price": 100}, now=NOW); bd.transition(d5, T5, "sold", {"price": 150}, now=NOW)
r = bd.transition(d5, T5, "delete", now=NOW)
check(r["deal"] is None and not [x for x in ud.read(d5, "produkty_lista")["value"] if x["t"] == T5] and not [e for e in ents_of(d5) if e["ref"]] and not [t for t in tasks_of(d5) if t["ref"]] and len(ents_of(d5)) == 1 and len(ud.read(d5, "produkty_lista")["value"]) == 1, "usunięcie oferty usuwa jej wpisy i zadania, reszta zostaje")

# 8. statystyki i oferty wymagające uwagi
today = "2026-11-23"
mk = lambda t, status, **k: dict({"t": t, "name": f"O{t}", "status": status, "buy": 100, "sell": 160, "fee": 10, "url": "", "profit": None, "roi": None, "hr": None, "costs": 0, "bought_price": None, "list_price": None, "sold_price": None,
                                 "fee_paid": None, "d_bought": "", "d_listed": "", "d_sold": "", "note": ""}, **k)
deals = [mk(1, "kandydat"), mk(2, "kupione", bought_price=100, costs=10, d_bought="2026-11-18"), mk(3, "wystawione", bought_price=200, d_bought="2026-11-01", d_listed="2026-11-05", list_price=300),
         mk(4, "sprzedane", bought_price=50, costs=5, d_bought="2026-11-10", d_listed="2026-11-11", list_price=90, sold_price=90, fee_paid=9, d_sold="2026-11-20"), mk(5, "sprzedane", bought_price=80, d_bought="2026-10-01", sold_price=60, fee_paid=0, d_sold="2026-10-11"),
         mk(6, "odrzucone"), mk(int((NOW - 20 * DAY) * 1000) * 10, "kandydat", name="Stary kandydat")]
s = bd.stats(deals, today)
check(s["counts"] == {"kandydat": 2, "kupione": 1, "wystawione": 1, "sprzedane": 2, "odrzucone": 1} and s["invested"] == 310.0, f"liczby i zamrożony kapitał {s}")
check(s["expected_profit"] == round((160 - 110 - 16) + (300 - 200 - 30), 2) and s["realized_month"] == 26.0 and s["realized_total"] == 6.0 and s["sold_n"] == 2 and s["avg_days_to_sell"] == 10.0, f"zyski i czas sprzedaży {s}")
at = bd.attention(deals, today, NOW)
check([a["text"].split(":")[0] for a in at] == ["O3", "O2", "Stary kandydat"] and at[0]["level"] == "warn" and at[2]["level"] == "info" and "wystawione 18 dni" in at[0]["text"] and "kupione 5 dni" in at[1]["text"] and "kandydat od 20 dni" in at[2]["text"], f"alerty {at}")
check(bd.attention([mk(1, "kupione", d_bought="2026-11-22"), mk(2, "wystawione", d_listed="2026-11-15")], today, NOW) == [], "świeże oferty nie alarmują")
check(bd.stats([], today)["avg_days_to_sell"] is None and bd.realized(mk(1, "kupione")) is None, "puste dane")
print("deals ok", ok)
