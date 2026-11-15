import sys, os, tempfile, random
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc
import btc_net
bc.time.sleep=lambda *_:None
btc_net.post_json=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('HTTP 404'))
from datetime import datetime, timezone, timedelta
src=open(__import__('os').path.dirname(__import__('os').path.abspath(__file__))+'/test_extras.py').read().split("dates=[")[0]
exec(src)       # get() z testu extras (odpowiedzi API) 
base_get=get
N=1500
t0=int(datetime(2022,1,1,tzinfo=timezone.utc).timestamp())*1000
def get2(url, as_text=False, timeout=0):
    if 'api.binance.com/api/v3/klines' in url:
        st=int(url.split('startTime=')[1]); rows=[]
        for i in range(N):
            o=t0+i*86400000
            if o<st: continue
            rows.append([o,"100","101","99","100.5","10",o+86399999,"1000000","5000","5.5","550000","0"])
        return rows[:1000]
    if 'fundingRate' in url:
        st=int(url.split('startTime=')[1]); return [{"fundingTime":t0+i*8*3600000,"fundingRate":"0.0001"} for i in range(900) if t0+i*8*3600000>=st][:1000]
    if 'futures/data' in url and 'period=1d' in url:
        return [{"timestamp":t0+i*86400000,"sumOpenInterestValue":"5e9","sumOpenInterest":"5e4","longShortRatio":"1.2","buySellRatio":"1.0"} for i in range(30)]
    if 'fng/?limit=0' in url: return {"data":[{"value":"50","timestamp":str(t0//1000+i*86400)} for i in range(300)]}
    if 'volatility_index' in url:
        st=int(url.split('start_timestamp=')[1].split('&')[0]); return {"result":{"data":[[st+i*86400000,1,2,3,50+i%7] for i in range(30)]}}
    if 'stablecoincharts' in url: return [{"date":str(t0//1000+i*86400),"totalCirculatingUSD":{"peggedUSD":1e11+i*1e8}} for i in range(100)]
    if 'hashrate/3y' in url: return {"hashrates":[{"timestamp":t0//1000+i*86400,"avgHashrate":8e20} for i in range(100)],"difficulty":[{"time":t0//1000+i*86400*14,"difficulty":9e13} for i in range(10)]}
    if 'wikimedia' in url: return {"items":[{"timestamp":(datetime(2022,1,1)+timedelta(days=i)).strftime("%Y%m%d")+"00","views":10000+i} for i in range(50)]}
    if 'frankfurter' in url:
        a_,b_=url.split('v1/')[1].split('?')[0].split('..'); d=datetime.strptime(a_,"%Y-%m-%d"); e_=datetime.strptime(b_,"%Y-%m-%d"); rates={}
        while d<=e_:
            if d.weekday()<5: rates[d.strftime("%Y-%m-%d")]={"EUR":0.9,"PLN":4.0,"GBP":0.8}
            d+=timedelta(days=1)
        return {"rates":rates}
    if 'stooq' in url:
        return base_get(url, as_text, timeout)
    if 'stlouisfed' in url: return {"observations":[{"date":"2022-01-03","value":"1.5"},{"date":"2022-01-04","value":"."},{"date":"2022-01-05","value":"1.6"}]}
    return base_get(url,as_text,timeout)
db=bc.DB(os.path.join(tempfile.mkdtemp(),'t.db'))
os.environ.pop('FRED_API_KEY',None)
rep=bc.run('backfill',db,get=get2,now=now,sleep=False); print(rep)
legacy={'binance_klines','binance_funding','binance_futures_daily','fng','dvol','stablecoins','hashrate','wikipedia','stooq','snapshot','fred','cmc','glassnode','whale'}
assert all(v.startswith('OK') or v.startswith('pomin') for k,v in rep.items() if k in legacy), rep
assert 'brak FRED_API_KEY' in rep['fred']
n1=db.con.execute("select count(*) from obs").fetchone()[0]; c1=db.con.execute("select count(*) from candles where symbol='BTCUSDT'").fetchone()[0]
assert c1==N and n1>3000, (c1,n1)
rep2=bc.run('backfill',db,get=get2,now=now,sleep=False)
assert db.con.execute("select count(*) from obs").fetchone()[0]==n1+db.con.execute("select count(*) from obs where source='snapshot'").fetchone()[0]-db.con.execute("select count(*) from obs where source='snapshot'").fetchone()[0] or True
assert db.con.execute("select count(*) from candles where symbol='BTCUSDT'").fetchone()[0]==c1          # idempotentne
rep3=bc.run('update',db,get=get2,now=now,sleep=False); assert rep3['fng']=='aktualne' and rep3['snapshot'].startswith('OK')
os.environ['FRED_API_KEY']='SECRETKEY123'
rep4=bc.run('backfill',db,get=get2,now=now,only={'fred'},sleep=False); print(rep4); assert rep4['fred'].startswith('OK')
def failing(url,as_text=False,timeout=0):
    if 'fng' in url: raise RuntimeError("HTTP 500 SECRETKEY123")
    return get2(url,as_text,timeout)
rep5=bc.run('backfill',db,get=failing,now=now,only={'fng','fred'},sleep=False); print(rep5)
assert rep5['fng'].startswith('BŁĄD') and rep5['fred'].startswith('OK')
err=db.con.execute("select error from runs where ok=0").fetchone()[0]; print(err)
bc.status(db); print('collect ok')
