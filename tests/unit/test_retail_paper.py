from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import json
import tempfile
import unittest

from quantos.application.retail_trader import RetailPaperEngine
from quantos.domain.alpha.hft import HftSide
from quantos.domain.alpha.retail import RetailDirectionalAlpha, RetailSignal
from quantos.domain.evaluation.retail import RetailEvaluation
from quantos.domain.execution.retail_paper import RetailPaperAccount
from quantos.domain.risk.retail import (
    RetailRiskDecision, RetailRiskEngine, RetailRiskPolicy,
)
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.domain.risk.hft import HftFeeSchedule
from quantos.infrastructure.storage.retail_paper import (
    RetailPaperStateError, RetailPaperStateStore,
)
from quantos.interfaces.retail import _json
from tests.unit.test_hft_paper import at, book, delta, feature, fees, rules


def signal(side=HftSide.BUY, expected="30") -> RetailSignal:
    return RetailSignal("signal-1", side, D(expected), D(expected), 101,
                        2_000_000_000, 4_000_000_000)


def depth(target, *, final: int, ns: int, bids=(), asks=()):
    item = replace(delta(
        first=final, final=final, previous=final - 1,
        bids=bids, asks=asks, second=final,
    ), received_monotonic_ns=ns)
    target.apply(item)
    return item


class RetailPaperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = RetailRiskPolicy()
        self.account = RetailPaperAccount(fees(), self.policy)
        self.book = book()

    def _entry(self, quantity=".05", visible=None):
        decision = RetailRiskDecision(True, "approved", D(quantity), D("13"), D(".3"))
        order = self.account.submit_entry(decision, signal(), at(2), 2_000_000_000, D("101"))
        if visible is not None:
            asks = ((D("101"), D(visible)),)
        else:
            asks = ()
        before = depth(self.book, final=102, ns=2_099_999_999, asks=asks)
        self.assertIsNone(self.account.on_depth(self.book, before.received_at, before.received_monotonic_ns))
        self.assertEqual(order.remaining, D(quantity))
        filled = depth(self.book, final=103, ns=2_100_000_000)
        fill = self.account.on_depth(self.book, filled.received_at, filled.received_monotonic_ns)
        return order, fill

    def _exit(self, *, bid="103", ask="104", reason="horizon_expired"):
        order = self.account.submit_exit(reason, at(4), 4_000_000_000, self.book.last_update_id)
        bids = ((D("100"), D("0")), (D(bid), D("10")))
        asks = tuple(
            (price, D("0")) for price in (D("101"), D("102"))
            if price != D(ask)
        ) + ((D(ask), D("10")),)
        item = depth(self.book, final=self.book.last_update_id + 1,
                     ns=4_100_000_000, bids=bids, asks=asks)
        fill = self.account.on_depth(self.book, item.received_at, item.received_monotonic_ns)
        return order, fill

    def test_profitable_trade_and_signal_order_attribution(self):
        entry_order, entry_fill = self._entry()
        exit_order, exit_fill = self._exit()
        self.assertIsNotNone(entry_fill)
        self.assertIsNotNone(exit_fill)
        self.assertGreater(exit_fill.net_pnl, 0)
        self.assertGreater(self.account.balance, D("200"))
        self.assertEqual(entry_fill.signal_id, exit_fill.signal_id)
        self.assertEqual(entry_fill.order_id, entry_order.order_id)
        self.assertEqual(exit_fill.order_id, exit_order.order_id)
        self.assertEqual(self.account.realized_net_pnl,
                         self.account.balance - D("200"))
        rendered = json.loads(_json({"event": "retail_fill", "fill": exit_fill}))
        self.assertEqual(rendered["fill"]["signal_id"], "signal-1")
        self.assertEqual(rendered["fill"]["net_pnl"], str(exit_fill.net_pnl))

    def test_losing_trade_and_spread_slippage_loss(self):
        self._entry()
        _, losing = self._exit(bid="99", ask="101")
        self.assertLess(losing.net_pnl, 0)
        self.assertLess(self.account.balance, D("200"))
        flat = RetailPaperAccount(fees(), self.policy)
        target = book()
        approved = RetailRiskDecision(True, "approved", D(".05"), D("13"), D(".3"))
        flat.submit_entry(approved, signal(), at(2), 2_000_000_000, D("101"))
        item = depth(target, final=102, ns=2_100_000_000)
        flat.on_depth(target, item.received_at, item.received_monotonic_ns)
        flat.submit_exit("horizon_expired", at(4), 4_000_000_000, target.last_update_id)
        item = depth(target, final=103, ns=4_100_000_000)
        fill = flat.on_depth(target, item.received_at, item.received_monotonic_ns)
        self.assertLess(fill.net_pnl, 0)

    def test_partial_liquidity_fill_and_ack_boundary(self):
        order, fill = self._entry(visible=".01")
        self.assertEqual(fill.quantity, D(".01"))
        self.assertEqual(order.remaining, D(".04"))
        self.assertEqual(self.account.position.quantity, D(".01"))
        self.assertIsNone(self.account.on_depth(self.book, at(3), 2_100_000_000))
        unchanged = depth(self.book, final=104, ns=2_200_000_000,
                          bids=((D("99"), D("5")),))
        self.assertIsNone(self.account.on_depth(
            self.book, unchanged.received_at, unchanged.received_monotonic_ns
        ))
        item = depth(self.book, final=105, ns=2_300_000_000,
                     asks=((D("101"), D(".02")),))
        next_fill = self.account.on_depth(self.book, item.received_at, item.received_monotonic_ns)
        self.assertEqual(next_fill.quantity, D(".01"))
        self.assertEqual(order.remaining, D(".03"))

    def test_adverse_price_before_ack_cancels_entry(self):
        decision = RetailRiskDecision(True, "approved", D(".05"), D("13"), D(".3"))
        self.account.submit_entry(
            decision, signal(), at(2), 2_000_000_000, D("101")
        )
        item = depth(
            self.book, final=102, ns=2_100_000_000,
            asks=((D("101"), D("0")), (D("102"), D("5"))),
        )
        self.assertIsNone(self.account.on_depth(
            self.book, item.received_at, item.received_monotonic_ns
        ))
        self.assertIsNone(self.account.pending)
        self.assertEqual(self.account.fills, [])
        self.assertEqual(self.account.lifecycle[-1]["reason"],
                         "adverse_price_before_fill")

    def test_failed_risk_cannot_submit_order(self):
        risk = RetailRiskEngine(self.policy, fees(), rules())
        features = feature()
        decision = risk.evaluate(signal(expected="1"), features,
                                 D("200"), D("200"), False)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "non_economic_after_full_cost")
        self.assertGreater(decision.hurdle_bps, D("13"))
        with self.assertRaisesRegex(ValueError, "Risk approval required"):
            self.account.submit_entry(decision, signal(expected="1"), at(2), 2_000_000_000, D("101"))
        self.assertEqual(self.account.orders_submitted, 0)

    def test_latency_cost_and_stale_feed_fail_closed(self):
        risk = RetailRiskEngine(self.policy, fees(), rules())
        fresh = risk.evaluate(signal(expected="15"), feature(), D("200"), D("200"), False)
        delayed = risk.evaluate(
            signal(expected="15"), replace(feature(), feed_latency_ms=D("1000")),
            D("200"), D("200"), False,
        )
        stale = risk.evaluate(
            signal(expected="30"), replace(feature(), feed_latency_ms=D("1501")),
            D("200"), D("200"), False,
        )
        self.assertTrue(fresh.approved)
        self.assertFalse(delayed.approved)
        self.assertGreater(delayed.hurdle_bps, fresh.hurdle_bps)
        self.assertEqual(stale.reason, "stale_feed_latency")

    def test_reduced_fee_schedule_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "fee below approved cost floor"):
            RetailRiskEngine(
                self.policy, HftFeeSchedule(D("0"), D("0")), rules()
            )

    def test_compounding_after_profit_and_reduced_sizing_after_loss(self):
        risk = RetailRiskEngine(self.policy, fees(), rules())
        features = feature()
        baseline = risk.evaluate(signal(), features, D("200"), D("200"), False)
        self._entry()
        self._exit()
        profit = risk.evaluate(signal(), features, self.account.balance,
                               self.account.peak_equity, False)
        self.assertGreater(profit.quantity, baseline.quantity)
        losing = RetailPaperAccount(fees(), self.policy)
        self.account = losing
        self.book = book()
        self._entry()
        self._exit(bid="99", ask="101")
        self.assertLess(losing.balance, D("200"))
        reduced = risk.evaluate(signal(), features, losing.balance,
                                losing.peak_equity, False)
        self.assertLess(reduced.quantity, baseline.quantity)

    def test_signal_requires_causal_response_flow_and_imbalance(self):
        alpha = RetailDirectionalAlpha()
        self.assertIsNone(alpha.observe(feature(), 1_000_000_000))
        strong = replace(feature(), mid_price=D("101"), static_obi=D(".3"),
                         signed_trade_flow=D("2"))
        found = alpha.observe(strong, 31_000_000_000)
        self.assertEqual(found.side, HftSide.BUY)
        self.assertGreater(found.expected_move_bps, D("2"))
        self.assertIsNone(alpha.observe(replace(strong, signed_trade_flow=D("-1")),
                                        31_100_000_000))

    def test_invalid_data_disarms_and_cancels_pending_without_fill(self):
        config = load_hft_config(Path("configs/futures_hft_200_paper.toml"))
        engine = RetailPaperEngine(config, rules(), self.account,
                                   RetailEvaluation(), self.policy,
                                   processing_clock_ns=lambda: 2_200_000_000)
        engine.book = self.book
        engine.disarmed = False
        approved = RetailRiskDecision(True, "approved", D(".05"), D("13"), D(".3"))
        self.account.submit_entry(approved, signal(), at(2), 2_000_000_000, D("101"))
        bad = replace(delta(first=104, final=104, previous=102),
                      received_monotonic_ns=2_200_000_000)
        with self.assertRaisesRegex(Exception, "sequence gap"):
            engine.on_event(bad, feed_latency_ms=D("1"), clock_healthy=True)
        self.assertTrue(engine.disarmed)
        self.assertIsNone(self.account.pending)
        self.assertEqual(self.account.fills, [])

    def test_stale_feed_latency_blocks_pending_fill_before_depth_matching(self):
        config = load_hft_config(Path("configs/futures_hft_200_paper.toml"))
        engine = RetailPaperEngine(config, rules(), self.account,
                                   RetailEvaluation(), self.policy,
                                   processing_clock_ns=lambda: 2_200_000_000)
        engine.book = self.book
        engine.disarmed = False
        approved = RetailRiskDecision(True, "approved", D(".05"), D("13"), D(".3"))
        self.account.submit_entry(approved, signal(), at(2), 2_000_000_000, D("101"))
        item = replace(delta(first=102, final=102, previous=101),
                       received_monotonic_ns=2_200_000_000)
        self.assertIsNone(engine.on_event(
            item, feed_latency_ms=D("1501"), clock_healthy=True,
        ))
        self.assertTrue(engine.disarmed)
        self.assertEqual(self.account.fills, [])
        self.assertIsNone(self.account.pending)

    def test_new_state_restart_cancels_pending_once_and_rejects_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RetailPaperStateStore(Path(directory) / "retail.json")
            store.reset("identity", fees(), self.policy)
            account, _ = store.load("identity", fees(), self.policy)
            decision = RetailRiskDecision(True, "approved", D(".05"), D("13"), D(".3"))
            account.submit_entry(decision, signal(), at(2), 2_000_000_000, D("101"))
            store.save("identity", account, {})
            first, _ = store.load("identity", fees(), self.policy)
            second, _ = store.load("identity", fees(), self.policy)
            self.assertEqual(first.orders_cancelled, 1)
            self.assertEqual(second.orders_cancelled, 1)
            self.assertIsNone(second.pending)
            self.assertEqual(second.starting_equity, D("200"))
            with self.assertRaisesRegex(RetailPaperStateError, "identity"):
                store.load("different", fees(), self.policy)


if __name__ == "__main__":
    unittest.main()

class RetailStabilityTests(unittest.TestCase):
    setUp = RetailPaperTests.setUp
    _entry = RetailPaperTests._entry
    def test_signal_diagnostics_and_reconnect_identity(self):
        first = RetailDirectionalAlpha()
        first.observe(feature(), 1_000_000_000)
        self.assertEqual(first.last_reason, "warmup_30s")
        first.observe(feature(), 31_000_000_000)
        self.assertEqual(first.last_reason, "response_below_2bps")
        strong = replace(feature(), mid_price=D("101"), static_obi=D(".3"), signed_trade_flow=D("2"))
        a = first.observe(strong, 31_100_000_000)
        second = RetailDirectionalAlpha()
        second.observe(feature(), 1_000_000_000)
        b = second.observe(replace(strong, book_update_id=999), 31_100_000_000)
        self.assertNotEqual(a.signal_id, b.signal_id)
        self.assertEqual(first.last_reason, "confirmed")

    def test_expiry_boundary_never_fills(self):
        decision = RetailRiskDecision(True, "approved", D(".05"), D("13"), D(".3"))
        self.account.submit_entry(decision, signal(), at(2), 2_000_000_000, D("101"))
        item = depth(self.book, final=102, ns=4_000_000_000)
        self.assertIsNone(self.account.on_depth(self.book, item.received_at, item.received_monotonic_ns))
        self.assertEqual(self.account.lifecycle[-1]["reason"], "order_expired")

    def test_revisited_price_cannot_reuse_consumed_liquidity(self):
        order, fill = self._entry(visible=".01")
        # A better level appears and disappears; the original level never replenished.
        item = depth(self.book, final=104, ns=2_200_000_000, asks=((D("100.99"), D(".01")),))
        self.account.on_depth(self.book, item.received_at, item.received_monotonic_ns)
        item = depth(self.book, final=105, ns=2_300_000_000, asks=((D("100.99"), D("0")),))
        self.assertIsNone(self.account.on_depth(self.book, item.received_at, item.received_monotonic_ns))
        self.assertEqual(order.remaining, D(".03"))
