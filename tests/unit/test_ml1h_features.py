import math
import unittest

from quantos.paper.research.ml1h_features import (
    FEATURE_NAMES, SOURCE_RUN_SHA256, WARMUP_BARS, extract_features,
)


def sample_bars(count=210):
    times, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    start = 1_609_459_200_000  # 2021-01-01T00:00:00Z
    for i in range(count):
        opened = 100.0 + i * 0.2
        times.append(start + i * 3_600_000)
        opens.append(opened)
        highs.append(opened + 2.0)
        lows.append(opened - 1.0)
        closes.append(opened + 1.0)
        volumes.append(10.0 + i % 3)
    return times, opens, highs, lows, closes, volumes


class Ml1hFeatureTests(unittest.TestCase):
    def test_layout_and_warmup_match_reference_metadata(self):
        self.assertEqual(len(FEATURE_NAMES), 32)
        self.assertEqual(FEATURE_NAMES[:5], ("b0", "uw0", "lw0", "rg0", "cl0"))
        self.assertEqual(FEATURE_NAMES[-5:], ("vz", "rz", "hs", "hc", "dow"))
        self.assertEqual(SOURCE_RUN_SHA256,
                         "5a6470bfb28848ea31e21e7a821a7b69db410f63acf332262b2e7245253f6577")
        batch = extract_features(*sample_bars())
        self.assertEqual(len(batch.rows), 210)
        self.assertTrue(all(math.isnan(x) for row in batch.rows[:WARMUP_BARS] for x in row))
        self.assertTrue(all(math.isfinite(x) for x in batch.rows[200][:27]))
        self.assertEqual(batch.atr[12] != batch.atr[12], True)  # first 13 ATR values are undefined
        self.assertAlmostEqual(batch.atr[13], 3.0)

    def test_selected_definitions_match_causal_reference_formulas(self):
        times, opens, highs, lows, closes, volumes = sample_bars()
        row = extract_features(times, opens, highs, lows, closes, volumes).rows[200]
        self.assertAlmostEqual(row[0], 1.0 / 3.0)  # current body / 14-bar mean range
        self.assertAlmostEqual(row[1], 1.0 / 3.0)  # current upper wick / ATR
        self.assertAlmostEqual(row[2], 1.0 / 3.0)  # current lower wick / ATR
        self.assertAlmostEqual(row[3], 1.0)
        self.assertAlmostEqual(row[4], 2.0 / 3.0)
        self.assertEqual(row[18], 5.0)
        self.assertAlmostEqual(row[19], math.log(closes[200]) - math.log(closes[197]))
        day_start = 192
        typical = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(day_start, 201)]
        volume = volumes[day_start:201]
        vwap = sum(p * v for p, v in zip(typical, volume)) / sum(volume)
        self.assertAlmostEqual(row[22], (closes[200] - vwap) / 3.0)
        self.assertAlmostEqual(row[23], (closes[200] - opens[day_start]) / 3.0)

    def test_prefix_invariance_proves_no_future_rows_affect_prior_features(self):
        bars = sample_bars(230)
        full = extract_features(*bars)
        prefix = extract_features(*(column[:217] for column in bars))
        self.assertEqual(full.rows[:217], prefix.rows)
        self.assertEqual(full.atr[:217], prefix.atr)

    def test_rejects_misaligned_or_nonmonotonic_inputs(self):
        times, opens, highs, lows, closes, volumes = sample_bars(4)
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            extract_features(times, opens[:-1], highs, lows, closes, volumes)
        times[2] = times[1]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            extract_features(times, opens, highs, lows, closes, volumes)


if __name__ == "__main__":
    unittest.main()
