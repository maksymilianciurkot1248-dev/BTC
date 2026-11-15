import sys, os, tempfile, random, math, time, json, sqlite3, shutil
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc, btc_strategies as bs
from datetime import datetime, timezone, timedelta
DAY=86400
def bdays(a,n):
    out=[];d=a
    while len(out)<n:
        if d.weekday()<5: out.append(d)
        d+=timedelta(days=1)
    return out
days=bdays(datetime(2018,1,1,tzinfo=timezone.utc),1700)
specs={'BTCUSDT':(0.5,0.75),'ETHUSDT':(0.5,0.85),'SOLUSDT':(0.6,1.0),'XAUUSD':(0.07,0.14),'TLT':(0.02,0.14),'SPX':(0.10,0.17),'NDX':(0.14,0.21),'NVDA.US':(0.35,0.45),'MSFT.US':(0.18,0.25),'AAPL.US':(0.2,0.27),'XAGUSD':(0.05,0.28),'WTI':(0.03,0.35)}
def make(path,cut=None):
    db=bc.DB(path); r=random.Random(5)
    for k,(mu,sig) in specs.items():
        p=100.0; rows=[]; regime=0
        for i,d in enumerate(days):
            if i%250==0: regime=r.gauss(0,0.25)       # zmienne reżimy dryfu
            p*=math.exp((mu+regime*(1 if sig>0.3 else 0.3))/252-0.5*(sig/math.sqrt(252))**2+sig/math.sqrt(252)*r.gauss(0,1))
            rows.append((int(d.timestamp()),p,p*1.01,p*0.99,p,1))
        if cut: rows=[x for x in rows if x[0]<=cut]
        db.put_candles(k,'1d',rows,'t')
    db.put_obs('fred_DFF',[(int(d.timestamp()),2.0) for d in days if not cut or int(d.timestamp())<=cut],'t')
    return db
d1=tempfile.mkdtemp(); db=make(d1+'/a.db')
t=time.time(); rep=bs.analyze(db.con); print('analiza %.1fs'%(time.time()-t))
P={p['id']:p for p in rep['profiles']}
print('%-11s %7s %7s %7s %7s %7s %6s'%('profil','CAGR','vol','Sharpe','maxDD','mies+','trzyma'))
for p in rep['profiles']:
    s=p['stats']; print('%-11s %6.1f%% %6.1f%% %7.2f %6.1f%% %6.0f%% %6.1f'%(p['name'],s['cagr']*100,s['vol']*100,s['sharpe'],s['max_dd']*100,s['pos_months']*100,p['avg_holdings']))
for k,v in rep['benchmarks'].items(): s=v['stats']; print('%-28s %6.1f%% %6.1f%% %7.2f %6.1f%%'%(k,s['cagr']*100,s['vol']*100,s['sharpe'],s['max_dd']*100))
# --- porządek ryzyka
vols=[P[k]['stats']['vol'] for k in ('protect','safe','balanced','growth','aggressive')]
assert all(a<b+0.005 for a,b in zip(vols,vols[1:])) and vols[0]<0.6*vols[-1], vols
dds=[P[k]['stats']['max_dd'] for k in ('protect','safe','balanced','growth','aggressive')]
assert dds[0]>dds[-1], dds
# --- trafienie w docelową zmienność (bez dźwigni: realizowana nie przekracza docelowej ponad miarę)
for k in ('protect','safe','balanced'):
    tv=bs.PROFILES[k]['target_vol']; rv=P[k]['stats']['vol']; assert 0.4*tv<rv<1.8*tv,(k,tv,rv)
# --- limity wag i klas
for p in rep['profiles']:
    prof=bs.PROFILES[p['id']]; cur=p['current']; tot=sum(w['weight'] for w in cur['weights'])
    assert tot<=1-prof['min_cash']+1e-6 and abs(tot+cur['cash']-1)<1e-6,(p['id'],tot)
    assert all(w['weight']<=prof['max_w']+1e-6 for w in cur['weights']),p['id']
    byc={}
    for w in cur['weights']: byc[w['cls']]=byc.get(w['cls'],0)+w['weight']
    for c,v in byc.items(): assert v<=prof['caps'][c]+1e-6,(p['id'],c,v)
    assert len(cur['weights'])<=prof['max_assets']
assert not any(w['cls']=='crypto' for w in P['protect']['current']['weights'])
# --- statystyki spójne
for p in rep['profiles']:
    s=p['stats']; assert s['max_dd']<=0 and -1<s['worst_month']<=s['best_month'] and s['var95_month']<=0 and s['cvar95_month']<=s['var95_month']+1e-12
    assert p['montecarlo']['p5']<=p['montecarlo']['median']<=p['montecarlo']['p95'] and len(p['montecarlo']['fan'])==12 and s['sharpe_ci'][0]<s['sharpe_ci'][1]
    assert abs(s['years'][max(s['years'])])<5 and len(p['equity'])>200
# --- brak podglądania przyszłości: skrócone dane = identyczna przeszłość
cut=int(days[1200].timestamp()); db2=make(d1+'/b.db',cut=cut)
uni1=bs.universe(db.con); days1,px1,rf1=bs.load_prices(db.con,uni1); uni2=bs.universe(db2.con); days2,px2,rf2=bs.load_prices(db2.con,uni2)
st=300
for pid in ('safe','aggressive'):
    b1=bs.backtest(days1,px1,rf1,uni1,bs.PROFILES[pid],start_idx=st,last=len(days2)-2); b2=bs.backtest(days2,px2,rf2,uni2,bs.PROFILES[pid],start_idx=st,last=len(days2)-2)
    assert all(abs(x-y)<1e-12 for x,y in zip(b1['equity'],b2['equity'])) and len(b1['equity'])==len(b2['equity']),pid
print('brak podglądania przyszłości: OK')
json.dump(rep,open(__import__('os').path.dirname(__import__('os').path.abspath(__file__))+'/strategies_sample.json','w'),default=float); print('strategies ok, rozmiar JSON',len(json.dumps(rep,default=float))//1024,'KB')
