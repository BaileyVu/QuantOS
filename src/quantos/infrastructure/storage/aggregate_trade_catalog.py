"""Rebuildable local catalog over immutable aggregate-trade publications."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from quantos.application.aggregate_trade_ranges import (
    AggregateTradeRangeBatchStream,
    AggregateTradeRangeStreamReport,
    DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
)

from quantos.domain.common import require_v1_symbol
from quantos.domain.market_data.research_events import (
    AggregateTradeBoundaryValidation,
    AggregateTradeArchiveManifest,
    AggregateTradeDatasetIdentity,
    AggregateTradeLogicalPartitionKey,
    AggregateTradeValidationError,
    IncrementalAggregateTradeSequenceHasher,
    IncrementalAggregateTradeSequenceValidator,
    ValidatedAggregateTradeArchive,
    aggregate_trade_dataset_id,
    aggregate_trade_archive_manifest_bytes,
    aggregate_trade_archive_manifest_id,
    canonical_aggregate_trade_event_bytes,
)
from quantos.infrastructure.storage.aggregate_trade_parquet import (
    AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION,
    AggregateTradeParquetStorageError,
    ParquetAggregateTradeArchiveStore,
)


class AggregateTradeCatalogError(ValueError):
    """The local immutable archive catalog cannot be trusted."""


class _SqliteExactAggregateTradeIdRegistry:
    """Exact range-wide duplicate state with a bounded SQLite page cache."""

    def __init__(self) -> None:
        self._temporary: TemporaryDirectory[str] | None = None
        self._connection: sqlite3.Connection | None = None
        self._existing: dict[int, bytes] = {}
        self._pending: dict[int, bytes] = {}

    def __enter__(self) -> _SqliteExactAggregateTradeIdRegistry:
        self._temporary = TemporaryDirectory(prefix="quantos-de1h-ids-")
        path = Path(self._temporary.name) / "aggregate-trade-ids.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA cache_size=-4096")
        connection.execute("PRAGMA locking_mode=EXCLUSIVE")
        connection.execute(
            "CREATE TABLE seen ("
            "aggregate_trade_id INTEGER PRIMARY KEY, "
            "canonical_event BLOB NOT NULL"
            ") WITHOUT ROWID"
        )
        self._connection = connection
        return self

    def previous_or_add(
        self, aggregate_trade_id: int, canonical_event_bytes: bytes
    ) -> bytes | None:
        if self._connection is None:
            raise AggregateTradeCatalogError("exact ID registry is not open")
        existing = self._existing.get(aggregate_trade_id)
        if existing is not None:
            return existing
        previous = self._pending.get(aggregate_trade_id)
        if previous is None:
            self._pending[aggregate_trade_id] = canonical_event_bytes
            return None
        return previous

    def prepare_batch(self, aggregate_trade_ids: tuple[int, ...]) -> None:
        """Load prior exact identities for one bounded input batch."""

        connection = self._connection
        if connection is None:
            raise AggregateTradeCatalogError("exact ID registry is not open")
        if self._pending:
            raise AggregateTradeCatalogError(
                "exact ID registry batch was not flushed"
            )
        self._existing.clear()
        unique_identifiers = tuple(dict.fromkeys(aggregate_trade_ids))
        for offset in range(0, len(unique_identifiers), 900):
            selected = unique_identifiers[offset : offset + 900]
            if not selected:
                continue
            placeholders = ",".join("?" for _ in selected)
            rows = connection.execute(
                "SELECT aggregate_trade_id, canonical_event FROM seen "
                f"WHERE aggregate_trade_id IN ({placeholders})",
                selected,
            ).fetchall()
            for aggregate_trade_id, canonical_event in rows:
                if type(canonical_event) is not bytes:
                    raise AggregateTradeCatalogError(
                        "exact ID registry returned invalid state"
                    )
                self._existing[aggregate_trade_id] = canonical_event

    def flush(self) -> None:
        """Validate and persist one bounded batch of exact identities."""

        connection = self._connection
        if connection is None:
            raise AggregateTradeCatalogError("exact ID registry is not open")
        if not self._pending:
            return
        identifiers = tuple(self._pending)
        for offset in range(0, len(identifiers), 900):
            selected = identifiers[offset : offset + 900]
            placeholders = ",".join("?" for _ in selected)
            rows = connection.execute(
                "SELECT aggregate_trade_id, canonical_event FROM seen "
                f"WHERE aggregate_trade_id IN ({placeholders})",
                selected,
            ).fetchall()
            for aggregate_trade_id, previous in rows:
                current = self._pending[aggregate_trade_id]
                if type(previous) is not bytes:
                    raise AggregateTradeCatalogError(
                        "exact ID registry returned invalid state"
                    )
                label = "duplicate" if previous == current else "conflicting"
                raise AggregateTradeValidationError(
                    f"{label} aggregate_trade_id {aggregate_trade_id}"
                )
        connection.executemany(
            "INSERT INTO seen VALUES (?, ?)", self._pending.items()
        )
        self._pending.clear()
        self._existing.clear()

    def __exit__(self, *args: object) -> None:
        connection = self._connection
        temporary = self._temporary
        self._connection = None
        self._temporary = None
        self._existing.clear()
        self._pending.clear()
        try:
            if connection is not None:
                connection.close()
        finally:
            if temporary is not None:
                temporary.cleanup()


@dataclass(frozen=True, slots=True)
class CatalogedAggregateTradeRevision:
    """Verified local paths referencing one authoritative embedded manifest."""

    manifest: AggregateTradeArchiveManifest
    canonical_parquet_path: Path
    raw_archive_path: Path

    def __post_init__(self) -> None:
        if type(self.manifest) is not AggregateTradeArchiveManifest:
            raise TypeError("manifest must be an AggregateTradeArchiveManifest")
        AggregateTradeArchiveManifest.__post_init__(self.manifest)
        if not isinstance(self.canonical_parquet_path, Path):
            raise TypeError("canonical_parquet_path must be a Path")
        if not isinstance(self.raw_archive_path, Path):
            raise TypeError("raw_archive_path must be a Path")

    @property
    def logical_partition(self) -> AggregateTradeLogicalPartitionKey:
        return AggregateTradeLogicalPartitionKey.from_manifest(self.manifest)

    @property
    def manifest_id(self) -> str:
        return aggregate_trade_archive_manifest_id(self.manifest)

    def as_canonical_identity_dict(self) -> dict[str, object]:
        """Return root-independent immutable catalog identity."""

        return {
            "canonical_sequence_sha256": (
                self.manifest.canonical_sequence_sha256
            ),
            "dataset_id": self.manifest.dataset_id,
            "logical_partition": (
                self.logical_partition.as_canonical_dict()
            ),
            "manifest_id": self.manifest_id,
            "raw_zip_sha256": self.manifest.raw_zip_sha256,
            "source_revision_id": self.manifest.source_revision_id,
        }

    def as_operator_dict(self) -> dict[str, object]:
        result = self.as_canonical_identity_dict()
        result.update(
            {
                "accepted_row_count": self.manifest.accepted_row_count,
                "canonical_parquet_path": str(
                    self.canonical_parquet_path
                ),
                "normalizer_version": self.manifest.normalizer_version,
                "observed_first_event_time": (
                    self.manifest.observed_first_event_time.isoformat()
                ),
                "observed_last_event_time": (
                    self.manifest.observed_last_event_time.isoformat()
                ),
                "raw_archive_path": str(self.raw_archive_path),
                "manifest": self.manifest.as_canonical_dict(),
                "schema_version": self.manifest.schema_version,
                "source_timestamp_unit": (
                    self.manifest.source_timestamp_unit.value
                ),
                "validation_status": self.manifest.validation_status.value,
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class AggregateTradeArchiveCatalogView:
    """Deterministic, non-authoritative view rebuilt from immutable files."""

    entries: tuple[CatalogedAggregateTradeRevision, ...]

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or any(
            type(entry) is not CatalogedAggregateTradeRevision
            for entry in self.entries
        ):
            raise TypeError("entries must be an exact tuple of catalog revisions")
        ordered = tuple(
            sorted(
                self.entries,
                key=lambda entry: (
                    entry.logical_partition.provider,
                    entry.logical_partition.market,
                    entry.logical_partition.event_family,
                    entry.logical_partition.symbol,
                    entry.logical_partition.source_date,
                    entry.manifest_id,
                ),
            )
        )
        seen_manifest_ids: set[str] = set()
        source_revisions: dict[str, str] = {}
        dataset_ids: dict[str, str] = {}
        for entry in ordered:
            CatalogedAggregateTradeRevision.__post_init__(entry)
            manifest_id = entry.manifest_id
            if manifest_id in seen_manifest_ids:
                raise AggregateTradeCatalogError(
                    f"duplicate catalog manifest {manifest_id}"
                )
            seen_manifest_ids.add(manifest_id)
            for label, identifier, seen in (
                (
                    "source revision",
                    entry.manifest.source_revision_id,
                    source_revisions,
                ),
                ("dataset", entry.manifest.dataset_id, dataset_ids),
            ):
                previous = seen.get(identifier)
                if previous is not None and previous != manifest_id:
                    raise AggregateTradeCatalogError(
                        f"conflicting {label} identity {identifier}"
                    )
                seen[identifier] = manifest_id
        object.__setattr__(self, "entries", ordered)

    @property
    def catalog_id(self) -> str:
        payload = {
            "entries": [
                entry.as_canonical_identity_dict() for entry in self.entries
            ],
            "schema_version": "aggregate-trade-local-catalog-v1",
        }
        encoded = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def revisions(
        self, *, symbol: str, source_date: date
    ) -> tuple[AggregateTradeArchiveManifest, ...]:
        require_v1_symbol(symbol)
        if isinstance(source_date, datetime) or type(source_date) is not date:
            raise ValueError("source_date must be a built-in date")
        return tuple(
            entry.manifest
            for entry in self.entries
            if entry.logical_partition.symbol == symbol
            and entry.logical_partition.source_date == source_date
        )

    def entry_for_manifest_id(
        self, manifest_id: str
    ) -> CatalogedAggregateTradeRevision:
        if (
            type(manifest_id) is not str
            or len(manifest_id) != 64
            or any(
                character not in "0123456789abcdef"
                for character in manifest_id
            )
        ):
            raise AggregateTradeCatalogError(
                "manifest_id must be a lowercase SHA-256 digest"
            )
        matches = tuple(
            entry for entry in self.entries if entry.manifest_id == manifest_id
        )
        if len(matches) != 1:
            raise AggregateTradeCatalogError(
                f"catalog contains no unique manifest {manifest_id}"
            )
        return matches[0]


class _LocalAggregateTradeRangeStream(AggregateTradeRangeBatchStream):
    """Verify raw and canonical partitions while yielding bounded batches."""

    def __init__(
        self,
        store: ParquetAggregateTradeArchiveStore,
        entries: tuple[CatalogedAggregateTradeRevision, ...],
        *,
        batch_size: int,
    ) -> None:
        if type(entries) is not tuple or not entries:
            raise AggregateTradeCatalogError(
                "stream_range requires at least one exact catalog entry"
            )
        if type(batch_size) is not int or not 1 <= batch_size <= 1_000_000:
            raise AggregateTradeCatalogError(
                "batch_size must be between 1 and 1000000"
            )
        self._store = store
        self._entries = entries
        self._batch_size = batch_size
        self._report: AggregateTradeRangeStreamReport | None = None
        self._started = False

    @property
    def report(self) -> AggregateTradeRangeStreamReport:
        if self._report is None:
            raise AggregateTradeCatalogError(
                "range stream report is unavailable before complete exhaustion"
            )
        return self._report

    @staticmethod
    def _identity(
        manifest: AggregateTradeArchiveManifest,
    ) -> AggregateTradeDatasetIdentity:
        return AggregateTradeDatasetIdentity(
            symbol=manifest.symbol,
            requested_start_time=manifest.requested_start_time,
            requested_end_time_exclusive=(
                manifest.requested_end_time_exclusive
            ),
            source_timestamp_unit=manifest.source_timestamp_unit,
            schema_version=manifest.schema_version,
            normalizer_version=manifest.normalizer_version,
            provenance=manifest.provenance,
            research_role=manifest.research_role,
        )

    @staticmethod
    def _require_partition_result(
        manifest: AggregateTradeArchiveManifest,
        result,
        canonical_sha256: str,
    ) -> None:
        if (
            result.event_count != manifest.accepted_row_count
            or result.first_event.event_time
            != manifest.observed_first_event_time
            or result.last_event.event_time
            != manifest.observed_last_event_time
            or result.observed_numerical_id_gap_count
            != manifest.observed_numerical_id_gap_count
            or canonical_sha256 != manifest.canonical_sequence_sha256
            or aggregate_trade_dataset_id(result.identity)
            != manifest.dataset_id
        ):
            raise AggregateTradeCatalogError(
                "streamed partition evidence differs from immutable manifest"
            )

    def __iter__(self) -> Iterator[tuple]:
        if self._started:
            raise AggregateTradeCatalogError("range stream is single-use")
        self._started = True
        boundaries: list[AggregateTradeBoundaryValidation] = []
        previous_result = None
        previous_manifest: AggregateTradeArchiveManifest | None = None
        first_event_time: datetime | None = None
        last_event_time: datetime | None = None
        total_event_count = 0
        max_batch_event_count = 0
        try:
            with _SqliteExactAggregateTradeIdRegistry() as registry:
                for entry in self._entries:
                    CatalogedAggregateTradeRevision.__post_init__(entry)
                    manifest = entry.manifest
                    self._store.verify_raw(manifest)
                    identity = self._identity(manifest)
                    validator = IncrementalAggregateTradeSequenceValidator(
                        identity, registry
                    )
                    hasher = IncrementalAggregateTradeSequenceHasher(
                        identity._validated_copy()
                    )
                    for batch in self._store.iter_event_batches(
                        entry.canonical_parquet_path,
                        expected_manifest=manifest,
                        batch_size=self._batch_size,
                    ):
                        registry.prepare_batch(
                            tuple(
                                event.aggregate_trade_id for event in batch
                            )
                        )
                        for event in batch:
                            encoded = canonical_aggregate_trade_event_bytes(event)
                            validator.observe(event, encoded)
                            hasher.update(
                                event, canonical_event_bytes=encoded
                            )
                        registry.flush()
                        max_batch_event_count = max(
                            max_batch_event_count, len(batch)
                        )
                        yield batch
                    result = validator.result()
                    canonical_sha256 = hasher.hexdigest()
                    self._require_partition_result(
                        manifest, result, canonical_sha256
                    )
                    if previous_result is not None and previous_manifest is not None:
                        left = previous_result.last_event
                        right = result.first_event
                        if (
                            left.event_time,
                            left.aggregate_trade_id,
                        ) >= (
                            right.event_time,
                            right.aggregate_trade_id,
                        ):
                            raise AggregateTradeCatalogError(
                                "cross-partition chronological reversal"
                            )
                        boundaries.append(
                            AggregateTradeBoundaryValidation(
                                left_source_date=previous_manifest.source_date,
                                right_source_date=manifest.source_date,
                                left_last_event_time=left.event_time,
                                right_first_event_time=right.event_time,
                                left_last_aggregate_trade_id=(
                                    left.aggregate_trade_id
                                ),
                                right_first_aggregate_trade_id=(
                                    right.aggregate_trade_id
                                ),
                                chronological_order_valid=True,
                                duplicate_aggregate_id_count=0,
                                conflicting_aggregate_id_count=0,
                                overlapping_event_identity_count=0,
                                observed_aggregate_id_delta=(
                                    right.aggregate_trade_id
                                    - left.aggregate_trade_id
                                ),
                                numerical_continuity_asserted=False,
                            )
                        )
                    if first_event_time is None:
                        first_event_time = result.first_event.event_time
                    last_event_time = result.last_event.event_time
                    total_event_count += result.event_count
                    previous_result = result
                    previous_manifest = manifest
        except AggregateTradeCatalogError:
            raise
        except (
            AggregateTradeParquetStorageError,
            AggregateTradeValidationError,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            raise AggregateTradeCatalogError(
                f"streamed range verification failed: {error}"
            ) from error
        if first_event_time is None or last_event_time is None:
            raise AggregateTradeCatalogError("streamed range contains no events")
        self._report = AggregateTradeRangeStreamReport(
            total_event_count=total_event_count,
            first_event_time=first_event_time,
            last_event_time=last_event_time,
            boundaries=tuple(boundaries),
            max_batch_event_count=max_batch_event_count,
        )


class LocalAggregateTradeArchiveCatalog:
    """Rebuild a catalog from verified canonical Parquet and raw ZIP files."""

    def __init__(
        self,
        root: Path,
        *,
        provider: str,
        market: str,
        event_family: str,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a Path")
        for value, name in (
            (provider, "provider"),
            (market, "market"),
            (event_family, "event_family"),
        ):
            if type(value) is not str or not value or value != value.strip():
                raise AggregateTradeCatalogError(
                    f"{name} must be a canonical string"
                )
        self._root = root
        self._provider = provider
        self._market = market
        self._event_family = event_family
        self._store = ParquetAggregateTradeArchiveStore(root)
        self._view = AggregateTradeArchiveCatalogView(())

    @property
    def view(self) -> AggregateTradeArchiveCatalogView:
        return self._view

    def _candidate_paths(self) -> tuple[Path, ...]:
        canonical_root = (
            self._root
            / "market_data"
            / "aggregate_trades"
            / "canonical"
            / AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION
        )
        if not canonical_root.exists():
            return ()
        if not canonical_root.is_dir():
            raise AggregateTradeCatalogError(
                "aggregate-trade canonical catalog root is not a directory"
            )
        try:
            return tuple(canonical_root.rglob("*.parquet"))
        except OSError as error:
            raise AggregateTradeCatalogError(
                f"cannot enumerate aggregate-trade catalog: {error}"
            ) from error

    def rebuild(self) -> AggregateTradeArchiveCatalogView:
        self._view = AggregateTradeArchiveCatalogView(())
        entries: list[CatalogedAggregateTradeRevision] = []
        try:
            candidates = sorted(
                self._candidate_paths(), key=lambda path: path.as_posix()
            )
            for path in candidates:
                manifest = self._store.read_manifest(path)
                key = AggregateTradeLogicalPartitionKey.from_manifest(manifest)
                if (
                    key.provider != self._provider
                    or key.market != self._market
                    or key.event_family != self._event_family
                ):
                    raise AggregateTradeCatalogError(
                        "catalog publication has conflicting logical scope"
                    )
                canonical_path = self._store.dataset_path(manifest)
                if path != canonical_path:
                    raise AggregateTradeCatalogError(
                        "catalog publication is not at its canonical path"
                    )
                raw_path = self._store.raw_archive_path(manifest)
                entry = CatalogedAggregateTradeRevision(
                    manifest=manifest,
                    canonical_parquet_path=canonical_path,
                    raw_archive_path=raw_path,
                )
                verification = _LocalAggregateTradeRangeStream(
                    self._store,
                    (entry,),
                    batch_size=DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
                )
                for _batch in verification:
                    pass
                verification.report
                entries.append(entry)
        except AggregateTradeCatalogError:
            raise
        except AggregateTradeParquetStorageError as error:
            raise AggregateTradeCatalogError(
                f"catalog rebuild failed source verification: {error}"
            ) from error
        self._view = AggregateTradeArchiveCatalogView(tuple(entries))
        return self._view

    def revisions(
        self, *, symbol: str, source_date: date
    ) -> tuple[AggregateTradeArchiveManifest, ...]:
        return self._view.revisions(symbol=symbol, source_date=source_date)

    def load(
        self, manifest: AggregateTradeArchiveManifest
    ) -> ValidatedAggregateTradeArchive:
        if type(manifest) is not AggregateTradeArchiveManifest:
            raise AggregateTradeCatalogError(
                "load requires an authoritative archive manifest"
            )
        manifest_id = aggregate_trade_archive_manifest_id(manifest)
        entry = self._view.entry_for_manifest_id(manifest_id)
        if aggregate_trade_archive_manifest_bytes(entry.manifest) != (
            aggregate_trade_archive_manifest_bytes(manifest)
        ):
            raise AggregateTradeCatalogError(
                "selected manifest differs from catalog authority"
            )
        try:
            self._store.verify_raw(manifest)
            archive = self._store.read(entry.canonical_parquet_path)
        except AggregateTradeParquetStorageError as error:
            raise AggregateTradeCatalogError(
                f"selected catalog revision failed verification: {error}"
            ) from error
        if archive.manifest != manifest:
            raise AggregateTradeCatalogError(
                "selected canonical archive manifest changed"
            )
        return archive

    def stream_range(
        self,
        manifests: tuple[AggregateTradeArchiveManifest, ...],
        *,
        batch_size: int,
    ) -> AggregateTradeRangeBatchStream:
        if type(manifests) is not tuple or not manifests:
            raise AggregateTradeCatalogError(
                "stream_range requires a non-empty exact manifest tuple"
            )
        entries: list[CatalogedAggregateTradeRevision] = []
        for manifest in manifests:
            if type(manifest) is not AggregateTradeArchiveManifest:
                raise AggregateTradeCatalogError(
                    "stream_range requires authoritative manifests"
                )
            manifest_id = aggregate_trade_archive_manifest_id(manifest)
            entry = self._view.entry_for_manifest_id(manifest_id)
            if aggregate_trade_archive_manifest_bytes(entry.manifest) != (
                aggregate_trade_archive_manifest_bytes(manifest)
            ):
                raise AggregateTradeCatalogError(
                    "selected stream manifest differs from catalog authority"
                )
            entries.append(entry)
        return _LocalAggregateTradeRangeStream(
            self._store,
            tuple(entries),
            batch_size=batch_size,
        )
