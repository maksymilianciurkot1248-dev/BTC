import sys, os, time, tempfile, json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_account as acc, btc_collect as bc
now = int(time.time() * 1000); D = 86400000
class Fake:
    calls = []
    def api_key_info(s): return {"readOnly": 0, "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": ["AccountTransfer", "Withdraw"]}, "ips": ["*"], "expiredAt": "2026-11-20T00:00:00Z"}
    def wallet_balance(s, t="UNIFIED"): s.calls.append("w"); return {"totalEquity": "1500.5", "totalWalletBalance": "1490", "totalAvailableBalance": "900", "totalPerpUPL": "10.5", "totalInitialMargin": "250", "totalMaintenanceMargin": "20", "accountIMRate": "0.1667", "accountMMRate": "0.0133",
                                                  "coin": [{"coin": "USDT", "walletBalance": "1200", "usdValue": "1200", "unrealisedPnl": "10"}, {"coin": "BTC", "walletBalance": "0.003", "usdValue": "300", "unrealisedPnl": "0.5"}, {"coin": "DUST", "walletBalance": "0", "usdValue": "0"}]}
    def positions(s): return [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.01", "avgPrice": "80000", "markPrice": "81000", "positionValue": "810", "leverage": "2", "unrealisedPnl": "10", "stopLoss": "0", "takeProfit": "", "liqPrice": "60000", "createdTime": str(now - D)},
                              {"symbol": "ETHUSDT", "side": "Sell", "size": "0", "avgPrice": "0"}]
    def open_orders(s): return [{"symbol": "SOLUSDT", "side": "Buy", "orderType": "Limit", "qty": "2", "price": "150", "triggerPrice": "0", "reduceOnly": False, "createdTime": str(now - 3600000)}]
    def closed_pnl(s, days=90):
        s.calls.append("c")
        rows = []
        for i in range(40):
            rows.append({"symbol": "BTCUSDT" if i % 2 else "ETHUSDT", "side": "Sell" if i % 3 else "Buy", "closedPnl": "6" if i % 3 else "-4", "openFee": "0.1", "closeFee": "0.2", "updatedTime": str(now - i * 2 * D)})
        return {"records": rows, "truncated": False}
    def executions(s, days=30): return [{"execType": "Trade", "execFee": "0.3"}, {"execType": "Funding", "execFee": "-0.5"}, {"execType": "Funding", "execFee": "0.2"}]
db = bc.DB(os.path.join(tempfile.mkdtemp(), "a.db"))
c = Fake(); out = acc.collect(c, db=db, now_ms=now)
assert out["wallet"]["equity"] == 1500.5 and out["wallet"]["coins"][0]["coin"] == "USDT" and all(x["coin"] != "DUST" for x in out["wallet"]["coins"])
assert len(out["positions"]) == 1 and out["positions"][0]["stop"] is None and out["positions"][0]["liq_dist_pct"] == 25.93 and out["exposure"]["without_stop"] == ["BTCUSDT"] and abs(out["exposure"]["x"] - 0.54) < 0.01
k = out["key"]; assert k["withdraw"] and not k["read_only"] and len(k["warnings"]) == 2 and "wypłat" in k["warnings"][0] and len(k["notes"]) == 1, k
ok_ = acc.key_info({"readOnly": 0, "permissions": {"ContractTrade": ["Order"]}, "ips": ["1.2.3.4"], "expiredAt": "2099-01-01T00:00:00Z"}); assert ok_["warnings"] == [] and ok_["notes"] == [] and not ok_["withdraw"]   # klucz do odczytu i handlu jest w porządku
s30 = out["stats"]["30"]; assert s30["trades"] == 16 and s30["fees"] == 4.8 and "by_side" in s30, s30
assert out["stats"]["90"]["trades"] == 40 and out["stats"]["7"]["trades"] == 4
assert out["by_symbol"]["90"][0]["pnl"] >= out["by_symbol"]["90"][-1]["pnl"] and len(out["daily"]) > 10
assert out["costs"] == {"trading_fees_30d": 0.3, "funding_30d": -0.3, "note": "dodatnie = zapłacone"}, out["costs"]
assert out["equity_curve"][-1][1] == 1500.5 and "h" not in json.dumps(out).lower().split('"secret"')[0][:0]
# drugi przebieg: historia z pamięci podręcznej (bez ponownego pobierania zamkniętych pozycji)
c.calls.clear(); out2 = acc.collect(c, prev=out, db=db, now_ms=now + 60000)
assert "c" not in c.calls and "w" in c.calls and out2["stats"] == out["stats"]
# po 31 minutach historia jest odświeżana
c.calls.clear(); acc.collect(c, prev=out, db=db, now_ms=now + 31 * 60000); assert "c" in c.calls
# brak klucza i zapis/odczyt
d = tempfile.mkdtemp(); acc.write(d, {"ok": False, "error": "no_key"}); assert acc.read(d)["error"] == "no_key"
# okno z pustą historią
class Empty(Fake):
    def closed_pnl(s, days=90): return {"records": [], "truncated": False}
o3 = acc.collect(Empty(), db=db, now_ms=now); assert o3["stats"]["30"] == {"trades": 0}
print("account ok")
