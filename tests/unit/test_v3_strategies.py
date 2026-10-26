from dataclasses import replace
from decimal import Decimal as D
import unittest

from quantos.paper.strategies import (
    DonchianAtrParameters, DonchianAtrStopStrategy, EmaAtrLimitStrategy,
    EmaAtrParameters, strategy_registry,
)
from quantos.paper.types import Candle, Instrument, Quote, Snapshot


def make_snapshot(closes, at_ms=None):
    start = 1_800_000_000_000
    bars = []
    for i, c in enumerate(closes):
        c = D(str(c))
        o = closes[i-1] if i else c
        o = D(str(o))
        bars.append(Candle(start+i*300_000, start+(i+1)*300_000-1,
                           o, max(o, c)+D("0.4"), min(o, c)-D("0.4"), c, D("10")))
    now = at_ms if at_ms is not None else bars[-1].close_ms
    instrument = Instrument("BTCUSDC", "USDC", "TRADING", D("0.1"), D("0.001"), D("0.001"), D("100"), D("5"))
    quote = Quote("BTCUSDC", now, now, D("99"), D("101"), D("2"), D("2"), D("100"))
    return Snapshot(now, instrument, quote, (), tuple(bars))


class V3StrategyTests(unittest.TestCase):
    def test_ema_long_short_and_no_signal(self):
        strategy = EmaAtrLimitStrategy()
        up = [D(100)+D(i)*D("0.3") for i in range(80)]
        down = [D(124)-D(i)*D("0.3") for i in range(80)]
        flat = [D(100)]*80
        self.assertEqual(strategy.evaluate(make_snapshot(up)).side, "LONG")
        self.assertEqual(strategy.evaluate(make_snapshot(down)).side, "SHORT")
        self.assertIsNone(strategy.evaluate(make_snapshot(flat)))

    def test_donchian_breakout_long_short_and_no_signal(self):
        strategy = DonchianAtrStopStrategy()
        rising = [100+i*D("0.1") for i in range(65)]
        rising[-1] += D(5)
        falling = [D(110)-i*D("0.1") for i in range(65)]
        falling[-1] -= D(5)
        long_snapshot = make_snapshot(rising)
        short_snapshot = make_snapshot(falling)
        long_signal = strategy.evaluate(long_snapshot)
        short_signal = strategy.evaluate(short_snapshot)
        self.assertEqual(long_signal.side, "LONG")
        self.assertGreater(long_signal.entry_price, long_snapshot.candles_5m[-1].high)
        self.assertEqual(short_signal.side, "SHORT")
        self.assertLess(short_signal.entry_price, short_snapshot.candles_5m[-1].low)
        self.assertIsNone(strategy.evaluate(make_snapshot([D(100)]*65)))

    def test_future_bar_stale_gap_and_nonfinite_are_rejected(self):
        snap = make_snapshot([D(100)+D(i)*D("0.2") for i in range(70)])
        strategy = EmaAtrLimitStrategy()
        self.assertIsNone(strategy.evaluate(replace(snap, at_ms=snap.candles_5m[-1].close_ms-1)))
        self.assertIsNone(strategy.evaluate(replace(snap, at_ms=snap.at_ms+600_001)))
        gap = list(snap.candles_5m)
        gap[20] = replace(gap[20], open_ms=gap[20].open_ms+1)
        self.assertIsNone(strategy.evaluate(replace(snap, candles_5m=tuple(gap))))
        bad = list(snap.candles_5m)
        bad[-1] = replace(bad[-1], close=D("NaN"))
        self.assertIsNone(strategy.evaluate(replace(snap, candles_5m=tuple(bad))))

    def test_only_closed_data_drives_signal_and_identity_is_deterministic(self):
        closes = [D(100)+D(i)*D("0.2") for i in range(70)]
        snap = make_snapshot(closes)
        strategy = EmaAtrLimitStrategy()
        first = strategy.evaluate(snap)
        self.assertEqual(first, strategy.evaluate(snap))
        self.assertEqual(first.signal_id, strategy.evaluate(snap).signal_id)
        self.assertEqual(first.expires_ms, snap.at_ms+90_000)
        self.assertEqual(first.maximum_hold_seconds, 3600)
        # Appending an unclosed future observation is rejected rather than read.
        future = replace(snap.candles_5m[-1], open_ms=snap.at_ms+1,
                         close_ms=snap.at_ms+300_000, close=D(999), high=D(1000))
        with_future = replace(snap, candles_5m=snap.candles_5m+(future,))
        self.assertIsNone(strategy.evaluate(with_future))

    def test_parameter_and_version_changes_change_identity(self):
        snap = make_snapshot([D(100)+D(i)*D("0.2") for i in range(70)])
        a = EmaAtrLimitStrategy()
        b = EmaAtrLimitStrategy(EmaAtrParameters(pullback_atr=D("0.5")))
        c = replace(a, version="1.0.1")
        self.assertNotEqual(a.evaluate(snap).signal_id, b.evaluate(snap).signal_id)
        self.assertNotEqual(a.evaluate(snap).signal_id, c.evaluate(snap).signal_id)
        self.assertEqual(strategy_registry()[-1].status, "MONITOR_ONLY")
        self.assertFalse(strategy_registry()[-1].eligible)

    def test_oversized_period_and_invalid_parameters_fail_closed(self):
        snap = make_snapshot([D(100)+D(i)*D("0.2") for i in range(70)])
        self.assertIsNone(EmaAtrLimitStrategy(EmaAtrParameters(slow_ema=100)).evaluate(snap))
        self.assertIsNone(DonchianAtrStopStrategy(DonchianAtrParameters(channel_period=100)).evaluate(snap))
        self.assertIsNone(EmaAtrLimitStrategy(EmaAtrParameters(pullback_atr=D("NaN"))).evaluate(snap))


if __name__ == "__main__":
    unittest.main()
