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


class Unreachable(FakeExchange):
    """Exchange whose reads fail with a transport error while `down` is set."""
    down = True

    def open_orders(self, symbol=None):
        if self.down: raise ConnectionError("GET /fapi/v1/openOrders: transport failure URLError: timeout")
        return []


class TransientTests(MirrorTests):
    def test_transport_blips_hold_entries_then_clear(self):
        ex, e = Unreachable(), Engine()
        m = self.mirror(ex)
        for now in (1_000, 31_000):
            result = m.check(now)
            self.assertTrue(result["transient"])
        self.assertFalse(m.kill.active)
        self.assertTrue(m.hold)
        self.assertIn(("warn", "exchange_hold"), self.alerts)
        ex.down = False
        self.assertTrue(m.sync(e, 60_000)["ok"])
        self.assertFalse(m.hold)
        self.assertFalse(e.exchange_hold)
        self.assertIn(("recovered", "exchange_hold"), self.alerts)

    def test_hold_reaches_engine_and_retries_with_backoff(self):
        ex, e = Unreachable(), Engine()
        m = self.mirror(ex)
        m.sync(e, 40_000)
        self.assertTrue(e.exchange_hold)
        self.assertEqual(m.sync(e, 42_000)["skipped"], "INTERVAL")  # 5s backoff not yet up
        self.assertTrue(e.exchange_hold)
        self.assertTrue(m.sync(e, 45_500)["transient"])             # retried after 5s, not 30s

    def test_sustained_transport_failure_kills(self):
        ex = Unreachable()
        m = self.mirror(ex)
        for now in (0, 30_000, 60_000, 90_000):
            m.check(now)
        self.assertFalse(m.kill.active)                             # 4 checks but only 90s
        m.check(120_000)
        self.assertTrue(m.kill.active)
        self.assertIn(("critical", "kill_switch"), self.alerts)

    def test_many_fast_checks_alone_do_not_kill(self):
        m = self.mirror(Unreachable())
        for now in range(0, 60_000, 5_000):
            m.check(now)
        self.assertFalse(m.kill.active)

    def test_rate_limit_and_5xx_are_transient_auth_is_not(self):
        from quantos.live.bridge import is_transient_error
        from quantos.live.client import RateLimited
        self.assertTrue(is_transient_error(RateLimited(-1003, "rate limited", 429)))
        self.assertTrue(is_transient_error(BinanceError(-1, "bad gateway", 502)))
        self.assertTrue(is_transient_error(TimeoutError()))
        self.assertFalse(is_transient_error(BinanceError(-2015, "Invalid API-key", 401)))
        self.assertFalse(is_transient_error(ValueError("bad payload")))

    def test_mismatch_after_blip_still_kills_on_second_check(self):
        ex = Unreachable()
        m = self.mirror(ex)
        m.check(0)
        ex.down = False
        ex.pos["ETHUSDC"] = D("1")
        self.assertFalse(m.check(5_000)["ok"])
        self.assertFalse(m.hold)
        self.assertFalse(m.kill.active)
        m.check(35_000)
        self.assertTrue(m.kill.active)


class KillFlattenTests(MirrorTests):
    class Flaky(FakeExchange):
        down = False

        def position_risk(self, symbol=None):
            if self.down: raise ConnectionError("transport failure URLError: timeout")
            return super().position_risk(symbol)

    def held(self):
        ex, e = self.Flaky(), Engine()
        e.orders = [order()]
        m = self.mirror(ex)
        m.sync(e, 1)
        return ex, e, m

    def test_failed_flatten_retries_until_verified(self):
        ex, e, m = self.held()
        ex.down = True
        m._halt("test")
        self.assertFalse(m.flat_confirmed)
        self.assertIn(("critical", "kill_flatten"), self.alerts)
        self.assertEqual(ex.pos["SOLUSDC"], D("0.05"))
        self.assertEqual(m.sync(e, 40_000), {"skipped": "KILL", "flat": False})
        self.assertEqual(ex.pos["SOLUSDC"], D("0.05"))
        ex.down = False
        m.sync(e, 50_000)                                 # inside the 30s retry window
        self.assertFalse(m.flat_confirmed)
        self.assertEqual(m.sync(e, 70_000), {"skipped": "KILL"})
        self.assertEqual((ex.pos["SOLUSDC"], ex.algo), (D(0), {}))
        self.assertIn(("recovered", "kill_flatten"), self.alerts)
        self.assertEqual({v["state"] for v in m.book.values()}, {"CLOSED"})

    def test_restart_with_kill_file_flattens_open_exchange_position(self):
        ex, e, m = self.held()
        m.kill.engage("manual")                           # e.g. `touch KILL`, or restart during an outage
        fresh = self.mirror(ex)
        self.assertEqual(fresh.sync(e, 40_000), {"skipped": "KILL"})
        self.assertEqual(ex.pos["SOLUSDC"], D(0))

    def test_flat_book_closed_so_resume_does_not_rekill(self):
        ex, e, m = self.held()
        ex.pos["SOLUSDC"], ex.algo = D(0), {}             # closed by hand on the exchange
        m._halt("test")
        self.assertTrue(m.flat_confirmed)
        m.kill.path.unlink()
        e.orders = []
        fresh = self.mirror(ex)
        self.assertTrue(fresh.sync(e, 40_000)["ok"])
        self.assertFalse(fresh.kill.active)


class StopMoveTests(MirrorTests):
    class OneStop(FakeExchange):
        """Like Binance: a second closePosition stop on the same side is rejected with -4130."""
        fail_new, fail_restore, cancel_fail = False, False, False
        placed = 0

        def new_algo_order(self, p):
            self.placed += 1
            if self.algo: raise BinanceError(-4130, "An open stop or take profit order already exists", 400)
            if self.fail_new and str(p["triggerPrice"]) != "75.00": raise BinanceError(-2021, "would trigger", 400)
            if self.fail_restore: raise BinanceError(-2021, "would trigger", 400)
            return super().new_algo_order(p)

        def cancel_algo_order(self, cid):
            if self.cancel_fail: raise ConnectionError("transport failure")
            return super().cancel_algo_order(cid)

    def held(self):
        ex, e = self.OneStop(), Engine()
        e.orders = [order()]
        m = self.mirror(ex)
        m.sync(e, 1)
        e.orders = [order(stop="80.00")]
        return ex, e, m

    def triggers(self, ex): return [o["triggerPrice"] for o in ex.algo.values()]

    def test_move_cancels_first_and_exchange_stop_follows(self):
        ex, e, m = self.held()
        self.assertTrue(m.sync(e, 40_000)["ok"])
        self.assertEqual(self.triggers(ex), ["80.00"])
        self.assertFalse(m.kill.active)

    def test_moves_are_rate_limited(self):
        ex, e, m = self.held()
        m.sync(e, 40_000)
        count = ex.placed
        e.orders = [order(stop="81.00")]
        m.sync(e, 42_000)
        self.assertEqual((ex.placed, self.triggers(ex)), (count, ["80.00"]))
        m.sync(e, 50_000)
        self.assertEqual(self.triggers(ex), ["81.00"])

    def test_rejected_move_restores_old_stop_and_backs_off(self):
        ex, e, m = self.held()
        ex.fail_new = True
        m.sync(e, 40_000)
        self.assertEqual(self.triggers(ex), ["75.00"])
        self.assertFalse(m.kill.active)
        self.assertIn(("warn", f"stop:{'a' * 32}"), self.alerts)
        count = ex.placed
        for now in range(42_000, 70_000, 2_000):           # the old bug retried here every 2s
            m.sync(e, now)
        self.assertEqual(ex.placed, count)
        ex.fail_new = False
        m.sync(e, 70_000)
        self.assertEqual(self.triggers(ex), ["80.00"])

    def test_failed_cancel_keeps_old_stop(self):
        ex, e, m = self.held()
        ex.cancel_fail = True
        m.sync(e, 40_000)
        self.assertEqual(self.triggers(ex), ["75.00"])
        self.assertEqual(m.book["a" * 32]["stop"], "75.00")

    def test_unprotected_after_failed_restore_halts(self):
        ex, e, m = self.held()
        ex.fail_restore = True
        m.sync(e, 40_000)
        self.assertTrue(m.kill.active)
        self.assertIn(("critical", "kill_switch"), self.alerts)
