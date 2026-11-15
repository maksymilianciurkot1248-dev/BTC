import sys, os, time, tempfile, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_trader as t
tmp=tempfile.mkdtemp(); t.HERE=tmp
os.environ.update(TRADER_MODE='testnet',TRADER_API_KEY='k',TRADER_API_SECRET='s',TRADER_MAX_CAPITAL_USDT='1000')
os.environ.pop('TRADER_EXPECTED_IP',None)
calls=[]
class Fake:
    positions=[]; orders=[]
    def __init__(s,c): pass
    def ok(s,m,path,params=None,allow=()):
        calls.append((m,path,params))
        if path=='/v5/account/wallet-balance': return {'list':[{'totalEquity':'1500'}]}
        if path=='/v5/position/list': return {'list':Fake.positions}
        if path=='/v5/order/realtime': return {'list':Fake.orders}
        if path=='/v5/position/closed-pnl': return {'list':[{'closedPnl':'-5'}]}
        if path=='/v5/order/create': return {'orderId':'1'}
        return {}
plan={'action':'long','setup':'pullback','date':'2026-11-01','valid_until_ms':10**13,'live_state':'waiting',
      'entry':100000.0,'stop':98000.0,'tp1':104000.0,'entry_type':'limit'}
class Pub:
    def ticker(s,sym): return {'lastPrice':'100500','turnover24h':'5e9'}
    def _request(s,p,params=None): return {'list':[{'lotSizeFilter':{'qtyStep':'0.001','minOrderQty':'0.001'},'priceFilter':{'tickSize':'0.1'}}]}
t.Api=Fake; t.make_pub=lambda c:Pub(); t.get_plan=lambda c,sym=None,pub=None,*a,**k:(dict(plan),100500.0,Pub())
t.run()
cr=[x for x in calls if x[1]=='/v5/order/create']; lev=[x for x in calls if x[1]=='/v5/position/set-leverage']
assert len(cr)==1 and lev[0][2]['buyLeverage']=='2', calls
o=cr[0][2]; print(o)
assert o['stopLoss']=='98000.0' and o['takeProfit']=='104000.0' and float(o['qty'])*100000<=1000*2   # kapitał ograniczony do 1000
# drugi przebieg: to samo zlecenie nie może zostać złożone ponownie
calls.clear(); t.run(); assert not [x for x in calls if x[1]=='/v5/order/create']
# STOP file
os.remove(os.path.join(tmp,'trader_state.json')); open(os.path.join(tmp,'STOP'),'w').close(); calls.clear(); t.run()
assert not [x for x in calls if x[1]=='/v5/order/create']; os.remove(os.path.join(tmp,'STOP'))
# pozycja bez SL: ręczna (niezarządzana) nie jest zamykana, pozycja bota tak
Fake.positions=[{'symbol':'BTCUSDT','size':'0.01','side':'Buy','leverage':'2','positionValue':'1000','stopLoss':'0'}]; os.remove(os.path.join(tmp,'trader_state.json')) if os.path.exists(os.path.join(tmp,'trader_state.json')) else None; calls.clear(); t.run()
assert not [x for x in calls if x[1]=='/v5/order/create' and x[2].get('reduceOnly')], calls
json.dump({'day':time.strftime('%Y-%m-%d',time.gmtime()),'orders':0,'placed':[],'halted':False,'managed':{'BTCUSDT':int(time.time())}},open(os.path.join(tmp,'trader_state.json'),'w'))
# pozycja bez SL -> zamknięcie reduceOnly
Fake.positions=[{'symbol':'BTCUSDT','size':'0.01','side':'Buy','leverage':'2','positionValue':'1000','stopLoss':'0'}]; calls.clear(); t.run()
cl=[x for x in calls if x[1]=='/v5/order/create']; assert cl and cl[0][2]['reduceOnly'] and cl[0][2]['side']=='Sell', calls
# limit strat: -5 USDT z 1000 = 0.5% <2% ok; wymuś 2%
Fake.positions=[]; os.remove(os.path.join(tmp,'trader_state.json'))
os.environ['TRADER_MAX_DAILY_LOSS_PCT']='0.4'; calls.clear(); t.run()
assert not [x for x in calls if x[1]=='/v5/order/create'], calls
print('flow ok')
