"""Offline deterministic A1 baseline and optional O2/O3 cold/warm measurement.

Run with the repository venv, for example::

    python research/a1_de1_baseline_benchmark.py --root G:\\QuantOS-A1-Bench \
        --days 2 --events-per-minute 25 --batch-size 16384

The root must be a NEW directory with an existing parent, external to the
worktree. Nothing is deleted or overwritten. Summary goes to stderr; one sorted,
compact JSON result goes to stdout (redirect stdout to save it if desired).
Publication includes synthetic CSV/ZIP construction, offline adapter validation
and canonical Parquet publication, one day at a time. Batch size applies to range
composition and minute materialization, not the existing daily publication API.
Minute materialization measures the returned in-memory canonical dataset, not
an additional minute-state Parquet export. Total timing covers the four phases
and their orchestration, excluding argument/root validation and result formatting.
Timings and throughput are observational; only scientific identities are stable.
This is a warm sequential pipeline, not a controlled cold-cache experiment.
Add --minute-cache to publish sources/rebuild/compose once, then time a cold
primitive-cache build and a warm reuse against those same publications. The
existing minute_materialization timing denotes the cold run in this mode;
minute_cache_cold/warm timings and raw-event diagnostics are also emitted.
Total time then includes both runs and their exact scientific parity checks.
Add --partition-certificates for O3 (implies --minute-cache): after cold rebuild
and minute-cache construction, discard the catalog, create a fresh instance,
then measure warm certificate rebuild, range composition and minute reuse.
Warm timings include required source/certificate/cache verification. Existing
catalog_rebuild/range_compose fields retain their cold-run meanings; O3 emits
separate warm_catalog_rebuild/warm_range_compose fields and a fully warm sum.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from io import BytesIO
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import stat
import sys
import time
from zipfile import ZIP_STORED, ZipFile, ZipInfo

# Support the documented direct-script invocation and reuse repository fixtures.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import duckdb
import pyarrow

from quantos.application import (
    AggregateTradeStreamingDiagnostics,
    aggregate_trade_minute_states,
    compose_aggregate_trade_range,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeRangeRequest,
    ResearchDatasetRole,
    RevisionSelectionPolicy,
)
from quantos.infrastructure.binance import (
    BinanceSpotAggregateTradeDailyArchiveAdapter,
    aggregate_trade_archive_resource_urls,
)
from quantos.infrastructure.storage import ParquetAggregateTradeArchiveStore
from quantos.infrastructure.storage.aggregate_trade_catalog import AggregateTradeCatalogRebuildDiagnostics
from quantos.infrastructure.storage.aggregate_trade_minute_primitive_cache import (
    ParquetAggregateTradeMinutePrimitiveCache,
)
from tests.aggregate_trade_range_fixtures import local_catalog
from tests.unit.test_binance_aggregate_trade_archive import (
    RecordingHttpGet,
    checksum_bytes,
    csv_bytes,
    row,
)

SCHEMA_VERSION = "quantos-a1-de1-baseline-v1"
START_DATE = date(2025, 1, 1)
START_TIMESTAMP_US = 1_735_689_600_000_000
MINUTES_PER_DAY = 1440
US_PER_MINUTE = 60_000_000
FORBIDDEN_ROOTS = (
    PureWindowsPath(r"G:\QuantOS"),
    PureWindowsPath(r"G:\QuantOS-Data\research\alpha-funnel-af4b"),
    PureWindowsPath(str(REPOSITORY_ROOT)),
)


@dataclass(frozen=True)
class Parameters:
    days: int = 1
    events_per_minute: int = 25
    batch_size: int = 16384
    symbol: str = "BTCUSDT"

    def __post_init__(self) -> None:
        limits = (
            ("days", self.days, (date.max - START_DATE).days),
            ("events_per_minute", self.events_per_minute, US_PER_MINUTE),
            ("batch_size", self.batch_size, 1_000_000),
        )
        for name, value, maximum in limits:
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [1, {maximum}]")
        if self.symbol not in ("BTCUSDT", "ETHUSDT"):
            raise ValueError("symbol must be BTCUSDT or ETHUSDT")


def validate_root(root: Path) -> Path:
    """Reject protected names before filesystem access; reject links/junctions.

    A fresh leaf prevents accidental scanning/reuse of pre-existing datasets.
    Parent directories must remain under the caller's exclusive control during
    the run; this is not a sandbox against concurrent filesystem replacement.
    """
    raw = os.fspath(root)
    windows = PureWindowsPath(raw)
    if raw.startswith(("\\\\", "//")) or windows.drive.startswith("\\\\"):
        raise ValueError("network and device paths are forbidden")
    if any(
        part.endswith((".", " ")) or ":" in part
        for part in windows.parts[1:] if part not in (".", "..")
    ):
        raise ValueError("ambiguous Windows paths are forbidden")
    candidate = Path(os.path.abspath(raw))

    def reject_protected(path: Path) -> None:
        win = PureWindowsPath(str(path))
        if any(win == base or base in win.parents for base in FORBIDDEN_ROOTS):
            raise ValueError("benchmark root is inside a protected directory")
        if path == REPOSITORY_ROOT or REPOSITORY_ROOT in path.parents:
            raise ValueError("benchmark root must be external to the worktree")

    reject_protected(candidate)
    for component in (*reversed(candidate.parents), candidate):
        try:
            status = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(status.st_mode) or (
            getattr(status, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise ValueError("benchmark path must not contain links or junctions")
    resolved = candidate.resolve()
    reject_protected(resolved)
    if resolved.exists():
        raise ValueError("benchmark root must be new (must not already exist)")
    if not resolved.parent.is_dir():
        raise ValueError("benchmark root parent must already exist")
    return resolved


def synthetic_rows(parameters: Parameters, day_index: int) -> list[list[str]]:
    """Integer-only timestamps/IDs and exact decimal text; no float conversion.

    Since January 2025 the existing archive contract uses microseconds. IDs and
    underlying singleton trade IDs are consecutive across UTC days. Alternating
    buyer-maker flags exercise both taker sides even at one event per minute.
    """
    if type(day_index) is not int or not 0 <= day_index < parameters.days:
        raise ValueError("day_index is outside benchmark coverage")
    rows = []
    for minute in range(MINUTES_PER_DAY):
        absolute_minute = day_index * MINUTES_PER_DAY + minute
        for event_index in range(parameters.events_per_minute):
            index = absolute_minute * parameters.events_per_minute + event_index
            identifier = index + 1
            timestamp = (
                START_TIMESTAMP_US + absolute_minute * US_PER_MINUTE
                + event_index * US_PER_MINUTE // parameters.events_per_minute
            )
            rows.append(row(
                aggregate_trade_id=str(identifier),
                price=f"30000.{index % 10000:04d}",
                quantity=f"0.{1 + index % 100:08d}",
                first_trade_id=str(identifier),
                last_trade_id=str(identifier),
                timestamp=str(timestamp),
                buyer_is_maker="True" if index % 2 else "False",
                best_price_match="False" if index % 3 else "True",
            ))
    return rows


def publish_day(root: Path, parameters: Parameters, day_index: int):
    source_date = START_DATE + timedelta(days=day_index)
    _, _, zip_name, csv_name = aggregate_trade_archive_resource_urls(
        symbol=parameters.symbol, archive_date=source_date
    )
    output = BytesIO()
    # Existing source_zip/publish_rows fixtures use the wall clock in ZIP metadata.
    # Fix just that container metadata; reuse CSV and offline transport helpers.
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        archive.writestr(
            ZipInfo(csv_name, date_time=(2025, 1, 1, 0, 0, 0)),
            csv_bytes(synthetic_rows(parameters, day_index)),
        )
    content = output.getvalue()
    adapter = BinanceSpotAggregateTradeDailyArchiveAdapter(
        http_get=RecordingHttpGet(checksum_bytes(content, zip_name), content)
    )
    fetched = adapter.fetch_daily_archive(
        symbol=parameters.symbol,
        archive_date=source_date,
        research_role=ResearchDatasetRole.DEVELOPMENT,
    )
    publication = ParquetAggregateTradeArchiveStore(root).write(
        fetched.archive, raw_zip_bytes=fetched.raw_zip_bytes
    )
    return fetched, publication


def run_benchmark(
    root: Path, parameters: Parameters, *, minute_cache: bool = False,
    partition_certificates: bool = False,
) -> dict:
    if type(parameters) is not Parameters:
        raise ValueError("parameters must be Parameters")
    if type(minute_cache) is not bool:
        raise ValueError("minute_cache must be a boolean")
    if type(partition_certificates) is not bool:
        raise ValueError("partition_certificates must be a boolean")
    minute_cache = minute_cache or partition_certificates
    parameters.__post_init__()
    root = validate_root(root)
    root.mkdir()  # Exclusive creation: never reuse an existing benchmark run.
    result = {}

    def measured(name, action):
        wall, cpu = time.perf_counter(), time.process_time()
        value = action()
        result[f"{name}_cpu_seconds"] = time.process_time() - cpu
        result[f"{name}_wall_seconds"] = time.perf_counter() - wall
        return value

    def publish():
        for day_index in range(parameters.days):
            publish_day(root, parameters, day_index)

    total_wall, total_cpu = time.perf_counter(), time.process_time()
    measured("publication", publish)
    catalog = local_catalog(root)
    cold_catalog_diagnostics = AggregateTradeCatalogRebuildDiagnostics()
    measured("catalog_rebuild", lambda: catalog.rebuild(diagnostics=cold_catalog_diagnostics))
    request = AggregateTradeRangeRequest(
        symbol=parameters.symbol,
        start_date=START_DATE,
        end_date_exclusive=START_DATE + timedelta(days=parameters.days),
        selection_policy=RevisionSelectionPolicy.UNIQUE,
    )
    selected = measured("range_compose", lambda: compose_aggregate_trade_range(
        catalog, request, batch_size=parameters.batch_size
    ))
    cold_diagnostics = AggregateTradeStreamingDiagnostics()
    cache = ParquetAggregateTradeMinutePrimitiveCache(root) if minute_cache else None
    minutes = measured("minute_materialization", lambda: aggregate_trade_minute_states(
        catalog, selected, batch_size=parameters.batch_size,
        primitive_cache=cache, diagnostics=cold_diagnostics,
    ))
    if minute_cache:
        for clock in ("cpu", "wall"):
            result[f"minute_cache_cold_{clock}_seconds"] = result[f"minute_materialization_{clock}_seconds"]
        if partition_certificates:
            for clock in ("cpu", "wall"):
                result[f"cold_catalog_rebuild_{clock}_seconds"] = result[f"catalog_rebuild_{clock}_seconds"]
            result["cold_catalog_rebuild_diagnostics"] = asdict(cold_catalog_diagnostics)
            cold_catalog_id = catalog.view.catalog_id
            del catalog
            catalog = local_catalog(root)
            warm_catalog_diagnostics = AggregateTradeCatalogRebuildDiagnostics()
            measured("warm_catalog_rebuild", lambda: catalog.rebuild(diagnostics=warm_catalog_diagnostics))
            result["warm_catalog_rebuild_diagnostics"] = asdict(warm_catalog_diagnostics)
            if (
                catalog.view.catalog_id != cold_catalog_id
                or warm_catalog_diagnostics.canonical_events_replayed != 0
                or warm_catalog_diagnostics.certificate_hit_partition_count != parameters.days
            ):
                raise ValueError("warm catalog identity or zero-replay proof failed")
            warm_selected = measured("warm_range_compose", lambda: compose_aggregate_trade_range(
                catalog, request, batch_size=parameters.batch_size
            ))
            if warm_selected != selected:
                raise ValueError("cold/warm range manifests differ")
            selected = warm_selected
        warm_diagnostics = AggregateTradeStreamingDiagnostics()
        warm = measured("minute_cache_warm", lambda: aggregate_trade_minute_states(
            catalog, selected, batch_size=parameters.batch_size,
            primitive_cache=ParquetAggregateTradeMinutePrimitiveCache(root),
            diagnostics=warm_diagnostics,
        ))
        if (
            warm != minutes or warm.dataset_id != minutes.dataset_id
            or warm.content_sha256 != minutes.content_sha256
            or warm_diagnostics.raw_events_consumed != 0
            or warm_diagnostics.cache_hit_partition_count != parameters.days
        ):
            raise ValueError("cold/warm minute-cache parity or zero-replay proof failed")
        result["minute_cache_cold_diagnostics"] = asdict(cold_diagnostics)
        result["minute_cache_warm_diagnostics"] = asdict(warm_diagnostics)
        result["minute_cache_exact_parity"] = True
        result["warm_post_acquisition_wall_seconds"] = (
            result["warm_catalog_rebuild_wall_seconds" if partition_certificates else "catalog_rebuild_wall_seconds"]
            + result["warm_range_compose_wall_seconds" if partition_certificates else "range_compose_wall_seconds"]
            + result["minute_cache_warm_wall_seconds"]
        )
        if partition_certificates:
            result["fully_warm_post_acquisition_wall_seconds"] = result["warm_post_acquisition_wall_seconds"]
    result["total_cpu_seconds"] = time.process_time() - total_cpu
    result["total_wall_seconds"] = time.perf_counter() - total_wall
    expected_minutes = parameters.days * MINUTES_PER_DAY
    expected_events = expected_minutes * parameters.events_per_minute
    if (
        selected.total_accepted_event_count != expected_events
        or minutes.input_event_count != expected_events
        or len(minutes.states) != expected_minutes
        or minutes.zero_event_minute_count != 0
    ):
        raise ValueError("canonical output counts differ from synthetic workload")
    try:
        import numpy
        numpy_version = numpy.__version__
    except ImportError:
        numpy_version = None
    result.update(
        benchmark_schema_version=SCHEMA_VERSION,
        synthetic_only=True,
        root=str(root),
        symbol=parameters.symbol,
        day_count=parameters.days,
        start_date=START_DATE.isoformat(),
        end_date_exclusive=request.end_date_exclusive.isoformat(),
        events_per_minute=parameters.events_per_minute,
        batch_size=parameters.batch_size,
        expected_total_event_count=expected_events,
        actual_range_total_event_count=selected.total_accepted_event_count,
        minute_materialization_input_events=minutes.input_event_count,
        range_compose_input_events=selected.total_accepted_event_count,
        expected_minute_count=expected_minutes,
        actual_minute_state_count=len(minutes.states),
        range_id=selected.range_id,
        minute_state_dataset_id=minutes.dataset_id,
        minute_state_content_sha256=minutes.content_sha256,
        zero_event_minute_count=minutes.zero_event_minute_count,
        python_version=platform.python_version(),
        duckdb_version=duckdb.__version__,
        pyarrow_version=pyarrow.__version__,
        numpy_version=numpy_version,
    )
    for phase in ("range_compose", "minute_materialization"):
        elapsed = result[f"{phase}_wall_seconds"]
        result[f"{phase}_events_per_wall_second"] = (
            expected_events / elapsed if elapsed > 0 else None
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="new external run directory")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--events-per-minute", type=int, default=25)
    parser.add_argument("--batch-size", type=int, default=16384)
    parser.add_argument("--symbol", default="BTCUSDT", choices=("BTCUSDT", "ETHUSDT"))
    parser.add_argument("--minute-cache", action="store_true", help="measure O2 cold build and warm reuse")
    parser.add_argument("--partition-certificates", action="store_true",
                        help="measure O3 fresh-instance warm startup (implies --minute-cache)")
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(args.root, Parameters(
            days=args.days, events_per_minute=args.events_per_minute,
            batch_size=args.batch_size, symbol=args.symbol,
        ), minute_cache=args.minute_cache, partition_certificates=args.partition_certificates)
    except (ValueError, OSError) as error:
        parser.exit(2, f"benchmark failed: {error}\n")
    print(f"{SCHEMA_VERSION}: {result['symbol']}, {result['day_count']} day(s), "
          f"{result['actual_range_total_event_count']} events, "
          f"{result['actual_minute_state_count']} minute states; "
          f"zero-event minutes={result['zero_event_minute_count']}", file=sys.stderr)
    for phase in ("publication", "catalog_rebuild", "range_compose",
                  "minute_materialization", "total"):
        print(f"  {phase}: wall={result[f'{phase}_wall_seconds']:.6f}s "
              f"CPU={result[f'{phase}_cpu_seconds']:.6f}s", file=sys.stderr)
    if args.partition_certificates:
        for phase in ("cold_catalog_rebuild", "warm_catalog_rebuild", "warm_range_compose"):
            print(f"  {phase}: wall={result[f'{phase}_wall_seconds']:.6f}s "
                  f"CPU={result[f'{phase}_cpu_seconds']:.6f}s", file=sys.stderr)
        print(f"  warm catalog events replayed="
              f"{result['warm_catalog_rebuild_diagnostics']['canonical_events_replayed']}", file=sys.stderr)
    if args.minute_cache or args.partition_certificates:
        print(f"  minute_cache_warm: wall={result['minute_cache_warm_wall_seconds']:.6f}s "
              f"CPU={result['minute_cache_warm_cpu_seconds']:.6f}s; "
              f"raw_events_consumed={result['minute_cache_warm_diagnostics']['raw_events_consumed']}",
              file=sys.stderr)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
