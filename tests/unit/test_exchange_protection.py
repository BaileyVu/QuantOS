import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from quantos.live.bridge import ExchangeMirror
from quantos.live.client import SignedClient
from quantos.live.watchdog import protect_once
from quantos.paper.types import D
from quantos.paper.service import build_watchdog_plist
from tests.unit.test_live_bridge import FakeExchange, FillsExchange, BookingEngine, order


class PaginationTests(unittest.TestCase):
    def test_order_specific_pages_deduplicate_and_advance(self):
        c = SignedClient("dry-run", None)
        calls = []
        def page(symbol, start_time=None, **kw):
            calls.append((start_time, kw))
            return [{"id": i, "orderId": 9} for i in ([1, 2] if kw["from_id"] is None else [3])]
        c.user_trades = page
        self.assertEqual([r["id"] for r in c.order_trades("SOLUSDC", 9, 100, page_size=2)], [1, 2, 3])
        self.assertEqual(calls[1], (None, {"order_id": 9, "from_id": 3, "limit": 2}))

    def test_nonadvancing_and_unrelated_pages_fail(self):
        c = SignedClient("dry-run", None)
        c.user_trades = lambda *a, **kw: [{"id": 1, "orderId": 9}]
        with self.assertRaisesRegex(ValueError, "advance"):
            c.order_trades("SOLUSDC", 9, page_size=1)
        with self.assertRaisesRegex(ValueError, "another order"):
            c.order_trades("SOLUSDC", 10)


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.ex, self.e = FillsExchange(), BookingEngine()
        self.e.orders = [order()]
        self.m = ExchangeMirror(self.ex, "r", self.state, lambda *a: None)

    def test_tp_is_reduce_only_expected_and_cancelled_after_close(self):
        self.e.orders[0]["signal"]["take_profit"] = "90"
        self.assertTrue(self.m.sync(self.e, 1000)["ok"])
        self.assertEqual({a["orderType"] for a in self.ex.algo.values()}, {"STOP_MARKET", "TAKE_PROFIT_MARKET"})
        tp = next(a for a in self.ex.algo.values() if a["orderType"] == "TAKE_PROFIT_MARKET")
        self.assertTrue(tp["reduceOnly"])
        self.e.orders = []
        self.m.sync(self.e, 3000)
        self.assertEqual(self.ex.algo, {})

    def test_unrelated_exit_trade_cannot_pollute_books(self):
        self.m.sync(self.e, 1000)
        self.ex.price = D(1)
        self.ex.fill("SELL", D("0.05"), 999)
        self.ex.price = D(82)
        self.e.orders = []
        self.m.sync(self.e, 3000)
        self.assertEqual(self.e.booked[-1][1][0], D(82))

    def test_failed_close_and_flatten_retain_existing_protection(self):
        self.m.sync(self.e, 1000)
        self.ex.reject_reduce = True
        self.e.orders = []
        self.m.sync(self.e, 3000)
        self.assertTrue(self.m.kill.active)
        self.assertFalse(self.m.flat_confirmed)
        self.assertTrue(self.ex.algo)
        self.assertEqual(self.ex.pos["SOLUSDC"], D(".05"))

    def test_settlement_never_marks_simulated_values_as_verified(self):
        self.ex.hide = True
        self.m.sync(self.e, 1000)
        m = self.m.book["a" * 32]
        for n in range(15): self.m._settle(self.e, "a" * 32, m, 1_000_000 * (n + 1))
        self.assertNotEqual(m["booked"], "all")
        self.assertTrue(m["settlement_pending"])
        self.assertIn("a" * 32, self.m.settle_retry)

    def test_forward_clock_jump_expires_position_and_cancels_brackets(self):
        self.e.orders[0]["signal"].update(maximum_hold_seconds=10, take_profit="90")
        self.m.sync(self.e, 1000)
        (self.state / "heartbeat.json").write_text(json.dumps({"updated_ms": 1000}))
        result = protect_once(self.state, self.ex, now_ms=20_000)
        self.assertEqual(result, {"closed": ["a" * 32], "errors": {}})
        self.assertEqual(self.ex.pos["SOLUSDC"], 0)
        self.assertEqual(self.ex.algo, {})
        self.assertFalse(self.m.kill.active)  # max-hold alone does not kill other trades

    def test_backward_clock_jump_engages_kill(self):
        self.m.sync(self.e, 1000)
        (self.state / "heartbeat.json").write_text(json.dumps({"updated_ms": 20_000}))
        protect_once(self.state, self.ex, now_ms=2000)
        self.assertTrue(self.m.kill.active)
        self.assertEqual(self.ex.pos["SOLUSDC"], 0)

    def test_terminal_zero_fill_is_voided_on_engine_recovery(self):
        self.m.sync(self.e, 1000)
        record = self.m.book["a" * 32]
        record.update(state="PENDING", pending_since=1000)
        self.m._save()
        entry = next(iter(self.ex.orders.values()))
        entry.update(status="EXPIRED", executedQty="0")
        self.ex.pos["SOLUSDC"], self.ex.algo = D(0), {}
        self.assertEqual(protect_once(self.state, self.ex, now_ms=200_000)["errors"], {})
        voided, closed = [], []
        self.e.void_entry = lambda *args: voided.append(args)
        self.e.close_external = lambda *args: closed.append(args)
        self.m.sync(self.e, 201_000)
        self.assertEqual(voided[0][0], "a" * 32)
        self.assertEqual(closed, [])
        self.assertEqual(self.m.book["a" * 32]["state"], "VOID")

    def test_kill_settles_exact_real_exit_and_retains_id_across_restart(self):
        self.m.sync(self.e, 1000)
        self.ex.price = D("81.25")
        self.m._halt("test kill")
        # Simulate lost final mirror write after the close. Recover its stable cid.
        self.m.book["a" * 32].pop("exit_orders")
        self.m._save()
        closed = []
        self.e.close_external = lambda *args: closed.append(args)
        fresh = ExchangeMirror(self.ex, "r", self.state, lambda *a: None)
        fresh.sync(self.e, 2000)
        self.assertEqual(fresh.book["a" * 32]["booked"], "all")
        self.assertEqual(self.e.booked[-1], ("exit", (D("81.25"), D("0.00203125"))))
        self.assertEqual(closed[0][2], "KILL_EXIT")

    def test_missing_position_response_does_not_confirm_flat(self):
        self.m.sync(self.e, 1000)
        self.ex.position_risk = lambda symbol=None: []
        self.assertFalse(self.m._flat_on_exchange("SOLUSDC"))
        result = protect_once(self.state, self.ex, now_ms=200_000)
        self.assertIn("a" * 32, result["errors"])
        self.assertTrue(self.ex.algo)

    def test_process_dies_after_entry_before_stop_and_restart_adopts_once(self):
        # Actual subprocess exit, not an exception swallowed by the order wrapper.
        program = '''
import json, os, sys
from pathlib import Path
from tests.unit.test_live_bridge import FakeExchange, Engine, order
from quantos.live.bridge import ExchangeMirror
ex, e = FakeExchange(), Engine()
e.orders = [order()]
original = ex.new_order
def crash(p):
    result = original(p)
    Path(sys.argv[1], "fake-exchange.json").write_text(json.dumps({"orders": ex.orders, "pos": ex.pos}, default=str))
    os._exit(23)
ex.new_order = crash
ExchangeMirror(ex, "r", Path(sys.argv[1]), lambda *a: None).sync(e, 1000)
'''
        child = subprocess.run([sys.executable, "-c", program, str(self.state)], check=False)
        self.assertEqual(child.returncode, 23)
        durable = json.loads((self.state / "mirror.json").read_text())
        self.assertEqual(durable["a" * 32]["state"], "PENDING")
        saved = json.loads((self.state / "fake-exchange.json").read_text())
        ex = FakeExchange()
        ex.orders, ex.pos = saved["orders"], {s: D(q) for s, q in saved["pos"].items()}
        fresh = ExchangeMirror(ex, "r", self.state, lambda *a: None)
        fresh.sync(self.e, 3000)
        self.assertEqual(fresh.book["a" * 32]["state"], "OPEN")
        self.assertEqual((len(ex.orders), ex.algo_posts), (1, 1))

    def test_watchdog_closes_without_main_runtime_after_process_death(self):
        program = '''
import json, os, sys
from pathlib import Path
from tests.unit.test_live_bridge import FakeExchange, Engine, order
from quantos.live.bridge import ExchangeMirror
ex, e = FakeExchange(), Engine()
e.orders = [order()]
original = ex.new_order
def crash(p):
    result = original(p)
    Path(sys.argv[1], "fake-exchange.json").write_text(json.dumps({"orders": ex.orders, "pos": ex.pos}, default=str))
    os._exit(23)
ex.new_order = crash
ExchangeMirror(ex, "r", Path(sys.argv[1]), lambda *a: None).sync(e, 1000)
'''
        child = subprocess.run([sys.executable, "-c", program, str(self.state)], check=False)
        self.assertEqual(child.returncode, 23)
        saved = json.loads((self.state / "fake-exchange.json").read_text())
        ex = FakeExchange()
        ex.orders, ex.pos = saved["orders"], {s: D(q) for s, q in saved["pos"].items()}
        # No main runtime or engine recovery: only independent watchdog code runs.
        result = protect_once(self.state, ex, now_ms=200_000)
        self.assertEqual(result, {"closed": ["a" * 32], "errors": {}})
        self.assertEqual(ex.pos["SOLUSDC"], 0)
        self.assertEqual(len(ex.orders), 2)

    def test_watchdog_plist_is_separate_and_demo_only(self):
        p = build_watchdog_plist(Path(sys.executable), self.state, self.state, execution="testnet")
        self.assertIn("quantos.live.watchdog", p["ProgramArguments"])
        self.assertEqual(p["StartInterval"], 15)
        with self.assertRaises(ValueError):
            build_watchdog_plist(Path(sys.executable), self.state, self.state, execution="mainnet")
