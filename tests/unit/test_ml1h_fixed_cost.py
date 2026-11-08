import unittest
import numpy as np

from quantos.paper.research.ml1h_fixed_cost import labels, net_returns, select_positions, HOUR_MS
from quantos.paper.research.ml1h_gates import block_means, decide


class FixedCostTests(unittest.TestCase):
    def bars(self):
        return np.arange(60)*HOUR_MS, np.full(60, 101.), np.full(60, 99.), np.full(60, 100.), np.full(60, 2.)

    def test_stop_wins_collision_and_all_stresses(self):
        t, h, l, c, a = self.bars()
        h[2], l[2] = 104, 96
        y, gross, taker, end = labels(t, h, l, c, a, 1)
        self.assertEqual(y[0], 0)
        self.assertAlmostEqual(gross[0], -.03)
        self.assertEqual(end[0], 48*HOUR_MS)
        for multiple in (1, 2, 3):
            self.assertAlmostEqual(net_returns(gross, taker, multiple)[0], -.03-.0014*multiple)

    def test_entry_bar_target_not_filled_and_boundary_purged(self):
        t, h, l, c, a = self.bars()
        h[1] = 104
        y, gross, taker, end = labels(t, h, l, c, a, 1)
        self.assertEqual(y[0], 0)
        self.assertTrue(taker[0])
        self.assertTrue(np.isnan(gross[-48:]).all())
        h[2] = 104
        y, gross, taker, _ = labels(t, h, l, c, a, 1)
        self.assertEqual(y[0], 1)
        self.assertFalse(taker[0])
        self.assertAlmostEqual(net_returns(gross, taker, 3)[0], .03)

    def test_unfilled_and_gap_fail(self):
        t, h, l, c, a = self.bars()
        l[1] = 100
        self.assertTrue(np.isnan(labels(t, h, l, c, a, 1)[1][0]))
        t[20] += HOUR_MS
        with self.assertRaises(ValueError): labels(t, h, l, c, a, 1)

    def test_cross_side_lock_is_shared_and_tie_deterministic(self):
        chosen = select_positions([0, 0, HOUR_MS, 48*HOUR_MS], ['BTC']*4,
                                  [-1, 1, -1, -1], [.9]*4, [.8]*4)
        self.assertEqual(chosen.tolist(), [1, 3])

    def test_no_hindsight_side_switch_after_unfilled_order(self):
        chosen = select_positions([0, 0, HOUR_MS], ['BTC']*3, [1, -1, 1],
                                  [.95, .9, .9], [.8]*3, filled=[False, True, True])
        self.assertEqual(chosen.tolist(), [2])

    def test_delay_keeps_original_limit_and_49h_boundary(self):
        t, h, l, c, a = self.bars()
        l[1] = 100
        normal = labels(t, h, l, c, a, 1)
        delayed = labels(t, h, l, c, a, 1, delay=1)
        self.assertTrue(np.isnan(normal[1][0]))
        self.assertTrue(np.isfinite(delayed[1][0]))
        self.assertEqual(delayed[3][0], 49*HOUR_MS)

    def test_paired_bootstrap_identical_arms_and_no_false_promotion(self):
        t = np.arange(30)*86_400_000
        a = block_means(t, np.linspace(-.01, .03, 30), start_ms=0, end_ms=30*86_400_000)
        b = block_means(t, np.linspace(-.01, .03, 30), start_ms=0, end_ms=30*86_400_000)
        self.assertTrue(np.array_equal(a, b))
        base = {str(x): {'pass': True, 'mean': .01} for x in (1, 2)}
        self.assertEqual(decide(base, base, {'1': [0, 0], '2': [0, 0]},
                                delay_mean_1x=.01, delay_mean_2x=.01)['decision'], 'REJECTED')


if __name__ == '__main__': unittest.main()
