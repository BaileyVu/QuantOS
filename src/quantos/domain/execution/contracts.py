"""Provider-independent execution intent and result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from quantos.domain.runtime_contracts import AccountSnapshot, Position

from quantos.domain.common import require_decimal, require_non_empty, require_utc, require_v1_symbol


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ExecutionStatus(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A stable internal order intent; it does not submit an order."""

    request_id: str
    timestamp: datetime
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.request_id, "request_id")
        require_utc(self.timestamp, "timestamp")
        require_v1_symbol(self.symbol)
        if not isinstance(self.side, OrderSide):
            raise ValueError("side must be an OrderSide")
        if not isinstance(self.order_type, OrderType):
            raise ValueError("order_type must be an OrderType")
        require_decimal(self.quantity, "quantity")
        if self.quantity <= Decimal("0"):
            raise ValueError("quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("market orders must not include limit_price")
        if self.limit_price is not None:
            require_decimal(self.limit_price, "limit_price")
            if self.limit_price <= Decimal("0"):
                raise ValueError("limit_price must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """A normalized report of an execution attempt or observed order state."""

    request_id: str
    timestamp: datetime
    status: ExecutionStatus
    requested_quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    fill_price: Decimal | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.request_id, "request_id")
        require_utc(self.timestamp, "timestamp")
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("status must be an ExecutionStatus")
        require_decimal(self.requested_quantity, "requested_quantity")
        if self.requested_quantity <= Decimal("0"):
            raise ValueError("requested_quantity must be positive")
        require_decimal(self.filled_quantity, "filled_quantity", non_negative=True)
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("filled_quantity must not exceed requested_quantity")
        if self.fill_price is not None:
            require_decimal(self.fill_price, "fill_price")
            if self.fill_price <= Decimal("0"):
                raise ValueError("fill_price must be positive")
        if self.reason is not None:
            require_non_empty(self.reason, "reason")
        has_fill = self.filled_quantity > Decimal("0")
        if has_fill != (self.fill_price is not None):
            raise ValueError("fill_price must be present exactly when filled_quantity is positive")
        if self.status is ExecutionStatus.ACKNOWLEDGED:
            if has_fill:
                raise ValueError("acknowledged reports must not claim fills")
        elif self.status is ExecutionStatus.REJECTED:
            if has_fill:
                raise ValueError("rejected reports must not claim fills")
            if self.reason is None:
                raise ValueError("rejected reports require reason")
        elif self.status is ExecutionStatus.PARTIALLY_FILLED:
            if not Decimal("0") < self.filled_quantity < self.requested_quantity:
                raise ValueError("partially filled reports require a partial positive fill")
        elif self.status is ExecutionStatus.FILLED:
            if self.filled_quantity != self.requested_quantity:
                raise ValueError("filled reports require the full requested quantity")
        elif self.status is ExecutionStatus.CANCELED:
            if self.filled_quantity >= self.requested_quantity:
                raise ValueError("canceled reports must not be fully filled")
        elif self.status is ExecutionStatus.UNKNOWN and self.reason is None:
            raise ValueError("unknown reports require reason")
