#!/usr/bin/env python3
"""Odsprzedaż jako potok: kandydat → kupione → wystawione → sprzedane (albo odrzucone). Strona Zarabiaj.

Oferta (lista produkty_lista w userdata.json) przechodzi kolejne statusy, a KAŻDA zmiana statusu aktualizuje naraz, jednym atomowym zapisem:
  - ofertę (ceny, daty),
  - wpisy notatnika Zarobki (koszt przy zakupie, przychód i prowizja przy sprzedaży; każdy wpis ma `ref` wskazujący ofertę),
  - zadania Alfreda w planie dnia (po zakupie „Wystawić”, po wystawieniu „Sprawdzić zainteresowanie” za 5 dni; zamknięcie zadań, gdy etap jest zrobiony).
Dzięki temu pieniądze, plan dnia i lista ofert nigdy się nie rozjeżdżają, a cofnięcie statusu sprząta wpisy i zadania, które powstały automatycznie.
Wpisy i zadania dodane ręcznie nie są ruszane (rozpoznajemy tylko własne po `ref`: d<id>b zakup, d<id>s sprzedaż, d<id>f prowizja; zadania: d<id>).
"""
import datetime
import os
import sys
import time
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_agenda as ag  # noqa: E402
import btc_userdata as ud  # noqa: E402

ACTIONS = ("bought", "listed", "sold", "reject", "undo", "delete")
IDEA = "Odsprzedaż"
STALE_BOUGHT_DAYS, STALE_LISTED_DAYS, STALE_CANDIDATE_DAYS = 3, 14, 14
KEYS = ["produkty_lista", "zarobki", "alfred_tasks"]


def _num(v, name, lo=0.0, hi=1e9):
    if v in (None, ""):
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise ValueError(f"{name}: zła liczba")
    return v


def _day(v, today):
    if v in (None, ""):
        return today
    try:
        datetime.date.fromisoformat(str(v))
    except ValueError:
        raise ValueError("zła data") from None
    return str(v)


def _plus(day, n):
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=n)).isoformat()


def _days_between(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


class _Ids:
    """Unikalne id (ms × 10 + licznik), niekolidujące z istniejącymi."""

    def __init__(self, items, now_ms):
        self.used, self.nxt = {x["t"] for x in items}, now_ms * 10

    def new(self):
        while self.nxt in self.used:
            self.nxt += 1
        self.used.add(self.nxt)
        self.nxt += 1
        return self.nxt - 1


def transition(d, deal_id, action, payload=None, now=None, tz_name=None):
    """Zmiana statusu oferty. Zwraca {"deal": oferta lub None, "ts": {klucz: ts}, "values": {klucz: wartość}} (tylko zmienione klucze).
    ValueError przy niedozwolonym przejściu lub złych danych: wtedy nic się nie zapisuje."""
    if action not in ACTIONS:
        raise ValueError("nieznana akcja")
    p = payload if isinstance(payload, dict) else {}
    now = time.time() if now is None else now
    tz = ZoneInfo(tz_name or ag._env(d).get("AGENDA_TZ") or ag.DEFAULT_TZ)
    today = datetime.datetime.fromtimestamp(now, tz).date().isoformat()
    now_ms = int(now * 1000)
    out = {}

    def fn(v):
        deals, zar, tasks = v["produkty_lista"], v["zarobki"], v["alfred_tasks"]
        deal = next((x for x in deals if x["t"] == deal_id), None)
        if deal is None:
            raise ValueError("nie ma takiej oferty")
        t, name, st = deal["t"], deal["name"], deal["status"]
        ref_task = f"d{t}"
        eids, tids = _Ids(zar["entries"], now_ms), _Ids(tasks, now_ms)

        def need(ok, msg):
            if not ok:
                raise ValueError(msg)

        def drop_entries(*refs):
            zar["entries"] = [e for e in zar["entries"] if e.get("ref") not in refs]

        def add_entry(kind, amt, note, ref, date):
            if amt > 0:
                zar["entries"].append({"idea": IDEA, "kind": kind, "amt": round(amt, 2), "date": date, "note": note[:100], "t": eids.new(), "ref": ref})

        def close_tasks():
            for x in tasks:
                if x.get("ref") == ref_task and not x["done"]:
                    x["done"], x["done_ts"] = True, now_ms

        def add_task(text, date, minutes):
            tasks.append({"t": tids.new(), "text": text[:200], "date": date, "done": False, "top": False, "done_ts": None, "min": minutes, "ref": ref_task})

        if action == "bought":
            need(st == "kandydat", f"kupić można tylko kandydata (teraz: {st})")
            price = _num(p.get("price"), "price")
            price = deal["buy"] if price is None else price
            need(price is not None, "podaj cenę kupna")
            costs = _num(p.get("costs"), "costs")
            costs = (deal.get("costs") or 0) if costs is None else costs
            date = _day(p.get("date"), today)
            deal.update(status="kupione", bought_price=price, costs=costs, d_bought=date)
            drop_entries(f"d{t}b")
            add_entry("out", price + costs, f"kupno: {name}" + (f" (+ koszty {costs:g})" if costs else ""), f"d{t}b", date)
            add_task(f"Wystawić: {name}", _plus(today, 1), 20)
        elif action == "listed":
            need(st == "kupione", f"wystawić można tylko kupione (teraz: {st})")
            price = _num(p.get("price"), "price")
            price = (deal.get("list_price") or deal.get("sell")) if price is None else price
            need(price is not None, "podaj cenę wystawienia")
            deal.update(status="wystawione", list_price=price, d_listed=_day(p.get("date"), today))
            close_tasks()
            add_task(f"Sprawdzić zainteresowanie: {name}", _plus(today, 5), 10)
        elif action == "sold":
            need(st in ("wystawione", "kupione"), f"sprzedać można wystawione albo kupione (teraz: {st})")
            price = _num(p.get("price"), "price")
            price = (deal.get("list_price") or deal.get("sell")) if price is None else price
            need(price is not None, "podaj cenę sprzedaży")
            fee = _num(p.get("fee"), "fee")
            fee = round(price * (deal.get("fee") or 0) / 100, 2) if fee is None else fee
            date = _day(p.get("date"), today)
            deal.update(status="sprzedane", sold_price=price, fee_paid=fee, d_sold=date)
            drop_entries(f"d{t}s", f"d{t}f")
            add_entry("in", price, f"sprzedaż: {name}", f"d{t}s", date)
            add_entry("out", fee, f"prowizja: {name}", f"d{t}f", date)
            close_tasks()
        elif action == "reject":
            need(st == "kandydat", f"odrzucić można tylko kandydata (teraz: {st})")
            deal["status"] = "odrzucone"
            close_tasks()
        elif action == "undo":
            if st == "sprzedane":
                drop_entries(f"d{t}s", f"d{t}f")
                deal.update(status="wystawione" if deal.get("d_listed") else "kupione", sold_price=None, fee_paid=None, d_sold="")
            elif st == "wystawione":
                deal.update(status="kupione", list_price=None, d_listed="")
            elif st == "kupione":
                drop_entries(f"d{t}b")
                tasks[:] = [x for x in tasks if x.get("ref") != ref_task]
                deal.update(status="kandydat", bought_price=None, d_bought="")
            elif st == "odrzucone":
                deal["status"] = "kandydat"
            else:
                raise ValueError("nie ma czego cofać")
        else:                                                      # delete
            drop_entries(f"d{t}b", f"d{t}s", f"d{t}f")
            tasks[:] = [x for x in tasks if x.get("ref") != ref_task]
            deals.remove(deal)
        out["deal"] = None if action == "delete" else deal
        return {"produkty_lista": deals, "zarobki": zar, "alfred_tasks": tasks}

    ts, vals = ud.mutate(d, KEYS, fn, now_ms=now_ms)
    after = next((x for x in vals["produkty_lista"] if x["t"] == deal_id), None) if out.get("deal") is not None else None
    return {"deal": after, "ts": ts, "values": {k: vals[k] for k in ts}}


def realized(x):
    """Zysk sprzedanej oferty po wszystkich kosztach (None, gdy jeszcze nie sprzedana)."""
    if x["status"] != "sprzedane" or x.get("sold_price") is None:
        return None
    return round(x["sold_price"] - (x.get("bought_price") or 0) - (x.get("costs") or 0) - (x.get("fee_paid") or 0), 2)


def stats(deals, today):
    """Liczby do podsumowania: ile w którym etapie, ile pieniędzy zamrożone, oczekiwany i zrealizowany zysk, średni czas do sprzedaży."""
    counts = {s: 0 for s in ud.STATUSES}
    invested = expected = month = total = 0.0
    days = []
    for x in deals:
        counts[x["status"]] += 1
        cost = (x.get("bought_price") or 0) + (x.get("costs") or 0)
        if x["status"] in ("kupione", "wystawione"):
            invested += cost
            tgt = x.get("list_price") or x.get("sell")
            if tgt is not None:
                expected += tgt - cost - tgt * (x.get("fee") or 0) / 100
        r = realized(x)
        if r is not None:
            total += r
            if (x.get("d_sold") or "").startswith(today[:7]):
                month += r
            if x.get("d_bought") and x.get("d_sold"):
                days.append(max(0, _days_between(x["d_bought"], x["d_sold"])))
    return {"counts": counts, "invested": round(invested, 2), "expected_profit": round(expected, 2), "realized_month": round(month, 2), "realized_total": round(total, 2),
            "sold_n": counts["sprzedane"], "avg_days_to_sell": round(sum(days) / len(days), 1) if days else None}


def attention(deals, today, now):
    """Oferty, które stoją za długo: [{level, text, t, status, days}], najpierw pilniejsze."""
    out = []
    for x in deals:
        st = x["status"]
        if st == "kupione" and x.get("d_bought"):
            n = _days_between(x["d_bought"], today)
            if n >= STALE_BOUGHT_DAYS:
                out.append({"level": "warn", "text": f"{x['name']}: kupione {n} dni temu, a jeszcze niewystawione", "t": x["t"], "status": st, "days": n})
        elif st == "wystawione" and x.get("d_listed"):
            n = _days_between(x["d_listed"], today)
            if n >= STALE_LISTED_DAYS:
                out.append({"level": "warn", "text": f"{x['name']}: wystawione {n} dni bez sprzedaży, rozważ obniżenie ceny", "t": x["t"], "status": st, "days": n})
        elif st == "kandydat" and x["t"] > 1e12:                       # id to czas utworzenia: ms albo ms × 10 (patrz _Ids i strona)
            n = int((now * 1000 - (x["t"] / 10 if x["t"] > 5e12 else x["t"])) // 86400000)
            if n >= STALE_CANDIDATE_DAYS:
                out.append({"level": "info", "text": f"{x['name']}: kandydat od {n} dni, kup albo odrzuć", "t": x["t"], "status": st, "days": n})
    return sorted(out, key=lambda a: (a["level"] != "warn", -a["days"]))
