from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

from quantos.application.hft_trader import run_hft_paper_session
from quantos.domain.evaluation.hft import HftEvaluation
from quantos.domain.execution.hft_paper import HftPaperAccount
from quantos.domain.market_data.hft import (
    AggregateTrade,
    DepthDelta,
    DepthSnapshot,
)
from quantos.infrastructure.binance.hft import BinanceHftStream
from quantos.infrastructure.configuration.hft import load_hft_config

from tests.unit.test_hft_paper import fees, rules


D = Decimal
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def utc_now(self) -> datetime:
        return BASE + timedelta(seconds=self.value)

    async def sleep(self, seconds: float) -> None:
        self.value += seconds
        await asyncio.sleep(0)


class AdvancingQueueWaiter:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.timeouts = 0

    async def __call__(self, queue: asyncio.Queue, timeout: float):
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(0)
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            self.clock.value += timeout
            self.timeouts += 1
            raise asyncio.TimeoutError


class FakeStream:
    def __init__(self, events=(), *, public_ready: bool = False) -> None:
        self.supplied_events = tuple(events)
        self.public_ready = public_ready
        self.closed = False

    async def wait_public_ready(self) -> None:
        if self.public_ready:
            return
        await asyncio.Future()

    async def events(self, symbol: str):
        try:
            for event in self.supplied_events:
                yield event
            await asyncio.Future()
        finally:
            self.closed = True


class FakeRecorder:
    def __init__(self) -> None:
        self.events = []
        self.flush_count = 0

    def append(self, event, now):
        self.events.append(event)

    def checkpoint(self, *args):
        return None

    def flush(self):
        self.flush_count += 1


class FakeStateStore:
    def __init__(self) -> None:
        self.saves = []

    def save(self, account, evaluation, runtime, identity, now) -> None:
        self.saves.append(dict(runtime))


def snapshot() -> DepthSnapshot:
    return DepthSnapshot(
        symbol="BTCUSDC",
        last_update_id=100,
        bids=((D("100"), D("10")), (D("99"), D("4"))),
        asks=((D("101"), D("5")), (D("102"), D("8"))),
        exchange_time=BASE,
        received_at=BASE,
    )


def bridge() -> DepthDelta:
    return DepthDelta(
        symbol="BTCUSDC",
        first_update_id=100,
        final_update_id=101,
        previous_final_update_id=99,
        bids=(),
        asks=(),
        exchange_time=BASE,
        received_at=BASE,
    )


def trade() -> AggregateTrade:
    return AggregateTrade(
        symbol="BTCUSDC",
        aggregate_trade_id=1,
        price=D("100"),
        quantity=D("1"),
        buyer_is_maker=True,
        exchange_time=BASE,
        received_at=BASE,
    )


class HftRuntimeLivenessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = load_hft_config(Path("configs/futures_hft_paper.toml"))
        self.config = replace(
            config,
            maximum_staleness=timedelta(seconds=100),
            websocket_read_timeout=timedelta(seconds=1),
            snapshot_timeout=timedelta(seconds=3),
            bootstrap_timeout=timedelta(seconds=10),
            heartbeat_interval=timedelta(seconds=5),
            reconnect_delay=timedelta(milliseconds=100),
            shutdown_timeout=timedelta(seconds=1),
        )

    async def run_session(
        self,
        *,
        duration: int,
        stream_factory,
        cycles: int = 0,
        snapshot_loader=None,
        emit=None,
    ):
        clock = FakeClock()
        waiter = AdvancingQueueWaiter(clock)
        recorder = FakeRecorder()
        store = FakeStateStore()
        account = HftPaperAccount(D("100"), fees())
        evaluation = HftEvaluation(D("100"))
        runtime = {}
        emitted = []

        async def load_snapshot(client, symbol):
            return snapshot()

        metrics = await run_hft_paper_session(
            config=self.config,
            rules=rules(),
            account=account,
            evaluation=evaluation,
            state_store=store,
            identity="test-identity",
            runtime=runtime,
            cycles=cycles,
            duration_seconds=duration,
            client=object(),
            stream_factory=stream_factory,
            recorder=recorder,
            emit=emit or emitted.append,
            monotonic_clock=clock.monotonic,
            utc_clock=clock.utc_now,
            wait_for_item=waiter,
            snapshot_loader=snapshot_loader or load_snapshot,
            sleep=clock.sleep,
        )
        return {
            "clock": clock,
            "waiter": waiter,
            "recorder": recorder,
            "store": store,
            "account": account,
            "runtime": runtime,
            "emitted": emitted,
            "metrics": metrics,
        }

    async def test_finite_duration_exits_with_zero_incoming_messages(self) -> None:
        result = await self.run_session(
            duration=3,
            stream_factory=lambda: FakeStream(public_ready=True),
        )
        self.assertEqual(result["runtime"]["shutdown_reason"], "duration_expired")
        self.assertGreaterEqual(result["clock"].value, 3)
        self.assertEqual(result["account"].fills, [])

    async def test_finite_duration_exits_while_receive_is_blocked(self) -> None:
        streams = []

        def factory():
            stream = FakeStream((bridge(),), public_ready=True)
            streams.append(stream)
            return stream

        result = await self.run_session(duration=3, stream_factory=factory)
        self.assertEqual(result["runtime"]["shutdown_reason"], "duration_expired")
        self.assertTrue(streams[0].closed)
        self.assertGreater(result["waiter"].timeouts, 0)

    async def test_idle_heartbeat_has_required_health_fields(self) -> None:
        result = await self.run_session(
            duration=6,
            stream_factory=lambda: FakeStream((bridge(),), public_ready=True),
        )
        heartbeats = [
            event for event in result["emitted"]
            if event["event"] == "hft_heartbeat"
        ]
        self.assertEqual(len(heartbeats), 1)
        heartbeat = heartbeats[0]
        self.assertTrue(heartbeat["book_ready"])
        self.assertEqual(heartbeat["symbol"], "BTCUSDC")
        self.assertEqual(heartbeat["fills"], 0)
        self.assertEqual(heartbeat["remaining_duration_seconds"], 1)
        self.assertTrue({
            "uptime_seconds", "last_depth_event_age_ms",
            "last_trade_event_age_ms", "last_bookticker_event_age_ms",
            "current_best_bid", "current_best_ask", "spread_bps",
            "current_vamp", "alpha_bps", "quote_state", "inventory",
            "equity", "sequence_gap_count", "reconnect_count",
            "events_received", "quotes_submitted",
        }.issubset(heartbeat))

    async def test_read_timeout_returns_control_without_fake_fill(self) -> None:
        result = await self.run_session(
            duration=3,
            stream_factory=lambda: FakeStream((bridge(),), public_ready=True),
        )
        self.assertTrue(any(
            event["event"] == "websocket_timeout"
            for event in result["emitted"]
        ))
        self.assertGreater(result["waiter"].timeouts, 0)
        self.assertEqual(result["account"].fills, [])

    async def test_snapshot_timeout_fails_closed_with_exact_stage(self) -> None:
        async def timed_out_snapshot(client, symbol):
            raise asyncio.TimeoutError

        result = await self.run_session(
            duration=2,
            stream_factory=lambda: FakeStream(public_ready=True),
            snapshot_loader=timed_out_snapshot,
        )
        failures = [
            event for event in result["emitted"]
            if event["event"] == "hft_bootstrap_failed"
        ]
        self.assertTrue(failures)
        self.assertEqual(failures[0]["stage"], "depth_snapshot")
        self.assertFalse(result["runtime"]["book_valid"])
        self.assertEqual(result["account"].fills, [])

    async def test_idle_timeout_preserves_sequence_state(self) -> None:
        result = await self.run_session(
            duration=3,
            stream_factory=lambda: FakeStream((bridge(),), public_ready=True),
        )
        self.assertEqual(result["runtime"]["last_update_id"], 101)
        self.assertEqual(result["runtime"]["sequence_gap_count"], 0)
        self.assertEqual(result["runtime"]["events_received"]["depth"], 1)

    async def test_cancelled_run_flushes_and_persists_state(self) -> None:
        ready = asyncio.Event()
        emitted = []

        def emit(event):
            emitted.append(event)
            if event["event"] == "order_book_ready":
                ready.set()

        clock = FakeClock()
        recorder = FakeRecorder()
        store = FakeStateStore()
        account = HftPaperAccount(D("100"), fees())
        runtime = {}

        async def load_snapshot(client, symbol):
            return snapshot()

        task = asyncio.create_task(run_hft_paper_session(
            config=self.config,
            rules=rules(),
            account=account,
            evaluation=HftEvaluation(D("100")),
            state_store=store,
            identity="test-identity",
            runtime=runtime,
            cycles=0,
            duration_seconds=0,
            client=object(),
            stream_factory=lambda: FakeStream(
                (bridge(),), public_ready=True
            ),
            recorder=recorder,
            emit=emit,
            monotonic_clock=clock.monotonic,
            utc_clock=clock.utc_now,
            snapshot_loader=load_snapshot,
            sleep=clock.sleep,
        ))
        await asyncio.wait_for(ready.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(recorder.flush_count, 1)
        self.assertEqual(len(store.saves), 1)
        self.assertEqual(runtime["shutdown_reason"], "cancelled")
        self.assertTrue(any(
            event["event"] == "hft_runtime_shutdown_complete"
            for event in emitted
        ))

    async def test_zero_duration_is_unlimited_until_cycle_limit(self) -> None:
        result = await self.run_session(
            duration=0,
            cycles=1,
            stream_factory=lambda: FakeStream(
                (bridge(), trade()), public_ready=True
            ),
        )
        self.assertEqual(
            result["runtime"]["shutdown_reason"],
            "finite_cycle_completion",
        )
        self.assertEqual(result["account"].fills, [])


class BlockedSocket:
    async def recv(self):
        await asyncio.Future()


class SocketContext:
    async def __aenter__(self):
        return BlockedSocket()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class HftStreamReadTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_blocked_socket_receive_is_bounded(self) -> None:
        events = []
        stream = BinanceHftStream(
            read_timeout=0.001,
            shutdown_timeout=0.05,
            lifecycle=events.append,
        )
        with patch(
            "quantos.infrastructure.binance.hft.connect",
            return_value=SocketContext(),
        ):
            generator = stream.events("BTCUSDC")
            consumer = asyncio.create_task(anext(generator))
            await asyncio.wait_for(stream.wait_public_ready(), timeout=1)
            for _ in range(100):
                if any(event["event"] == "websocket_timeout" for event in events):
                    break
                await asyncio.sleep(0.001)
            self.assertTrue(any(
                event["event"] == "websocket_timeout" for event in events
            ))
            consumer.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await consumer
        names = {event["event"] for event in events}
        self.assertTrue({
            "websocket_connecting", "websocket_connected", "subscribing",
            "subscription_confirmed", "market_stream_ready",
            "websocket_timeout", "websocket_disconnected",
        }.issubset(names))


if __name__ == "__main__":
    unittest.main()
