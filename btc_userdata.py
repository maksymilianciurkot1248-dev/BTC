#!/usr/bin/env python3
"""Dane użytkownika zapisywane na serwerze, żeby nie ginęły z przeglądarką i były widoczne na każdym urządzeniu (telefon, komputer).

Trzy klucze (lista zamknięta, nic innego strona nie zapisze):
  zarobki         notatnik przychodów i kosztów (strona Zarobki)
  produkty_lista  lista kandydatów do odsprzedaży (strona Produkty)
  mentor_profile  profil dla mentora (strona Mentor)
Plik userdata.json leży w katalogu bota i wchodzi do kopii zapasowej (btc_backup.py). Nie zawiera kluczy ani haseł.

Konflikty: każdy zapis ma znacznik czasu (ts, ms). Klient przysyła base_ts, czyli ts wersji, którą wczytał. Gdy na serwerze jest nowsza wersja
(zapis z innego urządzenia), zapis jest odrzucany (Conflict) i klient dostaje wersję z serwera. Ostatni zapis nie nadpisuje po cichu cudzego.
Dane są oczyszczane do znanych pól i rozmiarów: nie da się tu zapisać dowolnej treści.
"""
import datetime
import json
import math
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_botcfg as bb  # noqa: E402  (zapis atomowy z prawami 640)

FILE = "userdata.json"
MAX_BODY = 300 * 1024
LIMITS = {"zarobki": 256 * 1024, "produkty_lista": 128 * 1024, "mentor_profile": 16 * 1024}
_lock = threading.Lock()


class Conflict(Exception):
    """Na serwerze jest nowsza wersja niż ta, na której oparł się klient."""

    def __init__(self, ts, value):
        super().__init__("conflict")
        self.ts, self.value = ts, value


def _num(v, name, lo=0.0, hi=1e9, none_ok=False):
    if v is None or v == "":
        if none_ok:
            return None
        raise ValueError(f"{name}: wymagana liczba")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{name}: liczba spoza zakresu")
    return round(float(v), 4) if isinstance(v, float) else v


def _str(v, name, n):
    if v is None:
        return ""
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ValueError(f"{name}: oczekiwano tekstu")
    return str(v).strip()[:n]


def _clean_zarobki(v):
    if not isinstance(v, dict) or not isinstance(v.get("entries"), list) or len(v["entries"]) > 5000:
        raise ValueError("zarobki: oczekiwano {entries: [...], goal}")
    out = []
    for e in v["entries"]:
        if not isinstance(e, dict) or e.get("kind") not in ("in", "out"):
            raise ValueError("zarobki: zły wpis")
        date = _str(e.get("date"), "date", 10)
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            raise ValueError("zarobki: zła data") from None
        out.append({"idea": _str(e.get("idea"), "idea", 60), "kind": e["kind"], "amt": _num(e.get("amt"), "amt", 0.0, 1e9), "date": date,
                    "note": _str(e.get("note"), "note", 100), "t": int(_num(e.get("t"), "t", 0, 1e15))})
    return {"entries": out, "goal": _num(v.get("goal"), "goal", 0.0, 1e9, none_ok=True)}


def _clean_lista(v):
    if not isinstance(v, list) or len(v) > 500:
        raise ValueError("produkty_lista: oczekiwano listy do 500 pozycji")
    out = []
    for x in v:
        if not isinstance(x, dict):
            raise ValueError("produkty_lista: zła pozycja")
        out.append({"name": _str(x.get("name"), "name", 100), "buy": _num(x.get("buy"), "buy", -1e9, 1e9, True), "sell": _num(x.get("sell"), "sell", -1e9, 1e9, True),
                    "profit": _num(x.get("profit"), "profit", -1e9, 1e9, True), "roi": _num(x.get("roi"), "roi", -1e9, 1e9, True), "hr": _num(x.get("hr"), "hr", -1e9, 1e9, True),
                    "t": int(_num(x.get("t"), "t", 0, 1e15))})
    return out


def _clean_profile(v):
    if not isinstance(v, dict):
        raise ValueError("mentor_profile: oczekiwano obiektu")
    return {k: _str(v.get(k), k, 500) for k in ("p_cap", "p_time", "p_risk", "p_skills", "p_goal")}


CLEAN = {"zarobki": _clean_zarobki, "produkty_lista": _clean_lista, "mentor_profile": _clean_profile}


def _path(d):
    return os.path.join(d, FILE)


def _load(d):
    """Zawartość pliku. Uszkodzony plik jest odkładany obok (nie kasujemy cudzych danych) i traktowany jak pusty."""
    p = _path(d)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("keys"), dict):
            return data
        raise ValueError("zła struktura")
    except FileNotFoundError:
        return {"v": 1, "keys": {}}
    except (OSError, ValueError):
        try:
            os.replace(p, p + f".bad-{int(time.time())}")
        except OSError:
            pass
        return {"v": 1, "keys": {}}


def read(d, key):
    if key not in LIMITS:
        raise ValueError("nieznany klucz")
    cur = _load(d)["keys"].get(key)
    return {"found": True, "value": cur["v"], "ts": cur["ts"]} if isinstance(cur, dict) and "v" in cur else {"found": False, "value": None, "ts": 0}


def write(d, key, value, base_ts=None, now_ms=None):
    """Zapisuje wartość klucza i zwraca nowy ts. Conflict, gdy serwer ma nowszą wersję niż base_ts."""
    if key not in LIMITS:
        raise ValueError("nieznany klucz")
    clean = CLEAN[key](value)
    if len(json.dumps(clean, ensure_ascii=False, separators=(",", ":"))) > LIMITS[key]:
        raise ValueError("dane za duże")
    base = 0 if base_ts in (None, "") else _num(base_ts, "base_ts", 0, 1e16)
    with _lock:
        data = _load(d)
        cur = data["keys"].get(key)
        if isinstance(cur, dict) and cur.get("ts", 0) > base:
            raise Conflict(cur["ts"], cur["v"])
        ts = max(int(now_ms if now_ms is not None else time.time() * 1000), (cur or {}).get("ts", 0) + 1)
        data["keys"][key] = {"v": clean, "ts": ts}
        bb._atomic_write(_path(d), json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return ts
