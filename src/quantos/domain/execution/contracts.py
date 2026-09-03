"""Provider-independent execution intent and result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping

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


@dataclass(frozen=True, slots=True)
class Position:
    """A V1 spot position snapshot maintained with execution state."""

    symbol: str
    quantity: Decimal
    average_entry_price: Decimal | None
    timestamp: datetime

    def __post_init__(self) -> None:
        require_v1_symbol(self.symbol)
        require_decimal(self.quantity, "quantity", non_negative=True)
        require_utc(self.timestamp, "timestamp")
        if self.quantity == Decimal("0") and self.average_entry_price is not None:
            raise ValueError("a flat position must not have an average_entry_price")
        if self.quantity > Decimal("0"):
            if self.average_entry_price is None:
                raise ValueError("an open position requires an average_entry_price")
            require_decimal(self.average_entry_price, "average_entry_price")
            if self.average_entry_price <= Decimal("0"):
                raise ValueError("average_entry_price must be positive")


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """A timestamped account state used by Risk and Execution."""

    timestamp: datetime
    balances: Mapping[str, Decimal]
    positions: tuple[Position, ...]

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        copied_balances: dict[str, Decimal] = {}
        for asset, value in self.balances.items():
            require_non_empty(asset, "asset")
            copied_balances[asset] = require_decimal(value, f"balance {asset}", non_negative=True)
        object.__setattr__(self, "balances", MappingProxyType(copied_balances))
        try:
            positions = tuple(self.positions)
        except TypeError as error:
            raise ValueError("positions must be an iterable of Position values") from error
        if not all(isinstance(position, Position) for position in positions):
            raise ValueError("positions must contain only Position values")
        if len({position.symbol for position in positions}) != len(positions):
            raise ValueError("positions must not contain duplicate symbols")
        object.__setattr__(self, "positions", positions)
