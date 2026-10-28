import unittest

from quantos.paper.research.metrics import DAY_MS, summarize, kelly_fraction, drawdown_monte_carlo


class MetricsTests(unittest.TestCase):
    def test_mtm_drawdown_and_calendar_windows(self):
        points = [(i*DAY_MS, 200.0+i) for i in range(32)]
        points.insert(3, (2*DAY_MS+1000, 100.0))
        stats = summarize(points, [{"net_pnl": -5, "fees": 1, "exit_reason": "CATASTROPHE_STOP"},
                                   {"net_pnl": 36, "fees": 1, "exit_reason": "TIME"}])
        self.assertAlmostEqual(stats["max_drawdown"], 1-100/202)
        self.assertAlmostEqual(stats["net_return"], 31/200)
        self.assertEqual(stats["positive_3day_window_fraction"], 1)
        self.assertEqual(stats["catastrophe_stop_net_pnl"], -5)
        self.assertEqual(stats["profit_factor"], 7.2)
        with self.assertRaises(ValueError): summarize(points[:2] + points[3:], [])

    def test_losing_or_empty_results_are_not_success_claims(self):
        stats = summarize([(0, 200), (DAY_MS, 100)], [])
        self.assertIsNone(stats["top5_days_share_of_net_pnl"])
        self.assertIsNone(stats["profit_factor"])
        self.assertIsNone(stats["positive_3day_window_fraction"])
        self.assertEqual(kelly_fraction([-1, 1])["half_fraction"], 0)
        self.assertIsNone(kelly_fraction([1, 2])["half_fraction"])

    def test_kelly_known_binary_bet_and_deterministic_resampling(self):
        result = kelly_fraction([1, 1, 1, -1])
        self.assertAlmostEqual(result["fraction"], .5)
        self.assertAlmostEqual(result["half_fraction"], .25)
        mc = drawdown_monte_carlo([-.6, .1], [-.6, .1], draws=1000)
        self.assertEqual(mc["trade_reshuffle_p_dd50"], 1)
        self.assertEqual(mc, drawdown_monte_carlo([-.6, .1], [-.6, .1], draws=1000))


if __name__ == "__main__": unittest.main()
