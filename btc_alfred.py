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
