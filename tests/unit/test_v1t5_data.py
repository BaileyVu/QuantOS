"""Causal UTC aggregation and gap isolation for the V1-T5 research inputs."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

import numpy as np

from quantos.domain.market_data import Candle, DatasetIdentity, validate_candle_sequence
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore
from quantos.infrastructure.storage.v1t5_data import (
    CanonicalSegment, aggregate_segment, load_canonical_segment,
)


class V1T5DataTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.identity = DatasetIdentity("BTCUSDT", "1m", self.start,
            self.start + timedelta(minutes=120), "fixture", "candle-v1", "test")
        rows = np.ones((121, 8))
        rows[:, 0] = self.start.timestamp() + np.arange(121) * 60
        rows[:, 1] = 10
        rows[:, 2] = 12
        rows[:, 3] = 8
        rows[:, 4] = 11
        self.segment = CanonicalSegment("test", "hash", self.identity, rows)

    def test_bar_boundaries_and_next_minute_are_causal(self):
        result = aggregate_segment(self.segment, 30,
            end_exclusive=self.start + timedelta(minutes=121))
        self.assertEqual(result.bars.shape, (4, 8))
        np.testing.assert_array_equal(result.bars[:, 0] + 1800, result.next_minutes[:, 0])
        np.testing.assert_array_equal(result.bars[0, 1:], [10, 12, 8, 11, 30, 30, 30])

    def test_unfinished_next_minute_is_excluded(self):
        result = aggregate_segment(self.segment, 60,
            end_exclusive=self.start + timedelta(minutes=120))
        self.assertEqual(len(result.bars), 1)

    def test_missing_minute_must_not_cross_bar_or_features(self):
        gap = CanonicalSegment("test", "hash", self.identity, np.delete(self.segment.minutes, 70, axis=0))
        with self.assertRaisesRegex(ValueError, "gap"):
            aggregate_segment(gap, 30, end_exclusive=self.start + timedelta(days=1))

    def test_partial_initial_bin_is_discarded(self):
        partial = CanonicalSegment("test", "hash", self.identity, self.segment.minutes[5:])
        result = aggregate_segment(partial, 30, end_exclusive=self.start + timedelta(days=1))
        self.assertEqual(result.bars[0, 0], self.start.timestamp() + 1800)
        self.assertEqual(len(result.bars), 3)

    def test_exact_canonical_read(self):
        candles = tuple(Candle("BTCUSDT", "1m", self.start + timedelta(minutes=i),
            self.start + timedelta(minutes=i + 1, milliseconds=-1),
            *(Decimal(x) for x in (10, 12, 8, 11, 1, 1)), 1) for i in range(121))
        with tempfile.TemporaryDirectory() as directory:
            path = ParquetCandleDatasetStore(Path(directory)).write(
                validate_candle_sequence(self.identity, candles))
            loaded = load_canonical_segment(path)
        np.testing.assert_array_equal(loaded.minutes, self.segment.minutes)
        self.assertEqual(len(loaded.file_sha256), 64)
        self.assertFalse(loaded.minutes.flags.writeable)


if __name__ == "__main__":
    unittest.main()
