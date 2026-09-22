"""Immutable source-health contract for validated research-event archives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json

from quantos.domain.market_data.research_events.aggregate_trade import (
    ResearchDatasetRole,
    ResearchEventValidationStatus,
    SourceTimestampUnit,
    aggregate_trade_dataset_id,
)
from quantos.domain.market_data.research_events.content_identity import (
    canonical_aggregate_trade_sequence_sha256,
)
from quantos.domain.market_data.research_events.validation import (
    ValidatedAggregateTradeSequence,
)


class ArchiveChecksumStatus(str, Enum):
    """Checksum state eligible for canonical archive publication."""

    VERIFIED = "verified"


class AggregateIdContinuityStatus(str, Enum):
    """Provider documentation does not establish numerical ID continuity."""

    NOT_ASSERTED = "not_asserted"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty canonical string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _hex_digest(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact integer")
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


@dataclass(frozen=True, slots=True)
class AggregateTradeArchiveManifest:
    """Deterministic archive provenance and source-health evidence."""

    provider: str
    market: str
    source_family: str
    source_type: str
    symbol: str
    research_role: ResearchDatasetRole
    source_date: date
    requested_start_time: datetime
    requested_end_time_exclusive: datetime
    archive_url: str
    checksum_url: str
    source_timestamp_unit: SourceTimestampUnit
    schema_version: str
    schema_fingerprint: str
    normalizer_version: str
    published_checksum: str
    raw_zip_sha256: str
    checksum_status: ArchiveChecksumStatus
    archive_member_name: str
    fetched_resource_count: int
    parsed_row_count: int
    accepted_row_count: int
    rejected_row_count: int
    duplicate_count: int
    conflicting_id_count: int
    observed_numerical_id_gap_count: int
    id_continuity_status: AggregateIdContinuityStatus
    observed_first_event_time: datetime
    observed_last_event_time: datetime
    validation_status: ResearchEventValidationStatus
    canonical_sequence_sha256: str
    dataset_id: str
    source_revision_id: str
    provenance: str

    def __post_init__(self) -> None:
        for name in (
            "provider",
            "market",
            "source_family",
            "source_type",
            "symbol",
            "archive_url",
            "checksum_url",
            "schema_version",
            "normalizer_version",
            "archive_member_name",
            "provenance",
        ):
            _text(getattr(self, name), name)
        if isinstance(self.source_date, datetime) or type(self.source_date) is not date:
            raise ValueError("source_date must be a built-in date")
        if type(self.research_role) is not ResearchDatasetRole:
            raise TypeError("research_role must be explicitly declared")
        start = _utc(self.requested_start_time, "requested_start_time")
        end = _utc(self.requested_end_time_exclusive, "requested_end_time_exclusive")
        if end - start != timedelta(days=1) or start.time() != datetime.min.time():
            raise ValueError("requested interval must be one exact UTC day")
        if start.date() != self.source_date:
            raise ValueError("requested interval must match source_date")
        first = _utc(self.observed_first_event_time, "observed_first_event_time")
        last = _utc(self.observed_last_event_time, "observed_last_event_time")
        if not start <= first <= last < end:
            raise ValueError("observed coverage must be within the requested day")
        if type(self.source_timestamp_unit) is not SourceTimestampUnit:
            raise TypeError("source_timestamp_unit must be explicit")
        expected_unit = (
            SourceTimestampUnit.MILLISECOND
            if self.source_date < date(2025, 1, 1)
            else SourceTimestampUnit.MICROSECOND
        )
        if self.source_timestamp_unit is not expected_unit:
            raise ValueError(
                "source timestamp unit does not match source-date policy"
            )
        for name in (
            "schema_fingerprint",
            "published_checksum",
            "raw_zip_sha256",
            "canonical_sequence_sha256",
            "dataset_id",
            "source_revision_id",
        ):
            _hex_digest(getattr(self, name), name)
        if type(self.checksum_status) is not ArchiveChecksumStatus:
            raise TypeError("checksum_status must be an ArchiveChecksumStatus")
        if self.checksum_status is not ArchiveChecksumStatus.VERIFIED:
            raise ValueError("published archives require a verified checksum")
        if self.published_checksum != self.raw_zip_sha256:
            raise ValueError("published checksum must equal calculated raw ZIP SHA-256")
        for name in (
            "fetched_resource_count",
            "parsed_row_count",
            "accepted_row_count",
            "rejected_row_count",
            "duplicate_count",
            "conflicting_id_count",
            "observed_numerical_id_gap_count",
        ):
            _count(getattr(self, name), name)
        if self.fetched_resource_count != 2:
            raise ValueError("daily archive provenance requires checksum and ZIP resources")
        if self.parsed_row_count != self.accepted_row_count + self.rejected_row_count:
            raise ValueError("parsed count must equal accepted plus rejected counts")
        if self.accepted_row_count == 0:
            raise ValueError("a validated archive must contain accepted events")
        if self.rejected_row_count or self.duplicate_count or self.conflicting_id_count:
            raise ValueError("validated archive publication requires zero rejected or duplicate rows")
        if self.observed_numerical_id_gap_count > self.accepted_row_count - 1:
            raise ValueError("observed numerical ID-gap count is impossible")
        if type(self.id_continuity_status) is not AggregateIdContinuityStatus:
            raise TypeError("id_continuity_status must be explicit")
        if self.id_continuity_status is not AggregateIdContinuityStatus.NOT_ASSERTED:
            raise ValueError("numerical aggregate-ID continuity must not be asserted")
        if self.validation_status is not ResearchEventValidationStatus.VALIDATED:
            raise ValueError("archive manifest must record validated state")

    def as_canonical_dict(self) -> dict[str, str | int]:
        """Return every manifest field in deterministic serialization form."""

        return {
            "accepted_row_count": self.accepted_row_count,
            "archive_member_name": self.archive_member_name,
            "archive_url": self.archive_url,
            "canonical_sequence_sha256": self.canonical_sequence_sha256,
            "checksum_status": self.checksum_status.value,
            "checksum_url": self.checksum_url,
            "conflicting_id_count": self.conflicting_id_count,
            "dataset_id": self.dataset_id,
            "duplicate_count": self.duplicate_count,
            "fetched_resource_count": self.fetched_resource_count,
            "id_continuity_status": self.id_continuity_status.value,
            "market": self.market,
            "normalizer_version": self.normalizer_version,
            "observed_first_event_time": _utc_text(self.observed_first_event_time),
            "observed_last_event_time": _utc_text(self.observed_last_event_time),
            "observed_numerical_id_gap_count": self.observed_numerical_id_gap_count,
            "parsed_row_count": self.parsed_row_count,
            "provenance": self.provenance,
            "provider": self.provider,
            "published_checksum": self.published_checksum,
            "raw_zip_sha256": self.raw_zip_sha256,
            "research_role": self.research_role.value,
            "rejected_row_count": self.rejected_row_count,
            "requested_end_time_exclusive": _utc_text(self.requested_end_time_exclusive),
            "requested_start_time": _utc_text(self.requested_start_time),
            "schema_fingerprint": self.schema_fingerprint,
            "schema_version": self.schema_version,
            "source_date": self.source_date.isoformat(),
            "source_family": self.source_family,
            "source_revision_id": self.source_revision_id,
            "source_timestamp_unit": self.source_timestamp_unit.value,
            "source_type": self.source_type,
            "symbol": self.symbol,
            "validation_status": self.validation_status.value,
        }


def aggregate_trade_archive_manifest_bytes(
    manifest: AggregateTradeArchiveManifest,
) -> bytes:
    if type(manifest) is not AggregateTradeArchiveManifest:
        raise TypeError("manifest must be an AggregateTradeArchiveManifest")
    AggregateTradeArchiveManifest.__post_init__(manifest)
    return (
        json.dumps(
            manifest.as_canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def aggregate_trade_archive_manifest_id(
    manifest: AggregateTradeArchiveManifest,
) -> str:
    return sha256(aggregate_trade_archive_manifest_bytes(manifest)).hexdigest()


def aggregate_trade_archive_manifest_from_bytes(
    payload: bytes,
) -> AggregateTradeArchiveManifest:
    """Parse only the exact canonical manifest representation."""

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
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("manifest is not valid canonical UTF-8 JSON") from error
    if type(decoded) is not dict:
        raise ValueError("manifest JSON must be an object")
    expected = set(AggregateTradeArchiveManifest.__dataclass_fields__)
    if set(decoded) != expected:
        raise ValueError("manifest JSON fields do not match the canonical contract")
    try:
        decoded["source_date"] = date.fromisoformat(decoded["source_date"])
        for name in (
            "requested_start_time",
            "requested_end_time_exclusive",
            "observed_first_event_time",
            "observed_last_event_time",
        ):
            decoded[name] = datetime.fromisoformat(decoded[name].replace("Z", "+00:00"))
        decoded["source_timestamp_unit"] = SourceTimestampUnit(
            decoded["source_timestamp_unit"]
        )
        decoded["research_role"] = ResearchDatasetRole(decoded["research_role"])
        decoded["checksum_status"] = ArchiveChecksumStatus(decoded["checksum_status"])
        decoded["id_continuity_status"] = AggregateIdContinuityStatus(
            decoded["id_continuity_status"]
        )
        decoded["validation_status"] = ResearchEventValidationStatus(
            decoded["validation_status"]
        )
        manifest = AggregateTradeArchiveManifest(**decoded)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"invalid archive manifest: {error}") from error
    if aggregate_trade_archive_manifest_bytes(manifest) != payload:
        raise ValueError("manifest is not canonically encoded")
    return manifest


@dataclass(frozen=True, slots=True)
class ValidatedAggregateTradeArchive:
    """A canonical sequence bound to verified immutable source provenance."""

    sequence: ValidatedAggregateTradeSequence
    manifest: AggregateTradeArchiveManifest

    def __post_init__(self) -> None:
        if type(self.sequence) is not ValidatedAggregateTradeSequence:
            raise TypeError("sequence must be a ValidatedAggregateTradeSequence")
        if type(self.manifest) is not AggregateTradeArchiveManifest:
            raise TypeError("manifest must be an AggregateTradeArchiveManifest")
        sequence = ValidatedAggregateTradeSequence(
            self.sequence.identity, self.sequence.events
        )
        AggregateTradeArchiveManifest.__post_init__(self.manifest)
        identity = sequence.identity
        manifest = self.manifest
        expected_pairs = (
            (manifest.provider, identity.provider, "provider"),
            (manifest.market, identity.market, "market"),
            (manifest.source_family, identity.event_family, "source_family"),
            (manifest.symbol, identity.symbol, "symbol"),
            (manifest.research_role, identity.research_role, "research_role"),
            (manifest.requested_start_time, identity.requested_start_time, "requested_start_time"),
            (manifest.requested_end_time_exclusive, identity.requested_end_time_exclusive, "requested_end_time_exclusive"),
            (manifest.source_timestamp_unit, identity.source_timestamp_unit, "source_timestamp_unit"),
            (manifest.schema_version, identity.schema_version, "schema_version"),
            (manifest.normalizer_version, identity.normalizer_version, "normalizer_version"),
            (manifest.validation_status, identity.validation_status, "validation_status"),
            (manifest.provenance, identity.provenance, "provenance"),
        )
        for actual, expected, name in expected_pairs:
            if actual != expected:
                raise ValueError(f"manifest {name} does not match sequence identity")
        if manifest.dataset_id != aggregate_trade_dataset_id(identity):
            raise ValueError("manifest dataset_id does not match sequence identity")
        if manifest.accepted_row_count != len(sequence.events):
            raise ValueError("manifest accepted count does not match sequence")
        if manifest.observed_first_event_time != sequence.observed_start_time:
            raise ValueError("manifest first event time does not match sequence")
        if manifest.observed_last_event_time != sequence.observed_end_time:
            raise ValueError("manifest last event time does not match sequence")
        observed_gap_count = sum(
            current.aggregate_trade_id > previous.aggregate_trade_id + 1
            for previous, current in zip(
                sequence.events, sequence.events[1:]
            )
        )
        if (
            manifest.observed_numerical_id_gap_count
            != observed_gap_count
        ):
            raise ValueError(
                "manifest numerical aggregate-ID gap count does not match sequence"
            )
        if manifest.canonical_sequence_sha256 != canonical_aggregate_trade_sequence_sha256(sequence):
            raise ValueError("manifest canonical hash does not match sequence")
        object.__setattr__(self, "sequence", sequence)
