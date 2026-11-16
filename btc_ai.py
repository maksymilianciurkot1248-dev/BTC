#!/usr/bin/env python3
"""Asystent AI w panelu: czat, który zna bieżący stan bota (status, ustawienia, mapa przewagi, laboratorium, cień, zdarzenia).

Odpowiada model Claude przez API Anthropic (oficjalny pakiet `anthropic`). Asystent TYLKO czyta i tłumaczy:
nie ma narzędzi, nie składa zleceń, nie zmienia ustawień i nie widzi kluczy giełdowych.
Klucz API podajesz sam w pliku ai.env obok programu (chmod 600):  ANTHROPIC_API_KEY=...
Opcjonalnie w tym samym pliku: AI_MODEL (domyślnie claude-opus-5-5), AI_EFFORT (low|medium|high, domyślnie low), AI_MAX_PER_DAY (domyślnie 100).

API panelu (btc-api) nie ma dostępu do internetu ani do klucza, więc tylko zapisuje pytanie do katalogu ai_jobs/.
Pytania wykonuje osobna usługa btc-ai (ten plik uruchomiony jako program), która jako jedyna zna klucz i łączy się z API Anthropic.
POST /api/ai/chat {"messages":[...]} -> {"id"}  oraz  GET /api/ai/chat?id=... -> {"status":"run|done|error","text"}.
"""
import json
import os
import re
import threading
import time
import uuid

import btc_botcfg as bb

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = "ai.env"
MODEL_DEFAULT = "claude-opus-5-5"
MAX_MSG_CHARS = 4000
MAX_HISTORY = 20
MAX_BODY = 64 * 1024
MAX_PER_HOUR = 30
JOB_TTL_S = 900
JOBS_DIR = "ai_jobs"
ALIVE = ".alive"
JOB_RE = re.compile(r"^[0-9a-f]{16}$")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """Jesteś asystentem w prywatnym panelu bota handlującego kontraktami na Bybit. Rozmawiasz z właścicielem panelu.
W wiadomości systemowej poniżej dostajesz migawkę stanu systemu w JSON (status bota, ustawienia, mapa przewagi, laboratorium wariantów, tryb cienia, kopiowanie portfeli, ostatnie zdarzenia). Opieraj się na niej; jeśli czegoś w niej nie ma, powiedz to wprost zamiast zgadywać.

Zasady:
- Odpowiadaj po polsku, zwięźle, zwykłym tekstem bez Markdownu (panel pokazuje surowy tekst). Liczby podawaj z jednostkami.
- Najpierw odpowiedź na pytanie, potem najwyżej kilka zdań uzasadnienia.
- Tylko czytasz i tłumaczysz. Nie możesz składać zleceń ani zmieniać ustawień; jeśli użytkownik o to prosi, wskaż, gdzie w panelu zrobi to sam.
- Bądź uczciwy co do niepewności: strategia nie ma udowodnionej przewagi, dopóki mapa przewagi i wyniki poza próbą (cień) tego nie pokażą. Nie obiecuj zysków i nie przedstawiaj przypuszczeń jako faktów.
- Jednostka R to wynik transakcji w wielokrotności ryzyka początkowego. „Cień” to transakcje symulowane na żywo bez zleceń.
- Jeśli widzisz w migawce coś groźnego (wyłącznik ryzyka, błąd, pozycja bez stopa, ryzyko wyższe niż domyślne 0,5% na transakcję), wspomnij o tym krótko, nawet gdy pytanie dotyczy czegoś innego."""

_lock, _calls, _running = threading.Lock(), [], set()


def load_env(d, with_key=True):
    """Czyta ai.env (KLUCZ=wartość). Wartości nie trafiają do os.environ procesu API poza kluczem Anthropic."""
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
    for k in ("ANTHROPIC_API_KEY", "AI_MODEL", "AI_EFFORT", "AI_MAX_PER_DAY"):
        if k not in out and os.environ.get(k):
            out[k] = os.environ[k]
    if not with_key:
        out.pop("ANTHROPIC_API_KEY", None)      # API panelu nie potrzebuje klucza (używa go tylko usługa btc-ai)
    return out


def _trim(o, depth=0):
    """Skraca długie listy i teksty, żeby migawka miała kilka-kilkanaście tysięcy znaków."""
    if isinstance(o, dict):
        return {k: _trim(v, depth + 1) for k, v in list(o.items())[:60]}
    if isinstance(o, list):
        return [_trim(v, depth + 1) for v in o[:12 if depth else 40]]
    if isinstance(o, str):
        return o[:300]
    if isinstance(o, float):
        return round(o, 5)
    return o


def snapshot(d):
    """Stan systemu dla modelu. Bez kluczy: czytamy tylko pliki stanu, nigdy .env."""
    snap = {"czas_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime())}

    def put(name, fn):
        try:
            snap[name] = _trim(fn())
        except Exception as e:  # noqa: BLE001
            snap[name] = f"niedostępne ({type(e).__name__})"
    put("status_bota", lambda: bb.read_status(d))
    put("ustawienia", lambda: bb.read_settings(d))
    put("wnioski_do_zatwierdzenia", lambda: bb.read_proposals(d))
    put("kwarantanna", lambda: bb.read_quar(d))
    put("zdarzenia", lambda: bb.read_events(d, 25))

    def edge():
        e = bb.read_edge(d) or {}
        out = {"dopuszczone": e.get("allowed"), "utrzymane": e.get("held"), "ts": e.get("ts")}
        for strat, m in (e.get("strategies") or {}).items():
            out[strat] = {"dopuszczone": m.get("allowed"), "aktywa": {s: {k: a.get(k) for k in ("n", "mean_r", "win_rate", "p", "p_adj", "allowed", "held", "reason") if k in a} for s, a in list((m.get("assets") or {}).items())[:30]}}
        return out
    put("mapa_przewagi", edge)

    def lab():
        import btc_lab
        L = btc_lab.read(d)
        return {"mistrz": L.get("champion"), "alarmy": L.get("alerts"), "wnioski_o_awans": L.get("proposals"), "wykonanie": L.get("execution"),
                "warianty": [{"id": v.get("id"), "nazwa": v.get("name"), "rola": v.get("role"), "werdykt": v.get("verdict"),
                              "historia": {k: (v.get("hist") or {}).get(k) for k in ("n", "mean_r", "win_rate", "total_r", "p_week")},
                              "cien": {k: (v.get("fwd") or {}).get(k) for k in ("n", "mean_r", "win_rate", "total_r", "p_week", "weeks")}} for v in L.get("variants") or []],
                "sygnaly": L.get("signals")}
    put("laboratorium", lab)

    def shadow():
        import btc_shadow
        return (btc_shadow.read(d) or {}).get("strategies")
    put("cien", shadow)

    def copy():
        import btc_copy
        return [{"portfel": w["addr"][:10], "status": w["status"], "powod": w["why"], "poza_proba": w["fwd"]} for w in btc_copy.read(d).get("wallets", [])[:15]]
    put("kopiowanie_portfeli", copy)

    def scalp():
        import btc_scalp
        o = btc_scalp.read_out(d)
        return {k: o.get(k) for k in ("mode", "symbols", "strategies", "allowed", "gate", "gate_on", "brake", "real_by", "criteria", "blocked", "open", "day", "real", "notes")}
    put("scalper", scalp)

    def flow():
        import btc_flow
        o = btc_flow.read_out(d)
        return {"opis": "wskaznik laczny z danych o przeplywie: EKSPERYMENT bez potwierdzonej skutecznosci (na 2 latach danych polowa skladnikow nie wykazala przewagi); |wskaznik| >= 1 = wyrazne wskazanie na 12 h; trafnosc.oos to jedyny uczciwy sprawdzian", "trafnosc": o.get("accuracy"),
                "aktywa": {s: {"wskaznik": v.get("score"), "wskazanie": v.get("lean"), "skladniki": {p["key"]: [p["value"], p["unit"], p["z"]] for p in v.get("parts", [])}, "info": v.get("info")}
                           for s, v in (o.get("symbols") or {}).items()}}
    put("rynek_na_zywo", flow)
    return snap


def clean_messages(msgs):
    """Waliduje historię z przeglądarki: tylko role user/assistant, tekst, limit długości; ostatnia musi być pytaniem użytkownika."""
    if not isinstance(msgs, list) or not msgs:
        raise ValueError("brak wiadomości")
    out = []
    for m in msgs[-MAX_HISTORY:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            raise ValueError("zła postać wiadomości")
        text = m["content"].strip()
        if not text:
            continue
        if len(text) > MAX_MSG_CHARS:
            raise ValueError(f"wiadomość dłuższa niż {MAX_MSG_CHARS} znaków")
        out.append({"role": m["role"], "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    if not out or out[-1]["role"] != "user":
        raise ValueError("ostatnia wiadomość musi być pytaniem")
    return out


def _allow(cfg, now=None):
    now = now or time.time()
    day = int(cfg.get("AI_MAX_PER_DAY") or 100)
    with _lock:
        _calls[:] = [t for t in _calls if now - t < 86400]
        if sum(1 for t in _calls if now - t < 3600) >= MAX_PER_HOUR:
            raise ValueError(f"limit {MAX_PER_HOUR} pytań na godzinę")
        if len(_calls) >= day:
            raise ValueError(f"limit {day} pytań na dobę (AI_MAX_PER_DAY w ai.env)")
        _calls.append(now)


def ask(d, messages, cfg=None, client=None):
    """Jedno zapytanie do modelu. Zwraca (tekst, zużycie)."""
    import anthropic
    cfg = cfg or load_env(d)
    if client is None:
        if not cfg.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("brak klucza: wpisz ANTHROPIC_API_KEY=... do pliku ai.env obok programu")
        client = anthropic.Anthropic(api_key=cfg["ANTHROPIC_API_KEY"], timeout=120.0, max_retries=1)
    system = [{"type": "text", "text": SYSTEM},
              {"type": "text", "text": "Migawka stanu systemu (JSON):\n" + json.dumps(snapshot(d), ensure_ascii=False, default=str, separators=(",", ":"))}]
    try:
        with client.beta.messages.stream(model=cfg.get("AI_MODEL") or MODEL_DEFAULT, max_tokens=8000, system=system, messages=messages,
                                         output_config={"effort": cfg.get("AI_EFFORT") or "low"},
                                         betas=[FALLBACK_BETA], fallbacks="default") as stream:   # odmowa modelu głównego -> automatycznie model zapasowy
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise RuntimeError("klucz API odrzucony: sprawdź ANTHROPIC_API_KEY w ai.env") from None
    except anthropic.RateLimitError:
        raise RuntimeError("limit zapytań API przekroczony, spróbuj za chwilę") from None
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"błąd API ({e.status_code}): {str(e.message)[:200]}") from None
    except anthropic.APIConnectionError:
        raise RuntimeError("brak połączenia z API Anthropic") from None
    if msg.stop_reason == "refusal":
        raise RuntimeError("model odmówił odpowiedzi na to pytanie")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if msg.stop_reason == "max_tokens":
        text += "\n[odpowiedź ucięta: limit długości]"
    u = msg.usage
    return text or "(pusta odpowiedź)", {"in": u.input_tokens, "out": u.output_tokens, "model": msg.model}


def _jobs_dir(d):
    p = os.path.join(d, JOBS_DIR)
    os.makedirs(p, exist_ok=True)
    return p


def _write_job(path, job):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False)
    os.replace(tmp, path)


def _read_job(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def worker_alive(d, now=None):
    try:
        return (now or time.time()) - os.path.getmtime(os.path.join(d, JOBS_DIR, ALIVE)) < 30
    except OSError:
        return False


def start(d, body):
    """API panelu: przyjmuje pytanie i zapisuje je jako zadanie dla usługi btc-ai. Zwraca identyfikator."""
    messages = clean_messages(body.get("messages"))
    cfg = load_env(d, with_key=False)
    if not worker_alive(d):
        raise ValueError("usługa asystenta nie działa (sudo systemctl status btc-ai)")
    _allow(cfg)
    jid = uuid.uuid4().hex[:16]
    _write_job(os.path.join(_jobs_dir(d), jid + ".json"), {"status": "new", "ts": time.time(), "messages": messages})
    return jid


def result(d, jid):
    """API panelu: stan zadania."""
    jid = str(jid)
    if not JOB_RE.match(jid):
        return 404, {"error": "zły identyfikator"}
    job = _read_job(os.path.join(d, JOBS_DIR, jid + ".json"))
    if not job:
        return 404, {"error": "nie ma takiego zadania (wygasło?)"}
    if job["status"] in ("new", "run"):
        return 200, {"status": "run"}
    return 200, {k: v for k, v in job.items() if k in ("status", "text", "usage", "error")}


def info(d):
    cfg = load_env(d, with_key=False)
    alive = worker_alive(d)
    return {"ready": alive, "worker": alive, "model": cfg.get("AI_MODEL") or MODEL_DEFAULT}


# ----------------------------------------------------------------- usługa btc-ai (jedyny proces z kluczem API i dostępem do sieci)
def process(d, path, ask_fn=ask):
    job = _read_job(path)
    if not job or job.get("status") != "new":
        return False
    job["status"] = "run"
    _write_job(path, job)
    try:
        text, usage = ask_fn(d, clean_messages(job.get("messages")))
        out = {"status": "done", "ts": job["ts"], "text": text, "usage": usage}
    except Exception as e:  # noqa: BLE001
        out = {"status": "error", "ts": job["ts"], "error": str(e)[:300] if isinstance(e, (RuntimeError, ValueError)) else f"{type(e).__name__}: {str(e)[:200]}"}
    _write_job(path, out)
    return True


def tick(d, ask_fn=ask, now=None, threaded=True):
    """Jeden obrót pętli: znacznik życia, sprzątanie starych zadań, uruchomienie nowych."""
    now = now or time.time()
    jd = _jobs_dir(d)
    with open(os.path.join(jd, ALIVE), "w") as f:
        f.write(str(int(now)))
    started = 0
    for name in sorted(os.listdir(jd)):
        path = os.path.join(jd, name)
        if not name.endswith(".json"):
            if name.endswith(".tmp") and now - os.path.getmtime(path) > 60:
                os.remove(path)
            continue
        job = _read_job(path)
        if not job or now - job.get("ts", 0) > JOB_TTL_S:
            os.remove(path)
        elif job["status"] == "new" and name not in _running:
            _running.add(name)

            def work(path=path, name=name):
                try:
                    process(d, path, ask_fn)
                finally:
                    _running.discard(name)
            if threaded:
                threading.Thread(target=work, daemon=True).start()
            else:
                work()
            started += 1
    return started


def main():
    d = os.environ.get("BTC_BOT_DIR", HERE)
    cfg = load_env(d)
    print(f"btc-ai: start, model {cfg.get('AI_MODEL') or MODEL_DEFAULT}, klucz {'jest' if cfg.get('ANTHROPIC_API_KEY') else 'BRAK (wpisz do ai.env)'}", flush=True)
    while True:
        try:
            tick(d)
        except Exception as e:  # noqa: BLE001
            print(f"btc-ai: {type(e).__name__}: {e}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
