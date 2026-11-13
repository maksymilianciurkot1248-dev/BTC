#!/usr/bin/env python3
"""Wspólne pliki bota handlującego: ustawienia z panelu, status, dziennik zdarzeń i statystyki.

Panel WWW (btc_api.py) zapisuje tylko bot_settings.json (parametry ryzyka w twardych granicach) i czyta bot_status.json
oraz bot_events.jsonl, które zapisuje trader. Panel NIE zna kluczy giełdowych i nie zmienia trybu (dryrun/testnet/demo/live).
"""
import json
import math
import os
import re
import tempfile
import time

SETTINGS = "bot_settings.json"
STATUS = "bot_status.json"
EVENTS = "bot_events.jsonl"
EDGE = "edge_map.json"            # mapa przewagi (zapisuje btc_learn.py)
LEARN_LOG = "learn_log.jsonl"     # dziennik nauki systemu
EDGE_HISTORY = "edge_history.json"  # historia decyzji mapy przewagi: dopuszczone / wycofane / utrzymane (zapisuje btc_learn.py)
PROPOSALS = "bot_proposals.json"    # wnioski o pozycje czekające na Twoje zatwierdzenie w panelu
ORDERS = "bot_orders.json"            # zlecenia złożone ze strony (kolejka i historia); wykonuje je btc_exec.py
EXEC = "bot_exec.json"                # stan wykonawcy zleceń (tryb, czy zlecenia ze strony są włączone na serwerze)
CATALOG = "catalog.json"          # wszystkie płynne kontrakty USDT dostępne na Bybit (zapisuje btc_learn.py)
RISK = "bot_risk.json"              # stan ryzyka portfela i wyłączników (zapisuje trader, czyta btc_exec i panel)
RISK_RESET = "bot_risk_reset.json"  # prośba o odblokowanie wyłączników z panelu
QUAR = "bot_learn.json"           # kwarantanna aktywów po serii strat (zapisuje trader)

# (min, max, typ): twarde granice, których nie da się przekroczyć z panelu
BOUNDS = {
    "risk_pct": (0.05, 2.0, float),             # % kapitału ryzykowany na pozycję (do stopa)
    "max_lev": (1.0, 2.0, float),               # dźwignia (kod i tak nie przepuści > 2)
    "max_daily_loss_pct": (0.5, 5.0, float),    # dzienny limit straty, po którym bot się wstrzymuje
    "max_orders_day": (1, 5, int),              # liczba nowych zleceń na dobę
    "max_capital": (10.0, 1_000_000.0, float),  # kapitał, od którego liczymy ryzyko (USDT)
    "max_positions": (1, 3, int),               # ile pozycji naraz (suma nominałów nadal <= dźwignia * kapitał)
    "stop_mult": (0.5, 2.0, float),             # mnożnik odległości stop-lossa od wejścia (1 = wg strategii)
    "fixed_notional": (0.0, 100_000.0, float),  # stała wartość pozycji w USDT (0 = rozmiar liczony z ryzyka)
    "max_heat_pct": (0.5, 10.0, float),         # limit ryzyka portfela (skorelowanego) w % kapitału, patrz btc_risk
    "streak_losses": (2, 10, int),              # po tylu stratach z rzędu bot robi pauzę
    "streak_pause_h": (1, 168, int),            # długość pauzy po serii strat (godziny od ostatniej straty)
    "max_weekly_loss_pct": (1.0, 30.0, float),  # strata tygodniowa (od poniedziałku UTC), po której bot czeka do następnego tygodnia
    "max_drawdown_pct": (3.0, 50.0, float),     # spadek kapitału od szczytu, po którym bot staje do Twojego odblokowania
}
LIVE_MAX = {"streak_pause_h"}                    # w trybie live panel może tylko wydłużyć pauzę (dla pozostałych limitów: tylko zmniejszyć)
DIRECTIONS = ("long", "short")
STRATEGIES = ("plan", "ensemble")      # plan = klasyczny plan z btc_analysis, ensemble = zespół sygnałów (btc_decision)
VARIANT_RE = re.compile(r"^[a-z][a-z0-9]{0,11}$")   # id wariantu zespołu sygnałów (rejestr w btc_lab; nieznany id = wariant domyślny)
LEDGER = "bot_trades.jsonl"            # zamknięte transakcje bota z wynikiem w R (zapisuje trader, czyta laboratorium)
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,15}USDT$")
MAX_SYMBOLS = 8
# podpowiedzi w panelu; faktyczną dostępność i płynność bot sprawdza przy każdym przebiegu
SUGGESTED = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "XAUTUSDT", "PAXGUSDT"]
BOOLS = ("paused", "use_take_profit", "auto_select", "require_approval")     # stop-loss celowo NIE ma przełącznika: każda pozycja musi go mieć


def bounds_public():
    return {k: {"min": lo, "max": hi} for k, (lo, hi, _t) in BOUNDS.items()}


def validate(d):
    """Zwraca oczyszczony słownik ustawień albo zgłasza ValueError. Nieznane klucze są odrzucane."""
    if not isinstance(d, dict):
        raise ValueError("oczekiwano obiektu")
    out = {}
    for k, (lo, hi, typ) in BOUNDS.items():
        if k not in d or d[k] in (None, ""):
            continue
        try:
            v = float(d[k])
        except (TypeError, ValueError):
            raise ValueError(f"{k}: to nie jest liczba") from None
        if not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"{k}: poza zakresem {lo}–{hi}")
        out[k] = int(round(v)) if typ is int else round(v, 4)
    if "directions" in d:
        dirs = d["directions"]
        if not isinstance(dirs, list) or not dirs or any(x not in DIRECTIONS for x in dirs):
            raise ValueError("directions: wybierz long i/lub short")
        out["directions"] = sorted(set(dirs))
    for k in BOOLS:
        if k in d:
            if not isinstance(d[k], bool):
                raise ValueError(f"{k}: oczekiwano true/false")
            out[k] = d[k]
    if "strategy" in d:
        if d["strategy"] not in STRATEGIES:
            raise ValueError("strategy: plan lub ensemble")
        out["strategy"] = d["strategy"]
    if "ens_variant" in d:
        if not isinstance(d["ens_variant"], str) or not VARIANT_RE.match(d["ens_variant"]):
            raise ValueError("ens_variant: identyfikator wariantu, np. v2")
        out["ens_variant"] = d["ens_variant"]
    if "symbols" in d:
        syms = d["symbols"]
        if not isinstance(syms, list) or not syms or any(not isinstance(x, str) for x in syms):
            raise ValueError("symbols: podaj listę symboli, np. BTCUSDT")
        syms = list(dict.fromkeys(x.strip().upper() for x in syms))
        if len(syms) > MAX_SYMBOLS or any(not SYMBOL_RE.match(x) for x in syms):
            raise ValueError(f"symbols: do {MAX_SYMBOLS} symboli w formacie XXXUSDT")
        out["symbols"] = syms
    unknown = set(d) - set(BOUNDS) - set(BOOLS) - {"directions", "symbols", "strategy", "ens_variant"}
    if unknown:
        raise ValueError("nieznane pola: " + ", ".join(sorted(unknown))[:80])
    return out


def _atomic_write(path, text):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".bot.tmp.", dir=d)
    try:
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def read_settings(d):
    s = _read_json(os.path.join(d, SETTINGS))
    try:
        clean = validate({k: v for k, v in s.items() if k not in ("updated",)})
    except ValueError:      # uszkodzony plik nie może zmienić zachowania bota
        return {}
    if "updated" in s:
        clean["updated"] = s["updated"]
    return clean


def write_settings(d, clean):
    """Zapisuje ustawienia, łącząc je z poprzednimi (nieprzekazane pola zostają)."""
    cur = read_settings(d)
    cur.pop("updated", None)
    cur.update(validate(clean))
    cur["updated"] = int(time.time())
    _atomic_write(os.path.join(d, SETTINGS), json.dumps(cur, ensure_ascii=False))
    return cur


def apply_settings(c, s):
    """Nakłada ustawienia z panelu na konfigurację tradera. W trybie live ustawienia mogą tylko ZMNIEJSZAĆ ryzyko
    względem wartości z trader.env (panel nie może go zwiększyć). Zwraca listę zmian do pokazania w panelu."""
    live = c["mode"] == "live"
    changes = []
    for k, (lo, hi, typ) in BOUNDS.items():
        if k not in s:
            continue
        v = s[k]
        cur = c.get(k)
        if live and cur and k != "stop_mult":
            v = max(v, cur) if k in LIVE_MAX else min(v, cur)
        if cur != v:
            changes.append(f"{k}: {cur} → {v}")
        c[k] = v
    c["directions"] = list(s.get("directions") or DIRECTIONS)
    c["paused"] = bool(s.get("paused", False))
    c["use_take_profit"] = bool(s.get("use_take_profit", True))
    c["require_approval"] = bool(s.get("require_approval", False))
    c["strategy"] = s.get("strategy") if s.get("strategy") in STRATEGIES else "plan"
    c["ens_variant"] = s.get("ens_variant")          # None = wariant domyślny z laboratorium
    want_auto = bool(s.get("auto_select", False))
    c["auto"] = want_auto and (not live or os.environ.get("TRADER_AUTO_LIVE") == "yes")   # w live automat wymaga zgody w trader.env
    c["auto_blocked_live"] = want_auto and not c["auto"]
    if s.get("symbols"):
        want = s["symbols"]
        if live:     # w trybie live panel może tylko zawęzić listę aktywów z trader.env
            want = [x for x in want if x in c["symbols"]] or c["symbols"]
        if want != c["symbols"]:
            changes.append(f"aktywa: {','.join(c['symbols'])} → {','.join(want)}")
        c["symbols"] = want
    c["max_lev"] = min(c["max_lev"], 2.0)
    return changes


def append_event(d, kind, msg, keep=500, fname=EVENTS):
    """Dopisuje zdarzenie; pomija powtórzenie poprzedniego (bot działa co 5 minut)."""
    path = os.path.join(d, fname)
    rows = read_events(d, keep, fname)
    if rows and rows[-1]["kind"] == kind and rows[-1]["msg"] == msg:
        return False
    rows.append({"ts": int(time.time()), "kind": kind, "msg": msg[:400]})
    _atomic_write(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows[-keep:]) + "\n")
    return True


def read_events(d, n=60, fname=EVENTS):
    rows = []
    try:
        with open(os.path.join(d, fname), encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    if isinstance(r, dict) and "msg" in r:
                        rows.append({"ts": r.get("ts", 0), "kind": str(r.get("kind", "info")), "msg": str(r["msg"])})
                except ValueError:
                    continue
    except OSError:
        pass
    return rows[-n:]


def read_edge(d):
    return _read_json(os.path.join(d, EDGE))


def write_edge(d, m):
    _atomic_write(os.path.join(d, EDGE), json.dumps(m, ensure_ascii=False, default=float))


def read_edge_history(d):
    ev = _read_json(os.path.join(d, EDGE_HISTORY)).get("events")
    return [e for e in ev if isinstance(e, dict)] if isinstance(ev, list) else []


def write_edge_history(d, events):
    _atomic_write(os.path.join(d, EDGE_HISTORY), json.dumps({"version": 1, "events": events}, ensure_ascii=False, default=float))


def append_ledger(d, row):
    """Dopisuje zamkniętą transakcję bota do dziennika (jedna linia JSON)."""
    with open(os.path.join(d, LEDGER), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=float) + "\n")


def read_catalog(d):
    return _read_json(os.path.join(d, CATALOG)).get("symbols") or []


def write_catalog(d, rows):
    _atomic_write(os.path.join(d, CATALOG), json.dumps({"ts": int(time.time()), "symbols": rows}))


# ---- wnioski o zatwierdzenie: bot zgłasza dokładnie opisaną pozycję, Ty klikasz w panelu, a bot sam ją stawia przy następnym przebiegu
PENDING_TTL_S = 12 * 3600          # niezatwierdzony wniosek wygasa po 12 godzinach
APPROVED_TTL_S = 3600              # zatwierdzony wniosek trzeba zrealizować w ciągu godziny
ID_RE = re.compile(r"^p[0-9a-f]{20,40}$")


def read_proposals(d):
    items = _read_json(os.path.join(d, PROPOSALS)).get("items")
    return items if isinstance(items, list) else []


def _write_proposals(d, items):
    _atomic_write(os.path.join(d, PROPOSALS), json.dumps({"items": items[-60:]}, ensure_ascii=False, default=float))


def find_proposal(d, pid):
    return next((p for p in read_proposals(d) if p.get("id") == pid), None)


def add_proposal(d, prop, now=None):
    now = int(now or time.time())
    items = read_proposals(d)
    items.append({**prop, "created": now, "expires": now + PENDING_TTL_S, "status": "pending"})
    _write_proposals(d, items)


def set_proposal(d, pid, **fields):
    items = read_proposals(d)
    for p in items:
        if p.get("id") == pid:
            p.update(fields)
    _write_proposals(d, items)


def expire_proposals(d, now=None):
    now = int(now or time.time())
    items, changed = read_proposals(d), False
    for p in items:
        if p.get("status") == "pending" and p.get("expires", 0) < now:
            p["status"], changed = "expired", True
        elif p.get("status") == "approved" and p.get("decided", 0) + APPROVED_TTL_S < now:
            p["status"], changed = "expired", True
    if changed:
        _write_proposals(d, items)


def decide(d, pid, decision, now=None):
    """Decyzja z panelu: zwraca (kod, komunikat). Tylko wniosek oczekujący i nieprzeterminowany można zatwierdzić lub odrzucić."""
    now = int(now or time.time())
    if decision not in ("approve", "reject") or not isinstance(pid, str) or not ID_RE.match(pid):
        raise ValueError("niepoprawna decyzja lub identyfikator")
    p = find_proposal(d, pid)
    if p is None:
        return 404, "nie ma takiego wniosku"
    if p["status"] != "pending" or p.get("expires", 0) < now:
        return 409, f"wniosek ma już status: {p['status'] if p['status'] != 'pending' else 'expired'}"
    set_proposal(d, pid, status="approved" if decision == "approve" else "rejected", decided=now)
    return 200, "zatwierdzono" if decision == "approve" else "odrzucono"


# ---- zlecenia ze strony: strona tylko zapisuje prośbę; zlecenie składa osobny proces na serwerze po sprawdzeniu limitów
ORDER_ID_RE = re.compile(r"^o[0-9a-f]{20}$")
MAX_QUEUED = 10


def validate_order(d):
    """Oczyszcza prośbę o zlecenie ze strony albo zgłasza ValueError. Stop-loss jest obowiązkowy dla otwarcia pozycji."""
    if not isinstance(d, dict):
        raise ValueError("oczekiwano obiektu")
    typ = d.get("type")
    sym = str(d.get("symbol", "")).strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError("symbol w formacie XXXUSDT")
    if typ == "close":
        if set(d) - {"type", "symbol"}:
            raise ValueError("nieznane pola")
        return {"type": "close", "symbol": sym}
    if typ != "open":
        raise ValueError("type: open lub close")
    allowed = {"type", "symbol", "side", "notional", "order_type", "price", "stop_pct", "tp_pct"}
    if set(d) - allowed:
        raise ValueError("nieznane pola: " + ", ".join(sorted(set(d) - allowed))[:80])
    if d.get("side") not in DIRECTIONS:
        raise ValueError("side: long lub short")

    def num(k, lo, hi, required=True):
        v = d.get(k)
        if v in (None, ""):
            if required:
                raise ValueError(f"{k}: wymagane")
            return None
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k}: to nie jest liczba") from None
        if not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"{k}: poza zakresem {lo}–{hi}")
        return round(v, 6)
    ot = d.get("order_type", "market")
    if ot not in ("market", "limit"):
        raise ValueError("order_type: market lub limit")
    out = {"type": "open", "symbol": sym, "side": d["side"], "order_type": ot, "notional": num("notional", 5, 100_000), "stop_pct": num("stop_pct", 0.2, 50)}
    if ot == "limit":
        out["price"] = num("price", 1e-9, 1e9)
    tp = num("tp_pct", 0.2, 500, required=False)
    if tp:
        out["tp_pct"] = tp
    return out


def read_orders(d):
    items = _read_json(os.path.join(d, ORDERS)).get("items")
    return items if isinstance(items, list) else []


def _write_orders(d, items):
    _atomic_write(os.path.join(d, ORDERS), json.dumps({"items": items[-80:]}, ensure_ascii=False, default=float))


def add_order(d, clean, now=None):
    """Dodaje prośbę do kolejki. Zgłasza ValueError, gdy w kolejce jest już za dużo niewykonanych."""
    import secrets
    items = read_orders(d)
    if sum(1 for o in items if o.get("status") == "queued") >= MAX_QUEUED:
        raise ValueError("za dużo oczekujących zleceń w kolejce")
    oid = "o" + secrets.token_hex(10)
    items.append({**clean, "id": oid, "created": int(now or time.time()), "status": "queued"})
    _write_orders(d, items)
    return oid


def set_order(d, oid, **fields):
    items = read_orders(d)
    for o in items:
        if o.get("id") == oid:
            o.update(fields)
    _write_orders(d, items)


def read_exec(d):
    return _read_json(os.path.join(d, EXEC))


def write_exec(d, obj):
    _atomic_write(os.path.join(d, EXEC), json.dumps(obj, ensure_ascii=False))


def write_risk(d, obj):
    _atomic_write(os.path.join(d, RISK), json.dumps(obj, ensure_ascii=False, default=float))


def read_risk(d):
    return _read_json(os.path.join(d, RISK))


def request_risk_reset(d):
    _atomic_write(os.path.join(d, RISK_RESET), json.dumps({"ts": int(time.time())}))


def read_quar(d):
    q = _read_json(os.path.join(d, QUAR))
    return {"until": {k: float(v) for k, v in (q.get("until") or {}).items()}, "reset": {k: float(v) for k, v in (q.get("reset") or {}).items()}}


def write_quar(d, q):
    _atomic_write(os.path.join(d, QUAR), json.dumps(q))


def write_status(d, st):
    _atomic_write(os.path.join(d, STATUS), json.dumps(st, ensure_ascii=False, default=float))


def read_status(d):
    return _read_json(os.path.join(d, STATUS))


def stats_from_closed(rows, max_points=200):
    """Statystyki z listy zamkniętych pozycji Bybit (pola: closedPnl, updatedTime|createdTime w ms)."""
    pts = []
    for r in rows:
        try:
            ts = int(r.get("updatedTime") or r.get("createdTime"))
            pts.append((ts, float(r["closedPnl"])))
        except (KeyError, TypeError, ValueError):
            continue
    pts.sort()
    n = len(pts)
    if not n:
        return {"trades": 0}
    pnls = [p for _, p in pts]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    cum, curve, peak, mdd = 0.0, [], 0.0, 0.0
    for ts, p in pts:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
        curve.append([ts, round(cum, 4)])
    if len(curve) > max_points:
        step = len(curve) / max_points
        curve = [curve[int(i * step)] for i in range(max_points - 1)] + [curve[-1]]
    gl = -sum(losses)
    return {"trades": n, "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / n * 100, 1),
            "total_pnl": round(sum(pnls), 4), "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
            "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else None,
            "best": round(max(pnls), 4), "worst": round(min(pnls), 4), "max_drawdown": round(mdd, 4),
            "first_ts": pts[0][0], "last_ts": pts[-1][0], "curve": curve}
