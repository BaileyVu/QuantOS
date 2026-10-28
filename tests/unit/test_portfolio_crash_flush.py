from dataclasses import replace
from decimal import Decimal as D
import unittest

from quantos.paper.types import Candle, Instrument, OpenInterestValue, Quote, Snapshot
from quantos.paper.strategies.crash_flush_e2 import CrashFlushE2Strategy
from quantos.paper.strategies.candidate_features import HOUR_MS


def crash_snapshot(symbol="BTCUSDT"):
    # 725 hourly archive labels include one unavailable-at-decision row; the
    # latest available label is one hour before the bar boundary.
    oi = []
    for i in range(726):
        value = D(1000 + i % 7)
        if i == 724:
            value = D(700)
        stamp = i * HOUR_MS
        oi.append(OpenInterestValue(stamp, stamp + HOUR_MS, value))
    bars = []
    closes = [D(100)] * 20 + [D(100), D(99), D(98), D(96), D(90)]
    start = 700 * HOUR_MS
    prior = closes[0]
    for i, close in enumerate(closes):
        open_ = prior if i else close
        stamp = start + i * HOUR_MS
        bars.append(Candle(stamp, stamp + HOUR_MS - 1, open_, max(open_, close) + 2,
                           min(open_, close) - 1, close, D(10)))
        prior = close
    last = bars[-1]
    ins = Instrument(symbol, "USDT", "TRADING", D("0.01"), D("0.001"), D("0.001"),
                     D("1000"), D("5"), base_asset=symbol[:-4])
    quote = Quote(symbol, last.close_ms, last.close_ms, D(89), D(91), D(2), D(2), D(90))
    return Snapshot(last.close_ms + 1, ins, quote, (), (), candles_1h=tuple(bars),
                    oi_values=tuple(oi))


class CrashFlushCandidateTests(unittest.TestCase):
    def test_crash_flush_requires_all_conditions_and_emits_catastrophe_stop(self):
        snapshot = crash_snapshot()
        signal = CrashFlushE2Strategy().evaluate(snapshot)
        self.assertIsNotNone(signal)
        self.assertEqual((signal.strategy_id, signal.side, signal.entry_type),
                         ("crash_flush_e2", "LONG", "MARKET"))
        self.assertEqual(signal.entry_price, D(90))
        self.assertEqual(signal.stop_loss, D("67.50"))
        self.assertEqual(signal.maximum_hold_seconds, 24 * 60 * 60)
        self.assertLess(dict(signal.features)["oi_value_change_4h_z"], D(-2))

        wrong_symbol = CrashFlushE2Strategy().evaluate(
            replace(snapshot, instrument=replace(snapshot.instrument, symbol="LTCUSDT"),
                    quote=replace(snapshot.quote, symbol="LTCUSDT")))
        self.assertIsNone(wrong_symbol)
        last = snapshot.candles_1h[-1]
        no_drawdown = snapshot.candles_1h[:-1] + (
            replace(last, high=D(98), low=D(93), close=D(94)),
        )
        self.assertIsNone(CrashFlushE2Strategy().evaluate(replace(snapshot, candles_1h=no_drawdown)))

    def test_future_oi_measurement_cannot_change_current_decision(self):
        strategy = CrashFlushE2Strategy()
        snapshot = crash_snapshot()
        base = strategy.evaluate(snapshot)
        self.assertEqual(base.observed_ms, snapshot.at_ms)
        # The newest archived label at the decision time is one hour stale by
        # label and available exactly at the decision boundary.
        self.assertEqual(snapshot.oi_values[-2].time_ms, snapshot.at_ms - HOUR_MS)
        self.assertEqual(snapshot.oi_values[-2].available_ms, snapshot.at_ms)
        self.assertEqual(dict(base.features)["oi_lag_hours"], D(1))
        future = OpenInterestValue(726 * HOUR_MS, 726 * HOUR_MS + HOUR_MS, D(1))
        changed = strategy.evaluate(replace(snapshot, oi_values=snapshot.oi_values + (future,)))
        self.assertEqual(base, changed)

    def test_latest_contemporaneous_available_oi_is_allowed_with_zero_lag(self):
        snapshot = crash_snapshot()
        rows = snapshot.oi_values
        # Move the synthetic shock to a row labeled at the decision boundary
        # and make it available then; retain its exact source label.
        rows = rows[:-2] + (
            replace(rows[-2], value=D(1000)),
            replace(rows[-1], available_ms=snapshot.at_ms, value=D(700)),
        )
        signal = CrashFlushE2Strategy().evaluate(replace(snapshot, oi_values=rows))
        self.assertIsNotNone(signal)
        self.assertEqual(dict(signal.features)["oi_lag_hours"], D(0))

    def test_hold_and_stop_sensitivity_are_explicit_variants(self):
        snapshot = crash_snapshot()
        for hours in (24, 48, 72):
            signal = CrashFlushE2Strategy(holding_hours=hours).evaluate(snapshot)
            self.assertEqual(signal.maximum_hold_seconds, hours * 3600)
        sensitivity = CrashFlushE2Strategy(stop_fraction=D("0.15")).evaluate(snapshot)
        self.assertEqual(sensitivity.stop_loss, D("76.50"))
        with self.assertRaises(ValueError):
            CrashFlushE2Strategy(holding_hours=36)
        with self.assertRaises(ValueError):
            CrashFlushE2Strategy(stop_fraction=D("0.20"))

    def test_late_or_gapped_oi_fails_closed(self):
        strategy = CrashFlushE2Strategy()
        snapshot = crash_snapshot()
        late = snapshot.oi_values[:-2] + (replace(snapshot.oi_values[-2],
                                                available_ms=snapshot.at_ms + 1), snapshot.oi_values[-1])
        self.assertIsNone(strategy.evaluate(replace(snapshot, oi_values=late)))
        # Once the newest two observations are unavailable, the remaining
        # observation is more than one hour stale and must fail closed.
        stale = snapshot.oi_values[:-2] + (
            replace(snapshot.oi_values[-2], available_ms=snapshot.at_ms + 1),
            replace(snapshot.oi_values[-1], available_ms=snapshot.at_ms + 1),
        )
        self.assertIsNone(strategy.evaluate(replace(snapshot, oi_values=stale)))
        gap = snapshot.oi_values[:-2] + snapshot.oi_values[-1:]
        self.assertIsNone(strategy.evaluate(replace(snapshot, oi_values=gap)))


if __name__ == "__main__":
    unittest.main()
