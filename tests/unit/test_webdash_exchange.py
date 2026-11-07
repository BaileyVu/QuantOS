import unittest

from quantos.paper import webdash


class FakeClient:
    def __init__(self): self.calls, self.fail = 0, False
    def position_risk(self):
        self.calls += 1
        if self.fail: raise OSError("down")
        return [{"symbol": "NEARUSDC", "positionAmt": "-23", "entryPrice": "4.149", "markPrice": "4.025",
                 "unRealizedProfit": "2.852"}, {"symbol": "ADAUSDC", "positionAmt": "0", "entryPrice": "0",
                                                 "markPrice": "0.5", "unRealizedProfit": "0"}]
    def account(self): return {"totalWalletBalance": "5000.5", "totalUnrealizedProfit": "2.852"}


class ExchangeViewTests(unittest.TestCase):
    def tearDown(self):
        webdash._EXCHANGE.clear(); webdash._CACHE.pop("exchange", None)

    def test_disabled_without_keys(self):
        self.assertEqual(webdash.exchange_view(), {"enabled": False})

    def test_reports_binance_mark_and_upnl_cached_and_stale_on_error(self):
        c = FakeClient()
        webdash._EXCHANGE.update(mode="demo", client=c)
        v = webdash.exchange_view()
        self.assertEqual(v["positions"], [{"symbol": "NEARUSDC", "amount": -23.0, "entry": 4.149, "mark": 4.025,
                                           "upnl": 2.852}])
        webdash.exchange_view()
        self.assertEqual(c.calls, 1)  # cached for 2s
        c.fail = True
        t, data = webdash._CACHE["exchange"]
        webdash._CACHE["exchange"] = (t - 5, data)
        stale = webdash.exchange_view()
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["positions"][0]["mark"], 4.025)


if __name__ == "__main__":
    unittest.main()
