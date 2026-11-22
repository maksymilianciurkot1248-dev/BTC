import sys, os, json, threading, time, urllib.request, urllib.error, tempfile
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
S=__import__('os').path.dirname(__import__('os').path.abspath(__file__))+''
exec(open(S+'/opp_setup.py').read())
import tempfile as _tf
_ext=_tf.mkdtemp(); os.environ['BTC_API_EXT']=_ext
open(os.path.join(_ext,'a_ok.py'),'w').write("GET={'/api/ext_test': lambda state,q: (200, {'ok': True, 'bot_dir': bool(state.bot_dir), 'q': q}), '/api/health': lambda s,q: (200, {'hack': 1}), 'zly': 1}\n")
open(os.path.join(_ext,'b_bad.py'),'w').write("raise RuntimeError('zepsute rozszerzenie')\n")
import btc_api
live=os.path.join(tempfile.mkdtemp(),'live.json')
def write_live(q):
    json.dump({"ts":now,"quotes":q},open(live+'.tmp','w')); os.replace(live+'.tmp',live)
write_live({"XAUUSD":{"price":1093.9,"src":"stooq"}})
botd=tempfile.mkdtemp(); srv,state,stop=btc_api.serve(eng,live,'127.0.0.1',0,bot_dir=botd); port=srv.server_address[1]
threading.Thread(target=srv.serve_forever,daemon=True).start()
def call(path,method='GET',body=None,headers=None,raw=None):
    h=dict(headers or {}); data=raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None: h.setdefault('Content-Type','application/json')
    req=urllib.request.Request(f'http://127.0.0.1:{port}{path}',data=data,headers=h,method=method)
    try:
        with urllib.request.urlopen(req,timeout=10) as r: return r.status,json.load(r)
    except urllib.error.HTTPError as e: return e.code,json.load(e)
code,h=call('/api/health'); assert code==200 and h['ok'] and h['assets']==6, h
code,sc=call('/api/scan'); assert code==200 and len(sc['items'])==6 and sc['items'][0]['status']=='okazja'; print('scan:',[(x['key'],x['status']) for x in sc['items'][:3]])
code,sc2=call('/api/scan?cls=crypto'); assert {x['cls'] for x in sc2['items']}=={'crypto'}
code,r=call('/api/report?symbol=XAUUSD&capital=10000&risk=1'); assert code==200 and r['sizing']['loss_at_stop']==100 and r['plan_full']['stop']<r['plan_full']['entry_lo'], r.get('error')
assert call('/api/report?symbol=NOPE')[0]==404
assert call('/api/report?symbol=XAUUSD&capital=abc')[0]==400 and call('/api/report?symbol=XAUUSD&risk=99')[0]==400 and call('/api/report?symbol=XAUUSD&capital=-5')[0]==400
assert call('/api/nic')[0]==404
# żywa cena zmienia raport
write_live({"XAUUSD":{"price":1200.0,"src":"stooq"}}); time.sleep(0.05); os.utime(live,(time.time()+2,time.time()+2))
code,r2=call('/api/report?symbol=XAUUSD'); assert abs(r2['price']-1200)<1e-6, r2['price']
# POST: ochrona
good={'symbol':'BTCUSDT','entry':100,'stop':95}
assert call('/api/track','POST',good)[0]==403                                              # brak X-Requested-With
assert call('/api/track','POST',raw=b'{"symbol":"BTCUSDT"}',headers={'X-Requested-With':'btc-ui','Content-Type':'text/plain'})[0]==400
assert call('/api/track','POST',raw=b'x'*5000,headers={'X-Requested-With':'btc-ui'})[0]==400
assert call('/api/track','POST',raw=b'not json',headers={'X-Requested-With':'btc-ui'})[0]==400
H={'X-Requested-With':'btc-ui'}
for bad in ({'symbol':'FAKE','entry':100,'stop':95},{'symbol':'BTCUSDT','entry':-1,'stop':1},{'symbol':'BTCUSDT','entry':100,'stop':100},{'symbol':'BTCUSDT','entry':'abc','stop':5},{'symbol':'BTCUSDT','entry':1e30,'stop':5},{'symbol':'BTCUSDT','entry':100,'stop':95,'qty':-3}):
    c,_=call('/api/track','POST',bad,H); assert c in (400,404), (bad,c)
code,res=call('/api/track','POST',dict(good,note='x'*500,qty=2),H); assert code==200; pid=res['id']
code,ps=call('/api/positions'); assert code==200 and ps['positions'][0]['key']=='BTCUSDT' and ps['positions'][0]['actions'] and len(ps['positions'][0]['note'])<=200
assert call('/api/untrack','POST',{'id':pid},H)[0]==200 and call('/api/positions')[1]['positions']==[]
code,pf=call('/api/performance'); assert code==200 and 'params' in pf
# równoległe zapytania
res=[]; ths=[threading.Thread(target=lambda:res.append(call('/api/report?symbol=BTCUSDT')[0])) for _ in range(16)]
t=time.time(); [x.start() for x in ths]; [x.join() for x in ths]; assert res.count(200)==16, res; print('16 równoległych raportów: %.2fs'%(time.time()-t))
# wpisy do dziennika z pętli
time.sleep(6)
n=eng._q("select count(*) from signals where source='live'",'fetchone')[0]; print('okazje w dzienniku (zapisane przez pętlę w tle):',n); assert n>=1
assert eng.log_signals(state.scan)==0   # nie dubluje w ciągu 5 dni
changed=eng.update_signals(); print('rozliczone:',changed)
# bot: status i ustawienia
code,b=call('/api/bot'); assert code==200 and b['status']=={} and b['bounds']['max_lev']['max']==2.0 and b['settings']=={} and b['stop_file'] is False
assert call('/api/bot/settings','POST',{'risk_pct':0.4})[0]==403
code,r=call('/api/bot/settings','POST',{'risk_pct':0.4,'directions':['long'],'paused':True},H); assert code==200 and r['settings']['risk_pct']==0.4
assert call('/api/bot')[1]['settings']['paused'] is True
for bad in ({'max_lev':3},{'risk_pct':-1},{'paused':'x'},{'api_key':'x'},{'directions':[]},{'max_orders_day':99}):
    assert call('/api/bot/settings','POST',bad,H)[0]==400, bad
assert call('/api/bot')[1]['settings']['risk_pct']==0.4
assert call('/api/catalog')[1]=={'symbols':[]} and call('/api/learn')[0]==200
_bh = __import__('btc_botcfg'); assert call('/api/learn')[1]['history'] == []
_bh.write_edge_history(botd, [{'ts': 1, 'strategy': 'plan', 'symbol': 'ETHUSDT', 'action': 'allow', 'n': 40, 'mean_r': 0.2, 'q': 0.01, 'reasons': []}])
assert call('/api/learn')[1]['history'][0]['action'] == 'allow' and call('/api/learn')[1]['history'][0]['symbol'] == 'ETHUSDT'
# laboratorium wariantów: odczyt i zmiana aktywnego wariantu
c_, r_ = call('/api/lab'); assert c_ == 200 and r_['active'] == 'v2' and [x['id'] for x in r_['registry']] == ['v1', 'v2', 'v1n'] and r_['age_s'] is None and r_['strategy'] == 'plan', r_
assert call('/api/bot/settings', 'POST', {'ens_variant': 'v1'})[0] == 403
assert call('/api/bot/settings', 'POST', {'ens_variant': 'v1'}, H)[0] == 200 and call('/api/lab')[1]['active'] == 'v1' and call('/api/bot')[1]['settings']['risk_pct'] == 0.4
for bad in ({'ens_variant': 'zzz'}, {'ens_variant': 'V1'}, {'ens_variant': 1}, {'ens_variant': '../../etc'}):
    c_, r_ = call('/api/bot/settings', 'POST', bad, H); assert c_ == 400, (bad, c_, r_)
assert call('/api/lab')[1]['active'] == 'v1' and call('/api/bot/settings', 'POST', {'ens_variant': 'v2', 'strategy': 'ensemble'}, H)[0] == 200
_bh._atomic_write(os.path.join(botd, 'lab.json'), json.dumps({'ts': int(time.time()) - 60, 'champion': 'v2', 'variants': [{'id': 'v2'}]}))
c_, r_ = call('/api/lab'); assert r_['champion'] == 'v2' and r_['active'] == 'v2' and r_['strategy'] == 'ensemble' and 50 < r_['age_s'] < 300 and r_['variants'] == [{'id': 'v2'}], r_
assert call('/api/bot/settings', 'POST', {'strategy': 'plan'}, H)[0] == 200
c_,r_=call('/api/ext_test?x=1'); assert c_==200 and r_['ok'] and r_['q']=={'x':['1']}, r_
assert call('/api/health')[1].get('hack') is None and 'ok' in call('/api/health')[1]       # rozszerzenie nie nadpisze wbudowanego adresu
import btc_botcfg as _bb
_bb.add_proposal(botd, {'id': 'p'+'a'*24, 'symbol': 'BTCUSDT', 'side': 'long', 'qty': 0.01, 'entry': 100.0, 'stop': 95.0})
assert call('/api/bot')[1]['proposals'][-1]['status'] == 'pending'
assert call('/api/bot/proposal','POST',{'id':'p'+'a'*24,'decision':'approve'})[0]==403
assert call('/api/bot/proposal','POST',{'id':'p'+'a'*24,'decision':'approve'},H)[0]==200 and call('/api/bot')[1]['proposals'][-1]['status']=='approved'
assert call('/api/bot/proposal','POST',{'id':'p'+'a'*24,'decision':'reject'},H)[0]==409
assert call('/api/bot/proposal','POST',{'id':'p'+'b'*24,'decision':'approve'},H)[0]==404
assert call('/api/bot/proposal','POST',{'id':'../etc','decision':'approve'},H)[0]==400 and call('/api/bot/proposal','POST',{'id':'p'+'a'*24,'decision':'x'},H)[0]==400
O={'type':'open','symbol':'SOLUSDT','side':'long','notional':20,'stop_pct':3}
assert call('/api/bot/order','POST',O)[0]==403
c_,r_=call('/api/bot/order','POST',O,H); assert c_==200 and r_['id'].startswith('o')
assert call('/api/bot')[1]['orders'][-1]['status']=='queued' and call('/api/bot')[1]['exec']=={}
for bad in ({**O,'stop_pct':0},{k:v for k,v in O.items() if k!='stop_pct'},{**O,'leverage':99},{'type':'close','symbol':'../x'},{**O,'side':'x'}):
    assert call('/api/bot/order','POST',bad,H)[0]==400, bad
for _ in range(12): call('/api/bot/order','POST',O,H)
assert call('/api/bot/order','POST',O,H)[0]==400        # limit kolejki
assert call('/api/quote?symbol=bad')[0]==400 and call('/api/quote')[0]==400
btc_api.bybit_quote = lambda sym: {'symbol': sym, 'price': 123.4, 'turnover24h': 1e9}
assert call('/api/quote?symbol=solusdt')[1]['price']==123.4
assert call('/api/shadow')[0]==200
assert call('/api/bot/risk-reset','POST',{})[0]==403
assert call('/api/bot/risk-reset','POST',{},H)[0]==200 and os.path.exists(os.path.join(botd,'bot_risk_reset.json')) and call('/api/bot')[1]['risk']=={}
print('bot endpoints ok')
# --- asystent AI przez HTTP
import btc_ai
X={'X-Requested-With':'btc-ui'}
code,r=call('/api/ai/chat'); assert code==200 and r['ready'] is False, r
code,r=call('/api/ai/chat','POST',{"messages":[{"role":"user","content":"hej"}]},X); assert code==400 and 'btc-ai' in r['error'], r
code,r=call('/api/ai/chat','POST',{"messages":[{"role":"user","content":"hej"}]}); assert code==403, r
btc_ai.tick(botd, ask_fn=lambda d,m:("x",{}), threaded=False)
code,r=call('/api/ai/chat'); assert code==200 and r['ready'] is True, r
big=[{"role":"user","content":"a"*3000},{"role":"assistant","content":"b"*3000},{"role":"user","content":"Dlaczego bot nie handluje?"}]
code,r=call('/api/ai/chat','POST',{"messages":big},X); assert code==200 and len(r['id'])==16, r     # treść > 4096 bajtów przechodzi tylko dla czatu
jid=r['id']
code,r=call('/api/ai/chat?id='+jid); assert code==200 and r=={'status':'run'}, r
btc_ai.tick(botd, ask_fn=lambda d,m:("Bo nie ma przewagi.",{"in":10,"out":5,"model":"claude-opus-5-5"}), threaded=False)
code,r=call('/api/ai/chat?id='+jid); assert code==200 and r['status']=='done' and r['text']=="Bo nie ma przewagi.", r
code,r=call('/api/ai/chat?id=zzz'); assert code==404, r
code,r=call('/api/bot/settings','POST',{"x":"a"*5000},X); assert code==400, r                        # inne adresy dalej mają limit 4096
print('ai http ok')
# --- scalper przez HTTP
code,r=call('/api/scalp'); assert code==200 and r['settings']=={'enabled':True} and r['age_s'] is None, r
code,r=call('/api/scalp/settings','POST',{"enabled":False},X); assert code==200 and r['settings']=={'enabled':False}, r
code,r=call('/api/scalp/settings','POST',{"enabled":"x"},X); assert code==400, r
code,r=call('/api/scalp/settings','POST',{"enabled":True}); assert code==403, r
print('scalp http ok')
# --- rynek na żywo (dane o przepływie)
code,r=call('/api/flow'); assert code==200 and r['symbols']=={} and r['age_s'] is None, r
json.dump({"ts": int(time.time())-30, "symbols": {"BTCUSDT": {"score": 1.2, "lean": "wzrostowe", "parts": [], "info": {}}}, "accuracy": {"all": {"n": 0}, "oos": {"n": 0}}}, open(os.path.join(botd,'flow.json'),'w'))
code,r=call('/api/flow'); assert code==200 and r['symbols']['BTCUSDT']['lean']=='wzrostowe' and 25<r['age_s']<60, r
print('flow http ok')
# --- dane użytkownika (zarobki, produkty, profil mentora): zapis, odczyt, konflikt, walidacja, CSRF
code,r=call('/api/userdata?key=zarobki'); assert code==200 and r['found'] is False, r
Zv={"entries":[{"idea":"rowery","kind":"in","amt":450,"date":"2026-11-20","note":"","t":1}],"goal":1000}
code,r=call('/api/userdata','POST',{"key":"zarobki","value":Zv,"base_ts":0},X); assert code==200 and r['ts']>0, r
ts1=r['ts']
code,r=call('/api/userdata?key=zarobki'); assert code==200 and r['found'] and r['value']['goal']==1000 and r['ts']==ts1, r
code,r=call('/api/userdata','POST',{"key":"zarobki","value":Zv,"base_ts":0},X); assert code==409 and r['ts']==ts1 and r['value']['goal']==1000, r
code,r=call('/api/userdata','POST',{"key":"zarobki","value":Zv,"base_ts":ts1},X); assert code==200 and r['ts']>ts1, r
code,r=call('/api/userdata','POST',{"key":"hasla","value":{},"base_ts":0},X); assert code==400, r
code,r=call('/api/userdata?key=hasla'); assert code==400, r
code,r=call('/api/userdata','POST',{"key":"zarobki","value":{"entries":"x"},"base_ts":0},X); assert code==400, r
code,r=call('/api/userdata','POST',{"key":"zarobki","value":Zv,"base_ts":0}); assert code==403, r
code,r=call('/api/userdata','POST',raw=b'x'*(300*1024+10),headers=dict(X,**{'Content-Type':'application/json'})); assert code==400, r
print('userdata http ok')
# --- kopia zapasowa: stan dla panelu
code,r=call('/api/backup'); assert code==200 and r['configured'] is False and r['age_s'] is None and r['ts']==0, r
json.dump({"ts":int(time.time())-100,"ok":True,"name":"btc-20261120-0350.tar.gz","size":123456,"files":12,"archives":5,"last_ok_ts":int(time.time())-100,"skipped":[],"warnings":[],"remote":"gdrive:btc-backup"},open(os.path.join(botd,'backup_status.json'),'w'))
code,r=call('/api/backup'); assert code==200 and 90<r['age_s']<130 and r['archives']==5 and r['name'].startswith('btc-'), r
print('backup http ok')
srv.shutdown(); stop.set();
print('api ok')
