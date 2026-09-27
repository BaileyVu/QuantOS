"""Offline safety tests; deterministic Alpha fixtures are test-only."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quantos.application.paper_runtime import PaperRuntime, PaperRuntimeError, PaperRuntimePolicy, StopSignal
from quantos.domain.alpha import AlphaAction
from quantos.domain.execution.core import PaperFillProvider
from quantos.domain.features import MIN_HISTORY
from quantos.domain.market_data import MarketEvent
from quantos.domain.risk.engine import RiskEngine
from quantos.domain.runtime_contracts import canonical, identity
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from quantos.infrastructure.storage.paper_runtime import LocalPaperRuntimeStore
from tests.unit.test_v1t2_evaluation import START, POLICY, FixtureAlpha, dataset


class Clock:
    def __init__(self):
        self.now = START + timedelta(minutes=1)

    def __call__(self):
        return self.now


class Feed:
    def __init__(self, events, clock, *, delay=timedelta(0), on_event=None):
        self.events = iter(events)
        self.clock, self.delay, self.on_event = clock, delay, on_event
        self.closed = False
        self.waiting = asyncio.Event()

    async def __anext__(self):
        try:
            event = next(self.events)
        except StopIteration:
            raise StopAsyncIteration from None
        if event is None:
            self.waiting.set()
            await asyncio.Event().wait()
        self.clock.now = max(self.clock.now, event.timestamp+self.delay)
        if self.on_event:
            self.on_event(event)
        return event

    async def aclose(self):
        self.closed = True


def events(count=22, symbols=("BTCUSDT", "ETHUSDT"), start=START):
    data = {s: dataset(s, count, start).candles for s in symbols}
    return [MarketEvent(data[s][i].close_time, data[s][i]) for i in range(count) for s in symbols]


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.policy = PaperRuntimePolicy(("BTCUSDT", "ETHUSDT"), D("20"), POLICY, 50, "test-only-v1")

    def make(self, *, alpha=None, policy=None, stop=None, store=None, root=None):
        root = root or self.root
        return PaperRuntime(policy=policy or self.policy, alpha=alpha or FixtureAlpha(),
            ledger=JsonlExecutionLedger(root/"orders.jsonl"),
            store=store or LocalPaperRuntimeStore(root/"runtime.json", root/"minutes.jsonl", root/"orders.jsonl"),
            clock=self.clock, stop=stop)

    def state(self):
        return json.loads((self.root/"runtime.json").read_bytes())["state"]

    def evidence(self):
        return [json.loads(line)["evidence"] for line in (self.root/"minutes.jsonl").read_text().splitlines()]

    def rewrite(self, state):
        (self.root/"runtime.json").write_bytes((canonical({"state": state, "checksum": identity(state)})+"\n").encode("ascii"))

    async def test_barrier_both_arrival_orders_yield_identical_evidence(self):
        outputs = []
        for order in (("BTCUSDT", "ETHUSDT"), ("ETHUSDT", "BTCUSDT")):
            alpha = FixtureAlpha()
            root = self.root/order[0]
            self.clock = Clock()
            runtime = self.make(alpha=alpha, root=root)
            feed = Feed(events(symbols=order), self.clock)
            await runtime.run(feed)
            self.assertEqual([f.symbol for f in alpha.seen], ["BTCUSDT", "ETHUSDT"]*2)
            self.assertTrue(feed.closed)
            outputs.append((root/"minutes.jsonl").read_bytes())
        self.assertEqual(*outputs)

    async def test_missing_partner_times_out_before_alpha(self):
        alpha = FixtureAlpha()
        runtime = self.make(alpha=alpha)
        feed = Feed(events(21)[:-1]+[None], self.clock)
        with self.assertRaisesRegex(PaperRuntimeError, "synchronization timeout"):
            await runtime.run(feed)
        self.assertEqual(alpha.seen, [])
        self.assertEqual(len(runtime.ledger.read()), 1)
        self.assertTrue(feed.closed)

    async def test_pending_duplicate_exact_only(self):
        sequence = events(1)
        runtime = self.make()
        await runtime.run(Feed([sequence[0], sequence[0], sequence[1]], self.clock))
        self.assertEqual(runtime.evidence_count, 1)

    async def test_conflicting_pending_duplicate_fails(self):
        item = events(1)[0]
        conflict = replace(item, candle=replace(item.candle, volume=D("99")))
        runtime = self.make()
        with self.assertRaisesRegex(PaperRuntimeError, "conflicting"):
            await runtime.run(Feed([item, conflict], self.clock))
        self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_cross_symbol_minute_mismatch_fails(self):
        sequence = events(2)
        runtime = self.make()
        with self.assertRaisesRegex(PaperRuntimeError, "minute mismatch"):
            await runtime.run(Feed([sequence[0], sequence[3]], self.clock))
        self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_warmup_history_bounded_and_survives_restart(self):
        alpha = FixtureAlpha()
        runtime = self.make(alpha=alpha)
        await runtime.run(Feed(events(20), self.clock))
        self.assertEqual(alpha.seen, [])
        restarted = self.make(alpha=alpha)
        await restarted.run(Feed(events(24)[40:], self.clock))
        self.assertEqual(alpha.seen[0].timestamp, START+timedelta(minutes=21))
        self.assertEqual(len(alpha.seen), 8)
        self.assertEqual({len(h) for h in restarted.histories.values()}, {MIN_HISTORY})
        self.assertEqual({len(h) for h in self.state()["histories"].values()}, {MIN_HISTORY})

    async def test_stale_actual_observation_rejected_before_alpha(self):
        runtime = self.make()
        with self.assertRaisesRegex(PaperRuntimeError, "stale"):
            await runtime.run(Feed(events(1), self.clock, delay=timedelta(seconds=61)))
        self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_freshness_rechecked_after_alpha(self):
        def alpha(feature):
            self.clock.now += timedelta(seconds=61)
            return FixtureAlpha()(feature)
        runtime = self.make(alpha=alpha)
        with self.assertRaisesRegex(PaperRuntimeError, "stale"):
            await runtime.run(Feed(events(), self.clock))
        self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_future_event_timestamp_rejected(self):
        event = events(1)[0]
        feed = Feed([replace(event, timestamp=event.timestamp+timedelta(seconds=5))], self.clock,
                    on_event=lambda e: setattr(self.clock, "now", e.candle.close_time))
        with self.assertRaisesRegex(PaperRuntimeError, "future event"):
            await self.make().run(feed)

    async def test_same_minute_second_risk_sees_updated_account(self):
        t = START+timedelta(minutes=21)
        alpha = FixtureAlpha({(s, t): AlphaAction.BUY for s in self.policy.symbols})
        seen = []
        original = RiskEngine.evaluate
        def observe(engine, decision, context):
            if decision.timestamp == t:
                seen.append(context)
            return original(engine, decision, context)
        runtime = self.make(alpha=alpha, policy=replace(self.policy, risk=replace(POLICY, max_exposure_fraction=D("0.4"))))
        with patch.object(RiskEngine, "evaluate", observe):
            await runtime.run(Feed(events(21), self.clock))
        self.assertLess(seen[1].account.balances["USDT"], seen[0].account.balances["USDT"])
        self.assertEqual(len(seen[1].account.positions), 1)
        self.assertEqual(len(seen[1].markets), 2)
        self.assertTrue(all(m.candle.close_time == t for m in seen[1].markets))
        self.assertEqual(len(runtime.ledger.read()), 2)
        self.assertIsInstance(runtime.execution.provider, PaperFillProvider)
        last = self.evidence()[-1]["decisions"]
        self.assertTrue(last[0]["risk"]["approved"])
        self.assertFalse(last[1]["risk"]["approved"])

    async def test_hold_and_edge_rejection_no_orders(self):
        t = START+timedelta(minutes=21)
        runtime = self.make(alpha=FixtureAlpha({("BTCUSDT", t): AlphaAction.BUY}, gross_edge=D("0")))
        await runtime.run(Feed(events(21), self.clock))
        self.assertEqual(len(runtime.ledger.read()), 1)
        self.assertEqual(runtime.totals["trade_count"], 0)
        self.assertTrue(all(d["execution"] is None for d in self.evidence()[-1]["decisions"]))

    async def test_restart_duplicate_suppression_and_next_minute(self):
        t = START+timedelta(minutes=21)
        alpha = FixtureAlpha({("BTCUSDT", t): AlphaAction.BUY})
        runtime = self.make(alpha=alpha)
        await runtime.run(Feed(events(21), self.clock))
        before = runtime.execution.snapshot
        restarted = self.make(alpha=alpha)
        await restarted.run(Feed(events(22)[40:], self.clock))
        self.assertEqual(restarted.execution.snapshot, before)
        self.assertEqual(len(restarted.ledger.read()), 2)
        self.assertEqual(restarted.evidence_count, 22)

    async def test_restart_rejects_older_gap_or_conflicting_last(self):
        for case in ("old", "gap", "conflict"):
            with self.subTest(case=case):
                root = self.root/case
                self.clock = Clock()
                await self.make(root=root).run(Feed(events(2), self.clock))
                sequence = events(4)
                item = sequence[0] if case == "old" else sequence[6] if case == "gap" else replace(sequence[2], candle=replace(sequence[2].candle, volume=D("99")))
                with self.assertRaises(PaperRuntimeError):
                    await self.make(root=root).run(Feed([item], self.clock))

    async def test_checkpoint_is_canonical_and_restores_equity_references(self):
        runtime = self.make()
        await runtime.run(Feed(events(22), self.clock))
        raw = (self.root/"runtime.json").read_bytes()
        self.assertEqual(raw, (canonical(json.loads(raw))+"\n").encode("ascii"))
        restart = self.make()
        await restart.run(Feed([], self.clock))
        self.assertEqual(restart.histories, runtime.histories)
        self.assertEqual((restart.day, restart.day_equity, restart.peak), (runtime.day, runtime.day_equity, runtime.peak))
        self.assertEqual(self.state()["execution_id"], identity(restart.execution.snapshot))
        self.assertFalse((self.root/"runtime.json.lock").exists())
        self.assertFalse((self.root/"runtime.json.tmp").exists())

    async def test_changed_config_or_alpha_identity_fails(self):
        await self.make().run(Feed(events(1), self.clock))
        for policy in (replace(self.policy, alpha_implementation_id="changed"),
                       replace(self.policy, risk=replace(POLICY, order_notional=D("6")))):
            with self.assertRaisesRegex(PaperRuntimeError, "incompatible"):
                await self.make(policy=policy).run(Feed([], self.clock))

    async def test_corrupt_checkpoint_and_execution_mismatch_fail(self):
        await self.make().run(Feed(events(1), self.clock))
        saved = (self.root/"runtime.json").read_bytes()
        (self.root/"runtime.json").write_bytes(saved[:-5])
        with self.assertRaises(PaperRuntimeError):
            await self.make().run(Feed([], self.clock))
        (self.root/"runtime.json").write_bytes(saved)
        state = self.state()
        state["execution_id"] = "wrong"
        self.rewrite(state)
        with self.assertRaisesRegex(PaperRuntimeError, "ledger state mismatch"):
            await self.make().run(Feed([], self.clock))

    async def test_evidence_truncation_fails_closed(self):
        await self.make().run(Feed(events(2), self.clock))
        path = self.root/"minutes.jsonl"
        path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
        with self.assertRaisesRegex(PaperRuntimeError, "evidence/checkpoint mismatch"):
            await self.make().run(Feed([], self.clock))

    async def test_missing_runtime_state_never_recovers_ledger_silently(self):
        await self.make().run(Feed(events(1), self.clock))
        (self.root/"runtime.json").unlink()
        with self.assertRaisesRegex(PaperRuntimeError, "missing runtime checkpoint"):
            await self.make().run(Feed([], self.clock))

    async def test_crash_after_fill_before_minute_commit_blocks_restart(self):
        class Crash(BaseException):
            pass
        class CrashingStore(LocalPaperRuntimeStore):
            def append(inner, evidence):
                if evidence["decisions"]:
                    raise Crash()
                return super().append(evidence)
        t = START+timedelta(minutes=21)
        runtime = self.make(alpha=FixtureAlpha({("BTCUSDT", t): AlphaAction.BUY}),
            store=CrashingStore(self.root/"runtime.json", self.root/"minutes.jsonl", self.root/"orders.jsonl"))
        with self.assertRaises(Crash):
            await runtime.run(Feed(events(21), self.clock))
        self.assertEqual(len(runtime.ledger.read()), 2)
        self.assertEqual(self.state()["status"], "processing")
        with self.assertRaisesRegex(PaperRuntimeError, "unfinished"):
            await self.make().run(Feed([], self.clock))
        self.assertEqual(len(runtime.ledger.read()), 2)

    async def test_persistence_failure_stops_further_trading(self):
        store = LocalPaperRuntimeStore(self.root/"runtime.json", self.root/"minutes.jsonl", self.root/"orders.jsonl")
        runtime = self.make(store=store)
        feed = Feed(events(22), self.clock)
        with patch.object(store, "append", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(PaperRuntimeError, "disk full"):
                await runtime.run(feed)
        self.assertTrue(feed.closed)
        self.assertEqual(self.state()["status"], "blocked")
        self.assertEqual(len(runtime.ledger.read()), 1)

    async def test_kill_active_before_start_no_alpha_or_receive(self):
        stop = StopSignal()
        stop.request("operator stop")
        alpha = FixtureAlpha()
        runtime = self.make(stop=stop, alpha=alpha)
        feed = Feed(events(21), self.clock)
        await runtime.run(feed)
        self.assertEqual(alpha.seen, [])
        self.assertTrue(feed.closed)
        self.assertEqual(runtime.evidence_count, 0)

    async def test_kill_during_alpha_prevents_risk_and_keeps_recoverable_state(self):
        stop = StopSignal()
        def alpha(feature):
            stop.request("kill in callback")
            return FixtureAlpha()(feature)
        runtime = self.make(stop=stop, alpha=alpha)
        with patch.object(RiskEngine, "evaluate", side_effect=AssertionError("Risk after stop")):
            await runtime.run(Feed(events(22), self.clock))
        self.assertEqual(runtime.evidence_count, 21)
        await self.make().run(Feed([], self.clock))

    async def test_kill_while_waiting_closes_adapter_without_liquidation(self):
        stop = StopSignal()
        t = START+timedelta(minutes=21)
        runtime = self.make(stop=stop, alpha=FixtureAlpha({("BTCUSDT", t): AlphaAction.BUY}))
        feed = Feed(events(21)+[None], self.clock)
        task = asyncio.create_task(runtime.run(feed))
        await asyncio.wait_for(feed.waiting.wait(), 10)
        stop.request("operator halt")
        await asyncio.wait_for(task, 10)
        self.assertTrue(feed.closed)
        self.assertEqual(len(runtime.execution.snapshot.positions), 1)
        self.assertEqual(len(runtime.ledger.read()), 2)
        await self.make().run(Feed([], self.clock))

    async def test_cancellation_propagates_and_state_stays_consistent(self):
        runtime = self.make()
        feed = Feed(events(2)+[None], self.clock)
        task = asyncio.create_task(runtime.run(feed))
        await asyncio.wait_for(feed.waiting.wait(), 10)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(feed.closed)
        self.assertEqual(self.state()["status"], "ready")
        await self.make().run(Feed([], self.clock))

    async def test_utc_day_rollover_uses_causal_marks(self):
        start = START+timedelta(hours=23, minutes=35)
        self.clock.now = start+timedelta(minutes=1)
        t = start+timedelta(minutes=21)
        runtime = self.make(alpha=FixtureAlpha({("BTCUSDT", t): AlphaAction.BUY}))
        await runtime.run(Feed(events(30, start=start), self.clock))
        points = [r["equity"] for r in self.evidence()]
        midnight = next(p for p in points if "T00:00:00" in p["timestamp"])
        self.assertEqual(midnight["day_start_equity"], midnight["equity"])
        self.assertGreater(runtime.day_equity, D("0"))
        self.assertGreaterEqual(runtime.peak, D(points[-1]["equity"]))

    async def test_single_symbol_works_without_partner(self):
        runtime = self.make(policy=replace(self.policy, symbols=("BTCUSDT",)))
        await runtime.run(Feed(events(21, ("BTCUSDT",)), self.clock))
        self.assertEqual(runtime.evidence_count, 21)

    async def test_session_lock_prevents_second_runtime(self):
        first = LocalPaperRuntimeStore(self.root/"runtime.json", self.root/"minutes.jsonl", self.root/"orders.jsonl")
        first.acquire()
        try:
            with self.assertRaises(PaperRuntimeError):
                await self.make().run(Feed([], self.clock))
            self.assertTrue(first.lock_path.exists())
        finally:
            first.release()


if __name__ == "__main__":
    unittest.main()
