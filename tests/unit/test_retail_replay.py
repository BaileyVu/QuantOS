from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from quantos.application.retail_replay import RetailCausalProcessor
from quantos.application.retail_trader import RetailPaperEngine
from quantos.domain.evaluation.retail import RetailEvaluation
from quantos.domain.execution.retail_paper import RetailPaperAccount
from quantos.domain.market_data.hft import AggregateTrade, MarkPriceEvent
from quantos.domain.risk.retail import RetailRiskPolicy
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.infrastructure.storage.retail_events import RetailEventJournal
from quantos.infrastructure.storage.retail_paper import account_payload, validate_account, RetailPaperStateError, exclusive_account
from quantos.interfaces.retail_edge import replay_capture
from tests.unit.test_hft_paper import snapshot, delta, at, rules
from tests.unit.test_retail_intelligence import quote


def synthetic_capture(path, profit=True, *, clock_delay_ns=0, submission_extra_ns=0, expect_fill=True):
    config_text = Path("configs/futures_hft_200_paper.toml").read_text().replace("depth_levels = 5", "depth_levels = 1")
    temp_config = path.parent / "fixture.toml"
    temp_config.write_text(config_text)
    config = load_hft_config(temp_config)
    policy = RetailRiskPolicy()
    exchange_rules = replace(rules(), price_tick=D(".01"))
    account = RetailPaperAccount(config.fees["BTCUSDC"], policy)
    engine = RetailPaperEngine(config, exchange_rules, account, RetailEvaluation(), policy, unified=True)
    processor = RetailCausalProcessor(engine)
    journal = RetailEventJournal(path)
    journal.append("manifest", version="qos04-v2", config_text=config_text, policy=asdict(policy), rules=asdict(exchange_rules), account=account_payload(account))
    def dispatch(action, **arguments):
        if action == "event" and (clock_delay_ns or submission_extra_ns):
            arguments["processing_ns"] += clock_delay_ns
            start = arguments["processing_ns"]
            samples = iter((start, start + submission_extra_ns))
            processor.apply(action, arguments, clock_ns=lambda: next(samples))
        else:
            processor.apply(action, arguments)
        journal.append("command", action=action, arguments=arguments)
    snap = replace(snapshot(), bids=((D("100"),D("10")),), asks=((D("100.02"),D("5")),), received_monotonic_ns=0)
    dispatch("bootstrap", snapshot=snap, buffered=[replace(delta(), received_monotonic_ns=1_000_000_000)])
    update = 101
    def event(item, ns):
        dispatch("event", event=replace(item, received_at=at(ns//1_000_000_000), exchange_time=at(ns//1_000_000_000), received_monotonic_ns=ns), processing_ns=ns, feed_latency_ms=D("1"), clock_healthy=True)
    def depth(ns, bids=(), asks=()):
        nonlocal update
        update += 1
        event(delta(first=update, final=update, previous=update-1, bids=bids, asks=asks), ns)
    depth(1_100_000_000)
    dispatch("external", event=quote(1_100_000_000, 1))
    # Fresh target events avoid invented data continuity across the lookback.
    for second in range(2,7):
        depth(second*1_000_000_000)
    event(MarkPriceEvent("BTCUSDC",D("100"),D(".0001"),at(100),at(6),at(6),6_010_000_000),6_010_000_000)
    event(AggregateTrade("BTCUSDC",1,D("100"),D("2"),False,at(6),at(6),received_monotonic_ns=6_020_000_000),6_020_000_000)
    dispatch("external", event=quote(6_090_000_000, 2, "100.8", "100.82"))
    depth(6_100_000_000)
    depth(6_200_000_000)
    depth(6_210_000_000)
    if not expect_fill:
        if account.position is not None:
            raise AssertionError("delayed processing created a premature fill")
        dispatch("disarm", reason="runtime_shutdown", timestamp=at(7))
        validate_account(account)
        report = processor.report()
        journal.append("complete", report=report)
        journal.close()
        return report
    if not account.position:
        raise AssertionError(processor.report())
    # Force an explicit risk-off safety exit on fresh causal depth, never a fill command.
    # Record the control through processor, so replay uses the same transition.
    dispatch("tick", processing_ns=6_220_000_000, timestamp=at(6), kill=True)
    dispatch("resume")
    bid = D("100.5") if profit else D("99.5")
    depth(6_300_000_000, bids=((D("100"),D("0")),(bid,D("10"))), asks=((D("100.02"),D("0")),(bid+D(".02"),D("5"))))
    depth(6_400_000_000)
    depth(6_410_000_000)
    dispatch("disarm", reason="runtime_shutdown", timestamp=at(7))
    validate_account(account)
    report = processor.report()
    journal.append("complete", report=report)
    journal.close()
    return report


class RetailReplayTests(unittest.TestCase):
    def test_profitable_and_losing_pipeline_exact_replay(self):
        for profit in (True, False):
            with self.subTest(profit=profit), tempfile.TemporaryDirectory() as root:
                path = Path(root)/"synthetic.jsonl"
                report = synthetic_capture(path, profit)
                self.assertEqual(report["entry_fills"],1)
                self.assertEqual(report["exit_fills"],1)
                self.assertEqual(report["realized_net_pnl"] > 0, profit)
                replayed = replay_capture(path)
                self.assertTrue(replayed["matches_capture"])
                self.assertEqual(replayed["metrics"]["ledger_digest"], report["ledger_digest"])
                self.assertEqual(replay_capture(path), replayed)

    def test_execution_stress_never_improves_synthetic_profit(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"capture.jsonl"
            report=synthetic_capture(path)
            stressed=replay_capture(path, stress=True)
            self.assertLessEqual(stressed["metrics"]["realized_net_pnl"], report["realized_net_pnl"])

    def test_reconciliation_rejects_fabricated_balance_and_lock_is_exclusive(self):
        from tests.unit.test_hft_paper import fees
        account=RetailPaperAccount(fees(),RetailRiskPolicy())
        account.balance += 1
        with self.assertRaisesRegex(RetailPaperStateError,"reconcile"):
            validate_account(account)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"state.json"
            with exclusive_account(path):
                with self.assertRaisesRegex(RetailPaperStateError,"already in use"):
                    with exclusive_account(path):
                        pass

class SubmissionClockTests(unittest.TestCase):
    def test_delayed_processing_cannot_fill_before_actual_submission_ack(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"delayed.jsonl"
            report = synthetic_capture(path, clock_delay_ns=500_000_000, expect_fill=False)
            self.assertEqual(report["entry_fills"], 0)
            self.assertGreater(report["orders_submitted"], 0)
            self.assertGreater(report["orders_cancelled"], 0)
            self.assertTrue(replay_capture(path)["matches_capture"])

    def test_live_submission_clock_samples_roundtrip_exactly(self):
        from quantos.infrastructure.storage.retail_events import read_journal
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"sampled.jsonl"
            report = synthetic_capture(path, submission_extra_ns=1_000_000)
            self.assertEqual(report["entry_fills"], 1)
            self.assertEqual(report["exit_fills"], 1)
            self.assertTrue(replay_capture(path)["matches_capture"])
            stressed = replay_capture(path, stress=True)
            self.assertLessEqual(stressed["metrics"]["realized_net_pnl"], report["realized_net_pnl"])
            samples = [row["payload"]["arguments"]["clock_samples_ns"]
                       for row in read_journal(path)
                       if row["kind"] == "command" and row["payload"]["action"] == "event"]
            submission = [clocks for clocks in samples if len(clocks) == 2]
            self.assertEqual(len(submission), 2)
            self.assertTrue(all(clocks[1] - clocks[0] == 1_000_000 for clocks in submission))
