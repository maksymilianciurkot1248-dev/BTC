#!/usr/bin/env python3
"""API skanera okazji (nasłuch tylko na 127.0.0.1; publicznie dostępne wyłącznie przez nginx z hasłem).

GET  /api/health
GET  /api/scan?cls=crypto|commodity|fx|stock          lista okazji (odświeżana co kilkanaście sekund)
GET  /api/report?symbol=XAUUSD&capital=10000&risk=1   szczegółowy raport pozycji
GET  /api/positions                                   Twoje śledzone pozycje z bieżącą radą
POST /api/track     {"symbol","entry","stop",["tp1","tp2","qty","note"]}
POST /api/untrack   {"id"}
GET  /api/performance                                 skuteczność systemu i bieżące korekty
GET  /api/bot                                         status bota, statystyki, zdarzenia, ustawienia i ich granice
GET  /api/account                                     moje konto Bybit: saldo, pozycje, statystyki (tylko odczyt)
GET  /api/shadow                                      tryb cienia: wyniki obu strategii na żywo, bez zleceń
GET  /api/learn                                       mapa przewagi, dziennik nauki, historia decyzji (dopuszczone/wycofane) i kwarantanny aktywów
GET  /api/ai/chat[?id=]                               asystent AI: gotowość albo wynik zadania; POST /api/ai/chat {"messages":[...]} zadaje pytanie (klucz w ai.env)
GET  /api/flow                                        rynek na żywo: przepływ zleceń i pozycjonowanie (wolumen agresywny, konta long/short, premia), wskaźnik łączny i jego trafność
GET  /api/scalp                                       scalper: wyniki strategii w cieniu, bramka, otwarte pozycje, prawdziwe transakcje; POST /api/scalp/settings {"enabled":bool}
GET  /api/copy                                        cudze transakcje (Hyperliquid): czy kopiowanie portfela ma przewagę poza próbą (dodawanie: btc_copy.py add)
GET  /api/lab                                         laboratorium wariantów: diagnoza sygnałów, test historyczny i wyniki z cienia wariantów, wnioski o awans
POST /api/bot/order {"type":"open|close",...}          prośba o pozycję ze strony: wykonuje ją btc_exec.py po sprawdzeniu limitów (wymaga TRADER_WEB_ORDERS=yes)
POST /api/bot/risk-reset                              prośba o odblokowanie wyłączników ryzyka (wykonuje trader; w trybie live wymaga TRADER_WEB_ORDERS=yes)
POST /api/bot/proposal {"id","decision":"approve|reject"}    zatwierdzenie lub odrzucenie pozycji zgłoszonej przez bota
POST /api/bot/settings {"risk_pct",...,"paused"}      ustawienia bota w twardych granicach (bez kluczy i bez zmiany trybu)
Nie składa żadnych zleceń i nie zna kluczy giełdowych. Zapisy (POST) wymagają nagłówka X-Requested-With: btc-ui.
Użycie:  python btc_api.py --db btc_data.db --live /var/www/btc/live.json --port 8787
"""
import argparse
import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_botcfg as bb  # noqa: E402
import btc_opportunities as bo  # noqa: E402

MAX_BODY = 4096
EXT_DIR = os.environ.get("BTC_API_EXT", os.path.join(HERE, "api_ext"))
EXT_GET = {}      # dodatkowe adresy GET z plików api_ext/*.py (przetrwają aktualizacje btc_api.py)


def load_ext():
    """Wczytuje rozszerzenia z katalogu api_ext: plik .py z słownikiem GET = {"/api/nazwa": funkcja(state, query) -> (kod, obiekt)}.
    Błąd w rozszerzeniu nie zatrzymuje API. Adresy są chronione tym samym hasłem nginx co reszta /api/."""
    import glob
    import importlib.util
    EXT_GET.clear()
    for path in sorted(glob.glob(os.path.join(EXT_DIR, "*.py"))):
        try:
            spec = importlib.util.spec_from_file_location("api_ext_" + os.path.basename(path)[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for p, fn in (getattr(mod, "GET", None) or {}).items():
                if isinstance(p, str) and p.startswith("/api/") and callable(fn) and p not in ("/api/health",):
                    EXT_GET[p] = fn
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"api_ext {os.path.basename(path)}: {type(e).__name__}: {e}\n")


class State:
    def __init__(self, engine, live_path, stream_path=None, bot_dir=None):
        self.engine, self.live_path = engine, live_path
        self.bot_dir = bot_dir or HERE
        self.stream_path = stream_path or os.path.join(os.path.dirname(os.path.abspath(live_path)), "stream.json")
        self.scan, self.scan_at, self.live_mtime, self.live_ts = [], 0.0, 0.0, 0.0
        self.lock = threading.Lock()
        self.errors = []

    def read_live(self):
        try:
            m = os.stat(self.live_path).st_mtime
            if m == self.live_mtime:
                return False
            with open(self.live_path, encoding="utf-8") as fh:
                j = json.load(fh)
            self.live_mtime, self.live_ts = m, j.get("ts", 0)
            self.engine.set_live(j.get("quotes", {}), ts=j.get("ts"))
            return True
        except (OSError, ValueError):
            return False

    def refresh(self, force=False):
        changed = self.read_live()
        if force or changed or time.time() - self.scan_at > 15:
            res = self.engine.scan()
            with self.lock:
                self.scan, self.scan_at = res, time.time()
            return True
        return False

    def housekeeping(self):
        self.engine.log_signals(self.scan)
        self.engine.update_signals()


def background(state, stop, every_reload=600):
    last_reload, last_house = time.time(), 0.0
    while not stop.is_set():
        try:
            state.refresh()
            if time.time() - last_house > 60:
                state.housekeeping()
                last_house = time.time()
            if time.time() - last_reload > every_reload:
                state.engine.load()
                last_reload = time.time()
        except Exception as e:  # noqa: BLE001 — pętla nie może się zatrzymać
            state.errors = (state.errors + [f"{time.strftime('%H:%M:%S')} {type(e).__name__}: {e}"])[-10:]
        stop.wait(5)


_QUOTES = {}


def bybit_quote(sym):
    """Cena z publicznego API Bybit (bez kluczy), z krótką pamięcią podręczną. Używana do podglądu zlecenia na stronie."""
    import json as _json
    import urllib.request
    hit = _QUOTES.get(sym)
    if hit and time.time() - hit[0] < 5:
        return hit[1]
    base = (os.environ.get("BYBIT_BASE_URL") or "https://api.bybit.com").rstrip("/")
    with urllib.request.urlopen(urllib.request.Request(f"{base}/v5/market/tickers?category=linear&symbol={sym}", headers={"User-Agent": "btc-api/1.0"}), timeout=6) as r:
        lst = (_json.load(r).get("result") or {}).get("list") or []
    if not lst:
        raise ValueError("nieznany symbol")
    out = {"symbol": sym, "price": float(lst[0]["lastPrice"]), "turnover24h": float(lst[0].get("turnover24h") or 0)}
    _QUOTES[sym] = (time.time(), out)
    return out


def num(q, name, default, lo, hi):
    try:
        v = float(q.get(name, [default])[0])
    except (TypeError, ValueError):
        raise ValueError(f"niepoprawny parametr {name}")
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{name} poza zakresem {lo}-{hi}")
    return v


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        server_version = "btc-api"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=float, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json_body(self, limit=MAX_BODY):
            if self.headers.get("X-Requested-With") != "btc-ui":
                raise PermissionError("brak nagłówka X-Requested-With")
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("wymagany Content-Type: application/json")
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > limit:
                raise ValueError("zły rozmiar treści")
            d = json.loads(self.rfile.read(n))
            if not isinstance(d, dict):
                raise ValueError("oczekiwano obiektu JSON")
            return d

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/api/health":
                    return self._send(200, {"ok": True, "assets": len(state.engine.data), "scan_age_s": round(time.time() - state.scan_at, 1) if state.scan_at else None,
                                            "live_age_s": round(time.time() - state.live_ts, 1) if state.live_ts else None, "loaded_age_s": round(state.engine.now() - state.engine.loaded_at), "errors": state.errors})
                if u.path == "/api/scan":
                    state.refresh()
                    cls = (q.get("cls") or [""])[0]
                    with state.lock:
                        items = [x for x in state.scan if not cls or x.get("cls") == cls]
                    return self._send(200, {"computed_at": state.scan_at, "live_age_s": round(time.time() - state.live_ts, 1) if state.live_ts else None, "items": items,
                                            "events": state.engine.upcoming_events(5), "counts": {k: sum(1 for x in items if x["status"] == k) for k in {x["status"] for x in items}}})
                if u.path == "/api/report":
                    key = (q.get("symbol") or [""])[0]
                    if key not in state.engine.data:
                        return self._send(404, {"error": "nieznany symbol"})
                    state.read_live()
                    return self._send(200, state.engine.report(key, num(q, "capital", 0, 0, 1e9), num(q, "risk", 1, 0.05, 10)))
                if u.path == "/api/stream":
                    try:
                        with open(state.stream_path, encoding="utf-8") as fh:
                            d = json.load(fh)
                        return self._send(200, {"age_s": round(time.time() - d.get("ts", 0), 1), "flow": d.get("flow"), "dvol": d.get("dvol"), "mark": d.get("bybit_mark"), "block": d.get("block"),
                                                "fees": d.get("fees"), "providers": d.get("providers"), "index": d.get("deribit_index")})
                    except (OSError, ValueError):
                        return self._send(200, {"age_s": None, "flow": None})
                if u.path == "/api/bot":
                    st = bb.read_status(state.bot_dir)
                    return self._send(200, {"status": st, "age_s": round(time.time() - st["ts"], 1) if st.get("ts") else None,
                                            "events": bb.read_events(state.bot_dir, 60), "proposals": bb.read_proposals(state.bot_dir)[-15:], "orders": bb.read_orders(state.bot_dir)[-15:], "risk": bb.read_risk(state.bot_dir), "exec": bb.read_exec(state.bot_dir), "settings": bb.read_settings(state.bot_dir),
                                            "bounds": bb.bounds_public(), "suggested": bb.SUGGESTED, "max_symbols": bb.MAX_SYMBOLS, "stop_file": os.path.exists(os.path.join(state.bot_dir, "STOP"))})
                if u.path == "/api/account":
                    acc = bb._read_json(os.path.join(state.bot_dir, "bybit_account.json"))
                    acc["age_s"] = round(time.time() - acc["ts"], 1) if acc.get("ts") else None
                    return self._send(200, acc)
                if u.path == "/api/shadow":
                    sh = bb._read_json(os.path.join(state.bot_dir, "shadow_stats.json"))
                    sh["age_s"] = round(time.time() - sh["ts"], 1) if sh.get("ts") else None
                    return self._send(200, sh)
                if u.path == "/api/quote":
                    sym = (q.get("symbol") or [""])[0].upper()
                    if not bb.SYMBOL_RE.match(sym):
                        return self._send(400, {"error": "symbol w formacie XXXUSDT"})
                    try:
                        return self._send(200, bybit_quote(sym))
                    except ValueError as e:
                        return self._send(404, {"error": str(e)})
                    except OSError:
                        return self._send(502, {"error": "Bybit nie odpowiada"})
                if u.path == "/api/catalog":
                    return self._send(200, {"symbols": bb.read_catalog(state.bot_dir)})
                if u.path == "/api/learn":
                    ed = bb.read_edge(state.bot_dir)
                    return self._send(200, {"edge": ed, "age_s": round(time.time() - ed["ts"], 1) if ed.get("ts") else None,
                                            "events": bb.read_events(state.bot_dir, 60, bb.LEARN_LOG), "quarantine": bb.read_quar(state.bot_dir).get("until", {}),
                                            "history": bb.read_edge_history(state.bot_dir)[-120:],
                                            "auto": (bb.read_status(state.bot_dir) or {}).get("auto")})
                if u.path == "/api/ai/chat":
                    import btc_ai
                    if q.get("id"):
                        code, obj = btc_ai.result(state.bot_dir, q["id"][0])
                        return self._send(code, obj)
                    return self._send(200, btc_ai.info(state.bot_dir))
                if u.path == "/api/flow":
                    import btc_flow
                    o = btc_flow.read_out(state.bot_dir)
                    return self._send(200, {**o, "age_s": round(time.time() - o["ts"], 1) if o.get("ts") else None})
                if u.path == "/api/scalp":
                    import btc_scalp
                    o = btc_scalp.read_out(state.bot_dir)
                    return self._send(200, {**o, "age_s": round(time.time() - o["ts"], 1) if o.get("ts") else None, "settings": btc_scalp.read_settings(state.bot_dir)})
                if u.path == "/api/copy":
                    import btc_copy
                    c = btc_copy.read(state.bot_dir)
                    return self._send(200, {**c, "age_s": round(time.time() - c["ts"], 1) if c.get("ts") else None, "tracked": len(btc_copy.read_wallets(state.bot_dir))})
                if u.path == "/api/lab":
                    import btc_lab
                    lab = btc_lab.read(state.bot_dir)
                    return self._send(200, {**lab, "age_s": round(time.time() - lab["ts"], 1) if lab.get("ts") else None, "active": btc_lab.champion(state.bot_dir),
                                            "strategy": bb.read_settings(state.bot_dir).get("strategy") or "plan",
                                            "registry": [{"id": k, "name": v["name"], "origin": v["origin"]} for k, v in btc_lab.variants(state.bot_dir).items()]})
                if u.path in EXT_GET:
                    code, obj = EXT_GET[u.path](state, q)
                    return self._send(code, obj)
                if u.path == "/api/positions":
                    state.read_live()
                    return self._send(200, {"positions": state.engine.positions()})
                if u.path == "/api/performance":
                    return self._send(200, state.engine.performance())
                return self._send(404, {"error": "nie ma takiego adresu"})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                state.errors = (state.errors + [f"{type(e).__name__}: {e}"])[-10:]
                return self._send(500, {"error": "błąd wewnętrzny"})

        def do_POST(self):
            u = urlparse(self.path)
            try:
                if u.path == "/api/ai/chat":
                    import btc_ai
                    return self._send(200, {"id": btc_ai.start(state.bot_dir, self._json_body(btc_ai.MAX_BODY))})
                d = self._json_body()
                if u.path == "/api/track":
                    f = lambda k: (float(d[k]) if d.get(k) not in (None, "") else None)
                    vals = [f("entry"), f("stop"), f("tp1"), f("tp2"), f("qty")]
                    if any(v is not None and (not math.isfinite(v) or v <= 0 or v > 1e12) for v in vals):
                        raise ValueError("liczby muszą być dodatnie i skończone")
                    pid = state.engine.add_position(str(d.get("symbol", "")), vals[0], vals[1], vals[2], vals[3], vals[4], str(d.get("note") or ""))
                    return self._send(200, {"id": pid})
                if u.path == "/api/scalp/settings":
                    import btc_scalp
                    return self._send(200, {"settings": btc_scalp.write_settings(state.bot_dir, d)})
                if u.path == "/api/bot/settings":
                    clean = bb.validate(d)
                    if "ens_variant" in clean:
                        import btc_lab
                        if clean["ens_variant"] not in btc_lab.variants(state.bot_dir):
                            raise ValueError("ens_variant: nie ma takiego wariantu w laboratorium")
                    return self._send(200, {"settings": bb.write_settings(state.bot_dir, clean)})
                if u.path == "/api/bot/order":
                    oid = bb.add_order(state.bot_dir, bb.validate_order(d))
                    return self._send(200, {"id": oid})
                if u.path == "/api/bot/proposal":
                    code, msg = bb.decide(state.bot_dir, d.get("id"), d.get("decision"))
                    return self._send(code, {"message": msg} if code == 200 else {"error": msg})
                if u.path == "/api/bot/risk-reset":
                    bb.request_risk_reset(state.bot_dir)
                    return self._send(200, {"message": "prośba o odblokowanie zapisana; bot wykona ją przy najbliższym przebiegu (do 5 minut)"})
                if u.path == "/api/untrack":
                    state.engine.close_position(int(d["id"]))
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "nie ma takiego adresu"})
            except PermissionError as e:
                return self._send(403, {"error": str(e)})
            except KeyError:
                return self._send(404, {"error": "nieznany symbol"})
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                state.errors = (state.errors + [f"{type(e).__name__}: {e}"])[-10:]
                return self._send(500, {"error": "błąd wewnętrzny"})
    return H


def serve(engine, live_path, host="127.0.0.1", port=8787, bot_dir=None):
    load_ext()
    state = State(engine, live_path, bot_dir=bot_dir)
    state.refresh(force=True)
    stop = threading.Event()
    threading.Thread(target=background, args=(state, stop), daemon=True).start()
    srv = ThreadingHTTPServer((host, port), make_handler(state))
    return srv, state, stop


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--live", default=os.path.join(HERE, "live.json"))
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    eng = bo.Engine(a.db)
    eng.load()
    srv, state, stop = serve(eng, a.live, "127.0.0.1", a.port)
    print(f"API na 127.0.0.1:{a.port}, aktywa: {len(eng.data)}", flush=True)
    try:
        srv.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
