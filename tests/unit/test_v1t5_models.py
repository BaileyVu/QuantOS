import unittest

import numpy as np

from quantos.infrastructure.models.v1t5 import (
    FoldTransform, feature_matrix, fit_fold, fractional_difference,
    hourly_targets, purged_indices, triple_barrier_labels,
)


def bars(n=350):
    rng = np.random.default_rng(42)
    c = 100*np.exp(np.cumsum(rng.normal(0, .004, n)))
    o = np.r_[c[0], c[:-1]]
    volume = np.exp(rng.normal(3, 1, n))
    return np.column_stack((np.arange(n)*1800, o, np.maximum(o, c)*1.002,
        np.minimum(o, c)*.998, c, volume, volume*c, np.ceil(volume*5)))


class V1T5ModelTests(unittest.TestCase):
    def test_features_future_perturbation_and_prefix_parity(self):
        b = bars()
        full, names = feature_matrix(b)
        self.assertEqual(len(names), 39)
        prefix, _ = feature_matrix(b[:250])
        np.testing.assert_allclose(full[:250], prefix, equal_nan=True)
        changed = b.copy()
        changed[250:, 1:5] *= 2
        result, _ = feature_matrix(changed)
        np.testing.assert_allclose(full[:250], result[:250], equal_nan=True)

    def test_fractional_difference_known_order_one(self):
        x = np.arange(100.)**2
        result = fractional_difference(x, 1)
        self.assertTrue(np.isnan(result[:63]).all())
        np.testing.assert_allclose(result[63:], np.diff(x)[62:])

    def test_fold_local_scaling_and_selection(self):
        x, names = feature_matrix(bars())
        a = FoldTransform.fit(x[70:200], names)
        x[201:] *= 1000
        b = FoldTransform.fit(x[70:200], names)
        np.testing.assert_array_equal(a.indices, b.indices)
        np.testing.assert_array_equal(a.mean, b.mean)
        self.assertEqual(a.fractional_d, b.fractional_d)
        with self.assertRaises(ValueError):
            a.apply(x[:1])

    def test_label_horizon_and_purge(self):
        b = bars()
        x, _ = feature_matrix(b)
        labels, end = triple_barrier_labels(b, 8, 1)
        mask = np.arange(len(b)) < 200
        ix = purged_indices(mask, end, x, labels)
        self.assertEqual(ix[-1], 191)
        self.assertTrue(np.all(end[ix] < 200))
        changed = b.copy()
        changed[200:, 1:5] *= 2
        other, _ = triple_barrier_labels(changed, 8, 1)
        np.testing.assert_equal(labels[ix], other[ix])

    def test_both_barriers_lower_first(self):
        b = bars()
        b[101, 2] = 10000
        b[101, 3] = .001
        labels, _ = triple_barrier_labels(b, 4, 1)
        self.assertEqual(labels[100], 0)

    def test_hourly_target_boundary(self):
        b = bars()
        y, end = hourly_targets(b)
        self.assertAlmostEqual(y[100], b[101, 4]/b[100, 4]-1)
        self.assertEqual(end[100], 101)
        self.assertTrue(np.isnan(y[-1]))

    def test_gap_rejected(self):
        with self.assertRaises(ValueError):
            feature_matrix(np.delete(bars(), 200, axis=0))

    def test_models_deterministic_and_schema(self):
        b = bars()
        tr = np.arange(len(b)) < 240
        va = np.arange(len(b)) >= 240
        x, names = feature_matrix(b)
        for family in ("A", "B"):
            a = fit_fold(b, tr, va, family)
            second = fit_fold(b, tr, va, family)
            np.testing.assert_array_equal(a.predict(x[300:]), second.predict(x[300:]))
            self.assertEqual(a.config, second.config)
            self.assertLess(a.metadata["last_train_label_index"], 240)
            self.assertTrue(set(a.metadata["feature_names"]).issubset(names))
            if family == "A":
                np.testing.assert_allclose(a.predict(x[300:]).sum(1), 1, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
