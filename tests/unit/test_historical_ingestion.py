"""Tests for provider-neutral historical-ingestion orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from quantos.application import HistoricalIngestionError, ingest_historical_range
from quantos.domain.market_data import (
    Candle,
    DatasetIdentity,
    DatasetValidationError,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(minute: int, *, symbol: str = "BTCUSDT") -> Candle:
    open_time = START + timedelta(minutes=minute)
    return Candle(
        symbol=symbol,
        interval="1m",
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59, milliseconds=999),
        open=Decimal("100"),
        high=Decimal("103"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=Decimal("1"),
        quote_volume=Decimal("102"),
        trade_count=1,
    )


class FakeHistoricalRangeFetcher:
    def __init__(self, result: tuple[Candle, ...] | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    def fetch_open_time_range(self, **arguments: object) -> tuple[Candle, ...]:
        self.calls.append(arguments)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class HistoricalIngestionTests(unittest.TestCase):
    def ingest(
        self,
        fetcher: FakeHistoricalRangeFetcher,
        **overrides: object,
    ) -> ValidatedCandleSequence:
        arguments: dict[str, object] = {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "start_open_time": START,
            "end_open_time_exclusive": START + timedelta(minutes=3),
            "source": "binance-spot",
            "schema_version": "candle-v1",
            "ingestion_version": "ingestion-v1",
        }
        arguments.update(overrides)
        return ingest_historical_range(fetcher, **arguments)

    def test_ingests_one_and_multiple_candles_for_both_v1_symbols(self) -> None:
        scenarios = (
            ("BTCUSDT", (candle(0),)),
            (
                "ETHUSDT",
                tuple(candle(minute, symbol="ETHUSDT") for minute in range(3)),
            ),
        )
        for symbol, candidates in scenarios:
            with self.subTest(symbol=symbol, count=len(candidates)):
                fetcher = FakeHistoricalRangeFetcher(candidates)
                end_exclusive = START + timedelta(minutes=len(candidates))

                result = self.ingest(
                    fetcher,
                    symbol=symbol,
                    end_open_time_exclusive=end_exclusive,
                )

                self.assertIsInstance(result, ValidatedCandleSequence)
                self.assertEqual(result.candles, candidates)
                self.assertEqual(result.identity.symbol, symbol)
                self.assertEqual(result.identity.timeframe, "1m")
                self.assertEqual(result.identity.start_time, candidates[0].open_time)
                self.assertEqual(result.identity.end_time, candidates[-1].open_time)
                self.assertEqual(result.identity.source, "binance-spot")
                self.assertEqual(result.identity.schema_version, "candle-v1")
                self.assertEqual(result.identity.ingestion_version, "ingestion-v1")
                self.assertIs(
                    result.identity.validation_status,
                    DatasetValidationStatus.VALIDATED,
                )
                self.assertEqual(
                    fetcher.calls,
                    [
                        {
                            "symbol": symbol,
                            "interval": "1m",
                            "start_open_time": START,
                            "end_open_time_exclusive": end_exclusive,
                        }
                    ],
                )
                if len(candidates) == 1:
                    self.assertEqual(result.identity.start_time, result.identity.end_time)

    def test_identity_is_deterministic_and_uses_explicit_metadata(self) -> None:
        candidates = (candle(0), candle(1), candle(2))

        first = self.ingest(FakeHistoricalRangeFetcher(candidates))
        second = self.ingest(FakeHistoricalRangeFetcher(candidates))

        self.assertEqual(first.identity, second.identity)
        self.assertEqual(first.identity.source, "binance-spot")
        self.assertEqual(first.identity.schema_version, "candle-v1")
        self.assertEqual(first.identity.ingestion_version, "ingestion-v1")

    def test_identity_is_unvalidated_when_passed_to_canonical_validation(self) -> None:
        observed_statuses: list[DatasetValidationStatus] = []

        def recording_validation(
            identity: DatasetIdentity, candidates: Iterable[Candle]
        ) -> ValidatedCandleSequence:
            observed_statuses.append(identity.validation_status)
            return validate_candle_sequence(identity, candidates)

        with patch(
            "quantos.application.historical_ingestion.validate_candle_sequence",
            side_effect=recording_validation,
        ):
            result = self.ingest(
                FakeHistoricalRangeFetcher((candle(0),)),
                end_open_time_exclusive=START + timedelta(minutes=1),
            )

        self.assertEqual(observed_statuses, [DatasetValidationStatus.UNVALIDATED])
        self.assertIs(result.identity.validation_status, DatasetValidationStatus.VALIDATED)

    def test_rejects_empty_and_contiguous_partial_ranges(self) -> None:
        scenarios = (
            ("empty", ()),
            ("truncated prefix missing final", (candle(0), candle(1))),
            ("truncated suffix missing first", (candle(1), candle(2))),
            ("wrong count", (candle(0),)),
        )
        for description, candidates in scenarios:
            with self.subTest(description=description):
                fetcher = FakeHistoricalRangeFetcher(candidates)

                with self.assertRaises(HistoricalIngestionError):
                    self.ingest(fetcher)

                self.assertEqual(len(fetcher.calls), 1)

    def test_rejects_candles_outside_the_requested_range_without_filtering(self) -> None:
        scenarios = (
            ("before start", (candle(-1), candle(0), candle(1), candle(2))),
            ("at exclusive end", (candle(0), candle(1), candle(2), candle(3))),
        )
        for description, candidates in scenarios:
            with self.subTest(description=description):
                original = deepcopy(candidates)

                with self.assertRaisesRegex(HistoricalIngestionError, "outside"):
                    self.ingest(FakeHistoricalRangeFetcher(candidates))

                self.assertEqual(candidates, original)

    def test_delegates_sequence_integrity_failures_to_canonical_validation(self) -> None:
        wrong_interval = candle(1)
        object.__setattr__(wrong_interval, "interval", "5m")
        scenarios = (
            ("duplicate", (candle(0), candle(1), candle(1), candle(2)), "duplicate"),
            ("out of order", (candle(0), candle(1), candle(0), candle(2)), "out-of-order"),
            ("gap", (candle(0), candle(2)), "missing"),
            (
                "symbol mismatch",
                (candle(0), candle(1, symbol="ETHUSDT"), candle(2)),
                "symbol",
            ),
            ("interval mismatch", (candle(0), wrong_interval, candle(2)), "interval"),
        )
        for description, candidates, message in scenarios:
            with self.subTest(description=description):
                original = deepcopy(candidates)

                with self.assertRaisesRegex(DatasetValidationError, message):
                    self.ingest(FakeHistoricalRangeFetcher(candidates))

                self.assertEqual(candidates, original)

    def test_rejects_invalid_request_and_metadata_before_fetching(self) -> None:
        invalid_arguments = (
            ({"symbol": "SOLUSDT"}, "symbol"),
            ({"interval": "5m"}, "interval"),
            ({"start_open_time": datetime(2026, 1, 1)}, "UTC"),
            ({"end_open_time_exclusive": datetime(2026, 1, 1, 0, 3)}, "UTC"),
            (
                {
                    "start_open_time": datetime(
                        2026, 1, 1, tzinfo=timezone(timedelta(hours=7))
                    )
                },
                "UTC",
            ),
            (
                {
                    "end_open_time_exclusive": datetime(
                        2026, 1, 1, 0, 3, tzinfo=timezone(timedelta(hours=7))
                    )
                },
                "UTC",
            ),
            ({"end_open_time_exclusive": START}, "after"),
            ({"start_open_time": START + timedelta(minutes=4)}, "after"),
            ({"start_open_time": START + timedelta(seconds=1)}, "aligned"),
            (
                {"end_open_time_exclusive": START + timedelta(minutes=3, microseconds=1)},
                "aligned",
            ),
            ({"source": ""}, "source"),
            ({"schema_version": " "}, "schema_version"),
            ({"ingestion_version": ""}, "ingestion_version"),
        )
        for overrides, message in invalid_arguments:
            with self.subTest(overrides=overrides):
                fetcher = FakeHistoricalRangeFetcher(())

                with self.assertRaisesRegex(ValueError, message):
                    self.ingest(fetcher, **overrides)

                self.assertEqual(fetcher.calls, [])

    def test_provider_failure_propagates_without_retry(self) -> None:
        provider_error = RuntimeError("provider unavailable")
        fetcher = FakeHistoricalRangeFetcher(provider_error)

        with self.assertRaises(RuntimeError) as captured:
            self.ingest(fetcher)

        self.assertIs(captured.exception, provider_error)
        self.assertEqual(len(fetcher.calls), 1)

    def test_does_not_mutate_the_provider_candle_tuple(self) -> None:
        candidates = (candle(0), candle(1), candle(2))
        original = deepcopy(candidates)

        result = self.ingest(FakeHistoricalRangeFetcher(candidates))

        self.assertEqual(candidates, original)
        self.assertEqual(result.candles, candidates)
        self.assertIsInstance(result.candles, tuple)
