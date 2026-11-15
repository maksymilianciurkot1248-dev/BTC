import sys, os, tempfile, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc, btc_markets as bm, btc_live as bl
from datetime import datetime, timezone, timedelta
bc.time.sleep = lambda *_: None
now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
DAYMS = 86400000
T0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def okx_rows(n, base=100.0, last_confirmed=True):
    # format z prawdziwej odpowiedzi OKX: [ts_ms, o, h, l, c, vol, volCcy, volCcyQuote, confirm], najnowsze pierwsze
    rows = [[str(T0 + i * DAYMS), str(base + i), str(base + i + 2), str(base + i - 2), str(base + i + 1), "10", "1000", "1000", "1"] for i in range(n)][::-1]
    if not last_confirmed:
        rows[0][8] = "0"
    return rows


def get(url, as_text=False, timeout=0, retries=0):
    if 'api.binance.com' in url or 'fapi.binance' in url:
        raise RuntimeError('HTTP 451')
    if 'stooq.com' in url:
        raise RuntimeError('połączenie: [Errno 104] Connection reset by peer')
    if 'okx.com/api/v5/market/history-candles' in url:
        inst = url.split('instId=')[1].split('&')[0]
        after = int(url.split('after=')[1]) if 'after=' in url else None
        rows = okx_rows(150, 4000 if 'XAUT' in inst else 100, last_confirmed=False)
        if after:
            rows = [r for r in rows if int(r[0]) < after]
        return {"code": "0", "data": rows[:100]}
    if 'polygon.io' in url:
        if 'BADTICK' in url:
            return {"status": "ERROR", "error": "bad key POLYSECRET"}
        base = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
        return {"status": "OK", "results": [{"t": base + i * DAYMS, "o": 1.0 + i, "h": 2.0 + i, "l": 0.5 + i, "c": 1.5 + i, "v": 10} for i in range(30)]}
    if 'twelvedata' in url:
        if 'BAD' in url:
            return {"status": "error", "message": "bad symbol apikey=TDSECRETKEY"}
        return {"values": [{"datetime": "2026-11-03", "open": "1.1", "high": "1.2", "low": "1.0", "close": "1.15", "volume": "0"},
                           {"datetime": "2026-11-02", "open": "1.0", "high": "1.1", "low": "0.9", "close": "1.05"}]}
    if 'frankfurter' in url:
        if '..' in url:
            raise RuntimeError('HTTP 522')
        return {"amount": 1.0, "base": "USD", "date": "2026-11-03", "rates": {"PLN": 3.95, "EUR": 0.86}}
    raise RuntimeError('HTTP 404')


# --- OKX: tylko zamknięte świece, paginacja, sortowanie rosnąco
rows = bm.fetch_okx_daily(get, 'BTC-USDT')
assert len(rows) == 149 and rows[0][0] < rows[-1][0] and rows[-1][4] > 0, len(rows)       # 150 - 1 niezamknięta
# --- Polygon
pg = bm.fetch_polygon_ohlcv(get, 'C:XAUUSD', 'POLYSECRET')
assert len(pg) == 30 and pg[0][0] % 86400 == 0 and pg[0][4] == 1.5
try:
    bm.fetch_polygon_ohlcv(get, 'BADTICK', 'POLYSECRET'); assert 0
except RuntimeError as e:
    assert 'POLYSECRET' not in str(e)
# --- Twelve Data
td = bm.fetch_twelvedata_ohlcv(get, 'EUR/USD', 'KEY')
assert [r[4] for r in td] == [1.05, 1.15] and td[0][0] < td[1][0]
try:
    bm.fetch_twelvedata_ohlcv(get, 'BAD', 'TDSECRETKEY'); assert 0
except RuntimeError as e:
    assert 'TDSECRETKEY' not in str(e)
# --- EBC (EUR jako baza -> USD jako baza)
ECB='<gesmes:Envelope><Cube><Cube time="2026-11-03"><Cube currency="USD" rate="1.1600"/><Cube currency="PLN" rate="4.2400"/><Cube currency="JPY" rate="174.00"/></Cube><Cube time="2026-11-02"><Cube currency="USD" rate="1.1500"/><Cube currency="PLN" rate="4.2000"/></Cube></Cube></gesmes:Envelope>'
e=bm.fetch_fx_ecb(lambda u,as_text=False,timeout=0:ECB, now.date()-timedelta(days=5), now.date())
assert abs(e['PLN'][-1][1]-4.24/1.16)<1e-9 and abs(e['EUR'][-1][1]-1/1.16)<1e-9 and abs(e['JPY'][0][1]-0.0) < 200 and len(e['PLN'])==2 and e['PLN'][0][0]<e['PLN'][1][0]
# --- Frankfurter: zakresy padają (522) i EBC niedostępny, zapas = bieżący kurs
fx = bm.fetch_fx(get, now.date() - timedelta(days=200), now.date())
assert fx['PLN'] == [(int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp()), 3.95)], fx['PLN']
# --- kolektor: Binance 451 + Stooq reset -> OKX (krypto, złoto z tokenu XAUT)
db = bc.DB(os.path.join(tempfile.mkdtemp(), 'f.db'))
os.environ.pop('TWELVEDATA_API_KEY', None)
rep = bc.run('backfill', db, get=get, now=now, only={'binance_klines', 'klines_eth', 'klines_crypto', 'klines_paxg', 'commodities', 'fx'})
print(rep)
assert rep['binance_klines'].startswith('OK') and rep['klines_eth'].startswith('OK') and rep['klines_crypto'].startswith('OK') and rep['klines_paxg'].startswith('OK'), rep
n = lambda q: db.con.execute(q).fetchone()[0]
assert n("select count(*) from candles where symbol='BTCUSDT' and source like 'okx%'") == 149
assert n("select count(distinct symbol) from candles where source like 'okx%'") >= 12            # 12 krypto + PAXG + złoto
assert n("select count(*) from candles where symbol='XAUUSD' and source like 'okx%'") == 149      # złoto z XAUT, gdy Stooq zawiódł
assert rep['commodities'].startswith('OK') and rep['fx'].startswith('OK')                    # częściowy sukces nie wywala źródła
# --- z kluczem Polygon (przed Twelve Data) waluty i złoto idą przez Polygon
os.environ.pop('TWELVEDATA_API_KEY', None); os.environ['POLYGON_API_KEY'] = 'POLYSECRET'
bc.POLY_PAUSE = 0
db3 = bc.DB(os.path.join(tempfile.mkdtemp(), 'f3.db'))
rep3 = bc.run('backfill', db3, get=get, now=now, only={'commodities'})
assert rep3['commodities'].startswith('OK')
assert db3.con.execute("select count(*) from candles where symbol='EURUSD' and source='polygon'").fetchone()[0] == 30
assert db3.con.execute("select count(*) from candles where symbol='XAUUSD' and source='polygon'").fetchone()[0] == 30     # Polygon wygrywa z zapasem OKX
assert 'POLYSECRET' not in ' '.join(str(r[0]) for r in db3.con.execute('select error from runs'))
os.environ.pop('POLYGON_API_KEY')
# --- z kluczem Twelve Data waluty idą przez TD
os.environ['TWELVEDATA_API_KEY'] = 'TDSECRETKEY'
db2 = bc.DB(os.path.join(tempfile.mkdtemp(), 'f2.db'))
rep2 = bc.run('backfill', db2, get=get, now=now, only={'commodities'})
assert rep2['commodities'].startswith('OK')
assert db2.con.execute("select count(*) from candles where symbol='EURUSD' and source='twelvedata'").fetchone()[0] == 2
assert 'TDSECRETKEY' not in ' '.join(str(r[0]) for r in db2.con.execute('select error from runs'))
# --- live: Bybit 403 -> OKX; złoto z XAUT, gdy Stooq nie działa
def lget(url, as_text=False, timeout=0, retries=0):
    if 'bybit' in url:
        raise RuntimeError('HTTP 403')
    if 'okx.com/api/v5/market/tickers' in url:
        return {"data": [{"instId": "BTC-USDT", "last": "78000", "open24h": "76000"}, {"instId": "XAUT-USDT", "last": "4030", "open24h": "4000"}, {"instId": "SOL-EUR", "last": "1", "open24h": "1"}, {"instId": "FOO-USDT", "last": "1", "open24h": "1"}]}
    raise RuntimeError('połączenie: reset')
snap = bl.snapshot(lget)
q = snap['quotes']
assert q['BTCUSDT']['src'] == 'okx' and abs(q['BTCUSDT']['chg24'] - 2.6316) < 0.01 and 'FOO' not in str(q) and 'SOLEUR' not in q
assert q['XAUUSD']['src'] == 'xaut' and q['XAUUSD']['price'] == 4030, q.get('XAUUSD')
assert any('bybit' in e for e in snap['errors'])
print('fallbacks ok')
