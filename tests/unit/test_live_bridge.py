from pathlib import Path
import tempfile
import unittest

from quantos.live.bridge import ExchangeMirror
from quantos.live.client import BinanceError
from quantos.paper.types import D

INS = {"symbol": "SOLUSDC", "margin_asset": "USDC", "status": "TRADING", "tick": "0.01", "step": "0.01",
       "min_qty": "0.01", "max_qty": "1000", "min_notional": "5", "market_step": "0.01",
       "market_min_qty": "0.01", "market_max_qty": "1000"}


class FakeExchange:
    mode, conditional_api = "testnet", "algo"

    def __init__(self, fail_stop=False, auth=False):
        self.pos, self.algo, self.orders, self.fail_stop, self.auth = {}, {}, {}, fail_stop, auth

    def set_margin_type(self, s, m): pass
    def set_leverage(self, s, lev): pass

    def new_order(self, p):
        if self.auth: raise BinanceError(-2015, "Invalid API-key, IP, or permissions", 401)
        q = D(p["quantity"]) * (1 if p["side"] == "BUY" else -1)
        self.pos[p["symbol"]] = self.pos.get(p["symbol"], D(0)) + q
        self.orders[p["newClientOrderId"]] = o = {"clientOrderId": p["newClientOrderId"], "status": "FILLED",
                                                  "executedQty": p["quantity"]}
        return o

    def get_order(self, s, cid): return self.orders[cid]

    def new_algo_order(self, p):
        if self.fail_stop: raise BinanceError(-2021, "would trigger", 400)
        self.algo[p["clientAlgoId"]] = o = {"clientAlgoId": p["clientAlgoId"], "symbol": p["symbol"],
                                            "orderType": p["type"], "side": p["side"], "closePosition": True,
                                            "triggerPrice": str(p["triggerPrice"])}
        return o

    def get_algo_order(self, cid): return self.algo[cid]
    def cancel_algo_order(self, cid): return self.algo.pop(cid)
    def open_orders(self, symbol=None): return []
    def open_algo_orders(self, symbol=None): return list(self.algo.values())
    def position_risk(self, symbol=None): return [{"symbol": s, "positionAmt": str(q)} for s, q in self.pos.items()]
    def cancel_all(self, s): pass
    def cancel_all_algo(self, s): self.algo = {k: v for k, v in self.algo.items() if v["symbol"] != s}


class Engine:
    def __init__(self): self.orders = []
    def active(self): return self.orders


def order(stop="75.00", state="PROTECTED"):
    return {"id": "a" * 32, "state": state, "filled": "0.05", "stop_price": stop, "instrument": INS,
            "signal": {"side": "LONG", "symbol": "SOLUSDC"}, "decision": {"leverage": "1"}}


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.alerts = []
        self.alert = lambda level, key, msg: self.alerts.append((level, key))

    def tearDown(self): self.dir.cleanup()

    def mirror(self, ex): return ExchangeMirror(ex, "run1", Path(self.dir.name), self.alert)

    def test_open_tighten_close_round_trip_reconciles(self):
        ex, e = FakeExchange(), Engine()
        m = self.mirror(ex)
        e.orders = [order()]
        self.assertTrue(m.sync(e, 1)["ok"])
        self.assertEqual(ex.pos["SOLUSDC"], D("0.05"))
        self.assertEqual([o["triggerPrice"] for o in ex.algo.values()], ["75.00"])
        self.assertTrue(self.mirror(ex).sync(e, 2)["ok"])  # restart: no duplicate entry
        self.assertEqual(ex.pos["SOLUSDC"], D("0.05"))
        e.orders = [order(stop="80.00")]
        self.assertTrue(m.sync(e, 3)["ok"])
        self.assertEqual([o["triggerPrice"] for o in ex.algo.values()], ["80.00"])
        e.orders = []
        self.assertTrue(m.sync(e, 4)["ok"])
        self.assertEqual((ex.pos["SOLUSDC"], ex.algo), (D(0), {}))

    def test_protection_failure_closes_and_kills(self):
        ex, e = FakeExchange(fail_stop=True), Engine()
        e.orders = [order()]
        m = self.mirror(ex)
        m.sync(e, 1)
        self.assertEqual(ex.pos["SOLUSDC"], D(0))
        self.assertTrue(m.kill.active)
        self.assertIn(("critical", "kill_switch"), self.alerts)
        self.assertEqual(m.sync(e, 2), {"skipped": "KILL"})

    def test_unknown_exchange_position_trips_after_two_checks(self):
        ex = FakeExchange()
        ex.pos["ETHUSDC"] = D("1")
        m = self.mirror(ex)
        self.assertFalse(m.check()["ok"])
        self.assertFalse(m.kill.active)
        self.assertFalse(m.check()["ok"])
        self.assertTrue(m.kill.active)

    def test_auth_failure_alerts(self):
        ex, e = FakeExchange(auth=True), Engine()
        e.orders = [order()]
        self.mirror(ex).sync(e, 1)
        self.assertIn(("critical", "auth"), self.alerts)


class IntervalTests(MirrorTests):
    def test_reconcile_throttled_when_nothing_changes(self):
        ex, e = FakeExchange(), Engine()
        e.orders = [order()]
        m = self.mirror(ex)
        self.assertNotIn("skipped", m.sync(e, 1_000))      # entry placed -> immediate check
        self.assertEqual(m.sync(e, 3_000)["skipped"], "INTERVAL")
        self.assertNotIn("skipped", m.sync(e, 40_000))     # 30s elapsed -> check
