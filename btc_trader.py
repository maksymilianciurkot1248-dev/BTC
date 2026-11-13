#!/usr/bin/env python3
"""Automatyczny wykonawca planu z btc_analysis.py na Bybit (futures linear, dźwignia <= x2).

NIE UMIESZCZAJ tego pliku ani kluczy w public_html. Trzymaj w katalogu poza WWW, np. ~/btc_trader/.
Strona (index/kalkulator) nigdy nie składa zleceń — robi to wyłącznie ten skrypt z crona.

Tryby (TRADER_MODE): dryrun (domyślny, nic nie wysyła), testnet (api-testnet.bybit.com), demo (Demo Trading na głównym koncie, api-demo.bybit.com), live.
Live wymaga jeszcze TRADER_CONFIRM_LIVE=I_ACCEPT_THE_RISK oraz TRADER_MAX_CAPITAL_USDT.

Konfiguracja: plik trader.env obok skryptu (chmod 600) albo zmienne środowiskowe:
  TRADER_API_KEY, TRADER_API_SECRET   klucz Bybit: tylko Contract/Trade, BEZ wypłat, z whitelistą IP
  TRADER_MAX_CAPITAL_USDT             kapitał, od którego liczymy ryzyko (nigdy więcej niż saldo)
  TRADER_RISK_PCT        (0.5)        % kapitału ryzykowany na pozycję (do stopa)
  TRADER_MAX_LEVERAGE    (2)          twardy limit, nie większy niż 2
  TRADER_MAX_DAILY_LOSS_PCT (2)       po przekroczeniu stop do końca dnia UTC
  TRADER_MAX_ORDERS_PER_DAY (2)
  TRADER_SYMBOL (BTCUSDT), TRADER_BASE_URL (opcjonalnie np. https://api.bybit.eu)
  TRADER_EXPECTED_IP                  IP VPS z whitelisty Bybit; gdy inny wychodzący IP -> skrypt nie handluje
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  powiadomienia o zleceniach, blokadach i błędach (opcjonalnie)
Wyłącznik awaryjny: utwórz pusty plik STOP obok skryptu — skrypt przestaje otwierać pozycje.

Cron (co 5 min):  */5 * * * * cd ~/btc_trader && /usr/bin/python3 btc_trader.py >> trader.log 2>&1
"""
import atexit
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_analysis as ba  # noqa: E402  (ten sam katalog)
import btc_botcfg as bb  # noqa: E402  (ustawienia z panelu, status, zdarzenia)
import btc_edge as be  # noqa: E402  (mapa przewagi, filtry kontekstu, kwarantanna)
import btc_risk as rk  # noqa: E402  (ryzyko portfela z korelacją i wyłączniki)

HARD_MAX_LEVERAGE = 2.0
RECV = "10000"
BASES = {"testnet": "https://api-testnet.bybit.com", "demo": "https://api-demo.bybit.com", "live": "https://api.bybit.com"}


BOT_LINK_RE = re.compile(r"^[po][0-9a-f]{20,40}$")     # orderLinkId zleceń bota (p...) i zleceń ze strony (o...)
SUPERVISE_ALL = os.environ.get("TRADER_SUPERVISE", "bot").lower() == "all"
LOGBUF = []     # linie logu z bieżącego uruchomienia (trafiają do dziennika zdarzeń w panelu)


def log(msg):
    LOGBUF.append(msg)
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


NOTIFY_STATE = os.path.join(HERE, ".notify_state.json")
NOTIFY_QUEUE = []       # powiadomienia z bieżącego przebiegu: wychodzą jako JEDNA wiadomość (flush_notify)


def _notify_seen(key, cooldown_h, now=None):
    """True, gdy to samo powiadomienie poszło już w ostatnich cooldown_h godzinach (wtedy tylko dziennik, bez Telegrama)."""
    now = now or time.time()
    try:
        with open(NOTIFY_STATE, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:  # noqa: BLE001
        seen = {}
    if now - seen.get(key, 0) < cooldown_h * 3600:
        return True
    seen = {k: v for k, v in seen.items() if now - v < 7 * 86400}
    seen[key] = now
    try:
        with open(NOTIFY_STATE, "w", encoding="utf-8") as f:
            json.dump(seen, f)
    except OSError:
        pass
    return False


def notify(msg, key=None, cooldown_h=0, quiet=False):
    """Powiadomienie Telegram (opcjonalne). Zawsze trafia do dziennika; na Telegram tylko rzeczy ważne:
    quiet=True       tylko dziennik i panel,
    key + cooldown_h ten sam stan (np. przekroczony limit, powtarzający się błąd) zgłaszamy raz na cooldown_h godzin,
    reszta           zbierana i wysyłana jedną wiadomością na koniec przebiegu (flush_notify)."""
    log("NOTIFY " + msg)
    if quiet or (key and cooldown_h and _notify_seen(key, cooldown_h)):
        return
    if msg not in NOTIFY_QUEUE:
        NOTIFY_QUEUE.append(msg)


def flush_notify():
    """Wysyła zebrane powiadomienia jedną wiadomością. Błąd wysyłki nigdy nie przerywa handlu."""
    msgs, NOTIFY_QUEUE[:] = list(NOTIFY_QUEUE), []
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (msgs and tok and chat):
        return
    mode = (os.environ.get("TRADER_MODE") or "dryrun").lower()
    text = f"Bot ({mode})\n" + "\n".join("• " + m[:400] for m in msgs[:12]) + (f"\n… i {len(msgs) - 12} więcej (panel, zakładka Bot)" if len(msgs) > 12 else "")
    try:
        data = json.dumps({"chat_id": chat, "text": text[:3900]}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data,
                               headers={"Content-Type": "application/json"}), timeout=10).read()
    except Exception as e:  # noqa: BLE001
        log(f"telegram: {e}")


atexit.register(flush_notify)       # każdy koniec przebiegu (także po błędzie) wysyła to, co zebrano


def single_instance():
    """Dwa równoległe uruchomienia nigdy nie handlują naraz."""
    fh = open(os.path.join(HERE, ".trader.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("inna instancja już działa")
    return fh


def ip_check():
    exp = os.environ.get("TRADER_EXPECTED_IP")
    if not exp:
        return
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
            ip = r.read().decode().strip()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"nie mogę ustalić własnego IP ({e}): nie handluję")
    if ip != exp:
        sys.exit(f"IP wychodzące {ip} != oczekiwane {exp}: nie handluję")


def load_env():
    path = os.path.join(HERE, "trader.env")
    if os.path.exists(path) and os.name == "posix" and os.stat(path).st_mode & 0o077:
        sys.exit("trader.env jest czytelny dla innych użytkowników. Wykonaj: chmod 600 trader.env")
    if os.path.abspath(HERE).split(os.sep)[-1] in ("public_html", "www", "htdocs") or "public_html" in HERE:
        sys.exit("btc_trader.py i trader.env nie mogą leżeć w public_html. Przenieś je poza folder WWW.")
    ba.load_dotenv([path, os.path.join(HERE, ".env")])      # klucze BYBIT_TEST_* / BYBIT_LIVE_* z panelu kluczy leżą w .env


def pick_keys(mode, e):
    """Zestaw kluczy według trybu: testnet i demo używają klucza TESTOWEGO, live używa PRAWDZIWEGO.
    Stare nazwy TRADER_API_KEY / TRADER_API_SECRET nadal działają jako rezerwa."""
    if mode in ("testnet", "demo"):
        k, sec = e("BYBIT_TEST_API_KEY"), e("BYBIT_TEST_API_SECRET")
    elif mode == "live":
        k, sec = e("BYBIT_LIVE_API_KEY"), e("BYBIT_LIVE_API_SECRET")
    else:
        k = sec = None
    return (k or e("TRADER_API_KEY", ""), sec or e("TRADER_API_SECRET", ""))


def key_age_check(c):
    """Klucz bez whitelisty IP wygasa po ok. 90 dniach: ostrzegaj wcześniej (TRADER_KEY_CREATED=RRRR-MM-DD)."""
    created = os.environ.get("TRADER_KEY_CREATED")
    if not created:
        return
    try:
        age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(created).date()).days
    except ValueError:
        sys.exit("TRADER_KEY_CREATED musi mieć format RRRR-MM-DD")
    if age >= 75:      # tylko przypomnienie: Bybit sam odrzuci wygasły klucz bez ograniczenia IP, a bot nie przerywa pracy
        log(f"UWAGA: klucz ma {age} dni, klucze bez ograniczenia IP wygasają po ok. 90. Odnów go.")


def cfg():
    e = os.environ.get
    mode = (e("TRADER_MODE") or "dryrun").lower()
    if mode not in ("dryrun", "testnet", "demo", "live"):
        sys.exit("TRADER_MODE musi być: dryrun | testnet | demo | live")
    c = {
        "mode": mode,
        "key": pick_keys(mode, e)[0], "secret": pick_keys(mode, e)[1],
        "symbol": e("TRADER_SYMBOL", "BTCUSDT"),
        "exit_mode": (e("TRADER_EXIT_MODE") or "staged").lower(),
        "max_heat_pct": float(e("TRADER_MAX_HEAT_PCT", "3")), "streak_losses": int(e("TRADER_STREAK_LOSSES", "4")), "streak_pause_h": int(e("TRADER_STREAK_PAUSE_H", "24")),
        "max_weekly_loss_pct": float(e("TRADER_MAX_WEEKLY_LOSS_PCT", "6")), "max_drawdown_pct": float(e("TRADER_MAX_DRAWDOWN_PCT", "12")),
        "use_take_profit": True, "stop_mult": 1.0, "max_positions": 1, "fixed_notional": 0.0,
        "risk_pct": float(e("TRADER_RISK_PCT", "0.5")),
        "max_lev": min(float(e("TRADER_MAX_LEVERAGE", "2")), HARD_MAX_LEVERAGE),
        "max_daily_loss_pct": float(e("TRADER_MAX_DAILY_LOSS_PCT", "2")),
        "max_orders_day": int(e("TRADER_MAX_ORDERS_PER_DAY", "2")),
        "max_capital": float(e("TRADER_MAX_CAPITAL_USDT", "0") or 0),
    }
    syms = [x.strip().upper() for x in (e("TRADER_SYMBOLS") or c["symbol"]).split(",") if x.strip()]
    if not syms or len(syms) > bb.MAX_SYMBOLS or any(not bb.SYMBOL_RE.match(x) for x in syms):
        sys.exit(f"TRADER_SYMBOLS: podaj 1-{bb.MAX_SYMBOLS} symboli typu BTCUSDT, oddzielonych przecinkami")
    if c["exit_mode"] not in ("staged", "single"):
        sys.exit("TRADER_EXIT_MODE musi być: staged | single")
    c["symbols"] = syms
    c["base"] = (e("TRADER_BASE_URL") or BASES.get(mode, BASES["live"])).rstrip("/")
    if mode == "testnet" and "testnet" not in c["base"]:
        sys.exit("Tryb testnet wymaga adresu testnet.")
    if mode == "demo" and "api-demo" not in c["base"]:
        sys.exit("Tryb demo wymaga adresu api-demo.bybit.com.")
    if mode == "live":
        if e("TRADER_CONFIRM_LIVE") != "I_ACCEPT_THE_RISK":
            sys.exit("Live wymaga TRADER_CONFIRM_LIVE=I_ACCEPT_THE_RISK")
        if c["max_capital"] <= 0:
            sys.exit("Live wymaga TRADER_MAX_CAPITAL_USDT > 0")
    if mode != "dryrun" and not (c["key"] and c["secret"]):
        sys.exit("Brak TRADER_API_KEY / TRADER_API_SECRET")
    if not (0 < c["risk_pct"] <= 2):
        sys.exit("TRADER_RISK_PCT musi być w (0, 2]")
    return c


# --------------------------------------------------------------------------
# Klient z podpisem (osobny od BybitClient, który jest tylko do odczytu)
# --------------------------------------------------------------------------
class Api:
    def __init__(self, c):
        self.c = c

    def call(self, method, path, params=None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        ts = str(int(time.time() * 1000))
        if method == "GET":
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            payload, url, data = qs, self.c["base"] + path + (f"?{qs}" if qs else ""), None
        else:
            payload = json.dumps(params, separators=(",", ":"))
            url, data = self.c["base"] + path, payload.encode()
        sig = hmac.new(self.c["secret"].encode(), (ts + self.c["key"] + RECV + payload).encode(), hashlib.sha256).hexdigest()
        h = {"X-BAPI-API-KEY": self.c["key"], "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": RECV,
             "X-BAPI-SIGN": sig, "Content-Type": "application/json", "User-Agent": "btc-trader/1.0"}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=15) as r:
                body = json.load(r)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise RuntimeError(f"{path}: {e}") from None
        return body

    def ok(self, method, path, params=None, allow=()):
        b = self.call(method, path, params)
        if b.get("retCode") not in (0, *allow):
            raise RuntimeError(f"{path}: {b.get('retMsg')} (retCode {b.get('retCode')})")
        return b.get("result") or {}


# --------------------------------------------------------------------------
# Czysta logika (testowalna bez sieci)
# --------------------------------------------------------------------------
def size_position(capital, risk_pct, entry, stop, max_lev, qty_step, min_qty):
    """Ilość BTC: ryzyko do stopa = risk_pct kapitału, ale nominał <= max_lev * kapitał. Zaokrąglenie W DÓŁ."""
    dist = abs(entry - stop)
    if capital <= 0 or dist <= 0 or entry <= 0:
        return 0.0
    qty = min(capital * risk_pct / 100 / dist, capital * max_lev / entry)
    qty = math.floor(qty / qty_step + 1e-9) * qty_step
    return round(qty, 8) if qty >= min_qty else 0.0


def plan_ok_to_trade(plan, now_ms):
    """Zwraca (True, '') albo (False, powód)."""
    if not plan or plan.get("action") not in ("long", "short"):
        return False, "plan: czekaj"
    if now_ms > plan["valid_until_ms"]:
        return False, "plan wygasł"
    if plan.get("live_state") not in ("waiting", "near"):
        return False, f"stan planu: {plan.get('live_state')}"
    for k in ("entry", "stop", "tp1"):
        if not plan.get(k):
            return False, f"brak pola {k}"
    sg = 1 if plan["action"] == "long" else -1
    if not (plan["stop"] * sg < plan["entry"] * sg < plan["tp1"] * sg):
        return False, "niespójne poziomy stop/entry/tp"
    return True, ""


def fmt_px(x, tick):
    return f"{round(round(x / tick) * tick, 8):.{max(0, -int(math.floor(math.log10(tick))))}f}"


# --------------------------------------------------------------------------
def load_state():
    p = os.path.join(HERE, "trader_state.json")
    try:
        with open(p) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    today = datetime.now(timezone.utc).date().isoformat()
    if s.get("day") != today:
        s = {"day": today, "orders": 0, "placed": s.get("placed", [])[-50:], "halted": False, "managed": s.get("managed", {}), "trades": s.get("trades", {}), "risk": s.get("risk", {}),
             "ledger_pending": s.get("ledger_pending", [])}
    return s


def save_state(s):
    p = os.path.join(HERE, "trader_state.json")
    with open(p + ".tmp", "w") as f:
        json.dump(s, f)
    os.replace(p + ".tmp", p)


def make_pub(c):
    return ba.BybitClient(base_url=c["base"] if c["mode"] in ("testnet", "live") else os.environ.get("BYBIT_BASE_URL"))   # demo: dane publiczne z głównego adresu


def get_plan(c, symbol, pub=None, btc=None, edge=None):
    """Plan dla aktywa wg wybranej strategii: klasyczny (btc_analysis.plan_at) albo zespół sygnałów (btc_decision)."""
    pub = pub or make_pub(c)
    now = int(time.time() * 1000)
    candles = [x for x in pub.klines(symbol, "D", 1000) if x[0] + ba.DAY_MS <= now]
    if len(candles) < ba.PLAN_WARMUP + 40:
        raise RuntimeError("za mało świec")
    tk = pub.ticker(symbol)
    price = float(tk["lastPrice"])
    ind = ba.build_indicators(candles)
    if c.get("strategy") == "ensemble":
        import btc_decision
        try:
            fund = float(tk.get("fundingRate")) if tk.get("fundingRate") not in (None, "") else None
        except ValueError:
            fund = None
        import btc_lab
        reg = btc_lab.variants(HERE)
        vid = c.get("ens_variant") if c.get("ens_variant") in reg else btc_lab.CHAMPION_DEFAULT        # nieznany lub brak: wariant domyślny
        eng = btc_decision.Engine(symbol, btc_candles=btc, events_s=(edge or {}).get("events"), overlay={"funding": fund} if fund is not None else None,
                                  params=reg[vid]["params"], variant=vid)
        plan = eng.plan_at(ind, len(candles) - 1, c["risk_pct"])
    else:
        plan = ba.plan_at(ind, len(candles) - 1, c["risk_pct"])
    return ba.decorate_plan(plan, price), price, pub


MIN_TURNOVER_USD = 20_000_000     # poniżej tej dobowej płynności aktywo jest pomijane (duży poślizg i rozstrzał)


def adjust_stop(plan, mult):
    """Przesuwa stop-loss o mnożnik odległości od wejścia (cel zostaje). Zwraca kopię planu."""
    p = dict(plan)
    if p.get("entry") and p.get("stop") and mult != 1.0:
        p["stop"] = p["entry"] - (p["entry"] - p["stop"]) * mult
    return p


def scalp_symbols():
    """Aktywa z otwartą pozycją scalpera (osobna usługa btc-scalp): nie liczą się do limitu pozycji bota dziennego."""
    try:
        with open(os.path.join(HERE, "scalp_state.json"), encoding="utf-8") as f:
            return set((json.load(f).get("live") or {}).get("open") or {})
    except (OSError, ValueError):
        return set()


def notional_left(capital, max_lev, open_values):
    """Ile nominału wolno jeszcze otworzyć, żeby suma wszystkich pozycji nie przekroczyła max_lev * kapitał."""
    return max(0.0, capital * max_lev - sum(open_values))


STATS_EVERY_S = 3600


def event_kind(line):
    if line.startswith("NOTIFY"):
        return "alert"
    if line.startswith(("PLAN", "DRYRUN")):
        return "plan"
    if line.startswith(("start tryb", "ustawienia z panelu")):
        return "info"
    return "decision"


def _closed_pages(api, start, end, symbol=None):
    rows, cursor = [], None
    for _page in range(5):
        res = api.ok("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": symbol, "startTime": start, "endTime": end, "limit": 100, "cursor": cursor})
        rows += res.get("list") or []
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
    return rows


def closed_pnl_range(api, c, start, end):
    """Zamknięte pozycje ze wszystkich symboli; gdy giełda wymaga symbolu, pytamy o każdy symbol z listy."""
    try:
        return _closed_pages(api, start, end)
    except RuntimeError:
        rows = []
        for sym in c["symbols"]:
            rows += _closed_pages(api, start, end, sym)
        return rows


def fetch_closed_pnl(api, c, days=56):
    """Zamknięte pozycje z ostatnich `days` dni (okna po 7 dni, stronicowanie kursorem)."""
    rows, end = [], int(time.time() * 1000)
    for _ in range(max(1, days // 7)):
        start = end - 7 * 86400_000
        rows += closed_pnl_range(api, c, start, end)
        end = start - 1
    return rows


def run():
    load_env()
    c = cfg()
    c["_settings"] = bb.read_settings(HERE)
    changes = bb.apply_settings(c, c["_settings"])
    st = {"ts": int(time.time()), "mode": c["mode"], "symbols": c["symbols"], "ok": True}
    try:
        if changes:
            log("ustawienia z panelu: " + "; ".join(changes))
        _run(c, st)
    except SystemExit as e:
        st["ok"] = False
        st["error"] = str(e.code)[:200] if e.code not in (None, 0) else None
        raise
    except Exception as e:  # noqa: BLE001
        st["ok"] = False
        st["error"] = str(e)[:200]
        raise
    finally:
        try:
            finish_status(c, st)
        except Exception as e:  # noqa: BLE001 — panel nie może zepsuć handlu
            print(f"status: {e}", flush=True)


def finish_status(c, st):
    prev = bb.read_status(HERE)
    for line in LOGBUF:
        if not line.startswith("start tryb"):
            bb.append_event(HERE, event_kind(line), line)
    if st.get("error"):
        bb.append_event(HERE, "alert", "BŁĄD: " + st["error"])
    decisions = [x for x in LOGBUF if not x.startswith(("start tryb", "ustawienia z panelu"))]
    st["decision"] = (decisions[-1] if decisions else st.get("error") or "")[:300]
    st["effective"] = {k: c.get(k) for k in bb.BOUNDS}
    st["effective"].update(directions=c.get("directions"), paused=c.get("paused"))
    st["settings_updated"] = c.get("_settings", {}).get("updated")
    st["stop_file"] = os.path.exists(os.path.join(HERE, "STOP"))
    if "stats" not in st and prev.get("stats"):
        st["stats"], st["stats_ts"] = prev["stats"], prev.get("stats_ts")
    bb.write_status(HERE, st)


def _run(c, st):
    _lock = single_instance()  # noqa: F841  (trzymamy uchwyt do końca procesu)
    log(f"start tryb={c['mode']} aktywa={','.join(c['symbols'])}")
    if c["mode"] != "dryrun":
        key_age_check(c)
        ip_check()
    state = load_state()
    bb.expire_proposals(HERE)
    api = Api(c) if c["mode"] != "dryrun" else None
    pub = make_pub(c)
    stop_file = os.path.exists(os.path.join(HERE, "STOP"))
    st["symbols"] = c["symbols"]

    # 1) nadzór nad istniejącymi pozycjami (działa też przy STOP: tylko zmniejsza ryzyko)
    info = {"equity": None, "pnl_today": None, "positions": [], "order_syms": [], "breakers": [], "capital": c["max_capital"] or 1000.0}
    if api:
        info = supervise(api, c, state)
        st.update(equity=info["equity"], pnl_today=info["pnl_today"], positions=info["positions"])
        prev = bb.read_status(HERE)
        if time.time() - (prev.get("stats_ts") or 0) > STATS_EVERY_S:
            try:
                rows = fetch_closed_pnl(api, c)
                st["stats"], st["stats_ts"] = bb.stats_from_closed(rows), int(time.time())
                capital = min(info["equity"], c["max_capital"]) if c["max_capital"] else info["equity"]
                q, newly = be.quarantine_update(rows, bb.read_quar(HERE), time.time(), capital * c["risk_pct"] / 100)
                bb.write_quar(HERE, q)
                for sym in newly:
                    notify(f"[{sym}] seria strat: kwarantanna na 7 dni", key=f"quar:{sym}", cooldown_h=24)
            except Exception as e:  # noqa: BLE001 — statystyki są dodatkiem
                log(f"statystyki: {e}")
    info["items"] = rk.position_items(info.get("raw_pos") or [])
    info["rcache"] = {}
    try:
        corr0 = risk_corr(pub, sorted({i["symbol"] for i in info["items"]}), info["rcache"]) if len(info["items"]) > 1 else {}
        heat = rk.summary(info["items"], corr0, info["capital"])
    except Exception as e:  # noqa: BLE001
        heat = {"error": str(e)[:80]}
    prev_keys = {b["key"] for b in ((bb.read_status(HERE).get("risk") or {}).get("breakers") or [])}
    for b in info.get("breakers") or []:
        if b["key"] not in prev_keys:
            notify(f"WYŁĄCZNIK RYZYKA: {b['label']} ({b['detail']}). Nowe pozycje wstrzymane.")
    st["risk"] = {"heat": heat, "limit_pct": c["max_heat_pct"], "limit_usdt": round(info["capital"] * c["max_heat_pct"] / 100, 2), "capital": round(info["capital"], 2),
                  "breakers": info.get("breakers") or [], "metrics": info.get("risk_metrics") or {}, "limits": {k: c[k] for k in ("streak_losses", "streak_pause_h", "max_weekly_loss_pct", "max_drawdown_pct", "max_daily_loss_pct")}}
    bb.write_risk(HERE, {"ts": int(time.time()), **st["risk"]})
    st.update(halted=bool(state.get("halted")), orders_today=state.get("orders", 0), max_orders=c["max_orders_day"])
    if stop_file or c.get("paused"):
        log("pauza: plik STOP lub ustawienie z panelu, nie otwieram nowych pozycji")
        return save_state(state)
    if state["halted"]:
        log("dzienny limit straty osiągnięty: pauza do końca dnia UTC")
        return save_state(state)
    if info.get("breakers"):
        for b in info["breakers"]:
            log(f"wyłącznik ryzyka: {b['label']} ({b['detail']}), nie otwieram nowych pozycji")
        return save_state(state)

    # 2) wybór aktywów: ręczne + dopuszczone przez mapę przewagi (jeśli włączony automat) i filtry kontekstu
    edge, quar = bb.read_edge(HERE), bb.read_quar(HERE)
    if c.get("auto_blocked_live"):
        log("automatyczny wybór aktywów jest wyłączony w trybie live (wymaga TRADER_AUTO_LIVE=yes w trader.env)")
    strat = c.get("strategy", "plan")
    st["strategy"] = strat
    try:
        import btc_lab
        st["variant"] = btc_lab.champion(HERE)
        c["ens_variant"] = st["variant"]
        classic = next((v for v in (btc_lab.read(HERE).get("variants") or []) if v.get("id") == "plan"), None)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if strat == "plan" and classic and classic["hist"]["n"] >= 100 and classic["hist"]["mean_r"] < 0 and classic["hist"]["t_week"] <= -2 and state.get("warned_plan") != day:
            state["warned_plan"] = day
            log(f"UWAGA: strategia klasyczna ma w teście na historii {classic['hist']['mean_r']:+.2f}R na transakcję ({classic['hist']['n']} transakcji, wynik istotnie ujemny). "
                f"Rozważ przełączenie na zespół sygnałów w zakładce Bot.")
    except Exception as e:  # noqa: BLE001 — laboratorium jest dodatkiem
        log(f"laboratorium: {e}")
    scan, notes = be.select_symbols(edge, c["symbols"], quar, time.time(), c.get("auto", False), strategy=strat)
    st["auto"] = {"enabled": bool(c.get("auto")), "scan": scan, "notes": notes[:8], "edge_ts": edge.get("ts"), "allowed": edge.get("allowed", []), "held": be.held_symbols(edge, strat)}
    for n in notes:
        log(n)
    ctx = None
    btc = None
    if strat == "ensemble":
        try:
            btc = [x for x in pub.klines("BTCUSDT", "D", 400) if x[0] + ba.DAY_MS <= time.time() * 1000]
        except Exception as e:  # noqa: BLE001
            log(f"zespół sygnałów bez kontekstu BTC ({e})")
    if any((edge.get("assets_ens" if strat == "ensemble" else "assets", {}).get(x) or {}).get("filters") for x in scan):
        try:
            ctx = be.context_now(pub.klines("BTCUSDT", "D", 120), edge, time.time())
            st["auto"]["context"] = ctx
        except Exception as e:  # noqa: BLE001
            log(f"kontekst rynku niedostępny ({e}): aktywa z filtrami pomijam")
            ctx = {"btc_down": True, "near_event": True}
    st["plans"], st["decisions"] = {}, {}
    for sym in scan:
        if state["orders"] >= c["max_orders_day"]:
            log("limit zleceń na dobę osiągnięty")
            break
        try:
            consider(sym, c, api, pub, state, info, st, edge, ctx, btc)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — błąd jednego aktywa nie blokuje pozostałych
            log(f"[{sym}] błąd: {e}")
    return save_state(state)


def consider(sym, c, api, pub, state, info, st, edge=None, ctx=None, btc=None):
    """Ocena planu jednego aktywa i ewentualne złożenie zlecenia."""
    tag = f"[{sym}] "
    if info.get("over_limit"):
        log(tag + "suma pozycji przekracza limit dźwigni: nie otwieram nowych")
        return
    if sym in {p["symbol"] for p in info["positions"]}:
        log(tag + "pozycja już otwarta: nie dokładam")
        return
    if len([p for p in info["positions"] if p["symbol"] not in scalp_symbols()]) >= c["max_positions"]:
        log(tag + f"limit jednoczesnych pozycji ({c['max_positions']}) osiągnięty")
        return
    if sym in info["order_syms"]:
        log(tag + "są otwarte zlecenia: nie dokładam")
        return
    blocked = be.blocked_by_filters(edge, sym, ctx or {}, c.get("strategy", "plan"))
    if blocked:
        log(tag + f"pomijam: filtr kontekstu ({', '.join(blocked)}): w takich warunkach historia pokazuje gorsze wyniki")
        return
    plan, price, _ = get_plan(c, sym, pub, btc, edge)
    if plan and plan.get("decision"):
        st["decisions"][sym] = {k: plan["decision"][k] for k in ("score", "confidence", "agree", "regime_text", "action", "families", "vetoes", "notes", "reasons", "size_scalar")}
    plan = adjust_stop(plan, c["stop_mult"]) if plan and plan.get("action") in ("long", "short") else plan
    ok, why = plan_ok_to_trade(plan, int(time.time() * 1000))
    st["plans"][sym] = {k: plan.get(k) for k in ("action", "setup", "entry", "stop", "tp1", "live_state")} if plan else None
    if not ok:
        log(tag + f"brak zlecenia: {why}")
        return
    if plan["action"] not in c["directions"]:
        log(tag + f"brak zlecenia: kierunek {plan['action']} wyłączony w ustawieniach panelu")
        return
    key = f"{sym}-{plan['date']}-{plan['action']}-{plan['setup']}"
    link_id = ("p" + hashlib.sha1(key.encode()).hexdigest())[:32]
    if link_id in state["placed"]:
        log(tag + "to zlecenie już było złożone")
        return
    tk = pub.ticker(sym)
    if float(tk.get("turnover24h") or 0) < MIN_TURNOVER_USD:
        log(tag + "za mała płynność (obrót 24 h poniżej 20 mln USDT): pomijam")
        return
    inst = pub._request("/v5/market/instruments-info", {"category": "linear", "symbol": sym})["list"][0]
    if inst.get("status", "Trading") != "Trading":
        log(tag + f"instrument niedostępny do handlu ({inst.get('status')})")
        return
    qty_step = float(inst["lotSizeFilter"]["qtyStep"]); min_qty = float(inst["lotSizeFilter"]["minOrderQty"])
    tick = float(inst["priceFilter"]["tickSize"])
    if api:
        capital = min(info["equity"], c["max_capital"]) if c["max_capital"] else info["equity"]
    else:
        capital = c["max_capital"] or 1000.0
    left = notional_left(capital, c["max_lev"], [p["value"] for p in info["positions"]])
    qty = size_position(capital, c["risk_pct"], plan["entry"], plan["stop"], c["max_lev"], qty_step, min_qty)
    if plan.get("size_mult") and not c.get("fixed_notional", 0) > 0 and qty:     # zespół sygnałów skaluje pozycję (zmienność, pewność, funding)
        qty = math.floor(qty * plan["size_mult"] / qty_step + 1e-9) * qty_step
    hm = be.size_factor(edge, sym, c.get("strategy", "plan"))      # <1, gdy dowody dla aktywa osłabły (utrzymane przez histerezę mapy przewagi)
    if c.get("fixed_notional", 0) > 0:      # stała, mała wartość pozycji zamiast rozmiaru z ryzyka (nadal w granicach dźwigni i kapitału)
        qty = math.floor(min(c["fixed_notional"] * hm, capital * c["max_lev"]) / plan["entry"] / qty_step + 1e-9) * qty_step
    elif hm < 1.0 and qty:
        qty = math.floor(qty * hm / qty_step + 1e-9) * qty_step
    if hm < 1.0:
        log(tag + f"dowody dla aktywa osłabły (utrzymane przez histerezę mapy przewagi): pozycja zmniejszona do {round(hm * 100)}%")
    qty = min(qty, math.floor(left / plan["entry"] / qty_step + 1e-9) * qty_step) if qty else 0.0
    qty = round(qty, 8) if qty >= min_qty else 0.0
    if qty <= 0:
        log(tag + f"rozmiar poniżej minimum giełdy (min. {min_qty} szt. ≈ {min_qty * plan['entry']:.1f} USDT) albo brak wolnego limitu dźwigni: pomijam")
        return
    qty, hmsg = fit_heat(c, info, pub, sym, plan["action"], plan["entry"], plan["stop"], qty, qty_step, min_qty)
    if hmsg:
        log(tag + hmsg)
    if qty <= 0:
        return
    sg = 1 if plan["action"] == "long" else -1
    entry, stop, tp = (fmt_px(plan[k], tick) for k in ("entry", "stop", "tp1"))
    order = {"category": "linear", "symbol": sym, "side": "Buy" if sg == 1 else "Sell", "qty": f"{qty}",
             "timeInForce": "GTC", "orderLinkId": link_id, "positionIdx": 0,
             "stopLoss": stop, "slTriggerBy": "LastPrice", "tpslMode": "Full", "slOrderType": "Market"}   # stop-loss jest ZAWSZE w zleceniu
    staged = c["use_take_profit"] and c.get("exit_mode") == "staged" and bool(plan.get("tp2"))
    if c["use_take_profit"] and not staged:       # tryb single: jeden cel na całą pozycję (stare zachowanie)
        order.update(takeProfit=tp, tpTriggerBy="LastPrice", tpOrderType="Market")
    if plan.get("entry_type") == "stop":   # wybicie: warunkowe zlecenie limit tuż za poziomem
        slip = plan["entry"] * 0.001 * sg
        order.update(orderType="Limit", price=fmt_px(plan["entry"] + slip, tick), triggerPrice=entry,
                     triggerDirection=1 if sg == 1 else 2, triggerBy="LastPrice")
    else:
        order.update(orderType="Limit", price=entry)
    notional = qty * plan["entry"]
    log(f"PLAN {tag}{plan['action']} {plan['setup']} entry={entry} sl={stop} tp1={tp if c['use_take_profit'] else 'wyłączony'} qty={qty} nominał={notional:.0f} USDT "
        f"(x{notional / capital:.2f} kapitału {capital:.0f}) ryzyko={qty * abs(plan['entry'] - plan['stop']):.2f} USDT")
    if notional > left * 1.001 or notional > capital * c["max_lev"] * 1.001:
        sys.exit("BŁĄD LOGIKI: nominał przekracza limit dźwigni, przerywam")
    if c.get("require_approval"):
        pr = bb.find_proposal(HERE, link_id)
        if pr is None:
            bb.add_proposal(HERE, {"id": link_id, "symbol": sym, "side": plan["action"], "setup": plan.get("setup"), "entry": float(entry), "stop": float(stop),
                                   "tp": float(tp) if c["use_take_profit"] else None, "qty": qty, "notional": round(notional, 2), "risk_usdt": round(qty * abs(plan["entry"] - plan["stop"]), 2),
                                   "leverage": c["max_lev"], "mode": c["mode"]})
            notify(f"[{sym}] wniosek do zatwierdzenia w panelu: {plan['action']} {qty} po {entry}, stop {stop}")
            return
        if pr["status"] == "pending":
            log(tag + "wniosek czeka na Twoją decyzję w panelu")
            return
        if pr["status"] != "approved":
            log(tag + f"wniosek: {pr['status']}, nie stawiam")
            return
        log(tag + "wniosek zatwierdzony przez Ciebie: stawiam pozycję")
    if not api:
        log(tag + "DRYRUN: zlecenia nie wysłano")
        if c.get("require_approval"):
            bb.set_proposal(HERE, link_id, status="placed", note="dryrun: bez zlecenia")
        return
    api.ok("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": sym,
           "buyLeverage": f"{c['max_lev']:g}", "sellLeverage": f"{c['max_lev']:g}"}, allow=(110043,))
    res = api.ok("POST", "/v5/order/create", order)
    state["placed"].append(link_id); state["orders"] += 1
    if staged:       # wyjścia jak w teście: 50% na TP1, stop na wejście, reszta na TP2, zamknięcie po czasie (zarządza manage_trades)
        state.setdefault("trades", {})[link_id] = {"symbol": sym, "side": plan["action"], "qty": qty, "step": qty_step, "min_qty": min_qty, "tick": tick, "entry": float(entry),
                                                   "tp1": float(tp), "tp2": float(fmt_px(plan["tp2"], tick)), "phase": "pending", "created": int(time.time()), "opened": None,
                                                   "stop": float(stop), "plan_date": plan.get("date"), "strategy": plan.get("strategy") or "plan", "variant": plan.get("variant"),
                                                   "max_hold_s": int((plan.get("max_hold_bars") or ba.PLAN_MAX_HOLD_BARS) * 86400)}
    state.setdefault("managed", {})[sym] = int(time.time())          # od teraz bot nadzoruje pozycję na tym aktywie
    if c.get("require_approval"):
        bb.set_proposal(HERE, link_id, status="placed", order_id=res.get("orderId"))
    info["positions"].append({"symbol": sym, "side": "Buy" if sg == 1 else "Sell", "size": qty, "value": notional, "pending": True})
    info.setdefault("items", []).append({"symbol": sym, "side": sg, "risk": qty * abs(plan["entry"] - plan["stop"]), "no_stop": False})
    save_state(state)
    notify(f"[{sym}] zlecenie {plan['action']}: {qty} po {entry}, stop {stop}, cel {tp if c['use_take_profit'] else 'brak'} (x{notional / capital:.2f})")


def consume_reset(c, state, equity, now_s):
    """Odblokowanie wyłączników: plik RISK_RESET na serwerze (zawsze) albo prośba z panelu (w trybie live tylko przy TRADER_WEB_ORDERS=yes)."""
    ok = False
    manual, web = os.path.join(HERE, "RISK_RESET"), os.path.join(HERE, bb.RISK_RESET)
    if os.path.exists(manual):
        ok = True
        os.remove(manual)
    if os.path.exists(web):
        os.remove(web)
        if c["mode"] != "live" or os.environ.get("TRADER_WEB_ORDERS", "").lower() == "yes":
            ok = True
        else:
            log("prośba o odblokowanie z panelu zignorowana w trybie live (utwórz plik RISK_RESET na serwerze albo ustaw TRADER_WEB_ORDERS=yes)")
    if ok:
        state["risk"] = rk.apply_reset(state.get("risk"), equity, now_s)
        notify("wyłączniki ryzyka odblokowane: seria strat i strata tygodniowa liczą się od teraz, szczyt kapitału ustawiony na bieżący", quiet=True)


def risk_corr(pub, symbols, cache):
    """Macierz korelacji dziennych zwrotów (90 dni) dla podanych aktywów; pobiera świece raz na przebieg."""
    now = time.time() * 1000
    for s in symbols:
        if s not in cache:
            try:
                cache[s] = rk.returns_from_candles([x for x in pub.klines(s, "D", 130) if x[0] + ba.DAY_MS <= now])
            except Exception:  # noqa: BLE001 — brak danych: korelacja z priorytetu
                cache[s] = {}
    return rk.corr_matrix({s: cache[s] for s in symbols})


def fit_heat(c, info, pub, sym, side, entry, stop, qty, step, min_qty):
    """Dopasowuje rozmiar nowej pozycji do limitu ryzyka skorelowanego portfela. Zwraca (ilość, komunikat lub None)."""
    capital = info.get("capital") or c["max_capital"] or 1000.0
    limit = capital * c["max_heat_pct"] / 100
    items = info.get("items") or []
    new = {"symbol": sym, "side": 1 if side == "long" else -1, "risk": qty * abs(entry - stop)}
    corr = risk_corr(pub, sorted({i["symbol"] for i in items} | {sym}), info.setdefault("rcache", {}))
    k = rk.fit_scale(items, new, corr, limit)
    before = rk.corr_risk(items, corr)
    if k >= 1:
        return qty, None
    if k < rk.MIN_SCALE:
        return 0.0, f"limit ryzyka portfela: ryzyko skorelowane {before:.2f} USDT z limitu {limit:.2f} USDT ({c['max_heat_pct']:g}% kapitału), nowa pozycja się nie mieści"
    q = round(math.floor(qty * k / step + 1e-9) * step, 8)
    if q < min_qty:
        return 0.0, f"limit ryzyka portfela: po zmniejszeniu pozycji poniżej minimum giełdy ({min_qty}), pomijam"
    return q, f"limit ryzyka portfela: pozycja zmniejszona do {k * 100:.0f}% (ryzyko skorelowane {before:.2f} z {limit:.2f} USDT)"


def supervise(api, c, state):
    """Czyści stare zlecenia, pilnuje stopa i dźwigni na WSZYSTKICH pozycjach konta, liczy dzienną stratę."""
    now = int(time.time() * 1000)
    day0 = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    week = 7 * 86400_000
    rows = closed_pnl_range(api, c, now - week, now) + closed_pnl_range(api, c, now - 2 * week, now - week - 1)          # 14 dni: seria strat i strata tygodniowa
    tsof = lambda x: float(x.get("updatedTime") or x.get("createdTime") or now)       # noqa: E731
    pnl = sum(float(x["closedPnl"]) for x in rows if tsof(x) >= day0)
    eq = float(api.ok("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})["list"][0]["totalEquity"])
    base = min(eq, c["max_capital"]) if c["max_capital"] else eq
    info = {"equity": eq, "pnl_today": round(pnl, 4), "positions": [], "order_syms": [], "capital": base}
    consume_reset(c, state, eq, now / 1000)
    info["breakers"], state["risk"], info["risk_metrics"] = rk.evaluate(now / 1000, rows, eq, base, c, state.get("risk"))
    if pnl < 0 and -pnl >= base * c["max_daily_loss_pct"] / 100:
        if not state["halted"]:
            notify(f"STRATA DZIENNA {pnl:.2f} USDT >= limit: handel wstrzymany do końca dnia UTC")
        state["halted"] = True
    # niewypełnione zlecenia starsze niż ważność planu (2 dni)
    raw_orders = api.ok("GET", "/v5/order/realtime", {"category": "linear", "settleCoin": "USDT"})["list"]
    for o in raw_orders:
        if int(o["createdTime"]) < now - ba.PLAN_VALID_BARS * ba.DAY_MS and BOT_LINK_RE.match(o.get("orderLinkId") or ""):      # tylko zlecenia bota, nigdy Twoje ręczne
            api.ok("POST", "/v5/order/cancel", {"category": "linear", "symbol": o["symbol"], "orderId": o["orderId"]})
            log(f"[{o['symbol']}] anulowano stare zlecenie {o['orderId']}")
        else:
            info["order_syms"].append(o["symbol"])
    # pozycje bota: każda musi mieć SL i dźwignię <= limit
    managed = {s for s, ts in (state.get("managed") or {}).items() if ts > now / 1000 - 30 * 86400}
    total = 0.0
    raw_pos = api.ok("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT"})["list"]
    for p in raw_pos:
        size = float(p["size"])
        if size <= 0:
            continue
        value = float(p["positionValue"])
        total += value
        mine = SUPERVISE_ALL or p["symbol"] in managed
        info["positions"].append({"symbol": p["symbol"], "side": p["side"], "size": size, "entry": p.get("avgPrice"), "stop": p.get("stopLoss"), "tp": p.get("takeProfit"),
                                  "leverage": p.get("leverage"), "upnl": p.get("unrealisedPnl"), "value": value})
        bad_lev = float(p["leverage"]) > c["max_lev"] + 1e-9 or value > base * c["max_lev"] * 1.05
        no_sl = not p.get("stopLoss") or float(p["stopLoss"]) == 0
        if (bad_lev or no_sl) and not mine:       # pozycja otwarta ręcznie w aplikacji Bybit: tylko ostrzeżenie raz dziennie, bez zamykania
            if state.setdefault("warned", {}).get(p["symbol"]) != state.get("day"):
                state["warned"][p["symbol"]] = state.get("day")
                notify(f"[{p['symbol']}] Twoja ręczna pozycja ma dźwignię x{p['leverage']} lub brak stop-lossa: bot jej nie zamyka (zarządza tylko swoimi pozycjami; wyjątek: TRADER_SUPERVISE=all)", quiet=True)
        elif bad_lev or no_sl:
            notify(f"[{p['symbol']}] POZYCJA NIEZGODNA Z ZASADAMI (lev={p['leverage']}, sl={p.get('stopLoss')}): zamykam rynkowo")
            api.ok("POST", "/v5/order/create", {"category": "linear", "symbol": p["symbol"],
                   "side": "Sell" if p["side"] == "Buy" else "Buy", "orderType": "Market", "qty": p["size"],
                   "reduceOnly": True, "positionIdx": 0})
            state["halted"] = True
    if total > base * c["max_lev"] * 1.05:
        notify(f"SUMA POZYCJI {total:.0f} USDT przekracza limit dźwigni x{c['max_lev']:g} kapitału {base:.0f}: nie otwieram nowych", key="sum_lev", cooldown_h=24)
        info["over_limit"] = True
    manage_trades(api, state, raw_pos, raw_orders, time.time())
    settle_ledger(state, rows, time.time(), c["mode"])
    info["raw_pos"] = raw_pos
    return info


# --------------------------------------------------------------------------
# Wyjścia jak w teście wstecznym: 50% na TP1 -> stop na wejście -> reszta na TP2 -> zamknięcie po PLAN_MAX_HOLD_BARS dniach
# --------------------------------------------------------------------------
TIME_STOP_S = ba.PLAN_MAX_HOLD_BARS * 86400


def tp_link(link_id, n):
    return "t" + link_id[1:31] + str(n)


def place_tps(api, tr, link_id, size, have=()):
    """Dwa zlecenia limit reduce-only (TP1 na połowę, TP2 na resztę); gdy połowa jest poniżej minimum giełdy, jeden cel na całość."""
    step, mn, tick = tr["step"], tr["min_qty"], tr["tick"]
    q1 = round(math.floor(size / 2 / step + 1e-9) * step, 8)
    q2 = round(size - q1, 8)
    side = "Sell" if tr["side"] == "long" else "Buy"
    base = {"category": "linear", "symbol": tr["symbol"], "side": side, "orderType": "Limit", "reduceOnly": True, "timeInForce": "GTC", "positionIdx": 0}
    if q1 >= mn and q2 >= mn:
        for n, q, px in ((1, q1, tr["tp1"]), (2, q2, tr["tp2"])):
            if tp_link(link_id, n) not in have:          # po częściowym błędzie nie dublujemy już złożonych celów
                api.ok("POST", "/v5/order/create", {**base, "qty": f"{q}", "price": fmt_px(px, tick), "orderLinkId": tp_link(link_id, n)})
        tr["split"] = True
    else:
        if tp_link(link_id, 1) not in have:
            api.ok("POST", "/v5/order/create", {**base, "qty": f"{size}", "price": fmt_px(tr["tp1"], tick), "orderLinkId": tp_link(link_id, 1)})
        tr["split"] = False
    tr["tp_ok"] = True


def manage_trades(api, state, raw_pos, raw_orders, now):
    """Prowadzi otwarte transakcje bota: cele częściowe, stop na wejście po TP1, zamknięcie po czasie, sprzątanie zleceń."""
    trades = state.get("trades") or {}
    pos = {p["symbol"]: p for p in raw_pos if float(p["size"]) > 0}
    orders = {o.get("orderLinkId"): o for o in raw_orders}

    def cancel(link):
        o = orders.get(link)
        if o:
            api.ok("POST", "/v5/order/cancel", {"category": "linear", "symbol": o["symbol"], "orderId": o["orderId"]})

    def safe(label, fn):
        try:
            fn()
            return True
        except Exception as e:  # noqa: BLE001 — błąd jednej czynności nie zatrzymuje nadzoru; pozycja ma stop-loss
            notify(f"[{sym}] {label}: {str(e)[:120]} (ponawiam co przebieg; stop-loss działa)", key=f"safe:{sym}:{label}", cooldown_h=12)
            return False

    for lid, tr in list(trades.items()):
        sym, p = tr["symbol"], pos.get(tr["symbol"])
        size = float(p["size"]) if p else 0.0
        if tr["phase"] == "pending":
            if size > 0:
                tr.update(phase="open", opened=int(now), fill_qty=size, tp_ok=False)
                if safe("ustawienie celów", lambda: place_tps(api, tr, lid, size, set(orders))):
                    log(f"[{sym}] wejście wypełnione: ustawiono cele TP1/TP2" + ("" if tr.get("split") else " (jeden cel: połowa poniżej minimum giełdy)"))
            elif lid not in orders and now - tr["created"] > 900:
                trades.pop(lid)                 # wejście wygasło lub anulowano
            continue
        if size <= 0:                           # zamknięta (stop, cel albo ręcznie): sprzątamy cele
            safe("sprzątanie celów", lambda: (cancel(tp_link(lid, 1)), cancel(tp_link(lid, 2))))
            ledger_queue(state, trades.pop(lid), now, "zamknięta")
            log(f"[{sym}] pozycja zamknięta: usunięto pozostałe cele")
            continue
        if not tr.get("tp_ok"):
            safe("ustawienie celów", lambda: place_tps(api, tr, lid, size, set(orders)))      # ponowna próba po błędzie
        if tr["phase"] == "open" and tr.get("split") and size < tr["fill_qty"] * 0.75:
            if safe("przesunięcie stopa", lambda: api.ok("POST", "/v5/position/trading-stop", {"category": "linear", "symbol": sym, "tpslMode": "Full", "positionIdx": 0,
                                                                                                  "stopLoss": fmt_px(float(p["avgPrice"]), tr["tick"]), "slTriggerBy": "LastPrice"})):
                tr["phase"] = "tp1_done"
                notify(f"[{sym}] TP1 zrealizowany: stop przesunięty na wejście ({p['avgPrice']})")
        hold_s = tr.get("max_hold_s") or TIME_STOP_S
        if tr.get("opened") and now - tr["opened"] > hold_s:
            def close():
                cancel(tp_link(lid, 1)); cancel(tp_link(lid, 2))
                api.ok("POST", "/v5/order/create", {"category": "linear", "symbol": sym, "side": "Sell" if p["side"] == "Buy" else "Buy", "orderType": "Market", "qty": p["size"],
                                                    "reduceOnly": True, "positionIdx": 0})
            if safe("zamknięcie po czasie", close):
                ledger_queue(state, trades.pop(lid), now, "stop czasowy")
                notify(f"[{sym}] zamknięcie po {round(hold_s / 86400)} dniach (stop czasowy, jak w teście)")
    state["trades"] = trades


# --------------------------------------------------------------------------
# Dziennik zamkniętych transakcji bota (bot_trades.jsonl): wynik w R do porównania z symulatorem w laboratorium.
# Zapis jest dodatkiem: żaden błąd w tej części nie może wpłynąć na handel.
# --------------------------------------------------------------------------
LEDGER_WAIT_S = 2 * 3600          # tyle czekamy, aż giełda pokaże wynik zamkniętej pozycji
LEDGER_SETTLE_AFTER_S = 120       # rozliczamy najwcześniej przy następnym przebiegu: wynik zamknięcia z tego przebiegu nie jest jeszcze w pobranych danych


def ledger_queue(state, tr, now, reason):
    """Zamknięta transakcja czeka na wynik z giełdy (closed-pnl pojawia się z opóźnieniem)."""
    try:
        if tr.get("opened") and tr.get("stop") and tr.get("fill_qty"):
            state.setdefault("ledger_pending", []).append({k: tr.get(k) for k in ("symbol", "side", "entry", "stop", "fill_qty", "opened", "plan_date", "strategy", "variant")}
                                                           | {"closed": int(now), "reason": reason})
    except Exception as e:  # noqa: BLE001
        log(f"dziennik transakcji: {e}")


def settle_ledger(state, rows, now, mode):
    """Dopisuje do dziennika transakcje, dla których giełda podała już wynik (suma closedPnl aktywa między otwarciem a zamknięciem)."""
    keep = []
    for e in state.get("ledger_pending") or []:
        try:
            lo, hi = (e["opened"] - 300) * 1000, (e["closed"] + 900) * 1000
            mine = [r for r in rows if r.get("symbol") == e["symbol"] and lo <= float(r.get("updatedTime") or r.get("createdTime") or 0) <= hi]
            if now - e["closed"] < LEDGER_SETTLE_AFTER_S or (not mine and now - e["closed"] < LEDGER_WAIT_S):
                keep.append(e)       # za wcześnie (mogłyby się policzyć tylko wcześniejsze cele częściowe) albo giełda jeszcze nie podała wyniku
                continue
            pnl = sum(float(r["closedPnl"]) for r in mine) if mine else None
            unit = abs(e["entry"] - e["stop"]) * e["fill_qty"]
            bb.append_ledger(HERE, dict(e, ts=int(now), mode=mode, pnl=round(pnl, 4) if pnl is not None else None, r=round(pnl / unit, 3) if pnl is not None and unit > 0 else None,
                                         fills=len(mine)))
        except Exception as ex:  # noqa: BLE001 — wadliwy wpis porzucamy, handel trwa
            log(f"dziennik transakcji: {ex}")
    state["ledger_pending"] = keep[-50:]


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        raise
    except Exception as e:  # nigdy nie rób nic ryzykownego po błędzie
        notify(f"BŁĄD: {str(e)[:300]}", key="err:" + re.sub(r"[0-9]+", "#", str(e))[:80], cooldown_h=6)
        sys.exit(1)
