from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import time
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
from quantos.domain.market_data.hft_clock import (
    HftClockSample,
    robust_clock_calibration,
)
from quantos.infrastructure.binance.hft import (
    BinanceHftBackpressureError,
    BinanceHftStream,
)
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


async def fake_calibration(
    client, sample_count, lowest_rtt_sample_count, utc_clock, monotonic_clock
):
    del client
    samples = []
    for _ in range(sample_count):
        wall = utc_clock()
        monotonic = monotonic_clock()
        samples.append(HftClockSample(
            local_send_wall=wall,
            local_receive_wall=wall,
            server_wall=wall,
            local_send_monotonic=monotonic,
            local_receive_monotonic=monotonic,
        ))
    return robust_clock_calibration(
        tuple(samples), lowest_rtt_sample_count
    )


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

    def append(self, event, now, processing_monotonic_ns=None):
        del now, processing_monotonic_ns
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


def live_depth(
    update_id: int = 102, received_monotonic_ns: int | None = None
) -> DepthDelta:
    return DepthDelta(
        symbol="BTCUSDC",
        first_update_id=update_id,
        final_update_id=update_id,
        previous_final_update_id=update_id - 1,
        bids=(),
        asks=(),
        exchange_time=BASE,
        received_at=BASE,
        received_monotonic_ns=received_monotonic_ns,
    )


class TelemetryStream(FakeStream):
    def __init__(self, events=(), *, depth_socket_age_ms=D("0")) -> None:
        super().__init__(events, public_ready=True)
        self.depth_socket_age_ms = depth_socket_age_ms

    def telemetry(self, now_ns=None):
        del now_ns
        return {
            "socket_receive_age_by_route_ms": {
                "public": self.depth_socket_age_ms,
                "market": D("0"),
            },
            "depth_socket_receive_age_ms": self.depth_socket_age_ms,
            "stream_queue_current": 0,
            "stream_queue_high_water": 1,
            "backpressure_overflow_count": 0,
            "websocket_read_timeout_count": 0,
        }


class BurstAfterBridgeStream(TelemetryStream):
    async def events(self, symbol: str):
        del symbol
        try:
            yield bridge()
            await asyncio.sleep(0)
            for identifier in range(100):
                yield replace(trade(), aggregate_trade_id=identifier + 1)
            await asyncio.Future()
        finally:
            self.closed = True


class SlowClockRecorder(FakeRecorder):
    def __init__(self, clock: FakeClock) -> None:
        super().__init__()
        self.clock = clock
        self.advances_remaining = 2

    def append(self, event, now, processing_monotonic_ns=None):
        super().append(event, now, processing_monotonic_ns)
        if self.advances_remaining > 0:
            self.clock.value += 2
            self.advances_remaining -= 1


class BlockingRecorder(FakeRecorder):
    def append(self, event, now, processing_monotonic_ns=None):
        time.sleep(0.03)
        return super().append(event, now, processing_monotonic_ns)


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
        calibration_loader=None,
        emit=None,
        config=None,
        recorder_factory=None,
        application_queue_maxsize=4096,
    ):
        clock = FakeClock()
        waiter = AdvancingQueueWaiter(clock)
        recorder = (
            recorder_factory(clock)
            if recorder_factory is not None else FakeRecorder()
        )
        store = FakeStateStore()
        account = HftPaperAccount(D("100"), fees())
        evaluation = HftEvaluation(D("100"))
        runtime = {}
        emitted = []

        async def load_snapshot(client, symbol):
            return snapshot()

        metrics = await run_hft_paper_session(
            config=config or self.config,
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
            calibration_loader=calibration_loader or fake_calibration,
            sleep=clock.sleep,
            application_queue_maxsize=application_queue_maxsize,
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
            "exchange_clock_offset_ms", "calibration_rtt_ms",
            "raw_feed_latency_ms", "corrected_feed_latency_ms",
            "negative_latency_observation_count",
            "excessive_negative_latency_count", "clock_health",
            "last_clock_calibration_age_seconds",
            "socket_receive_age_by_route_ms",
            "depth_socket_receive_age_ms", "consumer_lag_ms",
            "stream_queue_current", "stream_queue_high_water",
            "application_queue_current", "application_queue_high_water",
            "backpressure_overflow_count", "stale_trading_disarm_count",
            "actual_transport_reconnect_count",
            "recovery_without_reconnect_count",
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
        self.assertEqual(
            result["runtime"]["reconnect_count"], 0, result["emitted"]
        )
        self.assertEqual(
            result["runtime"]["actual_transport_reconnect_count"], 0
        )

    async def test_consumer_lag_disarms_without_transport_reconnect(self) -> None:
        config = replace(
            self.config,
            maximum_staleness=timedelta(seconds=1),
        )
        result = await self.run_session(
            duration=0,
            cycles=1,
            config=config,
            stream_factory=lambda: TelemetryStream(
                (
                    replace(bridge(), received_monotonic_ns=0),
                    replace(trade(), received_monotonic_ns=0),
                )
            ),
            recorder_factory=SlowClockRecorder,
        )
        self.assertEqual(result["runtime"]["stale_trading_disarm_count"], 1)
        self.assertEqual(result["runtime"]["reconnect_count"], 0)
        self.assertEqual(
            result["runtime"]["actual_transport_reconnect_count"], 0
        )
        self.assertGreater(result["runtime"]["consumer_lag_ms"], D("1000"))
        self.assertTrue(any(
            event.get("event") == "hft_trading_disarmed"
            for event in result["emitted"]
        ))

    async def test_slow_recorder_does_not_block_asyncio_ingest_loop(self) -> None:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.001)

        ticker_task = asyncio.create_task(ticker())
        try:
            result = await self.run_session(
                duration=0,
                cycles=1,
                stream_factory=lambda: TelemetryStream(
                    (bridge(), trade())
                ),
                recorder_factory=lambda clock: BlockingRecorder(),
            )
        finally:
            ticker_task.cancel()
            await asyncio.gather(ticker_task, return_exceptions=True)
        self.assertGreater(ticks, 10)
        self.assertEqual(result["runtime"]["reconnect_count"], 0)
        self.assertEqual(
            result["runtime"]["actual_transport_reconnect_count"], 0
        )

    async def test_fresh_depth_recovers_without_reconnect_in_sequence(self) -> None:
        config = replace(
            self.config,
            maximum_staleness=timedelta(seconds=1),
        )

        async def deep_snapshot(client, symbol):
            del client, symbol
            return DepthSnapshot(
                symbol="BTCUSDC",
                last_update_id=100,
                bids=tuple(
                    (D("100") - D(index), D("10"))
                    for index in range(5)
                ),
                asks=tuple(
                    (D("101") + D(index), D("10"))
                    for index in range(5)
                ),
                exchange_time=BASE,
                received_at=BASE,
                received_monotonic_ns=0,
            )

        result = await self.run_session(
            duration=0,
            cycles=2,
            config=config,
            stream_factory=lambda: TelemetryStream((
                replace(bridge(), received_monotonic_ns=0),
                replace(trade(), received_monotonic_ns=0),
                live_depth(received_monotonic_ns=4_000_000_000),
            )),
            recorder_factory=SlowClockRecorder,
            snapshot_loader=deep_snapshot,
        )
        self.assertEqual(
            result["runtime"]["recovery_without_reconnect_count"], 1
        )
        self.assertEqual(
            result["runtime"]["reconnect_count"], 0, result["emitted"]
        )
        self.assertEqual(result["runtime"]["last_update_id"], 102)
        self.assertEqual(result["runtime"]["sequence_gap_count"], 0)

    async def test_application_queue_overflow_is_explicit_fail_closed(self) -> None:
        result = await self.run_session(
            duration=2,
            application_queue_maxsize=1,
            stream_factory=BurstAfterBridgeStream,
        )
        self.assertGreater(result["runtime"]["backpressure_overflow_count"], 0)
        self.assertGreater(result["runtime"]["reconnect_count"], 0)
        self.assertTrue(any(
            event.get("reason") == "backpressure_overflow"
            for event in result["emitted"]
        ))
        self.assertFalse(result["runtime"]["book_valid"])

    async def test_actual_transport_staleness_reconnects(self) -> None:
        config = replace(
            self.config,
            maximum_staleness=timedelta(seconds=1),
        )
        result = await self.run_session(
            duration=2,
            config=config,
            stream_factory=lambda: TelemetryStream(
                (bridge(),), depth_socket_age_ms=D("10000")
            ),
        )
        self.assertGreater(
            result["runtime"]["actual_transport_reconnect_count"], 0
        )
        self.assertGreater(result["runtime"]["reconnect_count"], 0)

    async def test_periodic_clock_failure_has_deterministic_reason(self) -> None:
        calls = 0

        async def calibration(
            client, sample_count, lowest_count, utc_clock, monotonic_clock
        ):
            nonlocal calls
            calls += 1
            if calls == 1:
                return await fake_calibration(
                    client, sample_count, lowest_count,
                    utc_clock, monotonic_clock,
                )
            raise asyncio.TimeoutError

        config = replace(
            self.config,
            clock_recalibration_interval=timedelta(seconds=1),
        )
        result = await self.run_session(
            duration=0,
            config=config,
            stream_factory=lambda: FakeStream(
                (bridge(),), public_ready=True
            ),
            calibration_loader=calibration,
        )
        self.assertEqual(
            result["runtime"]["shutdown_reason"],
            "clock_calibration_failed",
        )
        self.assertNotEqual(
            result["runtime"]["shutdown_reason"], "normal_completion"
        )

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
            calibration_loader=fake_calibration,
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

    async def test_tolerated_clock_skew_does_not_reconnect_ready_book(self) -> None:
        snapshot_value = DepthSnapshot(
            symbol="BTCUSDC",
            last_update_id=100,
            bids=tuple(
                (D("100000") - D(index) / D("10"), D("10"))
                for index in range(5)
            ),
            asks=tuple(
                (D("100000.1") + D(index) / D("10"), D("5"))
                for index in range(5)
            ),
            exchange_time=BASE,
            received_at=BASE,
            received_monotonic_ns=0,
        )
        bridge_value = DepthDelta(
            symbol="BTCUSDC",
            first_update_id=100,
            final_update_id=101,
            previous_final_update_id=99,
            bids=(),
            asks=(),
            exchange_time=BASE,
            received_at=BASE,
            transaction_time=BASE,
            received_monotonic_ns=0,
        )
        live_value = DepthDelta(
            symbol="BTCUSDC",
            first_update_id=102,
            final_update_id=102,
            previous_final_update_id=101,
            bids=(),
            asks=(),
            exchange_time=BASE + timedelta(milliseconds=100),
            received_at=BASE + timedelta(milliseconds=20),
            transaction_time=BASE + timedelta(milliseconds=100),
            received_monotonic_ns=0,
        )

        async def load_snapshot(client, symbol):
            return snapshot_value

        async def skewed_calibration(
            client, sample_count, lowest_count, utc_clock, monotonic_clock
        ):
            del client
            samples = []
            for _ in range(sample_count):
                wall = utc_clock()
                monotonic = monotonic_clock()
                samples.append(HftClockSample(
                    local_send_wall=wall,
                    local_receive_wall=wall,
                    server_wall=wall + timedelta(milliseconds=75),
                    local_send_monotonic=monotonic,
                    local_receive_monotonic=monotonic,
                ))
            return robust_clock_calibration(tuple(samples), lowest_count)

        result = await self.run_session(
            duration=0,
            cycles=1,
            stream_factory=lambda: FakeStream(
                (bridge_value, live_value), public_ready=True
            ),
            snapshot_loader=load_snapshot,
            calibration_loader=skewed_calibration,
        )
        self.assertEqual(result["runtime"]["reconnect_count"], 0)
        self.assertEqual(result["runtime"]["clock"]["clock_health"], "HEALTHY")
        self.assertEqual(
            result["runtime"]["clock"]["corrected_feed_latency_ms"],
            D("-5"),
        )
        self.assertEqual(
            result["runtime"]["clock"]["negative_latency_observation_count"],
            1,
        )
        self.assertIn("vamp_price", result["runtime"]["alpha_state"])
        self.assertIn("alpha_bps", result["runtime"]["alpha_state"])
        self.assertEqual(result["account"].fills, [])
        self.assertFalse(any(
            event.get("event") == "websocket_disconnected"
            and event.get("stage") == "runtime"
            for event in result["emitted"]
        ))


class BlockedSocket:
    async def recv(self):
        await asyncio.Future()


class SocketContext:
    async def __aenter__(self):
        return BlockedSocket()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class ScriptedSocket:
    def __init__(self, messages):
        self.messages = list(messages)

    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Future()


class ScriptedSocketContext:
    def __init__(self, messages):
        self.socket = ScriptedSocket(messages)

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def depth_message(update_id: int) -> str:
    return (
        '{"e":"depthUpdate","E":1000,"s":"BTCUSDC",'
        f'"U":{update_id},"u":{update_id},"pu":{update_id - 1},'
        '"b":[],"a":[]}'
    )


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

    async def test_depth_events_preserve_order_without_silent_drop(self) -> None:
        def connection(url, **kwargs):
            del kwargs
            messages = (
                [depth_message(1), depth_message(2), depth_message(3)]
                if "/public/" in url else []
            )
            return ScriptedSocketContext(messages)

        stream = BinanceHftStream(
            read_timeout=1,
            shutdown_timeout=0.05,
            queue_maxsize=10,
        )
        with patch(
            "quantos.infrastructure.binance.hft.connect",
            side_effect=connection,
        ):
            generator = stream.events("BTCUSDC")
            received = [
                (await asyncio.wait_for(anext(generator), timeout=1)).final_update_id
                for _ in range(3)
            ]
            await generator.aclose()
        self.assertEqual(received, [1, 2, 3])
        self.assertEqual(stream.telemetry()["backpressure_overflow_count"], 0)

    async def test_stream_queue_overflow_is_explicit(self) -> None:
        lifecycle = []

        def connection(url, **kwargs):
            del kwargs
            messages = (
                [depth_message(value) for value in range(1, 100)]
                if "/public/" in url else []
            )
            return ScriptedSocketContext(messages)

        stream = BinanceHftStream(
            read_timeout=1,
            shutdown_timeout=0.05,
            queue_maxsize=1,
            lifecycle=lifecycle.append,
        )
        with patch(
            "quantos.infrastructure.binance.hft.connect",
            side_effect=connection,
        ):
            generator = stream.events("BTCUSDC")
            with self.assertRaises(BinanceHftBackpressureError):
                while True:
                    await asyncio.wait_for(anext(generator), timeout=1)
        self.assertGreater(
            stream.telemetry()["backpressure_overflow_count"], 0
        )
        self.assertTrue(any(
            event.get("event") == "websocket_backpressure"
            for event in lifecycle
        ))


if __name__ == "__main__":
    unittest.main()
