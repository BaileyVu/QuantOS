from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest

from quantos.application.hft_trader import HftPaperEngine
from quantos.domain.evaluation.hft import HftEvaluation
from quantos.domain.execution.hft_paper import HftPaperAccount
from quantos.domain.market_data.hft import DepthDelta, DepthSnapshot
from quantos.domain.market_data.hft_clock import (
    HftClockHealth,
    HftClockMonitor,
    HftClockSample,
    robust_clock_calibration,
)
from quantos.infrastructure.binance.hft import normalize_hft_message
from quantos.infrastructure.configuration.hft import load_hft_config

from tests.unit.test_hft_paper import fees, rules


D = Decimal
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def sample(
    offset_ms: str,
    rtt_ms: str,
    *,
    send_wall: datetime = BASE,
    send_monotonic: float = 10.0,
) -> HftClockSample:
    rtt = D(rtt_ms)
    receive_wall = send_wall + timedelta(
        microseconds=int(rtt * D("1000"))
    )
    midpoint = send_wall + (receive_wall - send_wall) / 2
    server = midpoint + timedelta(
        microseconds=int(D(offset_ms) * D("1000"))
    )
    return HftClockSample(
        local_send_wall=send_wall,
        local_receive_wall=receive_wall,
        server_wall=server,
        local_send_monotonic=send_monotonic,
        local_receive_monotonic=(
            send_monotonic + float(rtt / D("1000"))
        ),
    )


def monitor(offset_ms: str = "0") -> HftClockMonitor:
    result = HftClockMonitor(D("5"), 3, timedelta(minutes=5))
    result.apply_calibration(robust_clock_calibration(
        (sample(offset_ms, "2"),), 1
    ))
    return result


def deep_snapshot() -> DepthSnapshot:
    return DepthSnapshot(
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


def depth(
    first: int,
    final: int,
    previous: int,
    *,
    exchange_ms: int,
    receive_ms: int,
) -> DepthDelta:
    return DepthDelta(
        symbol="BTCUSDC",
        first_update_id=first,
        final_update_id=final,
        previous_final_update_id=previous,
        bids=(),
        asks=(),
        exchange_time=BASE + timedelta(milliseconds=exchange_ms),
        received_at=BASE + timedelta(milliseconds=receive_ms),
        transaction_time=BASE + timedelta(milliseconds=exchange_ms),
        received_monotonic_ns=receive_ms * 1_000_000,
    )


class HftClockCalibrationTests(unittest.TestCase):
    def test_midpoint_offset_calibration(self) -> None:
        observation = sample("125", "40")
        calibration = robust_clock_calibration((observation,), 1)
        self.assertEqual(observation.local_midpoint, BASE + timedelta(milliseconds=20))
        self.assertLess(abs(observation.rtt_ms - D("40")), D("0.000001"))
        self.assertEqual(calibration.offset_ms, D("125"))

    def test_lowest_rtt_samples_use_robust_median(self) -> None:
        calibration = robust_clock_calibration((
            sample("1000", "50"),
            sample("30", "3"),
            sample("10", "1"),
            sample("20", "2"),
        ), 3)
        self.assertEqual(calibration.offset_ms, D("20"))
        self.assertEqual(
            [item.offset_ms for item in calibration.selected_samples],
            [D("10"), D("20"), D("30")],
        )

    def test_server_ahead_is_corrected_without_failure(self) -> None:
        clock = monitor("100")
        latency = clock.observe(
            BASE + timedelta(milliseconds=100),
            BASE + timedelta(milliseconds=20),
        )
        self.assertEqual(latency.raw_uncalibrated_latency_ms, D("-80"))
        self.assertEqual(latency.corrected_signed_latency_ms, D("20"))
        self.assertEqual(clock.health, HftClockHealth.HEALTHY)

    def test_server_behind_is_corrected_without_failure(self) -> None:
        clock = monitor("-100")
        latency = clock.observe(
            BASE - timedelta(milliseconds=100),
            BASE + timedelta(milliseconds=20),
        )
        self.assertEqual(latency.raw_uncalibrated_latency_ms, D("120"))
        self.assertEqual(latency.corrected_signed_latency_ms, D("20"))
        self.assertEqual(clock.health, HftClockHealth.HEALTHY)

    def test_monotonic_rtt_never_uses_epoch_wall_duration(self) -> None:
        observation = HftClockSample(
            local_send_wall=BASE,
            local_receive_wall=BASE + timedelta(seconds=10),
            server_wall=BASE + timedelta(seconds=5),
            local_send_monotonic=500.0,
            local_receive_monotonic=500.01,
        )
        self.assertEqual(observation.rtt_ms, D("9.999999999990905"))
        self.assertEqual(observation.offset_ms, D("0"))

    def test_slight_negative_is_preserved_but_tolerated(self) -> None:
        clock = monitor("75")
        latency = clock.observe(
            BASE + timedelta(milliseconds=100),
            BASE + timedelta(milliseconds=20),
        )
        self.assertEqual(latency.corrected_signed_latency_ms, D("-5"))
        self.assertEqual(latency.observed_feed_latency_ms, D("0"))
        self.assertEqual(clock.negative_latency_observation_count, 1)
        self.assertEqual(clock.excessive_negative_latency_count, 0)
        self.assertTrue(clock.quoting_allowed)

    def test_repeated_excessive_negative_recalibrates_then_fails_closed(self) -> None:
        clock = monitor("0")
        for _ in range(3):
            clock.observe(BASE + timedelta(milliseconds=100), BASE)
        self.assertEqual(clock.health, HftClockHealth.DEGRADED)
        self.assertTrue(clock.recalibration_requested)
        clock.apply_calibration(robust_clock_calibration(
            (sample("0", "1", send_monotonic=20),), 1
        ))
        for _ in range(3):
            clock.observe(BASE + timedelta(milliseconds=100), BASE)
        self.assertEqual(clock.health, HftClockHealth.UNSAFE)
        self.assertFalse(clock.quoting_allowed)

    def test_recalibration_does_not_reset_valid_book(self) -> None:
        config = load_hft_config(Path("configs/futures_hft_paper.toml"))
        engine = HftPaperEngine(
            config,
            rules(),
            HftPaperAccount(D("100"), fees()),
            HftEvaluation(D("100")),
        )
        engine.bootstrap(
            deep_snapshot(),
            (depth(100, 101, 99, exchange_ms=0, receive_ms=0),),
        )
        clock = monitor("0")
        before = (
            engine.book.last_update_id,
            engine.book.best_bid,
            engine.book.best_ask,
        )
        clock.apply_calibration(robust_clock_calibration(
            (sample("20", "1", send_monotonic=20),), 1
        ))
        self.assertTrue(engine.book.valid)
        self.assertEqual(before, (
            engine.book.last_update_id,
            engine.book.best_bid,
            engine.book.best_ask,
        ))

    def test_tolerated_skew_keeps_book_ready_and_alpha_processing_runs(self) -> None:
        config = load_hft_config(Path("configs/futures_hft_paper.toml"))
        account = HftPaperAccount(D("100"), fees())
        engine = HftPaperEngine(
            config, rules(), account, HftEvaluation(D("100"))
        )
        engine.bootstrap(
            deep_snapshot(),
            (depth(100, 101, 99, exchange_ms=0, receive_ms=0),),
        )
        clock = monitor("75")
        event = depth(
            102, 102, 101, exchange_ms=100, receive_ms=20
        )
        latency = clock.observe(event.exchange_time, event.received_at)
        result = engine.on_event(
            event,
            BASE + timedelta(milliseconds=21),
            monotonic_now=D("0.021"),
            latency=latency,
            allow_quoting=clock.quoting_allowed,
        )
        self.assertTrue(engine.book.valid)
        self.assertIsNotNone(engine.last_features)
        self.assertIsNotNone(engine.last_features.vamp_price)
        self.assertIsNotNone(engine.last_features.alpha_bps)
        self.assertNotEqual(result["event"], "hft_clock_hold")
        self.assertEqual(account.fills, [])

    def test_exchange_event_and_transaction_timestamps_are_distinct(self) -> None:
        event = normalize_hft_message({
            "e": "aggTrade", "E": 1000, "T": 900, "s": "BTCUSDC",
            "a": 1, "p": "100", "q": "1", "m": True,
        }, BASE, 123)
        self.assertEqual(event.exchange_time.timestamp(), 1)
        self.assertEqual(event.transaction_time.timestamp(), 0.9)
        self.assertEqual(event.received_monotonic_ns, 123)

    def test_zero_activity_hourly_rates_are_canonical_zero(self) -> None:
        account = HftPaperAccount(D("100"), fees())
        report = HftEvaluation(D("100")).report(account, D("0.000001"))
        self.assertEqual(str(report["trades_per_hour"]), "0")
        self.assertEqual(str(report["round_trips_per_hour"]), "0")


if __name__ == "__main__":
    unittest.main()
