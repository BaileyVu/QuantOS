"""Explainable Alpha Engine decision contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from quantos.domain.common import require_decimal, require_non_empty, require_utc, require_v1_symbol


class AlphaAction(str, Enum):
    """The frozen V1 alpha actions."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class AlphaDecision:
    """A versioned, explainable request from Alpha to Risk."""

    timestamp: datetime
    symbol: str
    strategy_version: str
    model_version: str
    feature_version: str
    action: AlphaAction
    reason: str
    strategy_state: str
    model_score: Decimal

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        require_v1_symbol(self.symbol)
        require_non_empty(self.strategy_version, "strategy_version")
        require_non_empty(self.model_version, "model_version")
        require_non_empty(self.feature_version, "feature_version")
        if not isinstance(self.action, AlphaAction):
            raise ValueError("action must be an AlphaAction")
        require_non_empty(self.reason, "reason")
        require_non_empty(self.strategy_state, "strategy_state")
        require_decimal(self.model_score, "model_score")
