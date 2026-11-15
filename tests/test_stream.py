import sys, os, json, asyncio, time, tempfile, sqlite3
os.environ['NO_PROXY'] = '127.0.0.1,localhost'; os.environ['no_proxy'] = '127.0.0.1,localhost'
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import websockets
import btc_stream as bs

# ---------------- czysta logika na formatach zaobserwowanych na żywo ----------------
clock = [1000.0]
agg = bs.Agg(now=lambda: clock[0])
st = {}
bs.handle_bybit_spot(agg, {"topic": "tickers.BTCUSDT", "type": "snapshot", "data": {"symbol": "BTCUSDT", "lastPrice": "80311.1", "price24hPcnt": "0.022937"}}, st)
bs.handle_bybit_spot(agg, {"topic": "tickers.BTCUSDT", "type": "delta", "data": {"symbol": "BTCUSDT", "bid1Price": "80311.0"}}, st)           # delta bez ceny: cena zostaje
assert agg.q["BTCUSDT"]["price"] == 80311.1 and abs(agg.q["BTCUSDT"]["chg24"] - 2.2937) < 1e-6
bs.handle_bybit_spot(agg, {"topic": "tickers.BTCUSDT", "type": "delta", "data": {"symbol": "BTCUSDT", "lastPrice": "80320.5"}}, st)
assert agg.q["BTCUSDT"]["price"] == 80320.5 and abs(agg.q["BTCUSDT"]["chg24"] - 2.2937) < 1e-6        # chg24 dziedziczone ze stanu
bs.handle_bybit_spot(agg, {"topic": "tickers.XAUTUSDT", "data": {"symbol": "XAUTUSDT", "lastPrice": "4106.61"}}, st); assert agg.q["XAUTUSDT"]["price"] == 4106.61
bs.handle_bybit_spot(agg, {"topic": "tickers.BADUSDT", "data": {"symbol": "BADUSDT", "lastPrice": "nan"}}, st); assert "BADUSDT" not in agg.q
lst = {}
bs.handle_bybit_linear(agg, {"topic": "publicTrade.BTCUSDT", "data": [{"T": 1000000, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "80000"}, {"T": 1000000, "s": "BTCUSDT", "S": "Sell", "v": "2", "p": "80000"},
                                                                       {"T": 1000000, "s": "BTCUSDT", "S": "Buy", "v": "4", "p": "80000"}]}, lst)
bs.handle_bybit_linear(agg, {"topic": "allLiquidation.BTCUSDT", "data": [{"T": 1000000, "s": "BTCUSDT", "S": "Buy", "v": "1", "p": "80000"}, {"T": 1000000, "s": "BTCUSDT", "S": "Sell", "v": "0.5", "p": "80000"}]}, lst)
bs.handle_bybit_linear(agg, {"topic": "allLiquidation.ETHUSDT", "data": {"T": 1000000, "s": "ETHUSDT", "S": "Buy", "v": "10", "p": "2400"}}, lst)    # obiekt zamiast listy
bs.handle_bybit_linear(agg, {"topic": "tickers.BTCUSDT", "data": {"markPrice": "80278.3", "openInterestValue": "5455234729", "fundingRate": "0.0001"}}, lst)
fl = agg.flow()["1m"]
assert fl["buy_usd"] == 360000 and fl["sell_usd"] == 160000 and fl["cvd_usd"] == 200000 and fl["trades"] == 3, fl
assert fl["whale_trades"] == 1 and fl["whale_net_usd"] == 320000, fl                            # próg 250 tys.: tylko 4 BTC (320 tys.) jest duże
assert fl["liq_long_usd"] == 80000 + 24000 and fl["liq_short_usd"] == 40000 and fl["liq_count"] == 3, fl   # Buy = zlikwidowana pozycja LONG
assert agg.mark["mark"] == 80278.3 and agg.mark["oi_usd"] == 5455234729 and abs(agg.mark["funding_pct"] - 0.01) < 1e-9
clock[0] += 400                                                                                  # okno 5 min: stare zdarzenia znikają, 1 h zostaje
assert agg.flow()["5m"]["trades"] == 0 and agg.flow()["1h"]["trades"] == 3
clock[0] += 4000; assert agg.flow()["1h"]["trades"] == 0 and agg.flow()["1h"]["liq_count"] == 0
bs.handle_deribit(agg, {"params": {"channel": "deribit_volatility_index.btc_usd", "data": {"volatility": 37.01}}}); assert agg.dvol["value"] == 37.01
bs.handle_deribit(agg, {"params": {"channel": "deribit_price_index.btc_usd", "data": {"price": 80229.54}}}); assert agg.index["price"] == 80229.54
bs.handle_mempool(agg, {"blocks": [{"height": 974648, "tx_count": 10}, {"height": 974649, "tx_count": 4296, "timestamp": 1793874566}]}); assert agg.block["height"] == 974649
bs.handle_mempool(agg, {"block": {"height": 974650, "tx_count": 1}, "fees": {"fastestFee": 3.8, "halfHourFee": 2.9, "hourFee": 2.3, "economyFee": 0.2}}); assert agg.block["height"] == 974650 and agg.fees["halfHourFee"] == 2.9
bs.handle_okx(agg, {"arg": {"channel": "tickers"}, "data": [{"instId": "SOL-USDT", "last": "96.5", "open24h": "93"}]}); assert agg.q["SOLUSDT"]["src"] == "ws:okx"
bs.handle_finnhub(agg, {"type": "trade", "data": [{"s": "OANDA:XAU_USD", "p": 4120.5}, {"s": "AAPL", "p": 270.1}, {"s": "OANDA:UNKNOWN", "p": 1}]}); assert agg.q["XAUUSD"]["price"] == 4120.5 and agg.q["AAPL.US"]["price"] == 270.1 and "OANDA:UNKNOWN" not in agg.q
bs.handle_alpaca(agg, [{"T": "t", "S": "MSFT", "p": 420.0}, {"T": "success", "msg": "authenticated"}]); assert agg.q["MSFT.US"]["price"] == 420.0
# OKX nie nadpisuje świeżego Bybit
a2 = bs.Agg(now=lambda: clock[0]); a2.price("BTCUSDT", 100.0, "ws:bybit"); a2.price("BTCUSDT", 99.0, "ws:okx"); assert a2.q["BTCUSDT"]["price"] == 100.0
print('logika ok')

# ---------------- lokalne serwery WebSocket ----------------
async def serve(handler):
    srv = await websockets.serve(handler, '127.0.0.1', 0)
    return srv, f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"

async def run():
    subs = {"spot": [], "linear": [], "deribit": [], "mempool": [], "okx": 0, "finnhub": [], "alpaca": []}
    drops = {"n": 0}

    async def spot(ws):
        async for raw in ws:
            m = json.loads(raw)
            if m.get("op") == "subscribe":
                subs["spot"].append(m["args"])
                for t in m["args"]:
                    sym = t.split(".")[1]
                    await ws.send(json.dumps({"topic": t, "type": "snapshot", "data": {"symbol": sym, "lastPrice": "100.5", "price24hPcnt": "0.01"}}))
                if drops["n"] == 0:                                   # pierwsze połączenie: serwer zrywa po subskrypcji
                    drops["n"] += 1
                    await ws.close()
                    return
                await ws.send(json.dumps({"topic": "tickers.BTCUSDT", "type": "delta", "data": {"symbol": "BTCUSDT", "lastPrice": "101.5"}}))

    async def linear(ws):
        async for raw in ws:
            m = json.loads(raw)
            if m.get("op") == "subscribe":
                subs["linear"].append(m["args"])
                if any(a.startswith("publicTrade") for a in m["args"]):
                    await ws.send(json.dumps({"topic": "publicTrade.BTCUSDT", "data": [{"T": int(time.time() * 1000), "s": "BTCUSDT", "S": "Buy", "v": "5", "p": "80000"}]}))
                else:
                    await ws.send(json.dumps({"topic": "allLiquidation.BTCUSDT", "data": [{"T": int(time.time() * 1000), "s": "BTCUSDT", "S": "Sell", "v": "2", "p": "80000"}]}))

    async def deribit(ws):
        async for raw in ws:
            m = json.loads(raw); subs["deribit"].append(m["params"]["channels"])
            await ws.send(json.dumps({"jsonrpc": "2.0", "method": "subscription", "params": {"channel": "deribit_volatility_index.btc_usd", "data": {"volatility": 37.0}}}))

    async def mempool(ws):
        async for raw in ws:
            subs["mempool"].append(json.loads(raw)); await ws.send(json.dumps({"block": {"height": 1000, "tx_count": 5}}))

    async def okx(ws):
        subs["okx"] += 1
        async for raw in ws:
            await ws.send(json.dumps({"arg": {"channel": "tickers"}, "data": [{"instId": "ETH-USDT", "last": "2400", "open24h": "2300"}]}))

    async def finnhub(ws):
        async for raw in ws:
            subs["finnhub"].append(json.loads(raw))
            if len(subs["finnhub"]) == 1:
                await ws.send(json.dumps({"type": "trade", "data": [{"s": "OANDA:EUR_USD", "p": 1.08}]}))

    async def alpaca(ws):
        async for raw in ws:
            m = json.loads(raw); subs["alpaca"].append(m)
            if m.get("action") == "subscribe":
                await ws.send(json.dumps([{"T": "t", "S": "AAPL", "p": 271.0}]))

    servers = {}
    urls = {}
    for name, h in (("bybit_spot", spot), ("bybit_linear", linear), ("deribit", deribit), ("mempool", mempool), ("okx", okx), ("finnhub", finnhub), ("alpaca", alpaca)):
        servers[name], urls[name] = await serve(h)
    # --- scenariusz 1: Bybit działa (z jednym zerwaniem), OKX ma czekać; klucze Finnhub/Alpaca włączają dodatkowe źródła
    env = {"FINNHUB_API_KEY": "FINNKEY", "ALPACA_KEY_ID": "AKID", "ALPACA_SECRET": "ASEC"}
    tmp = tempfile.mkdtemp(); out = os.path.join(tmp, "stream.json"); db = os.path.join(tmp, "d.db")
    agg, stop = bs.Agg(), asyncio.Event()
    tasks = [asyncio.create_task(t) for t in bs.make_tasks(agg, stop, urls, env)] + [asyncio.create_task(bs.writer(agg, out, db, stop, every=0.3, db_every=0.0))]
    await asyncio.sleep(5.5)                                                                   # zrywanie + ponowne połączenie (backoff 1,5 s)
    stop.set(); [t.cancel() for t in tasks]; await asyncio.gather(*tasks, return_exceptions=True)
    # subskrypcje: 13 tematów spot w partiach <= 10; linear w dwóch osobnych zapytaniach
    flat = {t for part in subs["spot"] for t in part}
    assert all(len(p) <= 10 for p in subs["spot"]) and len(flat) == 13 and "tickers.XAUTUSDT" in flat, subs["spot"]
    assert any(a[0].startswith("tickers") for a in subs["linear"]) and any(a[0].startswith("allLiquidation") for a in subs["linear"]) and len(subs["linear"]) == 2
    assert subs["mempool"][0]["action"] == "want" and "deribit_volatility_index.btc_usd" in subs["deribit"][0]
    assert agg.status["bybit_spot"]["reconnects"] >= 1 and agg.status["bybit_spot"]["msgs"] > 13, agg.status["bybit_spot"]      # wznowił po zerwaniu
    assert agg.q["BTCUSDT"]["price"] == 101.5 and agg.q["BTCUSDT"]["src"] == "ws:bybit"
    assert subs["okx"] == 0 and "ETHUSDT" not in {k for k, v in agg.q.items() if v["src"] == "ws:okx"}, "OKX nie powinien się łączyć, gdy Bybit działa"
    assert agg.flow()["1m"]["whale_trades"] == 1 and agg.flow()["1m"]["liq_short_usd"] == 160000 and agg.dvol["value"] == 37.0 and agg.block["height"] == 1000
    assert agg.q["EURUSD"]["src"] == "ws:finnhub" and agg.q["AAPL.US"]["src"] == "ws:alpaca"
    assert any(m.get("type") == "subscribe" and m.get("symbol") == "OANDA:XAU_USD" for m in subs["finnhub"]) and any(m.get("action") == "auth" and m["key"] == "AKID" for m in subs["alpaca"])
    d = json.load(open(out)); assert d["quotes"]["BTCUSDT"]["price"] == 101.5 and "providers" in d and d["flow"]["btc"]["5m"]["cvd_usd"] == 400000
    con = sqlite3.connect(db); got = dict(con.execute("select metric, value from obs where source='stream'").fetchall())
    assert got["liq_short_usd_1h"] == 160000 and got["cvd_btc_5m_usd"] == 400000 and got["dvol_live"] == 37.0, got
    print("scenariusz 1 ok (zerwanie i wznowienie, partie subskrypcji, bramka OKX, klucze, zapis JSON i bazy)")
    # --- scenariusz 2: Bybit niedostępny (martwy port): OKX przejmuje ceny
    dead = "ws://127.0.0.1:1"
    agg2, stop2 = bs.Agg(), asyncio.Event()
    t2 = [asyncio.create_task(t) for t in bs.make_tasks(agg2, stop2, {**urls, "bybit_spot": dead, "bybit_linear": dead}, {"STREAM_OKX_AFTER": "2"})]
    await asyncio.sleep(6.5); stop2.set(); [t.cancel() for t in t2]; await asyncio.gather(*t2, return_exceptions=True)
    assert subs["okx"] >= 1 and agg2.q["ETHUSDT"]["src"] == "ws:okx" and abs(agg2.q["ETHUSDT"]["chg24"] - 4.3478) < 1e-3, agg2.q
    assert agg2.status["bybit_spot"]["error"] and agg2.status["bybit_spot"]["connected"] is False
    print("scenariusz 2 ok (zapas OKX, gdy Bybit padł)")
    for s in servers.values(): s.close()

asyncio.run(run())
print("stream ok")
