import sys, os, json, tempfile, time
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as bt
import atexit; atexit.unregister(bt.flush_notify)
bt.NOTIFY_STATE = os.path.join(tempfile.mkdtemp(), "n.json")
sent = []
class R:
    def read(self): return b"{}"
def fake(req, timeout=0):
    sent.append(json.loads(req.data.decode())["text"]); return R()
bt.urllib.request.urlopen = fake
os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], os.environ["TRADER_MODE"] = "t", "1", "demo"
# 1. kilka zdarzeń = jedna wiadomość
bt.notify("[ETHUSDT] zlecenie open_long: 1 po 100, stop 95, cel 110 (x0.50)")
bt.notify("[ETHUSDT] TP1 zrealizowany")
bt.notify("cicho", quiet=True)
bt.notify("[ETHUSDT] TP1 zrealizowany")            # duplikat w przebiegu
assert not sent
bt.flush_notify()
assert len(sent) == 1 and sent[0].count("•") == 2 and "cicho" not in sent[0] and sent[0].startswith("Bot (demo)"), sent
# 2. limit: raz na 24 h
for _ in range(5):
    bt.notify("SUMA POZYCJI 900 USDT przekracza limit", key="sum_lev", cooldown_h=24); bt.flush_notify()
assert len(sent) == 2, len(sent)
# 3. po upływie czasu znów
st = json.load(open(bt.NOTIFY_STATE)); st["sum_lev"] -= 25 * 3600; json.dump(st, open(bt.NOTIFY_STATE, "w"))
bt.notify("SUMA POZYCJI 900 USDT przekracza limit", key="sum_lev", cooldown_h=24); bt.flush_notify()
assert len(sent) == 3
# 4. nic w kolejce = nic nie wysyła; brak tokenu = nic
bt.flush_notify(); assert len(sent) == 3
del os.environ["TELEGRAM_BOT_TOKEN"]; bt.notify("x"); bt.flush_notify(); assert len(sent) == 3 and not bt.NOTIFY_QUEUE
# 5. dużo zdarzeń: skrót
os.environ["TELEGRAM_BOT_TOKEN"] = "t"
for i in range(20): bt.notify(f"zdarzenie {i}")
bt.flush_notify(); assert "i 8 więcej" in sent[-1] and len(sent[-1]) < 3900
# 6. błąd wysyłki nie rzuca
def boom(*a, **k): raise OSError("net")
bt.urllib.request.urlopen = boom; bt.notify("y"); bt.flush_notify()
print("notify ok")
