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


def live_library():
    """$50 USDC run: trend + event rules; EMA/Donchian references disabled; event rules USDC-mapped."""
    from .tsmom_4h import Tsmom4hStrategy
    from .turtle_55 import Turtle55Strategy
    from .vol_squeeze_breakout import VolSqueezeBreakoutStrategy
    from .delist_short_e17 import DelistShortE17Strategy
    from .crash_flush_e2 import MAJORS, CrashFlushE2Strategy
    from .alt_dip_reversal import AltDipReversalStrategy
    from .candidate_features import ALT_SOURCE_UNIVERSE
    from .usdc_mapped import UsdcMapped
    return (Tsmom4hStrategy(), Turtle55Strategy(), VolSqueezeBreakoutStrategy(), DelistShortE17Strategy(),
            UsdcMapped(CrashFlushE2Strategy(oi_mode="tolerant"), MAJORS),
            UsdcMapped(AltDipReversalStrategy(D("0.01"), 24), frozenset(ALT_SOURCE_UNIVERSE)))


def library(include_candidates=False):
    """Preregistered paper strategies; references retain their historical IDs."""
    from .tsmom_4h import Tsmom4hStrategy
    from .turtle_55 import Turtle55Strategy
    from .vol_squeeze_breakout import VolSqueezeBreakoutStrategy
    from .delist_short_e17 import DelistShortE17Strategy
    existing = (EmaAtrLimitStrategy(), DonchianAtrStopStrategy(), Tsmom4hStrategy(),
                Turtle55Strategy(), VolSqueezeBreakoutStrategy(), DelistShortE17Strategy())
    if not include_candidates:
        return existing
    from .crash_flush_e2 import CrashFlushE2Strategy
    from .alt_dip_reversal import AltDipReversalStrategy
    from .ema_level_2h import EmaLevel2hStrategy
    return existing + (CrashFlushE2Strategy(), AltDipReversalStrategy(D("0.01"), 24), EmaLevel2hStrategy())
