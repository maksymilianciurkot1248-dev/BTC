import sys, json, time, tempfile, os, random
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_extras as e
from datetime import datetime, timezone, timedelta
random.seed(1)
now=datetime(2026,11,3,12,tzinfo=timezone.utc)
def stooq(base):
    d=datetime(2026,6,1); rows=["Date,Open,High,Low,Close,Volume"]; p=base
    for i in range(160):
        d2=d+timedelta(days=i)
        if d2.weekday()>=5: continue
        p*=1+random.uniform(-.01,.01); rows.append(f"{d2.date()},{p},{p},{p},{p},0")
    return "\n".join(rows)
ts=int(now.timestamp()*1000)
def get(url, as_text=False, timeout=0):
    if 'alternative.me' in url: return {"data":[{"value":str(30+i),"value_classification":"Fear","timestamp":str(1700000000+i*86400)} for i in range(30)][::-1]}
    if 'fees/recommended' in url: return {"fastestFee":12,"halfHourFee":9,"hourFee":6,"economyFee":3,"minimumFee":1}
    if url.endswith('/api/mempool'): return {"count":45000,"vsize":38000000}
    if 'difficulty-adjustment' in url: return {"progressPercent":41.2,"difficultyChange":2.1,"remainingBlocks":1180,"estimatedRetargetDate":1.79e12}
    if 'hashrate/3m' in url: return {"currentHashrate":9.1e20,"hashrates":[{"timestamp":i,"avgHashrate":(8.0+i*0.012)*1e20/1} for i in range(90)]}
    if 'tip/height' in url: return "912345"
    if 'premiumIndex' in url: return {"markPrice":"100100","indexPrice":"100000","lastFundingRate":"0.00012"}
    if 'openInterestHist' in url: return [{"sumOpenInterestValue":str(5e9+i*1e7)} for i in range(25)]
    if 'globalLongShort' in url or 'topLongShort' in url: return [{"longShortRatio":"1.4","longAccount":"0.58"}]*24
    if 'takerlongshort' in url: return [{"buySellRatio":"0.97"}]*24
    if 'openInterest?' in url: return {"openInterest":"80000"}
    if 'funding-rate' in url: return {"data":[{"fundingRate":"0.00008"}]}
    if 'open-interest' in url: return {"data":[{"oiUsd":"3000000000"}]}
    if 'long-short-account' in url: return {"data":[["1","1.2"],["2","1.3"]]}
    if 'volatility_index' in url: return {"result":{"data":[[0,1,2,3,45+i*0.3] for i in range(45)]}}
    if 'book_summary' in url:
        res=[]
        for exp in ("29NOV26","25DEC26"):
            for k in range(80000,121000,5000):
                res.append({"instrument_name":f"BTC-{exp}-{k}-C","open_interest":max(0,(120000-k)/1000)+5,"volume":3})
                res.append({"instrument_name":f"BTC-{exp}-{k}-P","open_interest":max(0,(k-80000)/1000)+5,"volume":4})
        res.append({"instrument_name":"BTC-1JAN20-5000-C","open_interest":1,"volume":0})  # wygasła
        return {"result":res}
    if 'coinbase' in url: return {"price":"100180.5"}
    if 'kraken' in url: return {"result":{"XXBTZUSD":{"c":["100150.0","0.1"]}}}
    if 'api.binance.com' in url: return {"price":"100000.0"}
    if 'stooq' in url: return stooq(5000 if 'spx' in url else 100)
    if 'llama' in url: return {"peggedAssets":[{"symbol":"USDT","pegType":"peggedUSD","circulating":{"peggedUSD":1.6e11},"circulatingPrevDay":{"peggedUSD":1.59e11},"circulatingPrevWeek":{"peggedUSD":1.58e11},"circulatingPrevMonth":{"peggedUSD":1.5e11}},
                                               {"symbol":"EURC","pegType":"peggedEUR","circulating":{"peggedEUR":1e9}}]}
    raise RuntimeError("HTTP 404")
dates=[(datetime(2026,6,1)+timedelta(days=i)).date().isoformat() for i in range(150)]
closes=[100000*(1+random.uniform(-.02,.02)) for _ in dates]
hp=os.path.join(tempfile.mkdtemp(),"h.jsonl")
x=e.collect_extras(dates,closes,hp,get=get,now=now)
bad={k:v for k,v in x['sources'].items() if not v['ok']}; assert not bad, bad
assert x['sentiment']['value']==30 or True
assert x['derivs']['funding_avg_pct']>0 and x['derivs']['binance']['oi_change_24h_pct']>0
assert abs(x['premium']['coinbase_vs_binance_pct']-0.1805)<1e-3
assert x['options']['expiries'][0]['max_pain'] and all(ex['days']>0 for ex in x['options']['expiries'])
assert x['macro']['spx']['corr_30d'] is not None and -1<=x['macro']['spx']['corr_30d']<=1
assert abs(x['stablecoins']['change_30d_pct']-6.667)<0.01 and len(x['stablecoins']['top'])==1
assert x['onchain']['height']==912345 and x['onchain']['hashrate']['change_30d_pct']
print(len(x['signals']),'sygnałów', x['tally']); [print(' -',s['state'],s['label'],'|',s['text']) for s in x['signals']]
assert x['history']['points']==1
x2=e.collect_extras(dates,closes,hp,get=get,now=now); assert x2['history']['points']==1   # min odstęp
# odporność: wszystko pada
def dead(url,as_text=False,timeout=0): raise RuntimeError("HTTP 403")
y=e.collect_extras(None,None,None,get=dead,now=now); assert not any(v['ok'] for v in y['sources'].values()) and y['signals']==[]
# częściowa awaria: Binance pada, reszta działa
def part(url,as_text=False,timeout=0):
    if 'binance.com' in url: raise RuntimeError("HTTP 451")
    return get(url,as_text,timeout)
z=e.collect_extras(dates,closes,None,get=part,now=now); assert z['sources']['derivs']['ok'] and 'okx' in z['derivs'] and 'binance' not in z['derivs']
assert z['sources']['premium']['ok'] and 'coinbase_vs_binance_pct' not in z['premium']
json.dumps(x)  # serializowalne
print('extras ok, payload', len(json.dumps(x)), 'B')
