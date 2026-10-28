"""Descriptive registry for paper strategies."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyMetadata:
    strategy_id: str
    version: str
    status: str
    eligible: bool
    rationale: str


def strategy_registry() -> tuple[StrategyMetadata, ...]:
    """Registry is descriptive; E17 cannot be evaluated as a trade strategy."""
    return (
        StrategyMetadata("ema_atr_limit_pullback", "1.0.0", "PAPER_REFERENCE", True, "Causal and synthetic lifecycle tests passed; no verified economic edge"),
        StrategyMetadata("donchian_atr_stop_breakout", "1.0.0", "PAPER_REFERENCE", True, "Causal and synthetic lifecycle tests passed; no verified economic edge"),
        StrategyMetadata("E17", "UNVERIFIED_SOURCE", "MONITOR_ONLY", False, "Source identity and original timing evidence are unverified"),
    )
