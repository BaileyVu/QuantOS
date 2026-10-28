"""55/20 Donchian Turtle System 2 adaptation on 4h candles."""
from ..types import Snapshot
from .indicators import atr_wilder, make_signal, validated_bars


class Turtle55Strategy:
    strategy_id = "turtle_55"
    version = "1.0.0"

    def evaluate(self, snapshot: Snapshot):
        bars = validated_bars(snapshot, "4h", 14_400_000, 56)
        if bars is None or snapshot.at_ms - bars[-1].close_ms > 90_000:
            return None
        prior, last = bars[-56:-1], bars[-1]
        upper, lower = max(b.high for b in prior), min(b.low for b in prior)
        side = "LONG" if last.close > upper else "SHORT" if last.close < lower else None
        atr = atr_wilder(bars, 20)
        if side is None or atr <= 0:
            return None
        # The STOP rests at the prior 55-bar boundary; breakout confirmation is
        # still based on this completed candle, so execution risk decides if late.
        entry = upper + snapshot.instrument.tick if side == "LONG" else lower - snapshot.instrument.tick
        stop = entry - 2 * atr if side == "LONG" else entry + 2 * atr
        exit_level = min(b.low for b in bars[-20:]) if side == "LONG" else max(b.high for b in bars[-20:])
        return make_signal(snapshot, self.strategy_id, self.version, str(last.close_ms), side,
                           "STOP", entry, stop, None, 259200, last.close_ms + 90_000,
                           "55-bar boundary STOP after completed breakout; exit on opposite 20-bar channel",
                           (("entry_high", upper), ("entry_low", lower), ("exit_level", exit_level), ("atr20", atr)))
