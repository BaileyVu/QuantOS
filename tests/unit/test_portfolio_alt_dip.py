from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
import unittest

from quantos.paper.types import Candle, Instrument, Quote, Snapshot
from quantos.paper.strategies.alt_dip_reversal import AltDipReversalStrategy
from quantos.paper.strategies.candidate_features import HOUR_MS


def dip_snapshot(return_24h=D("-0.10"), *, calibration_offset_hours=-72):
    quarter_start = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
    bars = []
    start = quarter_start
    current_close = D(100) * (1 + return_24h)
    for i in range(25):
        close = current_close if i == 24 else D(100)
        open_ = D(100)
        stamp = start + i * HOUR_MS
        bars.append(Candle(stamp, stamp + HOUR_MS - 1, open_, max(open_, close) + 1,
                           min(open_, close) - 1, close, D(10)))
    last = bars[-1]
    ins = Instrument("SOLUSDT", "USDT", "TRADING", D("0.01"), D("0.001"), D("0.001"),
                     D("10000"), D("5"), base_asset="SOL")
    quote = Quote(ins.symbol, last.close_ms, last.close_ms, last.close - 1,
                  last.close + 1, D(2), D(2), last.close)
    return Snapshot(last.close_ms + 1, ins, quote, (), (), candles_1h=tuple(bars),
                    quarterly_return_cutoff=D("-0.08"),
                    calibration_end_ms=quarter_start + calibration_offset_hours * HOUR_MS)


class AltDipCandidateTests(unittest.TestCase):
    def test_fixed_quarter_cutoff_and_hold_grid(self):
        snap = dip_snapshot()
        strategy = AltDipReversalStrategy(D("0.005"), 12)
        signal = strategy.evaluate(snap)
        self.assertIsNotNone(signal)
        self.assertEqual((signal.strategy_id, signal.side, signal.maximum_hold_seconds),
                         ("alt_dip_reversal", "LONG", 12 * 3600))
        self.assertEqual(signal.stop_loss, signal.entry_price * D("0.75"))
        self.assertEqual(dict(signal.features)["cutoff_quantile"], D("0.005"))
        sensitivity = AltDipReversalStrategy(D("0.005"), 12, D("0.15")).evaluate(snap)
        self.assertEqual(sensitivity.stop_loss, signal.entry_price * D("0.85"))

        # At the exact threshold, the rule is inclusive.
        equal = strategy.evaluate(dip_snapshot(D("-0.08")))
        self.assertIsNotNone(equal)
        self.assertIsNone(AltDipReversalStrategy(D("0.01"), 24).evaluate(
            dip_snapshot(D("-0.07"))))
        for q in (D("0.005"), D("0.01")):
            for hours in (12, 24, 72):
                AltDipReversalStrategy(q, hours)
        with self.assertRaises(ValueError):
            AltDipReversalStrategy(D("0.02"), 12)
        with self.assertRaises(ValueError):
            AltDipReversalStrategy(D("0.005"), 48)
        with self.assertRaises(ValueError):
            AltDipReversalStrategy(D("0.005"), 12, D("0.20"))

    def test_calibration_boundary_and_invalid_quarter_inputs_fail_closed(self):
        strategy = AltDipReversalStrategy(D("0.005"), 24)
        self.assertIsNone(strategy.evaluate(dip_snapshot(calibration_offset_hours=-48)))
        self.assertIsNone(strategy.evaluate(replace(dip_snapshot(), quarterly_return_cutoff=None)))
        bad_bars = dip_snapshot().candles_1h[:-1] + (
            replace(dip_snapshot().candles_1h[-1], open_ms=dip_snapshot().candles_1h[-1].open_ms + 1),
        )
        self.assertIsNone(strategy.evaluate(replace(dip_snapshot(), candles_1h=bad_bars)))


if __name__ == "__main__":
    unittest.main()
