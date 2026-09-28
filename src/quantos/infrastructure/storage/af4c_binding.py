"""Local-only AF4C binding preflight and immutable scientific publication.

Explicit local roots only. No CLI, acquisition, evaluation, or logging. Public
errors intentionally discard lower-level text and chained observation payloads.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import os
from pathlib import Path
import tempfile

from quantos.application.af4c_development_binding import (
    BindingError, BindingInputs, DevelopmentBinding, RECEIPT_SCHEMA, SCHEMA,
    SYMBOLS, _FROZEN, _TestOnlyContract, _build, _digest, _load, _require,
    canonical_bytes, load_receipt,
)
from quantos.application.aggregate_trade_minute_states import (
    AggregateTradeStreamingDiagnostics, aggregate_trade_minute_states,
)
from quantos.application.aggregate_trade_ranges import compose_aggregate_trade_range
from quantos.domain.market_data.research_events import (
    AggregateTradeRangeRequest, ExactAggregateTradeRevision, ResearchDatasetRole,
    RevisionSelectionPolicy, aggregate_trade_archive_manifest_id,
)
from quantos.infrastructure.storage.aggregate_trade_catalog import (
    AggregateTradeCatalogRebuildDiagnostics, LocalAggregateTradeArchiveCatalog,
)
from quantos.infrastructure.storage.aggregate_trade_minute_primitive_cache import (
    ParquetAggregateTradeMinutePrimitiveCache,
)
from quantos.infrastructure.storage.aggregate_trade_parquet import (
    AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION, ParquetAggregateTradeArchiveStore,
)
from quantos.infrastructure.storage.aggregate_trade_verification_certificate import (
    AggregateTradeVerificationCertificateStore,
)
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore, dataset_id


def _local(path: Path) -> Path:
    _require(isinstance(path, Path))
    # UNC/device paths may perform remote I/O before any Python network call.
    _require(not str(path).startswith(("\\\\", "//")))
    _require(".." not in path.parts)
    absolute = path.absolute()
    for part in (absolute, *absolute.parents):
        _require(not part.is_symlink() and not part.is_junction())
    return absolute


def _tree(root: Path) -> None:
    """Reject redirects before catalog discovery can follow them."""
    _local(root)
    if root.exists():
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in [*dirs, *files]:
                _local(Path(parent) / name)


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        digest = sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _preflight(candle_paths: tuple[Path, Path], source_root: Path,
               primitive_cache: ParquetAggregateTradeMinutePrimitiveCache,
               exact_revisions: tuple[tuple[ExactAggregateTradeRevision, ...], ...] | None,
               contract: _TestOnlyContract,
               expected_binding: DevelopmentBinding | None = None) -> tuple[DevelopmentBinding, bytes]:
    """Private fixture seam; public preflight has no interval override."""
    try:
        _require(type(candle_paths) is tuple and len(candle_paths) == 2)
        _require(type(primitive_cache) is ParquetAggregateTradeMinutePrimitiveCache)
        paths = tuple(_local(path) for path in candle_paths)
        root = _local(source_root)
        _tree(root)
        # Current cache has no public root accessor. Only inspect this exact type.
        _tree(_local(primitive_cache._root))
        if exact_revisions is not None:
            _require(type(exact_revisions) is tuple and len(exact_revisions) == 2)
        candles, hashes = [], []
        for path in paths:
            before = _sha(path)
            sequence = ParquetCandleDatasetStore(path.parent).read(path)
            _require(_sha(path) == before)
            candles.append(sequence)
            hashes.append(before)
        # O3 itself rebuilds corrupt derived certificates. STEP 9B instead stops
        # before that repair; absent certificates remain a valid cold path.
        archive_store = ParquetAggregateTradeArchiveStore(root)
        certificates = AggregateTradeVerificationCertificateStore(root)
        canonical_root = root / "market_data" / "aggregate_trades" / "canonical" / AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION
        for path in sorted(canonical_root.rglob("*.parquet")):
            manifest = archive_store.read_manifest(path)
            # Screen metadata before O3 can decode any certificate/event values.
            # Caller roots must contain only the permitted development universe.
            _require(manifest.research_role is ResearchDatasetRole.DEVELOPMENT)
            _require(contract.start.date() <= manifest.source_date < contract.end.date())
            proof = certificates.load(manifest)
            if proof is not None:
                _require(proof.parquet_byte_sha256 == _sha(path))
        catalog = LocalAggregateTradeArchiveCatalog(root, provider="binance", market="spot", event_family="aggregate_trade")
        diagnostics = AggregateTradeCatalogRebuildDiagnostics()
        catalog.rebuild(diagnostics=diagnostics)
        ranges, sources, minutes, operations = [], [], [], []
        for index, symbol in enumerate(SYMBOLS):
            request = AggregateTradeRangeRequest(
                symbol=symbol, start_date=contract.start.date(), end_date_exclusive=contract.end.date(),
                selection_policy=RevisionSelectionPolicy.UNIQUE if exact_revisions is None else RevisionSelectionPolicy.EXACT,
                exact_revisions=() if exact_revisions is None else exact_revisions[index])
            source_range = compose_aggregate_trade_range(catalog, request)
            selected = []
            for ref in source_range.partitions:
                matches = tuple(m for m in catalog.revisions(symbol=symbol, source_date=ref.logical_partition.source_date)
                                if aggregate_trade_archive_manifest_id(m) == ref.manifest_id
                                and m.source_revision_id == ref.source_revision_id)
                _require(len(matches) == 1 and matches[0].research_role is ResearchDatasetRole.DEVELOPMENT)
                selected.append(matches[0])
            available = catalog.verified_range_report(tuple(selected)) is not None
            minute_diagnostics = AggregateTradeStreamingDiagnostics()
            dataset = aggregate_trade_minute_states(catalog, source_range, primitive_cache=primitive_cache,
                                                     diagnostics=minute_diagnostics)
            ranges.append(source_range)
            sources.append(tuple(selected))
            minutes.append(dataset)
            # Buffer maxima and per-minute activity counters are deliberately absent.
            operations.append({"symbol": symbol, "o1_verified_range_available": available,
                               **{name: getattr(minute_diagnostics, name) for name in (
                                   "cache_hit_partition_count", "cache_miss_partition_count",
                                   "cache_built_partition_count", "cached_minute_rows_loaded",
                                   "raw_events_consumed", "warm_cache_used")}})
        binding = _build(BindingInputs(tuple(candles), tuple(hashes), tuple(ranges), tuple(sources), tuple(minutes)),
                         contract, dataset_id)
        if expected_binding is not None:
            _require(type(expected_binding) is DevelopmentBinding)
            _require(_load(expected_binding.content, contract, dataset_id).content == binding.content)
        # Detect ordinary source rewrites across the complete operation as well.
        _require(all(_sha(path) == digest for path, digest in zip(paths, hashes, strict=True)))
        for selected in sources:
            # May fall back to streaming; aggregation already established that proof.
            for source in selected:
                archive_store.verify_raw(source)
                proof = certificates.load(source)
                _require(proof is not None and proof.parquet_byte_sha256 == _sha(archive_store.dataset_path(source)))
        receipt = load_receipt(canonical_bytes({"schema_version": RECEIPT_SCHEMA,
            "binding_id": binding.binding_id, "catalog": asdict(diagnostics), "symbols": operations}))
        return binding, receipt
    except Exception:
        raise BindingError("local binding preflight rejected") from None


def preflight_development(*, candle_paths: tuple[Path, Path], source_root: Path,
                          primitive_cache: ParquetAggregateTradeMinutePrimitiveCache,
                          exact_revisions: tuple[tuple[ExactAggregateTradeRevision, ...], ...] | None = None,
                          expected_binding: DevelopmentBinding | None = None,
                          ) -> tuple[DevelopmentBinding, bytes]:
    """Bind the full frozen interval. Does not publish or execute the evaluator."""
    return _preflight(candle_paths, source_root, primitive_cache, exact_revisions, _FROZEN, expected_binding)


class DevelopmentBindingStore:
    """Atomic create-once binding files. Receipts are returned, not persisted here."""

    def __init__(self, root: Path):
        try:
            self._root = _local(root)
        except Exception:
            raise BindingError("binding storage root rejected") from None

    def _path(self, binding_id: str) -> Path:
        _digest(binding_id)
        path = self._root / "research" / "alpha-funnel-af4c" / "binding" / SCHEMA / f"{binding_id}.json"
        _local(path)
        _require(path.resolve().is_relative_to(self._root.resolve()))
        return path

    def read(self, binding_id: str) -> DevelopmentBinding:
        return self._read(binding_id, _FROZEN)

    def _read(self, binding_id: str, contract: _TestOnlyContract) -> DevelopmentBinding:
        try:
            binding = _load(self._path(binding_id).read_bytes(), contract, dataset_id)
            _require(binding.binding_id == binding_id)
            return binding
        except Exception:
            raise BindingError("binding storage read rejected") from None

    def write(self, binding: DevelopmentBinding) -> Path:
        return self._write(binding, _FROZEN)

    def _write(self, binding: DevelopmentBinding, contract: _TestOnlyContract) -> Path:
        temporary = None
        try:
            _require(type(binding) is DevelopmentBinding)
            checked = _load(binding.content, contract, dataset_id)
            destination = self._path(checked.binding_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._path(checked.binding_id)
            if destination.exists():
                _require(self._read(checked.binding_id, contract).content == checked.content)
                return destination
            with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=".binding-",
                                             suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(checked.content)
                handle.flush()
                os.fsync(handle.fileno())
            _require(_load(temporary.read_bytes(), contract, dataset_id).content == checked.content)
            self._path(checked.binding_id)
            try:
                os.link(temporary, destination)  # Atomic no-replace, including concurrent publishers.
            except FileExistsError:
                _require(destination.read_bytes() == checked.content)
            if os.name != "nt":
                descriptor = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            _require(self._read(checked.binding_id, contract).content == checked.content)
            return destination
        except Exception:
            raise BindingError("binding storage publication rejected") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except Exception:
                    raise BindingError("binding temporary cleanup rejected") from None
