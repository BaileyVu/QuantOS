"""Deterministic contracts for composing immutable daily event archives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json

from quantos.domain.common import require_v1_symbol
from quantos.domain.market_data.research_events.aggregate_trade import (
    ResearchEventValidationStatus,
    SourceTimestampUnit,
)
from quantos.domain.market_data.research_events.archive_dataset import (
    AggregateTradeArchiveManifest,
    aggregate_trade_archive_manifest_id,
)

AGGREGATE_TRADE_RANGE_SCHEMA_VERSION = "aggregate-trade-range-v1"
AGGREGATE_TRADE_REVISION_SELECTION_VERSION = (
    "aggregate-trade-revision-selection-v1"
)


class RevisionSelectionPolicy(str, Enum):
    """Approved deterministic archive-revision selection policies."""

    EXACT = "exact"
    UNIQUE = "unique"


class RangeCompletenessState(str, Enum):
    """Completeness state eligible for research access."""

    COMPLETE = "complete"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty canonical string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _digest(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _built_in_date(value: object, name: str) -> date:
    if isinstance(value, datetime) or type(value) is not date:
        raise ValueError(f"{name} must be a built-in date")
    return value


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError(f"{name} must be a built-in UTC datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _non_negative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact integer")
    return value


@dataclass(frozen=True, slots=True, order=True)
class AggregateTradeLogicalPartitionKey:
    """Provider-independent identity of one UTC daily logical partition."""

    provider: str
    market: str
    event_family: str
    symbol: str
    source_date: date

    def __post_init__(self) -> None:
        _text(self.provider, "provider")
        _text(self.market, "market")
        _text(self.event_family, "event_family")
        require_v1_symbol(self.symbol)
        _built_in_date(self.source_date, "source_date")

    @classmethod
    def from_manifest(
        cls, manifest: AggregateTradeArchiveManifest
    ) -> AggregateTradeLogicalPartitionKey:
        if type(manifest) is not AggregateTradeArchiveManifest:
            raise TypeError("manifest must be an AggregateTradeArchiveManifest")
        AggregateTradeArchiveManifest.__post_init__(manifest)
        return cls(
            provider=manifest.provider,
            market=manifest.market,
            event_family=manifest.source_family,
            symbol=manifest.symbol,
            source_date=manifest.source_date,
        )

    def as_canonical_dict(self) -> dict[str, str]:
        return {
            "event_family": self.event_family,
            "market": self.market,
            "provider": self.provider,
            "source_date": self.source_date.isoformat(),
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class ExactAggregateTradeRevision:
    """Exact immutable revision requested for one source date."""

    source_date: date
    manifest_id: str
    source_revision_id: str

    def __post_init__(self) -> None:
        _built_in_date(self.source_date, "source_date")
        _digest(self.manifest_id, "manifest_id")
        _digest(self.source_revision_id, "source_revision_id")


@dataclass(frozen=True, slots=True)
class AggregateTradeRangeRequest:
    """One research-only UTC date range and its revision-selection rule."""

    symbol: str
    start_date: date
    end_date_exclusive: date
    selection_policy: RevisionSelectionPolicy
    exact_revisions: tuple[ExactAggregateTradeRevision, ...] = ()

    def __post_init__(self) -> None:
        require_v1_symbol(self.symbol)
        start = _built_in_date(self.start_date, "start_date")
        end = _built_in_date(self.end_date_exclusive, "end_date_exclusive")
        if end <= start:
            raise ValueError("end_date_exclusive must be after start_date")
        if type(self.selection_policy) is not RevisionSelectionPolicy:
            raise TypeError("selection_policy must be explicit")
        if type(self.exact_revisions) is not tuple or any(
            type(item) is not ExactAggregateTradeRevision
            for item in self.exact_revisions
        ):
            raise TypeError("exact_revisions must be an exact tuple")
        required_dates = tuple(
            start + timedelta(days=index) for index in range((end - start).days)
        )
        selected_dates = tuple(item.source_date for item in self.exact_revisions)
        if self.selection_policy is RevisionSelectionPolicy.UNIQUE:
            if self.exact_revisions:
                raise ValueError("UNIQUE selection must not include exact revisions")
        elif selected_dates != required_dates:
            raise ValueError(
                "EXACT selection must provide one ordered revision for every date"
            )


@dataclass(frozen=True, slots=True)
class AggregateTradePartitionReference:
    """Immutable constituent reference copied from authoritative manifest data."""

    logical_partition: AggregateTradeLogicalPartitionKey
    manifest_id: str
    source_revision_id: str
    dataset_id: str
    canonical_sequence_sha256: str
    raw_zip_sha256: str
    source_timestamp_unit: SourceTimestampUnit
    schema_version: str
    normalizer_version: str
    accepted_row_count: int
    observed_first_event_time: datetime
    observed_last_event_time: datetime
    validation_status: ResearchEventValidationStatus

    def __post_init__(self) -> None:
        if type(self.logical_partition) is not AggregateTradeLogicalPartitionKey:
            raise TypeError("logical_partition must be explicit")
        AggregateTradeLogicalPartitionKey.__post_init__(self.logical_partition)
        for name in (
            "manifest_id",
            "source_revision_id",
            "dataset_id",
            "canonical_sequence_sha256",
            "raw_zip_sha256",
        ):
            _digest(getattr(self, name), name)
        if type(self.source_timestamp_unit) is not SourceTimestampUnit:
            raise TypeError("source_timestamp_unit must be explicit")
        _text(self.schema_version, "schema_version")
        _text(self.normalizer_version, "normalizer_version")
        if _non_negative_int(self.accepted_row_count, "accepted_row_count") == 0:
            raise ValueError("accepted_row_count must be positive")
        first = _utc(
            self.observed_first_event_time, "observed_first_event_time"
        )
        last = _utc(self.observed_last_event_time, "observed_last_event_time")
        if first > last:
            raise ValueError("observed event coverage is reversed")
        if self.validation_status is not ResearchEventValidationStatus.VALIDATED:
            raise ValueError("partition reference must be validated")

    @classmethod
    def from_manifest(
        cls, manifest: AggregateTradeArchiveManifest
    ) -> AggregateTradePartitionReference:
        AggregateTradeArchiveManifest.__post_init__(manifest)
        return cls(
            logical_partition=AggregateTradeLogicalPartitionKey.from_manifest(
                manifest
            ),
            manifest_id=aggregate_trade_archive_manifest_id(manifest),
            source_revision_id=manifest.source_revision_id,
            dataset_id=manifest.dataset_id,
            canonical_sequence_sha256=manifest.canonical_sequence_sha256,
            raw_zip_sha256=manifest.raw_zip_sha256,
            source_timestamp_unit=manifest.source_timestamp_unit,
            schema_version=manifest.schema_version,
            normalizer_version=manifest.normalizer_version,
            accepted_row_count=manifest.accepted_row_count,
            observed_first_event_time=manifest.observed_first_event_time,
            observed_last_event_time=manifest.observed_last_event_time,
            validation_status=manifest.validation_status,
        )

    def as_canonical_dict(self) -> dict[str, object]:
        return {
            "accepted_row_count": self.accepted_row_count,
            "canonical_sequence_sha256": self.canonical_sequence_sha256,
            "dataset_id": self.dataset_id,
            "logical_partition": self.logical_partition.as_canonical_dict(),
            "manifest_id": self.manifest_id,
            "normalizer_version": self.normalizer_version,
            "observed_first_event_time": _utc_text(
                self.observed_first_event_time
            ),
            "observed_last_event_time": _utc_text(
                self.observed_last_event_time
            ),
            "raw_zip_sha256": self.raw_zip_sha256,
            "schema_version": self.schema_version,
            "source_revision_id": self.source_revision_id,
            "source_timestamp_unit": self.source_timestamp_unit.value,
            "validation_status": self.validation_status.value,
        }


@dataclass(frozen=True, slots=True)
class AggregateTradeBoundaryValidation:
    """Observed integrity evidence at one adjacent daily boundary."""

    left_source_date: date
    right_source_date: date
    left_last_event_time: datetime
    right_first_event_time: datetime
    left_last_aggregate_trade_id: int
    right_first_aggregate_trade_id: int
    chronological_order_valid: bool
    duplicate_aggregate_id_count: int
    conflicting_aggregate_id_count: int
    overlapping_event_identity_count: int
    observed_aggregate_id_delta: int
    numerical_continuity_asserted: bool = False

    def __post_init__(self) -> None:
        left = _built_in_date(self.left_source_date, "left_source_date")
        right = _built_in_date(self.right_source_date, "right_source_date")
        if right - left != timedelta(days=1):
            raise ValueError("boundary dates must be adjacent and ordered")
        left_time = _utc(self.left_last_event_time, "left_last_event_time")
        right_time = _utc(self.right_first_event_time, "right_first_event_time")
        for name in (
            "left_last_aggregate_trade_id",
            "right_first_aggregate_trade_id",
        ):
            _non_negative_int(getattr(self, name), name)
        if type(self.chronological_order_valid) is not bool:
            raise TypeError("chronological_order_valid must be an exact bool")
        if self.chronological_order_valid != (
            (left_time, self.left_last_aggregate_trade_id)
            < (right_time, self.right_first_aggregate_trade_id)
        ):
            raise ValueError("boundary chronological status is inconsistent")
        for name in (
            "duplicate_aggregate_id_count",
            "conflicting_aggregate_id_count",
            "overlapping_event_identity_count",
        ):
            _non_negative_int(getattr(self, name), name)
        if type(self.observed_aggregate_id_delta) is not int:
            raise TypeError("observed_aggregate_id_delta must be an exact integer")
        if self.observed_aggregate_id_delta != (
            self.right_first_aggregate_trade_id
            - self.left_last_aggregate_trade_id
        ):
            raise ValueError("observed aggregate-ID delta is inconsistent")
        if type(self.numerical_continuity_asserted) is not bool:
            raise TypeError("numerical_continuity_asserted must be an exact bool")
        if self.numerical_continuity_asserted:
            raise ValueError("numerical aggregate-ID continuity must not be asserted")
        if (
            not self.chronological_order_valid
            or self.duplicate_aggregate_id_count
            or self.conflicting_aggregate_id_count
            or self.overlapping_event_identity_count
        ):
            raise ValueError("validated range boundary contains an integrity failure")

    def as_canonical_dict(self) -> dict[str, object]:
        return {
            "chronological_order_valid": self.chronological_order_valid,
            "conflicting_aggregate_id_count": self.conflicting_aggregate_id_count,
            "duplicate_aggregate_id_count": self.duplicate_aggregate_id_count,
            "left_last_aggregate_trade_id": self.left_last_aggregate_trade_id,
            "left_last_event_time": _utc_text(self.left_last_event_time),
            "left_source_date": self.left_source_date.isoformat(),
            "numerical_continuity_asserted": self.numerical_continuity_asserted,
            "observed_aggregate_id_delta": self.observed_aggregate_id_delta,
            "overlapping_event_identity_count": (
                self.overlapping_event_identity_count
            ),
            "right_first_aggregate_trade_id": (
                self.right_first_aggregate_trade_id
            ),
            "right_first_event_time": _utc_text(self.right_first_event_time),
            "right_source_date": self.right_source_date.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class AggregateTradeRangeManifest:
    """Validated logical range bound to exact immutable daily constituents."""

    symbol: str
    requested_start_date: date
    requested_end_date_exclusive: date
    selection_policy: RevisionSelectionPolicy
    selection_policy_version: str
    range_schema_version: str
    expected_partition_count: int
    selected_partition_count: int
    partitions: tuple[AggregateTradePartitionReference, ...]
    total_accepted_event_count: int
    observed_first_event_time: datetime
    observed_last_event_time: datetime
    boundaries: tuple[AggregateTradeBoundaryValidation, ...]
    completeness_state: RangeCompletenessState
    validation_status: ResearchEventValidationStatus

    def __post_init__(self) -> None:
        require_v1_symbol(self.symbol)
        start = _built_in_date(
            self.requested_start_date, "requested_start_date"
        )
        end = _built_in_date(
            self.requested_end_date_exclusive,
            "requested_end_date_exclusive",
        )
        if end <= start:
            raise ValueError("requested range must be non-empty and ordered")
        if type(self.selection_policy) is not RevisionSelectionPolicy:
            raise TypeError("selection_policy must be explicit")
        if (
            self.selection_policy_version
            != AGGREGATE_TRADE_REVISION_SELECTION_VERSION
        ):
            raise ValueError("unsupported revision-selection version")
        if self.range_schema_version != AGGREGATE_TRADE_RANGE_SCHEMA_VERSION:
            raise ValueError("unsupported aggregate-trade range schema")
        expected = (end - start).days
        if self.expected_partition_count != expected:
            raise ValueError("expected partition count does not match request")
        if self.selected_partition_count != expected:
            raise ValueError("selected partition count does not prove completeness")
        if type(self.partitions) is not tuple or len(self.partitions) != expected:
            raise ValueError("ordered partition references are incomplete")
        required_dates = tuple(
            start + timedelta(days=index) for index in range(expected)
        )
        actual_dates = tuple(
            item.logical_partition.source_date for item in self.partitions
        )
        if actual_dates != required_dates:
            raise ValueError("partition references are not in exact UTC date order")
        if any(
            type(item) is not AggregateTradePartitionReference
            for item in self.partitions
        ):
            raise TypeError("partitions must contain exact partition references")
        for item in self.partitions:
            AggregateTradePartitionReference.__post_init__(item)
            if item.logical_partition.symbol != self.symbol:
                raise ValueError("partition symbol differs from range symbol")
        scopes = {
            (
                item.logical_partition.provider,
                item.logical_partition.market,
                item.logical_partition.event_family,
            )
            for item in self.partitions
        }
        if len(scopes) != 1:
            raise ValueError("range partitions have conflicting source scope")
        if self.total_accepted_event_count != sum(
            item.accepted_row_count for item in self.partitions
        ):
            raise ValueError("range total does not equal constituent row counts")
        if self.observed_first_event_time != self.partitions[0].observed_first_event_time:
            raise ValueError("range first observed event differs from first partition")
        if self.observed_last_event_time != self.partitions[-1].observed_last_event_time:
            raise ValueError("range last observed event differs from last partition")
        _utc(self.observed_first_event_time, "observed_first_event_time")
        _utc(self.observed_last_event_time, "observed_last_event_time")
        if type(self.boundaries) is not tuple or len(self.boundaries) != expected - 1:
            raise ValueError("range boundary evidence is incomplete")
        for index, boundary in enumerate(self.boundaries):
            if type(boundary) is not AggregateTradeBoundaryValidation:
                raise TypeError("boundaries must contain exact boundary values")
            AggregateTradeBoundaryValidation.__post_init__(boundary)
            if (
                boundary.left_source_date != required_dates[index]
                or boundary.right_source_date != required_dates[index + 1]
            ):
                raise ValueError("boundary evidence does not match partition order")
        if self.completeness_state is not RangeCompletenessState.COMPLETE:
            raise ValueError("range manifest must be complete")
        if self.validation_status is not ResearchEventValidationStatus.VALIDATED:
            raise ValueError("range manifest must be validated")

    @property
    def range_id(self) -> str:
        return sha256(_canonical_json_bytes(self._identity_dict())).hexdigest()

    def _identity_dict(self) -> dict[str, object]:
        first = self.partitions[0].logical_partition
        return {
            "boundaries": [
                item.as_canonical_dict() for item in self.boundaries
            ],
            "completeness_state": self.completeness_state.value,
            "event_family": first.event_family,
            "expected_partition_count": self.expected_partition_count,
            "market": first.market,
            "observed_first_event_time": _utc_text(
                self.observed_first_event_time
            ),
            "observed_last_event_time": _utc_text(
                self.observed_last_event_time
            ),
            "partitions": [
                item.as_canonical_dict() for item in self.partitions
            ],
            "provider": first.provider,
            "range_schema_version": self.range_schema_version,
            "requested_end_date_exclusive": (
                self.requested_end_date_exclusive.isoformat()
            ),
            "requested_start_date": self.requested_start_date.isoformat(),
            "selected_partition_count": self.selected_partition_count,
            "selection_policy": self.selection_policy.value,
            "selection_policy_version": self.selection_policy_version,
            "symbol": self.symbol,
            "total_accepted_event_count": self.total_accepted_event_count,
            "validation_status": self.validation_status.value,
        }

    def as_canonical_dict(self) -> dict[str, object]:
        result = self._identity_dict()
        result["range_id"] = self.range_id
        return result


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


def aggregate_trade_range_manifest_bytes(
    manifest: AggregateTradeRangeManifest,
) -> bytes:
    if type(manifest) is not AggregateTradeRangeManifest:
        raise TypeError("manifest must be an AggregateTradeRangeManifest")
    AggregateTradeRangeManifest.__post_init__(manifest)
    return _canonical_json_bytes(manifest.as_canonical_dict())


def aggregate_trade_range_manifest_from_bytes(
    payload: bytes,
) -> AggregateTradeRangeManifest:
    """Parse only the exact canonical range-manifest representation."""

    if type(payload) is not bytes:
        raise TypeError("range manifest payload must be exact bytes")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("range manifest JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_pairs
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("range manifest is not valid canonical JSON") from error
    if type(decoded) is not dict:
        raise ValueError("range manifest JSON must be an object")
    recorded_range_id = decoded.pop("range_id", None)
    try:
        partition_values = decoded.pop("partitions")
        boundary_values = decoded.pop("boundaries")
        provider = decoded.pop("provider")
        market = decoded.pop("market")
        event_family = decoded.pop("event_family")
        if type(partition_values) is not list or type(boundary_values) is not list:
            raise TypeError("range manifest collections must be arrays")
        partitions = tuple(
            AggregateTradePartitionReference(
                logical_partition=AggregateTradeLogicalPartitionKey(
                    provider=item["logical_partition"]["provider"],
                    market=item["logical_partition"]["market"],
                    event_family=item["logical_partition"]["event_family"],
                    symbol=item["logical_partition"]["symbol"],
                    source_date=date.fromisoformat(
                        item["logical_partition"]["source_date"]
                    ),
                ),
                manifest_id=item["manifest_id"],
                source_revision_id=item["source_revision_id"],
                dataset_id=item["dataset_id"],
                canonical_sequence_sha256=item[
                    "canonical_sequence_sha256"
                ],
                raw_zip_sha256=item["raw_zip_sha256"],
                source_timestamp_unit=SourceTimestampUnit(
                    item["source_timestamp_unit"]
                ),
                schema_version=item["schema_version"],
                normalizer_version=item["normalizer_version"],
                accepted_row_count=item["accepted_row_count"],
                observed_first_event_time=datetime.fromisoformat(
                    item["observed_first_event_time"].replace("Z", "+00:00")
                ),
                observed_last_event_time=datetime.fromisoformat(
                    item["observed_last_event_time"].replace("Z", "+00:00")
                ),
                validation_status=ResearchEventValidationStatus(
                    item["validation_status"]
                ),
            )
            for item in partition_values
        )
        if partitions and (
            partitions[0].logical_partition.provider != provider
            or partitions[0].logical_partition.market != market
            or partitions[0].logical_partition.event_family != event_family
        ):
            raise ValueError("range source scope differs from partitions")
        boundaries = tuple(
            AggregateTradeBoundaryValidation(
                left_source_date=date.fromisoformat(item["left_source_date"]),
                right_source_date=date.fromisoformat(item["right_source_date"]),
                left_last_event_time=datetime.fromisoformat(
                    item["left_last_event_time"].replace("Z", "+00:00")
                ),
                right_first_event_time=datetime.fromisoformat(
                    item["right_first_event_time"].replace("Z", "+00:00")
                ),
                left_last_aggregate_trade_id=item[
                    "left_last_aggregate_trade_id"
                ],
                right_first_aggregate_trade_id=item[
                    "right_first_aggregate_trade_id"
                ],
                chronological_order_valid=item[
                    "chronological_order_valid"
                ],
                duplicate_aggregate_id_count=item[
                    "duplicate_aggregate_id_count"
                ],
                conflicting_aggregate_id_count=item[
                    "conflicting_aggregate_id_count"
                ],
                overlapping_event_identity_count=item[
                    "overlapping_event_identity_count"
                ],
                observed_aggregate_id_delta=item[
                    "observed_aggregate_id_delta"
                ],
                numerical_continuity_asserted=item[
                    "numerical_continuity_asserted"
                ],
            )
            for item in boundary_values
        )
        manifest = AggregateTradeRangeManifest(
            symbol=decoded.pop("symbol"),
            requested_start_date=date.fromisoformat(
                decoded.pop("requested_start_date")
            ),
            requested_end_date_exclusive=date.fromisoformat(
                decoded.pop("requested_end_date_exclusive")
            ),
            selection_policy=RevisionSelectionPolicy(
                decoded.pop("selection_policy")
            ),
            selection_policy_version=decoded.pop(
                "selection_policy_version"
            ),
            range_schema_version=decoded.pop("range_schema_version"),
            expected_partition_count=decoded.pop(
                "expected_partition_count"
            ),
            selected_partition_count=decoded.pop(
                "selected_partition_count"
            ),
            partitions=partitions,
            total_accepted_event_count=decoded.pop(
                "total_accepted_event_count"
            ),
            observed_first_event_time=datetime.fromisoformat(
                decoded.pop("observed_first_event_time").replace(
                    "Z", "+00:00"
                )
            ),
            observed_last_event_time=datetime.fromisoformat(
                decoded.pop("observed_last_event_time").replace(
                    "Z", "+00:00"
                )
            ),
            boundaries=boundaries,
            completeness_state=RangeCompletenessState(
                decoded.pop("completeness_state")
            ),
            validation_status=ResearchEventValidationStatus(
                decoded.pop("validation_status")
            ),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid aggregate-trade range manifest: {error}") from error
    if decoded:
        raise ValueError("range manifest contains unknown fields")
    if recorded_range_id != manifest.range_id:
        raise ValueError("range manifest ID does not match its contents")
    if aggregate_trade_range_manifest_bytes(manifest) != payload:
        raise ValueError("range manifest is not canonically encoded")
    return manifest
