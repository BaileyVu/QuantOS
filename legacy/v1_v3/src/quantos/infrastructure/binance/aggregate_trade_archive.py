"""Verified Binance Spot daily aggregate-trade archive ingestion."""

from __future__ import annotations

from collections.abc import Callable
import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import hmac
from io import BytesIO, StringIO
import json
import math
import socket
import stat
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile
import zlib

from quantos.domain.common import require_v1_symbol
from quantos.domain.market_data.research_events import (
    AggregateIdContinuityStatus,
    AggregateTrade,
    AggregateTradeArchiveManifest,
    AggregateTradeDatasetIdentity,
    AggregateTradeValidationError,
    ArchiveChecksumStatus,
    ResearchDatasetRole,
    ResearchEventValidationStatus,
    ValidatedAggregateTradeArchive,
    aggregate_trade_dataset_id,
    canonical_aggregate_trade_sequence_sha256,
    validate_aggregate_trade_sequence,
)
from quantos.infrastructure.binance.aggregate_trades import (
    AGGREGATE_TRADE_NORMALIZER_VERSION,
    ARCHIVE_AGGREGATE_TRADE_COLUMNS,
    BinanceAggregateTradeNormalizationError,
    archive_timestamp_unit,
    normalize_archive_aggregate_trade,
    raw_content_sha256,
)
from quantos.infrastructure.binance.klines import BinanceMarketDataError

BINANCE_PUBLIC_ARCHIVE_URL = "https://data.binance.vision"
AGGREGATE_TRADE_ARCHIVE_SCHEMA_VERSION = "aggregate-trade-v1"
AGGREGATE_TRADE_ARCHIVE_ADAPTER_VERSION = (
    "binance-spot-daily-aggtrades-archive-adapter-v1"
)
AGGREGATE_TRADE_ARCHIVE_SCHEMA_FINGERPRINT = sha256(
    json.dumps(
        {"columns": ARCHIVE_AGGREGATE_TRADE_COLUMNS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
).hexdigest()
MAX_AGGREGATE_TRADE_ZIP_BYTES = 512 * 1024 * 1024
MAX_AGGREGATE_TRADE_CSV_BYTES = 2 * 1024 * 1024 * 1024
MAX_AGGREGATE_TRADE_COMPRESSION_RATIO = 200
_HTTP_GET = Callable[[str, float], bytes]


class BinanceAggregateTradeArchiveError(BinanceMarketDataError):
    """A daily aggregate-trade archive failed verification or validation."""


@dataclass(frozen=True, slots=True)
class FetchedAggregateTradeArchive:
    """Verified raw ZIP bytes and their validated canonical representation."""

    archive: ValidatedAggregateTradeArchive
    raw_zip_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.archive) is not ValidatedAggregateTradeArchive:
            raise TypeError("archive must be a ValidatedAggregateTradeArchive")
        if type(self.raw_zip_bytes) is not bytes:
            raise TypeError("raw_zip_bytes must be exact bytes")
        ValidatedAggregateTradeArchive(self.archive.sequence, self.archive.manifest)
        if raw_content_sha256(self.raw_zip_bytes) != self.archive.manifest.raw_zip_sha256:
            raise ValueError("raw ZIP bytes do not match the archive manifest")


def aggregate_trade_archive_resource_urls(
    *, symbol: str, archive_date: date
) -> tuple[str, str, str, str]:
    """Return trusted ZIP/checksum identities and expected filenames."""

    if type(symbol) is not str:
        raise ValueError("symbol must be an exact built-in string")
    require_v1_symbol(symbol)
    if isinstance(archive_date, datetime) or type(archive_date) is not date:
        raise ValueError("archive_date must be a built-in date")
    stem = f"{symbol}-aggTrades-{archive_date.isoformat()}"
    zip_filename = f"{stem}.zip"
    csv_filename = f"{stem}.csv"
    archive_url = (
        f"{BINANCE_PUBLIC_ARCHIVE_URL}/data/spot/daily/aggTrades/"
        f"{symbol}/{zip_filename}"
    )
    return archive_url, f"{archive_url}.CHECKSUM", zip_filename, csv_filename


def _default_http_get(url: str, timeout_seconds: float) -> bytes:
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed HTTPS host
        if response.geturl() != url:
            raise BinanceAggregateTradeArchiveError(
                "Binance archive redirects are not accepted"
            )
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as error:
                raise BinanceAggregateTradeArchiveError(
                    "Binance archive Content-Length is malformed"
                ) from error
            if declared_length < 0 or declared_length > MAX_AGGREGATE_TRADE_ZIP_BYTES:
                raise BinanceAggregateTradeArchiveError(
                    "Binance archive response exceeds the configured byte limit"
                )
        payload = response.read(MAX_AGGREGATE_TRADE_ZIP_BYTES + 1)
        if len(payload) > MAX_AGGREGATE_TRADE_ZIP_BYTES:
            raise BinanceAggregateTradeArchiveError(
                "Binance archive response exceeds the configured byte limit"
            )
        return payload


def _normalize_csv(
    csv_bytes: bytes,
    *,
    symbol: str,
    archive_date: date,
    archive_filename: str,
) -> tuple[AggregateTrade, ...]:
    try:
        text = csv_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BinanceAggregateTradeArchiveError(
            f"{archive_filename} CSV is not valid UTF-8"
        ) from error
    events: list[AggregateTrade] = []
    try:
        rows = csv.reader(StringIO(text, newline=""), strict=True)
        for row_number, row in enumerate(rows, start=1):
            try:
                event = normalize_archive_aggregate_trade(
                    row,
                    symbol=symbol,
                    archive_date=archive_date,
                )
            except BinanceAggregateTradeNormalizationError as error:
                raise BinanceAggregateTradeArchiveError(
                    f"archive row {row_number} is invalid: {error}"
                ) from error
            events.append(event)
    except csv.Error as error:
        raise BinanceAggregateTradeArchiveError(
            f"{archive_filename} contains malformed CSV"
        ) from error
    return tuple(events)


def _duplicate_counts(events: tuple[AggregateTrade, ...]) -> tuple[int, int]:
    seen: dict[int, AggregateTrade] = {}
    duplicates = 0
    conflicts = 0
    for event in events:
        previous = seen.get(event.aggregate_trade_id)
        if previous is None:
            seen[event.aggregate_trade_id] = event
        elif previous == event:
            duplicates += 1
        else:
            conflicts += 1
    return duplicates, conflicts


def _observed_numerical_id_gap_count(
    events: tuple[AggregateTrade, ...],
) -> int:
    return sum(
        current.aggregate_trade_id > previous.aggregate_trade_id + 1
        for previous, current in zip(events, events[1:])
    )


def _source_revision_id(
    *, archive_url: str, checksum_url: str, raw_zip_sha256: str
) -> str:
    payload = {
        "archive_url": archive_url,
        "checksum_url": checksum_url,
        "raw_zip_sha256": raw_zip_sha256,
    }
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


class BinanceSpotAggregateTradeDailyArchiveAdapter:
    """Acquire and validate one immutable Spot daily aggTrades source revision."""

    def __init__(
        self,
        *,
        http_get: _HTTP_GET | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if type(timeout_seconds) not in (int, float):
            raise ValueError("timeout_seconds must be a finite positive number")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        self._http_get = http_get or _default_http_get
        self._timeout_seconds = float(timeout_seconds)

    def fetch_daily_archive(
        self,
        *,
        symbol: str,
        archive_date: date,
        research_role: ResearchDatasetRole,
    ) -> FetchedAggregateTradeArchive:
        """Fetch checksum then ZIP once each and return a validated partition."""

        if type(research_role) is not ResearchDatasetRole:
            raise ValueError("research_role must be explicitly declared")
        archive_url, checksum_url, zip_filename, csv_filename = (
            aggregate_trade_archive_resource_urls(
                symbol=symbol, archive_date=archive_date
            )
        )
        checksum_payload = self._download(
            checksum_url,
            resource_name="checksum",
            archive_filename=zip_filename,
        )
        published_checksum = _parse_checksum(
            checksum_payload, expected_filename=zip_filename
        )
        zip_bytes = self._download(
            archive_url,
            resource_name="ZIP",
            archive_filename=zip_filename,
        )
        raw_zip_sha = raw_content_sha256(zip_bytes)
        if not hmac.compare_digest(raw_zip_sha, published_checksum):
            raise BinanceAggregateTradeArchiveError(
                f"SHA-256 checksum mismatch for {zip_filename}"
            )
        csv_bytes = _read_expected_csv(
            zip_bytes,
            expected_csv_filename=csv_filename,
            archive_filename=zip_filename,
        )
        events = _normalize_csv(
            csv_bytes,
            symbol=symbol,
            archive_date=archive_date,
            archive_filename=zip_filename,
        )
        duplicates, conflicts = _duplicate_counts(events)
        if duplicates or conflicts:
            raise BinanceAggregateTradeArchiveError(
                "archive contains duplicate or conflicting aggregate trade IDs "
                f"(duplicates={duplicates}, conflicts={conflicts})"
            )
        day_start = datetime.combine(archive_date, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        provenance = (
            f"{AGGREGATE_TRADE_ARCHIVE_ADAPTER_VERSION}:"
            f"{archive_url}#sha256={raw_zip_sha}"
        )
        identity = AggregateTradeDatasetIdentity(
            symbol=symbol,
            requested_start_time=day_start,
            requested_end_time_exclusive=day_end,
            source_timestamp_unit=archive_timestamp_unit(archive_date),
            schema_version=AGGREGATE_TRADE_ARCHIVE_SCHEMA_VERSION,
            normalizer_version=AGGREGATE_TRADE_NORMALIZER_VERSION,
            provenance=provenance,
            research_role=research_role,
        )
        try:
            sequence = validate_aggregate_trade_sequence(identity, events)
        except (AggregateTradeValidationError, TypeError, ValueError) as error:
            raise BinanceAggregateTradeArchiveError(
                f"{zip_filename} failed canonical sequence validation: {error}"
            ) from error
        revision_id = _source_revision_id(
            archive_url=archive_url,
            checksum_url=checksum_url,
            raw_zip_sha256=raw_zip_sha,
        )
        manifest = AggregateTradeArchiveManifest(
            provider=sequence.identity.provider,
            market=sequence.identity.market,
            source_family=sequence.identity.event_family,
            source_type="daily_public_archive",
            symbol=symbol,
            research_role=research_role,
            source_date=archive_date,
            requested_start_time=day_start,
            requested_end_time_exclusive=day_end,
            archive_url=archive_url,
            checksum_url=checksum_url,
            source_timestamp_unit=sequence.identity.source_timestamp_unit,
            schema_version=sequence.identity.schema_version,
            schema_fingerprint=AGGREGATE_TRADE_ARCHIVE_SCHEMA_FINGERPRINT,
            normalizer_version=sequence.identity.normalizer_version,
            published_checksum=published_checksum,
            raw_zip_sha256=raw_zip_sha,
            checksum_status=ArchiveChecksumStatus.VERIFIED,
            archive_member_name=csv_filename,
            fetched_resource_count=2,
            parsed_row_count=len(events),
            accepted_row_count=len(events),
            rejected_row_count=0,
            duplicate_count=0,
            conflicting_id_count=0,
            observed_numerical_id_gap_count=_observed_numerical_id_gap_count(
                events
            ),
            id_continuity_status=AggregateIdContinuityStatus.NOT_ASSERTED,
            observed_first_event_time=sequence.observed_start_time,
            observed_last_event_time=sequence.observed_end_time,
            validation_status=ResearchEventValidationStatus.VALIDATED,
            canonical_sequence_sha256=canonical_aggregate_trade_sequence_sha256(
                sequence
            ),
            dataset_id=aggregate_trade_dataset_id(sequence.identity),
            source_revision_id=revision_id,
            provenance=provenance,
        )
        return FetchedAggregateTradeArchive(
            archive=ValidatedAggregateTradeArchive(sequence, manifest),
            raw_zip_bytes=zip_bytes,
        )

    def _download(
        self,
        url: str,
        *,
        resource_name: str,
        archive_filename: str,
    ) -> bytes:
        try:
            payload = self._http_get(url, self._timeout_seconds)
        except BinanceAggregateTradeArchiveError:
            raise
        except HTTPError as error:
            raise BinanceAggregateTradeArchiveError(
                f"Binance {resource_name} request failed for "
                f"{archive_filename}: {error.code}",
                http_status=error.code,
            ) from error
        except (socket.timeout, TimeoutError) as error:
            raise BinanceAggregateTradeArchiveError(
                f"Binance {resource_name} request timed out for {archive_filename}"
            ) from error
        except URLError as error:
            raise BinanceAggregateTradeArchiveError(
                f"Binance {resource_name} network request failed for "
                f"{archive_filename}: {error.reason}"
            ) from error
        if type(payload) is not bytes:
            raise BinanceAggregateTradeArchiveError(
                f"Binance {resource_name} response for {archive_filename} "
                "must be exact bytes"
            )
        if len(payload) > MAX_AGGREGATE_TRADE_ZIP_BYTES:
            raise BinanceAggregateTradeArchiveError(
                f"Binance {resource_name} response exceeds the byte limit"
            )
        return payload


def _parse_checksum(payload: bytes, *, expected_filename: str) -> str:
    if type(payload) is not bytes:
        raise BinanceAggregateTradeArchiveError(
            f"checksum for {expected_filename} must be exact bytes"
        )
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise BinanceAggregateTradeArchiveError(
            f"checksum for {expected_filename} is not valid ASCII"
        ) from error
    if text.endswith("\r\n"):
        record = text[:-2]
    elif text.endswith("\n"):
        record = text[:-1]
    else:
        record = text
    if not record or any(
        character in "\r\n"
        or (ord(character) < 32 and character != "\t")
        or ord(character) == 127
        for character in record
    ):
        raise BinanceAggregateTradeArchiveError(
            f"checksum for {expected_filename} must contain exactly one safe record"
        )
    record = record.strip(" \t")
    separator = next(
        (index for index, character in enumerate(record) if character in " \t"),
        None,
    )
    if separator is None:
        raise BinanceAggregateTradeArchiveError(
            f"checksum for {expected_filename} must contain one digest and filename"
        )
    digest = record[:separator]
    filename_start = separator
    while filename_start < len(record) and record[filename_start] in " \t":
        filename_start += 1
    recorded_filename = record[filename_start:]
    if recorded_filename.startswith("*"):
        recorded_filename = recorded_filename[1:]
    if (
        not recorded_filename
        or any(character in " \t" for character in recorded_filename)
        or recorded_filename != expected_filename
    ):
        raise BinanceAggregateTradeArchiveError(
            f"checksum filename does not match {expected_filename}"
        )
    if len(digest) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in digest
    ):
        raise BinanceAggregateTradeArchiveError(
            f"checksum for {expected_filename} is not a SHA-256 digest"
        )
    return digest.lower()


def _read_expected_csv(
    zip_bytes: bytes, *, expected_csv_filename: str, archive_filename: str
) -> bytes:
    if type(zip_bytes) is not bytes or len(zip_bytes) > MAX_AGGREGATE_TRADE_ZIP_BYTES:
        raise BinanceAggregateTradeArchiveError(
            f"{archive_filename} exceeds the configured compressed byte limit"
        )
    try:
        with ZipFile(BytesIO(zip_bytes)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} must contain exactly one data member"
                )
            member = members[0]
            if member.is_dir() or member.filename != expected_csv_filename:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} does not contain the exact expected CSV filename"
                )
            if member.flag_bits & 0x1:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} contains an encrypted member"
                )
            unix_type = (member.external_attr >> 16) & 0o170000
            if member.create_system == 3 and unix_type not in (0, stat.S_IFREG):
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} member must be a regular file"
                )
            if member.file_size > MAX_AGGREGATE_TRADE_CSV_BYTES:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} uncompressed member exceeds the byte limit"
                )
            if member.file_size > max(1, member.compress_size) * MAX_AGGREGATE_TRADE_COMPRESSION_RATIO:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} exceeds the compression-ratio limit"
                )
            try:
                with archive.open(member, "r") as source:
                    csv_bytes = source.read(MAX_AGGREGATE_TRADE_CSV_BYTES + 1)
            except (BadZipFile, RuntimeError, NotImplementedError, zlib.error) as error:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} member could not be read safely"
                ) from error
            if len(csv_bytes) > MAX_AGGREGATE_TRADE_CSV_BYTES:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} uncompressed member exceeds the byte limit"
                )
            if len(csv_bytes) != member.file_size:
                raise BinanceAggregateTradeArchiveError(
                    f"{archive_filename} member size does not match ZIP metadata"
                )
            return csv_bytes
    except BadZipFile as error:
        raise BinanceAggregateTradeArchiveError(
            f"{archive_filename} is not a valid ZIP archive"
        ) from error
