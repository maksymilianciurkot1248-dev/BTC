import sys, os, tempfile, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc
from datetime import datetime, timezone
bc.time.sleep = lambda *_: None
now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)

VIX = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2026,16.0,17.5,15.2,16.4\n01/05/2026,16.4,18.0,16.0,17.9\n"
SKEW = "DATE,SKEW\n01/02/2026,141.2\n01/05/2026,143.9\n"
UST = ("Date,1 Mo,2 Mo,3 Mo,4 Mo,6 Mo,1 Yr,2 Yr,3 Yr,5 Yr,7 Yr,10 Yr,20 Yr,30 Yr\n"
       "11/04/2026,4.1,4.1,4.0,3.9,3.8,3.7,3.60,3.65,3.80,3.95,4.10,4.55,4.60\n11/03/2026,4.1,4.1,4.0,3.9,3.8,3.7,3.55,3.60,3.75,3.90,4.05,4.50,4.58\n")
HL = [{"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "FOO"}]},
      [{"funding": "0.0000125", "openInterest": "10000", "markPx": "80000", "premium": "0.0003"}, {"funding": "-0.00001", "openInterest": "50000", "markPx": "2400", "premium": "-0.0001"},
       {"funding": "0.0", "openInterest": "1", "markPx": "10", "premium": "0"}]]
calls = []

def get(url, as_text=False, timeout=0, retries=0):
    calls.append(url)
    if 'cboe.com' in url:
        if 'VIX3M' in url: raise RuntimeError('HTTP 403')                    # jedno źródło pada
        return SKEW if 'SKEW' in url else VIX if '/VIX_' in url else VIX
    if 'treasury.gov' in url: return UST
    if 'newyorkfed' in url: return {"refRates": [{"effectiveDate": "2026-11-03", "percentRate": 4.05}, {"effectiveDate": "2026-11-04", "percentRate": 4.06}]}
    if 'gdeltproject' in url:
        if 'recession' in url: raise RuntimeError('HTTP 429')
        tone = 'timelinetone' in url
        return {"timeline": [{"series": "x", "data": [{"date": "20261103T000000Z", "value": -1.5 if tone else 100}, {"date": "20261103T001500Z", "value": -2.5 if tone else 200}, {"date": "20261104T000000Z", "value": -3.0 if tone else 50}]}]}
    raise RuntimeError('HTTP 404')
post = lambda url, payload, **k: HL if payload == {"type": "metaAndAssetCtxs"} else (_ for _ in ()).throw(RuntimeError('zły payload'))

db = bc.DB(os.path.join(tempfile.mkdtemp(), 'x.db'))
rep = bc.run('backfill', db, get=get, post=post, now=now, only={'cboe', 'hyperliquid', 'gdelt', 'treasury'})
print(rep)
assert all(v.startswith('OK') for v in rep.values()), rep
q = lambda m: db.con.execute("select ts, value from obs where metric=? order by ts", (m,)).fetchall()
d = lambda s: int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
assert q('cboe_vix') == [(d('2026-01-02'), 16.4), (d('2026-01-05'), 17.9)] and q('cboe_skew')[1][1] == 143.9 and not q('cboe_vix3m')       # VIX3M padło, reszta jest
assert db.con.execute("select count(*), max(c) from candles where symbol='VIX'").fetchone() == (2, 17.9) and db.con.execute("select h from candles where symbol='VIX' and ts=?", (d('2026-01-05'),)).fetchone()[0] == 18.0
assert q('ust_10y') == [(d('2026-11-03'), 4.05), (d('2026-11-04'), 4.10)] and abs(q('ust_10y_2y')[-1][1] - 0.50) < 1e-9 and q('ust_2y')[0][1] == 3.55
assert q('sofr')[-1] == (d('2026-11-04'), 4.06)
hl = {m: v for m, ts, v in db.con.execute("select metric, ts, value from obs where metric like 'hl_%'").fetchall()}
assert abs(hl['hl_funding_ann_pct_btc'] - 0.0000125 * 24 * 365 * 100) < 1e-9 and hl['hl_oi_usd_btc'] == 8e8 and abs(hl['hl_premium_pct_btc'] - 0.03) < 1e-9 and hl['hl_funding_ann_pct_eth'] < 0
assert hl['hl_total_oi_usd'] == 8e8 + 1.2e8 + 10 and 'hl_funding_ann_pct_foo' not in hl
assert q('gdelt_vol_bitcoin') == [(d('2026-11-03'), 300.0), (d('2026-11-04'), 50.0)]                       # natężenie: suma dnia
assert q('gdelt_tone_bitcoin') == [(d('2026-11-03'), -2.0), (d('2026-11-04'), -3.0)]                       # ton: średnia dnia
assert not q('gdelt_vol_recession') and q('gdelt_vol_fed')                                                 # 429 dla jednego tematu nie psuje reszty
assert any('timespan=1y' in u for u in calls)                                                              # backfill: rok historii
# update: krótsze okna
calls.clear(); db2 = bc.DB(os.path.join(tempfile.mkdtemp(), 'y.db')); bc.run('update', db2, get=get, post=post, now=now, only={'gdelt', 'treasury'})
assert any('timespan=10d' in u for u in calls) and sum('daily-treasury-rates' in u for u in calls) == 1
# Hyperliquid: pusta odpowiedź = błąd źródła, nie wyjątek
db3 = bc.DB(os.path.join(tempfile.mkdtemp(), 'z.db'))
r3 = bc.run('update', db3, get=get, post=lambda *a, **k: [{"universe": []}, []], now=now, only={'hyperliquid'})
assert r3['hyperliquid'].startswith('BŁĄD'), r3
print('free2 ok')
