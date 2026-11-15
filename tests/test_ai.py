import sys, os, json, tempfile, time, types
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_ai as ai
import anthropic
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
d = tempfile.mkdtemp()
open(os.path.join(d, "ai.env"), "w").write("# komentarz\nANTHROPIC_API_KEY=sk-test-123456789\nAI_EFFORT=medium\n")
open(os.path.join(d, ".env"), "w").write("BYBIT_API_SECRET=SEKRET_GIELDY_XYZ\n")
# 1. konfiguracja
cfg = ai.load_env(d)
check(cfg["ANTHROPIC_API_KEY"] == "sk-test-123456789" and cfg["AI_EFFORT"] == "medium", "ai.env")
check("ANTHROPIC_API_KEY" not in ai.load_env(d, with_key=False), "API bez klucza")
# 2. migawka: jest, nie zawiera sekretów, rozsądny rozmiar
snap = json.dumps(ai.snapshot(d), ensure_ascii=False, default=str)
check("SEKRET_GIELDY_XYZ" not in snap and "sk-test" not in snap, "bez kluczy")
check(len(snap) < 60000 and "status_bota" in snap and "laboratorium" in snap and "rynek_na_zywo" in snap and "scalper" in snap, "migawka")
# 3. walidacja historii
for bad in (None, [], [{"role": "system", "content": "x"}], [{"role": "user", "content": 5}], [{"role": "user", "content": "x" * 5000}], [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]):
    try: ai.clean_messages(bad); check(False, f"powinno odrzucić {str(bad)[:40]}")
    except ValueError: check(True, "odrzucone")
m = ai.clean_messages([{"role": "assistant", "content": "hej"}, {"role": "user", "content": " pytanie "}])
check(m == [{"role": "user", "content": "pytanie"}], "początek od user, przycięte")
# 4. ask z udawanym klientem: sprawdzamy parametry zapytania
seen = {}
class Stream:
    def __init__(s, **kw): seen.update(kw)
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def get_final_message(s):
        return types.SimpleNamespace(stop_reason=seen.get("_stop", "end_turn"), model="claude-opus-5-5", content=[types.SimpleNamespace(type="thinking", thinking=""), types.SimpleNamespace(type="text", text="Bot czeka, bo żadne aktywo nie ma przewagi.")],
                                     usage=types.SimpleNamespace(input_tokens=1200, output_tokens=40))
client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: Stream(**kw))))
text, usage = ai.ask(d, m, cfg, client=client)
check(text.startswith("Bot czeka") and usage == {"in": 1200, "out": 40, "model": "claude-opus-5-5"}, "odpowiedź")
check(seen["model"] == "claude-opus-5-5" and seen["output_config"] == {"effort": "medium"} and seen["fallbacks"] == "default" and seen["betas"] == ["server-side-fallback-2026-07-01"], "parametry")
check("thinking" not in seen and "temperature" not in seen and seen["messages"] == m and len(seen["system"]) == 2 and "Migawka" in seen["system"][1]["text"], "bez thinking/temperature, system 2 bloki")
seen["_stop"] = "refusal"
class S2(Stream):
    def get_final_message(s): return types.SimpleNamespace(stop_reason="refusal", model="x", content=[], usage=types.SimpleNamespace(input_tokens=1, output_tokens=0))
c2 = types.SimpleNamespace(beta=types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: S2(**kw))))
try: ai.ask(d, m, cfg, client=c2); check(False, "odmowa")
except RuntimeError as e: check("odmówił" in str(e), "odmowa zgłoszona")
try: ai.ask(d, m, {}); check(False, "brak klucza")
except RuntimeError as e: check("ai.env" in str(e), "brak klucza zgłoszony")
# 5. API bez działającej usługi: odmowa
try: ai.start(d, {"messages": m}); check(False, "bez usługi")
except ValueError as e: check("btc-ai" in str(e), "usługa nie działa")
check(ai.info(d)["ready"] is False, "info: nie gotowy")
# 6. pełny obieg: tick (żyje) -> start -> tick wykonuje -> result
ai.tick(d, ask_fn=lambda d_, msgs: ("odp", {"in": 1, "out": 1, "model": "m"}), threaded=False)
check(ai.info(d)["ready"] is True, "info: gotowy")
jid = ai.start(d, {"messages": m})
check(ai.result(d, jid) == (200, {"status": "run"}), "w toku")
n = ai.tick(d, ask_fn=lambda d_, msgs: ("odp: " + msgs[-1]["content"], {"in": 1, "out": 1, "model": "m"}), threaded=False)
code, r = ai.result(d, jid)
check(n == 1 and code == 200 and r["status"] == "done" and r["text"] == "odp: pytanie" and "messages" not in r, "gotowe")
# 7. błąd modelu trafia do wyniku
jid2 = ai.start(d, {"messages": m})
def boom(d_, msgs): raise RuntimeError("klucz API odrzucony")
ai.tick(d, ask_fn=boom, threaded=False)
check(ai.result(d, jid2)[1] == {"status": "error", "error": "klucz API odrzucony"}, "błąd")
# 8. zły identyfikator (próba wyjścia z katalogu), wygasanie
check(ai.result(d, "../../etc/passwd")[0] == 404 and ai.result(d, "0" * 16)[0] == 404, "identyfikator")
ai.tick(d, ask_fn=boom, now=time.time() + 1000, threaded=False)
check(ai.result(d, jid)[0] == 404, "stare zadania usunięte")
# 9. limit na godzinę
ai._calls.clear(); ai.tick(d, ask_fn=boom, threaded=False)
for i in range(ai.MAX_PER_HOUR): ai.start(d, {"messages": m})
try: ai.start(d, {"messages": m}); check(False, "limit")
except ValueError as e: check("limit" in str(e), "limit godzinowy")
print("ai ok", ok)
