from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from quantos.paper.research.data import Bar, OpenInterestMetric
from quantos.paper.research.dataset import DatasetIndex
from quantos.paper.research.signals import (
    TRAINING_END_MS, TRAINING_START_MS, SignalRecord, _quarter_cutoffs,
    _candles, _oi_values, build_signal_cache, load_signal_cache, save_signal_cache,
)
from quantos.paper.strategies.candidate_features import ALT_SOURCE_UNIVERSE, HOUR_MS, TimedValue
from quantos.paper.strategies.crash_flush_e2 import CrashFlushE2Strategy
from quantos.paper.types import OpenInterestValue
from quantos.paper.market import parse_exchange_info
from quantos.paper.strategies.indicators import make_signal
from quantos.paper.types import Instrument, Quote, Snapshot


def _index(bars, *, window=("2021-10-01", "2024-12-31")):
    return DatasetIndex(
        bars=bars, marks={}, funding={}, metrics={}, audit=(),
        bar_by_open_ms={s: {b.open_ms: b for b in rows} for s, rows in bars.items()},
        mark_by_open_ms={}, window=window, content_hash="fixture", unavailable_partitions=(),
    )


class PortfolioSignalCacheTests(unittest.TestCase):
    def test_cache_is_training_only_and_emits_no_future_decisions(self):
        # Deliberately include a future row in the input; its timestamp must not
        # create a training signal or extend the approved cache window.
        rows = tuple(Bar("BTCUSDT", "1h", t, t + HOUR_MS - 1,
                         D(100), D(101), D(99), D(100), D(1))
                     for t in (TRAINING_START_MS, TRAINING_END_MS))
        cache = build_signal_cache(_index({"BTCUSDT": rows}), include_alt=False,
                                   include_crash=False, include_ema=False)
        self.assertEqual(cache.training_window, (TRAINING_START_MS, TRAINING_END_MS))
        self.assertEqual(cache.data_hash, "fixture")
        self.assertEqual(len(cache.source_hash), 64)
        self.assertEqual(len(cache.filter_hash), 64)
        self.assertTrue(all(TRAINING_START_MS <= row.decision_ms < TRAINING_END_MS
                            for row in cache))

    def test_nontraining_index_is_rejected(self):
        with self.assertRaises(ValueError):
            build_signal_cache(_index({}, window=("2025-01-01", "2025-12-31")))

    def test_quarter_calibration_fails_closed_on_one_missing_sample(self):
        quarter = int(datetime(2022, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
        start, end = quarter - 90 * 24 * HOUR_MS, quarter - 3 * 24 * HOUR_MS
        rows = tuple(TimedValue(t, D("-0.01"))
                     for t in range(start, end, HOUR_MS))
        cutoffs = _quarter_cutoffs({symbol: () for symbol in ALT_SOURCE_UNIVERSE},
                                   quarter, (D("0.005"),))
        self.assertIsNone(cutoffs[(quarter, D("0.005"))])
        # The direct calibration primitive is strict for every requested sample.
        from quantos.paper.strategies.candidate_features import calibrate_quarterly_return_cutoff
        complete = {symbol: rows for symbol in ALT_SOURCE_UNIVERSE}
        self.assertIsNotNone(calibrate_quarterly_return_cutoff(complete, quarter, D("0.005")))
        incomplete = dict(complete)
        incomplete[ALT_SOURCE_UNIVERSE[-1]] = rows[:-1]
        self.assertIsNone(calibrate_quarterly_return_cutoff(incomplete, quarter, D("0.005")))

    def test_metric_label_and_conservative_availability_are_distinct(self):
        off_hour = OpenInterestMetric("BTCUSDT", HOUR_MS - 5 * 60_000, D(1), D(999), ())
        row = OpenInterestMetric("BTCUSDT", HOUR_MS, D(1), D(10), ())
        oi_rows = _oi_values((off_hour, row))
        self.assertEqual(len(oi_rows), 1)  # No nearest 5m sample or aggregation.
        oi = oi_rows[0]
        self.assertEqual(oi.time_ms, HOUR_MS)
        self.assertEqual(oi.available_ms, 2 * HOUR_MS)

    def test_cache_signal_matches_current_crash_strategy_at_causal_boundary(self):
        symbol = "BTCUSDT"
        start = TRAINING_START_MS
        prices = [D(10000)] * 745 + [D(10000), D(9900), D(9800), D(9600), D(9000)]
        bars = []
        prior = prices[0]
        for i, close in enumerate(prices):
            opened = start + i * HOUR_MS
            bars.append(Bar(symbol, "1h", opened, opened + HOUR_MS - 1,
                            prior, max(prior, close, D(10100)), min(prior, close, D(9900)),
                            close, D(1)))
            prior = close
        metrics = []
        for i in range(len(bars) + 1):
            label = start + i * HOUR_MS
            value = D(700) if i == len(prices) - 1 else D(1000 + i % 7)
            metrics.append(OpenInterestMetric(symbol, label, D(1), value, ()))
        index = _index({symbol: tuple(bars)})
        index = replace(index, metrics={symbol: tuple(metrics)})
        expected_at = start + len(prices) * HOUR_MS
        from quantos.paper.research.signals import _snapshot
        import json
        filter_data = json.loads(Path("data/portfolio/filters/current_usdt_proxy_20261029.json").read_text())
        instrument = parse_exchange_info(filter_data, "USDT")[symbol]
        eligible = [row for row in metrics if row.conservative_available_ms <= expected_at]
        direct_oi = tuple(OpenInterestValue(row.label_ms, row.conservative_available_ms,
                                            row.sum_open_interest_value)
                          for row in eligible[-725:])
        direct_snapshot = _snapshot(symbol, expected_at, instrument,
                                     tuple(row.to_candle() for row in bars[-25:]), (), direct_oi)
        direct_signal = CrashFlushE2Strategy().evaluate(direct_snapshot)
        self.assertIsNotNone(direct_signal)
        cache = build_signal_cache(index, include_alt=False, include_ema=False,
                                   include_stop_sensitivity=False, symbols=(symbol,))
        matched = [row for row in cache if row.signal.strategy_id == "crash_flush_e2"
                   and dict(row.parameters).get("holding_hours") == "24"]
        self.assertEqual(len(matched), 1)
        record = matched[0]
        self.assertEqual(record.decision_ms, start + len(prices) * HOUR_MS)
        self.assertEqual(dict(record.signal.features)["oi_lag_hours"], D(1))
        self.assertEqual(record.signal.entry_price % instrument.tick, D(0))
        hourly = tuple(row.to_candle() for row in bars[-25:])
        available_rows = [row for row in metrics
                          if row.conservative_available_ms <= record.decision_ms]
        available_oi = tuple(OpenInterestValue(row.label_ms, row.conservative_available_ms,
                                                row.sum_open_interest_value)
                             for row in available_rows[-725:])
        snap = _snapshot(symbol, record.decision_ms, instrument, hourly, (), available_oi)
        self.assertEqual(record.signal, CrashFlushE2Strategy().evaluate(snap))
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "signals.json.gz"
            save_signal_cache(cache, cache_path)
            loaded = load_signal_cache(
                cache_path, expected_data_hash=cache.data_hash,
                expected_filter_hash=cache.filter_hash,
                expected_parameter_hash=cache.parameter_hash,
            )
            self.assertEqual(loaded, cache)
            with self.assertRaises(FileExistsError):
                save_signal_cache(cache, cache_path)
            with self.assertRaisesRegex(ValueError, "data_hash is stale"):
                load_signal_cache(
                    cache_path, expected_data_hash="stale",
                    expected_filter_hash=cache.filter_hash,
                    expected_parameter_hash=cache.parameter_hash,
                )

    def test_timestamp_resampling_rejects_gap_without_rephasing_pairs(self):
        rows = tuple(Bar("BTCUSDT", "1h", i * HOUR_MS, (i + 1) * HOUR_MS - 1,
                         D(100), D(101), D(99), D(100), D(1))
                     for i in (0, 1, 3, 4, 5, 6))
        from quantos.paper.strategies.candidate_features import resample_hourly
        result = resample_hourly(_candles(rows), 2, as_of_ms=rows[-1].close_ms)
        self.assertIsNone(result)

    def test_signal_record_requires_exact_decision_boundary(self):
        instrument = Instrument("BTCUSDT", "USDT", "TRADING", D("0.01"), D("0.001"),
                                D("0.001"), D(100), D(5))
        snap = Snapshot(TRAINING_START_MS, instrument,
                        Quote("BTCUSDT", TRAINING_START_MS, TRAINING_START_MS,
                              D(100), D(100), D(1), D(1), D(100)), (), ())
        signal = make_signal(snap, "test", "1", "id", "LONG", "MARKET",
                             D(100), D(90), None, 3600, TRAINING_START_MS + 1, "fixture")
        self.assertIsNotNone(signal)
        with self.assertRaises(ValueError):
            SignalRecord(TRAINING_START_MS + 1, signal, ())


if __name__ == "__main__":
    unittest.main()
