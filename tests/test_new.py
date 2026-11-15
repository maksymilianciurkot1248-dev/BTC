import sys, os, tempfile, random, math, time, json, io, urllib.error
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_net, btc_collect as bc, btc_markets as bm, btc_live as bl, btc_situations as bs
from datetime import datetime, timezone, timedelta
real_sleep=time.sleep
# ---- 1) btc_net: ponowienia, gzip, brak wycieku URL
import gzip, urllib.request
calls={'n':0}
class R:
    def __init__(s,b,enc=None): s._b=b; s.headers={'Content-Encoding':enc} if enc else {}
    def read(s): return s._b
    def __enter__(s): return s
    def __exit__(s,*a): pass
def fake(req,timeout=0):
    calls['n']+=1
    if calls['n']<3: raise urllib.error.HTTPError(req.full_url,503,'x',{'Retry-After':'0'},None)
    return R(gzip.compress(b'{"ok":1}'),'gzip')
orig=urllib.request.urlopen; urllib.request.urlopen=fake; btc_net.time.sleep=lambda *_:None
assert btc_net.get('https://example.com/x?key=SECRET')=={'ok':1} and calls['n']==3
def dead(req,timeout=0): raise urllib.error.HTTPError(req.full_url,403,'x',{},None)
urllib.request.urlopen=dead
try: btc_net.get('https://example.com/x?key=SECRET'); assert 0
except RuntimeError as e: assert 'SECRET' not in str(e) and '403' in str(e)
urllib.request.urlopen=orig; print('net ok')

# ---- 2) kolektor: wszystkie nowe źródła, równolegle
now=datetime(2026,11,3,12,tzinfo=timezone.utc); rnd=random.Random(5)
def bdays(a,b):
    d=a;o=[]
    while d<=b:
        if d.weekday()<5:o.append(d)
        d+=timedelta(days=1)
    return o
days=bdays(datetime(2019,1,1),datetime(2026,11,2)); caldays=[datetime(2019,1,1)+timedelta(days=i) for i in range(2864)]
def stooq_csv(since=None,seed=0):
    r=random.Random(seed); p=100.0; rows=["Date,Open,High,Low,Close,Volume"]
    for d in days:
        p*=1+r.gauss(0.0003,0.01); rows.append(f"{d:%Y-%m-%d},{p},{p*1.01},{p*0.99},{p},1000")
    return "\n".join(rows)
active={'n':0,'max':0}
def get(url, as_text=False, timeout=0, retries=0):
    import threading
    active['n']+=1; active['max']=max(active['max'],active['n']); real_sleep(0.01); active['n']-=1
    if 'stooq.com/q/l/' in url: raise RuntimeError('HTTP 404')
    if 'stooq.com' in url:
        code=url.split('s=')[1].split('&')[0]
        if 'd1=' in url:                                   # przyrostowo: tylko kilka ostatnich dni
            full=stooq_csv(seed=hash(code)%97).split("\n"); return "\n".join(full[:1]+full[-4:])
        return stooq_csv(seed=hash(code)%97)
    if 'api.binance.com/api/v3/klines' in url:
        sym=url.split('symbol=')[1].split('&')[0]; st=int(url.split('startTime=')[1]); t0=int(datetime(2021,1,1,tzinfo=timezone.utc).timestamp()*1000)
        rows=[[t0+i*86400000,"100","101","99","100.5","10",t0+i*86400000+86399999,"1000000","5000","5.5","550000","0"] for i in range(1500) if t0+i*86400000>=st]
        return rows[:1000]
    if 'fundingRate' in url: return []
    if 'futures/data' in url: return []
    if 'fapi.binance.com' in url: return {"markPrice":"1","indexPrice":"1","lastFundingRate":"0.0001"}
    if 'alternative.me' in url: return {"data":[{"value":"50","timestamp":str(int((datetime(2022,1,1)+timedelta(days=i)).replace(tzinfo=timezone.utc).timestamp()))} for i in range(200)]}
    if 'frankfurter' in url:
        a,b=url.split('v1/')[1].split('?')[0].split('..'); a=datetime.strptime(a,"%Y-%m-%d"); b=datetime.strptime(b,"%Y-%m-%d")
        return {"rates":{d.strftime("%Y-%m-%d"):{c:(4 if c=="PLN" else 1)*(1+0.05*math.sin(d.toordinal()/90+i)) for i,c in enumerate(bm.FX_CCY)} for d in bdays(a,b)}}
    if 'publicreporting.cftc.gov' in url:
        return [{"report_date_as_yyyy_mm_dd":f"{(datetime(2024,1,2)+timedelta(days=7*i)):%Y-%m-%d}T00:00:00.000","noncomm_positions_long_all":str(200000+i*100),"noncomm_positions_short_all":"50000","comm_positions_long_all":"100000","comm_positions_short_all":"250000","open_interest_all":"500000"} for i in range(120)]
    if 'llama.fi/v2/historicalChainTvl' in url: return [{"date":str(int(datetime(2022,1,1).timestamp())+i*86400),"tvl":1e11+i} for i in range(100)]
    if 'coinbase.com' in url:
        a=datetime.strptime(url.split('start=')[1][:10],"%Y-%m-%d"); return [[int(datetime(a.year,a.month,a.day,tzinfo=timezone.utc).timestamp())+i*86400,1,2,0.5,100+i,3] for i in range(30)]
    if 'stlouisfed.org/fred/release/dates' in url:
        rid=int(url.split('release_id=')[1].split('&')[0]); return {"release_dates":[{"release_id":rid,"date":f"{y}-{m:02d}-{12 if rid==10 else 5}"} for y in range(2018,2027) for m in range(1,13)]}
    if 'stlouisfed.org' in url: return {"observations":[{"date":"2022-01-03","value":"1.5"}]}
    raise RuntimeError("HTTP 404")
bc.time.sleep=lambda *_:None
db=bc.DB(os.path.join(tempfile.mkdtemp(),'n.db'))
for k in list(os.environ):
    if k.endswith('_API_KEY'): del os.environ[k]
os.environ['FRED_API_KEY']='FREDSECRETKEY'
only={'commodities','klines_eth','klines_paxg','cot','defi_tvl','coinbase_daily','events','fx','fng','fred'}
t=time.time(); rep=bc.run('backfill',db,get=get,now=now,only=only); dt=time.time()-t
for k,v in rep.items(): print(f'  {k:16}{v}')
assert all(v.startswith('OK') for v in rep.values()), rep
print('równoległość: max jednoczesnych zapytań =',active['max'],'czas %.1fs'%dt); assert active['max']>=3
cnt=lambda q:db.con.execute(q).fetchone()[0]
assert cnt("select count(distinct symbol) from candles")>=17, cnt("select count(distinct symbol) from candles")   # surowce+ETF+indeksy+ETH+PAXG
assert cnt("select count(*) from events where kind='cpi'")>50 and cnt("select count(*) from events where kind='opex_m'")>50 and cnt("select count(*) from events where kind='halving'")==4
assert cnt("select count(*) from obs where metric='cot_gold_noncomm_net'")==120
# przyrostowo: 2. przebieg 'update' pobiera tylko okno d1
rep2=bc.run('update',db,get=get,now=now,only={'commodities'}); print(rep2); 
db.con.execute("delete from runs"); db.con.commit(); rep2=bc.run('update',db,get=get,now=now,only={'commodities'}); assert rep2['commodities'].startswith('OK') and int(rep2['commodities'].split()[1])<=19*5, rep2
assert 'FREDSECRETKEY' not in ' '.join(str(r[0]) for r in db.con.execute("select error from runs"))
print('kolektor ok')

# ---- 3) analiza złota i surowców
btc=[(int(d.replace(tzinfo=timezone.utc).timestamp()),100+i*0.1,0,0,100+i*0.1,1) for i,d in enumerate(days)]
db.put_candles('BTCUSDT','1d',btc,'t'); db.put_obs('fx_USDPLN',[(int(d.replace(tzinfo=timezone.utc).timestamp()),4.0) for d in days],'t')
out=bm.analyze_commodities(db.con,(sorted(x[0] for x in btc),[x[4] for x in btc]))
g=out['gold']; print('złoto: ',{k:(round(v,3) if isinstance(v,float) else '…') for k,v in list(g.items())[:6]}); print([ (r['label'],r['tone']) for r in g['readings']])
assert any(r['key']=='XAUUSD' for r in out['assets']) and g['gold_pln'] and g['gold_silver_ratio'] and g['cot_noncomm_net'] and g['readings']
assert 'btc_in_gold_oz' in g
print('złoto ok')

# ---- 4) live
csv="Symbol,Date,Time,Open,High,Low,Close,Volume\nXAUUSD,2026-11-03,12:00:00,2400,2410,2390,2405.5,0\nEURUSD,2026-11-03,12:00:00,1.08,1.09,1.07,1.085,0\nUSDPLN,2026-11-03,12:00:00,4.0,4.1,3.9,4.02,0\n^SPX,2026-11-03,12:00:00,5000,5010,4990,5005,0\n"
q=bl.parse_stooq(csv); assert q['XAUUSD']['price']==2405.5 and q['SPX']['price']==5005 and abs(q['XAUUSD']['chg_open']-0.2292)<0.01, q
def lget(url,as_text=False,timeout=0,retries=0):
    if 'bybit' in url: return {"result":{"list":[{"symbol":"BTCUSDT","lastPrice":"100000","price24hPcnt":"0.012"},{"symbol":"XAUTUSDT","lastPrice":"2400.1","price24hPcnt":"-0.002"},{"symbol":"FOOUSDT","lastPrice":"1"}]}}
    return csv
snap=bl.snapshot(lget); assert snap['quotes']['BTCUSDT']['price']==100000 and 'XAUTUSDT' in snap['quotes'] and 'FOOUSDT' not in snap['quotes'] and snap['quotes']['XAUUSD']
snap2=bl.snapshot(lambda *a,**k:(_ for _ in ()).throw(RuntimeError('HTTP 500')),prev=snap,do_stooq=True); assert snap2['quotes']['BTCUSDT'] and len(snap2['errors'])>=1   # zachowuje ostatnie notowania
p=os.path.join(tempfile.mkdtemp(),'live.json'); bl.write(p,snap); assert json.load(open(p))['quotes']['EURUSD']
print('live ok')
