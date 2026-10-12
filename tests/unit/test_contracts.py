"""Tests for canonical Phase 1 domain-contract invariants."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from quantos.domain.alpha import AlphaAction, AlphaDecision
from quantos.domain.execution import (
    AccountSnapshot,
    ExecutionReport,
    ExecutionStatus,
    Position,
)
from quantos.domain.market_data import Candle, MarketEvent
from quantos.domain.risk import RiskDecision

UTC = timezone.utc
OPEN_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
CLOSE_TIME = OPEN_TIME + timedelta(minutes=1)


def candle() -> Candle:
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=OPEN_TIME,
        close_time=CLOSE_TIME,
        open=Decimal("100"),
        high=Decimal("103"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=Decimal("1.5"),
        quote_volume=Decimal("151.5"),
        trade_count=10,
    )


def position() -> Position:
    return Position(
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        average_entry_price=Decimal("100"),
        timestamp=CLOSE_TIME,
    )


class ContractTests(unittest.TestCase):
    def test_candle_has_explicit_completed_candle_semantics(self) -> None:
        value = candle()

        self.assertFalse(value.is_complete_at(CLOSE_TIME - timedelta(microseconds=1)))
        self.assertTrue(value.is_complete_at(CLOSE_TIME))
        self.assertEqual(MarketEvent(timestamp=CLOSE_TIME, candle=value).candle, value)

    def test_candle_rejects_naive_and_non_utc_timestamps(self) -> None:
        invalid_times = (
            datetime(2026, 1, 1, 0, 0),
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=7))),
        )
        for open_time in invalid_times:
            with self.subTest(open_time=open_time):
                with self.assertRaisesRegex(ValueError, "UTC"):
                    Candle(
                        symbol="BTCUSDT",
                        interval="1m",
                        open_time=open_time,
                        close_time=CLOSE_TIME,
                        open=Decimal("100"),
                        high=Decimal("103"),
                        low=Decimal("99"),
                        close=Decimal("102"),
                        volume=Decimal("1"),
                        quote_volume=Decimal("102"),
                        trade_count=1,
                    )

    def test_market_event_rejects_an_incomplete_candle(self) -> None:
        with self.assertRaisesRegex(ValueError, "incomplete"):
            MarketEvent(timestamp=CLOSE_TIME - timedelta(microseconds=1), candle=candle())

    def test_alpha_decision_requires_strategy_state(self) -> None:
        with self.assertRaises(TypeError):
            AlphaDecision(
                timestamp=CLOSE_TIME,
                symbol="BTCUSDT",
                strategy_version="strategy-v1",
                model_version="model-v1",
                feature_version="features-v1",
                action=AlphaAction.HOLD,
                reason="insufficient confidence",
                model_score=Decimal("0.10"),
            )

    def test_alpha_decision_requires_a_finite_decimal_model_score(self) -> None:
        valid = AlphaDecision(
            timestamp=CLOSE_TIME,
            symbol="BTCUSDT",
            strategy_version="strategy-v1",
            model_version="model-v1",
            feature_version="features-v1",
            action=AlphaAction.HOLD,
            reason="insufficient confidence",
            strategy_state="neutral",
            model_score=Decimal("0.10"),
        )

        self.assertEqual(valid.model_score, Decimal("0.10"))
        with self.assertRaises(TypeError):
            AlphaDecision(
                timestamp=CLOSE_TIME,
                symbol="BTCUSDT",
                strategy_version="strategy-v1",
                model_version="model-v1",
                feature_version="features-v1",
                action=AlphaAction.HOLD,
                reason="insufficient confidence",
                strategy_state="neutral",
            )
        for invalid_score in (None, "0.10", 0.10):
            with self.subTest(invalid_score=invalid_score):
                with self.assertRaisesRegex(ValueError, "model_score must be a Decimal"):
                    AlphaDecision(
                        timestamp=CLOSE_TIME,
                        symbol="BTCUSDT",
                        strategy_version="strategy-v1",
                        model_version="model-v1",
                        feature_version="features-v1",
                        action=AlphaAction.HOLD,
                        reason="insufficient confidence",
                        strategy_state="neutral",
                        model_score=invalid_score,
                    )
        with self.assertRaisesRegex(ValueError, "model_score must be finite"):
            AlphaDecision(
                timestamp=CLOSE_TIME,
                symbol="BTCUSDT",
                strategy_version="strategy-v1",
                model_version="model-v1",
                feature_version="features-v1",
                action=AlphaAction.HOLD,
                reason="insufficient confidence",
                strategy_state="neutral",
                model_score=Decimal("NaN"),
            )

    def test_risk_decision_has_no_execution_order_dependency(self) -> None:
        approved = RiskDecision(
            timestamp=CLOSE_TIME,
            symbol="BTCUSDT",
            approved=True,
            approved_quantity=Decimal("0.01"),
        )
        rejected = RiskDecision(
            timestamp=CLOSE_TIME,
            symbol="BTCUSDT",
            approved=False,
            rejection_reason="daily loss limit reached",
        )

        self.assertEqual(approved.approved_quantity, Decimal("0.01"))
        self.assertEqual(rejected.rejection_reason, "daily loss limit reached")
        with self.assertRaisesRegex(ValueError, "requires rejection_reason"):
            RiskDecision(timestamp=CLOSE_TIME, symbol="BTCUSDT", approved=False)

    def test_execution_report_supports_valid_lifecycle_states(self) -> None:
        reports = (
            ExecutionReport("order-1", CLOSE_TIME, ExecutionStatus.ACKNOWLEDGED, Decimal("1")),
            ExecutionReport(
                "order-1",
                CLOSE_TIME,
                ExecutionStatus.REJECTED,
                Decimal("1"),
                reason="insufficient edge",
            ),
            ExecutionReport(
                "order-1",
                CLOSE_TIME,
                ExecutionStatus.PARTIALLY_FILLED,
                Decimal("1"),
                filled_quantity=Decimal("0.4"),
                fill_price=Decimal("100"),
            ),
            ExecutionReport(
                "order-1",
                CLOSE_TIME,
                ExecutionStatus.FILLED,
                Decimal("1"),
                filled_quantity=Decimal("1"),
                fill_price=Decimal("100"),
            ),
            ExecutionReport(
                "order-1",
                CLOSE_TIME,
                ExecutionStatus.CANCELED,
                Decimal("1"),
                filled_quantity=Decimal("0.4"),
                fill_price=Decimal("100"),
            ),
            ExecutionReport(
                "order-1",
                CLOSE_TIME,
                ExecutionStatus.UNKNOWN,
                Decimal("1"),
                reason="exchange state unavailable",
            ),
        )

        self.assertEqual(len(reports), 6)

    def test_execution_report_rejects_contradictory_lifecycle_states(self) -> None:
        invalid_reports = (
            (
                ExecutionStatus.REJECTED,
                Decimal("1"),
                Decimal("0.1"),
                Decimal("100"),
                "rejected",
            ),
            (
                ExecutionStatus.PARTIALLY_FILLED,
                Decimal("1"),
                Decimal("1"),
                Decimal("100"),
                None,
            ),
            (
                ExecutionStatus.FILLED,
                Decimal("1"),
                Decimal("0.5"),
                Decimal("100"),
                None,
            ),
            (
                ExecutionStatus.CANCELED,
                Decimal("1"),
                Decimal("1"),
                Decimal("100"),
                None,
            ),
            (
                ExecutionStatus.UNKNOWN,
                Decimal("1"),
                Decimal("0"),
                None,
                None,
            ),
        )
        for status, requested, filled, price, reason in invalid_reports:
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    ExecutionReport(
                        "order-1",
                        CLOSE_TIME,
                        status,
                        requested,
                        filled_quantity=filled,
                        fill_price=price,
                        reason=reason,
                    )

    def test_account_snapshot_copies_and_validates_positions(self) -> None:
        caller_positions = [position()]
        snapshot = AccountSnapshot(
            timestamp=CLOSE_TIME,
            balances={"USDT": Decimal("20")},
            positions=caller_positions,
        )
        caller_positions.append(
            Position(
                symbol="ETHUSDT",
                quantity=Decimal("0.02"),
                average_entry_price=Decimal("200"),
                timestamp=CLOSE_TIME,
            )
        )

        self.assertEqual(snapshot.positions, (position(),))
        self.assertIsInstance(snapshot.positions, tuple)
        with self.assertRaises(FrozenInstanceError):
            snapshot.positions = ()
        with self.assertRaisesRegex(ValueError, "only Position"):
            AccountSnapshot(
                timestamp=CLOSE_TIME,
                balances={"USDT": Decimal("20")},
                positions=("not-a-position",),
            )
