import os, tempfile, unittest, json
from unittest import mock
import hl_common as C
import copier as K
import recorder as R
import select_wallets as S

EX = {"symbols": [
    {"symbol": "BTCUSDC", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "BTC", "quoteAsset": "USDC",
     "filters": [{"filterType": "MIN_NOTIONAL", "notional": "5"}, {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}]},
    {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "BTC", "quoteAsset": "USDT",
     "filters": [{"filterType": "MIN_NOTIONAL", "notional": "100"}, {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}]},
    {"symbol": "1000PEPEUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "1000PEPE", "quoteAsset": "USDT",
     "filters": [{"filterType": "MIN_NOTIONAL", "notional": "5"}, {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"}]},
    {"symbol": "DEADUSDT", "status": "BREAK", "contractType": "PERPETUAL", "baseAsset": "DEAD", "quoteAsset": "USDT", "filters": []}]}
BASES, INFO = C.binance_perps(EX)


def klines(n=20, hi=101, lo=99, close=100, t0=0):
    return [[t0 + i * 3600000, "100", str(hi), str(lo), str(close), "1"] for i in range(n)]


class FakeNet:
    def __init__(self, bid=100.0, ask=100.1):
        self.bid, self.ask, self.calls = bid, ask, []

    def __call__(self, url, payload=None, timeout=20):
        self.calls.append(url)
        if "bookTicker" in url:
            return {"bidPrice": str(self.bid), "askPrice": str(self.ask)}
        if "klines" in url:
            return klines(20, 102, 98, 100)       # TR = 4 -> ATR 4, stop dist 10
        raise AssertionError(url)


class T(unittest.TestCase):
    def test_validators_and_allowlist(self):
        self.assertTrue(C.valid_addr("0x" + "a" * 40))
        for bad in ("0x" + "g" * 40, "0x12", None, "0x" + "a" * 41, "<script>"):
            self.assertFalse(C.valid_addr(bad))
        self.assertTrue(C.valid_coin("BTC") and C.valid_coin("kPEPE"))
        for bad in ("btc", "@107", "xyz:TSLA", "A" * 16, "", "BTC;", None):
            self.assertFalse(C.valid_coin(bad))
        for bad in ("nan", "inf", "-inf", "x"):
            with self.assertRaises(ValueError):
                C.num(bad)
        C.check_host("https://api.hyperliquid.xyz/info")
        C.check_host("https://fapi.binance.com/x")
        for bad in ("https://evil.com/", "http://api.hyperliquid.xyz/info", "https://api.hyperliquid.xyz.evil.com/",
                    "https://evil.com/api.hyperliquid.xyz", "file:///etc/passwd"):
            with self.assertRaises(ValueError):
                C.http_json(bad)

    def test_map_coin(self):
        self.assertEqual(C.map_coin("BTC", BASES), "BTCUSDC")
        self.assertEqual(C.map_coin("kPEPE", BASES), "1000PEPEUSDT")
        self.assertIsNone(C.map_coin("DEAD", BASES))
        self.assertIsNone(C.map_coin("@5", BASES))

    def test_diff(self):
        p = lambda s: {"BTC": {"szi": s, "entryPx": 100.0, "mark": 101.0}} if s else {}
        typ = lambda a, b: [e["type"] for e in R.diff_positions(p(a), p(b))]
        self.assertEqual(typ(0, 1), ["OPEN"])
        self.assertEqual(typ(1, 2), ["INCREASE"])
        self.assertEqual(typ(2, 1), ["REDUCE"])
        self.assertEqual(typ(1, 0), ["CLOSE"])
        self.assertEqual(typ(1, -1), ["FLIP"])
        self.assertEqual(typ(1, 1.001), [])
        e = R.diff_positions(p(1), p(-2))[0]
        self.assertEqual((e["side"], e["prev_size"], e["size"]), (-1, 1, -2))

    def test_sizing_stop(self):
        info = {"minNotional": 5, "step": 0.001, "minQty": 0.001}
        q, stop, why = K.size_position(50, 100, 4, 1, info)   # risk 1 / dist 10 = 0.1 qty, notional 10
        self.assertIsNone(why); self.assertAlmostEqual(q, 0.1); self.assertAlmostEqual(stop, 90)
        q, stop, why = K.size_position(50, 100, 4, -1, info)
        self.assertAlmostEqual(stop, 110)
        q, _, why = K.size_position(50, 100, 0.1, 1, info)    # risk qty 4 -> cap 3x: 1.5
        self.assertAlmostEqual(q, 1.5)
        self.assertAlmostEqual(K.compute_atr(klines(20, 102, 98), 10 ** 15), 4.0)

    def test_min_notional_skip(self):
        net = FakeNet()
        c = K.Copier(BASES, {"BTCUSDC": {"minNotional": 100, "step": 0.001, "minQty": 0.001}}, http=net, now=lambda: 10 ** 9)
        c.on_event({"type": "OPEN", "coin": "BTC", "wallet": "w", "side": 1})
        c.process_due()   # delay-0 book only
        bk = c.books["0"]
        self.assertEqual(bk["open"], {}); self.assertEqual(bk["skip_counts"], {"min_notional": 1})

    def test_delay_pricing_and_exit(self):
        net, clock = FakeNet(100.0, 100.1), [10 ** 9]
        c = K.Copier(BASES, INFO, http=net, now=lambda: clock[0])
        c.on_event({"type": "OPEN", "coin": "BTC", "wallet": "w", "side": 1})
        c.process_due()
        self.assertEqual(len(c.books["0"]["open"]), 1)
        self.assertEqual(len(c.books["30"]["open"]), 0)
        e0 = c.books["0"]["open"]["w|BTC"]["entry"]
        self.assertAlmostEqual(e0, 100.1 * 1.0005)        # long pays ask + slippage
        net.bid, net.ask = 110.0, 110.1
        clock[0] += 30; c.process_due()
        e30 = c.books["30"]["open"]["w|BTC"]["entry"]
        self.assertAlmostEqual(e30, 110.1 * 1.0005)        # priced when delay elapsed
        clock[0] += 30; c.process_due()
        self.assertEqual(len(c.books["60"]["open"]), 1)
        c.on_event({"type": "CLOSE", "coin": "BTC", "wallet": "w", "side": 1})
        c.process_due()
        self.assertEqual(c.books["0"]["closed"][0]["reason"], "wallet_exit")
        self.assertAlmostEqual(c.books["0"]["closed"][0]["exit"], 110.0 * 0.9995)
        self.assertEqual(len(c.books["30"]["open"]), 1)   # exit delayed
        # short uses bid on entry
        c.on_event({"type": "OPEN", "coin": "BTC", "wallet": "x", "side": -1}); c.process_due()
        self.assertAlmostEqual(c.books["0"]["open"]["x|BTC"]["entry"], 110.0 * 0.9995)

    def test_stop_and_max_open(self):
        net, clock = FakeNet(), [10 ** 9]
        c = K.Copier(BASES, INFO, http=net, now=lambda: clock[0])
        c.on_event({"type": "OPEN", "coin": "BTC", "wallet": "w", "side": 1}); c.process_due()
        net.bid, net.ask = 80.0, 80.1
        c.check_stops()
        bk = c.books["0"]
        self.assertEqual(bk["closed"][0]["reason"], "stop"); self.assertLess(bk["realized"], 0)
        for i in range(6):
            c.on_event({"type": "OPEN", "coin": "BTC", "wallet": "w%d" % i, "side": 1})
        net.bid, net.ask = 100.0, 100.1
        c.process_due()
        self.assertEqual(len(bk["open"]), 5); self.assertEqual(bk["skip_counts"]["max_open"], 1)

    def test_refuse_reselect(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "w.json")
            open(out, "w").write("{}")
            with mock.patch.object(C, "http_json", side_effect=AssertionError("no network")):
                with self.assertRaises(SystemExit):
                    S.main(out=out)
            self.assertEqual(open(out).read(), "{}")

    def test_recorder_cycle(self):
        resp = lambda s: {"marginSummary": {"accountValue": "1000"}, "assetPositions": [
            {"position": {"coin": "BTC", "szi": str(s), "entryPx": "100", "positionValue": str(abs(s) * 100)}}] if s else []}
        seq = [resp(0), resp(1), resp(0)]
        net = FakeNet()
        def http(url, payload=None, timeout=20):
            if payload and payload["type"] == "clearinghouseState":
                return seq.pop(0)
            if payload:
                return []
            return net(url)
        with tempfile.TemporaryDirectory() as d:
            r = R.Recorder(d, ["0x" + "a" * 40], BASES, INFO, http=http, interval=0)
            a = r.poll_wallet("0x" + "a" * 40); b = r.poll_wallet("0x" + "a" * 40); c = r.poll_wallet("0x" + "a" * 40)
            self.assertEqual((a, [e["type"] for e in b], [e["type"] for e in c]), ([], ["OPEN"], ["CLOSE"]))
            r.save()
            lines = open(os.path.join(d, "events.jsonl")).read().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(os.path.exists(os.path.join(d, "heartbeat.json")))


if __name__ == "__main__":
    unittest.main()
