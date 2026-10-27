"""Offline tests for canonical historical persistence orchestration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quantos.application import (
    HistoricalIngestionError,
    PersistedHistoricalDataset,
    extend_and_persist_historical_range,
    ingest_and_persist_historical_range,
)
from quantos.domain.market_data import (
    Candle,
    DatasetIdentity,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
MODULE = "quantos.application.historical_ingestion"


def candle(minute: int, *, symbol: str = "BTCUSDT") -> Candle:
    open_time = START + timedelta(minutes=minute)
    return Candle(
        symbol=symbol,
        interval="1m",
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59, milliseconds=999),
        open=Decimal("100.000000000000000001"),
        high=Decimal("103.000000000000000001"),
        low=Decimal("99.000000000000000001"),
        close=Decimal("102.000000000000000001"),
        volume=Decimal("1.000000000000000001"),
        quote_volume=Decimal("102.000000000000000102"),
        trade_count=7,
    )


def sequence(
    *,
    symbol: str = "BTCUSDT",
    count: int = 3,
    start_minute: int = 0,
    ingestion_version: str = "base-v1",
) -> ValidatedCandleSequence:
    candles = tuple(
        candle(start_minute + offset, symbol=symbol) for offset in range(count)
    )
    return validate_candle_sequence(
        DatasetIdentity(
            symbol=symbol,
            timeframe="1m",
            start_time=candles[0].open_time,
            end_time=candles[-1].open_time,
            source="binance-spot",
            schema_version="candle-v1",
            ingestion_version=ingestion_version,
        ),
        candles,
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


class FakeCanonicalWriter:
    def __init__(self, path: object, error: Exception | None = None) -> None:
        self._path = path
        self._error = error
        self.calls: list[ValidatedCandleSequence] = []

    def write(self, value: ValidatedCandleSequence) -> Path:
        self.calls.append(value)
        if self._error is not None:
            raise self._error
        return self._path  # type: ignore[return-value]


class HistoricalPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def initial(
        self,
        fetcher: FakeHistoricalRangeFetcher,
        writer: FakeCanonicalWriter,
        *,
        symbol: str = "BTCUSDT",
        count: int = 3,
    ) -> PersistedHistoricalDataset:
        return ingest_and_persist_historical_range(
            fetcher,
            writer,
            symbol=symbol,
            interval="1m",
            start_open_time=START,
            end_open_time_exclusive=START + timedelta(minutes=count),
            source="binance-spot",
            schema_version="candle-v1",
            ingestion_version="initial-v1",
        )

    def extend(
        self,
        existing: object,
        fetcher: FakeHistoricalRangeFetcher,
        writer: FakeCanonicalWriter,
        *,
        end_open_time_exclusive: datetime = START + timedelta(minutes=6),
        ingestion_version: object = "extended-v2",
    ) -> PersistedHistoricalDataset:
        return extend_and_persist_historical_range(
            existing,  # type: ignore[arg-type]
            fetcher,
            writer,
            end_open_time_exclusive=end_open_time_exclusive,
            ingestion_version=ingestion_version,  # type: ignore[arg-type]
        )

    def test_initial_ingestion_validates_then_persists_both_v1_symbols(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                candidates = tuple(candle(index, symbol=symbol) for index in range(3))
                fetcher = FakeHistoricalRangeFetcher(candidates)
                path = self.root / f"{symbol}.parquet"
                writer = FakeCanonicalWriter(path)

                result = self.initial(fetcher, writer, symbol=symbol)

                self.assertEqual(
                    fetcher.calls,
                    [
                        {
                            "symbol": symbol,
                            "interval": "1m",
                            "start_open_time": START,
                            "end_open_time_exclusive": START
                            + timedelta(minutes=3),
                        }
                    ],
                )
                self.assertEqual(len(writer.calls), 1)
                persisted = writer.calls[0]
                self.assertEqual(persisted.candles, candidates)
                self.assertIs(
                    persisted.identity.validation_status,
                    DatasetValidationStatus.VALIDATED,
                )
                self.assertEqual(result.identity, persisted.identity)
                self.assertEqual(result.path, path)

    def test_initial_provider_and_validation_failures_never_call_writer(self) -> None:
        provider_error = RuntimeError("provider unavailable")
        fetcher = FakeHistoricalRangeFetcher(provider_error)
        writer = FakeCanonicalWriter(self.root / "unused.parquet")
        with self.assertRaises(RuntimeError) as captured:
            self.initial(fetcher, writer)
        self.assertIs(captured.exception, provider_error)
        self.assertEqual(writer.calls, [])

        fetcher = FakeHistoricalRangeFetcher((candle(0), candle(2)))
        writer = FakeCanonicalWriter(self.root / "unused.parquet")
        with self.assertRaises(ValueError):
            self.initial(fetcher, writer)
        self.assertEqual(writer.calls, [])

    def test_initial_writer_failure_propagates_without_success_result(self) -> None:
        error = OSError("publication failed")
        fetcher = FakeHistoricalRangeFetcher(tuple(candle(index) for index in range(3)))
        writer = FakeCanonicalWriter(self.root / "unused.parquet", error)

        with self.assertRaises(OSError) as captured:
            self.initial(fetcher, writer)

        self.assertIs(captured.exception, error)
        self.assertEqual(len(writer.calls), 1)
        self.assertIs(
            writer.calls[0].identity.validation_status,
            DatasetValidationStatus.VALIDATED,
        )

    def test_persisted_result_is_frozen_explicit_and_rejects_bad_writer_path(self) -> None:
        writer = FakeCanonicalWriter("not-a-Path")
        with self.assertRaisesRegex(HistoricalIngestionError, "return a Path"):
            self.initial(
                FakeHistoricalRangeFetcher(tuple(candle(index) for index in range(3))),
                writer,
            )

        path = self.root / "canonical.parquet"
        result = self.initial(
            FakeHistoricalRangeFetcher(tuple(candle(index) for index in range(3))),
            FakeCanonicalWriter(path),
        )
        self.assertFalse(hasattr(result, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            result.path = self.root / "other.parquet"  # type: ignore[misc]

    def test_incremental_happy_paths_fetch_only_suffix_and_preserve_authority(self) -> None:
        scenarios = (
            ("BTCUSDT", 1),
            ("ETHUSDT", 1_440),
            ("BTCUSDT", 2_880),
        )
        for symbol, extension_count in scenarios:
            with self.subTest(symbol=symbol, extension_count=extension_count):
                existing = sequence(symbol=symbol, count=2)
                original = deepcopy(existing)
                extension = tuple(
                    candle(2 + offset, symbol=symbol)
                    for offset in range(extension_count)
                )
                fetcher = FakeHistoricalRangeFetcher(extension)
                path = self.root / f"{symbol}-{extension_count}.parquet"
                writer = FakeCanonicalWriter(path)
                requested_end = START + timedelta(minutes=2 + extension_count)

                result = self.extend(
                    existing,
                    fetcher,
                    writer,
                    end_open_time_exclusive=requested_end,
                )

                self.assertEqual(
                    fetcher.calls,
                    [
                        {
                            "symbol": symbol,
                            "interval": "1m",
                            "start_open_time": START + timedelta(minutes=2),
                            "end_open_time_exclusive": requested_end,
                        }
                    ],
                )
                self.assertEqual(len(writer.calls), 1)
                combined = writer.calls[0]
                self.assertEqual(combined.candles, existing.candles + extension)
                self.assertEqual(combined.identity.symbol, symbol)
                self.assertEqual(combined.identity.timeframe, "1m")
                self.assertEqual(combined.identity.start_time, START)
                self.assertEqual(
                    combined.identity.end_time, requested_end - timedelta(minutes=1)
                )
                self.assertEqual(combined.identity.source, existing.identity.source)
                self.assertEqual(
                    combined.identity.schema_version, existing.identity.schema_version
                )
                self.assertEqual(combined.identity.ingestion_version, "extended-v2")
                self.assertIs(
                    combined.identity.validation_status,
                    DatasetValidationStatus.VALIDATED,
                )
                self.assertEqual(result.identity, combined.identity)
                self.assertEqual(result.path, path)
                self.assertEqual(existing, original)

    def test_incremental_runs_canonical_validation_on_extension_and_full_result(self) -> None:
        existing = sequence(count=3)
        extension = tuple(candle(index) for index in range(3, 6))
        writer = FakeCanonicalWriter(self.root / "combined.parquet")

        with patch(
            f"{MODULE}.validate_candle_sequence", wraps=validate_candle_sequence
        ) as validation:
            self.extend(existing, FakeHistoricalRangeFetcher(extension), writer)

        self.assertEqual(validation.call_count, 2)
        extension_identity, extension_candidates = validation.call_args_list[0].args
        self.assertEqual(extension_identity.start_time, START + timedelta(minutes=3))
        self.assertEqual(extension_candidates, extension)
        combined_identity, combined_candidates = validation.call_args_list[1].args
        self.assertEqual(combined_identity.start_time, existing.identity.start_time)
        self.assertEqual(combined_identity.end_time, extension[-1].open_time)
        self.assertEqual(combined_identity.ingestion_version, "extended-v2")
        self.assertIs(
            combined_identity.validation_status, DatasetValidationStatus.UNVALIDATED
        )
        self.assertEqual(combined_candidates, existing.candles + extension)

    def test_repeated_identical_extension_is_deterministic_and_preserves_base(self) -> None:
        existing = sequence(count=3)
        original = deepcopy(existing)
        extension = tuple(candle(index) for index in range(3, 6))
        writer = FakeCanonicalWriter(self.root / "same.parquet")

        first = self.extend(
            existing, FakeHistoricalRangeFetcher(extension), writer
        )
        second = self.extend(
            existing, FakeHistoricalRangeFetcher(extension), writer
        )

        self.assertEqual(first, second)
        self.assertEqual(writer.calls[0], writer.calls[1])
        self.assertEqual(existing, original)

    def test_invalid_incremental_requests_fail_before_fetch_or_write(self) -> None:
        existing = sequence(count=3)
        extension_start = START + timedelta(minutes=3)
        request_cases = (
            ("end equal start", extension_start, "extended-v2"),
            ("end before start", extension_start - timedelta(minutes=1), "extended-v2"),
            ("naive end", datetime(2026, 1, 1, 0, 6), "extended-v2"),
            (
                "non-UTC end",
                datetime(
                    2026,
                    1,
                    1,
                    7,
                    6,
                    tzinfo=timezone(timedelta(hours=7)),
                ),
                "extended-v2",
            ),
            (
                "unaligned end",
                START + timedelta(minutes=6, microseconds=1),
                "extended-v2",
            ),
            ("empty version", START + timedelta(minutes=6), ""),
            ("blank version", START + timedelta(minutes=6), " "),
            ("unchanged version", START + timedelta(minutes=6), "base-v1"),
            ("non-string version", START + timedelta(minutes=6), None),
        )
        for description, requested_end, version in request_cases:
            with self.subTest(description=description):
                fetcher = FakeHistoricalRangeFetcher(
                    tuple(candle(index) for index in range(3, 6))
                )
                writer = FakeCanonicalWriter(self.root / "unused.parquet")

                with self.assertRaises(ValueError):
                    self.extend(
                        existing,
                        fetcher,
                        writer,
                        end_open_time_exclusive=requested_end,
                        ingestion_version=version,
                    )

                self.assertEqual(fetcher.calls, [])
                self.assertEqual(writer.calls, [])

    def test_all_invalid_extension_shapes_fail_closed_without_mutating_base(self) -> None:
        wrong_interval = candle(4)
        object.__setattr__(wrong_interval, "interval", "5m")
        cases = (
            ("empty", ()),
            ("missing first", (candle(4), candle(5))),
            ("boundary gap", (candle(4), candle(5))),
            ("missing internal", (candle(3), candle(5))),
            ("missing final", (candle(3), candle(4))),
            ("duplicate extension", (candle(3), candle(4), candle(4), candle(5))),
            (
                "out of order",
                (candle(3), candle(4), candle(3), candle(5)),
            ),
            (
                "boundary duplicate",
                (candle(2), candle(3), candle(4), candle(5)),
            ),
            (
                "timestamp before requested start",
                (candle(1), candle(3), candle(4), candle(5)),
            ),
            (
                "symbol mismatch",
                (candle(3), candle(4, symbol="ETHUSDT"), candle(5)),
            ),
            ("interval mismatch", (candle(3), wrong_interval, candle(5))),
        )
        for description, candidates in cases:
            with self.subTest(description=description):
                existing = sequence(count=3)
                original = deepcopy(existing)
                fetcher = FakeHistoricalRangeFetcher(candidates)
                writer = FakeCanonicalWriter(self.root / "unused.parquet")

                with self.assertRaises(ValueError):
                    self.extend(existing, fetcher, writer)

                self.assertEqual(len(fetcher.calls), 1)
                self.assertEqual(writer.calls, [])
                self.assertEqual(existing, original)

    def test_incremental_provider_and_writer_failures_propagate_and_preserve_base(self) -> None:
        existing = sequence(count=3)
        original = deepcopy(existing)
        provider_error = RuntimeError("provider failed")
        fetcher = FakeHistoricalRangeFetcher(provider_error)
        writer = FakeCanonicalWriter(self.root / "unused.parquet")

        with self.assertRaises(RuntimeError) as captured:
            self.extend(existing, fetcher, writer)
        self.assertIs(captured.exception, provider_error)
        self.assertEqual(writer.calls, [])
        self.assertEqual(existing, original)

        writer_error = OSError("writer failed")
        fetcher = FakeHistoricalRangeFetcher(
            tuple(candle(index) for index in range(3, 6))
        )
        writer = FakeCanonicalWriter(self.root / "unused.parquet", writer_error)
        with self.assertRaises(OSError) as captured:
            self.extend(existing, fetcher, writer)
        self.assertIs(captured.exception, writer_error)
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(existing, original)

    def test_corrupted_or_forged_base_is_rejected_before_fetch_and_write(self) -> None:
        def forge_gap(value: ValidatedCandleSequence) -> None:
            object.__setattr__(
                value, "candles", (value.candles[0], value.candles[2])
            )

        def forge_mutable_container(value: ValidatedCandleSequence) -> None:
            object.__setattr__(value, "candles", list(value.candles))

        def forge_identity_boundary(value: ValidatedCandleSequence) -> None:
            object.__setattr__(
                value.identity,
                "end_time",
                value.identity.end_time + timedelta(minutes=1),
            )

        def forge_identity_source(value: ValidatedCandleSequence) -> None:
            object.__setattr__(value.identity, "source", "")

        def forge_identity_status(value: ValidatedCandleSequence) -> None:
            object.__setattr__(
                value.identity,
                "validation_status",
                DatasetValidationStatus.UNVALIDATED,
            )

        def forge_negative_volume(value: ValidatedCandleSequence) -> None:
            object.__setattr__(value.candles[1], "volume", Decimal("-1"))

        def forge_ohlc(value: ValidatedCandleSequence) -> None:
            object.__setattr__(value.candles[1], "high", Decimal("1"))

        def forge_trade_count(value: ValidatedCandleSequence) -> None:
            object.__setattr__(value.candles[1], "trade_count", True)

        for description, forge in (
            ("sequence gap", forge_gap),
            ("mutable candle container", forge_mutable_container),
            ("identity boundary", forge_identity_boundary),
            ("identity source", forge_identity_source),
            ("identity status", forge_identity_status),
            ("negative scalar", forge_negative_volume),
            ("OHLC scalar", forge_ohlc),
            ("trade-count scalar type", forge_trade_count),
        ):
            with self.subTest(description=description):
                existing = sequence(count=3)
                forge(existing)
                fetcher = FakeHistoricalRangeFetcher(
                    tuple(candle(index) for index in range(3, 6))
                )
                writer = FakeCanonicalWriter(self.root / "unused.parquet")

                with self.assertRaises((TypeError, ValueError)):
                    self.extend(existing, fetcher, writer)

                self.assertEqual(fetcher.calls, [])
                self.assertEqual(writer.calls, [])

        fetcher = FakeHistoricalRangeFetcher(
            tuple(candle(index) for index in range(3, 6))
        )
        writer = FakeCanonicalWriter(self.root / "unused.parquet")
        with self.assertRaisesRegex(HistoricalIngestionError, "ValidatedCandleSequence"):
            self.extend(sequence().candles, fetcher, writer)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(writer.calls, [])

    def test_off_minute_base_is_rejected_before_extension_fetch(self) -> None:
        shifted_start = START + timedelta(seconds=1)
        candles = tuple(
            Candle(
                symbol="BTCUSDT",
                interval="1m",
                open_time=shifted_start + timedelta(minutes=index),
                close_time=shifted_start
                + timedelta(minutes=index, seconds=59, milliseconds=999),
                open=Decimal("100"),
                high=Decimal("103"),
                low=Decimal("99"),
                close=Decimal("102"),
                volume=Decimal("1"),
                quote_volume=Decimal("102"),
                trade_count=1,
            )
            for index in range(3)
        )
        existing = validate_candle_sequence(
            DatasetIdentity(
                symbol="BTCUSDT",
                timeframe="1m",
                start_time=candles[0].open_time,
                end_time=candles[-1].open_time,
                source="binance-spot",
                schema_version="candle-v1",
                ingestion_version="base-v1",
            ),
            candles,
        )
        fetcher = FakeHistoricalRangeFetcher(())
        writer = FakeCanonicalWriter(self.root / "unused.parquet")

        with self.assertRaisesRegex(ValueError, "aligned"):
            self.extend(
                existing,
                fetcher,
                writer,
                end_open_time_exclusive=START + timedelta(minutes=6, seconds=1),
            )

        self.assertEqual(fetcher.calls, [])
        self.assertEqual(writer.calls, [])


if __name__ == "__main__":
    unittest.main()
