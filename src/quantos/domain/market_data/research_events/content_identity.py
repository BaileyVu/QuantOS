"""Deterministic content identity for validated research-event sequences."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

from quantos.domain.market_data.research_events.validation import (
    ValidatedAggregateTradeSequence,
)


class AggregateTradeContentIdentityError(ValueError):
    """A sequence cannot produce trusted canonical content bytes."""


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _decimal_identity(value: Decimal) -> dict[str, int | str]:
    decimal_tuple = value.as_tuple()
    return {
        "digits": "".join(str(digit) for digit in decimal_tuple.digits),
        "exponent": decimal_tuple.exponent,
        "sign": decimal_tuple.sign,
    }


def canonical_aggregate_trade_sequence_bytes(
    sequence: ValidatedAggregateTradeSequence,
) -> bytes:
    """Serialize a revalidated sequence, including its normalizer identity."""

    if type(sequence) is not ValidatedAggregateTradeSequence:
        raise TypeError("sequence must be a ValidatedAggregateTradeSequence")
    try:
        checked = ValidatedAggregateTradeSequence(sequence.identity, sequence.events)
    except (AttributeError, OverflowError, TypeError, ValueError) as error:
        raise AggregateTradeContentIdentityError(
            f"invalid canonical aggregate-trade sequence: {error}"
        ) from error
    payload = {
        "dataset_identity": checked.identity.as_canonical_dict(),
        "events": [
            {
                "aggregate_trade_id": event.aggregate_trade_id,
                "best_price_match": event.best_price_match,
                "buyer_is_maker": event.buyer_is_maker,
                "event_time": _utc_text(event.event_time),
                "first_trade_id": event.first_trade_id,
                "last_trade_id": event.last_trade_id,
                "price": _decimal_identity(event.price),
                "quantity": _decimal_identity(event.quantity),
                "source_timestamp": event.source_timestamp,
                "source_timestamp_unit": event.source_timestamp_unit.value,
                "symbol": event.symbol,
            }
            for event in checked.events
        ],
    }
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_aggregate_trade_sequence_sha256(
    sequence: ValidatedAggregateTradeSequence,
) -> str:
    """Hash deterministic validated canonical sequence bytes."""

    return sha256(canonical_aggregate_trade_sequence_bytes(sequence)).hexdigest()
