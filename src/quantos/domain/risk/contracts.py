"""Risk approval or final-rejection contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quantos.domain.common import require_decimal, require_non_empty, require_utc, require_v1_symbol


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """The final risk gate before an order reaches Execution."""

    timestamp: datetime
    symbol: str
    approved: bool
    approved_quantity: Decimal | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        require_v1_symbol(self.symbol)
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a boolean")
        if self.approved:
            if self.approved_quantity is None:
                raise ValueError("an approved risk decision requires approved_quantity")
            require_decimal(self.approved_quantity, "approved_quantity")
            if self.approved_quantity <= Decimal("0"):
                raise ValueError("approved_quantity must be positive")
            if self.rejection_reason is not None:
                raise ValueError("an approved risk decision must not include rejection_reason")
        else:
            if self.approved_quantity is not None:
                raise ValueError("a rejected risk decision must not include approved_quantity")
            if self.rejection_reason is None:
                raise ValueError("a rejected risk decision requires rejection_reason")
            require_non_empty(self.rejection_reason, "rejection_reason")
