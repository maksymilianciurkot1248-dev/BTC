import sys, os, tempfile, random, math, time, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_collect as bc, btc_research as br
from datetime import datetime, timezone
random.seed(11)
N=2600; t0=int(datetime(2018,1,1,tzinfo=timezone.utc).timestamp())
db=bc.DB(os.path.join(tempfile.mkdtemp(),'r.db'))
s=0.0; price=10000.0; closes=[]; good=[]
for i in range(N):
    s=0.95*s+random.gauss(0,0.3)
    good.append(s+random.gauss(0,0.5))              # obserwowalny wskaźnik z szumem
    r=0.0003+0.004*s+random.gauss(0,0.025)          # s_t przewiduje zwrot dnia t+1 i dalsze (persistencja)
    price*=1+r; closes.append(price)
db.put_candles('BTCUSDT','1d',[(t0+i*86400,closes[i],closes[i],closes[i],closes[i],1) for i in range(N)],'test')
# poprawna zależność: wartość 'good' z dnia i oparta na s_i (znanym na koniec dnia i), zwrot idzie w kolejne dni
db.put_obs('good_signal',[(t0+i*86400,good[i]+50) for i in range(N)],'t')
for k in range(25):                                   # 25 szumów (AR1)
    x=0; rows=[]
    for i in range(N):
        x=0.9*x+random.gauss(0,1); rows.append((t0+i*86400,x+100))
    db.put_obs(f'noise_{k}',rows,'t')
# metryka śróddzienna (hourly snapshots) i krótka historia
db.put_obs('snap_short',[(t0+(N-60)*86400+i*3600,i) for i in range(1400)],'t')
t=time.time(); con=db.con
rep=br.analyze(con); print('czas analizy %.1fs'%(time.time()-t))
print(br.to_text(rep))
top=[r for r in rep['ic'] if r['q']<0.10]
names={r['feature'].split('__')[0] for r in top}
print('istotne:',sorted(names))
assert 'good_signal' in names, "nie wykryto prawdziwego sygnału"
false=[n for n in names if n.startswith('noise_')]
print('fałszywe odkrycia:',false)
assert len(false)<=3, false      # 25 szumów × cechy: korekta FDR ma je ograniczyć
assert rep['walkforward'] and rep['walkforward']['permutation_p']<0.2, rep['walkforward']
assert rep['regimes'] and rep['seasonality']['dow'] and rep['today']
json.dumps(rep,default=float)
# test czystego szumu: nic nie powinno być istotne
db2=bc.DB(os.path.join(tempfile.mkdtemp(),'n.db')); random.seed(5); p=10000; cl=[]
for i in range(N): p*=1+random.gauss(0.0003,0.025); cl.append(p)
db2.put_candles('BTCUSDT','1d',[(t0+i*86400,cl[i],cl[i],cl[i],cl[i],1) for i in range(N)],'t')
for k in range(25):
    x=0; rows=[]
    for i in range(N): x=0.9*x+random.gauss(0,1); rows.append((t0+i*86400,x+100))
    db2.put_obs(f'noise_{k}',rows,'t')
r2=br.analyze(db2.con); sig=[r for r in r2['ic'] if r['q']<0.10]
print('czysty szum: istotnych po FDR =',len(sig),'z',r2['tests'],'testów'); assert len(sig)<=2
wf=r2['walkforward']; print('walk-forward na szumie: p_perm=',wf and round(wf['permutation_p'],2), 'sharpe', wf and wf['strategy']['sharpe'], 'vs hold', wf and wf['buy_hold']['sharpe'])
print('research ok')
