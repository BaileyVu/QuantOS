"""Provider-independent account and evaluation-result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quantos.domain.common import require_decimal, require_non_empty, require_utc


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """A typed result contract for the frozen V1 evaluation metrics."""

    run_id: str
    timestamp: datetime
    expected_value: Decimal
    net_profit: Decimal
    sharpe: Decimal
    sortino: Decimal
    maximum_drawdown: Decimal
    profit_factor: Decimal
    win_rate: Decimal
    trade_count: int
    average_trade: Decimal
    exposure: Decimal
    fees: Decimal
    slippage: Decimal

    def __post_init__(self) -> None:
        require_non_empty(self.run_id, "run_id")
        require_utc(self.timestamp, "timestamp")
        for field_name in (
            "expected_value",
            "net_profit",
            "sharpe",
            "sortino",
            "maximum_drawdown",
            "profit_factor",
            "win_rate",
            "average_trade",
            "exposure",
            "fees",
            "slippage",
        ):
            require_decimal(getattr(self, field_name), field_name)
        if isinstance(self.trade_count, bool) or not isinstance(self.trade_count, int):
            raise ValueError("trade_count must be an integer")
        if self.trade_count < 0:
            raise ValueError("trade_count must not be negative")
