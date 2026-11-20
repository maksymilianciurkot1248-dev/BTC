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
# rola mentora: osobny prompt bez migawki bota, nieznana rola = panel
import unittest.mock as um
seen = {}
class FakeStream:
    def __init__(s, **k): seen.update(k)
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def get_final_message(s):
        B = type("B", (), {"type": "text", "text": "odp"}); U = type("U", (), {"input_tokens": 1, "output_tokens": 1})
        return type("M", (), {"stop_reason": "end_turn", "content": [B], "usage": U, "model": "m"})()
class FakeClient:
    class beta:
        class messages:
            stream = staticmethod(lambda **k: FakeStream(**k))
ai.ask(d, [{"role": "user", "content": "x"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=FakeClient, role="mentor")
check(len(seen["system"]) == 1 and "mentorem biznesowym" in seen["system"][0]["text"] and "nie fałszujesz" in seen["system"][0]["text"], "mentor: własny prompt, bez migawki stanu bota")
ai.ask(d, [{"role": "user", "content": "x"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=FakeClient)
check(len(seen["system"]) == 2 and "Migawka" in seen["system"][1]["text"], "panel: prompt i migawka")
ai._calls.clear()
jid = ai.start(d, {"messages": [{"role": "user", "content": "pomysł na biznes"}], "role": "mentor"})
check(json.load(open(os.path.join(d, ai.JOBS_DIR, jid + ".json")))["role"] == "mentor", "rola zapisana w zadaniu")
got = []
ai.tick(d, ask_fn=lambda d_, msgs, role="panel": got.append((msgs[-1]["content"], role)) or ("ok", {"in": 1, "out": 1, "model": "m"}), threaded=False)
check(("pomysł na biznes", "mentor") in got, f"usługa przekazuje rolę {got}")
jid2 = ai.start(d, {"messages": [{"role": "user", "content": "x"}], "role": "root"})
check(json.load(open(os.path.join(d, ai.JOBS_DIR, jid2 + ".json")))["role"] == "panel", "nieznana rola = panel")
# wyszukiwanie w internecie (mentor): narzędzie, pause_turn, źródła, limit dobowy
import types as _t
check(ai.search_tool("claude-opus-5-5", 5)["type"] == "web_search_20260209" and ai.search_tool("claude-haiku-4-5", 3)["type"] == "web_search_20250305", "wariant narzędzia zależy od modelu")
tl = ai.search_tool("claude-opus-5-5", 5)
check(tl["name"] == "web_search" and tl["max_uses"] == 5 and tl["user_location"]["country"] == "PL", "max_uses i lokalizacja")
def blk(type, **k): return _t.SimpleNamespace(type=type, **k)
cite = _t.SimpleNamespace(url="https://przyklad.pl/a", title="Trend A", page_age="3 dni temu")
R = [blk("server_tool_use", name="web_search", input={}), blk("web_search_tool_result", content=[cite, _t.SimpleNamespace(url="https://przyklad.pl/b", title="B", page_age=None)]),
     blk("text", text="Pomysł 1.", citations=[cite]), blk("text", text=" Pomysł 2.", citations=None)]
st = ai.sources_text(R)
check(st.count("przyklad.pl/a") == 1 and "przyklad.pl/b" in st and "brak daty" in st and "3 dni temu" in st, f"źródła bez duplikatów, z datą {st}")
check(ai.sources_text([blk("text", text="x", citations=None)]) == "", "bez źródeł nic nie dopisujemy")
calls = []
def mk_msg(stop, content, i=1, o=1): return _t.SimpleNamespace(stop_reason=stop, content=content, usage=_t.SimpleNamespace(input_tokens=i, output_tokens=o), model="claude-opus-5-5")
class SeqClient:
    def __init__(s, seq): s.seq = list(seq); s.beta = s; s.messages = s
    def stream(s, **k):
        calls.append(k); m = s.seq.pop(0)
        class C:
            def __enter__(c): return c
            def __exit__(c, *a): return False
            def get_final_message(c): return m
        return C()
cl = SeqClient([mk_msg("pause_turn", R[:2], 10, 5), mk_msg("end_turn", R[2:], 20, 7)])
text, usage = ai.ask(d, [{"role": "user", "content": "co na topie"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=cl, role="mentor", search=True)
check(len(calls) == 2 and calls[0]["tools"][0]["name"] == "web_search" and len(calls[0]["system"]) == 2 and "Źródła" in text and "Pomysł 1." in text and "Pomysł 2." in text, "pause_turn: wznowienie i złożenie odpowiedzi")
check(calls[1]["messages"][-1]["role"] == "assistant" and usage["in"] == 30 and usage["out"] == 12 and usage["searches"] == 1, f"wznowienie od przerwanej tury, zużycie zsumowane {usage}")
calls.clear()
ai.ask(d, [{"role": "user", "content": "x"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=SeqClient([mk_msg("end_turn", R[2:])]), role="mentor")
check("tools" not in calls[0] and len(calls[0]["system"]) == 1, "bez search: bez narzędzia")
calls.clear()
ai.ask(d, [{"role": "user", "content": "x"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=SeqClient([mk_msg("pause_turn", R[:2])] * 4), role="mentor", search=True)
check(len(calls) == ai.SEARCH_LOOPS, "pętla pause_turn ma twardy limit")
err = [blk("web_search_tool_result", content=_t.SimpleNamespace(error_code="max_uses_exceeded")), blk("text", text="Odp.", citations=None)]
t2, _ = ai.ask(d, [{"role": "user", "content": "x"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=SeqClient([mk_msg("end_turn", err)]), role="mentor", search=True)
check("max_uses_exceeded" in t2, "błąd wyszukiwania (HTTP 200 z obiektem błędu) jest widoczny, nie wywala odpowiedzi")
ai._calls.clear()
open(os.path.join(d, "ai.env"), "a").write("MENTOR_MAX_SEARCH_PER_DAY=2\n")
for _ in range(2): ai.start(d, {"messages": [{"role": "user", "content": "x"}], "role": "mentor", "search": True})
try: ai.start(d, {"messages": [{"role": "user", "content": "x"}], "role": "mentor", "search": True}); check(False, "limit")
except ValueError as e: check("przeglądów internetu" in str(e), "limit dobowy przeglądów")
j = ai.start(d, {"messages": [{"role": "user", "content": "x"}], "role": "mentor"})
check(json.load(open(os.path.join(d, ai.JOBS_DIR, j + ".json")))["search"] is False and ai.info(d)["search_left"] == 0, "zwykła rozmowa nie zużywa limitu; info pokazuje pozostałe")
j = ai.start(d, {"messages": [{"role": "user", "content": "x"}], "role": "panel", "search": True}) if False else None
# wyszukiwarka ofert: tylko serwisy z ogłoszeniami, oferty ze zmyślonym adresem odrzucone
check(ai.search_tool("claude-opus-5-5", 5, ai.OFFER_DOMAINS)["allowed_domains"][0] == "olx.pl" and "allowed_domains" not in ai.search_tool("claude-opus-5-5", 5), "domeny ofert")
UR = ["https://www.olx.pl/d/oferta/rower-A-CID1.html", "https://allegrolokalnie.pl/oferta/rower-b"]
js = ai.offers_json('tekst {"offers":[{"title":"Rower A","price_pln":300,"place":"Dębica","url":"https://www.olx.pl/d/oferta/rower-A-CID1.html","source":"olx","why":"tanio","est_sell_pln":450},'
                    '{"title":"Zmyślony","price_pln":1,"url":"https://olx.pl/d/oferta/nie-ma-takiej.html"},{"title":"B","price_pln":"abc","url":"https://allegrolokalnie.pl/oferta/rower-b/#x"},"śmieć"]} koniec', set(UR))
dd = json.loads(js[0])
check(len(dd["offers"]) == 2 and dd["dropped"] == 2 and dd["offers"][0]["est_sell_pln"] == 450 and dd["offers"][1]["price_pln"] is None, f"adres spoza wyników odrzucony, zła cena = null {dd}")
check(ai.offers_json("brak json", set()) is None and ai.offers_json('{"offers":"x"}', set()) is None, "nieczytelna odpowiedź = None")
Rr = [blk("server_tool_use", name="web_search", input={}), blk("web_search_tool_result", content=[_t.SimpleNamespace(url=UR[0], title="t", page_age=None), _t.SimpleNamespace(url=UR[1], title="t", page_age=None)]),
      blk("text", text='{"offers":[{"title":"Rower A","price_pln":300,"url":"%s","source":"olx","why":"x"},{"title":"Fałsz","url":"https://olx.pl/zmyslone"}]}' % UR[0], citations=None)]
calls.clear()
t3, u3 = ai.ask(d, [{"role": "user", "content": "rower"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=SeqClient([mk_msg("end_turn", Rr)]), role="offers")
o3 = json.loads(t3)
check(calls[0]["tools"][0]["allowed_domains"] == ai.OFFER_DOMAINS and calls[0]["system"][0]["text"] == ai.OFFERS and len(o3["offers"]) == 1 and o3["dropped"] == 1 and u3["searches"] == 1, "ask(role=offers): wynik tylko z prawdziwych adresów")
t4, _ = ai.ask(d, [{"role": "user", "content": "x"}], cfg={"ANTHROPIC_API_KEY": "k"}, client=SeqClient([mk_msg("end_turn", [blk("text", text="nie umiem", citations=None)])]), role="offers")
check(json.loads(t4)["offers"] == [] and "error" in json.loads(t4), "niepoprawna odpowiedź modelu nie psuje strony")
ai._calls.clear(); open(os.path.join(d, "mentor_search.json"), "w").write("{}")
j2 = ai.start(d, {"messages": [{"role": "user", "content": "rower"}], "role": "offers"})
check(json.load(open(os.path.join(d, ai.JOBS_DIR, j2 + ".json")))["search"] is True, "oferty zawsze liczą się do limitu przeglądów")
print("ai ok", ok)
