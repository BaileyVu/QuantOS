"""Donchian/ATR stop-breakout reference strategy."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D

from ..types import Signal, Snapshot
from ._reference import _atr, _bars, _signal


@dataclass(frozen=True)
class DonchianAtrParameters:
    channel_period: int = 20
    atr_period: int = 14
    stop_atr: Decimal = D("1.5")
    target_atr: Decimal = D("2.0")


@dataclass(frozen=True)
class DonchianAtrStopStrategy:
    parameters: DonchianAtrParameters = DonchianAtrParameters()
    strategy_id: str = "donchian_atr_stop_breakout"
    version: str = "1.0.0"

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        bars = _bars(snapshot)
        p = self.parameters
        if (bars is None or p.channel_period < 1 or p.atr_period < 1
                or len(bars) < max(p.channel_period + 1, p.atr_period)
                or any(not x.is_finite() or x <= 0 for x in (p.stop_atr, p.target_atr))):
            return None
        prior = bars[-p.channel_period-1:-1]
        last = bars[-1]
        upper, lower, atr = max(x.high for x in prior), min(x.low for x in prior), _atr(bars, p.atr_period)
        if atr <= 0:
            return None
        if last.close > upper:
            side, entry = "LONG", last.high + snapshot.instrument.tick
        elif last.close < lower:
            side, entry = "SHORT", last.low - snapshot.instrument.tick
        else:
            return None
        stop = entry - p.stop_atr * atr if side == "LONG" else entry + p.stop_atr * atr
        target = entry + p.target_atr * atr if side == "LONG" else entry - p.target_atr * atr
        return _signal(snapshot, self.strategy_id, self.version, p, side, "STOP", entry, stop, target,
                       "Donchian20 close breakout with ATR14 exits", (("donchian_high", upper), ("donchian_low", lower), ("atr", atr)))
