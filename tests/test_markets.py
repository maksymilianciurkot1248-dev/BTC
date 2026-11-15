import sys, os, tempfile, random, math, time, json, sqlite3
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc, btc_markets as bm
from datetime import datetime, timezone, timedelta
now=datetime(2026,11,3,12,tzinfo=timezone.utc)
def bdays(a,b):
    d=a; out=[]
    while d<=b:
        if d.weekday()<5: out.append(d)
        d+=timedelta(days=1)
    return out
days=bdays(datetime(2019,1,1),datetime(2026,11,2))

def make_world(momentum, seed):
    rnd=random.Random(seed); world={}
    for sym,mkt,name,sec in bm.DEFAULT_WATCHLIST:
        p=100.0; mu=0.0; rows=[]
        for d in days:
            mu = 0.995*mu + (rnd.gauss(0,0.0006) if momentum else 0.0)       # trwały dryf (momentum)
            r = mu + rnd.gauss(0,0.018)
            o=p; p*=1+r
            rows.append((d.strftime("%Y-%m-%d"),o,max(o,p)*1.01,min(o,p)*0.99,p,int(1e6*(1+rnd.random()))))
        world[bm.stooq_code(sym,mkt)]=rows
    return world

def mock(world, seed=1):
    rnd=random.Random(seed)
    def get(url, as_text=False, timeout=0):
        if 'frankfurter' in url:
            a,b=url.split('v1/')[1].split('?')[0].split('..'); a=datetime.strptime(a,"%Y-%m-%d"); b=datetime.strptime(b,"%Y-%m-%d")
            rates={}
            for d in bdays(a,b):
                k=(d-datetime(2015,1,1)).days
                rates[d.strftime("%Y-%m-%d")]={c:(1+0.1*math.sin(k/200+i))*(4 if c=="PLN" else 1) for i,c in enumerate(bm.FX_CCY)}
            return {"rates":rates}
        if 'stooq.com' in url:
            code=url.split('s=')[1].split('&')[0]
            if code in world:
                return "Date,Open,High,Low,Close,Volume\n"+"\n".join(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}" for r in world[code])
            if code in ('^spx','wig20'):
                p=3000; rows=["Date,Open,High,Low,Close,Volume"]
                for d in days: p*=1+rnd.gauss(0.0003,0.01); rows.append(f"{d:%Y-%m-%d},{p},{p},{p},{p},0")
                return "\n".join(rows)
            return "Przekroczony limit"
        if 'finnhub.io/api/v1/stock/metric' in url:
            return {"metric":{"revenueGrowthTTMYoy":rnd.uniform(-5,60),"epsGrowthTTMYoy":rnd.uniform(-20,80),"netProfitMarginTTM":rnd.uniform(-5,35),"roeTTM":rnd.uniform(-5,50),"peTTM":rnd.uniform(10,80),"totalDebt/totalEquityQuarterly":rnd.uniform(0,3)}}
        if 'recommendation' in url: return [{"strongBuy":10,"buy":15,"hold":8,"sell":1,"strongSell":0}]
        raise RuntimeError("HTTP 404")
    return get

def build(momentum, seed):
    db=bc.DB(os.path.join(tempfile.mkdtemp(),'m.db'))
    rnd=random.Random(seed+99); p=10000
    rows=[]
    for i,d in enumerate(days):
        p*=1+rnd.gauss(0.0005,0.03); rows.append((int(d.replace(tzinfo=timezone.utc).timestamp()),p,p,p,p,1))
    db.put_candles('BTCUSDT','1d',rows,'t')
    os.environ['FINNHUB_API_KEY']='FINNKEY12345'
    rep=bc.run('backfill',db,get=mock(make_world(momentum,seed)),now=now,only={'fx','stocks','fundamentals'},sleep=False)
    return db,rep
import time as _t
bc.time.sleep=lambda *_:None      # bez realnych pauz w teście
t=_t.time(); db,rep=build(True,3); print('zbieranie',rep,'%.1fs'%(_t.time()-t))
assert rep['fx'].startswith('OK') and rep['stocks'].startswith('OK') and rep['fundamentals'].startswith('OK'), rep
t=_t.time(); out=bm.analyze(db.con); print('analiza %.1fs'%(_t.time()-t))
fx=out['fx']; assert len(fx['currencies'])==len(bm.FX_CCY) and fx['usd_index'] and fx['pln_cross'][0]['ccy']=='USD'
print('FX silne 30d:',fx['strongest_30d'],'słabe:',fx['weakest_30d'],'USD idx:',fx['usd_index']['trend'], 'corr', fx['usd_index']['corr_btc_90d'])
st=out['stocks']; print('spółki',st['universe'],st['ranked'],'z fundamentami',st['with_fundamentals'],'pominięte',st['missing'][:3])
assert st['ranked']>=25 and st['with_fundamentals']>=15
top=st['ranking'][:5]; [print(' ',r['symbol'],round(r['score'],1),r['flags'],r['fund_score'] and round(r['fund_score'])) for r in top]
assert all(0<=r['score']<=100 for r in st['ranking']) and st['ranking'][0]['score']>=st['ranking'][-1]['score']
bt=st['backtest']; print('backtest z momentum:',json.dumps({k:{a:round(b,2) for a,b in v.items()} for k,v in bt.items()}))
assert bt['91 dni']['excess_mean_pct']>0 and bt['91 dni']['t_stat']>2, bt
db2,rep2=build(False,4); st2=bm.analyze(db2.con)['stocks']; bt2=st2['backtest']
print('backtest szum:',json.dumps({k:{a:round(b,2) for a,b in v.items()} for k,v in bt2.items()}))
assert abs(bt2['91 dni']['t_stat'])<3, bt2
# klucz Finnhub nie może wyciec do dziennika
err=' '.join(r[0] or '' for r in db.con.execute("select error from runs")); assert 'FINNKEY12345' not in err
# brak danych Stooq dla części spółek -> pominięte, reszta działa
w=make_world(True,3); del w['cdr.pl']; del w['nvda.us']
db3=bc.DB(os.path.join(tempfile.mkdtemp(),'p.db'))
os.environ.pop('FINNHUB_API_KEY'); r3=bc.run('backfill',db3,get=mock(w),now=now,only={'stocks'},sleep=False); print(r3)
st3=bm.analyze(db3.con)['stocks']; assert 'NVDA.US' in ' '.join(st3['missing']) and st3['with_fundamentals']==0 and st3['ranked']>=25
json.dump(out,open(__import__('os').path.dirname(__import__('os').path.abspath(__file__))+'/markets_sample.json','w'),default=float)
print('markets ok')
