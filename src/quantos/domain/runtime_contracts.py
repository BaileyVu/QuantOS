"""Shared immutable runtime contracts and pure helpers; no business-state owner.

Risk reads these snapshots. Execution alone applies account/order/fill effects.
This shared file is not an additional production business module.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import (Context, Decimal, DivisionByZero, FloatOperation, InvalidOperation,
                     Overflow, ROUND_HALF_EVEN, Underflow, localcontext)
from enum import Enum
import hashlib
import json
from types import MappingProxyType

from quantos.domain.common import require_decimal, require_non_empty, require_utc, require_v1_symbol


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


def validate_account(account: AccountSnapshot) -> None:
    if type(account) is not AccountSnapshot:
        raise ValueError('missing account snapshot')
    AccountSnapshot(account.timestamp, account.balances, account.positions)
    if 'USDT' not in account.balances or set(account.balances) - {'USDT', 'BTC', 'ETH'}:
        raise ValueError('unsupported account balance')
    positions = {p.symbol: p for p in account.positions}
    for position in account.positions:
        Position.__post_init__(position)
        if position.timestamp > account.timestamp:
            raise ValueError('future position timestamp')
    for symbol in ('BTCUSDT', 'ETHUSDT'):
        quantity = positions[symbol].quantity if symbol in positions else Decimal('0')
        if account.balances.get(symbol[:-4], Decimal('0')) != quantity:
            raise ValueError('base balance and position mismatch')


def arithmetic() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                   capitals=1, clamp=0, flags=[],
                   traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, FloatOperation])


@dataclass(frozen=True, slots=True)
class TransactionCosts:
    fee_rate: Decimal
    slippage_rate: Decimal

    def __post_init__(self):
        for name in ('fee_rate', 'slippage_rate'):
            value = require_decimal(getattr(self, name), name, non_negative=True)
            if value >= 1:
                raise ValueError(f'{name} must be below one')

    @property
    def conservative_cost_rate(self) -> Decimal:
        """Per-order fee plus adverse slippage, including fee on slipped notional."""
        with localcontext(arithmetic()):
            return self.fee_rate + self.slippage_rate + self.fee_rate*self.slippage_rate


def primitive(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('non-finite evidence')
        text = format(value, 'f')
        return (text.rstrip('0').rstrip('.') if '.' in text else text) if value else '0'
    if isinstance(value, datetime):
        return value.isoformat(timespec='microseconds')
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [primitive(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise ValueError('unsupported evidence type')


def canonical(value) -> str:
    return json.dumps(primitive(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False)


def identity(value) -> str:
    return hashlib.sha256(canonical(value).encode('ascii')).hexdigest()
