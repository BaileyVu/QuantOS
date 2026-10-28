"""Thirty-day 4h time-series momentum adaptation."""
from ..types import Snapshot
from .indicators import atr_wilder, ema, make_signal, validated_bars


class Tsmom4hStrategy:
    strategy_id = "tsmom_4h"
    version = "1.0.0"

    def evaluate(self, snapshot: Snapshot):
        bars = validated_bars(snapshot, "4h", 14_400_000, 181)
        if bars is None or snapshot.at_ms - bars[-1].close_ms > 90_000:
            return None
        closes = tuple(bar.close for bar in bars)
        ret = closes[-1] / closes[-181] - 1
        slope = ema(closes, 50) - ema(closes[:-1], 50)
        side = "LONG" if ret > 0 and slope > 0 else "SHORT" if ret < 0 and slope < 0 else None
        atr = atr_wilder(bars, 14)
        if side is None or atr <= 0:
            return None
        entry = bars[-1].close
        stop = entry - 2 * atr if side == "LONG" else entry + 2 * atr
        return make_signal(snapshot, self.strategy_id, self.version, str(bars[-1].close_ms), side,
                           "MARKET", entry, stop, None, 259200, bars[-1].close_ms + 90_000,
                           "30-day return and EMA50 slope agree",
                           (("return_30d", ret), ("ema50_slope", slope), ("atr14", atr)))
