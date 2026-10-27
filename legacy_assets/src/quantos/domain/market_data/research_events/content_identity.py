"""Deterministic content identity for validated research-event sequences."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

from quantos.domain.market_data.research_events.validation import (
    ValidatedAggregateTradeSequence,
)
from quantos.domain.market_data.research_events.aggregate_trade import (
    AggregateTrade,
    AggregateTradeDatasetIdentity,
    ResearchEventValidationStatus,
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


def _canonical_json_fragment(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_aggregate_trade_event_bytes(event: AggregateTrade) -> bytes:
    """Serialize one already canonical event as its sequence JSON element."""

    if type(event) is not AggregateTrade:
        raise TypeError("event must be an AggregateTrade")
    AggregateTrade.__post_init__(event)
    return _canonical_json_fragment(
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
    )


class IncrementalAggregateTradeSequenceHasher:
    """Produce the existing canonical sequence digest without retaining events."""

    __slots__ = ("_count", "_digest", "_finished")

    def __init__(self, identity: AggregateTradeDatasetIdentity) -> None:
        if type(identity) is not AggregateTradeDatasetIdentity:
            raise TypeError("identity must be an AggregateTradeDatasetIdentity")
        AggregateTradeDatasetIdentity.__post_init__(identity)
        if identity.validation_status is not ResearchEventValidationStatus.VALIDATED:
            raise ValueError("canonical sequence hashing requires validated identity")
        self._digest = sha256()
        self._digest.update(b'{"dataset_identity":')
        self._digest.update(_canonical_json_fragment(identity.as_canonical_dict()))
        self._digest.update(b',"events":[')
        self._count = 0
        self._finished = False

    @property
    def event_count(self) -> int:
        return self._count

    def update(
        self,
        event: AggregateTrade,
        *,
        canonical_event_bytes: bytes | None = None,
    ) -> bytes:
        if self._finished:
            raise AggregateTradeContentIdentityError(
                "canonical sequence hasher is already finalized"
            )
        encoded = (
            canonical_aggregate_trade_event_bytes(event)
            if canonical_event_bytes is None
            else canonical_event_bytes
        )
        if type(encoded) is not bytes:
            raise TypeError("canonical_event_bytes must be exact bytes")
        if self._count:
            self._digest.update(b",")
        self._digest.update(encoded)
        self._count += 1
        return encoded

    def hexdigest(self) -> str:
        if self._finished:
            raise AggregateTradeContentIdentityError(
                "canonical sequence hasher is already finalized"
            )
        digest = self._digest.copy()
        digest.update(b"]}\n")
        self._finished = True
        return digest.hexdigest()


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
    output = bytearray(b'{"dataset_identity":')
    output.extend(_canonical_json_fragment(checked.identity.as_canonical_dict()))
    output.extend(b',"events":[')
    for index, event in enumerate(checked.events):
        if index:
            output.extend(b",")
        output.extend(canonical_aggregate_trade_event_bytes(event))
    output.extend(b"]}\n")
    return bytes(output)


def canonical_aggregate_trade_sequence_sha256(
    sequence: ValidatedAggregateTradeSequence,
) -> str:
    """Hash deterministic validated canonical sequence bytes."""

    return sha256(canonical_aggregate_trade_sequence_bytes(sequence)).hexdigest()
