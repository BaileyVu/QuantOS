"""Tests for provider-independent Market Data identity and sequence validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from quantos.domain.market_data import (
    Candle,
    DatasetIdentity,
    DatasetValidationError,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def candle(
    minute: int,
    *,
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    close_offset: timedelta = timedelta(minutes=1),
    high: Decimal = Decimal("103"),
    low: Decimal = Decimal("99"),
    volume: Decimal = Decimal("1"),
) -> Candle:
    open_time = START + timedelta(minutes=minute)
    return Candle(
        symbol=symbol,
        interval=interval,
        open_time=open_time,
        close_time=open_time + close_offset,
        open=Decimal("100"),
        high=high,
        low=low,
        close=Decimal("102"),
        volume=volume,
        quote_volume=Decimal("102"),
        trade_count=1,
    )


def identity(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    start_time: datetime = START,
    end_time: datetime = START + timedelta(minutes=3),
) -> DatasetIdentity:
    return DatasetIdentity(
        symbol=symbol,
        timeframe=timeframe,
        start_time=start_time,
        end_time=end_time,
        source="fixture-source",
        schema_version="v1",
        ingestion_version="ingestion-1",
    )


class DatasetIdentityTests(unittest.TestCase):
    def test_identity_is_deterministic_for_both_v1_symbols(self) -> None:
        btc_identity = identity()
        eth_identity = identity(symbol="ETHUSDT")

        self.assertEqual(btc_identity, identity())
        self.assertEqual(btc_identity.symbol, "BTCUSDT")
        self.assertEqual(eth_identity.symbol, "ETHUSDT")
        self.assertIs(btc_identity.validation_status, DatasetValidationStatus.UNVALIDATED)

    def test_identity_rejects_unsupported_symbol_and_non_1m_timeframe(self) -> None:
        with self.assertRaisesRegex(ValueError, "symbol"):
            identity(symbol="SOLUSDT")
        with self.assertRaisesRegex(ValueError, "timeframe"):
            identity(timeframe="5m")

    def test_identity_rejects_naive_non_utc_and_invalid_time_ranges(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC"):
            identity(start_time=datetime(2026, 1, 1, 0, 0))
        with self.assertRaisesRegex(ValueError, "UTC"):
            identity(end_time=datetime(2026, 1, 1, 0, 3, tzinfo=timezone(timedelta(hours=7))))
        with self.assertRaisesRegex(ValueError, "end_time"):
            identity(end_time=START - timedelta(minutes=1))


class CandleValidationTests(unittest.TestCase):
    def test_valid_single_symbol_sequence_is_preserved_in_input_order(self) -> None:
        candles = [candle(0), candle(1), candle(2)]

        result = validate_candle_sequence(identity(), candles)

        self.assertIsInstance(result, ValidatedCandleSequence)
        self.assertEqual(result.candles, tuple(candles))
        self.assertIs(result.identity.validation_status, DatasetValidationStatus.VALIDATED)
        self.assertEqual(candles, [candle(0), candle(1), candle(2)])

    def test_rejects_conflicting_duplicate_timestamps_without_deduplicating(self) -> None:
        candles = [candle(0), candle(1), candle(1, volume=Decimal("2"))]
        unvalidated_identity = identity()

        with self.assertRaisesRegex(DatasetValidationError, "duplicate"):
            validate_candle_sequence(unvalidated_identity, candles)

        self.assertEqual(candles, [candle(0), candle(1), candle(1, volume=Decimal("2"))])
        self.assertIs(unvalidated_identity.validation_status, DatasetValidationStatus.UNVALIDATED)

    def test_rejects_identical_duplicate_timestamps(self) -> None:
        with self.assertRaisesRegex(DatasetValidationError, "duplicate"):
            validate_candle_sequence(identity(), [candle(0), candle(1), candle(1)])

    def test_rejects_missing_candles_without_synthesizing(self) -> None:
        candles = [candle(0), candle(2)]

        with self.assertRaisesRegex(DatasetValidationError, "missing"):
            validate_candle_sequence(identity(), candles)

        self.assertEqual(candles, [candle(0), candle(2)])

    def test_rejects_out_of_order_candles_without_sorting(self) -> None:
        candles = [candle(1), candle(0)]

        with self.assertRaisesRegex(DatasetValidationError, "out-of-order"):
            validate_candle_sequence(identity(), candles)

        self.assertEqual(candles, [candle(1), candle(0)])

    def test_rejects_empty_sequence_without_a_validated_output(self) -> None:
        unvalidated_identity = identity()

        with self.assertRaisesRegex(DatasetValidationError, "must not be empty"):
            validate_candle_sequence(unvalidated_identity, [])

        self.assertIs(unvalidated_identity.validation_status, DatasetValidationStatus.UNVALIDATED)

    def test_validates_a_one_candle_identity_with_equal_start_and_end_times(self) -> None:
        result = validate_candle_sequence(
            identity(start_time=START, end_time=START),
            [candle(0, close_offset=timedelta(seconds=30))],
        )

        self.assertEqual(result.candles, (candle(0, close_offset=timedelta(seconds=30)),))
        self.assertIs(result.identity.validation_status, DatasetValidationStatus.VALIDATED)

    def test_rejects_revalidation_of_a_validated_identity(self) -> None:
        result = validate_candle_sequence(identity(), [candle(0)])

        with self.assertRaisesRegex(DatasetValidationError, "must be unvalidated"):
            validate_candle_sequence(result.identity, result.candles)

    def test_direct_validated_sequence_rejects_empty_and_unvalidated_inputs(self) -> None:
        validated_identity = validate_candle_sequence(identity(), [candle(0)]).identity

        with self.assertRaisesRegex(DatasetValidationError, "must not be empty"):
            ValidatedCandleSequence(validated_identity, [])
        with self.assertRaisesRegex(DatasetValidationError, "require a validated"):
            ValidatedCandleSequence(identity(), [candle(0)])

    def test_direct_validated_sequence_rejects_invalid_sequence_invariants(self) -> None:
        validated_identity = validate_candle_sequence(identity(), [candle(0), candle(1)]).identity
        invalid_sequences = {
            "duplicate": [candle(0), candle(0, volume=Decimal("2"))],
            "out-of-order": [candle(1), candle(0)],
            "missing": [candle(0), candle(2)],
            "symbol mismatch": [candle(0), candle(1, symbol="ETHUSDT")],
        }

        for description, invalid_sequence in invalid_sequences.items():
            with self.subTest(description=description):
                with self.assertRaises(DatasetValidationError):
                    ValidatedCandleSequence(validated_identity, invalid_sequence)

    def test_direct_validated_sequence_copies_mutable_input_to_an_immutable_tuple(self) -> None:
        source_candles = [candle(0), candle(1)]
        validated_identity = validate_candle_sequence(identity(), source_candles).identity

        result = ValidatedCandleSequence(validated_identity, source_candles)
        source_candles.append(candle(2))

        self.assertEqual(result.candles, (candle(0), candle(1)))
        self.assertIsInstance(result.candles, tuple)

    def test_rejects_sequence_symbol_mismatch(self) -> None:
        with self.assertRaisesRegex(DatasetValidationError, "symbol"):
            validate_candle_sequence(identity(), [candle(0), candle(1, symbol="ETHUSDT")])

    def test_rejects_candle_contract_interval_and_invalid_time_mismatches(self) -> None:
        with self.assertRaisesRegex(ValueError, "interval"):
            candle(0, interval="5m")
        with self.assertRaisesRegex(ValueError, "close_time"):
            candle(0, close_offset=timedelta(0))

    def test_uses_open_time_spacing_not_close_time_for_continuity(self) -> None:
        result = validate_candle_sequence(
            identity(),
            [
                candle(0, close_offset=timedelta(seconds=30)),
                candle(1, close_offset=timedelta(seconds=10)),
            ],
        )

        self.assertEqual(len(result.candles), 2)

    def test_rejects_invalid_ohlc_and_negative_volume_at_candle_construction(self) -> None:
        with self.assertRaisesRegex(ValueError, "high"):
            candle(0, high=Decimal("101"))
        with self.assertRaisesRegex(ValueError, "volume"):
            candle(0, volume=Decimal("-1"))
