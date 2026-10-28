"""Causal, deliberately simple reference strategies for V3 paper evaluation.

These rules are transparent examples, not evidence of an economic edge.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
from typing import Literal

from ..types import Candle, Signal, Snapshot, Strategy
from ._reference import (
    BAR_MS, D, EXPIRY_MS, HOLD_SECONDS, MIN_BARS,
    _atr, _bars, _ceil_tick, _ema, _floor_tick, _id, _signal,
)
from ._registry import StrategyMetadata, strategy_registry
from .donchian_atr_ref import DonchianAtrParameters, DonchianAtrStopStrategy
from .ema_atr_ref import EmaAtrLimitStrategy, EmaAtrParameters

__all__ = [
    "asdict", "dataclass", "Decimal", "ROUND_CEILING", "ROUND_FLOOR",
    "hashlib", "json", "Literal", "Candle", "Signal", "Snapshot", "Strategy",
    "BAR_MS", "D", "EXPIRY_MS", "HOLD_SECONDS", "MIN_BARS",
    "DonchianAtrParameters", "DonchianAtrStopStrategy",
    "EmaAtrLimitStrategy", "EmaAtrParameters", "StrategyMetadata",
    "strategy_registry", "_atr", "_bars", "_ceil_tick", "_ema",
    "_floor_tick", "_id", "_signal",
]


def library():
    """Preregistered paper strategies; references retain their historical IDs."""
    from .tsmom_4h import Tsmom4hStrategy
    from .turtle_55 import Turtle55Strategy
    from .vol_squeeze_breakout import VolSqueezeBreakoutStrategy
    from .delist_short_e17 import DelistShortE17Strategy
    return (EmaAtrLimitStrategy(), DonchianAtrStopStrategy(), Tsmom4hStrategy(),
            Turtle55Strategy(), VolSqueezeBreakoutStrategy(), DelistShortE17Strategy())
