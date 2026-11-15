import sys, os, tempfile, random, math, time, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc, btc_situations as bs
from datetime import datetime, timezone, timedelta
random.seed(21)
N=3200; t0=int(datetime(2016,1,1,tzinfo=timezone.utc).timestamp()); DAY=86400
dates=[t0+i*DAY for i in range(N)]
# DXY: błądzenie losowe z trendami
dxy=[100.0]
for i in range(1,N): dxy.append(dxy[-1]*(1+random.gauss(0,0.004)+0.0004*math.sin(i/60)))
chg=[None]*20+[dxy[i]/dxy[i-20]-1 for i in range(20,N)]
cross=[i for i in range(21,N) if chg[i-1] is not None and chg[i-1]<=0.02<chg[i]]
spaced=[]; last=-99
for i in cross:
    if i-last>=5: spaced.append(i); last=i
print('DXY krzyżowań:',len(spaced))
# CPI: co ~30 dni
cpi=[t0+d*DAY for d in range(40,N-10,30)]; cpi_idx={ (t-t0)//DAY for t in cpi}
def gen(planted):
    p=1000.0; out=[]; shock={}
    for i in spaced:
        for k in range(1,6): shock[i+k]=shock.get(i+k,0)-0.004
    for i in range(N):
        r=random.gauss(0.0005,0.02)
        if planted:
            r+=shock.get(i,0)
            if i in cpi_idx: r=random.gauss(0.008,0.06)       # 3x zmienność i dodatni dryf w dniu CPI
        p*=1+r; out.append(p)
    return out
btc=gen(True); eth=gen(False)
db=bc.DB(os.path.join(tempfile.mkdtemp(),'s.db'))
mk=lambda cl:[(dates[i],cl[i],cl[i],cl[i],cl[i],1) for i in range(N)]
db.put_candles('BTCUSDT','1d',mk(btc),'t'); db.put_candles('ETHUSDT','1d',mk(eth),'t')
# bliskość progu: ostatnia zmiana DXY 20d = 1.8%
dxy[-1]=dxy[-21]*1.018; dxy[-2]=min(dxy[-2],dxy[-22]*1.015)
db.put_candles('DXY','1d',mk(dxy),'t')
now_ts=dates[-1]
db.put_events('cpi',[(t,'CPI') for t in cpi]+[(now_ts+3*DAY,'CPI')])
t=time.time(); rep=bs.analyze(db.con,now_ts); print('analiza %.1fs'%(time.time()-t), 'testów',rep['tests'],'istotnych',rep['significant'])
b=rep['assets']['BTC']
for s in b['situations'][:6]: print(' ',s['id'],s['h'],s.get('metric',''),'n',s['n'],'t %.1f'%s['t'],'q %.4f'%s['q'],s['direction'])
dx=[s for s in b['situations'] if s['id']=='dxy_up' and s['h']==5]
assert dx and dx[0]['direction']=='down' and dx[0]['q']<0.1, dx
cp=[s for s in b['situations'] if s['id']=='ev_cpi' and s['h']==0 and s.get('metric')=='vol']
assert cp and cp[0]['abs_lift']>2 and cp[0]['q']<0.05, cp
print('CPI zmienność x%.1f q=%.4f'%(cp[0]['abs_lift'],cp[0]['q']))
now={n['id']:n for n in b['now']}; print('teraz:',{k:(v['status'],v['detail'][:60]) for k,v in now.items()})
assert now.get('ev_cpi',{}).get('status')=='event_soon' and now['ev_cpi']['days_to']==3 and now['ev_cpi'].get('abs_lift',0)>2
assert now.get('dxy_up',{}).get('status') in ('approaching','active'), now.keys()
e=rep['assets']['ETH']; fals=[s for s in e['situations'] if s['q']<0.10]; print('ETH fałszywe q<0.10:',[(s['id'],s['h']) for s in fals])
assert len(fals)<=2
assert rep['upcoming_events'] and rep['upcoming_events'][0]['days']<=3
json.dumps(rep,default=float); print('situations ok')
