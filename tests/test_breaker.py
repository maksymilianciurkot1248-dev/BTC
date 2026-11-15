import sys, time, os
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_net as n
calls = []
import urllib.request
def boom(*a, **k): calls.append(1); raise TimeoutError('timed out')
orig = urllib.request.urlopen; urllib.request.urlopen = boom; n.time.sleep = lambda *_: None
errs = []
for i in range(6):
    try: n.get('http://x.test/a', retries=0)
    except RuntimeError as e: errs.append(str(e))
assert len(calls) == 3, calls                                  # po 3 porażkach reszta bez połączeń
assert 'pomijany' in errs[3] and 'połączenie' in errs[0], errs
n._open_until['x.test'] = 0; calls.clear()
class R:
    headers = {}
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s): return b'{"a":1}'
urllib.request.urlopen = lambda *a, **k: R()
assert n.get('http://x.test/a') == {'a': 1} and n._fails['x.test'] == 0
n._disabled.add('off.test')
try: n.get('http://off.test/'); assert False
except RuntimeError as e: assert 'wyłączony' in str(e)
# trip z btc_markets
import btc_markets as bm
n._open_until.clear(); n._fails.clear()
for _ in range(3):
    try: bm.fetch_stooq_ohlcv(lambda u, as_text=False: '<!DOCTYPE html>', 'x')
    except RuntimeError: pass
assert n._open_until.get('stooq.com', 0) > time.time()
print('breaker ok')
os.environ['BTC_DISABLE_HOSTS'] = 'stooq.com'                  # ustawione po imporcie (jak po load_dotenv)
try: n.get('https://stooq.com/q/d/l/?s=x'); assert False
except RuntimeError as e: assert 'wyłączony' in str(e)
del os.environ['BTC_DISABLE_HOSTS']
print('env ok')
