"""Plan dnia: zadania układane w wolnych oknach kalendarza, następny krok, alerty, tydzień, kontekst dla modelu i walidacja zadań z rozmowy."""
import sys, os, json, tempfile, datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_alfred as al, btc_agenda as ag, btc_userdata as ud, btc_botcfg as bb, btc_deals as bd
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1

TZ = ZoneInfo("Europe/Warsaw")
at = lambda h, m=0, day=23: datetime.datetime(2026, 11, day, h, m, tzinfo=TZ).timestamp()
hm = lambda ts: datetime.datetime.fromtimestamp(ts, TZ).strftime("%H:%M")
rng = lambda b: f"{hm(b['s'])}-{hm(b['e'])}"
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//t//EN
BEGIN:VEVENT
UID:a@x
DTSTART:20261123T080000Z
DTEND:20261123T090000Z
SUMMARY:Trening
END:VEVENT
BEGIN:VEVENT
UID:b@x
DTSTART:20261123T130000Z
DTEND:20261123T143000Z
SUMMARY:Spotkanie z klientem
END:VEVENT
BEGIN:VEVENT
UID:c@x
DTSTART;VALUE=DATE:20261124
DTEND;VALUE=DATE:20261125
SUMMARY:Urlop Ani
TRANSP:TRANSPARENT
END:VEVENT
BEGIN:VEVENT
UID:d@x
DTSTART:20261124T090000Z
DTEND:20261124T100000Z
SUMMARY:Dentysta
END:VEVENT
END:VCALENDAR
"""
d = tempfile.mkdtemp()
env = {"AGENDA_ICS_URLS": "Dom|https://example.org/x.ics"}
ag.refresh(d, env=env, get=lambda url, **k: ICS, now=at(7))
agenda = ag.read(d)
T = lambda t, text, date="2026-11-23", top=False, mins=None, done=False, ref="", done_ts=None: {"t": t, "text": text, "date": date, "done": done, "top": top, "done_ts": done_ts, "min": mins, "ref": ref}

# 1. układanie zadań w wolnych oknach (pierwsze pasujące okno, od najważniejszych, z przerwą 5 min)
tasks = [T(1, "Raport", top=True, mins=90), T(2, "Telefon do banku", top=True, mins=20), T(3, "Zakupy", mins=30), T(4, "Duży projekt", mins=240), T(5, "Na jutro", date="2026-11-24", mins=30), T(6, "Zaległe", date="2026-11-20", mins=15), T(7, "Zrobione", done=True), T(8, "Drugi duży", mins=240)]
p = al.plan_day(agenda, tasks, "2026-11-23", at(7), "Europe/Warsaw")
check([(b["title"], rng(b)) for b in p["blocks"]] == [("Telefon do banku", "08:00-08:20"), ("Zaległe", "08:25-08:40"), ("Raport", "10:00-11:30"), ("Zakupy", "11:35-12:05"), ("Duży projekt", "15:30-19:30")], f"bloki {[(b['title'], rng(b)) for b in p['blocks']]}")
check([u["title"] for u in p["unscheduled"]] == ["Drugi duży"] and p["summary"]["overloaded"], "drugie czterogodzinne zadanie już się nie mieści")
check(next(b for b in p["blocks"] if b["title"] == "Zaległe")["late"] and not next(b for b in p["blocks"] if b["title"] == "Raport")["late"], "zaległe oznaczone")
check("Na jutro" not in [b["title"] for b in p["blocks"]] + [u["title"] for u in p["unscheduled"]], "zadanie na jutro nie wchodzi do dzisiejszego planu; zrobione też nie")
s = p["summary"]
check(s["busy_min"] == 150 and s["planned_min"] == 20 + 15 + 90 + 30 + 240 and s["need_min"] == 635 and s["free_min"] == 15 + 110 + 25, f"podsumowanie {s}")
check([(hm(a), hm(b)) for a, b in p["free"]] == [("08:45", "09:00"), ("12:10", "14:00"), ("19:35", "20:00")], f"wolne po ułożeniu {[(hm(a), hm(b)) for a, b in p['free']]}")
check([i["kind"] + ":" + i["title"] for i in p["timeline"]] == ["task:Telefon do banku", "task:Zaległe", "event:Trening", "task:Raport", "task:Zakupy", "event:Spotkanie z klientem", "task:Duży projekt"], f"oś dnia: wydarzenia i bloki po czasie {[i['kind'] + ':' + i['title'] for i in p['timeline']]}")
# środek dnia: okna liczone od „teraz”
p2 = al.plan_day(agenda, tasks, "2026-11-23", at(13), "Europe/Warsaw")
check([(b["title"], rng(b)) for b in p2["blocks"]] == [("Telefon do banku", "13:00-13:20"), ("Zaległe", "13:25-13:40"), ("Raport", "15:30-17:00"), ("Zakupy", "17:05-17:35")] and len(p2["unscheduled"]) == 2, "po południu: od teraz, omijając spotkanie")
p3 = al.plan_day(agenda, tasks, "2026-11-23", at(21), "Europe/Warsaw")
check(p3["blocks"] == [] and len(p3["unscheduled"]) == 6 and p3["free"] == [], "po godzinach: nic się nie mieści, wszystko do przeniesienia")
# szacunek: brak = 30 min, ekstremalne wartości przycięte do 5..240
p4 = al.plan_day(agenda, [T(1, "A"), T(2, "B", mins=1), T(3, "C", mins=9999)], "2026-11-23", at(7), "Europe/Warsaw")
check([b["min"] for b in sorted(p4["blocks"], key=lambda b: b["t"])] == [30, 5, 240], "domyślnie 30 min, minimum 5, maksimum 240")
# jutro: tylko zadania na jutro i to, co dziś się nie zmieściło; urlop (całodniowy, „dostępny”) nie blokuje okien
d_ = tempfile.mkdtemp()
ud.write(d_, "alfred_tasks", tasks, base_ts=0)
ag.refresh(d_, env=env, get=lambda url, **k: ICS, now=at(7))
tv = al.today_view(d_, at(7), env)
tt = [b["title"] for b in tv["plan"]["tomorrow"]["blocks"]]
check(sorted(tt) == ["Drugi duży", "Na jutro"] and tv["plan"]["tomorrow"]["all_day"][0]["title"] == "Urlop Ani", f"jutro: zadanie na jutro + przepełnienie z dziś {tt}")
check(tv["plan"]["tomorrow"]["blocks"][0]["s"] == at(8, 0, 24), "jutro od początku dnia (8:00), całodniowy urlop nie blokuje")

# 2. następny krok
def ns(now, tasks_=tasks, review=False, active=True, hour=None):
    pl = al.plan_day(agenda, tasks_, "2026-11-23", now, "Europe/Warsaw")
    return al.next_step(now, TZ, pl, review, active, hour if hour is not None else datetime.datetime.fromtimestamp(now, TZ).hour)
n = ns(at(7, 30))
check(n["kind"] == "task" and n["title"] == "Telefon do banku" and "08:00-08:20" in n["why"] and n["min"] == 20, f"rano: pierwszy blok {n}")
n = ns(at(8, 45))
check(n["kind"] == "soon" and n["title"] == "Za 15 min: Trening", f"tuż przed wydarzeniem {n}")
n = ns(at(9, 30))
check(n["kind"] == "now_event" and n["title"] == "Teraz: Trening (do 10:00)" and "Potem: Raport (10:00)" in n["why"], f"w trakcie wydarzenia {n}")
n = ns(at(11, 0))
check(n["kind"] == "task" and n["title"] == "Raport" and "11:00-12:30" in n["why"] and "priorytet" in n["why"], f"w ciągu dnia: pierwszy blok od teraz {n}")
n = ns(at(10), tasks_=[])
check(n["kind"] == "plan" and "trzy priorytety" in n["title"], "bez zadań w dzień: wybierz priorytety")
n = ns(at(19, 30), tasks_=[], hour=19)
check(n["kind"] == "review", "po 19:00 bez przeglądu: przegląd dnia ma pierwszeństwo")
n = ns(at(19, 30), tasks_=[], review=True, hour=19)
check(n["kind"] == "rest", "po przeglądzie i bez zadań: plan wykonany")
n = ns(at(19, 30), tasks_=[], active=False, hour=19)
check(n["kind"] == "rest", "kto nie używa planu (brak zadań i nawyków), nie jest zaganiany do przeglądu")
n = ns(at(21), tasks_=[T(1, "Coś")], review=True, hour=21)
check(n["kind"] == "overload" and "1 zadanie" in n["title"], f"po godzinach zostają zadania do przeniesienia {n}")

# 3. alerty
d2 = tempfile.mkdtemp()
NOWA = at(10)
ag.refresh(d2, env=env, get=lambda url, **k: ICS, now=NOWA)
ud.write(d2, "alfred_tasks", [T(i, f"Zaległe {i}", date="2026-11-1" + str(i)) for i in range(3)], base_ts=0)
bb.write_status(d2, {"ts": int(NOWA) - 30 * 60, "mode": "demo", "equity": 1000, "pnl_today": 0, "ok": False, "error": "timeout giełdy", "halted": True, "risk": {"breakers": [{"key": "streak", "label": "seria 4 strat"}]}})
json.dump({"ts": int(NOWA) - 100000, "ok": True, "last_ok_ts": int(NOWA) - 40 * 3600}, open(os.path.join(d2, "backup_status.json"), "w"))
t2 = al.today_view(d2, NOWA, env)
al_ = t2["alerts"]
lv = [a["level"] for a in al_]
check(lv == sorted(lv, key=lambda x: {"bad": 0, "warn": 1, "info": 2}[x]) and al_[0]["level"] == "bad" and "wyłącznik ryzyka (seria 4 strat)" in al_[0]["text"], f"najpilniejsze pierwsze {al_[:2]}")
txt = " | ".join(a["text"] for a in al_)
check("nie raportował od 30 min" in txt and "limit straty" in txt and "timeout giełdy" in txt and "Kopia zapasowa jest sprzed 40 h" in txt and "Zaległe zadania: 3" in txt, f"alerty bota, kopii, zadań: {txt}")
check(any(a["where"] == "ai" and a["level"] == "info" for a in al_) and not any(a["where"] == "agenda" for a in al_), "brak klucza AI = info; kalendarz ok = brak alertu")
open(os.path.join(d2, "ai.env"), "w").write("ANTHROPIC_API_KEY=sk-ant-xxx\n")
check(not any(a["where"] == "ai" for a in al.today_view(d2, NOWA, env)["alerts"]), "klucz AI wpisany: brak alertu")
t3 = al.today_view(tempfile.mkdtemp(), NOWA, {})
check([a["where"] for a in t3["alerts"]] == ["backup", "agenda", "ai"] or {a["where"] for a in t3["alerts"]} >= {"agenda", "ai"}, f"nowa instalacja: tylko podpowiedzi (info) {[a['where'] for a in t3['alerts']]}")
check(all(a["level"] == "info" for a in t3["alerts"]), "nowa instalacja: żadnych alarmów")
ag.refresh(d2, env=env, get=lambda url, **k: (_ for _ in ()).throw(RuntimeError("HTTP 500")), now=NOWA + 7 * 3600)
check(any(a["where"] == "agenda" and a["level"] == "warn" and "HTTP 500" in a["text"] for a in al.today_view(d2, NOWA + 7 * 3600, env)["alerts"]), "kalendarz po awarii: ostrzeżenie z przyczyną")

# 4. tydzień w liczbach
tasks_w = [T(1, "a", done=True, done_ts=int(at(10) * 1000)), T(2, "b", done=True, done_ts=int(at(12) * 1000)), T(3, "c", done=True, done_ts=int(at(10, 0, 21) * 1000)), T(4, "stare", done=True, done_ts=int(at(10, 0, 10) * 1000)), T(5, "otwarte")]
habits = [{"id": "h1", "name": "Ruch", "days": ["2026-11-21", "2026-11-22", "2026-11-23"]}, {"id": "h2", "name": "Czytanie", "days": ["2026-11-23"]}]
w = al.week_stats(tasks_w, habits, "2026-11-23", TZ)
check([x["date"] for x in w["days"]] == [f"2026-11-{i}" for i in range(17, 24)] and w["tasks_done"] == 3 and w["days"][-1]["tasks_done"] == 2 and w["days"][4]["tasks_done"] == 1, f"zrobione zadania dzień po dniu {[x['tasks_done'] for x in w['days']]}")
check(w["habit_rate"] == round(4 / 14 * 100) and w["best_streak"] == 3 and w["days"][-1]["habits_done"] == 2 and w["days"][-1]["habits_total"] == 2, f"nawyki {w['habit_rate']} {w['best_streak']}")
check(al.week_stats([], [], "2026-11-23", TZ)["habit_rate"] is None, "bez nawyków: brak odsetka")

# 5. today(): pełny obraz, oferty i pieniądze razem
d3 = tempfile.mkdtemp()
ag.refresh(d3, env=env, get=lambda url, **k: ICS, now=at(7))
bd_ = [{"t": 17900000000001, "name": "Rower", "status": "kandydat", "buy": 300, "sell": 450}]
ud.write(d3, "produkty_lista", bd_, base_ts=0)
ud.write(d3, "zarobki", {"entries": [{"idea": "naprawy", "kind": "in", "amt": 200, "date": "2026-11-20", "note": "", "t": 1, "ref": ""}], "goal": 1000}, base_ts=0)
bd.transition(d3, 17900000000001, "bought", {"price": 280, "date": "2026-11-18"}, now=at(7), tz_name="Europe/Warsaw")
tv = al.today_view(d3, at(7), env)
check(set(tv) >= {"now", "date", "hour", "plan", "next_step", "alerts", "week", "habits", "review_done", "money", "deals"} and tv["date"] == "2026-11-23" and tv["hour"] == 7, "struktura odpowiedzi")
check(tv["money"] == {"profit_month": -80.0, "goal": 1000} and tv["deals"]["stats"]["invested"] == 280.0, f"pieniądze: koszt zakupu liczy się od razu {tv['money']}")
check(any("Wystawić: Rower" in b["title"] for b in tv["plan"]["tomorrow"]["blocks"]) and any(a["where"] == "deals" and "kupione 5 dni temu" in a["text"] for a in tv["alerts"]), "zakup z potoku trafia do planu na jutro, a stojąca oferta do alertów")
# kontekst dla modelu: te same liczby, nic poufnego
ud.write(d3, "mentor_profile", {"p_cap": "1000", "p_time": "6", "p_risk": "małe", "p_skills": "mam busa", "p_goal": "+1500"}, base_ts=0)
ctx = al.context(d3, at(7), env)
check(ctx["nastepny_krok"].startswith("Wystawić") is False and ctx["plan_dnia"]["podsumowanie_min"]["busy_min"] == 150 and ctx["oferty"]["w_toku"][0]["nazwa"] == "Rower" and ctx["oferty"]["zamrozony_kapital_zl"] == 280.0, f"kontekst: plan, oferty {ctx['nastepny_krok']}")
check(ctx["profil"] == {"kapital_zl": "1000", "wolny_czas_godz_tyg": "6", "ryzyko": "małe", "umiejetnosci": "mam busa", "cel": "+1500"} and any("kupione" in u for u in ctx["uwagi"]), "profil i uwagi w kontekście")
pb = al.plain_briefing(ctx)
check("Najpierw:" in pb and "Uwaga:" in pb, f"briefing zwykły zawiera następny krok i uwagi: {pb[:120]}")
check(len(json.dumps(ctx, ensure_ascii=False)) < 12000, f"kontekst mieści się w budżecie ({len(json.dumps(ctx, ensure_ascii=False))} B)")

# 6. zadania z rozmowy (rola „tasks”)
RAW = 'Oto zadania: {"tasks":[{"text":"  Wystawić   rower na OLX ","minutes":25,"days_from_now":1},{"text":"","minutes":10},{"text":"' + "x" * 200 + '","minutes":9999,"days_from_now":99},{"text":"Zbadać ceny","minutes":"dużo","days_from_now":-3},"śmieć",{"text":"A","minutes":5},{"text":"B"},{"text":"C"},{"text":"D"}]} koniec'
js, dropped = al.tasks_json(RAW, "2026-11-23")
out = json.loads(js)["tasks"]
check(len(out) == 5 and out[0] == {"text": "Wystawić rower na OLX", "min": 25, "date": "2026-11-24"} and len(out[1]["text"]) == 120 and out[1]["min"] == 120 and out[1]["date"] == "2026-11-30", f"normalizacja {out[:2]}")
check(out[2] == {"text": "Zbadać ceny", "min": 30, "date": "2026-11-23"} and out[3]["min"] == 10 and dropped >= 2, "złe wartości: domyślne 30 min i dziś; minimum 10 min; puste i śmieci odrzucone")
check(al.tasks_json("nie umiem", "2026-11-23") is None and al.tasks_json('{"tasks": "x"}', "2026-11-23") is None and json.loads(al.tasks_json('{"tasks": []}', "2026-11-23")[0])["tasks"] == [], "nieczytelna odpowiedź = None, pusta lista jest poprawna")
print("today ok", ok)
