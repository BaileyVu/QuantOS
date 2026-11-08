import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from quantos.paper.research.timesfm_experiment import (
    BUILD_START_MS, BUILD_END_MS, HELD_END_MS, HOUR_MS, Forecast,
    align_volatility_forecasts, assert_period, atr_percent, ewma_volatility, features_at,
    mean_absolute_error, persistence_volatility, register_variants, walk_forward_features,
    timesfm25_predictor,
)
from quantos.paper.research.registry import ResearchRegistry


def forecast(ts, price, *, median=None):
    median = median or {1: price * 1.01, 4: price * 1.04, 12: price * 1.12, 24: price * 1.24}
    return Forecast(ts, price, median,
                    {24: price * 0.9}, {24: price * 1.3})


class TimesfmExperimentTest(unittest.TestCase):
    def test_features_are_drift_width_and_prior_forecast_surprise(self):
        t = BUILD_START_MS
        current = forecast(t + HOUR_MS, 105)
        prior = forecast(t, 100, median={1: 102, 4: 104, 12: 112, 24: 124})
        result = features_at(current, completed_close_ms=t + HOUR_MS,
                             completed_close=105, prior_forecast=prior)
        self.assertAlmostEqual(result["drift_4h"], .04)
        self.assertAlmostEqual(result["drift_12h"], .12)
        self.assertAlmostEqual(result["drift_24h"], .24)
        self.assertAlmostEqual(result["width_24h"], .4)
        self.assertAlmostEqual(result["surprise"], 3 / 105)

    def test_missing_prior_forecast_is_explicit(self):
        t = BUILD_START_MS
        result = features_at(forecast(t, 100), completed_close_ms=t,
                             completed_close=100, prior_forecast=None)
        self.assertIsNone(result["surprise"])

    def test_rejects_future_or_misaligned_surprise_forecast(self):
        t = BUILD_START_MS + HOUR_MS
        with self.assertRaisesRegex(ValueError, "predate"):
            features_at(forecast(t, 100), completed_close_ms=t,
                        completed_close=100, prior_forecast=forecast(t, 100))
        with self.assertRaisesRegex(ValueError, "does not target"):
            features_at(forecast(t, 100), completed_close_ms=t,
                        completed_close=100, prior_forecast=forecast(
                            t - HOUR_MS, 100, median={4: 104, 12: 112, 24: 124}))

    def test_period_contract_excludes_2025_and_wrong_windows(self):
        assert_period(BUILD_START_MS, "BUILD")
        assert_period(BUILD_END_MS, "HELD")
        with self.assertRaises(ValueError):
            assert_period(BUILD_END_MS, "BUILD")
        with self.assertRaises(ValueError):
            assert_period(HELD_END_MS, "HELD")

    def test_walk_forward_predictor_only_receives_prefix(self):
        start = BUILD_START_MS
        bars = [(start + i * HOUR_MS, 100 + i) for i in range(3)]
        seen = []

        def predictor(prefix):
            seen.append(len(prefix))
            ts, price = prefix[-1]
            return forecast(ts, price)

        out = walk_forward_features(bars, predictor, period="BUILD")
        self.assertEqual(seen, [1, 2, 3])
        self.assertEqual(len(out), 3)

    def test_rejects_gap_and_future_feature_origin(self):
        start = BUILD_START_MS
        with self.assertRaisesRegex(ValueError, "contiguous"):
            walk_forward_features([(start, 100), (start + 2 * HOUR_MS, 102)],
                                  lambda _: None, period="BUILD")
        with self.assertRaisesRegex(ValueError, "wrong origin"):
            walk_forward_features([(start, 100)], lambda _: forecast(start + HOUR_MS, 100), period="BUILD")

    def test_official_model_adapter_maps_forecast_horizons_and_quantiles(self):
        import numpy as np

        class FakeTimesFM:
            class Wrapped:
                class Device:
                    type = "cpu"
                device = Device()
            model = Wrapped()

            def forecast(self, *, horizon, inputs):
                self.horizon = horizon
                self.inputs = inputs
                quantiles = np.zeros((1, horizon, 10))
                for h in range(horizon):
                    quantiles[0, h, 1] = h + 10
                    quantiles[0, h, 5] = h + 50
                    quantiles[0, h, 9] = h + 90
                return np.ones((1, horizon)), quantiles

        model = FakeTimesFM()
        ts = BUILD_START_MS
        adapted = timesfm25_predictor(model)([(ts, 100)])
        self.assertEqual(model.horizon, 24)
        self.assertEqual(model.inputs[0].tolist(), [100.0])
        self.assertEqual(adapted.median, {1: 50, 4: 53, 12: 61, 24: 73})
        self.assertEqual(adapted.q10, {24: 33})
        self.assertEqual(adapted.q90, {24: 113})

    def test_causal_baselines(self):
        persisted = persistence_volatility([.1, .2, .3], horizon=1)
        self.assertEqual([(x.origin_index, x.target_start_index, x.target_end_exclusive)
                          for x in persisted], [(0, 1, 2), (1, 2, 3)])
        self.assertEqual([x.volatility for x in persisted], [.1, .2])
        self.assertEqual(persistence_volatility([0, .2])[0].volatility, 0)
        ewma = ewma_volatility([.1, -.2, .3, .4], alpha=.5, horizon=1)
        self.assertEqual([(x.origin_index, x.target_start_index, x.target_end_exclusive)
                          for x in ewma], [(0, 1, 2), (1, 2, 3), (2, 3, 4)])
        self.assertAlmostEqual(ewma[0].volatility, .1)
        self.assertAlmostEqual(ewma[1].volatility, (.025) ** .5)
        self.assertAlmostEqual(ewma[2].volatility, (.0575) ** .5)
        multi = ewma_volatility([.1, -.2, .3, .4], alpha=.5, horizon=2)
        self.assertEqual([(x.target_start_index, x.target_end_exclusive)
                          for x in multi], [(1, 3), (2, 4)])
        self.assertAlmostEqual(multi[0].volatility, .1)
        atr = atr_percent([(11, 9, 10), (12, 10, 11)], window=1)
        self.assertAlmostEqual(atr[0], .2)
        self.assertAlmostEqual(atr[1], 2 / 11)
        atr_forecasts = align_volatility_forecasts(atr, horizon=1, first_index=0)
        self.assertEqual([(x.target_start_index, x.target_end_exclusive)
                          for x in atr_forecasts], [(1, 2)])
        self.assertEqual(align_volatility_forecasts([0, 1])[0].volatility, 0)
        with self.assertRaisesRegex(ValueError, "low <= close <= high"):
            atr_percent([(11, 9, 12)], window=1)
        self.assertAlmostEqual(mean_absolute_error([1, 2], [1.5, 3]), .75)

    def test_all_experiment_variants_register_against_manifest(self):
        with TemporaryDirectory() as tmp:
            manifest_hash = "a" * 64
            with ResearchRegistry(Path(tmp) / "registry.sqlite") as registry:
                keys = register_variants(registry, manifest_hash=manifest_hash,
                                         config={"model": "google/timesfm-2.5-200m-pytorch", "fold": 3})
                self.assertEqual(set(keys), {
                    "timesfm_ml1h", "timesfm_width_volatility", "timesfm_crash_dip_shadow"
                })
                self.assertEqual(registry.trial_counts("timesfm25")["family_n"], 3)
                self.assertEqual(registry.result_rows(family="timesfm25"), [])
                stored = [row[0] for row in registry.db.execute(
                    "SELECT params_json FROM variants WHERE family='timesfm25'")]
                self.assertTrue(all('"model":"google/timesfm-2.5-200m-pytorch"' in row
                                    for row in stored))
                with self.assertRaisesRegex(ValueError, "pinned"):
                    register_variants(registry, manifest_hash=manifest_hash,
                                      config={"model": "wrong-model"})


if __name__ == "__main__":
    unittest.main()
