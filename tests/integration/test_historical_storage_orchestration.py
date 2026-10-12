"""Offline Application orchestration through immutable canonical Parquet storage."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.application import (
    extend_and_persist_historical_range,
    ingest_and_persist_historical_range,
)
from quantos.infrastructure.storage import ParquetCandleDatasetStore
from tests.unit.test_historical_persistence import (
    START,
    FakeHistoricalRangeFetcher,
    candle,
)


class HistoricalStorageOrchestrationTests(unittest.TestCase):
    def test_initial_and_repeated_extension_preserve_both_immutable_versions(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol), TemporaryDirectory() as temporary:
                store = ParquetCandleDatasetStore(Path(temporary))
                prefix = tuple(candle(index, symbol=symbol) for index in range(3))
                initial_fetcher = FakeHistoricalRangeFetcher(prefix)
                initial = ingest_and_persist_historical_range(
                    initial_fetcher,
                    store,
                    symbol=symbol,
                    interval="1m",
                    start_open_time=START,
                    end_open_time_exclusive=START + timedelta(minutes=3),
                    source="binance-spot",
                    schema_version="candle-v1",
                    ingestion_version="initial-v1",
                )
                base = store.read(initial.path)
                self.assertEqual(base.candles, prefix)
                self.assertEqual(initial.path, store.dataset_path(base.identity))
                base_bytes = initial.path.read_bytes()
                base_stat = initial.path.stat()

                suffix = tuple(candle(index, symbol=symbol) for index in range(3, 6))
                first_fetcher = FakeHistoricalRangeFetcher(suffix)
                first_extension = extend_and_persist_historical_range(
                    base,
                    first_fetcher,
                    store,
                    end_open_time_exclusive=START + timedelta(minutes=6),
                    ingestion_version="extended-v2",
                )
                extended_bytes = first_extension.path.read_bytes()
                extended_stat = first_extension.path.stat()

                repeated_fetcher = FakeHistoricalRangeFetcher(suffix)
                repeated_extension = extend_and_persist_historical_range(
                    base,
                    repeated_fetcher,
                    store,
                    end_open_time_exclusive=START + timedelta(minutes=6),
                    ingestion_version="extended-v2",
                )

                expected_fetch = {
                    "symbol": symbol,
                    "interval": "1m",
                    "start_open_time": START + timedelta(minutes=3),
                    "end_open_time_exclusive": START + timedelta(minutes=6),
                }
                self.assertEqual(first_fetcher.calls, [expected_fetch])
                self.assertEqual(repeated_fetcher.calls, [expected_fetch])
                self.assertEqual(repeated_extension, first_extension)
                self.assertNotEqual(first_extension.path, initial.path)
                self.assertEqual(store.read(initial.path), base)
                self.assertEqual(store.read(first_extension.path).candles, prefix + suffix)
                self.assertEqual(initial.path.read_bytes(), base_bytes)
                self.assertEqual(initial.path.stat().st_size, base_stat.st_size)
                self.assertEqual(initial.path.stat().st_mtime_ns, base_stat.st_mtime_ns)
                self.assertEqual(first_extension.path.read_bytes(), extended_bytes)
                self.assertEqual(first_extension.path.stat().st_size, extended_stat.st_size)
                self.assertEqual(
                    first_extension.path.stat().st_mtime_ns,
                    extended_stat.st_mtime_ns,
                )
                self.assertEqual(len(list(Path(temporary).rglob("*.parquet"))), 2)
                self.assertEqual(list(Path(temporary).rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
