from decimal import Decimal as D
import unittest

from quantos.paper.types import Candle
from quantos.paper.strategies.candidate_features import (
    HOUR_MS, TimedValue, confirmed_swing_pivots, oi_value_4h_change_zscore,
    prior_rolling_quantile, resample_hourly, rolling_24h_return,
)


def hourly(count, start=0):
    rows = []
    for i in range(count):
        op = D(100 + i)
        start_ms = (start + i) * HOUR_MS
        rows.append(Candle(start_ms, start_ms + HOUR_MS - 1, op, op + 2, op - 1,
                           op + 1, D(3)))
    return tuple(rows)


class PortfolioFeatureTests(unittest.TestCase):
    def test_resample_uses_utc_epoch_buckets_not_row_groups(self):
        bars = hourly(9, start=1)  # Starts one hour into a 4h bucket.
        two = resample_hourly(bars, 2, as_of_ms=bars[-1].close_ms)
        self.assertEqual([x.open_ms // HOUR_MS for x in two], [2, 4, 6, 8])
        self.assertEqual((two[0].open, two[0].close, two[0].volume), (D(101), D(103), D(6)))
        four = resample_hourly(bars, 4, as_of_ms=bars[-1].close_ms)
        self.assertEqual([x.open_ms // HOUR_MS for x in four], [4])
        self.assertIsNone(resample_hourly(hourly(8)[:3] + hourly(5, start=4), 2, as_of_ms=10 * HOUR_MS))
        self.assertIsNone(resample_hourly(hourly(4), 3, as_of_ms=4 * HOUR_MS))
        # Even a structurally complete Candle is ignored until its close time.
        self.assertIsNone(resample_hourly(hourly(2), 2, as_of_ms=2 * HOUR_MS - 2))

    def test_rolling_24h_return_is_timestamped_and_future_changes_do_not_rewrite_past(self):
        bars = hourly(30)
        values = rolling_24h_return(bars)
        self.assertEqual(values[0].time_ms, bars[24].close_ms)
        self.assertEqual(values[0].value, bars[24].close / bars[0].close - 1)
        changed_future = bars[:27] + (Candle(bars[27].open_ms, bars[27].close_ms, D(500),
                               D(502), D(499), D(501), D(3)),) + bars[28:]
        changed = rolling_24h_return(changed_future)
        self.assertEqual(values[:3], changed[:3])
        self.assertIsNone(rolling_24h_return(bars[:24]))

    def test_prior_quantile_excludes_current_value_and_future(self):
        vals = tuple(map(D, (1, 2, 3, 4, 100)))
        q = prior_rolling_quantile(vals, 4, D("0.5"))
        self.assertEqual(q, (None, None, None, None, D("2.5")))
        changed = prior_rolling_quantile(vals[:-1] + (D(-1000),), 4, D("0.5"))
        self.assertEqual(q[:4], changed[:4])
        self.assertIsNone(prior_rolling_quantile(vals, 0, D("0.5")))

    def test_oi_zscore_uses_exact_four_hour_lag_and_prior_changes_only(self):
        obs = tuple(TimedValue(i * HOUR_MS, D(1000 + i * i + i)) for i in range(730))
        scores = oi_value_4h_change_zscore(obs)
        self.assertIsNotNone(scores)
        self.assertEqual(scores[0].time_ms, obs[724].time_ms)
        self.assertTrue(scores[0].value.is_finite())
        future = obs[:-1] + (TimedValue(obs[-1].time_ms, D(10_000_000)),)
        changed = oi_value_4h_change_zscore(future)
        self.assertEqual(scores[:-1], changed[:-1])
        with_gap = obs[:400] + tuple(TimedValue(x.time_ms + HOUR_MS, x.value) for x in obs[400:])
        self.assertIsNone(oi_value_4h_change_zscore(with_gap))

    def test_swing_pivot_is_available_at_confirmation_time(self):
        prices = (D(1), D(2), D(5), D(3), D(2), D(4))
        bars = tuple(Candle(i, i, x, x, x, x, D(1)) for i, x in enumerate(prices))
        pivots = confirmed_swing_pivots(bars, left=2, right=2)
        self.assertEqual(pivots, (type(pivots[0])(2, 4, "high", D(5)),))
        extended = bars + (Candle(6, 6, D(4), D(4), D(4), D(4), D(1)),)
        self.assertEqual(pivots, confirmed_swing_pivots(extended, left=2, right=2)[:1])


if __name__ == "__main__":
    unittest.main()
