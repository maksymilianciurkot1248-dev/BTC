#!/usr/bin/env python3
"""Kalendarz (Google i każdy inny z adresem iCal) tylko do ODCZYTU, odświeżany co 15 minut (btc-agenda.timer).

Skąd dane: „tajny adres w formacie iCal” kalendarza Google (Kalendarz Google → Ustawienia → wybierz kalendarz → Integrowanie kalendarza).
Adres jest SEKRETEM (kto go ma, czyta cały kalendarz): wpisujesz go sam na serwerze do /opt/btc/alfred.env, a program nigdy go nie zapisuje
w plikach stanu, nie pokazuje w panelu ani nie wysyła do modelu. Kalendarz można w każdej chwili „zresetować” w Google, co unieważnia adres.
Plik alfred.env:  AGENDA_ICS_URLS=Praca|https://...,Dom|https://...   (nazwa| jest opcjonalna; najwyżej 5 kalendarzy; tylko https)

Do pliku agenda.json trafiają: tytuł, czas, miejsce i nazwa kalendarza wydarzenia. Opisów i uczestników celowo nie czytamy: to treści od osób trzecich
(zaproszenie może zawierać dowolny tekst), więc nie powinny trafiać do modelu AI. Anulowane wydarzenia są pomijane; powtarzające się,
wyjątki (EXDATE) i przeniesione terminy rozwija biblioteka recurring-ical-events. Okno: od wczoraj do 15 dni naprzód.
Google nie podaje, jak szybko adres iCal odzwierciedla zmiany: nowe wydarzenie może pojawić się z opóźnieniem (minuty do godzin).

Użycie:  python btc_agenda.py            odświeża agenda.json (usługa btc-agenda)
         python btc_agenda.py --check    odświeża i wypisuje liczbę wydarzeń na kalendarz (bez adresów)
"""
import datetime
import hashlib
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FILE = "agenda.json"
ENV_FILE = "alfred.env"
MAX_CALENDARS = 5
MAX_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 400
BACK_DAYS, AHEAD_DAYS = 1, 15
DEFAULT_TZ = "Europe/Warsaw"
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/[^\s]{1,560}$")
NAME_RE = re.compile(r"^[^|\x00-\x1f]{1,30}$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


class AgendaError(Exception):
    pass


def load_env_file(d):
    """KLUCZ=wartość z alfred.env (dla ręcznych uruchomień; usługa dostaje te same zmienne od systemd)."""
    out = {}
    try:
        with open(os.path.join(d, ENV_FILE), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _env(d, env=None):
    """Ustawienia: podany słownik (testy) albo zmienne środowiska nałożone na alfred.env."""
    if env is not None:
        return env
    return {**load_env_file(d), **{k: v for k, v in os.environ.items() if k.startswith(("AGENDA_", "ALFRED_"))}}


def config(d, env=None):
    """[(nazwa, adres)] z AGENDA_ICS_URLS. Zły wpis to błąd (nie zgadujemy), ale komunikat nigdy nie zawiera adresu."""
    raw = (_env(d, env).get("AGENDA_ICS_URLS") or "").strip()
    if not raw:
        return []
    out = []
    for i, part in enumerate([p.strip() for p in raw.split(",") if p.strip()], 1):
        name, url = (part.split("|", 1) if "|" in part and not part.startswith("http") else (f"Kalendarz {i}", part))
        name, url = name.strip(), url.strip()
        if not URL_RE.match(url):
            raise AgendaError(f"AGENDA_ICS_URLS: wpis nr {i} nie wygląda na adres https (sprawdź alfred.env)")
        if not NAME_RE.match(name):
            raise AgendaError(f"AGENDA_ICS_URLS: wpis nr {i} ma zbyt długą lub złą nazwę (do 30 znaków, bez znaku |)")
        out.append((name, url))
    if len(out) > MAX_CALENDARS:
        raise AgendaError(f"AGENDA_ICS_URLS: najwyżej {MAX_CALENDARS} kalendarzy")
    return out


def _clean(s, n):
    return CTRL_RE.sub(" ", str(s or "")).strip()[:n]


def _epoch(v, tz):
    """date/datetime (także bez strefy) -> (epoka, czy całodniowe)."""
    if isinstance(v, datetime.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=tz)                         # czas „pływający”: w strefie użytkownika
        return v.timestamp(), False
    return datetime.datetime.combine(v, datetime.time.min, tzinfo=tz).timestamp(), True


def parse_events(ics_text, cal_name, start, end, tz):
    """Wydarzenia z jednego kalendarza w oknie [start, end): lista słowników posortowana po początku."""
    import icalendar
    import recurring_ical_events
    cal = icalendar.Calendar.from_ical(ics_text)
    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS") or "").upper() == "CANCELLED":
            continue
        ds = ev.get("DTSTART")
        if ds is None:
            continue
        s, all_day = _epoch(ds.dt, tz)
        de = ev.get("DTEND")
        if de is not None:
            e, _ = _epoch(de.dt, tz)
        elif ev.get("DURATION") is not None:
            e = s + ev["DURATION"].dt.total_seconds()
        else:
            e = s + (86400 if all_day else 0)
        if e < s:
            e = s
        sd = datetime.datetime.fromtimestamp(s, tz).date()
        ed = datetime.datetime.fromtimestamp(max(s, e - 1) if all_day else e, tz).date()
        out.append({"id": hashlib.sha1(f"{ev.get('UID')}|{int(s)}".encode()).hexdigest()[:12], "title": _clean(ev.get("SUMMARY"), 120) or "(bez tytułu)",
                    "s": int(s), "e": int(e), "all_day": all_day, "day": sd.isoformat(), "end_day": ed.isoformat(), "loc": _clean(ev.get("LOCATION"), 100),
                    "busy": str(ev.get("TRANSP") or "OPAQUE").upper() != "TRANSPARENT", "cal": cal_name})
    out.sort(key=lambda x: (x["s"], x["title"]))
    return out


def read(d):
    try:
        with open(os.path.join(d, FILE), encoding="utf-8") as f:
            a = json.load(f)
        return a if isinstance(a, dict) else {"ts": 0, "events": []}
    except (OSError, ValueError):
        return {"ts": 0, "events": [], "configured": False, "ok": False}


def _write(d, obj):
    p = os.path.join(d, FILE)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)


def fetch(url, get=None):
    """Treść iCal. Komunikaty błędów nie zawierają adresu (to sekret)."""
    if get is None:
        import btc_net
        get = btc_net.get
    try:
        text = get(url, as_text=True, timeout=25, retries=2, headers={"Accept": "text/calendar,*/*"})
    except RuntimeError as e:
        raise AgendaError(re.sub(r"https?://\S+", "[adres]", str(e))[:120]) from None
    if len(text) > MAX_BYTES:
        raise AgendaError("plik kalendarza jest zbyt duży")
    if "BEGIN:VCALENDAR" not in text[:4096]:
        raise AgendaError("odpowiedź nie jest kalendarzem iCal (zły lub unieważniony adres?)")
    return text


def refresh(d, env=None, get=None, now=None):
    """Pobiera wszystkie kalendarze i zapisuje agenda.json. Błąd jednego kalendarza nie kasuje danych pozostałych;
    gdy zawiodą wszystkie, zostają poprzednie wydarzenia (oznaczone jako stare). Zwraca zapisany słownik."""
    now = time.time() if now is None else now
    env = _env(d, env)
    tzname = env.get("AGENDA_TZ") or DEFAULT_TZ
    try:
        tz = ZoneInfo(tzname)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo(DEFAULT_TZ)
    prev = read(d)
    try:
        cals = config(d, env)
    except AgendaError as e:
        out = {**prev, "ts": int(now), "ok": False, "configured": True, "error": str(e)}
        _write(d, out)
        return out
    if not cals:
        out = {"ts": int(now), "ok": False, "configured": False, "events": [], "calendars": [], "tz": tzname}
        _write(d, out)
        return out
    today = datetime.datetime.fromtimestamp(now, tz).date()
    start = datetime.datetime.combine(today - datetime.timedelta(days=BACK_DAYS), datetime.time.min, tzinfo=tz)
    end = datetime.datetime.combine(today + datetime.timedelta(days=AHEAD_DAYS), datetime.time.min, tzinfo=tz)
    events, status = [], []
    for name, url in cals:
        try:
            evs = parse_events(fetch(url, get), name, start, end, tz)
            events += evs
            status.append({"name": name, "ok": True, "n": len(evs), "error": None})
        except AgendaError as e:
            status.append({"name": name, "ok": False, "n": 0, "error": str(e)})
        except Exception as e:  # noqa: BLE001 — uszkodzony plik iCal nie może zatrzymać usługi
            status.append({"name": name, "ok": False, "n": 0, "error": f"nie udało się odczytać kalendarza ({type(e).__name__})"})
    events.sort(key=lambda x: (x["s"], x["title"]))
    if any(x["ok"] for x in status):
        failed = {x["name"] for x in status if not x["ok"]}
        keep = [e for e in prev.get("events", []) if e.get("cal") in failed]       # nieudany kalendarz zachowuje poprzednie wydarzenia
        events = sorted(events + keep, key=lambda x: (x["s"], x["title"]))[:MAX_EVENTS]
        out = {"ts": int(now), "ts_ok": int(now), "ok": all(x["ok"] for x in status), "configured": True, "tz": tzname, "calendars": status, "events": events, "window": [int(start.timestamp()), int(end.timestamp())]}
    else:
        out = {**prev, "ts": int(now), "ok": False, "configured": True, "tz": tzname, "calendars": status}
    _write(d, out)
    return out


def day_events(agenda, day, include_free=True):
    """Wydarzenia dnia (YYYY-MM-DD): także te trwające kilka dni, które obejmują ten dzień."""
    return [e for e in agenda.get("events", []) if e["day"] <= day <= e["end_day"] and (include_free or e.get("busy", True))]


def free_slots(agenda, day, tz_name=DEFAULT_TZ, start_h=8, end_h=20, min_min=45, now=None):
    """Wolne okna dnia (epoka początku i końca) między start_h a end_h, minimum min_min minut; zajęte są tylko wydarzenia „busy”, całodniowe nie blokują."""
    tz = ZoneInfo(tz_name)
    d0 = datetime.date.fromisoformat(day)
    lo = datetime.datetime.combine(d0, datetime.time(start_h), tzinfo=tz).timestamp()
    hi = datetime.datetime.combine(d0, datetime.time(end_h), tzinfo=tz).timestamp()
    if now is not None:
        lo = max(lo, now)
    busy = sorted((max(e["s"], lo), min(e["e"], hi)) for e in day_events(agenda, day, include_free=False) if not e["all_day"] and e["e"] > lo and e["s"] < hi)
    out, cur = [], lo
    for s, e in busy:
        if s - cur >= min_min * 60:
            out.append((int(cur), int(s)))
        cur = max(cur, e)
    if hi - cur >= min_min * 60:
        out.append((int(cur), int(hi)))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    d = os.environ.get("BTC_BOT_DIR", HERE)
    a = refresh(d)
    if not a.get("configured"):
        print("kalendarz nie jest skonfigurowany (AGENDA_ICS_URLS w alfred.env)")
        return 0
    if a.get("error") and not a.get("calendars"):
        print("BŁĄD:", a["error"])
        return 1
    for c in a.get("calendars", []):
        print(f"{c['name']}: " + (f"{c['n']} wydarzeń" if c["ok"] else f"BŁĄD: {c['error']}"))
    return 0 if a.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
