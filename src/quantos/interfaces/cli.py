"""Local runtime and research-data operator CLI."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys
import tempfile

from quantos.application import (
    AggregateTradeRangeCompositionError,
    canonical_range_manifest_bytes,
    compose_aggregate_trade_range,
    run,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeRangeRequest,
    ExactAggregateTradeRevision,
    RevisionSelectionPolicy,
    aggregate_trade_archive_manifest_id,
)
from quantos.domain.common import require_v1_symbol
from quantos.infrastructure.configuration import ConfigurationError, load_config
from quantos.infrastructure.logging import configure_logging
from quantos.infrastructure.storage import (
    AggregateTradeCatalogError,
    LocalAggregateTradeArchiveCatalog,
)


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use canonical YYYY-MM-DD")
    return parsed


def _exact_revision(value: str) -> ExactAggregateTradeRevision:
    parts = value.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "exact revision must be DATE:MANIFEST_ID:SOURCE_REVISION_ID"
        )
    try:
        return ExactAggregateTradeRevision(
            source_date=_date(parts[0]),
            manifest_id=parts[1],
            source_revision_id=parts[2],
        )
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the QuantOS V1 runtime.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/default.toml"),
        help="Path to a QuantOS TOML configuration file.",
    )
    commands = parser.add_subparsers(dest="command")
    aggregate_trades = commands.add_parser(
        "aggregate-trades", help="Inspect immutable research event archives."
    )
    operations = aggregate_trades.add_subparsers(
        dest="aggregate_trade_operation", required=True
    )
    list_parser = operations.add_parser("list")
    list_parser.add_argument("--symbol")
    revisions = operations.add_parser("revisions")
    revisions.add_argument("symbol")
    revisions.add_argument("source_date", type=_date)
    inspect = operations.add_parser("inspect")
    inspect.add_argument("manifest_id")
    compose = operations.add_parser("compose")
    compose.add_argument("symbol")
    compose.add_argument("start_date", type=_date)
    compose.add_argument("end_date_exclusive", type=_date)
    compose.add_argument(
        "--policy",
        choices=tuple(policy.value for policy in RevisionSelectionPolicy),
        default=RevisionSelectionPolicy.UNIQUE.value,
    )
    compose.add_argument(
        "--exact",
        action="append",
        type=_exact_revision,
        default=[],
        help="DATE:MANIFEST_ID:SOURCE_REVISION_ID; repeat once per UTC date.",
    )
    compose.add_argument("--output", type=Path)
    return parser


def _json(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _write_immutable(path: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise AggregateTradeRangeCompositionError(
                    "range manifest output already contains different bytes: "
                    f"{path}"
                )
    except OSError as error:
        raise AggregateTradeRangeCompositionError(
            f"cannot save range manifest {path}: {error}"
        ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                active_error = sys.exception()
                if active_error is not None:
                    active_error.add_note(
                        "range-manifest temporary cleanup also failed: "
                        f"{cleanup_error}"
                    )
                else:
                    raise AggregateTradeRangeCompositionError(
                        "cannot remove range-manifest temporary file: "
                        f"{cleanup_error}"
                    ) from cleanup_error


def _aggregate_trade_command(args: argparse.Namespace, data_dir: Path) -> int:
    catalog = LocalAggregateTradeArchiveCatalog(
        data_dir,
        provider="binance",
        market="spot",
        event_family="aggregate_trade",
    )
    view = catalog.rebuild()
    operation = args.aggregate_trade_operation
    if operation == "list":
        entries = view.entries
        if args.symbol is not None:
            require_v1_symbol(args.symbol)
            entries = tuple(
                entry
                for entry in entries
                if entry.logical_partition.symbol == args.symbol
            )
        print(
            _json(
                {
                    "catalog_id": view.catalog_id,
                    "entries": [entry.as_operator_dict() for entry in entries],
                    "event": "aggregate_trade_catalog",
                }
            )
        )
        return 0
    if operation == "revisions":
        revisions = view.revisions(
            symbol=args.symbol, source_date=args.source_date
        )
        print(
            _json(
                {
                    "event": "aggregate_trade_revisions",
                    "revisions": [
                        view.entry_for_manifest_id(
                            aggregate_trade_archive_manifest_id(manifest)
                        ).as_operator_dict()
                        for manifest in revisions
                    ],
                    "source_date": args.source_date.isoformat(),
                    "symbol": args.symbol,
                }
            )
        )
        return 0
    if operation == "inspect":
        entry = view.entry_for_manifest_id(args.manifest_id)
        print(_json({"entry": entry.as_operator_dict(), "event": "aggregate_trade_revision"}))
        return 0
    if operation == "compose":
        policy = RevisionSelectionPolicy(args.policy)
        request = AggregateTradeRangeRequest(
            symbol=args.symbol,
            start_date=args.start_date,
            end_date_exclusive=args.end_date_exclusive,
            selection_policy=policy,
            exact_revisions=tuple(args.exact),
        )
        manifest = compose_aggregate_trade_range(catalog, request)
        payload = canonical_range_manifest_bytes(manifest)
        if args.output is not None:
            _write_immutable(args.output, payload)
        sys.stdout.buffer.write(payload)
        return 0
    raise AggregateTradeRangeCompositionError("unknown aggregate-trade operation")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigurationError as error:
        print(
            _json({"event": "configuration_error", "error": str(error)}),
            file=sys.stderr,
        )
        return 2
    try:
        if args.command == "aggregate-trades":
            return _aggregate_trade_command(args, config.data_dir)
    except (AggregateTradeCatalogError, AggregateTradeRangeCompositionError, TypeError, ValueError) as error:
        print(
            _json({"event": "operator_error", "error": str(error)}),
            file=sys.stderr,
        )
        return 2
    logger = configure_logging(config.log_level)
    return run(logger, config.log_context())
