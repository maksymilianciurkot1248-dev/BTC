#!/usr/bin/env python3
"""Alfred: osobisty asystent i plan dnia (strona alfred.html). Ten moduł składa kontekst dnia i robi poranny briefing oraz wieczorne przypomnienie.

Kontekst (context()) to migawka, którą dostaje model AI w roli „alfred”: kalendarz (dziś, jutro, najbliższe dni, wolne okna), zadania, nawyki z seriami,
cele, finanse z notatnika Zarobki, skrót stanu bota i kopii zapasowej oraz ostatnie przeglądy dnia. Nie zawiera kluczy, adresu kalendarza ani opisów
wydarzeń (patrz btc_agenda.py). Dane użytkownika leżą w userdata.json (btc_userdata.py).

Poranny briefing (btc-alfred-morning.timer, 07:00 czasu Europe/Warsaw): jedno zapytanie do modelu, wynik w alfred_briefing.json (widać go na stronie)
i wiadomość na Telegram. Gdy model jest niedostępny (brak klucza, limit, awaria), powstaje briefing zwykły (bez AI) z tych samych danych, więc rano
zawsze coś dostajesz. Wieczorne przypomnienie (20:30) jest krótkie, bez AI i tylko gdy jest o czym przypominać (niedokończone zadania, nawyki, brak przeglądu).
Ustawienia w alfred.env: ALFRED_MORNING=no wyłącza poranne zapytanie do AI, ALFRED_TELEGRAM=no wyłącza Telegram, ALFRED_URL=adres strony (do przypomnienia).

Użycie:  python btc_alfred.py --morning [--force]    briefing teraz (bez --force tylko raz dziennie)
         python btc_alfred.py --evening              wieczorne przypomnienie
         python btc_alfred.py --context              wypisuje kontekst (do diagnostyki; bez sekretów)
"""
import datetime
import json
import os
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_agenda as ag  # noqa: E402
import btc_deals as bd  # noqa: E402
import btc_userdata as ud  # noqa: E402

BRIEFING = "alfred_briefing.json"
DNI = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
MAX_LINES = 40

PROMPTS = {
    "morning": ("Poranny briefing na dziś. Zacznij od najważniejszego. Struktura, zwykły tekst bez Markdownu, do ok. 180 słów: "
                "1) Plan dnia: wydarzenia z kalendarza i wolne okna, w których coś zmieścisz. "
                "2) Trzy priorytety (z moich zadań albo zaproponuj, jeśli ich brakuje), każdy z orientacyjnym czasem. "
                "3) Jeden krok rozwoju na 15-30 minut, dopasowany do moich celów. "
                "4) Jeden krok finansowy (zarobki, mentor, bot), jeśli ma dziś sens. "
                "5) Nawyki do pilnowania dziś. "
                "6) Ostrzeżenia, jeśli coś w kalendarzu, zadaniach, bocie lub kopii zapasowej wymaga uwagi. Pomiń punkty, w których nie masz danych."),
    "tomorrow": ("Zaplanuj jutrzejszy dzień na podstawie kalendarza, zadań i celów: kolejność, wolne okna, trzy priorytety z orientacyjnym czasem i jedno ryzyko, "
                 "które warto zdjąć już dziś. Zwykły tekst bez Markdownu, do ok. 150 słów."),
    "week": ("Zrób krótki przegląd mojego tygodnia na podstawie zadań, nawyków, przeglądów dnia i celów: co się udało, co nie, jeden wzorzec, który widzisz, "
             "i jedna zmiana na przyszły tydzień. Bądź konkretny i uczciwy, bez moralizowania. Zwykły tekst, do ok. 150 słów."),
    "review": ("Pomóż mi zrobić wieczorny przegląd dnia. Podsumuj w dwóch zdaniach, co dziś wynikało z moich zadań i nawyków, a potem zadaj mi trzy krótkie pytania: "
               "co poszło dobrze, czego się nauczyłem, co zrobię jutro jako pierwsze."),
    "tasks": ("Na podstawie naszej rozmowy zaproponuj od 1 do 4 konkretnych zadań na najbliższe dni, które zbliżą mnie do celu. Zwróć WYŁĄCZNIE JSON, bez żadnego tekstu przed ani po: "
              '{"tasks":[{"text":"...","minutes":30,"days_from_now":0}]}. '
              "text: po polsku, do 120 znaków, zaczyna się od czasownika; minutes: 10-120; days_from_now: 0-7. Uwzględnij moje wolne okna z kalendarza i nie dubluj zadań, które już mam."),
    "radar": ("Znajdź w internecie nowe, sprawdzone narzędzia i pomysły (automatyzacja, produktywność, nauka, dochód, handel algorytmiczny), które pasują do mojego planu, "
              "celów i możliwości. Podaj 5: nazwa, co robi, do czego pasuje u mnie, koszt, jak wypróbować w 15 minut, ograniczenia, źródło z datą. "
              "Tylko to, co znalazłeś w wynikach wyszukiwania."),
}


# ----------------------------------------------------------------- pomocnicze
def tzone(d, env=None):
    return ZoneInfo((ag._env(d, env).get("AGENDA_TZ")) or ag.DEFAULT_TZ)


def streak(days, today):
    """Ile dni z rzędu (do dziś; jeśli dziś jeszcze nieodhaczone, do wczoraj) nawyk był wykonany."""
    ds, cur = set(days), datetime.date.fromisoformat(today)
    if today not in ds:
        cur -= datetime.timedelta(days=1)
    n = 0
    while cur.isoformat() in ds:
        n, cur = n + 1, cur - datetime.timedelta(days=1)
    return n


def _val(d, key, default):
    try:
        r = ud.read(d, key)
        return r["value"] if r["found"] else default
    except Exception:  # noqa: BLE001 — brak jednej sekcji nie psuje reszty kontekstu
        return default


def _hm(ts, tz):
    return datetime.datetime.fromtimestamp(ts, tz).strftime("%H:%M")


def _ev_line(e, tz):
    loc = f" ({e['loc'][:40]})" if e.get("loc") else ""
    title = e["title"][:80]
    if e["all_day"]:
        return f"cały dzień: {title}{loc}"
    return f"{_hm(e['s'], tz)}-{_hm(e['e'], tz)} {title}{loc}"


def month_profit(zar, month):
    """(zysk w miesiącu, cel) z notatnika Zarobki."""
    entries = (zar or {}).get("entries") or []
    profit = sum((e["amt"] if e["kind"] == "in" else -e["amt"]) for e in entries if e["date"].startswith(month))
    return round(profit, 2), (zar or {}).get("goal")


# ----------------------------------------------------------------- plan dnia, następny krok, alerty
def day_bounds(d, env=None):
    """Godziny dnia, w których układamy zadania (ALFRED_DAY_START/END w alfred.env, domyślnie 8-20)."""
    e = ag._env(d, env)
    try:
        a, b = int(e.get("ALFRED_DAY_START") or 8), int(e.get("ALFRED_DAY_END") or 20)
    except ValueError:
        return 8, 20
    return (a, b) if 0 <= a < b <= 23 else (8, 20)


def _local_date(ts, tz):
    return datetime.datetime.fromtimestamp(ts, tz).date().isoformat()


def plan_day(agenda, tasks, day, now, tz_name, start_h=8, end_h=20, gap_min=5, min_slot=15, select=None):
    """Układa zadania dnia w wolnych oknach kalendarza (pierwsze pasujące okno, od najważniejszych).
    Zadania dnia: otwarte i (priorytet albo termin nie późniejszy niż dzień, czyli także zaległe) albo wskazane przez `select(zadanie)`. Kolejność: priorytety, potem termin, potem wiek.
    Szacunek czasu `min` (domyślnie 30 min, 5-240). Między blokami zostaje przerwa gap_min. Nic nie zapisuje do kalendarza: to propozycja.
    Zwraca {"timeline", "all_day", "blocks", "unscheduled", "free", "summary"}."""
    tz = ZoneInfo(tz_name)
    today = datetime.datetime.fromtimestamp(now, tz).date().isoformat()
    pick = select or (lambda x: x["top"] or (x["date"] and x["date"] <= day))
    cand = sorted((x for x in tasks if not x["done"] and pick(x)), key=lambda x: (not x["top"], x["date"] or "9999-99-99", x["t"]))
    slots = [list(s) for s in ag.free_slots(agenda, day, tz_name, start_h, end_h, min_slot, now if day == today else None)]
    blocks, un = [], []
    for x in cand:
        m = max(5, min(240, x.get("min") or 30)) * 60
        for sl in slots:
            if sl[1] - sl[0] >= m:
                blocks.append({"kind": "task", "t": x["t"], "title": x["text"], "s": int(sl[0]), "e": int(sl[0] + m), "min": m // 60, "top": x["top"], "late": bool(x["date"] and x["date"] < day), "ref": x.get("ref") or ""})
                sl[0] += m + gap_min * 60
                break
        else:
            un.append({"t": x["t"], "title": x["text"], "min": m // 60, "top": x["top"]})
    blocks.sort(key=lambda b: b["s"])
    free = [(int(a), int(b)) for a, b in slots if b - a >= min_slot * 60]
    d0 = datetime.date.fromisoformat(day)
    lo = datetime.datetime.combine(d0, datetime.time(start_h), tzinfo=tz).timestamp()
    hi = datetime.datetime.combine(d0, datetime.time(end_h), tzinfo=tz).timestamp()
    evs = ag.day_events(agenda, day)
    busy = sum(max(0, min(e["e"], hi) - max(e["s"], lo)) for e in evs if e.get("busy", True) and not e["all_day"])
    timeline = [{"kind": "event", "s": e["s"], "e": e["e"], "title": e["title"], "loc": e["loc"], "busy": e.get("busy", True), "cal": e["cal"]} for e in evs if not e["all_day"]] + blocks
    timeline.sort(key=lambda i: (i["s"], i["kind"] != "event"))
    return {"day": day, "timeline": timeline, "all_day": [{"title": e["title"], "loc": e["loc"]} for e in evs if e["all_day"]], "blocks": blocks, "unscheduled": un, "free": free,
            "summary": {"busy_min": int(busy // 60), "planned_min": sum(b["e"] - b["s"] for b in blocks) // 60, "free_min": sum(b - a for a, b in free) // 60,
                        "need_min": sum(max(5, min(240, x.get("min") or 30)) for x in cand), "overloaded": bool(un)}}


def next_step(now, tz, plan, review_done, active, hour):
    """Jedno zalecenie „co teraz” z planu dnia (reguły po kolei, pierwsza pasująca wygrywa)."""
    hm_ = lambda ts: _hm(ts, tz)
    if hour >= 19 and not review_done and active:
        return {"kind": "review", "title": "Zrób wieczorny przegląd dnia", "why": "Dwie minuty: co poszło, czego się nauczyłeś, co jutro pierwsze."}
    evs = [e for e in plan["timeline"] if e["kind"] == "event" and e["busy"]]
    cur = next((e for e in evs if e["s"] <= now < e["e"]), None)
    first = plan["blocks"][0] if plan["blocks"] else None
    if cur:
        return {"kind": "now_event", "title": f"Teraz: {cur['title']} (do {hm_(cur['e'])})", "why": f"Potem: {first['title']} ({hm_(first['s'])})" if first else "Potem wolne okno."}
    soon = next((e for e in evs if now < e["s"] <= now + 20 * 60), None)
    if soon:
        return {"kind": "soon", "title": f"Za {max(1, round((soon['s'] - now) / 60))} min: {soon['title']}", "why": "Domknij to, co robisz, i przygotuj się."}
    if first:
        why = f"Zaplanowane {hm_(first['s'])}-{hm_(first['e'])} ({first['min']} min)" + (", priorytet" if first["top"] else "") + (", zaległe" if first["late"] else "")
        return {"kind": "task", "title": first["title"], "why": why, "t": first["t"], "min": first["min"], "start": first["s"]}
    if plan["unscheduled"]:
        n = len(plan["unscheduled"])
        return {"kind": "overload", "title": f"Na dziś nie mieści się {n} {'zadanie' if n == 1 else 'zadania' if n < 5 else 'zadań'}", "why": "Skróć je, przenieś na jutro albo zrezygnuj z najmniej ważnego."}
    if hour < 18:
        return {"kind": "plan", "title": "Wybierz trzy priorytety na dziś", "why": "Dzień bez planu rozpływa się w drobiazgach: dodaj zadania z szacunkiem czasu."}
    return {"kind": "rest", "title": "Na dziś plan wykonany", "why": "Możesz zaplanować jutro albo odpocząć."}


def alerts(d, ctx_in, now):
    """Rzeczy wymagające uwagi: [{level: bad|warn|info, where, text}] (bot, kopia, kalendarz, AI, zadania, oferty). Najpierw najpilniejsze."""
    out = []
    try:
        import btc_botcfg as bb
        st = bb.read_status(d) or {}
        if st.get("ts"):
            age = (now - st["ts"]) / 60
            for b in ((st.get("risk") or {}).get("breakers") or []):
                out.append({"level": "bad", "where": "bot", "text": f"Bot: wyłącznik ryzyka ({b.get('label') or b.get('key')}), nowe pozycje wstrzymane"})
            if age > 20:
                out.append({"level": "warn", "where": "bot", "text": f"Bot nie raportował od {round(age)} min (sprawdź usługę btc-trader)"})
            if st.get("halted"):
                out.append({"level": "warn", "where": "bot", "text": "Bot: dzienny limit straty osiągnięty, handel wstrzymany do końca dnia"})
            if st.get("ok") is False and st.get("error"):
                out.append({"level": "warn", "where": "bot", "text": "Bot zgłasza błąd: " + str(st["error"])[:120]})
    except Exception:  # noqa: BLE001
        pass
    try:
        import btc_backup
        b = btc_backup.read_status(d)
        conf = os.path.isfile(btc_backup.config(d, {})["conf"])
        if b.get("last_ok_ts") and now - b["last_ok_ts"] > 36 * 3600:
            out.append({"level": "warn", "where": "backup", "text": f"Kopia zapasowa jest sprzed {round((now - b['last_ok_ts']) / 3600)} h"})
        elif not b.get("ts") and not conf:
            out.append({"level": "info", "where": "backup", "text": "Kopia zapasowa na Dysk Google nie jest podłączona"})
    except Exception:  # noqa: BLE001
        pass
    ag_ = ctx_in["agenda"]
    if not ag_.get("configured"):
        out.append({"level": "info", "where": "agenda", "text": "Kalendarz nie jest podłączony: plan dnia nie zna Twoich wydarzeń"})
    elif not ag_.get("ok") or (ag_.get("ts_ok") and now - ag_["ts_ok"] > 6 * 3600):
        why = "; ".join(f"{c['name']}: {c['error']}" for c in ag_.get("calendars", []) if not c.get("ok")) or "dane są stare"
        out.append({"level": "warn", "where": "agenda", "text": "Kalendarz: " + why[:140]})
    try:
        import btc_ai
        if not btc_ai.key_present(d):
            out.append({"level": "info", "where": "ai", "text": "Brak klucza AI (ai.env): briefing i czat działają w wersji uproszczonej"})
    except Exception:  # noqa: BLE001
        pass
    late = ctx_in["late"]
    if late >= 3:
        out.append({"level": "warn", "where": "tasks", "text": f"Zaległe zadania: {late}. Przenieś na dziś albo usuń to, co już nieaktualne"})
    for a in ctx_in["deals_attention"][:3]:
        out.append({"level": a["level"], "where": "deals", "text": a["text"]})
    return sorted(out, key=lambda a: {"bad": 0, "warn": 1, "info": 2}[a["level"]])


def week_stats(tasks, habits, today, tz):
    """Siedem ostatnich dni w liczbach: zrobione zadania i nawyki dzień po dniu, odsetek wykonania nawyków, najdłuższa seria."""
    d0, days = datetime.date.fromisoformat(today), []
    for i in range(6, -1, -1):
        day = (d0 - datetime.timedelta(days=i)).isoformat()
        days.append({"date": day, "tasks_done": sum(1 for x in tasks if x["done"] and x.get("done_ts") and _local_date(x["done_ts"] / 1000, tz) == day),
                     "habits_done": sum(1 for h in habits if day in h["days"]), "habits_total": len(habits)})
    hd = sum(x["habits_done"] for x in days)
    return {"days": days, "tasks_done": sum(x["tasks_done"] for x in days), "habit_rate": round(hd / (len(habits) * 7) * 100) if habits else None,
            "best_streak": max((streak(h["days"], today) for h in habits), default=0)}


def today_view(d, now=None, env=None):
    """Wszystko, czego potrzebuje ekran „Dziś”: plan dnia i jutra, następny krok, alerty, tydzień, oferty w toku, pieniądze."""
    now = time.time() if now is None else now
    tz = tzone(d, env)
    sh, eh = day_bounds(d, env)
    t = datetime.datetime.fromtimestamp(now, tz)
    td, tm = t.date().isoformat(), (t.date() + datetime.timedelta(days=1)).isoformat()
    agenda = ag.read(d)
    tasks = _val(d, "alfred_tasks", [])
    habits = _val(d, "alfred_habits", {"habits": []})["habits"]
    journal = _val(d, "alfred_journal", [])
    deals = _val(d, "produkty_lista", [])
    zar = _val(d, "zarobki", None)
    p_today = plan_day(agenda, tasks, td, now, tz.key, sh, eh)
    overflow = {u["t"] for u in p_today["unscheduled"]}                                   # co się dziś nie zmieściło (albo dzień już się skończył), idzie na jutro
    plan = {"today": p_today, "tomorrow": plan_day(agenda, tasks, tm, now, tz.key, sh, eh, select=lambda x: x["date"] == tm or x["t"] in overflow)}
    review_done = any(e["date"] == td for e in journal)
    active = bool([x for x in tasks if not x["done"]] or habits or any(x["done"] and x.get("done_ts") and _local_date(x["done_ts"] / 1000, tz) == td for x in tasks))
    att = bd.attention(deals, td, now)
    open_t = [x for x in tasks if not x["done"]]
    profit, goal = month_profit(zar, td[:7])
    hd = sum(1 for h in habits if td in h["days"])
    return {"now": int(now), "date": td, "hour": t.hour, "tz": tz.key, "day_start": sh, "day_end": eh, "plan": plan, "next_step": next_step(now, tz, plan["today"], review_done, active, t.hour),
            "alerts": alerts(d, {"agenda": agenda, "late": sum(1 for x in open_t if x["date"] and x["date"] < td), "deals_attention": att}, now), "week": week_stats(tasks, habits, td, tz),
            "habits": {"done": hd, "total": len(habits)}, "review_done": review_done, "open_tasks": len(open_t), "late_tasks": sum(1 for x in open_t if x["date"] and x["date"] < td),
            "money": {"profit_month": profit if zar else None, "goal": goal}, "deals": {"stats": bd.stats(deals, td), "attention": att}}


def tasks_json(text, today_s):
    """Zadania z odpowiedzi modelu (rola „tasks”): zostają tylko poprawne, znormalizowane (tekst do 120 znaków, 10-120 min, termin 0-7 dni od dziś).
    Zwraca (JSON, liczba odrzuconych) albo None, gdy odpowiedź nie jest czytelnym JSON-em."""
    import json as _json
    a, b = text.find("{"), text.rfind("}")
    try:
        raw = _json.loads(text[a:b + 1]).get("tasks")
        if not isinstance(raw, list):
            return None
    except (ValueError, AttributeError):
        return None
    out, dropped = [], 0
    for x in raw[:12]:
        txt = " ".join(str(x.get("text") or "").split())[:120] if isinstance(x, dict) else ""
        mins, days = (x.get("minutes"), x.get("days_from_now")) if isinstance(x, dict) else (None, None)
        if not txt or len(out) >= 5:
            dropped += 1
            continue
        mins = int(mins) if isinstance(mins, (int, float)) and not isinstance(mins, bool) else 30
        days = int(days) if isinstance(days, (int, float)) and not isinstance(days, bool) else 0
        out.append({"text": txt, "min": max(10, min(120, mins)), "date": (datetime.date.fromisoformat(today_s) + datetime.timedelta(days=max(0, min(7, days)))).isoformat()})
    return _json.dumps({"tasks": out, "dropped": dropped}, ensure_ascii=False), dropped


# ----------------------------------------------------------------- kontekst dla modelu
def context(d, now=None, env=None):
    now = time.time() if now is None else now
    tz = tzone(d, env)
    t = datetime.datetime.fromtimestamp(now, tz)
    today, tomorrow = t.date().isoformat(), (t.date() + datetime.timedelta(days=1)).isoformat()
    out = {"teraz": f"{today} {t:%H:%M}, {DNI[t.weekday()]} ({tz.key})"}

    agenda = ag.read(d)
    if not agenda.get("configured"):
        out["kalendarz"] = {"status": "nieskonfigurowany: brak adresu iCal, nie znam planów z kalendarza"}
    else:
        age_h = round((now - (agenda.get("ts_ok") or 0)) / 3600, 1) if agenda.get("ts_ok") else None
        cal = {"status": ("ok" if agenda.get("ok") else "część kalendarzy niedostępna") + (f", dane sprzed {age_h} h" if age_h is not None else "")}
        evs = agenda.get("events", [])
        for name, day in (("dzis", today), ("jutro", tomorrow)):
            cal[name] = [_ev_line(e, tz) for e in ag.day_events(agenda, day)][:MAX_LINES]
        nxt = []
        for i in range(2, 7):
            day = (t.date() + datetime.timedelta(days=i)).isoformat()
            nxt += [f"{day} {_ev_line(e, tz)}" for e in ag.day_events(agenda, day) if e["day"] == day]
        cal["kolejne_dni"] = nxt[:MAX_LINES]
        slots = lambda day, n=None: [f"{_hm(a, tz)}-{_hm(b, tz)}" for a, b in ag.free_slots({"events": evs}, day, tz.key, now=n)]
        cal["wolne_okna_dzis"], cal["wolne_okna_jutro"] = slots(today, now), slots(tomorrow)
        out["kalendarz"] = cal

    tasks = _val(d, "alfred_tasks", [])
    open_t = sorted((x for x in tasks if not x["done"]), key=lambda x: (not x["top"], x["date"] or "9999-99-99", x["t"]))
    done_today = sum(1 for x in tasks if x["done"] and x.get("done_ts") and datetime.datetime.fromtimestamp(x["done_ts"] / 1000, tz).date().isoformat() == today)
    out["zadania"] = {"otwarte": [{"tekst": x["text"], "termin": x["date"] or None, "priorytet": x["top"]} for x in open_t[:25]], "otwartych_razem": len(open_t),
                      "zaleglych": sum(1 for x in open_t if x["date"] and x["date"] < today), "zrobione_dzis": done_today}

    habits = _val(d, "alfred_habits", {"habits": []})["habits"]
    last7 = {(t.date() - datetime.timedelta(days=i)).isoformat() for i in range(7)}
    out["nawyki"] = [{"nazwa": h["name"], "seria_dni": streak(h["days"], today), "dzis": today in h["days"], "ostatnie_7_dni": len(last7 & set(h["days"]))} for h in habits]

    zar = _val(d, "zarobki", None)
    profit, zgoal = month_profit(zar, today[:7])
    goals = []
    for g in _val(d, "alfred_goals", []):
        if g["kind"] == "zarobki":
            cur, tgt, unit = profit, (g["target"] if g["target"] else zgoal), "zł"
        else:
            cur, tgt, unit = g["current"], g["target"], g["unit"]
        goals.append({"nazwa": g["name"], "postep": (f"{cur:g} z {tgt:g} {unit}".strip() if cur is not None and tgt else "brak danych o postępie"), "termin": g["due"] or None})
    out["cele"] = goals
    if zar and zar.get("entries"):
        by = {}
        for e in zar["entries"]:
            by[e["idea"] or "bez nazwy"] = by.get(e["idea"] or "bez nazwy", 0) + (e["amt"] if e["kind"] == "in" else -e["amt"])
        out["finanse"] = {"zysk_w_tym_miesiacu_zl": profit, "cel_miesieczny_zl": zgoal, "pomysly_zysk_zl": {k: round(v, 2) for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:6]}}

    try:
        import btc_botcfg as bb
        st = bb.read_status(d) or {}
        if st.get("ts"):
            out["bot"] = {"tryb": st.get("mode"), "kapital_usdt": st.get("equity"), "wynik_dzis_usdt": st.get("pnl_today"), "otwarte_pozycje": len(st.get("positions") or []),
                          "ostatni_przebieg_min_temu": round((now - st["ts"]) / 60)}
    except Exception:  # noqa: BLE001
        pass
    try:
        import btc_backup
        b = btc_backup.read_status(d)
        if b.get("last_ok_ts"):
            out["kopia_zapasowa"] = {"ostatnia_udana_h_temu": round((now - b["last_ok_ts"]) / 3600, 1), "ostatnia_proba_ok": b.get("ok")}
    except Exception:  # noqa: BLE001
        pass
    journal = _val(d, "alfred_journal", [])
    if journal:
        out["ostatnie_przeglady_dnia"] = journal[-2:]
    try:                                                         # plan dnia, następny krok i uwagi liczy ten sam kod, który widzisz na stronie
        tv = today_view(d, now, env)
        pd_ = tv["plan"]["today"]
        out["plan_dnia"] = {"propozycja_algorytmu": [f"{_hm(b['s'], tz)}-{_hm(b['e'], tz)} {b['title'][:60]} ({b['min']} min)" for b in pd_["blocks"][:12]],
                            "nie_miesci_sie": [u["title"][:60] for u in pd_["unscheduled"][:8]], "podsumowanie_min": pd_["summary"]}
        out["nastepny_krok"] = f"{tv['next_step']['title']} ({tv['next_step']['why']})"
        out["uwagi"] = [a["text"] for a in tv["alerts"] if a["level"] != "info"][:6]
        out["tydzien"] = {"zrobione_zadania": tv["week"]["tasks_done"], "nawyki_proc": tv["week"]["habit_rate"], "najdluzsza_seria_dni": tv["week"]["best_streak"]}
    except Exception:  # noqa: BLE001
        pass
    deals = _val(d, "produkty_lista", [])
    if deals:
        st = bd.stats(deals, today)
        out["oferty"] = {"w_toku": [{"nazwa": x["name"][:50], "status": x["status"], "kupno_zl": x.get("bought_price"), "cena_wystawienia_zl": x.get("list_price") or x.get("sell"), "od": x.get("d_bought") or None}
                                    for x in deals if x["status"] in ("kupione", "wystawione")][:10],
                         "kandydaci": [x["name"][:50] for x in deals if x["status"] == "kandydat"][:5], "zamrozony_kapital_zl": st["invested"], "oczekiwany_zysk_zl": st["expected_profit"],
                         "zysk_zrealizowany_w_miesiacu_zl": st["realized_month"], "sredni_czas_sprzedazy_dni": st["avg_days_to_sell"]}
    prof = _val(d, "mentor_profile", {})
    if any(prof.values()):
        out["profil"] = {k: v for k, v in (("kapital_zl", prof.get("p_cap")), ("wolny_czas_godz_tyg", prof.get("p_time")), ("ryzyko", prof.get("p_risk")), ("umiejetnosci", prof.get("p_skills")), ("cel", prof.get("p_goal"))) if v}
    return out


# ----------------------------------------------------------------- briefing bez AI i wieczorne przypomnienie
def plain_briefing(ctx):
    """Briefing złożony wprost z danych (gdy model AI jest niedostępny)."""
    L = [f"Dzień dobry. Dziś {ctx['teraz']}."]
    cal = ctx["kalendarz"]
    if "dzis" in cal:
        L.append("Kalendarz dziś: " + ("; ".join(cal["dzis"]) if cal["dzis"] else "brak wydarzeń") + ".")
        if cal.get("wolne_okna_dzis"):
            L.append("Wolne okna: " + ", ".join(cal["wolne_okna_dzis"][:4]) + ".")
    else:
        L.append("Kalendarz nie jest podłączony.")
    pl = (ctx.get("plan_dnia") or {}).get("propozycja_algorytmu")
    if pl:
        L.append("Proponowany plan: " + "; ".join(pl[:5]) + ".")
    if ctx.get("nastepny_krok"):
        L.append("Najpierw: " + ctx["nastepny_krok"] + ".")
    for w in (ctx.get("uwagi") or [])[:2]:
        L.append("Uwaga: " + w + ".")
    z = ctx["zadania"]
    if z["otwarte"]:
        L.append("Zadania (najpierw priorytety): " + "; ".join(x["tekst"] + (" [!]" if x["priorytet"] else "") for x in z["otwarte"][:5]) + f". Otwartych razem: {z['otwartych_razem']}" + (f", zaległych: {z['zaleglych']}" if z["zaleglych"] else "") + ".")
    else:
        L.append("Brak otwartych zadań. Dodaj trzy priorytety na dziś.")
    todo = [h["nazwa"] for h in ctx["nawyki"] if not h["dzis"]]
    if todo:
        L.append("Nawyki na dziś: " + ", ".join(todo) + ".")
    for g in ctx["cele"][:2]:
        L.append(f"Cel „{g['nazwa']}”: {g['postep']}.")
    return "\n".join(L)


def evening_message(ctx, review_done, url=None):
    """Krótkie wieczorne przypomnienie albo None, gdy nie ma o czym przypominać."""
    L = []
    z = ctx["zadania"]
    if z["otwarte"]:
        L.append(f"Otwarte zadania: {z['otwartych_razem']}" + (f", w tym zaległe: {z['zaleglych']}" if z["zaleglych"] else "") + ". Najważniejsze: " + "; ".join(x["tekst"] for x in z["otwarte"][:3]) + ".")
    todo = [h["nazwa"] for h in ctx["nawyki"] if not h["dzis"]]
    if todo:
        L.append("Nawyki do odhaczenia: " + ", ".join(todo) + ".")
    if not review_done and (z["otwarte"] or z["zrobione_dzis"] or ctx["nawyki"]):          # przegląd przypominamy tylko komuś, kto w ogóle używa planu
        L.append("Przegląd dnia czeka (2 minuty).")
    if not L:
        return None
    return "Alfred, wieczór.\n" + "\n".join(L) + (f"\n{url}" if url else "")


# ----------------------------------------------------------------- briefing: zapis i wysyłka
def read_briefing(d):
    try:
        with open(os.path.join(d, BRIEFING), encoding="utf-8") as f:
            b = json.load(f)
        return b if isinstance(b, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_briefing(d, obj):
    p = os.path.join(d, BRIEFING)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)


def send_telegram(text, env=None):
    env = os.environ if env is None else env
    tok, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return False
    try:
        data = json.dumps({"chat_id": chat, "text": text[:3900]}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, headers={"Content-Type": "application/json"}), timeout=10).read()
        return True
    except Exception as e:  # noqa: BLE001 — błąd wysyłki nie zatrzymuje reszty
        print(f"telegram: {type(e).__name__}", flush=True)
        return False


def run_morning(d, now=None, ask_fn=None, send_fn=None, force=False, env=None, get=None):
    """Zwraca {"status": ..., "text": ...}. status: skipped | ai | plain."""
    now = time.time() if now is None else now
    env_a = ag._env(d, env)
    tz = tzone(d, env)
    today = datetime.datetime.fromtimestamp(now, tz).date().isoformat()
    prev = read_briefing(d)
    if not force and prev.get("date") == today and prev.get("kind") in ("ai", "plain"):
        return {"status": "skipped", "text": prev.get("text", "")}
    try:
        ag.refresh(d, env=env, get=get)                          # świeży kalendarz do briefingu
    except Exception:  # noqa: BLE001
        pass
    why = None
    if (env_a.get("ALFRED_MORNING") or "yes").lower() == "no":
        why = "poranne zapytanie do AI wyłączone (ALFRED_MORNING=no)"
    else:
        try:
            if ask_fn is None:
                import btc_ai
                ask_fn = btc_ai.ask
            text, usage = ask_fn(d, [{"role": "user", "content": PROMPTS["morning"]}], role="alfred")
            kind = "ai"
        except Exception as e:  # noqa: BLE001 — brak klucza, limit, sieć: zawsze coś rano dostajesz
            why = str(e)[:120] if isinstance(e, (RuntimeError, ValueError)) else type(e).__name__
    if why is not None:
        text, kind, usage = plain_briefing(context(d, now, env)) + f"\n(Bez AI: {why})", "plain", None
    _write_briefing(d, {"date": today, "ts": int(now), "kind": kind, "text": text, "usage": usage})
    if (env_a.get("ALFRED_TELEGRAM") or "yes").lower() != "no":
        (send_fn or send_telegram)("Alfred: poranny briefing\n" + text)
    return {"status": kind, "text": text}


def run_evening(d, now=None, send_fn=None, env=None):
    now = time.time() if now is None else now
    env_a = ag._env(d, env)
    tz = tzone(d, env)
    today = datetime.datetime.fromtimestamp(now, tz).date().isoformat()
    review_done = any(e["date"] == today for e in _val(d, "alfred_journal", []))
    msg = evening_message(context(d, now, env), review_done, env_a.get("ALFRED_URL") or None)
    if msg and (env_a.get("ALFRED_TELEGRAM") or "yes").lower() != "no":
        (send_fn or send_telegram)(msg)
    return {"status": "sent" if msg else "nothing", "text": msg or ""}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    d = os.environ.get("BTC_BOT_DIR", HERE)
    if "--context" in argv:
        print(json.dumps(context(d), ensure_ascii=False, indent=1))
        return 0
    try:                                                         # klucze Telegram leżą w trader.env
        import btc_trader as bt
        bt.load_env()
    except BaseException as e:  # noqa: BLE001
        print(f"trader.env: {type(e).__name__} (powiadomienia Telegram mogą nie działać)", flush=True)
    if "--morning" in argv:
        r = run_morning(d, force="--force" in argv)
        print(f"briefing: {r['status']}")
        return 0
    if "--evening" in argv:
        r = run_evening(d)
        print(f"przypomnienie: {r['status']}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
