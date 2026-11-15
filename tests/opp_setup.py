import sys, os, tempfile, random, math, time, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc, btc_opportunities as bo
from datetime import datetime, timezone, timedelta
DAY=86400; N=1800; T0=int(datetime(2019,1,1,tzinfo=timezone.utc).timestamp())
rnd=random.Random(3)
def gen(revert, trend=0.0004, seed=1, knife_end=False, dip_end=False):
    r=random.Random(seed); c=[100.0]; rows=[]; boost=0
    for i in range(1,N):
        mx=max(c[-60:]); dd=c[-1]/mx-1
        ret=r.gauss(trend,0.012)
        if revert and dd<-0.10 and boost==0: boost=10
        if boost>0: ret+=0.012; boost-=1
        c.append(c[-1]*(1+ret))
    if dip_end:                       # świeży dołek ze stabilizacją na końcu serii
        base=c[-16]; 
        for k in range(1,13): c[-16+k]=base*(1-0.011*k)
        c[-3]=c[-4]*0.997; c[-2]=c[-3]*1.012; c[-1]=c[-2]*1.006
    if knife_end:                     # ciągły spadek bez stabilizacji pod spadającą SMA200
        for k in range(1,60): c[-60+k]=c[-60]*(1-0.012*k)
    for i,p in enumerate(c):
        o=c[i-1] if i else p; u=r.random()*0.008
        rows.append((T0+i*DAY,o,max(o,p)*(1+u),min(o,p)*(1-u),p,1000))
    return rows
db=bc.DB(os.path.join(tempfile.mkdtemp(),'o.db'))
db.put_candles('XAUUSD','1d',gen(True,seed=1,dip_end=True),'t')
db.put_candles('EURUSD','1d',gen(False,seed=2),'t')
db.put_candles('BTCUSDT','1d',gen(True,trend=0.0006,seed=4,dip_end=True),'t')
db.put_candles('ETHUSDT','1d',gen(False,trend=-0.0008,seed=5,knife_end=True),'t')
db.put_candles('WTI','1d',gen(True,seed=6),'t'); db.put_candles('XAGUSD','1d',gen(True,seed=7),'t')
now=T0+N*DAY+3600
eng=bo.Engine(db.con.execute("PRAGMA database_list").fetchone()[2], now_fn=lambda:now); eng.load()
print('aktywa w skanerze:',sorted(eng.data))
