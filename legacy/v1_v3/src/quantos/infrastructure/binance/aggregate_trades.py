"""Pure Binance Spot aggregate-trade normalization and content identity."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import re

from quantos.domain.common import require_v1_symbol
from quantos.domain.market_data.research_events import (
    AggregateTrade,
    AggregateTradeContentIdentityError,
    SourceTimestampUnit,
    ValidatedAggregateTradeSequence,
    canonical_aggregate_trade_sequence_bytes as _canonical_sequence_bytes,
    canonical_aggregate_trade_sequence_sha256 as _canonical_sequence_sha256,
    normalize_source_timestamp,
)

AGGREGATE_TRADE_NORMALIZER_VERSION = (
    "binance-spot-aggregate-trade-normalizer-v1"
)
ARCHIVE_MICROSECOND_ERA_START = date(2025, 1, 1)
ARCHIVE_AGGREGATE_TRADE_COLUMNS = (
    "aggregate_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "timestamp",
    "buyer_is_maker",
    "best_price_match",
)
REST_AGGREGATE_TRADE_FIELDS = frozenset({"a", "p", "q", "f", "l", "T", "m", "M"})
WEBSOCKET_AGGREGATE_TRADE_FIELDS = frozenset(
    {"e", "E", "s", "a", "p", "q", "f", "l", "T", "m", "M"}
)

_UNSIGNED_INTEGER_TOKEN = re.compile(r"(?:0|[1-9][0-9]*)")
_DECIMAL_TOKEN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


class BinanceAggregateTradeNormalizationError(ValueError):
    """A provider fixture cannot be mapped to the canonical contract."""


class BinanceAggregateTradeSource(str, Enum):
    """Supported static provider representation."""

    ARCHIVE = "archive"
    REST = "rest"
    WEBSOCKET = "websocket"


def _symbol(value: object) -> str:
    if type(value) is not str:
        raise BinanceAggregateTradeNormalizationError(
            "symbol must be an exact built-in string"
        )
    try:
        return require_v1_symbol(value)
    except ValueError as error:
        raise BinanceAggregateTradeNormalizationError(str(error)) from error


def _timestamp_unit(value: object) -> SourceTimestampUnit:
    if type(value) is not SourceTimestampUnit:
        raise BinanceAggregateTradeNormalizationError(
            "source_timestamp_unit must be explicitly declared"
        )
    return value


def _unsigned_integer_token(value: object, field_name: str) -> int:
    if type(value) is not str or _UNSIGNED_INTEGER_TOKEN.fullmatch(value) is None:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} must be a canonical unsigned-integer token"
        )
    try:
        return int(value)
    except ValueError as error:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} is not representable as an integer"
        ) from error


def _integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} must be an exact integer, not bool"
        )
    if value < 0:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} must be non-negative"
        )
    return value


def _decimal_token(value: object, field_name: str) -> Decimal:
    if type(value) is not str or _DECIMAL_TOKEN.fullmatch(value) is None:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} must be a canonical non-negative decimal token"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} is not a valid decimal token"
        ) from error
    if not parsed.is_finite():
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} must be finite"
        )
    return parsed


def _boolean(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} must be an exact boolean"
        )
    return value


def _archive_boolean(value: object, field_name: str) -> bool:
    if type(value) is not str or value not in ("True", "False"):
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} must be exactly 'True' or 'False'"
        )
    return value == "True"


def _payload(payload: object, expected_fields: frozenset[str], source: str) -> dict:
    if type(payload) is not dict:
        raise BinanceAggregateTradeNormalizationError(
            f"{source} payload must be an exact JSON object"
        )
    if set(payload) != expected_fields:
        raise BinanceAggregateTradeNormalizationError(
            f"{source} payload must contain exactly the documented fields"
        )
    return payload


def _normalized_timestamp(
    value: int, unit: SourceTimestampUnit, field_name: str
) -> datetime:
    try:
        return normalize_source_timestamp(value, unit)
    except (OverflowError, TypeError, ValueError) as error:
        raise BinanceAggregateTradeNormalizationError(
            f"{field_name} is not a valid {unit.value} Unix timestamp"
        ) from error


def _canonical_trade(
    *,
    symbol: str,
    aggregate_trade_id: int,
    price: Decimal,
    quantity: Decimal,
    first_trade_id: int,
    last_trade_id: int,
    source_timestamp: int,
    source_timestamp_unit: SourceTimestampUnit,
    buyer_is_maker: bool,
    best_price_match: bool | None,
) -> AggregateTrade:
    event_time = _normalized_timestamp(
        source_timestamp, source_timestamp_unit, "trade timestamp"
    )
    try:
        return AggregateTrade(
            symbol=symbol,
            aggregate_trade_id=aggregate_trade_id,
            price=price,
            quantity=quantity,
            first_trade_id=first_trade_id,
            last_trade_id=last_trade_id,
            event_time=event_time,
            source_timestamp=source_timestamp,
            source_timestamp_unit=source_timestamp_unit,
            buyer_is_maker=buyer_is_maker,
            best_price_match=best_price_match,
        )
    except (AttributeError, OverflowError, TypeError, ValueError) as error:
        raise BinanceAggregateTradeNormalizationError(
            f"provider aggregate trade violates the canonical contract: {error}"
        ) from error


def archive_timestamp_unit(archive_date: date) -> SourceTimestampUnit:
    """Return the documented Spot archive unit selected only from source date."""

    if isinstance(archive_date, datetime) or type(archive_date) is not date:
        raise BinanceAggregateTradeNormalizationError(
            "archive_date must be an exact date, not a datetime"
        )
    if archive_date < ARCHIVE_MICROSECOND_ERA_START:
        return SourceTimestampUnit.MILLISECOND
    return SourceTimestampUnit.MICROSECOND


def normalize_archive_aggregate_trade(
    row: tuple[str, ...] | list[str],
    *,
    symbol: str,
    archive_date: date,
) -> AggregateTrade:
    """Normalize one headerless eight-column public archive row."""

    if type(row) not in (tuple, list):
        raise BinanceAggregateTradeNormalizationError(
            "archive row must be an exact tuple or list"
        )
    if len(row) != len(ARCHIVE_AGGREGATE_TRADE_COLUMNS):
        raise BinanceAggregateTradeNormalizationError(
            "archive row must contain exactly eight fields"
        )
    if any(type(value) is not str for value in row):
        raise BinanceAggregateTradeNormalizationError(
            "archive row fields must be exact strings"
        )
    unit = archive_timestamp_unit(archive_date)
    raw_timestamp = _unsigned_integer_token(row[5], "timestamp")
    normalized_time = _normalized_timestamp(raw_timestamp, unit, "timestamp")
    if normalized_time.date() != archive_date:
        raise BinanceAggregateTradeNormalizationError(
            "timestamp is inconsistent with archive_date and documented unit"
        )
    return _canonical_trade(
        symbol=_symbol(symbol),
        aggregate_trade_id=_unsigned_integer_token(row[0], "aggregate trade ID"),
        price=_decimal_token(row[1], "price"),
        quantity=_decimal_token(row[2], "quantity"),
        first_trade_id=_unsigned_integer_token(row[3], "first trade ID"),
        last_trade_id=_unsigned_integer_token(row[4], "last trade ID"),
        source_timestamp=raw_timestamp,
        source_timestamp_unit=unit,
        buyer_is_maker=_archive_boolean(row[6], "buyer-is-maker"),
        best_price_match=_archive_boolean(row[7], "best-price-match"),
    )


def normalize_rest_aggregate_trade(
    payload: dict[str, object],
    *,
    symbol: str,
    source_timestamp_unit: SourceTimestampUnit,
) -> AggregateTrade:
    """Normalize one documented ``GET /api/v3/aggTrades`` result object."""

    value = _payload(payload, REST_AGGREGATE_TRADE_FIELDS, "REST")
    unit = _timestamp_unit(source_timestamp_unit)
    return _canonical_trade(
        symbol=_symbol(symbol),
        aggregate_trade_id=_integer(value["a"], "a"),
        price=_decimal_token(value["p"], "p"),
        quantity=_decimal_token(value["q"], "q"),
        first_trade_id=_integer(value["f"], "f"),
        last_trade_id=_integer(value["l"], "l"),
        source_timestamp=_integer(value["T"], "T"),
        source_timestamp_unit=unit,
        buyer_is_maker=_boolean(value["m"], "m"),
        best_price_match=_boolean(value["M"], "M"),
    )


def normalize_websocket_aggregate_trade(
    payload: dict[str, object],
    *,
    source_timestamp_unit: SourceTimestampUnit,
) -> AggregateTrade:
    """Normalize one aggregate-trade stream payload using trade time ``T``.

    Provider event time ``E`` is validated under the declared connection unit
    but is not substituted for trade occurrence time or persisted as an
    availability timestamp.  WebSocket field ``M`` is documented as ignored,
    so it is validated but never mapped to canonical best-price-match.
    """

    value = _payload(payload, WEBSOCKET_AGGREGATE_TRADE_FIELDS, "WebSocket")
    unit = _timestamp_unit(source_timestamp_unit)
    if type(value["e"]) is not str or value["e"] != "aggTrade":
        raise BinanceAggregateTradeNormalizationError(
            "WebSocket e must be exactly 'aggTrade'"
        )
    provider_event_timestamp = _integer(value["E"], "E")
    _normalized_timestamp(provider_event_timestamp, unit, "provider event timestamp")
    _boolean(value["M"], "M")
    return _canonical_trade(
        symbol=_symbol(value["s"]),
        aggregate_trade_id=_integer(value["a"], "a"),
        price=_decimal_token(value["p"], "p"),
        quantity=_decimal_token(value["q"], "q"),
        first_trade_id=_integer(value["f"], "f"),
        last_trade_id=_integer(value["l"], "l"),
        source_timestamp=_integer(value["T"], "T"),
        source_timestamp_unit=unit,
        buyer_is_maker=_boolean(value["m"], "m"),
        best_price_match=None,
    )


def raw_content_sha256(content: bytes) -> str:
    """Hash raw provider bytes without decoding or canonicalizing them."""

    if type(content) is not bytes:
        raise TypeError("raw content must be exact bytes")
    return sha256(content).hexdigest()


def canonical_aggregate_trade_sequence_bytes(
    sequence: ValidatedAggregateTradeSequence,
) -> bytes:
    """Return domain-owned canonical bytes through the provider boundary."""

    try:
        return _canonical_sequence_bytes(sequence)
    except AggregateTradeContentIdentityError as error:
        raise BinanceAggregateTradeNormalizationError(
            str(error)
        ) from error


def canonical_aggregate_trade_sequence_sha256(
    sequence: ValidatedAggregateTradeSequence,
) -> str:
    """Hash deterministic validated canonical sequence bytes."""

    try:
        return _canonical_sequence_sha256(sequence)
    except AggregateTradeContentIdentityError as error:
        raise BinanceAggregateTradeNormalizationError(str(error)) from error
