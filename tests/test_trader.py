import sys; sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as t
# sizing: kapitał 1000, ryzyko 0.5% = 5 USDT, stop 2% od 100000 => dist 2000 => qty .0025; limit dźwigni x2 => 0.02
assert t.size_position(1000,0.5,100000,98000,2,0.001,0.001)==0.002, t.size_position(1000,0.5,100000,98000,2,0.001,0.001)
# ciasny stop: dist 100 => risk-qty 0.05, cap x2 => 0.02 (limit dźwigni wygrywa)
assert t.size_position(1000,0.5,100000,99900,2,0.001,0.001)==0.02
assert t.size_position(10,0.5,100000,98000,2,0.001,0.001)==0.0   # poniżej minimum
q=t.size_position(1234.5,1,67000,65000,2,0.001,0.001); assert q*67000<=1234.5*2
P=lambda **k:{**dict(action='long',valid_until_ms=10**13,live_state='waiting',entry=100,stop=95,tp1=110),**k}
assert t.plan_ok_to_trade(P(),1)[0]
assert not t.plan_ok_to_trade(P(live_state='through'),1)[0]
assert not t.plan_ok_to_trade(P(stop=105),1)[0]
assert not t.plan_ok_to_trade(P(valid_until_ms=0),1)[0]
assert t.plan_ok_to_trade(P(action='short',entry=100,stop=105,tp1=90),1)[0]
assert t.fmt_px(100000.123,0.1)=='100000.1'
print('ok')
import os
os.environ.update({"TRADER_MODE": "demo", 'TRADER_API_KEY': 'k', 'TRADER_API_SECRET': 's'}); os.environ.pop('TRADER_BASE_URL', None)
import btc_trader as _bt
assert _bt.cfg()['base'] == 'https://api-demo.bybit.com' and _bt.cfg()['max_lev'] <= 2
print('demo ok')
# klucze według trybu
env = {"BYBIT_TEST_API_KEY": "tk", "BYBIT_TEST_API_SECRET": "ts", "BYBIT_LIVE_API_KEY": "lk", "BYBIT_LIVE_API_SECRET": "ls", "TRADER_API_KEY": "old", "TRADER_API_SECRET": "olds"}
g = env.get
assert _bt.pick_keys("demo", g) == ("tk", "ts") and _bt.pick_keys("testnet", g) == ("tk", "ts") and _bt.pick_keys("live", g) == ("lk", "ls")
assert _bt.pick_keys("demo", {"TRADER_API_KEY": "old", "TRADER_API_SECRET": "os"}.get) == ("old", "os") and _bt.pick_keys("dryrun", g) == ("old", "olds")
# stary klucz (>90 dni) tylko ostrzega, nie przerywa
os.environ["TRADER_KEY_CREATED"] = "2025-01-01"; _bt.key_age_check({}); print("pick_keys ok")
