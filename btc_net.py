#!/usr/bin/env python3
"""Wspólna warstwa HTTP: gzip, ponowienia z wykładniczym opóźnieniem (429/5xx, Retry-After), limity równoległości na host."""
import gzip
import json
import random
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import os

UA = "btc-research/1.0 (analiza osobista" + (("; " + os.environ["BTC_UA_CONTACT"]) if os.environ.get("BTC_UA_CONTACT") else "") + ")"
LIMITS = {"stooq.com": 3, "finnhub.io": 1, "api.stlouisfed.org": 4, "publicreporting.cftc.gov": 2,
          "api.coingecko.com": 2, "api.whale-alert.io": 1, "wikimedia.org": 2, "api.twelvedata.com": 1, "api.polygon.io": 1, "www.okx.com": 6}
DEFAULT_LIMIT = 8
_sems, _lock = {}, threading.Lock()
BREAK_AFTER, BREAK_SECONDS = 3, 600          # po 3 porażkach z rzędu host jest pomijany na 10 minut (szybka porażka zamiast kilkuminutowych timeoutów)
_fails, _open_until = {}, {}
_disabled = set()   # dodatkowo (testy); właściwa lista pochodzi z BTC_DISABLE_HOSTS czytanej przy każdym zapytaniu (.env ładuje się po imporcie)


def _disabled_hosts():
    return _disabled | {h.strip().lower() for h in os.environ.get("BTC_DISABLE_HOSTS", "").split(",") if h.strip()}


def _check_open(host):
    dis = _disabled_hosts()
    if host.lower() in dis or any(host.lower().endswith("." + d) for d in dis):
        raise RuntimeError(f"host {host} wyłączony (BTC_DISABLE_HOSTS)")
    with _lock:
        until = _open_until.get(host, 0)
    if until > time.time():
        raise RuntimeError(f"host {host} pomijany po kolejnych błędach (jeszcze {int(until - time.time())} s)")


def _record(host, ok):
    with _lock:
        if ok:
            _fails[host] = 0
            return
        _fails[host] = _fails.get(host, 0) + 1
        if _fails[host] >= BREAK_AFTER:
            _open_until[host] = time.time() + BREAK_SECONDS
            _fails[host] = 0


def trip(host):
    """Zgłoś błąd logiczny hosta (np. HTML zamiast CSV), żeby bezpiecznik mógł go na jakiś czas pominąć."""
    _record(host, False)


def _sem(host):
    with _lock:
        return _sems.setdefault(host, threading.BoundedSemaphore(LIMITS.get(host, DEFAULT_LIMIT)))


def get(url, as_text=False, timeout=12, retries=2, headers=None, data=None):
    host = urlparse(url).hostname or ""
    h = {"User-Agent": UA, "Accept": "application/json,text/csv,*/*", "Accept-Encoding": "gzip"}
    h.update(headers or {})
    raw = b""
    _check_open(host)
    with _sem(host):
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET"), timeout=timeout) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    try:
                        delay = min(float(ra), 20.0)
                    except (TypeError, ValueError):
                        delay = 0.7 * 2 ** attempt + random.random() * 0.3
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < retries:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                _record(host, False)
                raise RuntimeError(f"połączenie: {getattr(e, 'reason', e)}") from None
    _record(host, True)
    text = raw.decode("utf-8", "replace")
    if as_text:
        return text
    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError("niepoprawny JSON") from None


def post_json(url, payload, timeout=12, retries=2):
    """POST z treścią JSON i odpowiedzią JSON (np. Hyperliquid)."""
    return get(url, timeout=timeout, retries=retries, headers={"Content-Type": "application/json"}, data=json.dumps(payload).encode())
