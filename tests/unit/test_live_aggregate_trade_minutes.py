"""Causal live minute-finalization and historical/live parity tests."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from quantos.application import (
    LiveAggregateTradeMinuteEngine,
    LiveAggregateTradeMinuteEngineError,
)
from quantos.domain.market_data.research_events import (
    LiveAggregateTradeConnectionEvent,
    LiveAggregateTradeConnectionKind,
    LiveAggregateTradeMinuteStatus,
    SourceTimestampUnit,
    aggregate_trade_minute_primitives,
)
from quantos.infrastructure.binance import normalize_live_aggregate_trade_message
from tests.unit.test_binance_live_aggregate_trades import combined_message


UTC = timezone.utc
BASE = datetime(2025, 1, 1, tzinfo=UTC)
ENDPOINT = "wss://data-stream.binance.vision/test"
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def connection(
    kind: LiveAggregateTradeConnectionKind,
    when: datetime,
    monotonic_ns: int,
    *,
    session_id: str = "session-1",
    reconnect_count: int = 0,
    reason: str | None = None,
) -> LiveAggregateTradeConnectionEvent:
    return LiveAggregateTradeConnectionEvent(
        kind=kind,
        session_id=session_id,
        observed_at=when,
        monotonic_ns=monotonic_ns,
        endpoint=ENDPOINT,
        symbols=SYMBOLS,
        timestamp_unit=SourceTimestampUnit.MICROSECOND,
        reconnect_count=reconnect_count,
        reason=reason,
    )


def observation(
    occurrence: datetime,
    local: datetime,
    monotonic_ns: int,
    *,
    symbol: str = "BTCUSDT",
    aggregate_trade_id: int = 100,
    session_id: str = "session-1",
    sequence: int = 1,
    buyer_is_maker: bool = False,
):
    emission = occurrence + timedelta(microseconds=100)
    return normalize_live_aggregate_trade_message(
        combined_message(
            symbol=symbol,
            aggregate_trade_id=aggregate_trade_id,
            occurrence=occurrence,
            emission=emission,
            buyer_is_maker=buyer_is_maker,
        ),
        expected_streams=frozenset(
            {"btcusdt@aggTrade", "ethusdt@aggTrade"}
        ),
        source_identity=ENDPOINT,
        session_id=session_id,
        receive_sequence=sequence,
        local_observation_time=local,
        local_monotonic_ns=monotonic_ns,
        source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
    )


class LiveAggregateTradeMinuteEngineTests(unittest.TestCase):
    def engine(self, *, allowance_seconds: int = 2):
        return LiveAggregateTradeMinuteEngine(
            symbols=SYMBOLS,
            lateness_allowance=timedelta(seconds=allowance_seconds),
        )

    def connect(self, engine, *, when=BASE, monotonic_ns=1):
        engine.process_connection(
            connection(
                LiveAggregateTradeConnectionKind.CONNECTED,
                when,
                monotonic_ns,
            )
        )

    def advance_watermark(self, engine, *, monotonic_ns: int = 4):
        later = observation(
            BASE + timedelta(minutes=1, seconds=3),
            BASE + timedelta(minutes=1, seconds=3, microseconds=200),
            monotonic_ns,
            aggregate_trade_id=102,
            sequence=3,
        )
        engine.process_observation(later)
        return later

    def test_exact_minute_membership_provisional_and_policy_finalization(self) -> None:
        engine = self.engine()
        self.connect(engine)
        at_start = observation(
            BASE,
            BASE + timedelta(microseconds=200),
            2,
            aggregate_trade_id=100,
        )
        before_end = observation(
            BASE + timedelta(seconds=59, microseconds=999999),
            BASE + timedelta(minutes=1, microseconds=200),
            3,
            aggregate_trade_id=101,
            sequence=2,
            buyer_is_maker=True,
        )
        engine.process_observation(at_start)
        engine.process_observation(before_end)
        provisional = engine.state(
            symbol="BTCUSDT", minute_start_time=BASE
        )
        self.assertIs(
            provisional.status, LiveAggregateTradeMinuteStatus.PROVISIONAL
        )

        self.advance_watermark(engine)

        finalized = engine.state(symbol="BTCUSDT", minute_start_time=BASE)
        next_minute = engine.state(
            symbol="BTCUSDT", minute_start_time=BASE + timedelta(minutes=1)
        )
        self.assertIs(
            finalized.status,
            LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY,
        )
        self.assertEqual(finalized.primitives.event_count, 2)
        self.assertEqual(finalized.primitives.aggressive_buy_event_count, 1)
        self.assertEqual(finalized.primitives.aggressive_sell_event_count, 1)
        self.assertEqual(next_minute.primitives.event_count, 1)

    def test_zero_event_minute_requires_healthy_coverage_and_later_watermark(self) -> None:
        engine = self.engine()
        self.connect(engine)
        self.advance_watermark(engine, monotonic_ns=2)
        zero = engine.state(symbol="BTCUSDT", minute_start_time=BASE)
        self.assertIs(
            zero.status,
            LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY,
        )
        self.assertEqual(zero.primitives.event_count, 0)

        partial = self.engine()
        self.connect(
            partial,
            when=BASE + timedelta(seconds=1),
            monotonic_ns=1,
        )
        self.advance_watermark(partial, monotonic_ns=2)
        incomplete = partial.state(symbol="BTCUSDT", minute_start_time=BASE)
        self.assertIs(
            incomplete.status,
            LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY,
        )

    def test_late_event_invalidates_finalized_causal_lineage_without_rewrite(self) -> None:
        engine = self.engine()
        self.connect(engine)
        first = observation(
            BASE + timedelta(seconds=10),
            BASE + timedelta(seconds=10, microseconds=200),
            2,
            aggregate_trade_id=100,
        )
        engine.process_observation(first)
        self.advance_watermark(engine, monotonic_ns=3)
        before = engine.state(symbol="BTCUSDT", minute_start_time=BASE)
        original_digest = before.primitives.content_sha256
        late = observation(
            BASE + timedelta(seconds=50),
            BASE + timedelta(minutes=1, seconds=4),
            4,
            aggregate_trade_id=101,
            sequence=4,
        )

        engine.process_observation(late)

        after = engine.state(symbol="BTCUSDT", minute_start_time=BASE)
        self.assertIs(
            after.status,
            LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY,
        )
        self.assertIn("after policy finalization", after.invalid_reason)
        self.assertEqual(after.primitives.content_sha256, original_digest)
        self.assertEqual(engine.late_event_violations, 1)

    def test_disconnect_marks_overlap_incomplete_and_reconnect_has_new_lineage(self) -> None:
        engine = self.engine()
        self.connect(engine)
        engine.process_observation(
            observation(
                BASE + timedelta(seconds=10),
                BASE + timedelta(seconds=10, microseconds=200),
                2,
            )
        )
        engine.process_connection(
            connection(
                LiveAggregateTradeConnectionKind.DISCONNECTED,
                BASE + timedelta(seconds=30),
                3,
                reason="socket_closed",
            )
        )
        first = engine.state(symbol="BTCUSDT", minute_start_time=BASE)
        self.assertIs(
            first.status,
            LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY,
        )

        second_start = BASE + timedelta(minutes=1)
        engine.process_connection(
            connection(
                LiveAggregateTradeConnectionKind.CONNECTED,
                second_start,
                4,
                session_id="session-2",
                reconnect_count=1,
            )
        )
        engine.process_observation(
            observation(
                second_start + timedelta(seconds=10),
                second_start + timedelta(seconds=10, microseconds=200),
                5,
                aggregate_trade_id=200,
                session_id="session-2",
            )
        )
        engine.process_observation(
            observation(
                second_start + timedelta(minutes=1, seconds=3),
                second_start + timedelta(minutes=1, seconds=3, microseconds=200),
                6,
                aggregate_trade_id=201,
                session_id="session-2",
                sequence=2,
            )
        )
        second = engine.state(
            symbol="BTCUSDT", minute_start_time=second_start
        )
        self.assertIs(
            second.status,
            LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY,
        )
        self.assertEqual(second.session_ids, ("session-2",))

    def test_conflicting_duplicate_and_chronological_reversal_fail_closed(self) -> None:
        for conflict in (True, False):
            with self.subTest(conflict=conflict):
                engine = self.engine()
                self.connect(engine)
                first = observation(
                    BASE + timedelta(seconds=10),
                    BASE + timedelta(seconds=10, microseconds=200),
                    2,
                    aggregate_trade_id=100,
                )
                engine.process_observation(first)
                second = observation(
                    (
                        BASE + timedelta(seconds=10)
                        if conflict
                        else BASE + timedelta(seconds=9)
                    ),
                    BASE + timedelta(seconds=11),
                    3,
                    aggregate_trade_id=(100 if conflict else 99),
                    sequence=2,
                    buyer_is_maker=conflict,
                )
                with self.assertRaises(LiveAggregateTradeMinuteEngineError):
                    engine.process_observation(second)
                state = engine.state(symbol="BTCUSDT", minute_start_time=BASE)
                self.assertIs(
                    state.status,
                    LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY,
                )

    def test_identical_inputs_have_historical_live_neutral_digest_equality(self) -> None:
        engine = self.engine()
        self.connect(engine)
        observations = (
            observation(
                BASE + timedelta(seconds=1),
                BASE + timedelta(seconds=1, microseconds=200),
                2,
                aggregate_trade_id=100,
            ),
            observation(
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=2, microseconds=200),
                3,
                aggregate_trade_id=101,
                sequence=2,
                buyer_is_maker=True,
            ),
        )
        for item in observations:
            engine.process_observation(item)
        self.advance_watermark(engine, monotonic_ns=4)
        live = engine.state(symbol="BTCUSDT", minute_start_time=BASE)
        historical = aggregate_trade_minute_primitives(
            symbol="BTCUSDT",
            minute_start_time=BASE,
            events=tuple(item.trade for item in observations),
        )
        self.assertEqual(live.primitives, historical)
        self.assertEqual(
            live.primitives.content_sha256,
            historical.content_sha256,
        )

    def test_replay_is_deterministic_and_local_clock_regression_is_fatal(self) -> None:
        def run():
            engine = self.engine()
            self.connect(engine)
            engine.process_observation(
                observation(
                    BASE + timedelta(seconds=1),
                    BASE + timedelta(seconds=1, microseconds=200),
                    2,
                )
            )
            self.advance_watermark(engine, monotonic_ns=3)
            return engine.states()

        self.assertEqual(run(), run())

        engine = self.engine()
        self.connect(engine)
        with self.assertRaisesRegex(
            LiveAggregateTradeMinuteEngineError, "wall clock regressed"
        ):
            engine.advance_time(
                now=BASE - timedelta(microseconds=1), monotonic_ns=2
            )

    def test_core_aggregation_and_live_engine_contain_no_float_or_predictive_fields(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for relative in (
            "src/quantos/domain/market_data/research_events/minute_primitives.py",
            "src/quantos/application/live_aggregate_trade_minutes.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            self.assertNotIn("future_return", source)
            self.assertNotIn("imbalance", source)
            self.assertNotIn("zscore", source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant):
                    self.assertNotIsInstance(node.value, float)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, "float")


if __name__ == "__main__":
    unittest.main()
