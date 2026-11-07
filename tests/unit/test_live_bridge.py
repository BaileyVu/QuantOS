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
        self.algo_posts = 0

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
        self.algo_posts += 1
        if self.fail_stop: raise BinanceError(-2021, "would trigger", 400)
        close = str(p.get("closePosition", "")).lower() == "true"
        if close and any(o["closePosition"] and o["symbol"] == p["symbol"] and o["side"] == p["side"]
                         for o in self.algo.values()):
            raise BinanceError(-4130, "An open stop or take profit order with GTE and closePosition in the "
                                      "direction is existing.", 400)
        self.algo[p["clientAlgoId"]] = o = {"clientAlgoId": p["clientAlgoId"], "symbol": p["symbol"],
                                            "orderType": p["type"], "side": p["side"], "closePosition": close,
                                            "reduceOnly": not close, "triggerPrice": str(p["triggerPrice"])}
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

    def test_trailing_stop_moves_twice_without_closeposition_conflict(self):
        ex, e = FakeExchange(), Engine()
        m = self.mirror(ex)
        for t, stop in enumerate(("75.00", "78.00", "80.00"), 1):
            e.orders = [order(stop=stop)]
            self.assertTrue(m.sync(e, t * 1000)["ok"])
            self.assertEqual([o["triggerPrice"] for o in ex.algo.values()], [stop])
        self.assertFalse([a for a in self.alerts if a[1].startswith("stop:")])

    def test_failed_stop_update_backs_off_instead_of_retrying_every_sync(self):
        ex, e = FakeExchange(), Engine()
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 0)
        ex.fail_stop, posts = True, ex.algo_posts
        e.orders = [order(stop="80.00")]
        for t in range(0, 30_000, 2000):  # 15 syncs inside the first backoff window
            m.sync(e, 1000 + t)
        self.assertEqual(ex.algo_posts - posts, 1)
        self.assertEqual([o["triggerPrice"] for o in ex.algo.values()], ["75.00"])  # old stop kept
        ex.fail_stop = False
        m.sync(e, 31_001)
        self.assertEqual([o["triggerPrice"] for o in ex.algo.values()], ["80.00"])
        self.assertEqual(self.alerts.count(("recovered", "stop:" + "a" * 32)), 1)

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


class FillsExchange(FakeExchange):
    """Keeps a userTrades list; market orders fill at `price` with 0.05% commission in USDC."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.trades, self.price, self.trade_calls, self.next_id, self.hide = [], D("80.00"), 0, 100, False
        self.reject_reduce, self.t0 = False, 1000

    def new_order(self, p):
        if p.get("reduceOnly") and self.reject_reduce:
            raise BinanceError(-2022, "ReduceOnly Order is rejected.", 400)
        o = super().new_order(p)
        self.next_id += 1
        o["orderId"] = self.next_id
        self.fill(p["side"], D(p["quantity"]), self.next_id)
        return o

    def fill(self, side, qty, order_id):
        self.trades.append({"orderId": order_id, "side": side, "price": str(self.price), "qty": str(qty),
                            "commission": str(self.price * qty * D("0.0005")), "commissionAsset": "USDC",
                            "time": self.t0 + len(self.trades)})

    def user_trades(self, symbol, start_time=None):
        self.trade_calls += 1
        if self.hide: return []
        return [t for t in self.trades if start_time is None or t["time"] >= start_time]


class BookingEngine(Engine):
    def __init__(self):
        super().__init__()
        self.booked = []

    def book_real_fills(self, oid, now_ms, entry=None, exit=None):
        self.booked.append(("entry", entry) if entry else ("exit", exit))
        return {"has_exit": exit is not None}


class RealFillTests(unittest.TestCase):
    setUp, tearDown, mirror = MirrorTests.setUp, MirrorTests.tearDown, MirrorTests.mirror

    def test_entry_and_exit_booked_from_exchange_fills_once(self):
        ex, e = FillsExchange(), BookingEngine()
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 1000)
        self.assertEqual(e.booked, [("entry", (D("80.00"), D("0.0020")))])
        for t in range(2000, 20_000, 2000):
            m.sync(e, t)
        self.assertEqual(ex.trade_calls, 1)  # no polling while the position is open
        ex.price = D("82.50")
        e.orders = []
        m.sync(e, 30_000)
        self.assertEqual(e.booked[-1], ("exit", (D("82.50"), D("0.0020625"))))
        m.sync(e, 40_000)
        self.assertEqual((ex.trade_calls, len(e.booked)), (2, 2))

    def test_exchange_stop_filled_first_is_not_a_kill_and_books_the_stop_price(self):
        ex, e = FillsExchange(), BookingEngine()
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 1000)
        ex.price = D("75.10")                         # stop triggered on Binance
        ex.fill("SELL", D("0.05"), 999)
        ex.pos["SOLUSDC"], ex.algo, ex.reject_reduce = D(0), {}, True
        e.orders = []
        m.sync(e, 5000)
        self.assertFalse(m.kill.active)
        self.assertEqual(m.book["a" * 32]["state"], "CLOSED")
        self.assertEqual(e.booked[-1][1][0], D("75.10"))

    def test_fills_not_visible_yet_retry_with_backoff(self):
        ex, e = FillsExchange(), BookingEngine()
        m = self.mirror(ex)
        ex.hide = True
        e.orders = [order()]
        m.sync(e, 1000)
        for t in range(1000, 5000, 1000):
            m.sync(e, 1000 + t)
        self.assertEqual((ex.trade_calls, e.booked), (1, []))
        ex.hide = False
        m.sync(e, 6001)
        self.assertEqual(len(e.booked), 1)

    def test_account_stream_flat_position_closes_paper_and_books_the_stop(self):
        ex, e = FillsExchange(), BookingEngine()
        closed = []
        e.close_external = lambda oid, now, reason: closed.append((oid, reason)) or True
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 1000)
        ex.price = D("75.10")                         # stop filled on Binance
        ex.fill("SELL", D("0.05"), 999)
        ex.pos["SOLUSDC"], ex.algo, ex.reject_reduce = D(0), {}, True
        m.sync(e, 3000, [{"e": "ACCOUNT_UPDATE", "a": {"P": [{"s": "SOLUSDC", "pa": "0"}]}}])
        self.assertEqual(closed, [("a" * 32, "EXCHANGE_CLOSED")])
        self.assertFalse(m.kill.active)
        e.orders = []                                 # paper is now CLOSING, so not live
        m.sync(e, 5000)
        self.assertEqual(m.book["a" * 32]["state"], "CLOSED")
        self.assertEqual(e.booked[-1][1][0], D("75.10"))

    def test_real_engine_books_inside_the_runtime_transaction(self):
        """End to end with the real engine and journal, called the way runtime.py calls it."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from test_thin_book import T, INS as AAVE, quote, signal
        from quantos.paper.engine import PaperEngine
        from quantos.paper.journal import Journal
        from quantos.paper.risk import Policy
        from quantos.paper.types import Trade
        j = Journal(Path(self.dir.name) / "journal_p1.db", "p1", "synthetic")
        self.addCleanup(j.close)
        eng = PaperEngine(j, Policy())
        eng.recover(T); eng.connection(True, T)
        eng.propose(signal(), AAVE, quote(), T)
        eng.trade(Trade("AAVEUSDC", 1, T + 1000, D("99.99"), D("1000"), True), T + 1000)
        ex = FillsExchange()
        ex.price, ex.t0 = D("100.07"), T + 2000
        m = self.mirror(ex)
        with j.transaction():
            m.sync(eng, T + 2000)
        self.assertEqual(D(j.orders()[0]["average"]), D("100.07"))
        self.assertEqual(j.db.execute("SELECT COUNT(*) FROM events WHERE kind='BOOKS_CORRECTION'").fetchone()[0], 1)


class FlakyExchange(FillsExchange):
    """Entry reaches Binance but the reply is lost, and lookups fail until `down` clears."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.lose_reply, self.down, self.reject_leverage, self.drop_entry = True, True, False, False

    def set_leverage(self, s, lev):
        if self.reject_leverage: raise BinanceError(-4028, "Leverage is not valid", 400)

    def new_order(self, p):
        if self.drop_entry and not p.get("reduceOnly"):
            from quantos.live.client import AmbiguousOrderState
            raise AmbiguousOrderState("timeout")      # never reached Binance
        o = super().new_order(p)
        if self.lose_reply and not p.get("reduceOnly"):
            from quantos.live.client import AmbiguousOrderState
            raise AmbiguousOrderState("timeout")
        return o

    def get_order(self, s, cid):
        if self.down: raise ConnectionError("network down")
        if cid not in self.orders:
            from quantos.live.client import UnknownOrder
            raise UnknownOrder(-2013, "Order does not exist.", 400)
        return self.orders[cid]


class VoidingEngine(BookingEngine):
    def __init__(self):
        super().__init__()
        self.voided = []
    def void_entry(self, oid, now, reason): self.voided.append(oid); return True


class EntrySafetyTests(unittest.TestCase):
    setUp, tearDown, mirror = MirrorTests.setUp, MirrorTests.tearDown, MirrorTests.mirror

    def test_lost_reply_entry_is_resolved_then_protected(self):
        ex, e = FlakyExchange(), VoidingEngine()
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 1000)
        self.assertEqual(m.book["a" * 32]["state"], "PENDING")
        self.assertEqual((ex.pos["SOLUSDC"], ex.algo), (D("0.05"), {}))   # filled, not yet protected
        ex.down = False
        m.sync(e, 3000)
        self.assertEqual(m.book["a" * 32]["state"], "OPEN")
        self.assertEqual([o["triggerPrice"] for o in ex.algo.values()], ["75.00"])
        self.assertFalse(m.kill.active)

    def test_entry_that_never_reached_binance_is_voided(self):
        ex, e = FlakyExchange(), VoidingEngine()
        ex.drop_entry = True
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 1000)
        ex.down = False
        m.sync(e, 3000)
        self.assertEqual((m.book["a" * 32]["state"], e.voided), ("FAILED", ["a" * 32]))
        self.assertEqual(ex.pos.get("SOLUSDC", D(0)), D(0))

    def test_unresolvable_entry_kills_after_the_limit(self):
        ex, e = FlakyExchange(), VoidingEngine()
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 1000)
        m.sync(e, 60_000)
        self.assertFalse(m.kill.active)
        m.sync(e, 95_000)
        self.assertTrue(m.kill.active)

    def test_rejected_leverage_never_enters(self):
        ex, e = FlakyExchange(), VoidingEngine()
        ex.reject_leverage = True
        m = self.mirror(ex)
        e.orders = [order()]
        m.sync(e, 1000)
        self.assertEqual((e.voided, ex.orders), (["a" * 32], {}))


class SharedAccountTests(unittest.TestCase):
    setUp, tearDown, mirror = MirrorTests.setUp, MirrorTests.tearDown, MirrorTests.mirror

    def test_alt_ml_usdt_positions_neither_fail_reconcile_nor_get_flattened(self):
        ex, e = FakeExchange(), Engine()
        m = self.mirror(ex)
        e.orders = [order()]
        ex.pos["NEARUSDT"] = D("-10")                 # the alt-ML executor's short on the same account
        ex.algo["mlalts-sl"] = {"clientAlgoId": "mlalts-sl", "symbol": "NEARUSDT", "orderType": "STOP_MARKET",
                                "side": "BUY", "closePosition": False, "reduceOnly": True, "triggerPrice": "5"}
        for t in (1000, 40_000, 80_000):
            self.assertTrue(m.sync(e, t)["ok"])
        m._halt("test")
        self.assertEqual(ex.pos["SOLUSDC"], D(0))
        self.assertEqual(ex.pos["NEARUSDT"], D("-10"))
        self.assertIn("mlalts-sl", ex.algo)
        self.assertTrue(m.flat_confirmed)
