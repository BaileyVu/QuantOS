"""Offline contracts for typed DuckDB queries over canonical Parquet."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import duckdb
import pyarrow.parquet as pq

from quantos.domain.market_data import Candle, DatasetIdentity, ValidatedCandleSequence, validate_candle_sequence
from quantos.infrastructure.storage import (
    DuckDBCandleDatasetQuery,
    DuckDBQueryError,
    ParquetCandleDatasetStore,
    ParquetStorageError,
)

UTC = timezone.utc
START = datetime(2024, 12, 31, 23, 57, tzinfo=UTC)
ONE_MINUTE = timedelta(minutes=1)
DECIMAL_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")


def candle(open_time: datetime, *, symbol: str = "BTCUSDT", marker: int = 0) -> Candle:
    close_microsecond = 999_000 if open_time.year < 2025 else 999_999
    increment = Decimal(marker) / Decimal("100000000000000000")
    return Candle(
        symbol=symbol,
        interval="1m",
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59, microseconds=close_microsecond),
        open=Decimal("100.123456789012345678") + increment,
        high=Decimal("103.123456789012345678") + increment,
        low=Decimal("99.123456789012345678") + increment,
        close=Decimal("102.123456789012345678") + increment,
        volume=Decimal("0.123456789012345678") + increment,
        quote_volume=Decimal("12.123456789012345678") + increment,
        trade_count=7 + marker,
    )


def sequence(
    *, symbol: str = "BTCUSDT", count: int = 6, start: datetime = START,
    ingestion_version: str = "fixture-2c2-v1",
) -> ValidatedCandleSequence:
    candles = tuple(candle(start + index * ONE_MINUTE, symbol=symbol, marker=index) for index in range(count))
    return validate_candle_sequence(
        DatasetIdentity(
            symbol=symbol,
            timeframe="1m",
            start_time=candles[0].open_time,
            end_time=candles[-1].open_time,
            source="offline-fixture",
            schema_version="candle-v1",
            ingestion_version=ingestion_version,
        ),
        candles,
    )


def epoch_microseconds(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds


def rows_for(candles: tuple[Candle, ...]) -> list[tuple[object, ...]]:
    return [
        (
            item.symbol,
            item.interval,
            epoch_microseconds(item.open_time),
            epoch_microseconds(item.close_time),
            item.open,
            item.high,
            item.low,
            item.close,
            item.volume,
            item.quote_volume,
            item.trade_count,
        )
        for item in candles
    ]


class RecordingConnection:
    def __init__(self, rows: list[tuple[object, ...]], error: duckdb.Error | None = None) -> None:
        self.rows = rows
        self.error = error
        self.sql: str | None = None
        self.parameters: tuple[object, ...] | None = None
        self.closed = False

    def execute(self, sql: str, parameters: tuple[object, ...]) -> RecordingConnection:
        self.sql = sql
        self.parameters = parameters
        if self.error is not None:
            raise self.error
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


class DuckDBCandleDatasetQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ParquetCandleDatasetStore(self.root)
        self.sequence = sequence()
        self.path = self.store.write(self.sequence)
        self.query = DuckDBCandleDatasetQuery(self.store)

    def run_query(
        self, *, start: datetime = START,
        end: datetime = START + 6 * ONE_MINUTE,
    ) -> ValidatedCandleSequence:
        return self.query.query_open_time_range(
            self.path, start_open_time=start, end_open_time_exclusive=end
        )

    def test_full_dataset_query_for_both_symbols_is_exact(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                source = sequence(symbol=symbol)
                path = self.store.write(source)
                result = self.query.query_open_time_range(
                    path,
                    start_open_time=source.identity.start_time,
                    end_open_time_exclusive=source.identity.end_time + ONE_MINUTE,
                )
                self.assertEqual(result, source)
                self.assertIsInstance(result, ValidatedCandleSequence)

    def test_complete_two_day_query_is_exact_across_the_timestamp_era_boundary(self) -> None:
        source = sequence(
            count=2_880,
            start=datetime(2024, 12, 31, tzinfo=UTC),
            ingestion_version="fixture-2c2-two-day",
        )
        path = self.store.write(source)

        result = self.query.query_open_time_range(
            path,
            start_open_time=source.identity.start_time,
            end_open_time_exclusive=source.identity.end_time + ONE_MINUTE,
        )

        self.assertEqual(result, source)
        self.assertEqual(len(result.candles), 2_880)
        self.assertEqual(result.candles[1_439].close_time.microsecond, 999_000)
        self.assertEqual(result.candles[1_440].close_time.microsecond, 999_999)

    def test_single_partial_and_cross_midnight_ranges_have_exact_subset_identity(self) -> None:
        cases = (
            (START, START + ONE_MINUTE),
            (START + ONE_MINUTE, START + 4 * ONE_MINUTE),
            (START + 2 * ONE_MINUTE, START + 5 * ONE_MINUTE),
        )
        for start, end in cases:
            with self.subTest(start=start, end=end):
                result = self.run_query(start=start, end=end)
                start_index = (start - START) // ONE_MINUTE
                end_index = (end - START) // ONE_MINUTE
                self.assertEqual(result.candles, self.sequence.candles[start_index:end_index])
                self.assertEqual(result.identity.start_time, start)
                self.assertEqual(result.identity.end_time, end - ONE_MINUTE)
                self.assertEqual(result.identity.symbol, self.sequence.identity.symbol)
                self.assertEqual(result.identity.timeframe, self.sequence.identity.timeframe)
                self.assertEqual(result.identity.source, self.sequence.identity.source)
                self.assertEqual(result.identity.schema_version, self.sequence.identity.schema_version)
                self.assertEqual(result.identity.ingestion_version, self.sequence.identity.ingestion_version)

    def test_timestamp_microseconds_and_decimal_values_are_preserved_exactly(self) -> None:
        result = self.run_query()
        self.assertEqual(result.candles[2].close_time.microsecond, 999_000)
        self.assertEqual(result.candles[3].close_time.microsecond, 999_999)
        for actual, expected in zip(result.candles, self.sequence.candles, strict=True):
            self.assertIs(type(actual.open_time), datetime)
            self.assertIs(actual.open_time.tzinfo, UTC)
            for name in DECIMAL_FIELDS:
                value = getattr(actual, name)
                self.assertIs(type(value), Decimal)
                self.assertEqual(value.as_tuple(), getattr(expected, name).as_tuple())

    def test_fixed_parameterized_query_uses_one_exact_path_and_integer_bounds(self) -> None:
        canonical = self.store.read(self.path)
        connection = RecordingConnection(rows_for(canonical.candles))
        with patch(
            "quantos.infrastructure.storage.duckdb_query.duckdb.connect",
            return_value=connection,
        ) as connect:
            result = self.run_query()
        self.assertEqual(result, canonical)
        connect.assert_called_once_with(database=":memory:")
        self.assertTrue(connection.closed)
        self.assertIsNotNone(connection.sql)
        self.assertNotIn(str(self.path), connection.sql)
        self.assertNotIn("SELECT *", connection.sql.upper())
        self.assertIn("ORDER BY open_time", connection.sql)
        self.assertIn("hive_partitioning = false", connection.sql)
        self.assertEqual(connection.sql.count("?"), 3)
        self.assertEqual(
            connection.parameters,
            (str(self.path), epoch_microseconds(START), epoch_microseconds(START + 6 * ONE_MINUTE)),
        )

    def test_parameter_binding_handles_an_apostrophe_in_the_exact_local_path(self) -> None:
        quoted_store = ParquetCandleDatasetStore(self.root / "quoted'root")
        quoted_path = quoted_store.write(self.sequence)
        quoted_query = DuckDBCandleDatasetQuery(quoted_store)

        result = quoted_query.query_open_time_range(
            quoted_path,
            start_open_time=START,
            end_open_time_exclusive=START + 6 * ONE_MINUTE,
        )

        self.assertEqual(result, self.sequence)

    def test_invalid_requests_fail_before_canonical_read_or_duckdb(self) -> None:
        class NanosecondDatetime(datetime):
            nanosecond = 1

        cases = (
            (datetime(2024, 12, 31, 23, 57), START + ONE_MINUTE),
            (START.astimezone(timezone(timedelta(hours=7))), START + ONE_MINUTE),
            (START + timedelta(seconds=1), START + ONE_MINUTE),
            (START, START + timedelta(minutes=1, microseconds=1)),
            (NanosecondDatetime(2024, 12, 31, 23, 57, tzinfo=UTC), START + ONE_MINUTE),
            (START, START),
            (START + ONE_MINUTE, START),
        )
        for start, end in cases:
            with self.subTest(start=start, end=end):
                with patch.object(self.store, "read", wraps=self.store.read) as read, patch(
                    "quantos.infrastructure.storage.duckdb_query.duckdb.connect"
                ) as connect:
                    with self.assertRaises(DuckDBQueryError):
                        self.query.query_open_time_range(
                            self.path,
                            start_open_time=start,
                            end_open_time_exclusive=end,
                        )
                read.assert_not_called()
                connect.assert_not_called()

    def test_outside_ranges_are_never_clipped_and_do_not_open_duckdb(self) -> None:
        cases = (
            (START - ONE_MINUTE, START + ONE_MINUTE),
            (START, START + 7 * ONE_MINUTE),
            (START + 6 * ONE_MINUTE, START + 7 * ONE_MINUTE),
        )
        for start, end in cases:
            with self.subTest(start=start, end=end), patch(
                "quantos.infrastructure.storage.duckdb_query.duckdb.connect"
            ) as connect:
                with self.assertRaisesRegex(DuckDBQueryError, "contained"):
                    self.query.query_open_time_range(
                        self.path, start_open_time=start, end_open_time_exclusive=end
                    )
                connect.assert_not_called()

    def test_missing_corrupt_and_invalid_metadata_are_rejected_before_duckdb(self) -> None:
        missing = self.root / "missing.parquet"
        corrupt = self.root / "corrupt.parquet"
        corrupt.write_bytes(b"PAR1 broken PAR1")

        with pq.ParquetFile(self.path) as parquet:
            table = parquet.read()
        metadata = dict(table.schema.metadata or {})
        metadata[b"quantos.storage_schema_version"] = b"parquet-v2"
        invalid_metadata = self.root / "invalid-metadata.parquet"
        pq.write_table(
            table.replace_schema_metadata(metadata),
            invalid_metadata,
            version="2.6",
            write_page_checksum=True,
            store_schema=True,
        )

        for path in (missing, corrupt, invalid_metadata):
            with self.subTest(path=path), patch(
                "quantos.infrastructure.storage.duckdb_query.duckdb.connect"
            ) as connect:
                with self.assertRaises(ParquetStorageError):
                    self.query.query_open_time_range(
                        path,
                        start_open_time=START,
                        end_open_time_exclusive=START + ONE_MINUTE,
                    )
                connect.assert_not_called()

    def test_valid_dataset_copy_at_noncanonical_path_is_rejected_before_duckdb(self) -> None:
        copied = self.root / "copied.parquet"
        copied.write_bytes(self.path.read_bytes())
        with patch("quantos.infrastructure.storage.duckdb_query.duckdb.connect") as connect:
            with self.assertRaisesRegex(DuckDBQueryError, "canonical dataset path"):
                self.query.query_open_time_range(
                    copied,
                    start_open_time=START,
                    end_open_time_exclusive=START + ONE_MINUTE,
                )
            connect.assert_not_called()

    def test_multiple_ingestion_versions_are_isolated_by_exact_selected_path(self) -> None:
        changed_candles = tuple(
            replace(item, volume=item.volume + Decimal("1.000000000000000000"))
            for item in self.sequence.candles
        )
        other = validate_candle_sequence(
            DatasetIdentity(
                symbol="BTCUSDT",
                timeframe="1m",
                start_time=changed_candles[0].open_time,
                end_time=changed_candles[-1].open_time,
                source="offline-fixture",
                schema_version="candle-v1",
                ingestion_version="fixture-2c2-v2",
            ),
            changed_candles,
        )
        other_path = self.store.write(other)
        first = self.run_query()
        second = self.query.query_open_time_range(
            other_path,
            start_open_time=START,
            end_open_time_exclusive=START + 6 * ONE_MINUTE,
        )
        self.assertEqual(first, self.sequence)
        self.assertEqual(second, other)
        self.assertNotEqual(first.candles, second.candles)
        self.assertNotEqual(self.path, other_path)

    def test_glob_capable_paths_fail_closed_before_file_or_duckdb_access(self) -> None:
        bracket_root = self.root / "[ambiguous]"
        bracket_store = ParquetCandleDatasetStore(bracket_root)
        bracket_path = bracket_store.write(self.sequence)
        query = DuckDBCandleDatasetQuery(bracket_store)
        with patch.object(bracket_store, "read", wraps=bracket_store.read) as read, patch(
            "quantos.infrastructure.storage.duckdb_query.duckdb.connect"
        ) as connect:
            with self.assertRaisesRegex(DuckDBQueryError, "glob"):
                query.query_open_time_range(
                    bracket_path,
                    start_open_time=START,
                    end_open_time_exclusive=START + ONE_MINUTE,
                )
        read.assert_not_called()
        connect.assert_not_called()

    def test_unexpected_count_order_content_and_conversion_types_are_rejected(self) -> None:
        canonical = self.store.read(self.path)
        valid_rows = rows_for(canonical.candles)
        variants = {
            "missing": valid_rows[:-1],
            "extra": valid_rows + [valid_rows[-1]],
            "reversed": list(reversed(valid_rows)),
            "wrong symbol": [("ETHUSDT", *valid_rows[0][1:]), *valid_rows[1:]],
            "float decimal": [(*valid_rows[0][:4], float(valid_rows[0][4]), *valid_rows[0][5:]), *valid_rows[1:]],
            "datetime timestamp": [(valid_rows[0][0], valid_rows[0][1], START, *valid_rows[0][3:]), *valid_rows[1:]],
            "changed value": [(*valid_rows[0][:8], valid_rows[0][8] + Decimal("1"), *valid_rows[0][9:]), *valid_rows[1:]],
        }
        for description, rows in variants.items():
            with self.subTest(description=description):
                connection = RecordingConnection(rows)
                with patch(
                    "quantos.infrastructure.storage.duckdb_query.duckdb.connect",
                    return_value=connection,
                ):
                    with self.assertRaises(DuckDBQueryError):
                        self.run_query()
                self.assertTrue(connection.closed)

    def test_duckdb_error_is_wrapped_and_connection_is_closed(self) -> None:
        connection = RecordingConnection([], duckdb.IOException("injected query failure"))
        with patch(
            "quantos.infrastructure.storage.duckdb_query.duckdb.connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(DuckDBQueryError, "injected query failure"):
                self.run_query()
        self.assertTrue(connection.closed)

    def test_revalidation_detects_dataset_change_during_query(self) -> None:
        canonical = self.store.read(self.path)
        changed_candles = (
            replace(canonical.candles[0], volume=canonical.candles[0].volume + Decimal("1")),
            *canonical.candles[1:],
        )
        changed = ValidatedCandleSequence(canonical.identity, changed_candles)
        connection = RecordingConnection(rows_for(canonical.candles))
        with patch.object(self.store, "read", side_effect=(canonical, changed)), patch(
            "quantos.infrastructure.storage.duckdb_query.duckdb.connect",
            return_value=connection,
        ):
            with self.assertRaisesRegex(DuckDBQueryError, "changed"):
                self.run_query()

    def test_actual_query_is_read_only_and_creates_no_persistent_database(self) -> None:
        before_bytes = self.path.read_bytes()
        before_hash = hashlib.sha256(before_bytes).hexdigest()
        before_stat = self.path.stat()
        before_files = set(path.relative_to(self.root) for path in self.root.rglob("*") if path.is_file())

        self.assertEqual(self.run_query(), self.sequence)

        after_bytes = self.path.read_bytes()
        after_stat = self.path.stat()
        after_files = set(path.relative_to(self.root) for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(hashlib.sha256(after_bytes).hexdigest(), before_hash)
        self.assertEqual(after_bytes, before_bytes)
        self.assertEqual(after_stat.st_size, before_stat.st_size)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(after_files, before_files)
        self.assertEqual(list(self.root.rglob("*.duckdb*")), [])

    def test_query_does_not_use_global_duckdb_sql_state(self) -> None:
        with patch(
            "quantos.infrastructure.storage.duckdb_query.duckdb.sql",
            side_effect=AssertionError("global state must not be used"),
        ) as global_sql:
            self.assertEqual(self.run_query(), self.sequence)
        global_sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
