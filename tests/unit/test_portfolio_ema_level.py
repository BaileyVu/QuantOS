from dataclasses import replace
from decimal import Decimal as D
import unittest

from quantos.paper.types import Candle, Instrument, Quote, Snapshot
from quantos.paper.strategies.ema_level_2h import EmaLevel2hStrategy, PERIOD_MS


def ema_snapshot():
    bars = []
    start = 1000 * PERIOD_MS
    closes = [D(105)] * 288
    for i in range(260, 287):
        closes[i] = D("102.1") - D(i - 260) * D("0.2") / D(26)
    closes[287] = D("110")
    previous = closes[0]
    for i, close in enumerate(closes):
        open_ = previous
        low = D(100) if i in (10, 30, 287) else min(open_, close) - 1
        high = D(120) if i == 20 else D(116)
        stamp = start + i * PERIOD_MS
        bars.append(Candle(stamp, stamp + PERIOD_MS - 1, open_, max(high, open_, close),
                           min(low, open_, close), close, D(10)))
        previous = close
    last = bars[-1]
    ins = Instrument("BTCUSDT", "USDT", "TRADING", D("0.01"), D("0.001"), D("0.001"),
                     D("1000"), D("5"), base_asset="BTC")
    at = last.close_ms + 1
    quote = Quote(ins.symbol, at, at, D(102), D(103), D(2), D(2), last.close)
    return Snapshot(at, ins, quote, (), (), candles_2h=tuple(bars))


class EmaLevelCandidateTests(unittest.TestCase):
    def test_ema_cross_at_third_distinct_confirmed_support_touch(self):
        snap = ema_snapshot()
        signal = EmaLevel2hStrategy().evaluate(snap)
        self.assertIsNotNone(signal)
        self.assertEqual((signal.strategy_id, signal.side, signal.entry_type),
                         ("ema_level_2h", "LONG", "MARKET"))
        self.assertEqual(signal.maximum_hold_seconds, 72 * 3600)
        self.assertEqual(dict(signal.features)["target_r"], D("1.5"))
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertGreater(signal.take_profit, signal.entry_price)

    def test_future_bars_do_not_change_current_signal_and_bad_alignment_fails_closed(self):
        strategy = EmaLevel2hStrategy()
        snap = ema_snapshot()
        base = strategy.evaluate(snap)
        last = snap.candles_2h[-1]
        future_open = last.close_ms + 1
        future = Candle(future_open, future_open + PERIOD_MS - 1, D(500), D(600), D(400), D(550), D(1))
        self.assertEqual(base, strategy.evaluate(replace(snap, candles_2h=snap.candles_2h + (future,))))

        gap = snap.candles_2h[:-2] + snap.candles_2h[-1:]
        self.assertIsNone(strategy.evaluate(replace(snap, candles_2h=gap)))
        off_grid = snap.candles_2h[:-1] + (replace(last, open_ms=last.open_ms + 1),)
        self.assertIsNone(strategy.evaluate(replace(snap, candles_2h=off_grid)))

    def test_only_original_nine_majors_are_eligible(self):
        snap = ema_snapshot()
        changed_ins = replace(snap.instrument, symbol="DOTUSDT")
        changed_quote = replace(snap.quote, symbol="DOTUSDT")
        self.assertIsNone(EmaLevel2hStrategy().evaluate(
            replace(snap, instrument=changed_ins, quote=changed_quote)))


if __name__ == "__main__":
    unittest.main()
