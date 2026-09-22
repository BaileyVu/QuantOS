"""Deterministic completed-minute state derived from validated aggregate trades."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json

from quantos.domain.common import require_v1_symbol
from quantos.domain.market_data.research_events.aggregate_trade import (
    ResearchEventValidationStatus,
    SourceTimestampUnit,
)
from quantos.domain.market_data.research_events.range_dataset import (
    AggregateTradePartitionReference,
)

AGGREGATE_TRADE_MINUTE_STATE_SCHEMA_VERSION = (
    "aggregate-trade-minute-state-v1"
)
AGGREGATE_TRADE_MINUTE_AGGREGATION_VERSION = (
    "aggregate-trade-minute-aggregation-v1"
)
AGGREGATE_TRADE_MINUTE_INTERVAL_SPECIFICATION = "PT1M:[start,end)"
AGGREGATE_TRADE_MINUTE_STATE_FAMILY = "aggregate_trade_minute_state"


class AggregateTradeMinuteCompletenessState(str, Enum):
    """Source-backed completeness eligible for historical research state."""

    VALIDATED_SOURCE_COMPLETE = "validated_source_complete"


class AggregateTradeMinuteAvailabilityState(str, Enum):
    """Historical occurrence does not establish live point-in-time availability."""

    HISTORICAL_ONLY_LIVE_UNPROVEN = "historical_only_live_unproven"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty canonical string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _digest(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError(f"{name} must be a built-in UTC datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    return value


def _minute_start(value: object, name: str) -> datetime:
    result = _utc(value, name)
    if result.second or result.microsecond:
        raise ValueError(f"{name} must be exactly minute-aligned")
    return result


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact integer")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")
    return value


def canonical_decimal_text(value: Decimal) -> str:
    """Return a representation-independent plain exact Decimal string."""

    _decimal(value, "decimal value")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def _decimal_component(value: Decimal) -> tuple[int, int]:
    sign, digits, exponent = value.as_tuple()
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    if sign:
        coefficient = -coefficient
    if exponent > 0:
        coefficient *= 10 ** exponent
        exponent = 0
    return coefficient, max(0, -exponent)


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


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _canonical_json_bytes(payload: dict[str, object]) -> bytes:
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


@dataclass(frozen=True, slots=True)
class AggregateTradeMinuteSourceReference:
    """One immutable daily constituent used by minute-state aggregation."""

    source_date: date
    manifest_id: str
    source_revision_id: str
    dataset_id: str
    canonical_sequence_sha256: str
    source_timestamp_unit: SourceTimestampUnit

    def __post_init__(self) -> None:
        if isinstance(self.source_date, datetime) or type(self.source_date) is not date:
            raise ValueError("source_date must be a built-in date")
        for name in (
            "manifest_id",
            "source_revision_id",
            "dataset_id",
            "canonical_sequence_sha256",
        ):
            _digest(getattr(self, name), name)
        if type(self.source_timestamp_unit) is not SourceTimestampUnit:
            raise TypeError("source_timestamp_unit must be explicit")

    @classmethod
    def from_partition_reference(
        cls, reference: AggregateTradePartitionReference
    ) -> AggregateTradeMinuteSourceReference:
        if type(reference) is not AggregateTradePartitionReference:
            raise TypeError("reference must be an AggregateTradePartitionReference")
        AggregateTradePartitionReference.__post_init__(reference)
        return cls(
            source_date=reference.logical_partition.source_date,
            manifest_id=reference.manifest_id,
            source_revision_id=reference.source_revision_id,
            dataset_id=reference.dataset_id,
            canonical_sequence_sha256=reference.canonical_sequence_sha256,
            source_timestamp_unit=reference.source_timestamp_unit,
        )

    def as_canonical_dict(self) -> dict[str, str]:
        return {
            "canonical_sequence_sha256": self.canonical_sequence_sha256,
            "dataset_id": self.dataset_id,
            "manifest_id": self.manifest_id,
            "source_date": self.source_date.isoformat(),
            "source_revision_id": self.source_revision_id,
            "source_timestamp_unit": self.source_timestamp_unit.value,
        }


@dataclass(frozen=True, slots=True)
class AggregateTradeMinuteState:
    """Neutral completed-minute aggregate-trade state for historical research."""

    symbol: str
    minute_start_time: datetime
    minute_end_time_exclusive: datetime
    source_range_id: str
    source_manifest_id: str
    source_revision_id: str
    source_dataset_id: str
    source_timestamp_unit: SourceTimestampUnit
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
    completeness_state: AggregateTradeMinuteCompletenessState
    availability_state: AggregateTradeMinuteAvailabilityState
    validation_status: ResearchEventValidationStatus

    def __post_init__(self) -> None:
        require_v1_symbol(self.symbol)
        start = _minute_start(self.minute_start_time, "minute_start_time")
        end = _utc(self.minute_end_time_exclusive, "minute_end_time_exclusive")
        if end - start != timedelta(minutes=1):
            raise ValueError("minute interval must be exactly [start, start + 1 minute)")
        for name in (
            "source_range_id",
            "source_manifest_id",
            "source_revision_id",
            "source_dataset_id",
        ):
            _digest(getattr(self, name), name)
        if type(self.source_timestamp_unit) is not SourceTimestampUnit:
            raise TypeError("source_timestamp_unit must be explicit")
        event_count = _count(self.event_count, "event_count")
        buy_count = _count(
            self.aggressive_buy_event_count, "aggressive_buy_event_count"
        )
        sell_count = _count(
            self.aggressive_sell_event_count, "aggressive_sell_event_count"
        )
        if buy_count + sell_count != event_count:
            raise ValueError("aggressor event counts must equal event_count")
        decimal_names = (
            "total_base_quantity",
            "total_quote_notional",
            "aggressive_buy_base_quantity",
            "aggressive_sell_base_quantity",
            "aggressive_buy_quote_notional",
            "aggressive_sell_quote_notional",
        )
        for name in decimal_names:
            _decimal(getattr(self, name), name)
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
        identifiers = (
            self.first_aggregate_trade_id,
            self.last_aggregate_trade_id,
        )
        event_times = (self.first_event_time, self.last_event_time)
        if event_count == 0:
            if any(value != Decimal(0) for value in (getattr(self, name) for name in decimal_names)):
                raise ValueError("zero-event state must have exact zero totals")
            if any(value is not None for value in identifiers + event_times):
                raise ValueError("zero-event state must have null event boundaries")
        else:
            if any(type(value) is not int or value < 0 for value in identifiers):
                raise ValueError("nonzero state requires non-negative event IDs")
            if any(value is None for value in event_times):
                raise ValueError("nonzero state requires event timestamps")
            first_time = _utc(self.first_event_time, "first_event_time")
            last_time = _utc(self.last_event_time, "last_event_time")
            if not start <= first_time <= last_time < end:
                raise ValueError("event boundaries must lie inside the minute")
            if (
                first_time,
                self.first_aggregate_trade_id,
            ) > (
                last_time,
                self.last_aggregate_trade_id,
            ):
                raise ValueError("first event boundary must not follow last boundary")
        if (
            self.completeness_state
            is not AggregateTradeMinuteCompletenessState.VALIDATED_SOURCE_COMPLETE
        ):
            raise ValueError("minute state must be backed by complete validated source")
        if (
            self.availability_state
            is not AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN
        ):
            raise ValueError("historical minute state cannot claim live availability")
        if self.validation_status is not ResearchEventValidationStatus.VALIDATED:
            raise ValueError("minute state must be validated")

    def as_canonical_dict(self) -> dict[str, object]:
        return {
            "aggressive_buy_base_quantity": canonical_decimal_text(
                self.aggressive_buy_base_quantity
            ),
            "aggressive_buy_event_count": self.aggressive_buy_event_count,
            "aggressive_buy_quote_notional": canonical_decimal_text(
                self.aggressive_buy_quote_notional
            ),
            "aggressive_sell_base_quantity": canonical_decimal_text(
                self.aggressive_sell_base_quantity
            ),
            "aggressive_sell_event_count": self.aggressive_sell_event_count,
            "aggressive_sell_quote_notional": canonical_decimal_text(
                self.aggressive_sell_quote_notional
            ),
            "availability_state": self.availability_state.value,
            "completeness_state": self.completeness_state.value,
            "event_count": self.event_count,
            "first_aggregate_trade_id": self.first_aggregate_trade_id,
            "first_event_time": (
                None if self.first_event_time is None else _utc_text(self.first_event_time)
            ),
            "last_aggregate_trade_id": self.last_aggregate_trade_id,
            "last_event_time": (
                None if self.last_event_time is None else _utc_text(self.last_event_time)
            ),
            "minute_end_time_exclusive": _utc_text(self.minute_end_time_exclusive),
            "minute_start_time": _utc_text(self.minute_start_time),
            "source_dataset_id": self.source_dataset_id,
            "source_manifest_id": self.source_manifest_id,
            "source_range_id": self.source_range_id,
            "source_revision_id": self.source_revision_id,
            "source_timestamp_unit": self.source_timestamp_unit.value,
            "symbol": self.symbol,
            "total_base_quantity": canonical_decimal_text(self.total_base_quantity),
            "total_quote_notional": canonical_decimal_text(self.total_quote_notional),
            "validation_status": self.validation_status.value,
        }


@dataclass(frozen=True, slots=True)
class AggregateTradeMinuteDatasetIdentity:
    """Immutable identity inputs for one completed-minute state dataset."""

    provider: str
    market: str
    source_event_family: str
    state_family: str
    symbol: str
    requested_start_time: datetime
    requested_end_time_exclusive: datetime
    source_range_id: str
    source_partitions: tuple[AggregateTradeMinuteSourceReference, ...]
    state_schema_version: str
    aggregation_version: str
    minute_interval_specification: str
    availability_state: AggregateTradeMinuteAvailabilityState
    validation_status: ResearchEventValidationStatus

    def __post_init__(self) -> None:
        for name in ("provider", "market", "source_event_family", "state_family"):
            _text(getattr(self, name), name)
        if self.state_family != AGGREGATE_TRADE_MINUTE_STATE_FAMILY:
            raise ValueError("unsupported completed-minute state family")
        require_v1_symbol(self.symbol)
        start = _minute_start(self.requested_start_time, "requested_start_time")
        end = _minute_start(
            self.requested_end_time_exclusive, "requested_end_time_exclusive"
        )
        if end <= start:
            raise ValueError("requested minute-state interval must be non-empty")
        if start.time() != datetime.min.time() or end.time() != datetime.min.time():
            raise ValueError("source-backed minute-state coverage must use UTC days")
        _digest(self.source_range_id, "source_range_id")
        if type(self.source_partitions) is not tuple or not self.source_partitions:
            raise ValueError("source_partitions must be a non-empty exact tuple")
        if any(
            type(item) is not AggregateTradeMinuteSourceReference
            for item in self.source_partitions
        ):
            raise TypeError("source_partitions must contain exact source references")
        for item in self.source_partitions:
            AggregateTradeMinuteSourceReference.__post_init__(item)
        required_dates = tuple(
            start.date() + timedelta(days=index)
            for index in range((end.date() - start.date()).days)
        )
        if tuple(item.source_date for item in self.source_partitions) != required_dates:
            raise ValueError("source partitions must cover every requested UTC day")
        if self.state_schema_version != AGGREGATE_TRADE_MINUTE_STATE_SCHEMA_VERSION:
            raise ValueError("unsupported minute-state schema version")
        _text(self.aggregation_version, "aggregation_version")
        if (
            self.minute_interval_specification
            != AGGREGATE_TRADE_MINUTE_INTERVAL_SPECIFICATION
        ):
            raise ValueError("unsupported minute interval specification")
        if (
            self.availability_state
            is not AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN
        ):
            raise ValueError("dataset cannot claim live availability")
        if self.validation_status is not ResearchEventValidationStatus.VALIDATED:
            raise ValueError("minute-state dataset identity must be validated")

    def as_canonical_dict(self) -> dict[str, object]:
        return {
            "aggregation_version": self.aggregation_version,
            "availability_state": self.availability_state.value,
            "market": self.market,
            "minute_interval_specification": self.minute_interval_specification,
            "provider": self.provider,
            "requested_end_time_exclusive": _utc_text(
                self.requested_end_time_exclusive
            ),
            "requested_start_time": _utc_text(self.requested_start_time),
            "source_event_family": self.source_event_family,
            "source_partitions": [
                item.as_canonical_dict() for item in self.source_partitions
            ],
            "source_range_id": self.source_range_id,
            "state_family": self.state_family,
            "state_schema_version": self.state_schema_version,
            "symbol": self.symbol,
            "validation_status": self.validation_status.value,
        }


def aggregate_trade_minute_dataset_id(
    identity: AggregateTradeMinuteDatasetIdentity,
    content_sha256: str,
) -> str:
    if type(identity) is not AggregateTradeMinuteDatasetIdentity:
        raise TypeError("identity must be an AggregateTradeMinuteDatasetIdentity")
    AggregateTradeMinuteDatasetIdentity.__post_init__(identity)
    _digest(content_sha256, "content_sha256")
    return sha256(
        _canonical_json_bytes(
            {
                "content_sha256": content_sha256,
                "identity": identity.as_canonical_dict(),
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidatedAggregateTradeMinuteDataset:
    """A complete consecutive UTC-minute grid bound to one exact source range."""

    identity: AggregateTradeMinuteDatasetIdentity
    states: tuple[AggregateTradeMinuteState, ...]

    def __post_init__(self) -> None:
        if type(self.identity) is not AggregateTradeMinuteDatasetIdentity:
            raise TypeError("identity must be an AggregateTradeMinuteDatasetIdentity")
        AggregateTradeMinuteDatasetIdentity.__post_init__(self.identity)
        if type(self.states) is not tuple or any(
            type(item) is not AggregateTradeMinuteState for item in self.states
        ):
            raise TypeError("states must be an exact tuple of minute states")
        expected_count = (
            self.identity.requested_end_time_exclusive
            - self.identity.requested_start_time
        ) // timedelta(minutes=1)
        if len(self.states) != expected_count:
            raise ValueError("minute-state sequence does not cover requested interval")
        references = {item.source_date: item for item in self.identity.source_partitions}
        for index, state in enumerate(self.states):
            AggregateTradeMinuteState.__post_init__(state)
            expected_start = self.identity.requested_start_time + timedelta(minutes=index)
            if state.minute_start_time != expected_start:
                raise ValueError("minute-state sequence contains a gap, duplicate, or reorder")
            if state.symbol != self.identity.symbol:
                raise ValueError("minute-state symbol differs from dataset identity")
            if state.source_range_id != self.identity.source_range_id:
                raise ValueError("minute-state source range differs from dataset identity")
            reference = references[state.minute_start_time.date()]
            if (
                state.source_manifest_id != reference.manifest_id
                or state.source_revision_id != reference.source_revision_id
                or state.source_dataset_id != reference.dataset_id
                or state.source_timestamp_unit is not reference.source_timestamp_unit
            ):
                raise ValueError("minute-state source lineage differs from partition")

    @property
    def content_sha256(self) -> str:
        return sha256(aggregate_trade_minute_state_content_bytes(self)).hexdigest()

    @property
    def dataset_id(self) -> str:
        return aggregate_trade_minute_dataset_id(
            self.identity, self.content_sha256
        )

    @property
    def input_event_count(self) -> int:
        return sum(item.event_count for item in self.states)

    @property
    def zero_event_minute_count(self) -> int:
        return sum(item.event_count == 0 for item in self.states)


def aggregate_trade_minute_state_content_bytes(
    dataset: ValidatedAggregateTradeMinuteDataset,
) -> bytes:
    if type(dataset) is not ValidatedAggregateTradeMinuteDataset:
        raise TypeError("dataset must be a ValidatedAggregateTradeMinuteDataset")
    ValidatedAggregateTradeMinuteDataset.__post_init__(dataset)
    return _canonical_json_bytes(
        {
            "state_schema_version": dataset.identity.state_schema_version,
            "states": [item.as_canonical_dict() for item in dataset.states],
        }
    )


@dataclass(frozen=True, slots=True)
class AggregateTradeMinuteDatasetManifest:
    """Compact immutable metadata for a published minute-state dataset."""

    identity: AggregateTradeMinuteDatasetIdentity
    content_sha256: str
    dataset_id: str
    state_count: int
    input_event_count: int
    zero_event_minute_count: int

    def __post_init__(self) -> None:
        if type(self.identity) is not AggregateTradeMinuteDatasetIdentity:
            raise TypeError("identity must be an AggregateTradeMinuteDatasetIdentity")
        AggregateTradeMinuteDatasetIdentity.__post_init__(self.identity)
        _digest(self.content_sha256, "content_sha256")
        _digest(self.dataset_id, "dataset_id")
        if self.dataset_id != aggregate_trade_minute_dataset_id(
            self.identity, self.content_sha256
        ):
            raise ValueError("minute-state dataset ID is inconsistent")
        expected_states = (
            self.identity.requested_end_time_exclusive
            - self.identity.requested_start_time
        ) // timedelta(minutes=1)
        if _count(self.state_count, "state_count") != expected_states:
            raise ValueError("manifest state count differs from requested interval")
        _count(self.input_event_count, "input_event_count")
        zero_count = _count(
            self.zero_event_minute_count, "zero_event_minute_count"
        )
        if zero_count > self.state_count:
            raise ValueError("zero-event minute count exceeds state count")

    @classmethod
    def from_dataset(
        cls, dataset: ValidatedAggregateTradeMinuteDataset
    ) -> AggregateTradeMinuteDatasetManifest:
        ValidatedAggregateTradeMinuteDataset.__post_init__(dataset)
        return cls(
            identity=dataset.identity,
            content_sha256=dataset.content_sha256,
            dataset_id=dataset.dataset_id,
            state_count=len(dataset.states),
            input_event_count=dataset.input_event_count,
            zero_event_minute_count=dataset.zero_event_minute_count,
        )

    def as_canonical_dict(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "dataset_id": self.dataset_id,
            "identity": self.identity.as_canonical_dict(),
            "input_event_count": self.input_event_count,
            "state_count": self.state_count,
            "zero_event_minute_count": self.zero_event_minute_count,
        }


def aggregate_trade_minute_dataset_manifest_bytes(
    manifest: AggregateTradeMinuteDatasetManifest,
) -> bytes:
    if type(manifest) is not AggregateTradeMinuteDatasetManifest:
        raise TypeError("manifest must be an AggregateTradeMinuteDatasetManifest")
    AggregateTradeMinuteDatasetManifest.__post_init__(manifest)
    return _canonical_json_bytes(manifest.as_canonical_dict())


def aggregate_trade_minute_dataset_manifest_from_bytes(
    payload: bytes,
) -> AggregateTradeMinuteDatasetManifest:
    """Parse only the exact canonical minute-state dataset manifest."""

    if type(payload) is not bytes:
        raise TypeError("manifest payload must be exact bytes")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("manifest JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_pairs)
        if type(decoded) is not dict or set(decoded) != {
            "content_sha256",
            "dataset_id",
            "identity",
            "input_event_count",
            "state_count",
            "zero_event_minute_count",
        }:
            raise ValueError("manifest fields do not match canonical contract")
        identity_value = decoded["identity"]
        if type(identity_value) is not dict or set(identity_value) != {
            "aggregation_version",
            "availability_state",
            "market",
            "minute_interval_specification",
            "provider",
            "requested_end_time_exclusive",
            "requested_start_time",
            "source_event_family",
            "source_partitions",
            "source_range_id",
            "state_family",
            "state_schema_version",
            "symbol",
            "validation_status",
        }:
            raise ValueError("identity fields do not match canonical contract")
        partition_values = identity_value["source_partitions"]
        if type(partition_values) is not list:
            raise TypeError("source_partitions must be an array")
        partitions = tuple(
            AggregateTradeMinuteSourceReference(
                source_date=date.fromisoformat(item["source_date"]),
                manifest_id=item["manifest_id"],
                source_revision_id=item["source_revision_id"],
                dataset_id=item["dataset_id"],
                canonical_sequence_sha256=item["canonical_sequence_sha256"],
                source_timestamp_unit=SourceTimestampUnit(
                    item["source_timestamp_unit"]
                ),
            )
            for item in partition_values
        )
        identity = AggregateTradeMinuteDatasetIdentity(
            provider=identity_value["provider"],
            market=identity_value["market"],
            source_event_family=identity_value["source_event_family"],
            state_family=identity_value["state_family"],
            symbol=identity_value["symbol"],
            requested_start_time=datetime.fromisoformat(
                identity_value["requested_start_time"].replace("Z", "+00:00")
            ),
            requested_end_time_exclusive=datetime.fromisoformat(
                identity_value["requested_end_time_exclusive"].replace(
                    "Z", "+00:00"
                )
            ),
            source_range_id=identity_value["source_range_id"],
            source_partitions=partitions,
            state_schema_version=identity_value["state_schema_version"],
            aggregation_version=identity_value["aggregation_version"],
            minute_interval_specification=identity_value[
                "minute_interval_specification"
            ],
            availability_state=AggregateTradeMinuteAvailabilityState(
                identity_value["availability_state"]
            ),
            validation_status=ResearchEventValidationStatus(
                identity_value["validation_status"]
            ),
        )
        manifest = AggregateTradeMinuteDatasetManifest(
            identity=identity,
            content_sha256=decoded["content_sha256"],
            dataset_id=decoded["dataset_id"],
            state_count=decoded["state_count"],
            input_event_count=decoded["input_event_count"],
            zero_event_minute_count=decoded["zero_event_minute_count"],
        )
    except (AttributeError, KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid minute-state manifest: {error}") from error
    if aggregate_trade_minute_dataset_manifest_bytes(manifest) != payload:
        raise ValueError("minute-state manifest is not canonically encoded")
    return manifest
