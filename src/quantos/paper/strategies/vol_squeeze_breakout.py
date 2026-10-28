"""Bollinger-inside-Keltner compression and first release-range breakout."""
from decimal import Decimal as D
from ..types import Snapshot
from .indicators import atr_wilder, ema, make_signal, validated_bars


class VolSqueezeBreakoutStrategy:
    strategy_id = "vol_squeeze_breakout"
    version = "1.0.0"

    def evaluate(self, snapshot: Snapshot):
        bars = validated_bars(snapshot, "1h", 3_600_000, 21)
        if bars is None or snapshot.at_ms - bars[-1].close_ms > 90_000:
            return None
        states = []
        for i in range(len(bars)):
            if i < 19:
                states.append(False)
                continue
            window = bars[i - 19:i + 1]
            closes = tuple(bar.close for bar in window)
            mid = sum(closes, D(0)) / D(20)
            variance = sum(((value - mid) ** 2 for value in closes), D(0)) / D(20)
            sd = variance.sqrt()
            bb_hi, bb_lo = mid + 2 * sd, mid - 2 * sd
            prefix = tuple(bars[:i + 1])
            kc_mid = ema(tuple(bar.close for bar in prefix), 20)
            kc_atr = atr_wilder(prefix, 20)
            states.append(bb_hi <= kc_mid + D("1.5") * kc_atr and bb_lo >= kc_mid - D("1.5") * kc_atr)
        if states[-1] or not states[-2]:
            return None
        start = len(states) - 2
        while start > 0 and states[start - 1]:
            start -= 1
        squeeze = bars[start:-1]
        if not squeeze:
            return None
        range_high, range_low = max(bar.high for bar in squeeze), min(bar.low for bar in squeeze)
        last = bars[-1]
        side = "LONG" if last.close > range_high else "SHORT" if last.close < range_low else None
        if side is None:
            return None
        entry = last.high + snapshot.instrument.tick if side == "LONG" else last.low - snapshot.instrument.tick
        stop = range_low if side == "LONG" else range_high
        risk = entry - stop if side == "LONG" else stop - entry
        if risk <= 0:
            return None
        target = entry + 2 * risk if side == "LONG" else entry - 2 * risk
        return make_signal(snapshot, self.strategy_id, self.version, str(last.close_ms), side, "STOP",
                           entry, stop, target, 14400, last.close_ms + 90_000,
                           "first squeeze-off candle closed beyond squeeze range",
                           (("range_high", range_high), ("range_low", range_low)))
