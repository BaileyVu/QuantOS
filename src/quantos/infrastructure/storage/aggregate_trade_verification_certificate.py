"""Persistent verification evidence for local immutable AggregateTrade sources.

Certificates detect corruption/change; they are not signatures and do not defend
against an attacker rewriting sources, certificates and every digest. Callers
must hash current canonical bytes and verify raw ZIPs before trusting evidence.
There is no filesystem lock against external writes after a successful hash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from quantos.domain.market_data.research_events import (
    AggregateTrade, AggregateTradeArchiveManifest, SourceTimestampUnit,
    aggregate_trade_archive_manifest_bytes, aggregate_trade_archive_manifest_id,
    canonical_aggregate_trade_event_bytes,
)
from quantos.infrastructure.storage.aggregate_trade_parquet import AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION

CERTIFICATE_SCHEMA_VERSION = "aggregate-trade-verification-certificate-v1"
VERIFICATION_VERSION = "aggregate-trade-exact-partition-verification-v1"


class AggregateTradeCertificateError(ValueError):
    """Derived certificate cannot establish reusable verification evidence."""


@dataclass(frozen=True, slots=True)
class _VerifiedPartitionEvidence:
    """O1 evidence shared by cold verification and persisted certificate reuse."""

    manifest_id: str
    first_event: AggregateTrade
    last_event: AggregateTrade
    min_aggregate_trade_id: int
    max_aggregate_trade_id: int
    accepted_event_count: int
    canonical_sequence_sha256: str
    parquet_byte_sha256: str


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AggregateTradeCertificateError("duplicate certificate JSON key")
        result[key] = value
    return result


def _reject_number(value):
    raise AggregateTradeCertificateError("certificate floats/constants are forbidden")


def _event(value) -> AggregateTrade:
    if type(value) is not dict:
        raise AggregateTradeCertificateError("certificate event must be an object")
    values = dict(value)
    for name in ("price", "quantity"):
        decimal = values[name]
        if (
            type(decimal) is not dict or set(decimal) != {"digits", "exponent", "sign"}
            or type(decimal["digits"]) is not str or not decimal["digits"]
            or any(digit not in "0123456789" for digit in decimal["digits"])
            or type(decimal["exponent"]) is not int
            or type(decimal["sign"]) is not int or decimal["sign"] not in (0, 1)
        ):
            raise AggregateTradeCertificateError("invalid exact Decimal representation")
        values[name] = Decimal((decimal["sign"], tuple(map(int, decimal["digits"])), decimal["exponent"]))
    values["event_time"] = datetime.fromisoformat(values["event_time"].replace("Z", "+00:00"))
    values["source_timestamp_unit"] = SourceTimestampUnit(values["source_timestamp_unit"])
    event = AggregateTrade(**values)
    if canonical_aggregate_trade_event_bytes(event) != _json(value):
        raise AggregateTradeCertificateError("noncanonical certificate event")
    return event


def certificate_bytes(source: AggregateTradeArchiveManifest, proof: _VerifiedPartitionEvidence) -> bytes:
    """Bind full authoritative manifest (all lineage/summary fields) and O1 proof."""
    if type(source) is not AggregateTradeArchiveManifest or type(proof) is not _VerifiedPartitionEvidence:
        raise AggregateTradeCertificateError("certificate requires exact manifest and evidence types")
    AggregateTradeArchiveManifest.__post_init__(source)
    for value in (proof.manifest_id, proof.canonical_sequence_sha256, proof.parquet_byte_sha256):
        if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise AggregateTradeCertificateError("invalid certificate SHA-256")
    if (
        proof.manifest_id != aggregate_trade_archive_manifest_id(source)
        or proof.canonical_sequence_sha256 != source.canonical_sequence_sha256
        or type(proof.accepted_event_count) is not int
        or proof.accepted_event_count != source.accepted_row_count
        or type(proof.min_aggregate_trade_id) is not int
        or type(proof.max_aggregate_trade_id) is not int
        or not 0 <= proof.min_aggregate_trade_id <= proof.max_aggregate_trade_id
        or proof.accepted_event_count > proof.max_aggregate_trade_id - proof.min_aggregate_trade_id + 1
    ):
        raise AggregateTradeCertificateError("certificate source summary differs")
    for event, timestamp in ((proof.first_event, source.observed_first_event_time),
                             (proof.last_event, source.observed_last_event_time)):
        if type(event) is not AggregateTrade:
            raise AggregateTradeCertificateError("certificate boundaries must be canonical events")
        AggregateTrade.__post_init__(event)
        if (
            event.symbol != source.symbol or event.event_time != timestamp
            or event.source_timestamp_unit is not source.source_timestamp_unit
            or not source.requested_start_time <= event.event_time < source.requested_end_time_exclusive
            or not proof.min_aggregate_trade_id <= event.aggregate_trade_id <= proof.max_aggregate_trade_id
        ):
            raise AggregateTradeCertificateError("certificate event lineage/boundary differs")
    if proof.accepted_event_count == 1 and (
        proof.first_event != proof.last_event
        or proof.min_aggregate_trade_id != proof.first_event.aggregate_trade_id
        or proof.max_aggregate_trade_id != proof.first_event.aggregate_trade_id
    ):
        raise AggregateTradeCertificateError("single-event certificate bounds differ")
    if proof.accepted_event_count > 1 and (
        (proof.first_event.event_time, proof.first_event.aggregate_trade_id)
        >= (proof.last_event.event_time, proof.last_event.aggregate_trade_id)
        or proof.first_event.aggregate_trade_id == proof.last_event.aggregate_trade_id
    ):
        raise AggregateTradeCertificateError("certificate endpoints are not distinct and ordered")
    if proof.accepted_event_count == 2 and (
        min(proof.first_event.aggregate_trade_id, proof.last_event.aggregate_trade_id)
        != proof.min_aggregate_trade_id
        or max(proof.first_event.aggregate_trade_id, proof.last_event.aggregate_trade_id)
        != proof.max_aggregate_trade_id
    ):
        raise AggregateTradeCertificateError("two-event certificate extrema differ")
    payload = {
        "certificate_schema_version": CERTIFICATE_SCHEMA_VERSION,
        "verification_version": VERIFICATION_VERSION,
        "storage_schema_version": AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION,
        "source_manifest": json.loads(aggregate_trade_archive_manifest_bytes(source)),
        "manifest_id": proof.manifest_id,
        "canonical_sequence_sha256": proof.canonical_sequence_sha256,
        "parquet_byte_sha256": proof.parquet_byte_sha256,
        "accepted_event_count": proof.accepted_event_count,
        "min_aggregate_trade_id": proof.min_aggregate_trade_id,
        "max_aggregate_trade_id": proof.max_aggregate_trade_id,
        "first_event": json.loads(canonical_aggregate_trade_event_bytes(proof.first_event)),
        "last_event": json.loads(canonical_aggregate_trade_event_bytes(proof.last_event)),
    }
    return _json({"certificate_id": sha256(_json(payload)).hexdigest(), "evidence": payload}) + b"\n"


def certificate_from_bytes(content: bytes, source: AggregateTradeArchiveManifest) -> _VerifiedPartitionEvidence:
    try:
        parsed = json.loads(content, object_pairs_hook=_unique_object,
                            parse_float=_reject_number, parse_constant=_reject_number)
        payload = parsed["evidence"]
        proof = _VerifiedPartitionEvidence(
            manifest_id=payload["manifest_id"], first_event=_event(payload["first_event"]),
            last_event=_event(payload["last_event"]),
            min_aggregate_trade_id=payload["min_aggregate_trade_id"],
            max_aggregate_trade_id=payload["max_aggregate_trade_id"],
            accepted_event_count=payload["accepted_event_count"],
            canonical_sequence_sha256=payload["canonical_sequence_sha256"],
            parquet_byte_sha256=payload["parquet_byte_sha256"],
        )
        # Reconstructing the complete expected envelope rejects unknown fields,
        # stale versions/lineage, altered IDs and noncanonical byte encodings.
        if certificate_bytes(source, proof) != content:
            raise AggregateTradeCertificateError("certificate identity, lineage or encoding differs")
        return proof
    except AggregateTradeCertificateError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, InvalidOperation, RecursionError) as error:
        raise AggregateTradeCertificateError(f"invalid certificate: {error}") from error


class AggregateTradeVerificationCertificateStore:
    """Replaceable derived certificates, issued only after exact source verification."""

    def __init__(self, root: Path):
        self._root = Path(root).resolve()

    def path_for(self, source: AggregateTradeArchiveManifest) -> Path:
        manifest_id = aggregate_trade_archive_manifest_id(source)
        path = (self._root / "market_data" / "aggregate_trades" / "verification"
                / CERTIFICATE_SCHEMA_VERSION / source.symbol / source.source_date.isoformat()
                / f"{manifest_id}.json")
        if not path.resolve().is_relative_to(self._root):
            raise AggregateTradeCertificateError("certificate path escapes root")
        return path

    def load(self, source: AggregateTradeArchiveManifest) -> _VerifiedPartitionEvidence | None:
        try:
            content = self.path_for(source).read_bytes()
        except FileNotFoundError:
            return None
        return certificate_from_bytes(content, source)

    def write(self, source: AggregateTradeArchiveManifest, proof: _VerifiedPartitionEvidence) -> None:
        content = certificate_bytes(source, proof)
        destination = self.path_for(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.path_for(source)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent,
                                             prefix=".certificate-", suffix=".tmp", delete=False) as handle:
                temporary_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            certificate_from_bytes(temporary_path.read_bytes(), source)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
