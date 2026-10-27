"""Source-neutral exact AggregateTrade completed-minute primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable

from quantos.domain.common import require_v1_symbol
from quantos.domain.market_data.research_events.aggregate_trade import AggregateTrade


def _utc_minute(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError(f"{name} must be a built-in timezone-aware datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    if value.second or value.microsecond:
        raise ValueError(f"{name} must be exactly minute-aligned")
    return value


def _decimal_component(value: Decimal) -> tuple[int, int]:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError("minute totals require finite non-negative Decimals")
    sign, digits, exponent = value.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    if sign:
        coefficient = -coefficient
    if exponent > 0:
        coefficient *= 10**exponent
        exponent = 0
    return coefficient, max(0, -exponent)


class _ExactDecimalAccumulator:
    __slots__ = ("_coefficient", "_scale")

    def __init__(self) -> None:
        self._coefficient = 0
        self._scale = 0

    def _add_component(self, coefficient: int, scale: int) -> None:
        target = max(self._scale, scale)
        self._coefficient = (
            self._coefficient * 10 ** (target - self._scale)
            + coefficient * 10 ** (target - scale)
        )
        self._scale = target

    def add(self, value: Decimal) -> None:
        self._add_component(*_decimal_component(value))

    def add_product(self, left: Decimal, right: Decimal) -> None:
        left_coefficient, left_scale = _decimal_component(left)
        right_coefficient, right_scale = _decimal_component(right)
        self._add_component(
            left_coefficient * right_coefficient,
            left_scale + right_scale,
        )

    def value(self) -> Decimal:
        if self._coefficient == 0:
            return Decimal(0)
        absolute = str(abs(self._coefficient))
        return Decimal(
            (
                1 if self._coefficient < 0 else 0,
                tuple(int(character) for character in absolute),
                -self._scale,
            )
        )


def _canonical_decimal(value: Decimal) -> str:
    _decimal_component(value)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AggregateTradeMinutePrimitives:
    """Neutral state shared by historical and live minute aggregation."""

    symbol: str
    minute_start_time: datetime
    minute_end_time_exclusive: datetime
    event_count: int
    aggressive_buy_event_count: int
    aggressive_sell_event_count: int
    total_base_quantity: Decimal
    total_quote_notional: Decimal
    aggressive_buy_base_quantity: Decimal
    aggressive_sell_base_quantity: Decimal
    aggressive_buy_quote_notional: Decimal
    aggressive_sell_quote_notional: Decimal
    first_aggregate_trade_id: int | None
    last_aggregate_trade_id: int | None
    first_event_time: datetime | None
    last_event_time: datetime | None

    def __post_init__(self) -> None:
        require_v1_symbol(self.symbol)
        start = _utc_minute(self.minute_start_time, "minute_start_time")
        if self.minute_end_time_exclusive != start + timedelta(minutes=1):
            raise ValueError("minute primitives require an exact one-minute interval")
        if type(self.event_count) is not int or self.event_count < 0:
            raise ValueError("event_count must be a non-negative exact integer")
        if (
            type(self.aggressive_buy_event_count) is not int
            or type(self.aggressive_sell_event_count) is not int
            or self.aggressive_buy_event_count < 0
            or self.aggressive_sell_event_count < 0
            or self.aggressive_buy_event_count
            + self.aggressive_sell_event_count
            != self.event_count
        ):
            raise ValueError("aggressor counts must be non-negative and exhaustive")
        decimal_values = (
            self.total_base_quantity,
            self.total_quote_notional,
            self.aggressive_buy_base_quantity,
            self.aggressive_sell_base_quantity,
            self.aggressive_buy_quote_notional,
            self.aggressive_sell_quote_notional,
        )
        for value in decimal_values:
            _decimal_component(value)
        if not _exact_sum_equal(
            self.aggressive_buy_base_quantity,
            self.aggressive_sell_base_quantity,
            self.total_base_quantity,
        ):
            raise ValueError("aggressor base quantities must equal total base quantity")
        if not _exact_sum_equal(
            self.aggressive_buy_quote_notional,
            self.aggressive_sell_quote_notional,
            self.total_quote_notional,
        ):
            raise ValueError("aggressor quote notionals must equal total quote notional")
        identifiers = (self.first_aggregate_trade_id, self.last_aggregate_trade_id)
        times = (self.first_event_time, self.last_event_time)
        if self.event_count == 0:
            if any(value != Decimal(0) for value in decimal_values):
                raise ValueError("zero-event primitives require exact zero totals")
            if any(value is not None for value in identifiers + times):
                raise ValueError("zero-event primitives require null boundaries")
        else:
            if any(type(value) is not int or value < 0 for value in identifiers):
                raise ValueError("nonzero primitives require non-negative event IDs")
            if any(type(value) is not datetime for value in times):
                raise ValueError("nonzero primitives require event timestamps")
            first = times[0]
            last = times[1]
            if first is None or last is None:
                raise ValueError("nonzero primitives require event timestamps")
            if first.tzinfo is None or last.tzinfo is None:
                raise ValueError("event timestamps must be timezone-aware UTC")
            if first.utcoffset() != timedelta(0) or last.utcoffset() != timedelta(0):
                raise ValueError("event timestamps must be UTC")
            if not start <= first <= last < self.minute_end_time_exclusive:
                raise ValueError("event boundaries must lie inside the minute")

    def as_canonical_dict(self) -> dict[str, object]:
        return {
            "aggressive_buy_base_quantity": _canonical_decimal(
                self.aggressive_buy_base_quantity
            ),
            "aggressive_buy_event_count": self.aggressive_buy_event_count,
            "aggressive_buy_quote_notional": _canonical_decimal(
                self.aggressive_buy_quote_notional
            ),
            "aggressive_sell_base_quantity": _canonical_decimal(
                self.aggressive_sell_base_quantity
            ),
            "aggressive_sell_event_count": self.aggressive_sell_event_count,
            "aggressive_sell_quote_notional": _canonical_decimal(
                self.aggressive_sell_quote_notional
            ),
            "event_count": self.event_count,
            "first_aggregate_trade_id": self.first_aggregate_trade_id,
            "first_event_time": _utc_text(self.first_event_time),
            "last_aggregate_trade_id": self.last_aggregate_trade_id,
            "last_event_time": _utc_text(self.last_event_time),
            "minute_end_time_exclusive": _utc_text(
                self.minute_end_time_exclusive
            ),
            "minute_start_time": _utc_text(self.minute_start_time),
            "symbol": self.symbol,
            "total_base_quantity": _canonical_decimal(self.total_base_quantity),
            "total_quote_notional": _canonical_decimal(self.total_quote_notional),
        }

    @property
    def content_sha256(self) -> str:
        payload = (
            json.dumps(
                self.as_canonical_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return sha256(payload).hexdigest()


def _exact_sum_equal(left: Decimal, right: Decimal, total: Decimal) -> bool:
    left_coefficient, left_scale = _decimal_component(left)
    right_coefficient, right_scale = _decimal_component(right)
    total_coefficient, total_scale = _decimal_component(total)
    scale = max(left_scale, right_scale, total_scale)
    return (
        left_coefficient * 10 ** (scale - left_scale)
        + right_coefficient * 10 ** (scale - right_scale)
        == total_coefficient * 10 ** (scale - total_scale)
    )


def aggregate_trade_minute_primitives(
    *,
    symbol: str,
    minute_start_time: datetime,
    events: Iterable[AggregateTrade],
) -> AggregateTradeMinutePrimitives:
    """Aggregate an already ordered canonical event sequence without floats."""

    require_v1_symbol(symbol)
    start = _utc_minute(minute_start_time, "minute_start_time")
    end = start + timedelta(minutes=1)
    try:
        selected = tuple(events)
    except TypeError as error:
        raise ValueError("events must be iterable") from error
    previous: AggregateTrade | None = None
    total_base = _ExactDecimalAccumulator()
    total_quote = _ExactDecimalAccumulator()
    buy_base = _ExactDecimalAccumulator()
    sell_base = _ExactDecimalAccumulator()
    buy_quote = _ExactDecimalAccumulator()
    sell_quote = _ExactDecimalAccumulator()
    buy_count = 0
    sell_count = 0
    seen_ids: set[int] = set()
    for event in selected:
        if type(event) is not AggregateTrade:
            raise TypeError("events must contain exact AggregateTrade values")
        AggregateTrade.__post_init__(event)
        if event.symbol != symbol or not start <= event.event_time < end:
            raise ValueError("event differs from requested symbol or minute")
        if event.aggregate_trade_id in seen_ids:
            raise ValueError("duplicate aggregate-trade ID in minute")
        if previous is not None and (
            event.event_time,
            event.aggregate_trade_id,
        ) <= (
            previous.event_time,
            previous.aggregate_trade_id,
        ):
            raise ValueError("minute events must retain strict canonical order")
        seen_ids.add(event.aggregate_trade_id)
        total_base.add(event.quantity)
        total_quote.add_product(event.price, event.quantity)
        if event.buyer_is_maker:
            sell_count += 1
            sell_base.add(event.quantity)
            sell_quote.add_product(event.price, event.quantity)
        else:
            buy_count += 1
            buy_base.add(event.quantity)
            buy_quote.add_product(event.price, event.quantity)
        previous = event
    first = selected[0] if selected else None
    last = selected[-1] if selected else None
    return AggregateTradeMinutePrimitives(
        symbol=symbol,
        minute_start_time=start,
        minute_end_time_exclusive=end,
        event_count=len(selected),
        aggressive_buy_event_count=buy_count,
        aggressive_sell_event_count=sell_count,
        total_base_quantity=total_base.value(),
        total_quote_notional=total_quote.value(),
        aggressive_buy_base_quantity=buy_base.value(),
        aggressive_sell_base_quantity=sell_base.value(),
        aggressive_buy_quote_notional=buy_quote.value(),
        aggressive_sell_quote_notional=sell_quote.value(),
        first_aggregate_trade_id=(
            None if first is None else first.aggregate_trade_id
        ),
        last_aggregate_trade_id=(None if last is None else last.aggregate_trade_id),
        first_event_time=None if first is None else first.event_time,
        last_event_time=None if last is None else last.event_time,
    )
