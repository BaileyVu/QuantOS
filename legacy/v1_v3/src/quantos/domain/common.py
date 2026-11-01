"""Shared validation helpers for provider-independent domain contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

V1_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT"})
V1_INTERVAL = "1m"


def require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def require_v1_symbol(symbol: str) -> str:
    require_non_empty(symbol, "symbol")
    if symbol not in V1_SYMBOLS:
        raise ValueError(f"symbol must be one of {sorted(V1_SYMBOLS)}")
    return symbol


def require_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware UTC datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be in UTC")
    return value


def require_decimal(value: Decimal, field_name: str, *, non_negative: bool = False) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if non_negative and value < Decimal("0"):
        raise ValueError(f"{field_name} must not be negative")
    return value

