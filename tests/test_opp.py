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
# --- analogi
st=eng.data['XAUUSD']['stats']; print('XAU analogi:',{k:round(v,3) if isinstance(v,float) else v for k,v in st['setup'].items() if k in('n','p_tp1','p_stop','avg_r')},'tło p_tp1',round(st['base']['p_tp1'],3))
assert st['setup']['n']>=12 and st['setup']['p_tp1']>st['base']['p_tp1']+0.1, st['setup']
stf=eng.data['EURUSD']['stats']['setup']; print('EURUSD (bez zaplanowanego odbicia):',stf and round(stf['p_tp1'],3))
# --- skan
scan=eng.scan(); [print(f"  {x['status']:32}{x['key']:8} wynik {x['score']:5.1f} RSI {x['rsi'] and round(x['rsi'])} stab {x['stab']} depth {x['depth_atr'] and round(x['depth_atr'],1)} p_tp1 {x['p_tp1'] and round(x['p_tp1'],2)}") for x in scan]
by={x['key']:x for x in scan}
assert by['ETHUSDT']['status'] in ('spadający nóż','za wcześnie (brak stabilizacji)') and by['ETHUSDT']['score']<=45, by['ETHUSDT']
assert by['XAUUSD']['status'] in ('okazja','obserwuj') and by['XAUUSD']['in_dip'], by['XAUUSD']
assert by['EURUSD']['status']=='brak dołka' or by['EURUSD']['score']<=45
# --- raport
rep=eng.report('XAUUSD',capital=10000,risk_pct=1)
pl=rep['plan_full']; print('plan:',{k:round(pl[k],3) for k in('entry_lo','entry_hi','stop','tp1','tp2','tp3','rr2')})
assert pl['stop']<pl['entry_lo']<=pl['entry_hi']<pl['tp1']<pl['tp2']<=pl['tp3']
assert abs((pl['tp1']-pl['entry_hi'])/(pl['entry_hi']-pl['stop'])-1.5)<1e-9
sz=rep['sizing']; assert abs(sz['loss_at_stop']-100)<1e-6 and abs(sz['qty']*(pl['entry_hi']-pl['stop'])-100)<1e-6
tm=rep['timing']; print('czas:',tm['to_tp1'],'wejście:',[(t['sessions'],round(t['prob'],2)) for t in tm['wait_for_entry']])
assert tm['wait_for_entry'][0]['prob']<=tm['wait_for_entry'][-1]['prob']
print(' ',rep['verdict']['headline']); [print('   -',x) for x in rep['reasons']]
assert rep['scenarios'] and rep['adjustments'] and rep['context']['events'] is not None
# --- nakładka na żywo: nowe minimum
eng.set_live({'XAUUSD':{'price':by['XAUUSD']['price']*0.93,'src':'t'}},ts=now)
x2=eng.evaluate('XAUUSD'); print('po spadku o 7% na żywo: depth',round(x2['depth_atr'],1),'(było',round(by['XAUUSD']['depth_atr'],1),') status',x2['status'])
assert x2['depth_atr']>by['XAUUSD']['depth_atr']
eng.set_live({})
# --- automat rozliczeń okazji
def S_(bars): return {'d':[T0+i*DAY for i in range(len(bars))],'o':[b[0] for b in bars],'h':[b[1] for b in bars],'l':[b[2] for b in bars],'c':[b[3] for b in bars]}
mk=lambda *b: S_(list(b))  # (o,h,l,c)
ts0=T0+0*DAY
B=lambda o,h,l,c:(o,h,l,c)
base=[B(100,100,100,100)]
def run(bars, **kw): return bo.eval_signal(S_(base+bars),ts0,kw.get('eh',100),kw.get('stop',95),kw.get('tp',107.5),20,kw.get('ft'),kw.get('fp'))
r=run([B(100,101,99,100),B(100,108,99.5,107)]); assert r['outcome']=='tp1' and abs(r['r']-1.5)<1e-9, r
r=run([B(100,101,99,100),B(100,101,94,95)]); assert r['outcome']=='stop' and r['r']==-1.0, r
r=run([B(100,108,94,100)]); assert r['outcome']=='stop', r                  # remis w jednej świecy = stop
r=run([B(103,104,102,103)]*6); assert r['outcome']=='missed', r
r=run([B(101,102,100,101)]*3); assert r['status']=='closed' or r['status']=='filled'
r=run([B(100,101,99.5,100)]+[B(100,101,99,100)]*21); assert r['outcome']=='timeout' and abs(r['r'])<0.5, r
rs=bo.eval_signal(S_(base+[B(99,100,98,99)]),ts0,100,95,107.5,20,None,None); assert rs['status']=='filled' and rs['fill_price']==99
rec=bo.recovery(S_(base+[B(100,100,94,95)]+[B(95,99,94,98)]*3+[B(98,108,97,107)]),1,107.5,20); assert rec==1
print('automat rozliczeń ok')
# --- uczenie się: wiele wybitych stopów, po których cena i tak sięgnęła celu
for k in range(12):
    db.con.execute("INSERT INTO signals(ts,symbol,cls,source,entry_hi,stop,tp1,status,outcome,r_mult,recovered,p_model,score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(now-30*DAY,'XAUUSD','commodity','live',100,95,107.5,'closed','stop' if k<9 else 'tp1',-1.0 if k<9 else 1.5,1 if k<9 else None,0.5,70))
db.con.commit()
notes=eng.adapt(); print('korekty:',notes); assert eng.params['stop_mult']['commodity']>1.0 and any('poszerz' in n for n in notes) and any('próg' in n for n in notes)
cal=bo.calibrate(0.5,30,{'n':12,'wins':3},20); assert abs(cal-(3+20*0.5)/(12+20))<1e-9; print('kalibracja p: 0.50 ->',round(cal,3))
rep2=eng.report('XAUUSD'); assert any('×' in a or 'skorygowana' in a for a in rep2['adjustments']), rep2['adjustments']
perf=eng.performance(); print('skuteczność live:',perf['live'].get('Surowce')); assert perf['live']['Surowce']['n']==12
# --- pozycje
pid=eng.add_position('BTCUSDT',100,95); adv=lambda price:(eng.set_live({'BTCUSDT':{'price':price}},ts=now),eng.positions()[0])[1]
a=adv(108); print('pozycja +8:',a['status'],a['actions'][0][:60]); assert a['status']=='CEL 1' and a['suggested_stop']>=100
a=adv(106); assert a['r_now']>=1 and a['suggested_stop']==100 and a['status']=='trzymaj'
a=adv(94); assert a['status']=='STOP'
a=adv(120); assert a['status']=='CEL 2'
for bad in (dict(entry=-1,stop=1),dict(entry=100,stop=100),dict(entry=100,stop=-5)):
    try: eng.add_position('BTCUSDT',**bad); assert 0
    except ValueError: pass
try: eng.add_position('FAKE',100,95); assert 0
except KeyError: pass
eng.close_position(pid); assert not eng.positions()
# --- replay
n=bo.replay(eng); pf=eng.performance()['replay']; print('replay:',n,'okazji',{k:(v if k=='_open' else {a:(round(b,3) if isinstance(b,float) else b) for a,b in v.items()}) for k,v in pf.items()})
assert n>0
print('opportunities ok')
