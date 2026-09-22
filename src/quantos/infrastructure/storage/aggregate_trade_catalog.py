"""Rebuildable local catalog over immutable aggregate-trade publications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path

from quantos.domain.common import require_v1_symbol
from quantos.domain.market_data.research_events import (
    AggregateTradeArchiveManifest,
    AggregateTradeLogicalPartitionKey,
    ValidatedAggregateTradeArchive,
    aggregate_trade_archive_manifest_bytes,
    aggregate_trade_archive_manifest_id,
)
from quantos.infrastructure.storage.aggregate_trade_parquet import (
    AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION,
    AggregateTradeParquetStorageError,
    ParquetAggregateTradeArchiveStore,
)


class AggregateTradeCatalogError(ValueError):
    """The local immutable archive catalog cannot be trusted."""


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
                archive = self._store.read(path)
                manifest = archive.manifest
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
                self._store.read_raw(manifest)
                entries.append(
                    CatalogedAggregateTradeRevision(
                        manifest=manifest,
                        canonical_parquet_path=canonical_path,
                        raw_archive_path=raw_path,
                    )
                )
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
            self._store.read_raw(manifest)
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
