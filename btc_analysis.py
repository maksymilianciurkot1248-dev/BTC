#!/usr/bin/env python3
"""Rozbudowana analiza BTC na danych z CoinGecko (oficjalne SDK: coingecko_sdk).

Każda sekcja raportu korzysta z innego endpointu API:
  simple.price            cena spot w kilku walutach
  coins.markets           ranking, kapitalizacja, ATH, zmiany % w wielu horyzontach
  coins.get_id            fundamenty, sentyment, aktywność deweloperska
  coins.market_chart      dzienna historia: SMA, MACD, RSI, Bollinger, zmienność, VaR
  coins.ohlc              świece: ATR, zakres 30 dni
  global_                 dominacja BTC i kapitalizacja całego rynku
  derivatives             kontrakty perpetual BTC: open interest, funding rate
  coins.tickers           płynność na giełdach, spready, rozbieżność cen
  public_treasury         ile BTC trzymają spółki publiczne i rządy
  search.trending         co jest teraz na topie
  market_chart (2. aktyw) korelacja i beta (domyślnie ETH)
  newsdata.io /1/latest   nagłówki newsów o BTC (+ /1/crypto), ocena nagłówków
  Bybit API v5 (odczyt)   konto, pozycje, wyniki z 30 dni, funding/OI/long-short BTCUSDT

Instalacja:
    pip install coingecko_sdk

Konfiguracja (klucze TYLKO ze zmiennych środowiskowych albo z pliku .env — nigdy w kodzie;
skopiuj .env.example jako .env i wpisz swoje klucze):
    export COINGECKO_API_KEY="twój_klucz"
    export COINGECKO_ENV="demo"      # "demo" (domyślnie) albo "pro"
    export NEWSDATA_API_KEY="twój_klucz_newsdata"   # opcjonalnie: sekcja newsów
    export BYBIT_API_KEY="..." BYBIT_API_SECRET="..."  # opcjonalnie: klucz Bybit TYLKO DO ODCZYTU
    export BYBIT_PRIVACY="masked"    # masked (domyślnie: bez kwot) albo full (pełne dane konta)
    # opcjonalnie: BYBIT_BASE_URL (np. https://api.bybit.eu), BYBIT_SYMBOL, BYBIT_ACCOUNT_TYPE

Użycie:
    python btc_analysis.py
    python btc_analysis.py --days 365 --vs pln --compare solana
    python btc_analysis.py --csv btc.csv --json btc_raport.json
    python btc_analysis.py --news latest --news-query "bitcoin OR BTC"   # lub: both | crypto | off
    python btc_analysis.py --quiet                                       # raport dla strony www (cron)
    python btc_analysis.py --print --bybit-privacy full                  # pełny raport w konsoli z kwotami Bybit

Uwaga: plan Demo ma limit zapytań (kilkanaście wywołań na jedno uruchomienie
mieści się w limicie) oraz historię ograniczoną do 365 dni. Sekcje niedostępne
w Twoim planie są pomijane z ostrzeżeniem, reszta raportu działa dalej.
"""
import argparse
import base64
import csv
import hashlib
import hmac
import bisect
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone

try:
    import btc_extras  # dodatkowe dane z darmowych API bez kluczy (opcjonalne)
except ImportError:
    btc_extras = None

try:
    import coingecko_sdk
    from coingecko_sdk import Coingecko
except ImportError:  # pozwala uruchamiać logikę analizy bez SDK (np. w testach)
    coingecko_sdk = None
    Coingecko = None

# Pusta krotka w "except" nic nie łapie — bezpieczne, gdy SDK nie jest zainstalowane.
RATE_LIMIT_ERROR = coingecko_sdk.RateLimitError if coingecko_sdk else ()
API_ERROR = coingecko_sdk.APIError if coingecko_sdk else ()


# ==========================================================================
# Konfiguracja: plik .env (bez dodatkowych zależności)
# ==========================================================================
DOTENV_STATUS = {"searched": [], "loaded": []}  # do czytelnego komunikatu o błędzie


def load_dotenv(paths=None):
    """Wczytuje KEY=VALUE z pliku .env (bieżący katalog, potem katalog skryptu).

    Zmienne ustawione już w terminalu mają pierwszeństwo przed plikiem."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = paths or [os.path.join(os.getcwd(), ".env"), os.path.join(here, ".env")]
    for path in dict.fromkeys(candidates):
        DOTENV_STATUS["searched"].append(path)
        if not os.path.isfile(path):
            continue
        DOTENV_STATUS["loaded"].append(path)
        with open(path, encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key and not os.environ.get(key):
                    os.environ[key] = value


# ==========================================================================
# Klient i pobieranie danych
# ==========================================================================
def make_client():
    if Coingecko is None:
        sys.exit("Brak biblioteki. Zainstaluj: pip install coingecko_sdk")
    api_key = os.environ.get("COINGECKO_API_KEY")
    if not api_key:
        searched = "\n  ".join(DOTENV_STATUS["searched"]) or "(load_dotenv nie został uruchomiony)"
        loaded = DOTENV_STATUS["loaded"]
        sys.exit(
            "Brak COINGECKO_API_KEY.\n"
            f"Szukano pliku .env w:\n  {searched}\n"
            + (f"Wczytano: {', '.join(loaded)}, ale nie ma w nim linii COINGECKO_API_KEY=... "
               "(sprawdź pisownię, brak spacji wokół '=' i brak końcówki .txt w nazwie pliku).\n"
               if loaded else "Żaden plik .env nie został znaleziony.\n")
            + "Rozwiązania: wpisz klucz do pliku .env obok skryptu albo ustaw go w terminalu / "
              "w konfiguracji uruchomienia PyCharma (Environment variables)."
        )
    if os.environ.get("COINGECKO_ENV", "demo") == "pro":
        return Coingecko(pro_api_key=api_key, environment="pro", max_retries=2)
    return Coingecko(demo_api_key=api_key, environment="demo", max_retries=2)


def to_plain(obj):
    """Zamienia odpowiedzi SDK (modele Pydantic) na zwykłe dict/list."""
    if hasattr(obj, "to_dict"):
        obj = obj.to_dict()
    if isinstance(obj, dict):
        if set(obj) == {"root"}:
            return to_plain(obj["root"])
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    return obj


class Fetcher:
    """Wywołuje metody SDK; błąd jednej sekcji nie psuje całego raportu."""

    def __init__(self):
        self.warnings = []

    def call(self, label, func, *args, **kwargs):
        try:
            return to_plain(func(*args, **kwargs))
        except RATE_LIMIT_ERROR:
            self.warnings.append(f"{label}: limit zapytań (429) — sekcję pominięto")
        except API_ERROR as e:
            status = getattr(e, "status_code", "?")
            self.warnings.append(
                f"{label}: błąd API ({status}) — sekcję pominięto (możliwe ograniczenie planu)"
            )
        return None


# ==========================================================================
# newsdata.io (newsy) — czyste stdlib, bez dodatkowych zależności
# ==========================================================================
NEWSDATA_BASE = "https://newsdata.io/api/1"
NEWS_QUERY = "bitcoin OR BTC"

# Prosty, jawny słownik nagłówków (angielskie nagłówki). To przybliżenie —
# na planach płatnych newsdata.io zwraca własne pole `sentiment`, które ma pierwszeństwo.
BULL_EXACT = {"rise", "rose", "risen", "up", "high", "highs", "bull", "buy", "buys", "boost"}
BULL_PREFIX = ("surg", "soar", "rall", "jump", "gain", "adopt", "approv", "inflow",
               "breakout", "bullish", "climb", "accumulat", "rebound", "recover")
BEAR_EXACT = {"ban", "bans", "banned", "fell", "fall", "falls", "down", "low", "lows",
              "bear", "sell", "sells", "loss", "losses", "fear", "fears"}
BEAR_PREFIX = ("crash", "plung", "drop", "slump", "bearish", "hack", "lawsuit", "liquidat",
               "declin", "tumbl", "warn", "fraud", "collaps", "dump", "slide", "slid", "outflow")


def headline_score(title):
    """+1 / -1 / 0 na podstawie słów kluczowych w nagłówku."""
    tokens = re.findall(r"[a-z]+", (title or "").lower())
    bull = sum(1 for t in tokens if t in BULL_EXACT or t.startswith(BULL_PREFIX))
    bear = sum(1 for t in tokens if t in BEAR_EXACT or t.startswith(BEAR_PREFIX))
    return (bull > bear) - (bull < bear)


def newsdata_get(endpoint, params, timeout=15):
    """GET na newsdata.io. Zwraca (payload, kredyty_pozostałe, błąd). Klucz idzie w nagłówku."""
    api_key = os.environ.get("NEWSDATA_API_KEY")
    url = f"{NEWSDATA_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"X-ACCESS-KEY": api_key or "", "User-Agent": "btc-analysis/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
            remaining = resp.headers.get("X-API-Limit-Remaining")
    except urllib.error.HTTPError as e:
        message = None
        try:
            detail = json.loads(e.read().decode("utf-8")).get("results")
            message = detail.get("message") if isinstance(detail, dict) else None
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass
        return None, None, f"newsdata.io/{endpoint}: HTTP {e.code}" + (f" — {message}" if message else "")
    except urllib.error.URLError as e:
        return None, None, f"newsdata.io/{endpoint}: błąd połączenia ({e.reason})"
    except (TimeoutError, ValueError):
        return None, None, f"newsdata.io/{endpoint}: timeout lub niepoprawna odpowiedź"
    if body.get("status") != "success":
        return None, None, f"newsdata.io/{endpoint}: odpowiedź z błędem"
    try:
        remaining = int(remaining) if remaining is not None else None
    except ValueError:
        remaining = None
    return body, remaining, None


def collect_news(mode, query, warnings):
    if not os.environ.get("NEWSDATA_API_KEY"):
        warnings.append("newsdata.io: brak zmiennej NEWSDATA_API_KEY — sekcję newsów pominięto")
        return None
    common = {"language": "en", "removeduplicate": "1", "size": "10"}
    jobs = []
    if mode in ("latest", "both"):
        jobs.append(("latest", {"qInTitle": query, **common}))
    if mode in ("crypto", "both"):
        jobs.append(("crypto", {"coin": "btc", **common}))
    news = {"query": query, "latest": None, "crypto": None, "credits_remaining": None}
    for endpoint, params in jobs:
        payload, remaining, err = newsdata_get(endpoint, params)
        if err:
            warnings.append(err)
            continue
        news[endpoint] = payload
        if remaining is not None:
            news["credits_remaining"] = remaining
    return news


def summarize_news(news):
    if not news:
        return None
    sections, seen, tally = {}, set(), {"bull": 0, "bear": 0, "neutral": 0}
    used_api = used_kw = False
    for key in ("latest", "crypto"):
        payload = news.get(key)
        if not payload:
            continue
        items = []
        for art in payload.get("results") or []:
            title = art.get("title")
            if not title:
                continue
            api_sent = art.get("sentiment") if art.get("sentiment") in ("positive", "neutral", "negative") else None
            if api_sent:
                verdict = {"positive": 1, "negative": -1, "neutral": 0}[api_sent]
            else:
                verdict = headline_score(title)
            items.append({"title": title, "source": art.get("source_name") or art.get("source_id"),
                          "date": art.get("pubDate"), "link": art.get("link"),
                          "api_sentiment": api_sent, "verdict": verdict})
            norm = re.sub(r"\W+", " ", title.lower()).strip()
            if norm not in seen:  # ten sam nagłówek z dwóch endpointów liczymy raz
                seen.add(norm)
                tally["bull" if verdict > 0 else "bear" if verdict < 0 else "neutral"] += 1
                used_api, used_kw = used_api or bool(api_sent), used_kw or not api_sent
        sections[key] = {"total": payload.get("totalResults"), "items": items}
    if not sections:
        return None
    basis = ("pole sentiment z API" if used_api and not used_kw
             else "słowa kluczowe w nagłówkach" if used_kw and not used_api
             else "mieszana: API + słowa kluczowe")
    return {"sections": sections, "tally": {**tally, "basis": basis, "total": sum(tally.values())},
            "credits_remaining": news.get("credits_remaining")}


# ==========================================================================
# Bybit (API v5, wyłącznie odczyt) — czyste stdlib, bez dodatkowych zależności
# ==========================================================================
BYBIT_DEFAULT_BASE = "https://api.bybit.com"   # w UE można użyć np. https://api.bybit.eu (zmienna BYBIT_BASE_URL)
BYBIT_RECV_WINDOW = "10000"                    # tolerancja zegara w ms
BYBIT_AUTH_CODES = {10003, 10004, 10007, 10010, 33004}  # zły klucz, sygnatura, autoryzacja, IP, wygaśnięcie
BYBIT_PERF_DAYS = 30
BYBIT_EXEC_DAYS = 30                           # okno historii własnych transakcji do rozkładu cen


class BybitError(Exception):
    """Błąd API Bybit: kod retCode albo status HTTP w polu .code."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class BybitClient:
    """Minimalny klient REST v5. Zawiera WYŁĄCZNIE metody GET (odczyt) — brak jakichkolwiek metod handlowych."""

    def __init__(self, api_key=None, api_secret=None, base_url=None, timeout=15):
        self.api_key, self.api_secret = api_key, api_secret
        self.base = (base_url or BYBIT_DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self.time_offset_ms = 0

    @property
    def authenticated(self):
        return bool(self.api_key and self.api_secret)

    def now_ms(self):
        return int(time.time() * 1000) + self.time_offset_ms

    def _sync_time(self):
        res = self._request("/v5/market/time")
        server = int(res["timeNano"]) // 1_000_000 if res.get("timeNano") else int(res["timeSecond"]) * 1000
        self.time_offset_ms = server - int(time.time() * 1000)

    RATE_CODES = (10006, 10018, 10429)          # limit zapytań Bybit: ponawiamy z rosnącą przerwą
    RATE_WAITS = (0.6, 1.2, 2.4, 4.8)

    def _request(self, path, params=None, signed=False, _retry=True, _rate=0):
        # safe="%": kursor zwracany przez API jest już zakodowany i nie wolno go kodować drugi raz
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}, safe="%")
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        headers = {"User-Agent": "btc-analysis/1.0", "Accept": "application/json"}
        if signed:
            if not self.authenticated:
                raise BybitError("brak kluczy API")
            ts = str(self.now_ms())
            to_sign = ts + self.api_key + BYBIT_RECV_WINDOW + query
            signature = hmac.new(self.api_secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
            headers.update({"X-BAPI-API-KEY": self.api_key, "X-BAPI-TIMESTAMP": ts,
                            "X-BAPI-RECV-WINDOW": BYBIT_RECV_WINDOW, "X-BAPI-SIGN": signature})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=self.timeout) as resp:
                body = json.load(resp)
        except urllib.error.HTTPError as e:
            hint = " (403: blokada regionu lub IP, spróbuj zmiennej BYBIT_BASE_URL)" if e.code == 403 else ""
            raise BybitError(f"HTTP {e.code} dla {path}{hint}", code=e.code) from None
        except urllib.error.URLError as e:
            raise BybitError(f"błąd połączenia ({e.reason})") from None
        except (TimeoutError, ValueError):
            raise BybitError("timeout lub niepoprawna odpowiedź") from None
        code = body.get("retCode")
        if code == 10002 and signed and _retry:  # zegar lokalny rozjechany z serwerem Bybit
            self._sync_time()
            return self._request(path, params, signed, _retry=False, _rate=_rate)
        if code in self.RATE_CODES and _rate < len(self.RATE_WAITS):
            time.sleep(self.RATE_WAITS[_rate])
            return self._request(path, params, signed, _retry, _rate + 1)
        if code != 0:
            raise BybitError(f"{body.get('retMsg')} (retCode {code})", code=code)
        return body.get("result") or {}

    # --- dane publiczne (bez klucza)
    def ticker(self, symbol, category="linear"):
        lst = self._request("/v5/market/tickers", {"category": category, "symbol": symbol}).get("list") or []
        return lst[0] if lst else {}

    def long_short_ratio(self, symbol, period="1h", limit=24):
        res = self._request("/v5/market/account-ratio", {"category": "linear", "symbol": symbol, "period": period, "limit": limit})
        return res.get("list") or []

    def open_interest_history(self, symbol, interval="1h", limit=25):
        res = self._request("/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": interval, "limit": limit})
        return res.get("list") or []

    def klines(self, symbol, interval="D", limit=1000, category="linear", end=None):
        """Świece, najstarsza pierwsza: [ts_ms, open, high, low, close, volume]. interval: D, 60, 15...
        end (ms): zwróć świece do tego momentu włącznie (do pobierania starszej historii stronami)."""
        params = {"category": category, "symbol": symbol, "interval": interval, "limit": limit}
        if end:
            params["end"] = int(end)
        res = self._request("/v5/market/kline", params)
        rows = []
        for r in res.get("list") or []:
            try:
                rows.append([int(r[0])] + [float(x) for x in r[1:6]])
            except (TypeError, ValueError, IndexError):
                continue
        rows.sort(key=lambda r: r[0])
        return rows

    def orderbook(self, symbol, limit=200, category="linear"):
        return self._request("/v5/market/orderbook", {"category": category, "symbol": symbol, "limit": limit})

    def recent_trades(self, symbol, limit=1000, category="linear"):
        return self._request("/v5/market/recent-trade", {"category": category, "symbol": symbol, "limit": limit}).get("list") or []

    # --- dane konta (podpisane, tylko odczyt)
    def api_key_info(self):
        return self._request("/v5/user/query-api", signed=True)

    def wallet_balance(self, account_type="UNIFIED"):
        lst = self._request("/v5/account/wallet-balance", {"accountType": account_type}, signed=True).get("list") or []
        return lst[0] if lst else {}

    def _paged(self, path, params, pages=10):
        items, cursor = [], None
        for _ in range(pages):
            res = self._request(path, {**params, "cursor": cursor}, signed=True)
            items += res.get("list") or []
            cursor = res.get("nextPageCursor")
            if not cursor:
                break
        return items

    def positions(self, category="linear", settle_coins=("USDT", "USDC")):
        out = []
        for coin in settle_coins:
            out += self._paged("/v5/position/list", {"category": category, "settleCoin": coin, "limit": 200})
        return out

    def open_orders(self, category="linear", settle_coins=("USDT", "USDC")):
        """Oczekujące zlecenia (limit, warunkowe, TP/SL). Zwraca listę surowych rekordów."""
        out = []
        for coin in settle_coins:
            out += self._paged("/v5/order/realtime", {"category": category, "settleCoin": coin, "limit": 50}, pages=5)
        return out

    def executions(self, days=BYBIT_EXEC_DAYS, category="linear"):
        """Własne transakcje (wypełnienia) z ostatnich `days` dni; API pozwala na okna do 7 dni."""
        out, floor, t_end = [], self.now_ms() - days * 86_400_000, self.now_ms()
        while t_end > floor:
            t_start = max(t_end - 7 * 86_400_000 + 1000, floor)
            cursor = None
            for _ in range(20):
                res = self._request("/v5/execution/list", {"category": category, "startTime": t_start, "endTime": t_end,
                                    "limit": 100, "cursor": cursor}, signed=True)
                out += res.get("list") or []
                cursor = res.get("nextPageCursor")
                if not cursor:
                    break
            t_end = t_start - 1
        return out

    def closed_pnl(self, days=BYBIT_PERF_DAYS, category="linear"):
        """Zamknięte pozycje z ostatnich `days` dni (API pozwala na okna do 7 dni, więc pobieramy po kawałku)."""
        records, truncated = [], False
        floor = self.now_ms() - days * 86_400_000
        t_end = self.now_ms()
        while t_end > floor:
            t_start = max(t_end - 7 * 86_400_000 + 1000, floor)
            cursor = None
            for _ in range(20):
                res = self._request("/v5/position/closed-pnl", {"category": category, "startTime": t_start,
                                    "endTime": t_end, "limit": 100, "cursor": cursor}, signed=True)
                records += res.get("list") or []
                cursor = res.get("nextPageCursor")
                if not cursor:
                    break
            else:
                truncated = True
            t_end = t_start - 1
        return {"records": records, "truncated": truncated}


def collect_bybit(mode, warnings):
    """mode: 'auto' (włącz, gdy są klucze), 'on' (zawsze, bez kluczy tylko dane publiczne), 'off'."""
    if mode == "off":
        return None
    key, secret = os.environ.get("BYBIT_API_KEY"), os.environ.get("BYBIT_API_SECRET")
    client = BybitClient(key, secret, os.environ.get("BYBIT_BASE_URL"))
    if mode == "auto" and not client.authenticated:
        return None
    symbol = os.environ.get("BYBIT_SYMBOL", "BTCUSDT")
    raw = {"authenticated": client.authenticated, "symbol": symbol, "errors": [], "auth_failed": False}

    def grab(name, func, *args, private=False, **kwargs):
        if private and (not client.authenticated or raw["auth_failed"]):
            return
        try:
            raw[name] = func(*args, **kwargs)
        except BybitError as e:
            raw[name] = None
            raw["errors"].append(f"{name}: {e}")
            if e.code in BYBIT_AUTH_CODES:  # po błędzie klucza nie ponawiamy kolejnych zapytań
                raw["auth_failed"] = True

    grab("ticker", client.ticker, symbol)
    grab("ratio", client.long_short_ratio, symbol)
    grab("oi_history", client.open_interest_history, symbol)
    grab("key_info", client.api_key_info, private=True)
    grab("wallet", client.wallet_balance, os.environ.get("BYBIT_ACCOUNT_TYPE", "UNIFIED"), private=True)
    grab("positions", client.positions, private=True)
    grab("open_orders", client.open_orders, private=True)
    grab("closed_pnl", client.closed_pnl, BYBIT_PERF_DAYS, private=True)
    grab("executions", client.executions, BYBIT_EXEC_DAYS, private=True)
    if not client.authenticated:
        raw["errors"].append("brak BYBIT_API_KEY / BYBIT_API_SECRET, pokazuję tylko dane publiczne")
    if raw["errors"]:
        warnings.append("Bybit: " + "; ".join(raw["errors"][:3]) + (" …" if len(raw["errors"]) > 3 else ""))
    return raw


HIST_TARGET_BARS = 3000   # ok. 8 lat dziennych świec; BTCUSDT na Bybit ma ich mniej, więc zwykle dociągamy całą dostępną historię
HIST_PAGES_MAX = 4
HIST_VERSION = 1


def history_path():
    return os.environ.get("BTC_HISTORY_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc_history.json")


def _contiguous_tail(rows, step=86_400_000):
    """Najdłuższy ciągły fragment (bez brakujących dni) kończący się na ostatniej świecy."""
    i = len(rows) - 1
    while i > 0 and rows[i][0] - rows[i - 1][0] == step:
        i -= 1
    return rows[i:] if rows else rows


def ensure_history(client, symbol, recent, warnings, target=HIST_TARGET_BARS, now_ms=None, path=None):
    """Łączy ostatnie ~1000 świec z bieżącego pobrania ze starszą historią zapamiętaną w pliku. Starsze strony pobieramy
    tylko raz (kolejne uruchomienia crona dokładają najwyżej jedną świecę), więc nie obciążamy API co 15 minut.
    Zwraca zakończone świece dzienne, najstarsza pierwsza, bez dziur."""
    now_ms = now_ms or int(time.time() * 1000)
    path = path or history_path()
    cache = {}
    try:
        with open(path, encoding="utf-8") as fh:
            cache = json.load(fh)
        if not isinstance(cache, dict) or cache.get("v") != HIST_VERSION or cache.get("symbol") != symbol:
            cache = {}
    except (OSError, ValueError):
        cache = {}
    by_ts = {}
    for c in cache.get("candles") or []:
        try:
            by_ts[int(c[0])] = [int(c[0])] + [float(x) for x in c[1:6]]
        except (TypeError, ValueError, IndexError):
            continue
    n_cached = len(by_ts)
    for c in recent or []:
        if c[0] + 86_400_000 <= now_ms:               # bieżąca (niezakończona) świeca nie trafia do historii
            by_ts[c[0]] = c
    rows = [by_ts[k] for k in sorted(by_ts)]
    complete = bool(cache.get("complete")) and n_cached > 0
    pages = 0
    try:
        while rows and not complete and len(rows) < target and pages < HIST_PAGES_MAX:
            older = [c for c in client.klines(symbol, "D", 1000, end=rows[0][0] - 1) if c[0] < rows[0][0]]
            pages += 1
            if not older:
                complete = True
                break
            rows = older + rows
            if len(older) < 1000:                      # krótsza strona = początek notowań
                complete = True
    except BybitError as e:
        warnings.append(f"Bybit (historia długoterminowa): {e}. Używam {len(rows)} dostępnych świec.")
    if len(rows) != n_cached or complete != bool(cache.get("complete")):
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"v": HIST_VERSION, "symbol": symbol, "complete": complete, "candles": rows}, fh, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError as e:
            warnings.append(f"Nie udało się zapisać historii świec ({e}).")
    tail = _contiguous_tail(rows)
    if len(tail) < len(rows):
        warnings.append(f"Historia świec ma lukę: do planów długoterminowych używam ciągłego fragmentu od "
                        f"{datetime.fromtimestamp(tail[0][0] / 1000, tz=timezone.utc).date().isoformat()} ({len(tail)} z {len(rows)} dni).")
    return tail


def collect_bybit_market(mode, warnings):
    """Publiczne dane rynkowe Bybit (bez klucza) dla planera: świece dzienne i 15-minutowe, księga zleceń, ostatnie transakcje."""
    if mode == "off":
        return None
    client = BybitClient(base_url=os.environ.get("BYBIT_BASE_URL"))
    symbol = os.environ.get("BYBIT_SYMBOL", "BTCUSDT")
    out = {"symbol": symbol, "errors": []}
    for name, func, args in (("candles_d", client.klines, (symbol, "D", 1000)), ("candles_15", client.klines, (symbol, "15", 1000)),
                             ("ticker", client.ticker, (symbol,)), ("book", client.orderbook, (symbol, 200)),
                             ("trades", client.recent_trades, (symbol, 1000))):
        try:
            out[name] = func(*args)
        except BybitError as e:
            out[name] = None
            out["errors"].append(f"{name}: {e}")
            if name == "candles_d":  # bez świec dziennych planer i tak nie ruszy, nie męczymy API dalej
                break
    if out.get("candles_d"):
        try:
            out["candles_long"] = ensure_history(client, symbol, out["candles_d"], warnings)
        except Exception as e:  # historia długoterminowa jest dodatkiem: błąd nie może zatrzymać planera
            warnings.append(f"Historia długoterminowa: {type(e).__name__}: {e}")
    if out["errors"]:
        warnings.append("Bybit (rynek, planer): " + "; ".join(out["errors"][:3]))
    return out


def _pct(x):
    return x * 100 if x is not None else None


def _iso_ms(ms):
    try:
        return datetime.fromtimestamp(int(float(ms)) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _perf_summary(recs, equity, full, days, truncated):
    """recs: lista (timestamp_ms, symbol, closedPnl). Tryb masked: wartości jako % bieżącego kapitału."""
    if not recs:
        return {"days": days, "trades": 0, "truncated": truncated}
    conv = (lambda v: v) if full else ((lambda v: v / equity * 100) if equity else None)
    pnls = [p for _, _, p in recs]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    res = {
        "days": days, "trades": len(pnls), "truncated": truncated,
        "win_rate_pct": len(wins) / len(pnls) * 100,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "avg_win_loss_ratio": statistics.fmean(wins) / -statistics.fmean(losses) if wins and losses else None,
        "unit": "usd" if full else ("pct" if conv else None),
        "realized": None, "best_trade": None, "worst_trade": None, "curve": None, "by_symbol": [],
    }
    if conv:
        res["realized"], res["best_trade"], res["worst_trade"] = conv(sum(pnls)), conv(max(pnls)), conv(min(pnls))
        by_day = {}
        for ts, _, p in recs:
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
            by_day[day] = by_day.get(day, 0.0) + p
        cum, pts = 0.0, [{"d": (min(by_day) - timedelta(days=1)).isoformat(), "v": 0.0}]
        for day in sorted(by_day):
            cum += by_day[day]
            pts.append({"d": day.isoformat(), "v": conv(cum)})
        res["curve"] = pts
    groups = {}
    for _, sym, p in recs:
        groups.setdefault(sym, []).append(p)
    ranked = sorted(groups.items(), key=lambda kv: abs(sum(kv[1])), reverse=True)[:6]
    res["by_symbol"] = [{"symbol": sym, "trades": len(v), "win_rate_pct": sum(1 for x in v if x > 0) / len(v) * 100,
                         "pnl": conv(sum(v)) if conv else None} for sym, v in ranked]
    return res


def _order_kind(o):
    """tp / sl / conditional / limit / market — na podstawie stopOrderType i typu zlecenia."""
    sot = str(o.get("stopOrderType") or "")
    if "TakeProfit" in sot:
        return "tp"
    if "StopLoss" in sot or "TrailingStop" in sot:
        return "sl"
    if num(o.get("triggerPrice")):
        return "conditional"
    return "limit" if str(o.get("orderType")) == "Limit" else "market"


def summarize_bybit(raw, privacy="masked"):
    """Zamienia surowe odpowiedzi na zwarty obiekt dla raportu. W trybie 'masked' nie ma w nim żadnych kwot,
    rozmiarów pozycji ani cen wejścia/likwidacji — tylko wartości względne (%)."""
    if not raw:
        return None
    full = privacy == "full"
    out = {"privacy": privacy, "authenticated": raw["authenticated"], "symbol": raw["symbol"],
           "errors": list(raw["errors"]), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "market": None, "key": None, "account": None, "allocation": None, "exposure": None,
           "positions": [], "orders": [], "positions_ok": raw.get("positions") is not None,
           "orders_ok": raw.get("open_orders") is not None,
           "open_orders": len(raw["open_orders"]) if raw.get("open_orders") is not None else None, "performance": None}
    out["status"] = ("public" if not raw["authenticated"] else "error" if not raw.get("wallet")
                     else "partial" if raw["errors"] else "ok")

    # --- rynek (dane publiczne, bez ograniczeń)
    t = raw.get("ticker")
    if t:
        mark, index, fr = num(t.get("markPrice")), num(t.get("indexPrice")), num(t.get("fundingRate"))
        ratio = (raw.get("ratio") or [None])[0] or {}
        oi_hist = raw.get("oi_history") or []
        oi_change = None
        if len(oi_hist) >= 2:
            first, last = num(oi_hist[-1].get("openInterest")), num(oi_hist[0].get("openInterest"))
            oi_change = (last / first - 1) * 100 if first and last is not None else None
        out["market"] = {
            "last": num(t.get("lastPrice")), "mark": mark, "index": index,
            "premium_pct": (mark / index - 1) * 100 if mark and index else None,
            "funding_pct": _pct(fr), "next_funding": _iso_ms(t.get("nextFundingTime")),
            "open_interest_usd": num(t.get("openInterestValue")),
            "oi_change_pct": oi_change, "oi_change_hours": len(oi_hist) - 1 if len(oi_hist) >= 2 else None,
            "turnover_24h_usd": num(t.get("turnover24h")), "price_24h_pct": _pct(num(t.get("price24hPcnt"))),
            "long_pct": _pct(num(ratio.get("buyRatio"))), "short_pct": _pct(num(ratio.get("sellRatio"))),
        }

    # --- bezpieczeństwo klucza
    ki = raw.get("key_info")
    if ki:
        read_only = ki.get("readOnly") in (1, "1", True)
        ips = [x for x in (ki.get("ips") or []) if x]
        ip_restricted = bool(ips) and "*" not in ips
        days_left = None
        try:
            exp = datetime.fromisoformat(str(ki.get("expiredAt")).replace("Z", "+00:00"))
            if exp.year > 2000:
                days_left = (exp - datetime.now(timezone.utc)).days
        except ValueError:
            pass
        warns = []
        if not read_only:
            warns.append("Klucz API NIE jest tylko do odczytu. Utwórz nowy klucz z uprawnieniami wyłącznie do odczytu.")
        if not ip_restricted:
            warns.append("Klucz nie ma przypisanego adresu IP. Rozważ ograniczenie go do adresu, z którego działa skrypt.")
        if days_left is not None and days_left <= 14:
            warns.append(f"Klucz API wygasa za {max(days_left, 0)} dn.")
        out["key"] = {"read_only": read_only, "ip_restricted": ip_restricted, "days_left": days_left, "warnings": warns}

    # --- konto
    w = raw.get("wallet") or {}
    equity = num(w.get("totalEquity")) if w else None
    if w:
        im, upl = num(w.get("totalInitialMargin")), num(w.get("totalPerpUPL"))
        acc = {"margin_usage_pct": im / equity * 100 if im is not None and equity else None,
               "mm_rate_pct": _pct(num(w.get("accountMMRate"))),
               "upl_pct_of_equity": upl / equity * 100 if upl is not None and equity else None}
        if full:
            acc.update(equity_usd=equity, available_usd=num(w.get("totalAvailableBalance")),
                       wallet_usd=num(w.get("totalWalletBalance")), unrealised_pnl_usd=upl)
        out["account"] = acc
        coins = [(c.get("coin"), num(c.get("usdValue"))) for c in w.get("coin") or []]
        coins = [(n, v) for n, v in coins if n and v and v > 0]
        total = sum(v for _, v in coins)
        if total:
            alloc, other = [], 0.0
            for n, v in sorted(coins, key=lambda x: x[1], reverse=True):
                if v / total < 0.01:
                    other += v
                    continue
                item = {"coin": n, "pct": v / total * 100}
                if full:
                    item["usd"] = v
                alloc.append(item)
            if other:
                alloc.append({"coin": "Inne", "pct": other / total * 100, **({"usd": other} if full else {})})
            out["allocation"] = alloc

    # --- pozycje
    longs = shorts = 0.0
    for p in raw.get("positions") or []:
        size = num(p.get("size"))
        if not size:
            continue
        side = "Long" if p.get("side") == "Buy" else "Short"
        mark, liq = num(p.get("markPrice")), num(p.get("liqPrice")) or None
        upl, im, value = num(p.get("unrealisedPnl")), num(p.get("positionIM")), num(p.get("positionValue"))
        liq_dist = None
        if liq and mark:
            liq_dist = (mark - liq) / mark * 100 if side == "Long" else (liq - mark) / mark * 100
        if value:
            if side == "Long":
                longs += value
            else:
                shorts += value
        item = {
            "symbol": p.get("symbol"), "side": side, "leverage": num(p.get("leverage")),
            "mode": "isolated" if str(p.get("tradeMode")) == "1" else "cross",
            "roe_pct": upl / im * 100 if upl is not None and im else None,
            "upl_pct_of_equity": upl / equity * 100 if upl is not None and equity else None,
            "exposure_pct": value / equity * 100 if value and equity else None,
            "liq_distance_pct": liq_dist,
            "has_tp": bool(num(p.get("takeProfit"))), "has_sl": bool(num(p.get("stopLoss"))),
            "opened": _iso_ms(p.get("createdTime")),
        }
        if full:
            item.update(size=size, avg_price=num(p.get("avgPrice")), liq_price=liq, upl_usd=upl, value_usd=value,
                        mark_price=mark, take_profit=num(p.get("takeProfit")) or None,
                        stop_loss=num(p.get("stopLoss")) or None, trailing_stop=num(p.get("trailingStop")) or None)
        out["positions"].append(item)
    if equity:
        out["exposure"] = {"long_pct": longs / equity * 100, "short_pct": shorts / equity * 100,
                           "net_pct": (longs - shorts) / equity * 100, "gross_pct": (longs + shorts) / equity * 100}

    # --- otwarte zlecenia (limit, warunkowe, TP/SL)
    marks = {x.get("symbol"): num(x.get("markPrice")) for x in raw.get("positions") or []}
    if t and t.get("symbol"):
        marks.setdefault(t.get("symbol"), num(t.get("markPrice")))
    for o in (raw.get("open_orders") or [])[:50]:
        sym, kind = o.get("symbol"), _order_kind(o)
        price = num(o.get("triggerPrice")) or num(o.get("price")) or None
        qty = num(o.get("leavesQty")) or num(o.get("qty"))
        mark = marks.get(sym)
        item = {"symbol": sym, "side": o.get("side"),  # Buy / Sell (dla zleceń reduce-only to strona zlecenia, nie pozycji)
                "type": o.get("orderType"), "kind": kind, "reduce_only": bool(o.get("reduceOnly")),
                "distance_pct": (price / mark - 1) * 100 if price and mark else None,
                "size_pct": qty * price / equity * 100 if qty and price and equity else None,
                "created": _iso_ms(o.get("createdTime"))}
        if full:
            item.update(price=price, qty=qty, take_profit=num(o.get("takeProfit")) or None,
                        stop_loss=num(o.get("stopLoss")) or None)
        out["orders"].append(item)
    out["orders"].sort(key=lambda x: (str(x["symbol"]), x["kind"]))

    # --- skuteczność (zamknięte pozycje)
    cp = raw.get("closed_pnl")
    if cp is not None:
        recs = []
        for x in cp["records"]:
            pnl, ts = num(x.get("closedPnl")), num(x.get("updatedTime")) or num(x.get("createdTime"))
            if pnl is not None and ts is not None:
                recs.append((int(ts), x.get("symbol"), pnl))
        recs.sort()
        out["performance"] = _perf_summary(recs, equity, full, BYBIT_PERF_DAYS, cp["truncated"])
    return out


def collect(client, vs, days, compare, news_mode="both", news_query=NEWS_QUERY, bybit_mode="auto"):
    f = Fetcher()
    vs_list = ",".join(dict.fromkeys([vs, "usd", "eur", "pln"]))
    data = {"vs_list": vs_list.split(",")}

    data["spot"] = f.call(
        "simple.price", client.simple.price.get,
        ids="bitcoin", vs_currencies=vs_list,
        include_market_cap=True, include_24hr_vol=True,
        include_24hr_change=True, include_last_updated_at=True,
    )
    data["market"] = f.call(
        "coins.markets", client.coins.markets.get,
        vs_currency=vs, ids="bitcoin",
        price_change_percentage="1h,24h,7d,30d,200d,1y",
    )
    data["details"] = f.call(
        "coins.get_id", client.coins.get_id, "bitcoin",
        localization=False, tickers=False, market_data=True,
        community_data=True, developer_data=True,
    )
    data["chart"] = f.call(
        "coins.market_chart (BTC)", client.coins.market_chart.get,
        "bitcoin", vs_currency=vs, days=str(days), interval="daily",
    )
    data["compare_id"] = compare
    data["compare_chart"] = None
    if compare:
        data["compare_chart"] = f.call(
            f"coins.market_chart ({compare})", client.coins.market_chart.get,
            compare, vs_currency=vs, days=str(days), interval="daily",
        )
    data["ohlc"] = f.call(
        "coins.ohlc", client.coins.ohlc.get, "bitcoin", vs_currency=vs, days="30"
    )
    data["global"] = f.call("global", client.global_.get)
    data["derivatives"] = f.call("derivatives", client.derivatives.get)
    data["tickers"] = f.call("coins.tickers", client.coins.tickers.get, "bitcoin")
    data["treasury_companies"] = f.call(
        "public_treasury (spółki)", client.public_treasury.get_coin_id,
        "bitcoin", entity="companies",
    )
    data["treasury_governments"] = f.call(
        "public_treasury (rządy)", client.public_treasury.get_coin_id,
        "bitcoin", entity="governments",
    )
    data["trending"] = f.call("search.trending", client.search.trending.get)
    data["news"] = collect_news(news_mode, news_query, f.warnings) if news_mode != "off" else None
    data["bybit"] = collect_bybit(bybit_mode, f.warnings)
    data["bybit_market"] = collect_bybit_market(bybit_mode, f.warnings)
    data["warnings"] = f.warnings
    return data


# ==========================================================================
# Wskaźniki i statystyki
# ==========================================================================
def num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def to_daily(points):
    """[[ts_ms, wartość], ...] -> {data_utc: wartość} (ostatni punkt dnia wygrywa)."""
    out = {}
    for row in points or []:
        if len(row) < 2 or row[1] is None:
            continue
        day = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).date()
        out[day] = float(row[1])
    return out


def sma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema_series(values, n):
    """EMA wyrównana do values[n-1:]."""
    if len(values) < n:
        return []
    k = 2.0 / (n + 1)
    out = [sum(values[:n]) / n]
    for v in values[n:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(values, fast=12, slow=26, signal=9):
    ef, es = ema_series(values, fast), ema_series(values, slow)
    if not es:
        return None
    ef = ef[-len(es):]
    line = [a - b for a, b in zip(ef, es)]
    sig = ema_series(line, signal)
    if not sig:
        return None
    line = line[-len(sig):]
    hist = [a - b for a, b in zip(line, sig)]
    return {
        "macd": line[-1],
        "signal": sig[-1],
        "hist": hist[-1],
        "prev_hist": hist[-2] if len(hist) > 1 else None,
    }


def rsi(values, period=14):
    if len(values) <= period:
        return None
    deltas = [b - a for a, b in zip(values, values[1:])]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def bollinger(values, n=20, k=2.0):
    if len(values) < n:
        return None
    window = values[-n:]
    mid = sum(window) / n
    sd = statistics.pstdev(window)
    upper, lower = mid + k * sd, mid - k * sd
    pct_b = (values[-1] - lower) / (upper - lower) if upper != lower else None
    return {"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b,
            "bandwidth": (upper - lower) / mid if mid else None}


def simple_returns(values):
    return [b / a - 1.0 for a, b in zip(values, values[1:]) if a]


def annualized_vol(values, window=None):
    v = values[-(window + 1):] if window else values
    r = [math.log(b / a) for a, b in zip(v, v[1:]) if a > 0 and b > 0]
    return statistics.stdev(r) * math.sqrt(365) if len(r) > 2 else None


def max_drawdown(values):
    peak, worst = values[0], 0.0
    for p in values:
        peak = max(peak, p)
        worst = min(worst, p / peak - 1.0)
    return worst


def historical_var(returns, level=0.95):
    """Historyczny VaR 1-dniowy (jako dodatni ułamek straty)."""
    if len(returns) < 20:
        return None
    ordered = sorted(returns)
    idx = int(math.floor((1 - level) * len(ordered)))
    return -ordered[idx]


def pct_change(values, days_back):
    if len(values) <= days_back or not values[-1 - days_back]:
        return None
    return values[-1] / values[-1 - days_back] - 1.0


def correlation(a, b):
    n = min(len(a), len(b))
    if n < 10:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return cov / math.sqrt(va * vb)


def beta(asset_returns, market_returns):
    n = min(len(asset_returns), len(market_returns))
    if n < 10:
        return None
    a, m = asset_returns[-n:], market_returns[-n:]
    ma, mm = statistics.fmean(a), statistics.fmean(m)
    var_m = sum((y - mm) ** 2 for y in m)
    if var_m == 0:
        return None
    return sum((x - ma) * (y - mm) for x, y in zip(a, m)) / var_m


def atr(candles, period=14):
    """ATR (Wilder) ze świec [ts, open, high, low, close]."""
    if len(candles) <= period:
        return None
    trs = []
    for prev, cur in zip(candles, candles[1:]):
        high, low, prev_close = cur[2], cur[3], prev[4]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def fib_levels(high, low):
    diff = high - low
    return {f"{r * 100:.1f}%": high - diff * r for r in (0.236, 0.382, 0.5, 0.618, 0.786)}


def summarize_derivatives(rows, index_id="BTC"):
    perps = []
    for r in rows or []:
        oi = num(r.get("open_interest"))
        if r.get("index_id") == index_id and r.get("contract_type") == "perpetual" and oi:
            perps.append({**r, "_oi": oi, "_fr": num(r.get("funding_rate")),
                          "_vol": num(r.get("volume_24h")), "_basis": num(r.get("basis"))})
    if not perps:
        return None
    total_oi = sum(r["_oi"] for r in perps)
    with_fr = [r for r in perps if r["_fr"] is not None]
    weighted_fr = (sum(r["_fr"] * r["_oi"] for r in with_fr) / sum(r["_oi"] for r in with_fr)
                   if with_fr else None)
    with_basis = [r for r in perps if r["_basis"] is not None]
    weighted_basis = (sum(r["_basis"] * r["_oi"] for r in with_basis) / sum(r["_oi"] for r in with_basis)
                      if with_basis else None)
    top = sorted(perps, key=lambda r: r["_oi"], reverse=True)[:5]
    return {
        "contracts": len(perps),
        "total_oi_usd": total_oi,
        "total_volume_24h_usd": sum(r["_vol"] or 0 for r in perps),
        "weighted_funding_rate": weighted_fr,
        "weighted_basis": weighted_basis,
        "top": [{"market": r.get("market"), "symbol": r.get("symbol"),
                 "oi_usd": r["_oi"], "funding_rate": r["_fr"]} for r in top],
    }


def summarize_tickers(payload, top_n=8):
    tickers = (payload or {}).get("tickers") or []
    rows = []
    for t in tickers:
        vol = num((t.get("converted_volume") or {}).get("usd"))
        px = num((t.get("converted_last") or {}).get("usd"))
        if vol:
            rows.append({
                "market": (t.get("market") or {}).get("name"),
                "pair": f"{t.get('base')}/{t.get('target')}",
                "target": t.get("target"),
                "price_usd": px,
                "volume_usd": vol,
                "spread_pct": num(t.get("bid_ask_spread_percentage")),
            })
    if not rows:
        return None
    rows.sort(key=lambda r: r["volume_usd"], reverse=True)
    top = rows[:top_n]
    usd_like = [r for r in top if r["target"] in ("USD", "USDT", "USDC") and r["price_usd"]]
    dispersion = None
    if len(usd_like) >= 2:
        lo = min(usd_like, key=lambda r: r["price_usd"])
        hi = max(usd_like, key=lambda r: r["price_usd"])
        dispersion = {"pct": (hi["price_usd"] / lo["price_usd"] - 1) * 100,
                      "low": f"{lo['market']} {lo['pair']}", "high": f"{hi['market']} {hi['pair']}"}
    spreads = [r["spread_pct"] for r in top if r["spread_pct"] is not None]
    return {"top": top, "dispersion": dispersion,
            "median_spread_pct": statistics.median(spreads) if spreads else None}


# ==========================================================================
# Analiza
# ==========================================================================
def analyze(data, vs, bybit_privacy="masked"):
    a = {"vs": vs, "history_ok": False}
    chart = data.get("chart") or {}
    price_d = to_daily(chart.get("prices"))

    if len(price_d) >= 30:
        a["history_ok"] = True
        dates = sorted(price_d)
        closes = [price_d[d] for d in dates]
        vol_d, cap_d = to_daily(chart.get("total_volumes")), to_daily(chart.get("market_caps"))
        volumes = [vol_d.get(d) for d in dates]
        a["series"] = {"date": [d.isoformat() for d in dates], "price": closes,
                       "volume": volumes, "market_cap": [cap_d.get(d) for d in dates]}
        a["n_days"] = len(closes)
        a["last"] = closes[-1]
        a["sma20"], a["sma50"], a["sma200"] = sma(closes, 20), sma(closes, 50), sma(closes, 200)
        a["macd"] = macd(closes)
        a["rsi14"] = rsi(closes, 14)
        a["boll"] = bollinger(closes, 20)
        a["changes"] = {f"{d}d": pct_change(closes, d) for d in (7, 30, 90, 180)}
        a["changes"]["full"] = closes[-1] / closes[0] - 1.0

        rets = simple_returns(closes)
        a["vol30"], a["vol_all"] = annualized_vol(closes, 30), annualized_vol(closes)
        a["max_drawdown"] = max_drawdown(closes)
        a["current_drawdown"] = closes[-1] / max(closes) - 1.0
        a["var95"] = historical_var(rets, 0.95)
        a["best_day"], a["worst_day"] = max(rets), min(rets)
        a["positive_days_share"] = sum(1 for r in rets if r > 0) / len(rets)
        a["period_high"], a["period_low"] = max(closes), min(closes)

        vols = [v for v in volumes if v]
        if len(vols) >= 30:
            a["volume_ratio_7_30"] = statistics.fmean(vols[-7:]) / statistics.fmean(vols[-30:])

        window = closes[-90:]
        a["fib"] = {"high": max(window), "low": min(window),
                    "levels": fib_levels(max(window), min(window))}

        cmp_chart = data.get("compare_chart")
        if cmp_chart:
            cmp_d = to_daily(cmp_chart.get("prices"))
            common = sorted(set(price_d) & set(cmp_d))
            if len(common) > 30:
                rb = [price_d[common[i]] / price_d[common[i - 1]] - 1 for i in range(1, len(common))]
                rc = [cmp_d[common[i]] / cmp_d[common[i - 1]] - 1 for i in range(1, len(common))]
                btc_30 = price_d[common[-1]] / price_d[common[-31]] - 1
                cmp_30 = cmp_d[common[-1]] / cmp_d[common[-31]] - 1
                a["compare"] = {
                    "id": data.get("compare_id"),
                    "corr_all": correlation(rb, rc),
                    "corr_30d": correlation(rb[-30:], rc[-30:]),
                    "beta_vs_btc": beta(rc, rb),
                    "btc_30d": btc_30, "other_30d": cmp_30,
                }

    candles = data.get("ohlc") or []
    if len(candles) > 15:
        a["ohlc"] = {"atr14": atr(candles, 14), "candles": len(candles),
                     "high_30d": max(c[2] for c in candles), "low_30d": min(c[3] for c in candles)}

    a["derivatives"] = summarize_derivatives(data.get("derivatives"))
    a["tickers"] = summarize_tickers(data.get("tickers"))
    a["news"] = summarize_news(data.get("news"))
    a["bybit"] = summarize_bybit(data.get("bybit"), bybit_privacy)
    a["signals"] = build_signals(a, data)
    return a


def build_signals(a, data):
    """Przejrzyste reguły heurystyczne — NIE model predykcyjny."""
    sig = []

    def add(label, verdict, detail):
        sig.append({"label": label, "verdict": verdict, "detail": detail})

    nt = (a.get("news") or {}).get("tally")
    if nt and nt["total"] >= 5:
        net = nt["bull"] - nt["bear"]
        verdict = "+" if net >= 3 else "-" if net <= -3 else "0"
        add("Nagłówki newsów", verdict,
            f"{nt['bull']} bycze / {nt['bear']} niedźwiedzie z {nt['total']} ({nt['basis']})")
    if not a.get("history_ok"):
        return sig
    last = a["last"]
    if a.get("sma200") is not None:
        add("Cena vs SMA200", "+" if last > a["sma200"] else "-",
            f"cena {money(last)}, SMA200 {money(a['sma200'])}")
    if a.get("sma50") is not None and a.get("sma200") is not None:
        add("SMA50 vs SMA200", "+" if a["sma50"] > a["sma200"] else "-",
            "układ średnich długoterminowy " + ("wzrostowy" if a["sma50"] > a["sma200"] else "spadkowy"))
    if a.get("sma50") is not None:
        add("Cena vs SMA50", "+" if last > a["sma50"] else "-", f"SMA50 {money(a['sma50'])}")
    m = a.get("macd")
    if m:
        rising = m["prev_hist"] is not None and m["hist"] > m["prev_hist"]
        add("MACD", "+" if m["hist"] > 0 else "-",
            f"histogram {m['hist']:+.1f} ({'rośnie' if rising else 'maleje'})")
    r = a.get("rsi14")
    if r is not None:
        verdict = "-" if r >= 70 else "+" if r <= 30 else "0"
        note = "wykupienie" if r >= 70 else "wyprzedanie" if r <= 30 else "strefa neutralna"
        add("RSI(14)", verdict, f"{r:.1f} — {note}")
    b = a.get("boll")
    if b and b["pct_b"] is not None:
        verdict = "-" if b["pct_b"] > 1 else "+" if b["pct_b"] < 0 else "0"
        add("Wstęgi Bollingera", verdict, f"%B = {b['pct_b']:.2f} (poza 0–1 = cena poza wstęgą)")
    vr, ch7 = a.get("volume_ratio_7_30"), (a.get("changes") or {}).get("7d")
    if vr is not None and ch7 is not None:
        if vr > 1.2 and ch7 > 0:
            add("Wolumen", "+", f"wolumen 7d/30d = {vr:.2f}, wzrost ceny potwierdzony wolumenem")
        elif vr > 1.2 and ch7 < 0:
            add("Wolumen", "-", f"wolumen 7d/30d = {vr:.2f}, spadek przy podwyższonym wolumenie")
        else:
            add("Wolumen", "0", f"wolumen 7d/30d = {vr:.2f}")
    fr = (a.get("derivatives") or {}).get("weighted_funding_rate")
    if fr is not None:
        if fr > 0.03:
            add("Funding rate (kontrariańsko)", "-", f"{fr:.4f}% — rynek przegrzany pozycjami long")
        elif fr < -0.01:
            add("Funding rate (kontrariańsko)", "+", f"{fr:.4f}% — przewaga shortów, ryzyko short squeeze")
        else:
            add("Funding rate (kontrariańsko)", "0", f"{fr:.4f}% — poziom typowy")
    return sig


# ==========================================================================
# Planer wejść: plan na dziś, dziennik propozycji, test wsteczny, rozkład zleceń
# ==========================================================================
# Uwaga: to heurystyka oparta o świece dzienne (trend, średnie, RSI, ATR, swingi). Nic nie zleca na giełdzie
# (klucz Bybit jest tylko do odczytu), a każda propozycja jest zapisywana w dzienniku i rozliczana z danych rynkowych.
DAY_MS = 86_400_000
PLAN_WARMUP = 210                 # świece potrzebne do SMA200 i nachylenia SMA50
PLAN_VALID_BARS = 2               # zlecenie limit jest ważne 2 świece dzienne od zamknięcia świecy planu
PLAN_MAX_HOLD_BARS = 7            # po 7 dniach od wejścia pozycja jest zamykana po cenie zamknięcia świecy
PLAN_MIN_CONF = 55                # poniżej tej pewności analizator mówi "czekaj"
PLAN_FEE_PCT = 0.075              # koszt rundy (wejście limit ~0,02% + wyjście rynkowe ~0,055%) od nominału
PLAN_FUNDING_PCT_DAY = 0.03       # funding płacony przez longa: 0,01% co 8 h (stawka bazowa kontraktów) = 0,03% nominału na dobę; shortowi nic nie doliczamy
PLAN_STOP_SLIP_PCT = 0.05         # poślizg zlecenia stop (rynkowe po aktywacji), % ceny; przy luce na otwarciu liczy się gorsza z cen
PLAN_FILLED = ("sl", "tp1_be", "tp2", "time_stop")
PLAN_NOT_FILLED = ("expired", "missed", "invalidated")
PLAN_CANCELLED = ("withdrawn", "replaced")
JOURNAL_VERSION = 1


def _enabled_setups():
    """Które typy wejść są aktywne: PLAN_SETUPS=pullback,breakout (domyślnie oba)."""
    raw = os.environ.get("PLAN_SETUPS", "pullback,breakout")
    en = tuple(x for x in (y.strip().lower() for y in raw.split(",")) if x in ("pullback", "breakout"))
    return en or ("pullback",)


def _fp(x, d=0):
    """Cena z odstępem co tysiące: 98 250."""
    return f"{x:,.{d}f}".replace(",", " ") if x is not None else "n/d"


def px_digits(price):
    """Ile miejsc po przecinku mają mieć poziomy cenowe planu: 5 cyfr znaczących, co najmniej 1 miejsce.
    Dla cen od 1000 w górę (BTC, ETH) to 0,1, czyli tak jak dotąd; dla tanich aktywów odpowiednio drobniej
    (np. 0,0001 przy cenie 2 USD). Stałe zaokrąglenie do 0,1 zrównywało wejście ze stopem na aktywach poniżej ok. 15 USD."""
    if not price or price <= 0:
        return 1
    return max(1, 4 - int(math.floor(math.log10(price))))


# ---------- wskaźniki (serie przyczynowe: wartość w indeksie t używa tylko świec do t) ----------
def _sma_series(v, n):
    out, s = [None] * len(v), 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _ema_full(v, n):
    out = [None] * len(v)
    if len(v) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(v[:n]) / n
    out[n - 1] = e
    for i in range(n, len(v)):
        e = v[i] * k + e * (1 - k)
        out[i] = e
    return out


def _rsi_series(v, n=14):
    out = [None] * len(v)
    if len(v) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        d = v[i] - v[i - 1]
        gain += max(d, 0.0)
        loss += max(-d, 0.0)
    ag, al = gain / n, loss / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    for i in range(n + 1, len(v)):
        d = v[i] - v[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def _atr_series(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    trs = [None] + [max(c[i][2] - c[i][3], abs(c[i][2] - c[i - 1][4]), abs(c[i][3] - c[i - 1][4])) for i in range(1, len(c))]
    a = sum(trs[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(c)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def _macd_hist_series(v):
    ef, es = _ema_full(v, 12), _ema_full(v, 26)
    line = [(a - b) if a is not None and b is not None else None for a, b in zip(ef, es)]
    first = next((i for i, x in enumerate(line) if x is not None), None)
    hist = [None] * len(v)
    if first is None:
        return hist
    for j, s in enumerate(_ema_full(line[first:], 9)):
        if s is not None:
            hist[first + j] = line[first + j] - s
    return hist


def _pivots(c, k=3):
    """Swingi: (indeks, cena). Szczyt/dołek jest potwierdzony dopiero po k kolejnych świecach (i + k)."""
    hi, lo = [], []
    for i in range(k, len(c) - k):
        h, l = c[i][2], c[i][3]
        if h > max(x[2] for x in c[i - k:i]) and h >= max(x[2] for x in c[i + 1:i + k + 1]):
            hi.append((i, h))
        if l < min(x[3] for x in c[i - k:i]) and l <= min(x[3] for x in c[i + 1:i + k + 1]):
            lo.append((i, l))
    return hi, lo


def build_indicators(candles):
    cl = [c[4] for c in candles]
    piv_hi, piv_lo = _pivots(candles, 3)
    return {"c": candles, "cl": cl, "sma20": _sma_series(cl, 20), "sma50": _sma_series(cl, 50),
            "sma200": _sma_series(cl, 200), "rsi": _rsi_series(cl, 14), "atr": _atr_series(candles, 14),
            "mh": _macd_hist_series(cl), "piv_hi": piv_hi, "piv_lo": piv_lo, "k": 3,
            "piv_hi_i": [i for i, _ in piv_hi], "piv_lo_i": [i for i, _ in piv_lo]}


def _recent_pivots(ind, name, t, k, span=120):
    """Ceny swingów potwierdzonych do świecy t (i + k <= t) i nie starszych niż `span` świec; kolejność jak w liście źródłowej."""
    idx = ind.get(name + "_i")
    if idx is None:
        return [p for i, p in ind[name] if i + k <= t and t - i <= span]
    return [p for _, p in ind[name][bisect.bisect_left(idx, t - span):bisect.bisect_right(idx, t - k)]]


# ---------- silnik decyzyjny ----------
def plan_at(ind, t, risk_pct=1.0):
    """Plan na podstawie świec dziennych do indeksu t włącznie (zamkniętych). Zwraca None, gdy za mało danych."""
    c, cl, k = ind["c"], ind["cl"], ind["k"]
    price, A = cl[t], ind["atr"][t]
    s20, s50, s200, r = ind["sma20"][t], ind["sma50"][t], ind["sma200"][t], ind["rsi"][t]
    if t < 25 or not (price and A and s20 and s50 and s200) or r is None:
        return None
    s50_prev = ind["sma50"][t - 10]
    votes = [price > s200, s50 > s200, price > s50, s20 > s50, bool(s50_prev) and s50 > s50_prev]
    tscore = sum(1 if v else -1 for v in votes)  # od -5 (silny spadek) do +5 (silny wzrost)
    mh, mh_prev = ind["mh"][t], ind["mh"][t - 1]
    piv_lo = _recent_pivots(ind, "piv_lo", t, k)
    piv_hi = _recent_pivots(ind, "piv_hi", t, k)
    win60, win20 = c[max(0, t - 59):t + 1], c[t - 19:t + 1]
    hi60, lo60 = max(x[2] for x in win60), min(x[3] for x in win60)
    hi20, lo20 = max(x[2] for x in win20), min(x[3] for x in win20)
    close_ms = c[t][0] + DAY_MS
    date = datetime.fromtimestamp(c[t][0] / 1000, tz=timezone.utc).date().isoformat()
    nd = px_digits(price)                 # dokładność poziomów cenowych dopasowana do skali ceny aktywa
    rp = lambda x: round(x, nd)           # noqa: E731
    fp = lambda x: _fp(x, 0 if nd <= 1 else nd)   # noqa: E731

    def candidates(side):
        sg = 1 if side == "long" else -1
        lv = [(s20, "SMA20"), (s50, "SMA50")]
        for f in (0.382, 0.5, 0.618):
            lv.append((hi60 - (hi60 - lo60) * f if sg == 1 else lo60 + (hi60 - lo60) * f, f"Fibonacci {f * 100:.1f}%"))
        lv += [(p, "dołek swingowy" if sg == 1 else "szczyt swingowy") for p in (piv_lo if sg == 1 else piv_hi)]
        return [(p, n) for p, n in lv if p and 0.35 * A <= (price - p) * sg <= 3.5 * A]

    def build(side):
        sg = 1 if side == "long" else -1
        cands = candidates(side)
        if not cands:
            return None
        best = None
        for p, _ in cands:
            members = [(q, n) for q, n in cands if abs(q - p) <= 0.5 * A]
            score = len(members) * 10 - abs(price - p) / A * 2
            if best is None or score > best[0]:
                best = (score, members)
        members = best[1]
        entry = rp(statistics.fmean(q for q, _ in members))
        names = list(dict.fromkeys(n for _, n in members))
        swing = [p for p in (piv_lo if sg == 1 else piv_hi) if 0.5 * A <= (entry - p) * sg <= 2.0 * A]
        if swing:
            ref = (max(swing) if sg == 1 else min(swing))
            stop = ref - sg * 0.25 * A
        else:
            stop = entry - sg * 1.2 * A
        edge = min(q for q, _ in members) if sg == 1 else max(q for q, _ in members)
        stop = rp(min(stop, edge - 0.3 * A) if sg == 1 else max(stop, edge + 0.3 * A))
        risk = abs(entry - stop)
        risk_px_pct = risk / entry * 100
        if risk <= 0 or not (0.8 <= risk_px_pct <= 12):
            return None
        goals = sorted(p for p in ([hi20, hi60] + piv_hi if sg == 1 else [lo20, lo60] + piv_lo) if (p - price) * sg >= 0.5 * A)
        if sg == -1:
            goals = goals[::-1]
        d = lambda p: abs(p - entry) / risk
        t1 = next((p for p in goals if 1.2 <= d(p) <= 4.0), None)
        # cel zawsze za bieżącą ceną (inaczej propozycja byłaby "przegapiona" w chwili powstania)
        tp1 = rp(t1) if t1 else rp(entry + sg * max(1.5 * risk, abs(price - entry) + 0.8 * A))
        t2 = next((p for p in goals if (p - tp1) * sg > 0.5 * risk and 2.4 <= d(p) <= 6.0), None)
        tp2 = rp(t2) if t2 else rp(entry + sg * max(2.5, d(tp1) + 1.0) * risk)
        r1, r2 = d(tp1), d(tp2)
        rr = 0.5 * r1 + 0.5 * r2
        # pewność: siła trendu + konfluencja poziomów + strefa RSI + jakość stosunku zysku do ryzyka - zmienność
        conf = 30 + 5 * abs(tscore) + min(16, 8 * (len(members) - 1))
        rs = r if sg == 1 else 100 - r
        conf += 10 if 35 <= rs <= 55 else 4 if 55 < rs <= 68 else 2
        conf += 6 if r1 >= 1.8 else 0
        conf -= 8 if A / price > 0.05 else 0
        mh_turn = mh is not None and mh_prev is not None and (mh > mh_prev) == (sg == 1)
        conf += 4 if mh_turn else 0
        return {"entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "r1": r1, "r2": r2, "rr": rr,
                "risk_px_pct": risk_px_pct, "names": names, "conf": int(max(0, min(95, conf))),
                "members": len(members), "swing_stop": bool(swing), "entry_type": "limit", "setup": "pullback"}

    prev20 = c[t - 20:t]  # 20 świec PRZED bieżącą: poziom wybicia nie zawiera dzisiejszego ruchu
    hp, lp = max(x[2] for x in prev20), min(x[3] for x in prev20)

    def build_breakout(side):
        """Wejście po wybiciu: zlecenie warunkowe tuż za 20-dniowym szczytem (long) lub dołkiem (short), tylko w silnym trendzie."""
        sg = 1 if side == "long" else -1
        if abs(tscore) < 4:
            return None
        level = hp if sg == 1 else lp
        gap = (level - price) * sg  # ile brakuje do poziomu (ujemne = wybicie już się stało, nie gonimy)
        rs = r if sg == 1 else 100 - r
        if not (0.15 * A <= gap <= 1.2 * A) or not (50 <= rs <= 74):
            return None
        entry = rp(level + sg * 0.1 * A)
        risk = min(2.2 * A, max(1.0 * A, (entry - s20) * sg + 0.25 * A))
        stop = rp(entry - sg * risk)
        risk = abs(entry - stop)              # po zaokrągleniu: dokładnie ta odległość, którą rozliczy symulator i trader
        risk_px_pct = risk / entry * 100
        if risk <= 0 or not (0.8 <= risk_px_pct <= 12):
            return None
        tp1, tp2 = rp(entry + sg * 1.5 * risk), rp(entry + sg * 3.0 * risk)
        conf = 25 + 5 * abs(tscore) + (8 if 55 <= rs <= 70 else 3) + (6 if gap <= 0.6 * A else 0)
        conf += 6 if (mh is not None and mh_prev is not None and (mh > mh_prev) == (sg == 1)) else 0
        conf -= 8 if A / price > 0.05 else 0
        return {"entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "r1": 1.5, "r2": 3.0, "rr": 2.25,
                "risk_px_pct": risk_px_pct, "names": [f"wybicie 20-dniowego {'szczytu' if sg == 1 else 'dołka'} {fp(level)}"],
                "conf": int(max(0, min(95, conf))), "members": 1, "swing_stop": False, "entry_type": "stop", "setup": "breakout"}

    side = "long" if (tscore >= 3 and r < 68) else "short" if (tscore <= -3 and r > 32) else None
    setups = []
    if side:
        en = _enabled_setups()
        for kind, fn in (("pullback", build), ("breakout", build_breakout)):
            cand = fn(side) if kind in en else None
            if cand:
                setups.append(cand)
    # wybieramy układ o najwyższej pewności; progi sprawdzamy niżej, a słabszy służy tylko do wyjaśnienia "czekaj"
    setup = max(setups, key=lambda x: x["conf"]) if setups else None

    trend_txt = ("silny trend wzrostowy" if tscore >= 4 else "trend wzrostowy" if tscore >= 3 else
                 "silny trend spadkowy" if tscore <= -4 else "trend spadkowy" if tscore <= -3 else "brak wyraźnego trendu")
    plan = {"action": "wait", "date": date, "candle_ms": c[t][0], "close_ms": close_ms, "price": price, "atr": A,
            "atr_pct": A / price * 100, "trend_score": tscore, "trend": trend_txt, "rsi": r,
            "sma20": s20, "sma50": s50, "sma200": s200, "macd_hist": mh, "reasons": [], "cautions": [],
            "waiting_for": [], "entry_type": None, "setup": None, "entry": None, "stop": None, "tp1": None, "tp2": None, "rr": None, "confidence": None,
            "valid_until_ms": close_ms + PLAN_VALID_BARS * DAY_MS}
    sup = sorted((p for p in [s20, s50, lo20] + piv_lo if p < price), reverse=True)[:3]
    res = sorted(p for p in [hi20] + piv_hi if p > price)[:3]
    plan["supports"], plan["resistances"] = [rp(x) for x in sup], [rp(x) for x in res]

    if setup and setup["conf"] >= PLAN_MIN_CONF and setup["rr"] >= 1.5:
        sg = 1 if side == "long" else -1
        plan.update(action=side, entry=setup["entry"], stop=setup["stop"], tp1=setup["tp1"], tp2=setup["tp2"],
                    rr=round(setup["rr"], 2), r1=round(setup["r1"], 2), r2=round(setup["r2"], 2),
                    confidence=setup["conf"], risk_px_pct=round(setup["risk_px_pct"], 2), entry_note=setup["names"],
                    entry_type=setup["entry_type"], setup=setup["setup"],
                    notional_pct=round(risk_pct / setup["risk_px_pct"] * 100, 1), risk_budget_pct=risk_pct)
        word = "wzrostowy" if sg == 1 else "spadkowy"
        trend_line = f"Trend {word}: {abs(tscore)} z 5 sygnałów zgodnych (cena vs SMA200 i SMA50, SMA50 vs SMA200, SMA20 vs SMA50, nachylenie SMA50)."
        tp_line = f"TP1 {fp(setup['tp1'])} ({setup['r1']:.1f}R, zamknięcie 50%), potem stop na wejście; TP2 {fp(setup['tp2'])} ({setup['r2']:.1f}R)."
        if setup["setup"] == "breakout":
            plan["reasons"] = [
                trend_line,
                f"Wejście po wybiciu: zlecenie warunkowe {'kupna' if sg == 1 else 'sprzedaży'} {fp(setup['entry'])} (tuż za {setup['names'][0]}). "
                f"Cena jest jeszcze {abs(setup['entry'] - price) / price * 100:.1f}% od tego poziomu.",
                f"Stop {fp(setup['stop'])} (za SMA20, ATR {fp(A)}). Ryzyko {setup['risk_px_pct']:.1f}% ceny.",
                tp_line,
                f"RSI {r:.0f}: momentum po stronie trendu, ale jeszcze bez przegrzania.",
            ]
            plan["cautions"].append("Wybicia często okazują się fałszywe: cena potrafi wrócić pod poziom. Sprawdź w teście wstecznym skuteczność tej metody osobno.")
        else:
            plan["reasons"] = [
                trend_line,
                f"Wejście limit {fp(setup['entry'])}: " + ("konfluencja " if setup["members"] > 1 else "poziom ") + ", ".join(setup["names"]) + ".",
                (f"Stop {fp(setup['stop'])} za ostatnim swingiem (ATR {fp(A)})." if setup["swing_stop"]
                 else f"Stop {fp(setup['stop'])} = 1,2 ATR od wejścia (ATR {fp(A)}).") + f" Ryzyko {setup['risk_px_pct']:.1f}% ceny.",
                tp_line,
                f"RSI {r:.0f}: " + ("zdrowe cofnięcie w trendzie." if 35 <= (r if sg == 1 else 100 - r) <= 55 else "poziom akceptowalny, ale nie idealny."),
            ]
        if setup["setup"] == "pullback" and ((r > 60 and sg == 1) or (r < 40 and sg == -1)):
            plan["cautions"].append(f"RSI {r:.0f}: cena może najpierw pójść dalej, zanim dotrze do poziomu wejścia.")
        if A / price > 0.05:
            plan["cautions"].append(f"Wysoka zmienność (ATR {A / price * 100:.1f}% ceny), rozważ mniejszy rozmiar pozycji.")
        if setup["setup"] == "pullback" and setup["members"] == 1:
            plan["cautions"].append("Wejście oparte na pojedynczym poziomie, bez konfluencji.")
        plan["invalidation"] = (f"Zamknięcie dnia {'poniżej' if sg == 1 else 'powyżej'} {fp(setup['stop'])} przed wypełnieniem "
                                f"albo brak {'wybicia' if setup['setup'] == 'breakout' else 'wypełnienia'} do {PLAN_VALID_BARS} dni unieważnia propozycję.")
    else:
        w = plan["reasons"]
        if setup and setup["conf"] < PLAN_MIN_CONF:
            w.append(f"Znaleziono układ {'long' if side == 'long' else 'short'} przy {fp(setup['entry'])}, ale pewność {setup['conf']}% jest poniżej progu {PLAN_MIN_CONF}%.")
        elif side and not setup:
            w.append(f"Jest {trend_txt}, ale nie ma w pobliżu poziomu, od którego dałoby się rozsądnie wejść (stosunek zysku do ryzyka lub odległość stopa).")
        elif abs(tscore) < 3:
            w.append(f"Trend niejednoznaczny ({tscore:+d} na skali od -5 do +5): przewaga żadnej ze stron nie jest wystarczająca.")
        if tscore >= 3 and r >= 68:
            w.append(f"RSI {r:.0f} wskazuje wykupienie: nie ścigamy ceny, czekamy na schłodzenie.")
        if tscore <= -3 and r <= 32:
            w.append(f"RSI {r:.0f} wskazuje wyprzedanie: short przy dnie ma kiepski stosunek ryzyka do zysku.")
        wf = plan["waiting_for"]
        if tscore >= 3:
            near = sorted((p for p, _ in [(s20, 0), (s50, 0)] + [(p, 0) for p in piv_lo] if p and p < price), reverse=True)
            if near:
                wf.append({"price": rp(near[0]), "text": f"cofnięcie do ok. {fp(near[0])} (możliwa strefa kupna w trendzie wzrostowym)"})
        elif tscore <= -3:
            near = sorted(p for p in [s20, s50] + piv_hi if p and p > price)
            if near:
                wf.append({"price": rp(near[0]), "text": f"odbicie do ok. {fp(near[0])} (możliwa strefa sprzedaży w trendzie spadkowym)"})
        else:
            wf.append({"price": rp(hi20), "text": f"zamknięcie dnia powyżej {fp(hi20)} (wybicie górą z 20 dni)"})
            wf.append({"price": rp(lo20), "text": f"zamknięcie dnia poniżej {fp(lo20)} (wybicie dołem z 20 dni)"})
    return plan


def _entry_gap(plan, price):
    """Ile (w cenie, ze znakiem dodatnim) brakuje cenie do poziomu wejścia: limit czeka na cofnięcie, zlecenie warunkowe na wybicie."""
    sg = 1 if plan["action"] == "long" else -1
    return (price - plan["entry"]) * sg if plan.get("entry_type") != "stop" else (plan["entry"] - price) * sg


def decorate_plan(plan, live_price):
    """Stan planu względem bieżącej ceny (opis, a także bramka tworzenia propozycji w dzienniku)."""
    if not plan or not live_price:
        return plan
    plan["live_price"] = live_price
    if plan["action"] == "wait":
        return plan
    sg = 1 if plan["action"] == "long" else -1
    e, A = plan["entry"], plan["atr"]
    stop_entry = plan.get("entry_type") == "stop"
    gap = _entry_gap(plan, live_price)
    plan["distance_pct"] = (live_price / e - 1) * 100
    if (live_price - plan["stop"]) * sg <= 0:
        st, txt = "invalidated", "Cena jest już za poziomem stopa: propozycja nieaktualna."
    elif (live_price - plan["tp1"]) * sg >= 0:
        st, txt = "missed", "Cena dotarła już do TP1 bez wejścia: nie goń, poczekaj na nową propozycję."
    elif gap <= 0:
        st, txt = "through", ("Cena przebiła już poziom wybicia: nie gonimy ruchu, czekamy na nową propozycję." if stop_entry else
                              "Cena jest już na poziomie wejścia lub za nim: limit mógłby się wypełnić od razu, nie powielamy tej propozycji.")
    elif gap <= 0.35 * A:
        st, txt = "near", "Cena jest tuż przy poziomie wejścia."
    else:
        word = ("poniżej" if sg == 1 else "powyżej") if stop_entry else ("powyżej" if sg == 1 else "poniżej")
        st, txt = "waiting", f"Cena jest {abs(plan['distance_pct']):.1f}% {word} poziomu wejścia: zlecenie {'warunkowe' if stop_entry else 'limit'} czeka."
    plan["live_state"], plan["live_text"] = st, txt
    return plan


# ---------- symulator pojedynczej propozycji ----------
def simulate_trade(p, candles, interval_ms, fee_pct=PLAN_FEE_PCT, last_price=None, funding_pct_day=None, stop_slip_pct=None):
    """Rozlicza propozycję na ZAKOŃCZONYCH świecach (rosnąco). Jest wznawialny: stan (status, stop, zrealizowane R)
    zostaje w propozycji, a kolejne wywołania przetwarzają tylko nowe świece (ts >= resolved_ms).
    Zawsze zakłada najgorszy przebieg w obrębie świecy: w świecy wypełnienia liczy się tylko stop (cele dopiero od
    następnej), a stop jest sprawdzany przed celem. Wynik w R (1R = odległość wejścia od stopa), po kosztach.
    TP1 zamyka 50% i przesuwa stop na wejście, TP2 zamyka resztę.
    Wejście typu 'stop' (po wybiciu) to zlecenie rynkowe po przebiciu poziomu: wypełnia się po gorszej z cen
    (poziom lub otwarcie świecy) i ma koszt 1,5x wyższy.
    Koszty: prowizja rundy (fee_pct), funding longa za każdą dobę w pozycji (funding_pct_day) oraz poślizg zlecenia stop
    (stop_slip_pct; przy luce na otwarciu świecy stop wypełnia się po cenie otwarcia). None = stałe PLAN_*."""
    long_ = p["side"] == "long"
    sg = 1 if long_ else -1
    stop_entry = p.get("entry_type") == "stop"
    E, S, T1, T2 = p["entry"], p["stop"], p["tp1"], p["tp2"]
    risk = abs(E - S)
    if risk <= 0:       # wadliwa propozycja (wejście równe stopowi): nie da się jej rozliczyć, zamykamy bez wyniku
        return {"status": "closed", "outcome": "invalidated", "r": None, "unrealized_r": None}
    fx = p.get("fill_px") or E
    fund_pct = PLAN_FUNDING_PCT_DAY if funding_pct_day is None else funding_pct_day
    slip_pct = PLAN_STOP_SLIP_PCT if stop_slip_pct is None else stop_slip_pct

    def levels():
        k = fx / risk / 100           # 1% nominału wyrażony w R
        return (max(0.1, (T1 - fx) * sg / risk), max(0.1, (T2 - fx) * sg / risk),
                fee_pct * (1.5 if stop_entry else 1.0) * k, slip_pct * k, (fund_pct * k if long_ else 0.0))

    r1, r2, fee_r, slip_r, fund_r = levels()
    st = {k: p.get(k) for k in ("status", "outcome", "r", "filled_ms", "closed_ms", "resolved_ms")}
    st.update(tp1_hit=bool(p.get("tp1_hit")), mfe_r=p.get("mfe_r") or 0.0, mae_r=p.get("mae_r") or 0.0,
              status=p.get("status") or "pending")
    stop, realized, remaining = p.get("stop_cur", S), p.get("realized", 0.0), p.get("remaining", 1.0)
    start_ms, last_close = max(p["created_ms"], p.get("resolved_ms") or 0), p.get("last_close")

    def close(outcome, gross, ms, stop_part=0.0):
        """gross: wynik w R przed kosztami (None = propozycja niewypełniona); stop_part: część pozycji zamknięta zleceniem stop."""
        r = None
        if gross is not None:
            days = max(0.0, (ms - st["filled_ms"]) / DAY_MS) if st.get("filled_ms") is not None else 0.0
            r = round(gross - fee_r - stop_part * slip_r - fund_r * days, 3)
        st.update(status="closed", outcome=outcome, r=r, closed_ms=ms)

    for cd in candles:
        ts, o, h, l, cl = cd[0], cd[1], cd[2], cd[3], cd[4]
        if ts < start_ms:
            continue
        end = ts + interval_ms
        st["resolved_ms"], last_close = end, cl
        if st["status"] == "pending":
            if ts >= p["valid_until_ms"]:
                close("expired", None, p["valid_until_ms"])
                break
            if stop_entry:
                touched = (h > E) if long_ else (l < E)
            else:
                touched = (l < E) if long_ else (h > E)
            if not touched:
                if stop_entry:
                    if (l <= S) if long_ else (h >= S):
                        close("invalidated", None, end)
                        break
                elif (h >= T1) if long_ else (l <= T1):
                    close("missed", None, end)
                    break
                continue
            if stop_entry:
                fx = max(E, o) if long_ else min(E, o)
                st["fill_px"] = fx
                r1, r2, fee_r, slip_r, fund_r = levels()
            st.update(status="open", filled_ms=ts)
            if (l <= S) if long_ else (h >= S):
                close("sl", sg * (S - fx) / risk, end, 1.0)      # -1R przy wypełnieniu po poziomie, gorzej po wybiciu z luką
                break
            st["mae_r"] = max(0.0, ((fx - l) if long_ else (h - fx)) / risk)
            continue
        st["mae_r"] = max(st["mae_r"], ((fx - l) if long_ else (h - fx)) / risk)
        st["mfe_r"] = max(st["mfe_r"], ((h - fx) if long_ else (fx - l)) / risk)
        if (l <= stop) if long_ else (h >= stop):
            px = min(stop, o) if long_ else max(stop, o)          # luka: świeca otwarta już za stopem wypełnia go po cenie otwarcia
            close("tp1_be" if st["tp1_hit"] else "sl", realized + remaining * sg * (px - fx) / risk, end, remaining)
            break
        if not st["tp1_hit"] and ((h >= T1) if long_ else (l <= T1)):
            st["tp1_hit"], realized, remaining, stop = True, realized + 0.5 * r1, 0.5, fx
        if st["tp1_hit"] and ((h >= T2) if long_ else (l <= T2)):
            close("tp2", realized + 0.5 * r2, end)
            break
        if end - st["filled_ms"] >= p["max_hold_ms"]:
            close("time_stop", realized + remaining * sg * (cl - fx) / risk, end)
            break
    if p.get("fill_px") and "fill_px" not in st:
        st["fill_px"] = p["fill_px"]
    st.update(stop_cur=stop, realized=realized, remaining=remaining, last_close=last_close)
    if st["status"] == "open":
        px = last_price or last_close
        if px:
            st["unrealized_r"] = round(realized + remaining * sg * (px - fx) / risk, 3)
    else:
        st["unrealized_r"] = None
    return st


# ---------- dziennik ----------
def new_journal(symbol):
    return {"version": JOURNAL_VERSION, "symbol": symbol, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "proposals": [], "log": [], "backtest": None}


def load_journal(path, symbol, warnings):
    try:
        with open(path, encoding="utf-8") as fh:
            j = json.load(fh)
        if not isinstance(j, dict) or j.get("version") != JOURNAL_VERSION or not isinstance(j.get("proposals"), list):
            raise ValueError("nieznany format")
        j.setdefault("log", [])
        return j
    except FileNotFoundError:
        return new_journal(symbol)
    except (ValueError, OSError) as e:
        try:
            os.replace(path, path + ".bak")
            warnings.append(f"Dziennik {os.path.basename(path)} był uszkodzony ({e}); kopia w .bak, zaczynam nowy.")
        except OSError:
            warnings.append(f"Dziennik {os.path.basename(path)} jest nieczytelny ({e}).")
        return new_journal(symbol)


def save_journal(path, j):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(j, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


class JournalLock:
    """Prosta blokada plikiem: dwa równoległe uruchomienia crona nie nadpiszą sobie dziennika."""

    def __init__(self, path, stale_s=600):
        self.path, self.stale_s, self.held = path + ".lock", stale_s, False

    def __enter__(self):
        for _ in range(2):
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.stale_s:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                return self
            except OSError:
                return self
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass


def _first_idx(candles, created_ms):
    i = len(candles)
    while i > 0 and candles[i - 1][0] >= created_ms:
        i -= 1
    return i


def journal_step(j, plan, candles, interval_ms, now_ms, last_price=None, allow_create=True, fee_pct=PLAN_FEE_PCT):
    """Jeden krok silnika dziennika: rozlicz aktywne propozycje, potem zastosuj bieżący plan.
    Używany tak samo na żywo (świece 15 min) i w teście wstecznym (świece dzienne)."""
    props = j["proposals"]
    for p in props:
        if p["status"] in ("pending", "open"):
            lo = _first_idx(candles, max(p["created_ms"], p.get("resolved_ms") or 0))
            p.update(simulate_trade(p, candles[lo:], interval_ms, fee_pct, last_price))
            if p["status"] == "closed" and not p.get("closed_ms"):
                p["closed_ms"] = now_ms
    if plan is None:
        return
    active = [p for p in props if p["status"] in ("pending", "open")]
    pending = [p for p in active if p["status"] == "pending"]
    opened = [p for p in active if p["status"] == "open"]

    def cancel(p, why):
        p.update(status="closed", outcome=why, closed_ms=now_ms)

    if plan["action"] == "wait":
        for p in pending:
            cancel(p, "withdrawn")
    else:
        side = plan["action"]
        create = True
        for p in pending:
            if p["side"] == side and abs(p["entry"] - plan["entry"]) < 0.5 * plan["atr"]:
                p["valid_until_ms"] = min(max(p["valid_until_ms"], plan["valid_until_ms"]), p["created_ms"] + 6 * DAY_MS)
                p["last_confirmed_ms"] = now_ms
                create = False
            else:
                cancel(p, "replaced")
        if opened:
            create = False  # nie dokładamy do otwartej pozycji
        # jeden dzienny plan = najwyżej jedna propozycja (po "przegapieniu" nie powielamy jej co godzinę)
        cm = plan.get("candle_ms")
        for q in reversed(props):       # lista rośnie po czasie utworzenia; propozycja starsza niż świeca planu nie może mieć jego daty
            if cm is not None and q["created_ms"] < cm:
                break
            if q.get("plan_date") == plan["date"] and q["side"] == side and abs(q["entry"] - plan["entry"]) < 0.5 * plan["atr"]:
                create = False
                break
        if create and not allow_create:
            create = False  # cena jest już poza planem albo limit wypełniłby się od razu po rynku
        if create and not abs(plan["entry"] - plan["stop"]) > 0:
            create = False  # plan bez odległości do stopa nie jest propozycją
        if create:
            n = sum(1 for p in props if p.get("plan_date") == plan["date"] and p["side"] == side) + 1
            props.append({"id": f"{plan['date']}-{side}-{n}", "created_ms": now_ms, "side": side, "entry": plan["entry"],
                          "stop": plan["stop"], "tp1": plan["tp1"], "tp2": plan["tp2"], "rr": plan["rr"],
                          "confidence": plan["confidence"], "atr": plan["atr"], "plan_date": plan["date"],
                          "entry_type": plan.get("entry_type") or "limit", "setup": plan.get("setup") or "pullback",
                          "valid_until_ms": plan["valid_until_ms"], "max_hold_ms": (plan.get("max_hold_bars") or PLAN_MAX_HOLD_BARS) * DAY_MS,
                          "why": plan.get("entry_note"), "status": "pending", "outcome": None, "r": None,
                          "filled_ms": None, "closed_ms": None, "tp1_hit": False, "mfe_r": 0.0, "mae_r": 0.0,
                          "last_confirmed_ms": now_ms})
    key = (plan["date"], plan["action"], round(plan["entry"] or 0))
    if not j["log"] or j["log"][-1].get("k") != list(key):
        j["log"].append({"k": list(key), "ms": now_ms, "date": plan["date"], "action": plan["action"], "entry": plan["entry"],
                         "stop": plan["stop"], "tp1": plan["tp1"], "confidence": plan["confidence"], "price": plan["price"]})
    del j["log"][:-300]
    if len(props) > 500:  # trzymamy ostatnie 500, aktywnych nigdy nie usuwamy
        keep = [p for p in props if p["status"] != "closed"] + [p for p in props if p["status"] == "closed"][-450:]
        j["proposals"] = sorted(keep, key=lambda p: p["created_ms"])


def _agg(rs):
    n = len(rs)
    if not n:
        return {"n": 0}
    wins, losses = [x for x in rs if x > 0], [x for x in rs if x < 0]
    return {"n": n, "win_rate_pct": len(wins) / n * 100, "avg_r": sum(rs) / n, "total_r": sum(rs),
            "profit_factor": sum(wins) / -sum(losses) if losses else None}


def journal_stats(props, recent=20, only_filled=False):
    done = sorted((p for p in props if p.get("outcome") in PLAN_FILLED and p.get("r") is not None), key=lambda p: p["closed_ms"])
    rs = [p["r"] for p in done]
    st = _agg(rs)
    cnt = lambda outs: sum(1 for p in props if p.get("outcome") in outs)
    filled_n, nf = len(done), cnt(PLAN_NOT_FILLED)
    st.update(not_filled=nf, cancelled=cnt(PLAN_CANCELLED),
              fill_rate_pct=filled_n / (filled_n + nf) * 100 if filled_n + nf else None,
              pending=sum(1 for p in props if p["status"] == "pending"), open=sum(1 for p in props if p["status"] == "open"))
    if done:
        cum = peak = dd = 0.0
        for x in rs:
            cum += x
            peak = max(peak, cum)
            dd = min(dd, cum - peak)
        st["max_drawdown_r"] = dd
        st["avg_hold_days"] = statistics.fmean((p["closed_ms"] - p["filled_ms"]) / DAY_MS for p in done if p.get("filled_ms"))
        st["best_r"], st["worst_r"] = max(rs), min(rs)
    st["by_setup"] = {k: _agg([p["r"] for p in done if (p.get("setup") or "pullback") == k]) for k in ("pullback", "breakout")}
    st["by_side"] = {s: _agg([p["r"] for p in done if p["side"] == s]) for s in ("long", "short")}
    buckets = (("55-64", 0, 65), ("65-74", 65, 75), ("75+", 75, 101))
    st["by_conf"] = [{"label": lb, **_agg([p["r"] for p in done if lo <= (p.get("confidence") or 0) < hi])} for lb, lo, hi in buckets]
    daily = {}
    for p in done:
        d = datetime.fromtimestamp(p["closed_ms"] / 1000, tz=timezone.utc).date()
        daily[d] = daily.get(d, 0.0) + p["r"]
    curve = []
    if daily:
        cum = 0.0
        curve = [{"d": (min(daily) - timedelta(days=1)).isoformat(), "v": 0.0}]
        for d in sorted(daily):
            cum += daily[d]
            curve.append({"d": d.isoformat(), "v": round(cum, 3)})
        if len(curve) > 160:  # nie rozdymamy JSON-a
            step = math.ceil(len(curve) / 160)
            curve = curve[::step] + ([curve[-1]] if (len(curve) - 1) % step else [])
    st["curve"] = curve
    st["recent"] = [{k: p.get(k) for k in ("id", "created_ms", "side", "entry", "stop", "tp1", "tp2", "confidence", "status",
                                           "outcome", "r", "filled_ms", "closed_ms", "unrealized_r", "tp1_hit", "why", "setup", "entry_type")}
                    for p in sorted((p for p in props if not only_filled or p.get("outcome") in PLAN_FILLED), key=lambda p: p["created_ms"], reverse=True)[:recent]]
    return st


def run_backtest(candles, risk_pct=1.0, fee_pct=PLAN_FEE_PCT):
    """Walk-forward: każdego dnia ta sama funkcja planu widzi tylko świece do tego dnia, a propozycje przechodzą
    przez ten sam silnik dziennika co na żywo. Parametrów nie dobierano pod ten okres."""
    n = len(candles)
    if n < PLAN_WARMUP + 40:
        return None
    ind, j = build_indicators(candles), new_journal("backtest")
    t0 = PLAN_WARMUP
    for t in range(t0, n):
        plan = plan_at(ind, t, risk_pct)
        journal_step(j, plan, candles[:t + 1], DAY_MS, candles[t][0] + DAY_MS, fee_pct=fee_pct)
    st = journal_stats(j["proposals"], recent=15, only_filled=True)
    d0 = datetime.fromtimestamp(candles[t0][0] / 1000, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(candles[-1][0] / 1000, tz=timezone.utc).date()
    st.update(asof=d1.isoformat(), from_date=d0.isoformat(), days=n - t0, signals=len(j["proposals"]),
              buy_hold_pct=(candles[-1][4] / candles[t0][4] - 1) * 100, fee_pct=fee_pct)
    # pełna lista rozliczonych transakcji [zamknięcie_ms, wynik_R, ryzyko_ceny_%, strona, setup]: surowiec dla laboratorium planów
    st["trades"] = [[p["closed_ms"], round(p["r"], 4), round(abs(p["entry"] - p["stop"]) / p["entry"] * 100, 3), p["side"], p.get("setup") or "pullback"]
                    for p in sorted((p for p in j["proposals"] if p.get("outcome") in PLAN_FILLED and p.get("r") is not None), key=lambda p: p["closed_ms"])]
    return st


# ---------- rozkład zleceń po cenach ----------
def _nice_step(raw):
    if not raw or raw <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(raw))
    f = raw / p
    return (1 if f < 1.5 else 2 if f < 3.5 else 5 if f < 7.5 else 10) * p


def _bin_rows(points, step, include=()):
    """points: (cena, klucz, ilość) -> wiersze od najwyższej ceny, bez dziur w osi (puste przedziały też są zwracane).
    include: dodatkowe ceny, które muszą zmieścić się w zakresie (np. bieżąca cena)."""
    rows = {}
    for price, key, qty in points:
        b = math.floor(price / step)
        r = rows.setdefault(b, {"lo": b * step, "hi": (b + 1) * step})
        r[key + "_n"] = r.get(key + "_n", 0) + 1
        r[key + "_q"] = r.get(key + "_q", 0.0) + qty
    if not rows:
        return []
    keys = list(rows) + [math.floor(x / step) for x in include if x]
    lo_b, hi_b = min(keys), max(keys)
    if hi_b - lo_b <= 60:
        for b in range(lo_b, hi_b + 1):
            rows.setdefault(b, {"lo": b * step, "hi": (b + 1) * step})
    return [rows[b] for b in sorted(rows, reverse=True)]


def summarize_flow(mb, execs, orders, price, privacy, symbol):
    full = privacy == "full"
    out = {"symbol": symbol, "price": price, "own": None, "book": None, "trades": None}
    base = symbol[:3]
    # --- Moje transakcje (podpisane dane; w trybie masked tylko liczby, bez cen)
    if execs is not None:
        tr = [e for e in execs if str(e.get("execType", "Trade")) == "Trade"]
        mine = [e for e in tr if str(e.get("symbol", "")).startswith(base)]
        buys, sells = [e for e in mine if e.get("side") == "Buy"], [e for e in mine if e.get("side") == "Sell"]
        q = lambda e: num(e.get("execQty")) or 0.0
        px = lambda e: num(e.get("execPrice")) or 0.0
        own = {"privacy": privacy, "days": BYBIT_EXEC_DAYS, "buy_n": len(buys), "sell_n": len(sells), "other_n": len(tr) - len(mine),
               "maker_pct": sum(1 for e in mine if e.get("isMaker") in (True, "true", 1, "1")) / len(mine) * 100 if mine else None,
               "bins": None, "step": None}
        if full and mine:
            bq, sq = sum(q(e) for e in buys), sum(q(e) for e in sells)
            own.update(buy_qty=bq, sell_qty=sq,
                       avg_buy=sum(px(e) * q(e) for e in buys) / bq if bq else None,
                       avg_sell=sum(px(e) * q(e) for e in sells) / sq if sq else None)
            pts = [(px(e), "buy" if e.get("side") == "Buy" else "sell", q(e)) for e in mine if px(e)]
            pend = [(num(o.get("price")), "pend_buy" if o.get("side") == "Buy" else "pend_sell", num(o.get("qty")) or 0.0)
                    for o in orders or [] if str(o.get("symbol", "")).startswith(base) and o.get("price")]
            allp = [p for p, _, _ in pts + pend] + ([price] if price else [])
            step = _nice_step((max(allp) - min(allp)) / 18)
            own["step"], own["bins"] = step, _bin_rows(pts + [(p, k, qq) for p, k, qq in pend if p], step, [price])
        out["own"] = own
    # --- Księga zleceń (publiczna)
    if mb and mb.get("book"):
        bids = [(num(x[0]), num(x[1])) for x in mb["book"].get("b") or [] if len(x) > 1]
        asks = [(num(x[0]), num(x[1])) for x in mb["book"].get("a") or [] if len(x) > 1]
        allp = [p for p, _ in bids + asks if p]
        if allp:
            step = _nice_step((max(allp) - min(allp)) / 14)
            pts = [(p, "buy", s) for p, s in bids if p and s] + [(p, "sell", s) for p, s in asks if p and s]
            bq, aq = sum(s for _, s in bids if s), sum(s for _, s in asks if s)
            mid = price or statistics.fmean(allp)
            out["book"] = {"step": step, "bins": _bin_rows(pts, step), "buy_q": bq, "sell_q": aq,
                           "imbalance_pct": (bq - aq) / (bq + aq) * 100 if bq + aq else None,
                           "span_pct": (max(allp) - min(allp)) / mid * 100 if mid else None, "levels": len(bids) + len(asks)}
    # --- Ostatnie transakcje publiczne (agresor kupujący = Buy)
    if mb and mb.get("trades"):
        tt = [(num(x.get("price")), x.get("side"), num(x.get("size")), num(x.get("time"))) for x in mb["trades"]]
        tt = [x for x in tt if x[0] and x[2] and x[1] in ("Buy", "Sell")]
        if tt:
            lo, hi = min(x[0] for x in tt), max(x[0] for x in tt)
            step = _nice_step((hi - lo) / 12) if hi > lo else 1.0
            ts = [x[3] for x in tt if x[3]]
            out["trades"] = {"step": step, "bins": _bin_rows([(p, "buy" if s == "Buy" else "sell", z) for p, s, z, _ in tt], step),
                             "buy_n": sum(1 for x in tt if x[1] == "Buy"), "sell_n": sum(1 for x in tt if x[1] == "Sell"),
                             "buy_q": sum(x[2] for x in tt if x[1] == "Buy"), "sell_q": sum(x[2] for x in tt if x[1] == "Sell"),
                             "minutes": (max(ts) - min(ts)) / 60000 if len(ts) > 1 else None}
    return out


# ---------- całość: uruchamiane po analyze() ----------
BT_VERSION = 3   # zmiana wymusza ponowne policzenie testu wstecznego zapisanego w dzienniku (3: funding, poślizg stopa, zaokrąglenia cen)


def run_planner(data, a, privacy="masked", journal_path=None, write=True, now_ms=None, risk_pct=None, fee_pct=None, notifier=None, lab=True):
    now_ms = now_ms or int(time.time() * 1000)
    risk_pct = risk_pct or float(os.environ.get("PLAN_RISK_PCT", "1") or 1)
    fee_pct = PLAN_FEE_PCT if fee_pct is None else fee_pct
    mb = data.get("bybit_market")
    out = {"status": "unavailable", "reason": None, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "params": {"min_conf": PLAN_MIN_CONF, "valid_bars": PLAN_VALID_BARS, "max_hold_bars": PLAN_MAX_HOLD_BARS,
                      "fee_pct": fee_pct, "risk_pct": risk_pct, "warmup": PLAN_WARMUP}}
    if mb is None:
        out["reason"] = "Planer wyłączony (--bybit off)."
        out["status"] = "off"
        return out
    symbol = mb.get("symbol", "BTCUSDT")
    out["symbol"] = symbol
    candles = [c for c in (mb.get("candles_d") or []) if c[0] + DAY_MS <= now_ms]
    if len(candles) < PLAN_WARMUP + 40:
        out["reason"] = ("Brak świec dziennych z Bybit (" + "; ".join(mb.get("errors") or ["pusta odpowiedź"]) + ")."
                         if not candles else f"Za mało świec dziennych ({len(candles)}); potrzeba co najmniej {PLAN_WARMUP + 40}.")
        out["flow"] = summarize_flow(mb, (data.get("bybit") or {}).get("executions"), (data.get("bybit") or {}).get("open_orders"),
                                     num((mb.get("ticker") or {}).get("lastPrice")), privacy, symbol)
        return out
    live = num((mb.get("ticker") or {}).get("lastPrice")) or a.get("last")
    # długa historia (cache btc_history.json) daje więcej transakcji w teście wstecznym; plan na dziś liczymy z tych samych świec
    hist = [c for c in (mb.get("candles_long") or []) if c[0] + DAY_MS <= now_ms]
    if len(hist) <= len(candles) or hist[-1][0] != candles[-1][0]:
        hist = candles
    ind = build_indicators(hist)
    plan = decorate_plan(plan_at(ind, len(hist) - 1, risk_pct), live)
    if plan is None:
        out["reason"] = "Za mało danych do obliczenia wskaźników."
        return out
    out.update(status="ok", price=live, candles=len(candles), source="Bybit kline (dzienne, zakończone)", plan=plan)
    if plan["action"] != "wait" and plan.get("notional_pct"):
        plan["leverage_x"] = round(plan["notional_pct"] / 100, 2)

    # kontekst: instrumenty pochodne i istniejące pozycje (tylko opis, nie zmienia planu ani wyników testu)
    b, notes = a.get("bybit") or {}, []
    mk = b.get("market") or {}
    if plan["action"] == "long" and mk.get("funding_pct") is not None and mk["funding_pct"] > 0.03:
        notes.append(f"Funding {mk['funding_pct']:.3f}% jest wysoki: trzymanie longa kosztuje.")
    if plan["action"] == "short" and mk.get("funding_pct") is not None and mk["funding_pct"] < -0.02:
        notes.append(f"Funding {mk['funding_pct']:.3f}% jest ujemny: trzymanie shorta kosztuje.")
    for p in b.get("positions") or []:
        if str(p.get("symbol", "")).startswith(symbol[:3]):
            same = (p.get("side", "").lower() == plan["action"])
            if plan["action"] == "wait":
                notes.append(f"Masz otwartą pozycję {p.get('side')} na {p.get('symbol')}: plan analizatora na dziś to czekanie, pilnuj istniejących SL i TP.")
            else:
                notes.append(f"Masz już otwartą pozycję {p.get('side')} na {p.get('symbol')}: " +
                             ("nowa propozycja zwiększyłaby ekspozycję." if same else "propozycja jest przeciwna do niej."))
    out["context"] = {"funding_pct": mk.get("funding_pct"), "oi_change_pct": mk.get("oi_change_pct"), "long_pct": mk.get("long_pct"),
                      "notes": notes}

    # dziennik (pamięć propozycji) i ich rozliczenie na świecach 15 min
    warnings = data.setdefault("warnings", [])
    journal_path = journal_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc_journal.json")
    c15 = [c for c in (mb.get("candles_15") or []) if c[0] + 900_000 <= now_ms]
    with JournalLock(journal_path) as lock:
        j = load_journal(journal_path, symbol, warnings)
        can_write = write and lock.held
        if write and not lock.held:
            warnings.append("Dziennik jest właśnie używany przez inne uruchomienie: ten raport go nie zapisze.")
        if j.get("symbol") != symbol and not j["proposals"]:
            j["symbol"] = symbol
        allow = True
        if live and plan["action"] != "wait":
            sg_ = 1 if plan["action"] == "long" else -1
            allow = plan.get("live_state") in ("waiting", "near") and _entry_gap(plan, live) >= 0.2 * plan["atr"]
        if not c15 and any(p["status"] in ("pending", "open") for p in j["proposals"]):
            warnings.append("Brak świec 15-minutowych z Bybit: propozycje z dziennika nie zostały teraz rozliczone.")
        else:
            journal_step(j, plan, c15, 900_000, now_ms, last_price=live, allow_create=allow, fee_pct=fee_pct)
        # powiadomienia: po rozliczeniu propozycji, przed zapisem, więc stan „co już wysłano” trafia do dziennika razem z nimi
        nsum = None
        if notifier is not None and notifier.channels and can_write:
            try:
                nsum = process_notifications(j, notifier, now_ms, live, symbol)
                warnings += [f"Powiadomienia ({x.split(':', 1)[0]}): " + x.split(':', 1)[-1].strip() for x in nsum["errors"]]
            except Exception as e:
                warnings.append(f"Powiadomienia: {type(e).__name__}: {_scrub(e, notifier.secrets())}")
        bt = j.get("backtest")
        last_day = datetime.fromtimestamp(hist[-1][0] / 1000, tz=timezone.utc).date().isoformat()
        setups_key = ",".join(_enabled_setups())
        if (not bt or bt.get("asof") != last_day or bt.get("fee_pct") != fee_pct or bt.get("v") != JOURNAL_VERSION
                or bt.get("setups") != setups_key or bt.get("bars") != len(hist) or bt.get("bt_v") != BT_VERSION):
            bt = run_backtest(hist, risk_pct, fee_pct)
            if bt:
                bt.update(v=JOURNAL_VERSION, setups=setups_key, bars=len(hist), bt_v=BT_VERSION)
            j["backtest"] = bt
        lab_data = j.get("lab")
        if lab:
            lkey = [LAB_VERSION, last_day, len(hist), setups_key, LAB_COST_PCT, risk_pct]
            if not lab_data or lab_data.get("key") != lkey:
                lab_data = run_lab(hist, (bt or {}).get("trades"), risk_pct, atr_pct=plan.get("atr_pct"))
                if lab_data:
                    lab_data["key"] = lkey
                j["lab"] = lab_data
        else:
            lab_data = None
        # zmiany nastawienia planów i bliskość likwidacji: stan bazowy zapisujemy zawsze, wysyłamy tylko przy włączonym kanale
        liq_live = None
        if can_write:
            st_n, sm = j.setdefault("notify", {}), {"sent": 0, "failed": 0, "errors": []}
            nt_ = notifier if notifier is not None else Notifier({})
            try:
                process_regime(st_n, nt_, lab_data, now_ms, live, symbol, sm)
                if notifier is not None and notifier.channels and "liq" in notifier.events:
                    liq_live = process_liq(st_n, notifier, (data.get("bybit") or {}).get("positions"), now_ms, sm)
            except Exception as e:
                warnings.append(f"Powiadomienia: {type(e).__name__}: {_scrub(e, nt_.secrets())}")
            warnings += [f"Powiadomienia ({x.split(':', 1)[0]}): " + x.split(':', 1)[-1].strip() for x in sm["errors"]]
            if sm["sent"] or sm["failed"]:
                nsum = nsum or {"sent": 0, "failed": 0, "errors": []}
                nsum["sent"] += sm["sent"]
                nsum["failed"] += sm["failed"]
        j["last_run_ms"] = now_ms
        write_ok = None
        if can_write:
            try:
                save_journal(journal_path, j)
                write_ok = True
            except OSError as e:
                write_ok = False
                warnings.append(f"Nie udało się zapisać dziennika ({e}).")
    stats = journal_stats(j["proposals"], recent=20)
    active = next((p for p in reversed(j["proposals"]) if p["status"] in ("pending", "open")), None)
    out["journal"] = {"stats": stats, "active": {k: active.get(k) for k in ("id", "created_ms", "side", "entry", "stop", "tp1", "tp2",
                      "confidence", "status", "filled_ms", "valid_until_ms", "unrealized_r", "tp1_hit")} if active else None,
                      "log": j["log"][-12:], "since": j.get("created"), "write_ok": write_ok, "proposals_total": len(j["proposals"])}
    out["backtest"] = {k: v for k, v in (j.get("backtest") or {}).items() if k != "trades"} or None
    if lab_data:
        out["lab"] = {k: v for k, v in lab_data.items() if k != "key"}
        for lp in out["lab"]["plans"]:
            if lp["group"] == "analyzer":
                lp["today"] = {"action": plan["action"]}
    else:
        out["lab"] = None
    ns = j.get("notify") or {}
    nreason = (None if notifier is not None and notifier.channels and can_write else
               "wyłączone (--notify off)" if notifier is None else
               "próbny przebieg (--no-journal)" if notifier.channels and not can_write else
               "brak konfiguracji w .env")
    out["notify"] = {"channels": notifier.channels if notifier is not None and can_write else [], "events": notifier.events if notifier is not None else [],
                     "reason": nreason, "last_ok_ms": ns.get("last_ok_ms"), "sent_total": ns.get("sent_total", 0),
                     "last_error": ns.get("last_error"), "sent_now": nsum["sent"] if nsum else 0,
                     "liq": ({"warn": notifier.liq_warn, "crit": notifier.liq_crit, "watched": len(liq_live),
                              "nearest_pct": min((x["dist"] for x in liq_live), default=None)}
                             if liq_live is not None else None)}
    b_raw = data.get("bybit") or {}
    out["flow"] = summarize_flow(mb, b_raw.get("executions"), b_raw.get("open_orders"), live, privacy, symbol)
    return out


# ==========================================================================
# Laboratorium planów: porównanie strategii długoterminowych i ich ryzyka
# ==========================================================================
LAB_VERSION = 1
LAB_START = PLAN_WARMUP            # wspólny początek dla wszystkich planów: po rozgrzewce SMA200, jak w teście wstecznym
LAB_COST_PCT = 0.10                # koszt jednej transakcji (prowizja + poślizg), % obrotu
LAB_VOL_TARGET = 0.40              # cel zmienności rocznej dla planu "cel zmienności"
LAB_BAND = 0.10                    # plan z celem zmienności przelicza ekspozycję dopiero po zmianie o co najmniej 10 pkt proc.
LAB_MC_SIMS, LAB_MC_BLOCK, LAB_MC_DAYS = 1000, 21, 365
LAB_PROFILES = (0.5, 1.0, 2.0)     # ryzyko na transakcję planu analizatora, % kapitału
LAB_DCA_WEEKLY = 100.0
LAB_POSITION_PLANS = (
    ("hold", "Kup i trzymaj", "Cały kapitał w BTC od początku do końca okresu. Punkt odniesienia dla pozostałych planów."),
    ("trend200", "Trend SMA200", "W BTC, gdy cena zamyka się powyżej średniej z 200 dni; poza tym gotówka. Klasyczny filtr trendu."),
    ("cross", "Krzyżowanie 50/200", "W BTC, gdy średnia z 50 dni leży powyżej średniej z 200 dni (złoty krzyż); poza tym gotówka."),
    ("volt", "Cel zmienności", "Zawsze w rynku, ale ekspozycja maleje, gdy zmienność z 30 dni rośnie ponad 40% rocznie (nigdy powyżej 100%, bez dźwigni)."),
    ("trendvolt", "Trend + cel zmienności", "Połączenie: poza rynkiem pod SMA200, a nad nią ekspozycja skalowana zmiennością."),
)
LAB_ANALYZER_DESC = ("Plan analizatora (propozycje z SL i TP) na zrealizowanych transakcjach z testu wstecznego; {r:g}% kapitału ryzyka na transakcję, procent składany. "
                     "Wynik zależy od wybranego ryzyka, więc z planami pozycyjnymi porównuj raczej Calmar i Sharpe niż sam wynik roczny.")


def _sma_list(vals, n):
    out, s = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _realized_vol(closes, n=30):
    """Zmienność zrealizowana z n dni w skali roku (365 dni, rynek krypto działa całą dobę)."""
    lr = [None] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out = [None] * len(closes)
    for i in range(n, len(closes)):
        w = lr[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / (n - 1)) * math.sqrt(365)
    return out


def lab_positions(closes):
    """Ekspozycja 0..1 wyznaczona PRZY ZAMKNIĘCIU każdego dnia, wyłącznie z danych do tego dnia włącznie."""
    n = len(closes)
    s50, s200, vol = _sma_list(closes, 50), _sma_list(closes, 200), _realized_vol(closes, 30)
    pos = {k: [0.0] * n for k, _, _ in LAB_POSITION_PLANS}
    pos["hold"] = [1.0] * n
    pv = pt = None
    for i in range(n):
        up = 1.0 if (s200[i] is not None and closes[i] > s200[i]) else 0.0
        pos["trend200"][i] = up
        pos["cross"][i] = 1.0 if (s50[i] is not None and s200[i] is not None and s50[i] > s200[i]) else 0.0
        if vol[i]:
            tgt = min(1.0, LAB_VOL_TARGET / vol[i])
            pv = round(tgt, 2) if pv is None or abs(tgt - pv) >= LAB_BAND else pv
            tt = tgt * up
            pt = round(tt, 2) if pt is None or abs(tt - pt) >= LAB_BAND or (tt == 0.0) != (pt == 0.0) else pt
        pos["volt"][i] = pv or 0.0
        pos["trendvolt"][i] = pt or 0.0
    return pos


def lab_equity(closes, pos, start, cost_pct=LAB_COST_PCT):
    """Kapitał dzień po dniu od `start` (=100). Pozycja z zamknięcia dnia i-1 pracuje w dniu i; zmiana pozycji kosztuje
    cost_pct od obrotu przy zamknięciu dnia, w którym zapadła decyzja. Zwraca (kapitał, dzienne zwroty, ekspozycja)."""
    c = cost_pct / 100.0
    e = 100.0 * (1 - c * pos[start])
    eq, rets, expo = [e], [], [pos[start]]
    for i in range(start + 1, len(closes)):
        prev = e
        e *= 1 + pos[i - 1] * (closes[i] / closes[i - 1] - 1)
        e *= 1 - c * abs(pos[i] - pos[i - 1])
        eq.append(e)
        rets.append(e / prev - 1)
        expo.append(pos[i])
    return eq, rets, expo


def lab_analyzer_equity(trades, ts_days, risk_pct):
    """Kapitał planu analizatora: każda rozliczona transakcja zmienia go o risk_pct% * wynik w R (procent składany).
    Wynik w R jest już po kosztach. Zwraca (kapitał dzienny, zwroty dzienne, liczba transakcji, największy nominał % kapitału)."""
    import bisect
    by_day = {}
    for t in trades:
        i = max(0, bisect.bisect_right(ts_days, t[0] - 1) - 1)
        by_day.setdefault(i, []).append(t)
    e, eq, rets, maxnom = 100.0, [], [], 0.0
    for i in range(len(ts_days)):
        prev = e
        for t in by_day.get(i, []):
            e *= max(0.0, 1 + risk_pct / 100.0 * t[1])
            if t[2] and t[2] > 0:
                maxnom = max(maxnom, risk_pct / t[2] * 100.0)
        eq.append(e)
        if i:
            rets.append(e / prev - 1 if prev > 0 else 0.0)   # po całkowitej utracie kapitału nic już się nie zmienia
    return eq, rets, maxnom


def lab_metrics(eq, ts, expo=None):
    """Miary wyniku i ryzyka z dziennej krzywej kapitału. ts: znaczniki dni w ms (ta sama długość co eq)."""
    n = len(eq)
    if n < 31 or eq[0] <= 0:
        return None, []
    rets = [eq[i] / eq[i - 1] - 1 if eq[i - 1] > 0 else 0.0 for i in range(1, n)]
    years = (n - 1) / 365.0
    total = eq[-1] / eq[0] - 1
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    ds = math.sqrt(sum(min(r, 0.0) ** 2 for r in rets) / len(rets))
    peak, mdd, uw, max_uw, dd = eq[0], 0.0, 0, 0, []
    for v in eq:
        peak = max(peak, v)
        d = v / peak - 1 if peak > 0 else 0.0
        dd.append(d)
        mdd = min(mdd, d)
        uw = 0 if v >= peak else uw + 1
        max_uw = max(max_uw, uw)
    r30 = [eq[i] / eq[i - 30] - 1 for i in range(30, n) if eq[i - 30] > 0]
    months = {}
    for v, t in zip(eq, ts):
        d = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
        months[(d.year, d.month)] = v
    mv = [eq[0]] + [months[k] for k in sorted(months)]
    mr = [mv[i] / mv[i - 1] - 1 for i in range(1, len(mv)) if mv[i - 1] > 0]
    m = {"total_pct": total * 100, "cagr_pct": cagr * 100, "vol_pct": sd * math.sqrt(365) * 100,
         "sharpe": mean / sd * math.sqrt(365) if sd > 0 else None, "sortino": mean / ds * math.sqrt(365) if ds > 0 else None,
         "max_dd_pct": mdd * 100, "calmar": cagr / -mdd if mdd < 0 else None, "max_underwater_days": max_uw,
         "dd_now_pct": dd[-1] * 100, "worst_30d_pct": min(r30) * 100 if r30 else None, "best_30d_pct": max(r30) * 100 if r30 else None,
         "worst_month_pct": min(mr) * 100 if mr else None, "pos_months_pct": sum(1 for x in mr if x > 0) / len(mr) * 100 if mr else None,
         "days": n - 1, "years": years, "end": eq[-1]}
    if expo is not None:
        m["avg_exposure_pct"] = sum(expo) / len(expo) * 100
        m["in_market_pct"] = sum(1 for x in expo if x > 0) / len(expo) * 100
    return m, dd


def lab_years(eq, ts):
    """Wynik w każdym roku kalendarzowym: od ostatniej wartości poprzedniego roku (lub początku okresu) do końca roku."""
    ends, first_year = {}, datetime.fromtimestamp(ts[0] / 1000, tz=timezone.utc).year
    for v, t in zip(eq, ts):
        ends[datetime.fromtimestamp(t / 1000, tz=timezone.utc).year] = v
    out, prev = {}, eq[0]
    ys = sorted(ends)
    for y in ys:
        out[str(y)] = {"ret": (ends[y] / prev - 1) * 100 if prev > 0 else None, "partial": y == ys[0] or y == ys[-1]}
        prev = ends[y]
    return out


def _pctl(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo = int(math.floor(k))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def lab_montecarlo(rets, seed, sims=LAB_MC_SIMS, block=LAB_MC_BLOCK, days=LAB_MC_DAYS):
    """Rozrzut możliwych wyników na rok do przodu: losujemy bloki po `block` dni z dziennych zwrotów planu (blok zachowuje
    serie zwyżek i spadków). To nie prognoza, tylko pokazanie, jak szeroko rozrzuca się wynik, gdy przyszłość przypomina przeszłość."""
    n = len(rets)
    if n < block * 3:
        return None
    rnd = random.Random(seed)
    finals, mdds = [], []
    for _ in range(sims):
        e = peak = 1.0
        mdd, k = 0.0, 0
        while k < days:
            s = rnd.randrange(0, n - block + 1)
            for r in rets[s:s + min(block, days - k)]:
                e *= 1 + r
                if e > peak:
                    peak = e
                else:
                    d = e / peak - 1
                    if d < mdd:
                        mdd = d
            k += block
        finals.append(e - 1)
        mdds.append(mdd)
    f, d = sorted(finals), sorted(mdds)
    frac = lambda cond: sum(1 for x in mdds if cond(x)) / sims * 100
    return {"sims": sims, "block": block, "days": days,
            "p5": _pctl(f, .05) * 100, "p25": _pctl(f, .25) * 100, "p50": _pctl(f, .5) * 100, "p75": _pctl(f, .75) * 100, "p95": _pctl(f, .95) * 100,
            "p_loss": sum(1 for x in finals if x < 0) / sims * 100, "p_dd20": frac(lambda x: x <= -0.20), "p_dd30": frac(lambda x: x <= -0.30),
            "p_dd50": frac(lambda x: x <= -0.50), "dd_med": _pctl(d, .5) * 100, "dd_p95": _pctl(d, .05) * 100}


def lab_dca(closes, grid, cost_pct=LAB_COST_PCT, weekly=LAB_DCA_WEEKLY):
    """Systematyczne kupowanie co tydzień stałą kwotą (DCA) oraz wariant z podwójną kwotą, gdy cena jest o 25% lub więcej
    poniżej szczytu z 365 dni. Porównanie z jednorazowym zakupem tej samej łącznej kwoty na początku."""
    c = cost_pct / 100.0
    variants = {"dca": {"mult": lambda i: 1.0}, "dip": {"mult": lambda i: 2.0 if closes[i] <= 0.75 * max(closes[max(0, i - 364):i + 1]) else 1.0}}
    out = {}
    for key, v in variants.items():
        units = invested = 0.0
        pnl, worst, uwater, buys = [], 0.0, 0, 0
        for gi, i in enumerate(grid):
            amt = weekly * v["mult"](i)
            units += amt * (1 - c) / closes[i]
            invested += amt
            buys += 1
            p = units * closes[i] / invested - 1
            pnl.append(p * 100)
            if gi >= 3:                      # pierwsze zakupy dają przypadkowo skrajne wartości
                worst = min(worst, p)
            uwater += 1 if p < 0 else 0
        out[key] = {"invested": invested, "value": units * closes[grid[-1]], "profit_pct": pnl[-1], "avg_price": invested * (1 - c) / units if units else None,
                    "worst_pct": worst * 100, "underwater_pct": uwater / len(grid) * 100, "buys": buys, "pnl": pnl}
    # jednorazowy zakup tej samej kwoty co zwykłe DCA
    lump = [((closes[i] / closes[grid[0]]) * (1 - c) - 1) * 100 for i in grid]
    peak, mdd = closes[grid[0]], 0.0
    for i in grid:
        peak = max(peak, closes[i])
        mdd = min(mdd, closes[i] / peak - 1)
    out["lump"] = {"profit_pct": lump[-1], "pnl": lump, "max_dd_pct": mdd * 100}
    return out


def lab_risk_now(closes, ts, atr_pct=None):
    """Termometr ryzyka: gdzie dziś jest zmienność i obsunięcie na tle całej historii oraz w jakim reżimie jest trend."""
    vol = _realized_vol(closes, 30)
    hist = [v for v in vol[LAB_START:] if v]
    now_v = vol[-1]
    pct_rank = sum(1 for v in hist if v <= now_v) / len(hist) * 100 if hist and now_v else None
    ath = max(closes)
    dd_ath = (closes[-1] / ath - 1) * 100
    hi365 = max(closes[-365:])
    s50, s200 = _sma_list(closes, 50)[-1], _sma_list(closes, 200)[-1]
    vs200 = (closes[-1] / s200 - 1) * 100 if s200 else None
    if s200 and s50:
        regime = "hossa" if closes[-1] > s200 and s50 > s200 else "bessa" if closes[-1] < s200 and s50 < s200 else "przejściowy"
    else:
        regime = None
    level = None
    if pct_rank is not None:
        level = ("wysokie" if pct_rank >= 85 or dd_ath <= -35 else "podwyższone" if pct_rank >= 65 or dd_ath <= -20
                 else "niskie" if pct_rank < 35 and dd_ath > -10 else "umiarkowane")
    return {"vol_pct": now_v * 100 if now_v else None, "vol_rank": pct_rank, "dd_ath_pct": dd_ath, "dd_365_pct": (closes[-1] / hi365 - 1) * 100,
            "vs_sma200_pct": vs200, "regime": regime, "level": level, "atr_pct": atr_pct, "ath_date": datetime.fromtimestamp(ts[closes.index(ath)] / 1000, tz=timezone.utc).date().isoformat()}


def _grid(n):
    """Co 7. dzień liczony od końca (zawsze z ostatnim dniem), z pierwszym dniem na początku."""
    idx = list(range(n - 1, -1, -7))[::-1]
    return idx if idx[0] == 0 else [0] + idx


def lab_insights(plans, dca, mc_key="hold"):
    """Wnioski wyliczone z liczb (bez ocen i rekomendacji)."""
    allp = [p for p in plans if p.get("m")]
    ok = [p for p in allp if p["group"] == "position" or p["id"] == "analyzer_1"]   # trzy profile ryzyka tego samego planu nie rywalizują ze sobą
    if len(ok) < 2:
        return []
    out = []
    best = max([p for p in ok if p["group"] == "position"], key=lambda p: p["m"]["cagr_pct"])
    out.append(f"Najwyższy wynik roczny w tym okresie dał plan „{best['name']}”: {best['m']['cagr_pct']:+.0f}% rocznie, ale z maksymalnym obsunięciem {best['m']['max_dd_pct']:.0f}%.")
    safest = max(ok, key=lambda p: p["m"]["max_dd_pct"])
    if safest["id"] != best["id"]:
        out.append(f"Najpłytsze obsunięcie miał plan „{safest['name']}”: {safest['m']['max_dd_pct']:.0f}% przy wyniku {safest['m']['cagr_pct']:+.0f}% rocznie.")
    cal = [p for p in ok if p["m"].get("calmar") is not None]
    if cal:
        bc = max(cal, key=lambda p: p["m"]["calmar"])
        out.append(f"Najlepszy stosunek wyniku do największego obsunięcia (Calmar) ma „{bc['name']}”: {bc['m']['calmar']:.2f}.")
    by = {p["id"]: p for p in allp}
    if "hold" in by and "trend200" in by:
        h, t = by["hold"]["m"], by["trend200"]["m"]
        out.append(f"Filtr trendu SMA200 zmienił maksymalne obsunięcie z {h['max_dd_pct']:.0f}% na {t['max_dd_pct']:.0f}%, a wynik roczny z {h['cagr_pct']:+.0f}% na {t['cagr_pct']:+.0f}%.")
    an = by.get("analyzer_1")
    if an:
        out.append(f"Plan analizatora przy ryzyku 1% na transakcję: {an['m']['cagr_pct']:+.0f}% rocznie, maks. obsunięcie {an['m']['max_dd_pct']:.0f}%, "
                   f"na podstawie {an.get('trades', 0)} transakcji" + (" (to mała próba, więc traktuj to orientacyjnie)." if an.get("trades", 0) < 30 else "."))
    mc = (by.get(mc_key) or {}).get("mc")
    if mc:
        out.append(f"W symulacji rocznej „{by[mc_key]['name']}” kończył rok na minusie w {mc['p_loss']:.0f}% przypadków, a w {mc['p_dd30']:.0f}% miał po drodze obsunięcie głębsze niż 30%.")
    return out


LAB_CAVEATS = [
    "To wyniki historyczne jednego instrumentu (BTC) w okresie z bardzo silnymi trendami wzrostowymi. Plany podążające za trendem wypadają w takim okresie lepiej, niż muszą wypaść w przyszłości.",
    "Reguły są standardowe (SMA 50/200, cel zmienności 40% rocznie); parametrów nie dobierano pod ten okres. Plany pozycyjne liczone bez dźwigni.",
    "Koszt to 0,10% za każdą transakcję (prowizja i poślizg). Nie uwzględniono funding rate, podatków ani poślizgu przy gwałtownych ruchach.",
    "Wynik planu analizatora liczony jest na zrealizowanych transakcjach: chwilowe obsunięcia otwartej pozycji nie są uwzględnione, więc rzeczywiste obsunięcie bywa głębsze.",
    "Symulacja Monte Carlo losuje bloki 21 dni z przeszłości. Pokazuje rozrzut wyników, a nie prognozę.",
]


def run_lab(candles, trades=None, risk_pct=1.0, cost_pct=None, analyzer_now=None, atr_pct=None):
    """Całe laboratorium na zakończonych świecach dziennych. trades: lista z run_backtest()['trades'] (opcjonalnie)."""
    cost_pct = LAB_COST_PCT if cost_pct is None else cost_pct
    n = len(candles)
    if n < LAB_START + 120:
        return None
    closes, ts_all = [c[4] for c in candles], [c[0] for c in candles]
    start = LAB_START
    ts = ts_all[start:]
    grid = _grid(len(ts))
    asof = datetime.fromtimestamp(ts[-1] / 1000, tz=timezone.utc).date().isoformat()
    pos = lab_positions(closes)
    seed0 = zlib.crc32(asof.encode())
    plans = []

    def pack(pid, name, desc, group, eq, rets, expo=None, extra=None, color_slot=None):
        m, dd = lab_metrics(eq, ts, expo)
        if m is None:
            return
        gdd = []
        for a, b_ in zip([-1] + grid[:-1], grid):
            gdd.append(round(min(dd[a + 1:b_ + 1]) * 100, 1))
        p = {"id": pid, "name": name, "desc": desc, "group": group, "m": m, "years": lab_years(eq, ts),
             "eq": [round(eq[i], 1) for i in grid], "dd": gdd,
             "mc": lab_montecarlo(rets, seed0 + zlib.crc32(pid.encode()))}
        p.update(extra or {})
        plans.append(p)

    for pid, name, desc in LAB_POSITION_PLANS:
        eq, rets, expo = lab_equity(closes, pos[pid], start, cost_pct)
        extra = {"today": {"exposure_pct": pos[pid][-1] * 100, "since_days": _since_days(pos[pid])}}
        pack(pid, name, desc, "position", eq, rets, expo, extra)
    tr = [t for t in (trades or []) if t[0] >= ts[0]]
    if tr:
        for rp in LAB_PROFILES:
            eq, rets, maxnom = lab_analyzer_equity(tr, ts, rp)
            wins = sum(1 for t in tr if t[1] > 0)
            extra = {"trades": len(tr), "win_rate_pct": wins / len(tr) * 100, "avg_r": sum(t[1] for t in tr) / len(tr), "risk_pct": rp,
                     "max_notional_pct": maxnom, "needs_leverage_x": max(1.0, maxnom / 100.0)}
            pack(f"analyzer_{rp:g}".replace(".", "_"), f"Analizator (ryzyko {rp:g}%)".replace(".", ","), LAB_ANALYZER_DESC.format(r=rp).replace(".", ","), "analyzer", eq, rets, None, extra)
    dca = lab_dca(closes, [start + i for i in grid], cost_pct)
    byid = {p["id"]: p for p in plans}
    return {"v": LAB_VERSION, "asof": asof, "from": datetime.fromtimestamp(ts[0] / 1000, tz=timezone.utc).date().isoformat(),
            "days": len(ts) - 1, "years": (len(ts) - 1) / 365.0, "cost_pct": cost_pct, "mc": {"sims": LAB_MC_SIMS, "block": LAB_MC_BLOCK, "days": LAB_MC_DAYS},
            "t": [datetime.fromtimestamp(ts[i] / 1000, tz=timezone.utc).date().isoformat() for i in grid],
            "plans": plans, "dca": {k: {**v, "pnl": [round(x, 1) for x in v["pnl"]]} for k, v in dca.items()},
            "risk": lab_risk_now(closes, ts_all, atr_pct), "insights": lab_insights(plans, dca), "caveats": LAB_CAVEATS,
            "analyzer_trades": len(tr), "year_list": sorted({y for p in plans for y in p["years"]})}


def _since_days(pos):
    """Od ilu dni (włącznie z ostatnim) plan jest po tej samej stronie rynku (w rynku / poza rynkiem)."""
    side, k = pos[-1] > 0, 0
    for v in reversed(pos):
        if (v > 0) != side:
            break
        k += 1
    return k


# ==========================================================================
# Powiadomienia (e-mail, WhatsApp)
# ==========================================================================
NOTIFY_EVENTS_ALL = ("new", "filled", "tp1", "closed", "ended", "regime", "liq")
NOTIFY_LIQ_WARN_PCT, NOTIFY_LIQ_CRIT_PCT = 7.0, 3.0   # odległość od likwidacji (% ceny), poniżej której ostrzegamy / alarmujemy
NOTIFY_LIQ_REPEAT_MS = 6 * 3600 * 1000                 # tak długo milczymy, gdy pozycja nadal siedzi w tej samej strefie
NOTIFY_LIQ_RETRY_MS = 3600 * 1000                      # alarm o likwidacji po godzinie jest już bezużyteczny, więc krócej ponawiamy
NOTIFY_REGIME_PLANS = ("trend200", "cross", "trendvolt")  # plany z wyraźnym stanem "w rynku / poza rynkiem"
NOTIFY_RETRY_MS = 6 * 3600 * 1000      # tyle czasu ponawiamy nieudaną wysyłkę, potem rezygnujemy (sygnał by się zestarzał)
NOTIFY_MAX_PER_RUN = 8                 # zabezpieczenie przed zalewem wiadomości (np. po długiej przerwie crona)
_DONE_SKIP, _DONE_GAVE_UP = -2, -1     # znaczniki w rekordzie zdarzenia: pominięte (zastąpione nowszym) / zrezygnowano

_OUTCOME_PL = {
    "sl": "Stop Loss", "tp1_be": "TP1, reszta zamknięta na wejściu", "tp2": "TP2 (cel końcowy)", "time_stop": "zamknięcie po czasie",
    "expired": "propozycja wygasła", "missed": "przegapiona (cena odjechała bez cofnięcia)",
    "invalidated": "unieważniona (cena zeszła do stopa przed wejściem)",
    "withdrawn": "wycofana (plan zmienił się na CZEKAJ)", "replaced": "zastąpiona nowszą propozycją",
}


def _scrub(text, secrets=()):
    """Usuwa z tekstu błędu klucze, hasła i parametry z URL, żeby nie trafiły do raportu ani logów."""
    t = str(text)
    for s in secrets:
        s = str(s or "")
        if len(s) >= 4:
            t = t.replace(s, "***").replace(urllib.parse.quote(s, safe=""), "***")
    t = re.sub(r"(apikey|api_key|token|password|pass|secret)=[^&\s\"']+", r"\1=***", t, flags=re.I)
    t = re.sub(r"\+?\d[\d\s-]{7,}\d", "***", t)  # numery telefonów
    return t.replace("\n", " ")[:220]


def normalize_phone(raw, default_cc="48"):
    """'600 100 200' -> '+48600100200'; '0048...' i '+48...' też działają. Zwraca None, gdy numer jest nieprawidłowy."""
    d = re.sub(r"[^\d+]", "", raw or "")
    if d.startswith("00"):
        d = "+" + d[2:]
    if not d.startswith("+"):
        d = "+" + (default_cc + d if len(d) == 9 else d)
    return d if re.fullmatch(r"\+\d{8,15}", d) else None


def _read_err_body(e):
    try:
        return e.read(400).decode("utf-8", "replace")
    except Exception:
        return ""


class Notifier:
    """Wysyła wiadomości z sygnałami: e-mail (SMTP) i/lub WhatsApp (CallMeBot albo Twilio). Cała konfiguracja z .env.
    Nic tu nie składa zleceń na giełdzie: to wyłącznie wysyłka tekstu do Ciebie."""

    def __init__(self, env=None):
        e = os.environ if env is None else env
        def g(k, d=""):
            v = (e.get(k) or "").strip()
            return d if not v or v.lower().startswith("wpisz") else v   # niewypełniony szablon z .env.example = brak wartości
        self.config_errors = []
        # e-mail
        self.email_to = [x.strip() for x in re.split(r"[;,]", g("NOTIFY_EMAIL_TO")) if x.strip()]
        bad = [x for x in self.email_to if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", x)]
        if bad:
            self.config_errors.append("NOTIFY_EMAIL_TO zawiera nieprawidłowy adres")
            self.email_to = [x for x in self.email_to if x not in bad]
        self.smtp_host, self.smtp_user, self.smtp_pass = g("SMTP_HOST"), g("SMTP_USER"), g("SMTP_PASSWORD")
        if self.smtp_host.lower().endswith("gmail.com"):
            self.smtp_pass = re.sub(r"\s+", "", self.smtp_pass)       # Google pokazuje hasło aplikacji w grupach po 4 znaki
        try:
            self.smtp_port = int(g("SMTP_PORT", "465"))
        except ValueError:
            self.smtp_port = 465
            self.config_errors.append("SMTP_PORT nie jest liczbą, używam 465")
        self.smtp_sec = g("SMTP_SECURITY", "ssl" if self.smtp_port == 465 else "starttls" if self.smtp_port == 587 else "none").lower()
        if self.smtp_sec not in ("ssl", "starttls", "none"):
            self.config_errors.append("SMTP_SECURITY: dozwolone ssl, starttls, none")
            self.smtp_sec = "ssl"
        self.email_from = g("NOTIFY_EMAIL_FROM") or self.smtp_user or (self.email_to[0] if self.email_to else "")
        # WhatsApp
        raw_phone = g("NOTIFY_WHATSAPP_TO")
        self.wa_to = normalize_phone(raw_phone, g("NOTIFY_DEFAULT_CC", "48")) if raw_phone else None
        if raw_phone and not self.wa_to:
            self.config_errors.append("NOTIFY_WHATSAPP_TO: nieprawidłowy numer (oczekuję np. +48600100200)")
        self.wa_provider = g("WHATSAPP_PROVIDER", "callmebot").lower()
        if self.wa_to and self.wa_provider not in ("callmebot", "twilio"):
            self.config_errors.append("WHATSAPP_PROVIDER: dozwolone callmebot albo twilio")
        self.cmb_key = g("CALLMEBOT_APIKEY")
        self.tw_sid, self.tw_token = g("TWILIO_ACCOUNT_SID"), g("TWILIO_AUTH_TOKEN")
        self.tw_from, self.tw_content = g("TWILIO_WHATSAPP_FROM"), g("TWILIO_CONTENT_SID")
        # zdarzenia, strefa czasu, link
        ev = [x.strip().lower() for x in re.split(r"[;,\s]+", g("NOTIFY_EVENTS")) if x.strip()]
        unknown = [x for x in ev if x not in NOTIFY_EVENTS_ALL]
        if unknown:
            self.config_errors.append("NOTIFY_EVENTS: nieznane zdarzenia " + ", ".join(unknown))
        self.events = [x for x in NOTIFY_EVENTS_ALL if (not ev or x in ev)]
        self.link = g("NOTIFY_LINK")
        try:
            self.liq_warn = float(g("NOTIFY_LIQ_WARN_PCT", str(NOTIFY_LIQ_WARN_PCT)).replace(",", "."))
            self.liq_crit = float(g("NOTIFY_LIQ_CRIT_PCT", str(NOTIFY_LIQ_CRIT_PCT)).replace(",", "."))
        except ValueError:
            self.liq_warn, self.liq_crit = -1.0, -1.0
        if not 0 < self.liq_crit < self.liq_warn <= 50:
            self.config_errors.append("NOTIFY_LIQ_WARN_PCT / NOTIFY_LIQ_CRIT_PCT: oczekuję 0 < pilny < ostrzeżenie <= 50, używam 7 i 3")
            self.liq_warn, self.liq_crit = NOTIFY_LIQ_WARN_PCT, NOTIFY_LIQ_CRIT_PCT
        self.tz_name, self.tz = g("NOTIFY_TZ", "Europe/Warsaw"), timezone.utc
        try:
            from zoneinfo import ZoneInfo
            self.tz = ZoneInfo(self.tz_name)
        except Exception:
            self.tz_name = "UTC"

    @property
    def channels(self):
        out = []
        local = self.smtp_host.lower() in ("localhost", "127.0.0.1", "::1")
        if self.email_to and self.smtp_host and (local or (self.smtp_user and self.smtp_pass)):
            out.append("email")
        if self.wa_to and ((self.wa_provider == "callmebot" and self.cmb_key) or
                           (self.wa_provider == "twilio" and self.tw_sid and self.tw_token and self.tw_from)):
            out.append("whatsapp")
        return out

    def secrets(self):
        return [self.smtp_pass, self.cmb_key, self.tw_token, self.tw_sid, self.wa_to, *self.email_to, self.smtp_user]

    def missing(self):
        """Podpowiedź, czego brakuje, gdy ktoś podał adresata, ale nie podał danych wysyłki."""
        m = []
        if self.email_to and "email" not in self.channels:
            m.append("e-mail: uzupełnij SMTP_HOST, SMTP_USER i SMTP_PASSWORD (dla Gmaila: hasło aplikacji)")
        if self.wa_to and "whatsapp" not in self.channels:
            m.append("WhatsApp: brak CALLMEBOT_APIKEY" if self.wa_provider == "callmebot"
                     else "WhatsApp (Twilio): brak TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN lub TWILIO_WHATSAPP_FROM")
        return m

    # --- wysyłka -------------------------------------------------------
    def send(self, channel, subject, text, wa_text=None):
        """Zwraca (True, None) albo (False, krótki opis błędu bez sekretów)."""
        try:
            if channel == "email":
                self._send_email(subject, text)
            elif channel == "whatsapp":
                self._send_whatsapp(wa_text or text)
            else:
                return False, f"nieznany kanał {channel}"
            return True, None
        except Exception as e:
            detail = _read_err_body(e) if isinstance(e, urllib.error.HTTPError) else ""
            if detail and self.wa_provider == "twilio":
                try:
                    j = json.loads(detail)
                    detail = f"{j.get('code')}: {j.get('message')}"
                except ValueError:
                    pass
            msg = f"{type(e).__name__}: {e}" + (f" ({detail.strip()[:140]})" if detail else "")
            return False, _scrub(msg, self.secrets())

    def _send_email(self, subject, body):
        import smtplib
        import ssl
        from email.message import EmailMessage
        from email.utils import formatdate, make_msgid
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, self.email_from or self.smtp_user, ", ".join(self.email_to)
        msg["Date"], msg["Message-ID"] = formatdate(localtime=False), make_msgid(domain=(self.email_from.split("@")[-1] if "@" in self.email_from else None))
        msg.set_content(body)
        ctx = ssl.create_default_context()
        srv = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20, context=ctx) if self.smtp_sec == "ssl" \
            else smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20)
        try:
            srv.ehlo()
            if self.smtp_sec == "starttls":
                srv.starttls(context=ctx)
                srv.ehlo()
            if self.smtp_user:
                srv.login(self.smtp_user, self.smtp_pass)
            refused = srv.send_message(msg)
            if refused:
                raise RuntimeError("serwer odrzucił adresata")
        finally:
            try:
                srv.quit()
            except Exception:
                pass

    def _send_whatsapp(self, text):
        if self.wa_provider == "twilio":
            form = {"To": "whatsapp:" + self.wa_to, "From": "whatsapp:" + re.sub(r"^whatsapp:", "", self.tw_from, flags=re.I).strip()}
            if self.tw_content:  # szablon zatwierdzony przez WhatsApp: zmienne nie mogą zawierać nowych linii
                form["ContentSid"] = self.tw_content
                form["ContentVariables"] = json.dumps({"1": re.sub(r"\s*\n+\s*", " | ", text)})
            else:
                form["Body"] = text
            tok = base64.b64encode(f"{self.tw_sid}:{self.tw_token}".encode()).decode()
            req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{self.tw_sid}/Messages.json",
                                         data=urllib.parse.urlencode(form).encode(), method="POST",
                                         headers={"Authorization": "Basic " + tok, "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if not 200 <= r.status < 300:
                    raise RuntimeError(f"HTTP {r.status}")
            return
        q = urllib.parse.urlencode({"phone": self.wa_to, "text": text, "apikey": self.cmb_key}, quote_via=urllib.parse.quote)
        req = urllib.request.Request("https://api.callmebot.com/whatsapp.php?" + q, headers={"User-Agent": "btc-analysis/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(1500).decode("utf-8", "replace")
            if not 200 <= r.status < 300 or re.search(r"\berror\b|invalid|incorrect|not (?:registered|activated)", body[:600], re.I):
                raise RuntimeError("CallMeBot odrzucił wiadomość: " + re.sub(r"<[^>]+>", " ", body)[:120].strip())

    def test(self):
        """Wysyła próbną wiadomość każdym skonfigurowanym kanałem. Zwraca [(kanał, ok, błąd)]."""
        stamp = datetime.now(timezone.utc).astimezone(self.tz).strftime("%d.%m.%Y %H:%M")
        txt = (f"Test powiadomień analizatora BTC ({stamp}).\nJeśli to czytasz, kanał działa i sygnały będą przychodzić tą drogą.")
        return [(c, *self.send(c, "[BTC] Test powiadomień", txt, txt)) for c in self.channels]


def _nfmt(x, dec=0):
    return "n/d" if x is None else f"{x:,.{dec}f}".replace(",", " ")


def build_message(event, p, nt, price=None, symbol="BTCUSDT", path=()):
    """Składa treść wiadomości: (temat e-mail, treść e-mail, krótka wersja na WhatsApp)."""
    q = symbol.replace("BTC", "", 1) or "USD"
    long_ = p["side"] == "long"
    side = "KUP (long)" if long_ else "SPRZEDAJ (short)"
    stop_e = p.get("entry_type") == "stop"
    kind = "zlecenie warunkowe po wybiciu" if stop_e else "zlecenie limit"
    when = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(nt.tz).strftime("%d.%m %H:%M")
    e_, s_, t1, t2 = p["entry"], p["stop"], p["tp1"], p["tp2"]
    risk = abs(e_ - s_) or 1
    r1, r2 = abs(t1 - e_) / risk, abs(t2 - e_) / risk
    pct = lambda v: f"{(v / e_ - 1) * 100:+.1f}%"
    levels = [f"Wejście: {_nfmt(e_)} {q}" + (" (po przebiciu tego poziomu)" if stop_e else " (limit na cofnięciu)"),
              f"Stop Loss: {_nfmt(s_)} ({pct(s_)})",
              f"TP1: {_nfmt(t1)} ({r1:.1f}R): zamknij 50% i przesuń stop na wejście",
              f"TP2: {_nfmt(t2)} ({r2:.1f}R): reszta pozycji"]
    foot = ["", "Wiadomość automatyczna. To heurystyka analizatora, nie porada inwestycyjna.",
            "Nic nie zostało zlecone na giełdzie: zlecenie składasz sam i na własną odpowiedzialność."]
    if nt.link:
        foot.append(nt.link)
    px = f"Cena teraz: {_nfmt(price)} {q}" if price else None
    steps = "; ".join(path)
    short = f"{side} {_nfmt(e_)}"
    if event == "new":
        head = f"NOWY SYGNAŁ BTC: {side}, {kind}"
        body = [head, ""] + levels + [f"Stosunek zysku do ryzyka 1:{p.get('rr', 0):.2f}, pewność heurystyki {p.get('confidence', 0)}%",
                                      f"Propozycja ważna do: {when(p['valid_until_ms'])} ({nt.tz_name})"]
        if px:
            body.append(px)
        why = p.get("why")
        if why:
            body += ["", "Dlaczego: " + (" + ".join(str(x) for x in why) if isinstance(why, (list, tuple)) else str(why))]
        wa = (f"BTC · NOWY SYGNAŁ\n{side}, {kind}\nWejście {_nfmt(e_)} | SL {_nfmt(s_)} ({pct(s_)})\n"
              f"TP1 {_nfmt(t1)} | TP2 {_nfmt(t2)}\nPewność {p.get('confidence', 0)}%, R:R 1:{p.get('rr', 0):.2f}\n"
              f"Ważne do {when(p['valid_until_ms'])}\n(heurystyka, nie porada; nic nie zlecono)")
        return f"[BTC] Sygnał: {short}, SL {_nfmt(s_)}", "\n".join(body + foot), wa
    if event == "filled":
        fp = p.get("fill_px") or e_
        head = f"WEJŚCIE (wg dziennika): {side} po ok. {_nfmt(fp)} {q}"
        body = [head, "", "Cena dotknęła poziomu wejścia. Jeśli złożyłeś zlecenie, powinno się wypełnić; sprawdź, czy masz ustawione SL i TP.", ""] + \
               levels + ([px] if px else [])
        wa = (f"BTC · WEJŚCIE (wg dziennika)\n{side} ok. {_nfmt(fp)}\nSL {_nfmt(s_)} | TP1 {_nfmt(t1)} | TP2 {_nfmt(t2)}\n"
              "Sprawdź, czy masz ustawione SL i TP.")
        return f"[BTC] Wejście: {short}", "\n".join(body + foot), wa
    if event == "tp1":
        head = f"TP1 OSIĄGNIĘTY (wg dziennika): {side}"
        body = [head, "", f"Cena doszła do {_nfmt(t1)} {q}. Zgodnie z planem zamknij 50% pozycji i przesuń stop na wejście ({_nfmt(p.get('fill_px') or e_)}).",
                f"Zostaje TP2: {_nfmt(t2)} ({r2:.1f}R)."] + ([px] if px else [])
        wa = (f"BTC · TP1 osiągnięty\nZamknij 50% i przesuń stop na wejście ({_nfmt(p.get('fill_px') or e_)}).\nZostaje TP2 {_nfmt(t2)}.")
        return f"[BTC] TP1: {short}", "\n".join(body + foot), wa
    if event == "closed":
        o = _OUTCOME_PL.get(p.get("outcome"), p.get("outcome") or "zamknięta")
        r = p.get("r")
        rtxt = f"{r:+.2f}R" if r is not None else "n/d"
        head = f"POZYCJA ZAMKNIĘTA (wg dziennika): {o}, wynik {rtxt}"
        body = [head, "", f"{side}, wejście {_nfmt(p.get('fill_px') or e_)}, stop {_nfmt(s_)}, TP1 {_nfmt(t1)}, TP2 {_nfmt(t2)}."]
        if steps:
            body.append("Przebieg: " + steps + ".")
        body.append("Wynik liczony po kosztach (1R = ryzyko do stopa). Zamknij ewentualną resztę pozycji i anuluj zlecenia, które zostały na giełdzie.")
        wa = f"BTC · ZAMKNIĘTA: {o}\n{side}, wynik {rtxt}\nAnuluj zlecenia, które zostały na giełdzie."
        return f"[BTC] Zamknięta: {short}, {rtxt}", "\n".join(body + foot), wa
    o = _OUTCOME_PL.get(p.get("outcome"), p.get("outcome") or "zakończona")
    head = f"PROPOZYCJA ZAKOŃCZONA BEZ WEJŚCIA: {o}"
    body = [head, "", f"{side}, wejście {_nfmt(e_)}. Jeśli złożyłeś zlecenie na tym poziomie, ANULUJ je: sygnał nie jest już aktualny."] + ([px] if px else [])
    wa = f"BTC · SYGNAŁ NIEAKTUALNY: {o}\n{side} {_nfmt(e_)}\nJeśli masz zlecenie na tym poziomie, anuluj je."
    return f"[BTC] Anuluj zlecenie: {short} ({o.split(' (')[0]})", "\n".join(body + foot), wa


def _events_of(p):
    ev = ["new"]
    if p.get("filled_ms"):
        ev.append("filled")
    if p.get("tp1_hit"):
        ev.append("tp1")
    if p["status"] == "closed":
        o = p.get("outcome")
        if o in PLAN_FILLED:
            ev.append("closed")
        elif o in PLAN_NOT_FILLED or o in PLAN_CANCELLED:
            ev.append("ended")
    return ev


def _delivered(rec):
    return bool(rec) and (rec.get("skip") or any(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
                                                 for k, v in rec.items() if k in ("email", "whatsapp")))


def process_notifications(j, nt, now_ms, price=None, symbol="BTCUSDT", max_per_run=NOTIFY_MAX_PER_RUN):
    """Wysyła powiadomienia o zmianach w propozycjach z dziennika i zapisuje w nim, co już poszło (nie dubluje przy kolejnych uruchomieniach).
    Stan jest w propozycji (p['ntf']), więc zapisuje się razem z dziennikiem. Błąd jednego kanału nie blokuje drugiego,
    a nieudana wysyłka jest ponawiana przy kolejnych uruchomieniach przez NOTIFY_RETRY_MS."""
    st = j.setdefault("notify", {})
    chans, summary, budget = nt.channels, {"sent": 0, "failed": 0, "errors": []}, max_per_run
    label = {"new": "zaproponowana", "filled": "wejście wypełnione", "tp1": "TP1 osiągnięty"}
    for p in j["proposals"]:
        active = p["status"] in ("pending", "open")
        ntf = p.get("ntf") or {}
        if not active and "new" in nt.events and not _delivered(ntf.get("new")):
            continue                                   # historia sprzed włączenia powiadomień albo sygnał, o którym nie zdążyliśmy powiedzieć
        evs = [e for e in _events_of(p) if e in nt.events]
        fresh = [e for e in evs if e not in ntf]       # zdarzenia widziane po raz pierwszy
        retry = [e for e in evs if e in ntf and not ntf[e].get("skip") and any(c not in ntf[e] for c in chans)]
        if not fresh and not retry:
            continue
        ntf = p.setdefault("ntf", {})
        path = []
        for e in fresh[:-1]:                           # po przerwie kilka nowych zdarzeń naraz: idzie tylko najnowsze, resztę opisuje
            ntf[e] = {"t": now_ms, "skip": True}
            path.append(label.get(e, e))
        last_fresh = fresh[-1] if fresh else None
        for e in evs:
            rec = ntf.setdefault(e, {"t": now_ms})
            if rec.get("skip"):
                continue
            if e != "new" and "new" in nt.events and not _delivered(ntf.get("new")):
                continue                               # nie piszemy o dalszych losach sygnału, którego nie dostarczono
            todo_ch = [c for c in chans if c not in rec]
            if not todo_ch:
                continue
            if now_ms - rec["t"] > NOTIFY_RETRY_MS:    # zbyt stare, żeby jeszcze było użyteczne
                for c in todo_ch:
                    rec[c] = _DONE_GAVE_UP
                continue
            if budget <= 0:
                continue
            budget -= 1
            subject, body, wa = build_message(e, p, nt, price, symbol, path if e == last_fresh else ())
            for c in todo_ch:
                ok, err = nt.send(c, subject, body, wa)
                if ok:
                    rec[c] = now_ms
                    summary["sent"] += 1
                    st["last_ok_ms"], st["sent_total"] = now_ms, st.get("sent_total", 0) + 1
                else:
                    summary["failed"] += 1
                    summary["errors"].append(f"{c}: {err}")
                    st["last_error"] = {"ms": now_ms, "channel": c, "message": err}
    return summary


# --- zmiana nastawienia planów długoterminowych i bliskość likwidacji --------------------------------------------
_REGIME_WHY = {
    "trend200": ("cena zamknęła się powyżej średniej z 200 dni", "cena zamknęła się poniżej średniej z 200 dni"),
    "cross": ("średnia z 50 dni przeszła powyżej średniej z 200 dni (złoty krzyż)", "średnia z 50 dni spadła poniżej średniej z 200 dni (krzyż śmierci)"),
    "trendvolt": ("cena zamknęła się powyżej średniej z 200 dni", "cena zamknęła się poniżej średniej z 200 dni"),
}


def _dispatch(nt, rec, msg, now_ms, st, summary, retry_ms=NOTIFY_RETRY_MS):
    """Wysyła gotową wiadomość (temat, treść, wersja WhatsApp) kanałami, do których jeszcze nie dotarła, i zapisuje wynik w rec.
    Nieudany kanał jest ponawiany przy kolejnych uruchomieniach przez retry_ms, potem rezygnujemy."""
    todo = [c for c in nt.channels if c not in rec]
    if not todo:
        return
    if now_ms - rec["t"] > retry_ms:
        for c in todo:
            rec[c] = _DONE_GAVE_UP
        return
    subject, body, wa = msg
    for c in todo:
        ok, err = nt.send(c, subject, body, wa)
        if ok:
            rec[c] = now_ms
            summary["sent"] += 1
            st["last_ok_ms"], st["sent_total"] = now_ms, st.get("sent_total", 0) + 1
        else:
            summary["failed"] += 1
            summary["errors"].append(f"{c}: {err}")
            st["last_error"] = {"ms": now_ms, "channel": c, "message": err}


def _footer(nt, extra=()):
    f = ["", *extra, "Wiadomość automatyczna. To heurystyka analizatora, nie porada inwestycyjna.", "Skrypt niczego nie zleca na giełdzie."]
    return f + ([nt.link] if nt.link else [])


def build_regime_message(plan, entering, day, prev_days, nt, price=None, symbol="BTCUSDT"):
    q = symbol.replace("BTC", "", 1) or "USD"
    name, exp = plan["name"], (plan.get("today") or {}).get("exposure_pct") or 0
    why = _REGIME_WHY.get(plan["id"], ("sygnał wejścia", "sygnał wyjścia"))[0 if entering else 1]
    head = f"PLAN DŁUGOTERMINOWY „{name}”: " + ("WEJŚCIE W RYNEK" if entering else "WYJŚCIE Z RYNKU (gotówka)")
    stance = ("Reguła planu mówi teraz: być w BTC" + (f", docelowa ekspozycja {exp:.0f}% kapitału" if exp < 99.5 else "") + "."
              if entering else "Reguła planu mówi teraz: nie trzymać BTC, kapitał w gotówce.")
    body = [head, "", f"Sygnał z zamknięcia dnia {day}: {why}.", stance]
    if price:
        body.append(f"Cena teraz: {_nfmt(price)} {q}")
    body.append(f"Poprzedni stan ({'poza rynkiem' if entering else 'w rynku'}) trwał {prev_days} dn.")
    fast = prev_days <= 5
    if fast:
        body.append("To szybka zmiana: gdy cena krąży wokół średniej, takie sygnały często się odwracają po kilku dniach.")
    body += _footer(nt, ["To zapis reguły z symulacji historycznej (sekcja „Długi termin” na panelu), nie zlecenie."])
    wa = (f"BTC · {name}\n" + ("WEJŚCIE W RYNEK" if entering else "WYJŚCIE Z RYNKU") + f"\nSygnał z dnia {day}: {why}.\n"
          + (f"Poprzedni stan trwał {prev_days} dn.\n" if not fast else "Szybka zmiana: sygnały przy cenie wokół średniej często się odwracają.\n")
          + "(symulacja planu, nie porada; nic nie zlecono)")
    return f"[BTC] Plan „{name}”: " + ("sygnał WEJŚCIA w rynek" if entering else "sygnał WYJŚCIA z rynku"), "\n".join(body), wa


def process_regime(st, nt, lab_data, now_ms, price=None, symbol="BTCUSDT", summary=None):
    """Zapamiętuje, czy plany pozycyjne są w rynku, i wysyła wiadomość, gdy któryś zmieni stan. Pierwsze uruchomienie tylko zapisuje stan
    wyjściowy (nie wysyłamy historii). Stan jest wspólny z dziennikiem (st = j['notify']), więc nie dubluje wiadomości."""
    summary = summary if summary is not None else {"sent": 0, "failed": 0, "errors": []}
    if not lab_data:
        return summary
    rg, day = st.setdefault("regime", {}), lab_data.get("asof")
    for p in lab_data.get("plans") or []:
        if p["id"] not in NOTIFY_REGIME_PLANS:
            continue
        exp = (p.get("today") or {}).get("exposure_pct")
        if exp is None or not day:
            continue
        cur, rec = exp > 0, rg.get(p["id"])
        if not rec or "in" not in rec:
            rg[p["id"]] = {"in": cur, "day": day}
            continue
        if rec["in"] != cur:
            try:
                prev_days = max(1, (datetime.fromisoformat(day) - datetime.fromisoformat(rec["day"])).days)
            except (ValueError, TypeError):
                prev_days = 1
            rec = rg[p["id"]] = {"in": cur, "day": day, "ev": {"t": now_ms}, "prev_days": prev_days}
        ev = rec.get("ev")
        if ev and "regime" in nt.events and nt.channels:
            _dispatch(nt, ev, build_regime_message(p, cur, day, rec.get("prev_days", 1), nt, price, symbol), now_ms, st, summary)
    return summary


def _liq_positions(positions):
    """Z surowej listy pozycji Bybit wybiera te, dla których znamy cenę likwidacji, i liczy odległość do niej (% ceny rynkowej)."""
    out = []
    for p in positions or []:
        size, mark, liq = num(p.get("size")), num(p.get("markPrice")), num(p.get("liqPrice"))
        if not size or not mark or not liq or liq <= 0:
            continue
        long_ = str(p.get("side", "")).lower() == "buy"
        dist = (mark - liq) / mark * 100 if long_ else (liq - mark) / mark * 100
        out.append({"key": f"{p.get('symbol')}|{'long' if long_ else 'short'}|{p.get('positionIdx', 0)}", "symbol": p.get("symbol") or "?",
                    "side": "long" if long_ else "short", "mark": mark, "liq": liq, "dist": max(dist, 0.0), "lev": num(p.get("leverage"))})
    return out


def build_liq_message(q, level, nt):
    head = "PILNE" if level == 2 else "UWAGA"
    dec = 0 if q["mark"] >= 1000 else 2
    line = f"Cena rynkowa (mark): {_nfmt(q['mark'], dec)}, szacowana likwidacja: {_nfmt(q['liq'], dec)}" + (f", dźwignia {q['lev']:g}×" if q.get("lev") else "")
    body = [f"{head}: pozycja {q['side']} na {q['symbol']} jest {q['dist']:.1f}% od ceny likwidacji.", "", line, "",
            "Co możesz zrobić: dołożyć margin, zmniejszyć pozycję albo ją zamknąć.",
            f"Progi: ostrzeżenie {nt.liq_warn:g}%, pilne {nt.liq_crit:g}% (NOTIFY_LIQ_WARN_PCT, NOTIFY_LIQ_CRIT_PCT).",
            "Cenę likwidacji podaje Bybit i zmienia się ona z saldem oraz innymi pozycjami (margin krzyżowy), więc sprawdź ją na giełdzie."]
    wa = (f"BTC · {head}: blisko likwidacji\n{q['symbol']} {q['side']}: {q['dist']:.1f}% do likwidacji\n"
          f"mark {_nfmt(q['mark'], dec)} | liq {_nfmt(q['liq'], dec)}\nDołóż margin, zmniejsz lub zamknij pozycję.")
    return f"[BTC] {head}: blisko likwidacji, {q['symbol']} {q['side']}, {q['dist']:.1f}%", "\n".join(body + _footer(nt)), wa


def process_liq(st, nt, positions, now_ms, summary=None):
    """Pilnuje odległości do likwidacji otwartych pozycji. Alarm przy wejściu w strefę ostrzeżenia, drugi przy pogorszeniu do strefy pilnej,
    powtórka dopiero po NOTIFY_LIQ_REPEAT_MS, ponowne uzbrojenie, gdy pozycja odejdzie od likwidacji o ponad 30% progu ostrzeżenia.
    positions=None (nieudane pobranie) nie zmienia stanu i zwraca None. Poza tym zwraca listę obserwowanych pozycji."""
    summary = summary if summary is not None else {"sent": 0, "failed": 0, "errors": []}
    if positions is None:
        return None
    live = _liq_positions(positions)
    if not nt.channels or "liq" not in nt.events:
        return live                                    # nic nie wyślemy, więc nie przesuwamy stanu (alarm nie zostanie "zużyty")
    state, seen = st.setdefault("liq", {}), set()
    for q in live:
        seen.add(q["key"])
        s = state.setdefault(q["key"], {"lvl": 0, "t": 0})
        lvl = 2 if q["dist"] <= nt.liq_crit else 1 if q["dist"] <= nt.liq_warn else 0
        if lvl == 0 and s["lvl"] and q["dist"] > nt.liq_warn * 1.3:
            s["lvl"] = 0
            s.pop("ev", None)
        if lvl > s["lvl"] or (lvl and lvl == s["lvl"] and now_ms - s["t"] >= NOTIFY_LIQ_REPEAT_MS):
            s["lvl"], s["t"], s["ev"] = lvl, now_ms, {"t": now_ms}
        if s.get("ev") and lvl:
            _dispatch(nt, s["ev"], build_liq_message(q, lvl, nt), now_ms, st, summary, retry_ms=NOTIFY_LIQ_RETRY_MS)
    for k in [k for k in state if k not in seen]:
        del state[k]                                   # pozycja została zamknięta
    return live


# ==========================================================================
# Formatowanie raportu
# ==========================================================================
def money(x, digits=2):
    return "n/d" if x is None else f"{x:,.{digits}f}"


def big(x):
    if x is None:
        return "n/d"
    for limit, name in ((1e12, "bln"), (1e9, "mld"), (1e6, "mln")):
        if abs(x) >= limit:
            return f"{x / limit:,.2f} {name}"
    return f"{x:,.0f}"


def ratio_pct(x, digits=2):
    return "n/d" if x is None else f"{x * 100:+.{digits}f}%"


def plain_pct(x, digits=1):
    return "n/d" if x is None else f"{x * 100:.{digits}f}%"


def api_pct(x, digits=2):
    x = num(x)
    return "n/d" if x is None else f"{x:+.{digits}f}%"


def row(label, value):
    return f"{label:<38}{value}"


def render(data, a, vs):
    cur = vs.upper()
    L = ["=" * 72, f" ANALIZA BTC/{cur} — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "=" * 72]

    def head(title):
        L.extend(["", title, "-" * len(title)])

    def missing():
        L.append("brak danych (zobacz ostrzeżenia na końcu)")

    # --- spot
    head("1. Cena spot (simple.price)")
    spot = (data.get("spot") or {}).get("bitcoin")
    if spot:
        for c in data["vs_list"]:
            L.append(row(f"BTC/{c.upper()}", f"{money(num(spot.get(c)))}   (24h: {api_pct(spot.get(c + '_24h_change'))})"))
        v = num(spot.get(f"{vs}_24h_vol"))
        L.append(row(f"Wolumen 24h ({cur})", big(v)))
    else:
        missing()

    # --- markets
    head("2. Snapshot rynkowy (coins.markets)")
    mk = (data.get("market") or [None])[0]
    if mk:
        L += [
            row("Ranking / kapitalizacja", f"#{mk.get('market_cap_rank')} / {big(num(mk.get('market_cap')))} {cur}"),
            row("Zakres 24h", f"{money(num(mk.get('low_24h')))} – {money(num(mk.get('high_24h')))}"),
            row("ATH / odległość od ATH", f"{money(num(mk.get('ath')))} / {api_pct(mk.get('ath_change_percentage'))}"),
            row("Zmiana 1h / 24h / 7d",
                f"{api_pct(mk.get('price_change_percentage_1h_in_currency'))} / "
                f"{api_pct(mk.get('price_change_percentage_24h_in_currency'))} / "
                f"{api_pct(mk.get('price_change_percentage_7d_in_currency'))}"),
            row("Zmiana 30d / 200d / 1r",
                f"{api_pct(mk.get('price_change_percentage_30d_in_currency'))} / "
                f"{api_pct(mk.get('price_change_percentage_200d_in_currency'))} / "
                f"{api_pct(mk.get('price_change_percentage_1y_in_currency'))}"),
        ]
        cap, vol = num(mk.get("market_cap")), num(mk.get("total_volume"))
        if cap and vol:
            L.append(row("Obrót / kapitalizacja", f"{vol / cap * 100:.2f}% dziennie"))
        circ, mx = num(mk.get("circulating_supply")), num(mk.get("max_supply"))
        if circ and mx:
            L.append(row("Podaż w obiegu", f"{circ:,.0f} BTC ({circ / mx * 100:.1f}% maks. {mx:,.0f})"))
    else:
        missing()

    # --- technical
    head(f"3. Analiza techniczna (coins.market_chart, {a.get('n_days', 0)} dni dziennych)")
    if a["history_ok"]:
        ch = a["changes"]
        L += [
            row("Zmiana 7d / 30d / 90d", f"{ratio_pct(ch['7d'])} / {ratio_pct(ch['30d'])} / {ratio_pct(ch['90d'])}"),
            row(f"Zmiana 180d / cały okres ({a['n_days']}d)", f"{ratio_pct(ch['180d'])} / {ratio_pct(ch['full'])}"),
            row("SMA20 / SMA50 / SMA200", f"{money(a['sma20'])} / {money(a['sma50'])} / {money(a['sma200'])}"),
        ]
        if a["macd"]:
            m = a["macd"]
            L.append(row("MACD (12,26,9)", f"linia {m['macd']:+.1f}, sygnał {m['signal']:+.1f}, histogram {m['hist']:+.1f}"))
        if a["rsi14"] is not None:
            L.append(row("RSI(14)", f"{a['rsi14']:.1f}"))
        if a["boll"]:
            b = a["boll"]
            L.append(row("Bollinger (20, 2σ)", f"{money(b['lower'])} – {money(b['upper'])}, %B = {b['pct_b']:.2f}"))
        L.append(row("Zakres okresu (min – max)", f"{money(a['period_low'])} – {money(a['period_high'])}"))
        fib = a["fib"]
        L.append(row("Fibonacci (ostatnie 90 dni)", f"szczyt {money(fib['high'])}, dołek {money(fib['low'])}"))
        L.append(row("", "  ".join(f"{k}: {money(v, 0)}" for k, v in fib["levels"].items())))
        if "ohlc" in a:
            o = a["ohlc"]
            L.append(row("Świece 4h: ATR(14)", f"{money(o['atr14'])} {cur}  (zakres 30d: {money(o['low_30d'])} – {money(o['high_30d'])})"))
    else:
        missing()

    # --- risk
    head("4. Ryzyko i zmienność")
    if a["history_ok"]:
        L += [
            row("Zmienność roczna (30d / całość)", f"{plain_pct(a['vol30'])} / {plain_pct(a['vol_all'])}"),
            row("Maks. obsunięcie w okresie", ratio_pct(a["max_drawdown"])),
            row("Obecnie od szczytu okresu", ratio_pct(a["current_drawdown"])),
            row("VaR 95% (1 dzień, historyczny)", f"-{a['var95'] * 100:.2f}%" if a["var95"] is not None else "n/d"),
            row("Najlepszy / najgorszy dzień", f"{ratio_pct(a['best_day'])} / {ratio_pct(a['worst_day'])}"),
            row("Dni na plusie", f"{a['positive_days_share'] * 100:.0f}%"),
        ]
        if a.get("volume_ratio_7_30") is not None:
            L.append(row("Wolumen 7d / średnia 30d", f"{a['volume_ratio_7_30']:.2f}x"))
    else:
        missing()

    # --- compare
    cmp_id = data.get("compare_id")
    if cmp_id:
        head(f"5. Korelacja z {cmp_id} (market_chart, dzienne stopy zwrotu)")
        c = a.get("compare")
        if c:
            L += [
                row("Korelacja (całość / 30d)", f"{c['corr_all']:.2f} / {c['corr_30d']:.2f}"),
                row(f"Beta {cmp_id} względem BTC", f"{c['beta_vs_btc']:.2f}"),
                row("Zmiana 30d: BTC vs " + cmp_id, f"{ratio_pct(c['btc_30d'])} vs {ratio_pct(c['other_30d'])}"),
            ]
        else:
            missing()

    # --- global
    head("6. Kontekst całego rynku (global)")
    g = ((data.get("global") or {}).get("data")) or None
    if g:
        L += [
            row("Kapitalizacja rynku", f"{big(num((g.get('total_market_cap') or {}).get(vs)))} {cur}"),
            row("Wolumen 24h rynku", f"{big(num((g.get('total_volume') or {}).get(vs)))} {cur}"),
            row("Zmiana kapitalizacji 24h", api_pct(g.get("market_cap_change_percentage_24h_usd"))),
            row("Dominacja BTC / ETH", f"{num((g.get('market_cap_percentage') or {}).get('btc')):.2f}% / "
                                       f"{num((g.get('market_cap_percentage') or {}).get('eth')):.2f}%"),
            row("Aktywne kryptowaluty", f"{g.get('active_cryptocurrencies'):,}" if g.get("active_cryptocurrencies") else "n/d"),
        ]
    else:
        missing()

    # --- derivatives
    head("7. Instrumenty pochodne — perpetual BTC (derivatives)")
    d = a.get("derivatives")
    if d:
        L += [
            row("Kontraktów perpetual", str(d["contracts"])),
            row("Open interest (suma)", f"{big(d['total_oi_usd'])} USD"),
            row("Wolumen 24h (suma)", f"{big(d['total_volume_24h_usd'])} USD"),
            row("Funding rate (ważony OI)", f"{d['weighted_funding_rate']:.4f}%" if d["weighted_funding_rate"] is not None else "n/d"),
            row("Basis (ważony OI)", f"{d['weighted_basis']:.4f}%" if d["weighted_basis"] is not None else "n/d"),
            "Największe rynki wg open interest:",
        ]
        for t in d["top"]:
            fr = f"{t['funding_rate']:.4f}%" if t["funding_rate"] is not None else "n/d"
            L.append(f"   {str(t['market'])[:26]:<27}{str(t['symbol']):<12}OI {big(t['oi_usd']):>12} USD   funding {fr}")
        L.append("Dodatni funding = longi płacą shortom (przewaga kupujących z dźwignią).")
    else:
        missing()

    # --- tickers
    head("8. Płynność na giełdach (coins.tickers)")
    t = a.get("tickers")
    if t:
        for r in t["top"]:
            sp = f"{r['spread_pct']:.3f}%" if r["spread_pct"] is not None else "n/d"
            L.append(f"   {str(r['market'])[:22]:<23}{r['pair']:<12}wol. {big(r['volume_usd']):>10} USD   spread {sp}")
        if t["median_spread_pct"] is not None:
            L.append(row("Mediana spreadu (top 8)", f"{t['median_spread_pct']:.3f}%"))
        if t["dispersion"]:
            x = t["dispersion"]
            L.append(row("Rozbieżność cen (USD/stable)", f"{x['pct']:.3f}%  ({x['low']} → {x['high']})"))
    else:
        missing()

    # --- treasuries
    head("9. Skarbce publiczne (public_treasury)")
    shown = False
    for label, key in (("Spółki publiczne", "treasury_companies"), ("Rządy", "treasury_governments")):
        p = data.get(key)
        if not p:
            continue
        shown = True
        L.append(f"{label}: {num(p.get('total_holdings')) or 0:,.0f} BTC, wartość {big(num(p.get('total_value_usd')))} USD")
        entries = p.get("companies") or p.get("governments") or []
        entries = sorted(entries, key=lambda e: num(e.get("total_holdings")) or 0, reverse=True)[:5]
        for e in entries:
            pct = num(e.get("percentage_of_total_supply"))
            L.append(f"   {str(e.get('name'))[:24]:<25}{num(e.get('total_holdings')) or 0:>12,.0f} BTC"
                     + (f"   ({pct:.3f}% podaży)" if pct is not None else ""))
    if not shown:
        missing()

    # --- fundamentals
    head("10. Fundamenty i sentyment (coins.get_id)")
    dt = data.get("details")
    if dt:
        dev = dt.get("developer_data") or {}
        L += [
            row("Algorytm / czas bloku", f"{dt.get('hashing_algorithm')} / {dt.get('block_time_in_minutes')} min"),
            row("Data genesis", str(dt.get("genesis_date") or "n/d")),
            row("Sentyment (głosy „w górę”)", api_pct(dt.get("sentiment_votes_up_percentage"), 1).replace("+", "")),
            row("Obserwujący na CoinGecko", f"{dt.get('watchlist_portfolio_users'):,}" if dt.get("watchlist_portfolio_users") else "n/d"),
            row("GitHub: commity 4 tyg. / ★", f"{dev.get('commit_count_4_weeks', 'n/d')} / {dev.get('stars', 'n/d')}"),
        ]
    else:
        missing()

    # --- trending
    head("11. Trendujące wyszukiwania (search.trending)")
    tr = (data.get("trending") or {}).get("coins")
    if tr:
        names = []
        btc_in = False
        for entry in tr[:7]:
            item = entry.get("item") or {}
            names.append(f"{item.get('name')} ({str(item.get('symbol', '')).upper()}, #{item.get('market_cap_rank')})")
            btc_in = btc_in or item.get("id") == "bitcoin"
        L.append("; ".join(names))
        L.append("BTC jest wśród trendujących." if btc_in else "BTC nie jest teraz wśród najczęściej wyszukiwanych.")
    else:
        missing()

    # --- news
    if data.get("news") is not None:
        head("12. Newsy o BTC (newsdata.io)")
        n = a.get("news")
        if n:
            labels = {"latest": "/1/latest", "crypto": "/1/crypto (coin=btc)"}
            marks = {1: "▲", -1: "▼", 0: "●"}
            for key, sec in n["sections"].items():
                L.append(f"{labels[key]} — trafień łącznie: {sec['total']}")
                for it in sec["items"][:6]:
                    L.append(f"   {marks[it['verdict']]} {str(it['date'] or '')[:16]}  "
                             f"{str(it['source'] or '')[:18]:<18}  {it['title'][:90]}")
                    if it.get("link"):
                        L.append(f"        {it['link']}")
            t = n["tally"]
            L.append(f"Nagłówki: {t['bull']} bycze, {t['bear']} niedźwiedzie, {t['neutral']} neutralne "
                     f"(metoda: {t['basis']})")
            if n.get("credits_remaining") is not None:
                L.append(row("Pozostałe kredyty newsdata.io", str(n["credits_remaining"])))
            L.append("Uwaga: na planie darmowym newsy mają ok. 12 h opóźnienia; ocena słów kluczowych jest przybliżona.")
        else:
            missing()

    # --- scorecard
    head("13. Scorecard sygnałów (heurystyka, nie prognoza)")
    if a["signals"]:
        icons = {"+": "▲ bycze", "-": "▼ niedźwiedzie", "0": "● neutralne"}
        for s in a["signals"]:
            L.append(f"   {icons[s['verdict']]:<16}{s['label']:<30}{s['detail']}")
        bulls = sum(1 for s in a["signals"] if s["verdict"] == "+")
        bears = sum(1 for s in a["signals"] if s["verdict"] == "-")
        neut = len(a["signals"]) - bulls - bears
        L.append(f"Podsumowanie: {bulls} bycze, {bears} niedźwiedzie, {neut} neutralne")
    else:
        missing()

    # --- bybit
    bb = a.get("bybit")
    if bb:
        full = bb["privacy"] == "full"
        head(f"14. Bybit — API v5, tylko odczyt ({'pełny' if full else 'kwoty zamaskowane'})")
        k = bb.get("key")
        if k:
            L.append(row("Klucz API", "tylko do odczytu" if k["read_only"] else "UWAGA: NIE jest tylko do odczytu"))
            L += [f"! {x}" for x in k["warnings"]]
        m = bb.get("market")
        if m:
            nxt = f", następne {m['next_funding'][11:16]} UTC" if m.get("next_funding") else ""
            fund = f"{m['funding_pct']:.4f}%" if m["funding_pct"] is not None else "n/d"
            L += [
                row(f"{bb['symbol']} mark / index", f"{money(m['mark'], 1)} / {money(m['index'], 1)}  (premia {api_pct(m['premium_pct'], 3)})"),
                row("Funding rate", fund + nxt),
                row("Open interest", f"{big(m['open_interest_usd'])} USD  (zmiana {m['oi_change_hours'] or '?'} godz.: {api_pct(m['oi_change_pct'], 1)})"),
                row("Konta long / short", f"{money(m['long_pct'], 1)}% / {money(m['short_pct'], 1)}%"),
            ]
        acc = bb.get("account")
        if acc:
            L += [row("Wykorzystany margines", f"{money(acc['margin_usage_pct'], 1)}% kapitału"),
                  row("Wskaźnik MM konta", f"{money(acc['mm_rate_pct'], 2)}% (likwidacja przy 100%)"),
                  row("Niezrealizowany PnL", f"{api_pct(acc['upl_pct_of_equity'], 2)} kapitału")]
            if full:
                L += [row("Kapitał / dostępne", f"{money(acc['equity_usd'])} / {money(acc['available_usd'])} USD"),
                      row("Niezrealizowany PnL (USD)", money(acc["unrealised_pnl_usd"]))]
        ex = bb.get("exposure")
        if ex:
            L.append(row("Ekspozycja long / short / netto", f"{money(ex['long_pct'], 0)}% / {money(ex['short_pct'], 0)}% / {api_pct(ex['net_pct'], 0)}"))
        if bb.get("open_orders") is not None:
            L.append(row("Otwarte zlecenia", str(bb["open_orders"])))
        if bb["status"] != "public":
            L.append("Pozycje:" if bb["positions"] else
                     "Pozycje: brak otwartych" if bb.get("positions_ok") else "Pozycje: nie udało się pobrać")
        for p_ in bb["positions"]:
            dist = f", do likwidacji {money(p_['liq_distance_pct'], 1)}%" if p_["liq_distance_pct"] is not None else ""
            L.append(f"   {str(p_['symbol']):<10}{p_['side']:<6}{money(p_['leverage'], 0)}x  ROE {api_pct(p_['roe_pct'], 1)}"
                     f"  ekspozycja {money(p_['exposure_pct'], 0)}% kapitału{dist}")
        kinds = {"tp": "Take Profit", "sl": "Stop Loss", "conditional": "warunkowe", "limit": "limit", "market": "rynkowe"}
        for o_ in bb.get("orders") or []:
            lvl = f" @ {money(o_['price'], 1)}" if o_.get("price") else ""
            dst = f" ({api_pct(o_['distance_pct'], 2)} od mark)" if o_.get("distance_pct") is not None else ""
            L.append(f"   zlecenie {str(o_['symbol']):<10}{(o_.get('side') or '?'):<6}{kinds.get(o_['kind'], o_['kind'])}"
                     f"{' (reduce-only)' if o_['reduce_only'] else ''}{lvl}{dst}")
        pf = bb.get("performance")
        if pf and pf.get("trades"):
            unit = " USD" if pf.get("unit") == "usd" else "% kapitału"
            L += [row(f"Zamknięte pozycje ({pf['days']} dni)", f"{pf['trades']}, skuteczność {money(pf['win_rate_pct'], 0)}%"),
                  row("Profit factor / śr. zysk do straty", f"{money(pf['profit_factor'], 2)} / {money(pf['avg_win_loss_ratio'], 2)}")]
            if pf.get("realized") is not None:
                L.append(row("Zrealizowany wynik", f"{pf['realized']:+,.2f}{unit}"))
        elif pf is not None:
            L.append(row(f"Zamknięte pozycje ({pf['days']} dni)", "brak"))
        L += [f"! {e}" for e in bb["errors"]]

    pl = a.get("planner")
    if pl and pl.get("status") == "ok":
        head("15. Plan analizatora (heurystyka, nie porada inwestycyjna)")
        p_ = pl["plan"]
        L.append(row("Trend / RSI / ATR", f"{p_['trend']} ({p_['trend_score']:+d}/5), RSI {p_['rsi']:.0f}, ATR {_fp(p_['atr'])} ({p_['atr_pct']:.1f}%)"))
        if p_["action"] == "wait":
            L.append(row("Propozycja", "CZEKAJ"))
            L += [f"   - {x}" for x in p_["reasons"]]
            L += [f"   > czekam na: {w['text']}" for w in p_["waiting_for"]]
        else:
            L.append(row("Propozycja", f"{'KUP (long)' if p_['action'] == 'long' else 'SPRZEDAJ (short)'} zleceniem "
                         f"{'warunkowym po wybiciu' if p_.get('entry_type') == 'stop' else 'limit'}, pewność {p_['confidence']}%"))
            L += [row("Wejście / stop", f"{_fp(p_['entry'])} / {_fp(p_['stop'])}  (ryzyko {p_['risk_px_pct']:.1f}% ceny)"),
                  row("TP1 / TP2", f"{_fp(p_['tp1'])} ({p_['r1']}R) / {_fp(p_['tp2'])} ({p_['r2']}R), R:R ważone {p_['rr']}"),
                  row("Rozmiar przy ryzyku", f"{p_['risk_budget_pct']:g}% kapitału => nominał ok. {p_['notional_pct']:.0f}% kapitału")]
            L += [f"   - {x}" for x in p_["reasons"]] + [f"   ! {x}" for x in p_["cautions"]]
            if p_.get("live_text"):
                L.append(f"   Stan: {p_['live_text']}")
        L += [f"   ! {x}" for x in (pl.get("context") or {}).get("notes", [])]
        js = (pl.get("journal") or {}).get("stats") or {}
        if js.get("n"):
            L.append(row("Dziennik (na żywo)", f"{js['n']} rozliczonych, skuteczność {js['win_rate_pct']:.0f}%, średnio {js['avg_r']:+.2f}R, razem {js['total_r']:+.1f}R"))
        else:
            L.append(row("Dziennik (na żywo)", f"brak rozliczonych propozycji (oczekuje: {js.get('pending', 0)}, otwarte: {js.get('open', 0)})"))
        nf = pl.get("notify") or {}
        if nf.get("channels"):
            L.append(row("Powiadomienia", ", ".join({"email": "e-mail", "whatsapp": "WhatsApp"}.get(c, c) for c in nf["channels"]) +
                         (f" (wysłano teraz: {nf['sent_now']})" if nf.get("sent_now") else "")))
        elif nf.get("reason"):
            L.append(row("Powiadomienia", nf["reason"]))
        bt = pl.get("backtest")
        if bt and bt.get("n"):
            L.append(row(f"Test wsteczny ({bt['days']} dni)", f"{bt['n']} transakcji, skuteczność {bt['win_rate_pct']:.0f}%, średnio {bt['avg_r']:+.2f}R, "
                         f"profit factor {money(bt['profit_factor'], 2) if bt.get('profit_factor') is not None else 'n/d'}, max obsunięcie {bt.get('max_drawdown_r', 0):.1f}R; "
                         f"kup i trzymaj {bt['buy_hold_pct']:+.0f}%"))
    elif pl and pl.get("reason"):
        head("15. Plan analizatora")
        L.append(f"! {pl['reason']}")

    lb = pl.get("lab") if pl and pl.get("status") == "ok" else None
    if lb:
        head(f"16. Plany długoterminowe: {lb['from']} do {lb['asof']} ({lb['years']:.1f} lat, koszt {lb['cost_pct']:.2f}% za transakcję)")
        L.append(f"{'Plan':<26}{'wynik/rok':>10}{'maks. DD':>10}{'Calmar':>8}{'Sharpe':>8}{'pod wodą':>10}{'rok: strata':>13}")
        for q in lb["plans"]:
            m_, mc = q["m"], q.get("mc") or {}
            L.append(f"{q['name']:<26}{m_['cagr_pct']:>+9.1f}%{m_['max_dd_pct']:>9.1f}%{(m_['calmar'] or 0):>8.2f}{(m_['sharpe'] or 0):>8.2f}"
                     f"{m_['max_underwater_days']:>8d}d{(mc.get('p_loss') or 0):>12.0f}%")
        d_ = (lb.get("dca") or {}).get("dca")
        if d_:
            L.append(row("DCA co tydzień (100/tydz.)", f"wpłacono {d_['invested']:,.0f}, wartość {d_['value']:,.0f} ({d_['profit_pct']:+.0f}%), "
                         f"średnia cena zakupu {d_['avg_price']:,.0f}, najgorszy moment {d_['worst_pct']:.0f}%, pod kreską {d_['underwater_pct']:.0f}% czasu"))
        rk = lb.get("risk") or {}
        if rk.get("level"):
            L.append(row("Ryzyko rynku dziś", f"{rk['level']} (zmienność 30d {rk['vol_pct']:.0f}% rocznie, percentyl {rk['vol_rank']:.0f}; "
                         f"od szczytu {rk['dd_ath_pct']:.0f}%; reżim: {rk.get('regime') or 'n/d'})"))
        L += [f"   - {x}" for x in lb.get("insights", [])]
        L.append("   ! Wyniki historyczne jednego instrumentu; to nie jest prognoza ani porada inwestycyjna (zobacz zastrzeżenia na stronie).")

    ex = a.get("extras")
    if ex:
        head("Dane dodatkowe (sentyment, on-chain, pochodne, opcje, makro) — opisowe, nie wpływają na plan")
        for sg in ex.get("signals", []):
            L.append(f"  [{ {'bull': '+', 'bear': '-'}.get(sg['state'], '=') }] {sg['label']}: {sg['text']}")
        t = ex.get("tally") or {}
        L.append(f"  Bilans sygnałów: {t.get('bull', 0)} byczych, {t.get('bear', 0)} niedźwiedzich, {t.get('neutral', 0)} neutralnych.")
    if data.get("warnings"):
        head("Ostrzeżenia")
        L += [f"! {w}" for w in data["warnings"]]
    L += ["", "To analiza techniczna na danych historycznych — nie jest poradą inwestycyjną."]
    return "\n".join(L)


# ==========================================================================
# Eksport
# ==========================================================================
def export_csv(path, analysis):
    s = analysis.get("series")
    if not s:
        print("Brak historii — pomijam eksport CSV.", file=sys.stderr)
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "price", "volume", "market_cap"])
        w.writerows(zip(s["date"], s["price"], s["volume"], s["market_cap"]))


def rolling_sma(values, n):
    out = [None] * min(n - 1, len(values))
    for i in range(n - 1, len(values)):
        out.append(sum(values[i - n + 1:i + 1]) / n)
    return out


def web_payload(data, a, report_text):
    """Zwarty JSON dla strony www (bez surowych danych z API, bez kluczy)."""
    vs = a.get("vs", "usd")
    s = a.get("series")
    series = None
    if s:
        closes = s["price"]
        series = {"date": s["date"], "price": closes,
                  "sma20": rolling_sma(closes, 20), "sma50": rolling_sma(closes, 50),
                  "sma200": rolling_sma(closes, 200)}
    mk = (data.get("market") or [None])[0] or {}
    market_keys = ("current_price", "market_cap", "market_cap_rank", "total_volume", "high_24h", "low_24h",
                   "ath", "ath_change_percentage", "circulating_supply", "max_supply",
                   "price_change_percentage_1h_in_currency", "price_change_percentage_24h_in_currency",
                   "price_change_percentage_7d_in_currency", "price_change_percentage_30d_in_currency",
                   "price_change_percentage_200d_in_currency", "price_change_percentage_1y_in_currency")
    g = ((data.get("global") or {}).get("data")) or {}
    spot = ((data.get("spot") or {}).get("bitcoin")) or {}

    def treasury(p):
        if not p:
            return None
        entries = p.get("companies") or p.get("governments") or []
        entries = sorted(entries, key=lambda e: num(e.get("total_holdings")) or 0, reverse=True)[:5]
        return {"total_holdings": num(p.get("total_holdings")), "total_value_usd": num(p.get("total_value_usd")),
                "top": [{"name": e.get("name"), "holdings": num(e.get("total_holdings")),
                         "pct_supply": num(e.get("percentage_of_total_supply"))} for e in entries]}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vs": vs,
        "spot": {c: {"price": num(spot.get(c)), "change_24h": num(spot.get(f"{c}_24h_change"))}
                 for c in data.get("vs_list", []) if spot.get(c) is not None},
        "market": {k: mk.get(k) for k in market_keys} if mk else None,
        "global": {
            "market_cap": num((g.get("total_market_cap") or {}).get(vs)),
            "volume": num((g.get("total_volume") or {}).get(vs)),
            "change_24h": num(g.get("market_cap_change_percentage_24h_usd")),
            "btc_dominance": num((g.get("market_cap_percentage") or {}).get("btc")),
            "eth_dominance": num((g.get("market_cap_percentage") or {}).get("eth")),
            "active_cryptocurrencies": g.get("active_cryptocurrencies"),
        } if g else None,
        "treasury": {"companies": treasury(data.get("treasury_companies")),
                     "governments": treasury(data.get("treasury_governments"))},
        "analysis": {k: v for k, v in a.items() if k not in ("series", "bybit", "planner", "extras")},
        "bybit": a.get("bybit"),
        "planner": a.get("planner"),
        "extras": a.get("extras"),
        "series": series,
        "report_text": report_text,
        "warnings": data.get("warnings", []),
    }


def export_web_json(path, payload):
    """Zapis atomowy: strona nigdy nie odczyta pliku w połowie zapisu."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"), default=str)
    os.replace(tmp, path)


HORIZON_VERSION = 1
HORIZON_MIN_BARS = 330          # 200 dni rozgrzewki SMA200 + co najmniej ok. 130 dni okien do testu
HORIZON_FEE_PCT = 0.15          # koszt rundy w kalkulatorze zagrań: 2 x 0,055% prowizji taker + poślizg, % nominału


def build_horizon_payload(data, a, now_ms=None):
    """Kompaktowa historia dzienna (high, low, close) dla podstrony kalkulator.html, która sama liczy z niej zagrania dla
    przedziału czasu podanego przez użytkownika. Tylko zakończone świece, ostatni ciągły odcinek. None, gdy za mało danych."""
    now_ms = now_ms or int(time.time() * 1000)
    mb = data.get("bybit_market") or {}
    rows = [c for c in (mb.get("candles_d") or []) if c[0] + DAY_MS <= now_ms]
    lng = [c for c in (mb.get("candles_long") or []) if c[0] + DAY_MS <= now_ms]
    if rows and len(lng) > len(rows) and lng[-1][0] == rows[-1][0]:
        rows = lng
    rows = _contiguous_tail(rows)
    if len(rows) < HORIZON_MIN_BARS:
        return None
    rd = lambda x: round(float(x), 2)
    pl = a.get("planner") or {}
    p = pl.get("plan") if pl.get("status") == "ok" else None
    plan = ({k: p.get(k) for k in ("action", "date", "entry", "stop", "tp1", "tp2", "confidence", "entry_type", "live_text")} if p else None)
    live = num((mb.get("ticker") or {}).get("lastPrice")) or a.get("last") or rows[-1][4]
    day = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
    return {"v": HORIZON_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbol": mb.get("symbol", "BTCUSDT"),
            "t0": day(rows[0][0]), "asof": day(rows[-1][0]), "n": len(rows), "price": rd(live), "fee_pct": HORIZON_FEE_PCT,
            "h": [rd(c[2]) for c in rows], "l": [rd(c[3]) for c in rows], "c": [rd(c[4]) for c in rows], "plan": plan}


def export_json(path, data, analysis):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"raw": {k: v for k, v in data.items() if k not in ("bybit", "bybit_market")}, "analysis": analysis}, fh, ensure_ascii=False, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description="Rozbudowana analiza BTC (CoinGecko, newsdata.io, Bybit)")
    parser.add_argument("--days", type=int, default=365, help="dni historii (domyślnie 365; Demo max 365)")
    parser.add_argument("--vs", default="usd", help="waluta wyceny: usd, pln, eur...")
    parser.add_argument("--compare", default="ethereum", help="ID drugiego aktywa do korelacji ('' = wyłącz)")
    parser.add_argument("--csv", help="zapisz historię cen do pliku CSV")
    parser.add_argument("--json", help="zapisz surowe dane i wyniki analizy do JSON (bez surowych danych Bybit)")
    parser.add_argument("--news", choices=["both", "latest", "crypto", "off"], default="both",
                        help="źródło newsów z newsdata.io (wymaga NEWSDATA_API_KEY)")
    parser.add_argument("--news-query", default=NEWS_QUERY, help="fraza szukana w tytułach (limit planu darmowego: 100 znaków)")
    parser.add_argument("--bybit", choices=["auto", "on", "off"], default="auto",
                        help="dane Bybit: auto = gdy są klucze BYBIT_API_KEY/BYBIT_API_SECRET, on = zawsze, off = wyłącz")
    parser.add_argument("--bybit-privacy", choices=["masked", "full"], default=None,
                        help="masked (domyślnie) = bez kwot, rozmiarów i cen wejścia; full = pełne dane konta. Można też BYBIT_PRIVACY w .env")
    parser.add_argument("--plan", choices=["auto", "off"], default="auto",
                        help="planer wejść (propozycje, dziennik, test wsteczny); wymaga publicznych danych Bybit, nie wymaga kluczy")
    parser.add_argument("--journal", default=None, help="plik dziennika propozycji (domyślnie btc_journal.json obok skryptu)")
    parser.add_argument("--no-journal", action="store_true", help="nie zapisuj dziennika (próbny przebieg)")
    parser.add_argument("--lab", choices=["auto", "off"], default="auto",
                        help="laboratorium planów (porównanie strategii długoterminowych i ryzyka); off = pomiń")
    parser.add_argument("--notify", choices=["auto", "off"], default="auto",
                        help="powiadomienia o sygnałach (e-mail, WhatsApp): auto = gdy skonfigurowane w .env, off = nie wysyłaj")
    parser.add_argument("--notify-test", action="store_true", help="wyślij próbną wiadomość każdym skonfigurowanym kanałem i zakończ")
    parser.add_argument("--extras", choices=["auto", "off"], default="auto",
                        help="dodatkowe dane: sentyment, on-chain, pochodne, opcje, makro, stablecoiny (darmowe API bez kluczy)")
    parser.add_argument("--web-json", default=None,
                        help="plik raportu dla strony www (domyślnie btc_report.json w katalogu skryptu)")
    parser.add_argument("--horizon-json", default=None,
                        help="plik z historią dla podstrony kalkulator.html (domyślnie btc_horizon.json obok raportu); 'off' = nie zapisuj")
    parser.add_argument("--print", dest="print_report", action="store_true", help="wypisz pełny raport tekstowy w konsoli")
    parser.add_argument("--quiet", action="store_true", help="nic nie wypisuj (cron)")
    args = parser.parse_args()

    load_dotenv()
    notifier = Notifier()
    if args.notify_test:
        for m in notifier.config_errors + notifier.missing():
            print("! " + m)
        if not notifier.channels:
            print("Brak skonfigurowanego kanału powiadomień (zobacz sekcję NOTIFY_* w .env.example).")
            sys.exit(1)
        res = notifier.test()
        for ch, ok, err in res:
            print(f"{'OK  ' if ok else 'BŁĄD'} {ch}" + ("" if ok else f": {err}"))
        sys.exit(0 if all(r[1] for r in res) else 1)
    vs = args.vs.lower()
    privacy = (args.bybit_privacy or os.environ.get("BYBIT_PRIVACY", "masked")).lower()
    if privacy not in ("masked", "full"):
        privacy = "masked"
    web_json = args.web_json or os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc_report.json")

    data = collect(make_client(), vs, args.days, args.compare or None,
                   news_mode=args.news, news_query=args.news_query, bybit_mode=args.bybit)
    analysis = analyze(data, vs, bybit_privacy=privacy)
    if args.plan != "off":
        try:
            analysis["planner"] = run_planner(data, analysis, privacy, journal_path=args.journal, write=not args.no_journal,
                                              notifier=None if args.notify == "off" else notifier, lab=args.lab != "off")
            if args.notify != "off":
                data["warnings"] += [f"Powiadomienia: {m}" for m in notifier.config_errors + notifier.missing()]
        except Exception as e:  # planer nie może wywrócić całego raportu
            data["warnings"].append(f"Planer: {type(e).__name__}: {e}")
            analysis["planner"] = None
    if (args.horizon_json or "").lower() != "off":
        try:
            hz = build_horizon_payload(data, analysis)
            if hz:
                export_web_json(args.horizon_json or os.path.join(os.path.dirname(os.path.abspath(web_json)), "btc_horizon.json"), hz)
            else:
                data["warnings"].append(f"Historia dla kalkulatora: za mało dziennych świec z Bybit (potrzeba co najmniej {HORIZON_MIN_BARS}), więc kalkulator.html nie ma danych. Sprawdź, czy Bybit odpowiada i czy plik btc_history.json jest zapisywalny.")
        except Exception as e:  # podstrona nie może wywrócić całego raportu
            data["warnings"].append(f"Historia dla kalkulatora: {type(e).__name__}: {e}")
    analysis["extras"] = None
    if args.extras != "off" and btc_extras is not None:
        try:
            ser = analysis.get("series") or {}
            hist_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "btc_extras_history.jsonl")
            analysis["extras"] = btc_extras.collect_extras(ser.get("date"), ser.get("price"), hist_path)
            for name, st in analysis["extras"]["sources"].items():
                if not st.get("ok"):
                    data["warnings"].append(f"Dane dodatkowe ({name}): {st.get('error')}")
        except Exception as e:  # dodatki nie mogą wywrócić całego raportu
            data["warnings"].append(f"Dane dodatkowe: {type(e).__name__}: {e}")
    report_text = render(data, analysis, vs)
    if args.print_report and not args.quiet:
        print(report_text)

    if analysis["history_ok"] or not os.path.exists(web_json):
        export_web_json(web_json, web_payload(data, analysis, report_text))
        if not args.quiet:
            print(f"Zapisano raport: {web_json}")
    else:  # nie nadpisuj dobrego raportu pustym, gdy CoinGecko chwilowo nie odpowiada
        print("Brak danych historycznych — zostawiam poprzedni raport bez zmian.", file=sys.stderr)
        sys.exit(2)
    if args.csv:
        export_csv(args.csv, analysis)
    if args.json:
        export_json(args.json, data, analysis)


if __name__ == "__main__":
    main()
