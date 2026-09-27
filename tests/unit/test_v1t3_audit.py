"""Narrow V1-T3 audit regressions: interruption, time, ownership and recovery."""
import asyncio
from dataclasses import replace
from datetime import timedelta, timezone
from decimal import Decimal as D
import json
import shutil
import unittest
from unittest.mock import patch

from quantos.application.paper_runtime import PaperRuntimeError, StopSignal
from quantos.domain.alpha import AlphaAction
from quantos.domain.risk.engine import RiskEngine
from quantos.domain.runtime_contracts import canonical, identity
from quantos.infrastructure.storage.paper_runtime import LocalPaperRuntimeStore
from tests.unit import test_v1t3_runtime as fixtures
from tests.unit.test_v1t3_runtime import Clock, Feed, events
from tests.unit.test_v1t2_evaluation import START, FixtureAlpha


class AuditTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.RuntimeTests.setUp
    make = fixtures.RuntimeTests.make
    state = fixtures.RuntimeTests.state
    evidence = fixtures.RuntimeTests.evidence
    rewrite = fixtures.RuntimeTests.rewrite

    async def test_future_same_symbol_before_partner_never_reaches_features(self):
        for index in (2, 3):  # BTC M+1 and ETH M+1
            with self.subTest(index=index):
                root = self.root/str(index)
                self.clock = Clock()
                runtime = self.make(root=root)
                with patch("quantos.application.paper_runtime.compute_feature_vector", side_effect=AssertionError("partial feature")):
                    with self.assertRaises(PaperRuntimeError):
                        await runtime.run(Feed([events(2)[0], events(2)[index]], self.clock))
                self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_injected_deadline_duplicate_does_not_reset_timeout(self):
        runtime = self.make()
        ticks = iter((10.0, 10.01, 10.02, 10.03, 10.06))
        runtime.monotonic = lambda: next(ticks)
        first, second = events(1)
        feed = Feed([first, first, second], self.clock)
        with patch.object(RiskEngine, "evaluate", side_effect=AssertionError("partial Risk")):
            with self.assertRaisesRegex(PaperRuntimeError, "synchronization timeout"):
                await runtime.run(feed)
        self.assertTrue(feed.closed)
        self.assertEqual(runtime.evidence_count, 0)
        self.assertEqual(self.state()["status"], "blocked")
        self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_backward_monotonic_clock_fails(self):
        runtime = self.make()
        ticks = iter((10.0, 9.0))
        runtime.monotonic = lambda: next(ticks)
        with self.assertRaisesRegex(PaperRuntimeError, "monotonic"):
            await runtime.run(Feed(events(1), self.clock))

    async def test_utc_clock_anomalies_never_reach_alpha(self):
        for name in ("backward", "naive", "offset", "negative_age"):
            with self.subTest(name=name):
                self.clock = Clock()
                runtime = self.make(root=self.root/name)
                def alter(event):
                    if name == "backward":
                        self.clock.now = START
                    elif name == "naive":
                        self.clock.now = self.clock.now.replace(tzinfo=None)
                    elif name == "offset":
                        self.clock.now = self.clock.now.replace(tzinfo=timezone(timedelta(hours=1)))
                    else:
                        self.clock.now = event.candle.close_time-timedelta(microseconds=1)
                        runtime.last_observation = None
                with self.assertRaises(PaperRuntimeError):
                    await runtime.run(Feed(events(1), self.clock, on_event=alter))
                self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_arrival_orders_have_identical_buy_sell_economics(self):
        snapshots, evidence = [], []
        actions = {("BTCUSDT", START+timedelta(minutes=21)): AlphaAction.BUY,
                   ("BTCUSDT", START+timedelta(minutes=22)): AlphaAction.SELL}
        for order in (("BTCUSDT", "ETHUSDT"), ("ETHUSDT", "BTCUSDT")):
            self.clock = Clock()
            root = self.root/order[0]
            runtime = self.make(root=root, alpha=FixtureAlpha(actions))
            await runtime.run(Feed(events(22, order), self.clock))
            snapshots.append(runtime.execution.snapshot)
            evidence.append((root/"minutes.jsonl").read_bytes())
        self.assertEqual(*snapshots)
        self.assertEqual(*evidence)

    async def test_crash_at_evidence_and_checkpoint_publication_boundaries(self):
        class Crash(BaseException):
            pass
        for boundary in ("before_processing", "after_processing", "after_evidence", "after_ready"):
            with self.subTest(boundary=boundary):
                root = self.root/boundary
                self.clock = Clock()
                class FaultStore(LocalPaperRuntimeStore):
                    def save(store, state):
                        if boundary == "before_processing" and state["status"] == "processing":
                            raise Crash()
                        super().save(state)
                        if boundary == "after_processing" and state["status"] == "processing":
                            raise Crash()
                        if boundary == "after_ready" and state["evidence_count"] == 1 and state["status"] == "ready":
                            raise Crash()
                    def append(store, evidence):
                        result = super().append(evidence)
                        if boundary == "after_evidence":
                            raise Crash()
                        return result
                store = FaultStore(root/"runtime.json", root/"minutes.jsonl", root/"orders.jsonl")
                runtime = self.make(root=root, store=store)
                with self.assertRaises(Crash):
                    await runtime.run(Feed(events(1), self.clock))
                restarted = self.make(root=root)
                if boundary in ("before_processing", "after_ready"):
                    await restarted.run(Feed([], self.clock))
                else:
                    with self.assertRaises(PaperRuntimeError):
                        await restarted.run(Feed([], self.clock))
                self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_durable_fill_with_old_ready_checkpoint_never_resubmits(self):
        await self.make().run(Feed(events(20), self.clock))
        checkpoint = (self.root/"runtime.json").read_bytes()
        evidence = (self.root/"minutes.jsonl").read_bytes()
        t = START+timedelta(minutes=21)
        runtime = self.make(alpha=FixtureAlpha({("BTCUSDT", t): AlphaAction.BUY}))
        await runtime.run(Feed(events(21)[40:], self.clock))
        (self.root/"runtime.json").write_bytes(checkpoint)
        (self.root/"minutes.jsonl").write_bytes(evidence)
        alpha = FixtureAlpha()
        with self.assertRaisesRegex(PaperRuntimeError, "ledger state mismatch"):
            await self.make(alpha=alpha).run(Feed(events(21)[40:], self.clock))
        self.assertEqual(alpha.seen, [])
        self.assertEqual(len(runtime.ledger.read()), 2)

    async def test_duplicate_only_restart_has_no_alpha_risk_or_account_effect(self):
        t = START+timedelta(minutes=21)
        runtime = self.make(alpha=FixtureAlpha({("BTCUSDT", t): AlphaAction.BUY}))
        await runtime.run(Feed(events(21), self.clock))
        before = runtime.execution.snapshot
        totals = dict(runtime.totals)
        alpha = FixtureAlpha()
        restart = self.make(alpha=alpha)
        with patch.object(RiskEngine, "evaluate", side_effect=AssertionError("duplicate Risk")):
            await restart.run(Feed(events(21)[40:], self.clock))
        self.assertEqual(alpha.seen, [])
        self.assertEqual(restart.execution.reconcile(), before)
        self.assertEqual(restart.totals, totals)
        self.assertEqual(restart.evidence_count, 21)

    async def test_checkpoint_totals_and_fee_basis_must_match_evidence(self):
        await self.make().run(Feed(events(1), self.clock))
        saved = (self.root/"runtime.json").read_bytes()
        for field in ("totals", "entry_fees", "last_equity"):
            state = json.loads(saved)["state"]
            if field == "totals":
                state[field]["fees"] = "1"
            elif field == "entry_fees":
                state[field] = {"BTCUSDT": "1"}
            else:
                state[field]["cash"] = "1"
            self.rewrite(state)
            with self.assertRaisesRegex(PaperRuntimeError, "evaluation evidence mismatch"):
                await self.make().run(Feed([], self.clock))
        (self.root/"runtime.json").write_bytes(saved)

    async def test_rehashed_evidence_totals_still_require_arithmetic_consistency(self):
        await self.make().run(Feed(events(1), self.clock))
        path = self.root/"minutes.jsonl"
        record = json.loads(path.read_bytes())
        record["evidence"]["totals"]["fees"] = "1"
        path.write_bytes((canonical(record)+"\n").encode("ascii"))
        state = self.state()
        state["evidence_id"] = identity(record)
        state["totals"]["fees"] = "1"
        self.rewrite(state)
        with self.assertRaisesRegex(PaperRuntimeError, "cumulative totals mismatch"):
            await self.make().run(Feed([], self.clock))

    async def test_restart_spanning_utc_midnight_preserves_marked_references(self):
        start = START+timedelta(hours=23, minutes=35)
        self.clock.now = start+timedelta(minutes=1)
        alpha = FixtureAlpha({("BTCUSDT", start+timedelta(minutes=21)): AlphaAction.BUY})
        runtime = self.make(alpha=alpha)
        await runtime.run(Feed(events(24, start=start), self.clock))
        first_peak = runtime.peak
        restart = self.make(alpha=alpha)
        await restart.run(Feed(events(25, start=start)[48:], self.clock))
        midnight_equity = restart.day_equity
        self.assertGreaterEqual(restart.peak, first_peak)
        again = self.make(alpha=alpha)
        await again.run(Feed(events(26, start=start)[48:], self.clock))
        self.assertEqual(again.day_equity, midnight_equity)
        self.assertGreaterEqual(again.peak, restart.peak)
        self.assertEqual(len(again.execution.snapshot.positions), 1)
        self.assertEqual(again.evidence_count, 26)

    async def test_stop_pending_bundle_logs_reason_and_recovers(self):
        stop = StopSignal()
        runtime = self.make(stop=stop)
        feed = Feed([events(1)[0], None], self.clock)
        with self.assertLogs("quantos", level="INFO") as logs:
            task = asyncio.create_task(runtime.run(feed))
            await asyncio.wait_for(feed.waiting.wait(), 5)
            stop.request("audit operator stop")
            await task
        self.assertTrue(feed.closed)
        self.assertEqual(runtime.evidence_count, 0)
        self.assertEqual(len(runtime.ledger.read()), 1)
        self.assertTrue(any(r.context.get("reason") == "audit operator stop" for r in logs.records))
        await self.make().run(Feed(events(1), self.clock))

    async def test_cancellation_at_first_event_or_pending_partner(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                root = self.root/str(pending)
                self.clock = Clock()
                runtime = self.make(root=root)
                feed = Feed(([events(1)[0]] if pending else [])+[None], self.clock)
                task = asyncio.create_task(runtime.run(feed))
                await asyncio.wait_for(feed.waiting.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(feed.closed)
                restart = self.make(root=root)
                await restart.run(Feed(events(1), self.clock))
                self.assertEqual(restart.evidence_count, 1)

    async def test_shared_ledger_different_checkpoint_session_rejected(self):
        first = LocalPaperRuntimeStore(self.root/"runtime.json", self.root/"minutes.jsonl", self.root/"orders.jsonl")
        first.acquire()
        try:
            second = LocalPaperRuntimeStore(self.root/"other.json", self.root/"other.jsonl", self.root/"orders.jsonl")
            with self.assertRaises(PaperRuntimeError):
                await self.make(store=second).run(Feed([], self.clock))
            self.assertTrue(first.ledger_lock_path.exists())
            self.assertFalse(second.lock_path.exists())
        finally:
            first.release()

    async def test_relocation_preserves_identity_but_costs_and_symbols_do_not(self):
        await self.make().run(Feed(events(1), self.clock))
        moved = self.root/"relocated"
        moved.mkdir()
        for name in ("runtime.json", "minutes.jsonl", "orders.jsonl", "orders.jsonl.head"):
            shutil.copyfile(self.root/name, moved/name)
        await self.make(root=moved).run(Feed([], self.clock))
        policies = (replace(self.policy, symbols=("BTCUSDT",)),
                    replace(self.policy, risk=replace(self.policy.risk,
                        costs=replace(self.policy.risk.costs, fee_rate=D("0.002")))))
        for policy in policies:
            with self.assertRaisesRegex(PaperRuntimeError, "incompatible"):
                await self.make(policy=policy).run(Feed([], self.clock))

    async def test_empty_checkpoint_cannot_claim_evaluation_totals(self):
        await self.make().run(Feed([], self.clock))
        state = self.state()
        state["totals"]["fees"] = "1"
        self.rewrite(state)
        with self.assertRaisesRegex(PaperRuntimeError, "without evidence"):
            await self.make().run(Feed([], self.clock))

    async def test_atomic_publication_failure_preserves_old_checkpoint_and_blocks(self):
        await self.make().run(Feed(events(1), self.clock))
        old = (self.root/"runtime.json").read_bytes()
        runtime = self.make()
        with patch("quantos.infrastructure.storage.paper_runtime._replace_durable",
                   side_effect=OSError("publication fault")):
            with self.assertRaisesRegex(PaperRuntimeError, "publication fault"):
                await runtime.run(Feed(events(2)[2:], self.clock))
        self.assertEqual((self.root/"runtime.json").read_bytes(), old)
        self.assertTrue((self.root/"runtime.json.tmp").exists())
        with self.assertRaisesRegex(PaperRuntimeError, "orphan"):
            await self.make().run(Feed([], self.clock))
        self.assertEqual(len(runtime.ledger.read()), 1)
