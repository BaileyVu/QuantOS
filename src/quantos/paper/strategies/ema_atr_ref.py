"""EMA/ATR limit-pullback reference strategy."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D

from ..types import Signal, Snapshot
from ._reference import _atr, _bars, _ema, _signal


@dataclass(frozen=True)
class EmaAtrParameters:
    fast_ema: int = 20
    slow_ema: int = 50
    atr_period: int = 14
    pullback_atr: Decimal = D("0.25")
    stop_atr: Decimal = D("1.5")
    target_atr: Decimal = D("2.0")


@dataclass(frozen=True)
class EmaAtrLimitStrategy:
    parameters: EmaAtrParameters = EmaAtrParameters()
    strategy_id: str = "ema_atr_limit_pullback"
    version: str = "1.0.0"

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        bars = _bars(snapshot)
        p = self.parameters
        if (bars is None or p.fast_ema <= 0 or p.slow_ema <= p.fast_ema or p.atr_period <= 0
                or len(bars) < max(p.slow_ema, p.atr_period)
                or any(not x.is_finite() or x <= 0 for x in (p.pullback_atr, p.stop_atr, p.target_atr))):
            return None
        closes = tuple(b.close for b in bars)
        fast, slow, atr, last = _ema(closes, p.fast_ema), _ema(closes, p.slow_ema), _atr(bars, p.atr_period), bars[-1]
        if atr <= 0 or fast == slow:
            return None
        side = "LONG" if fast > slow and last.close > fast else "SHORT" if fast < slow and last.close < fast else None
        if side is None:
            return None
        entry = fast - p.pullback_atr * atr if side == "LONG" else fast + p.pullback_atr * atr
        stop = entry - p.stop_atr * atr if side == "LONG" else entry + p.stop_atr * atr
        target = entry + p.target_atr * atr if side == "LONG" else entry - p.target_atr * atr
        return _signal(snapshot, self.strategy_id, self.version, p, side, "LIMIT", entry, stop, target,
                       "EMA20/EMA50 trend with ATR14 pullback", (("ema_fast", fast), ("ema_slow", slow), ("atr", atr)))
