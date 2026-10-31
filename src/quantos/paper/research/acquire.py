"""Manifest-driven, resumable Binance public archive acquisition.

The acquisition CLI only handles the training window supplied to `plan`.
It records unavailable partitions explicitly and never constructs data for
missing bars. Raw CSV/ZIP archives remain in the ignored portfolio cache.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Iterable

from .data import ArchiveRef, ArchiveStore, archive_ref

MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
          "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT")
DEFAULT_START = date(2021, 10, 1)
DEFAULT_END = date(2024, 12, 31)
MAX_WORKERS = 4


def load_universe(path: str | Path) -> tuple[tuple[str, ...], str]:
    """Read the literal `UNIVERSE` assignment without importing its dependencies."""
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=str(path))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("universe source must be valid UTF-8 Python") from exc
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "UNIVERSE"
                for target in node.targets):
            value = node.value
            break
    if value is None:
        raise ValueError("source has no top-level UNIVERSE assignment")
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "split":
        if len(value.args) != 0 or value.keywords or not isinstance(value.func.value, ast.Constant):
            raise ValueError("UNIVERSE split expression must be a literal string")
        symbols = value.func.value.value.split()
    else:
        try:
            symbols = ast.literal_eval(value)
        except (ValueError, TypeError):
            raise ValueError("UNIVERSE must be a literal tuple/list or whitespace string") from None
        if isinstance(symbols, str):
            symbols = symbols.split()
    if not isinstance(symbols, (tuple, list)) or not symbols:
        raise ValueError("UNIVERSE must contain symbols")
    if any(not isinstance(symbol, str) or not symbol.endswith("USDT")
           or not symbol.isascii() or not symbol.isalnum() or symbol != symbol.upper()
           for symbol in symbols):
        raise ValueError("UNIVERSE symbols must be uppercase USDT pairs")
    if len(set(symbols)) != len(symbols):
        raise ValueError("UNIVERSE contains duplicates")
    return tuple(symbols), digest


def _months(start: date, end: date) -> tuple[str, ...]:
    month, year = start.month, start.year
    result = []
    while (year, month) <= (end.year, end.month):
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return tuple(result)


def _days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _item(ref: ArchiveRef, status: str = "available") -> dict:
    key = ":".join((ref.dataset, ref.symbol, ref.interval or "-", ref.period))
    return {"id": key, "dataset": ref.dataset, "symbol": ref.symbol,
            "interval": ref.interval, "period": ref.period, "url": ref.url,
            "expected_bytes": ref.size_bytes, "status": status,
            "sha256": None, "cached_path": None, "actual_bytes": None,
            "error": None}


def _listed_items(store: ArchiveStore, dataset: str, symbols: Iterable[str],
                  start: date, end: date, *, interval: str | None = None,
                  granularity: str) -> tuple[list[dict], list[dict]]:
    if granularity == "monthly":
        expected = _months(start, end)
    elif granularity == "daily":
        expected = tuple(day.isoformat() for day in _days(start, end))
    else:
        raise ValueError("unsupported granularity")
    items: list[dict] = []
    audit_errors: list[dict] = []
    for symbol in symbols:
        try:
            refs = store.list_archives(dataset, symbol, interval, start, end,
                                       granularity=granularity)
            found = {ref.period: ref for ref in refs}
            ordered_found = sorted(found)
            first_found = ordered_found[0] if ordered_found else None
            last_found = ordered_found[-1] if ordered_found else None
            for period in expected:
                ref = found.get(period)
                if ref is None:
                    ref = archive_ref(dataset, symbol, period, interval)
                    entry = _item(ref, "missing")
                    if first_found is None or period < first_found:
                        entry["availability_class"] = "before_first_partition"
                    elif last_found is not None and period > last_found:
                        entry["availability_class"] = "after_last_partition"
                    else:
                        entry["availability_class"] = "internal_archive_gap"
                    items.append(entry)
                else:
                    items.append(_item(ref, "available"))
        except Exception as exc:
            audit_errors.append({"dataset": dataset, "symbol": symbol,
                                 "interval": interval, "error": f"{type(exc).__name__}: {exc}"})
            # Preserve uncertainty per expected source partition. These cannot
            # be downloaded until the listing succeeds and remain visible.
            for period in expected:
                ref = archive_ref(dataset, symbol, period, interval)
                entry = _item(ref, "unverified")
                entry["error"] = "coverage listing failed"
                items.append(entry)
    return items, audit_errors


def build_plan(universe_file: str | Path, start: date = DEFAULT_START,
               end: date = DEFAULT_END, *, store: ArchiveStore | None = None,
               audit_workers: int = MAX_WORKERS) -> dict:
    if end < start:
        raise ValueError("end date precedes start date")
    if end > DEFAULT_END:
        raise ValueError("planning beyond the approved training window is disabled")
    if not 1 <= audit_workers <= MAX_WORKERS:
        raise ValueError("audit workers must be from 1 to 4")
    symbols, source_sha = load_universe(universe_file)
    store = store or ArchiveStore()
    tasks = []
    audit_errors = []
    queries = []
    for dataset in ("klines", "markPriceKlines"):
        for symbol in symbols:
            queries.append((dataset, (symbol,), start, end, "1h", "monthly"))
    for symbol in symbols:
        queries.append(("fundingRate", (symbol,), start, end, None, "monthly"))
    for symbol in MAJORS:
        queries.append(("metrics", (symbol,), start, end, None, "daily"))
    def run(query):
        dataset, subset, lo, hi, interval, granularity = query
        return _listed_items(store, dataset, subset, lo, hi,
                             interval=interval, granularity=granularity)
    with ThreadPoolExecutor(max_workers=audit_workers) as pool:
        futures = [pool.submit(run, query) for query in queries]
        for future in as_completed(futures):
            items, errors = future.result()
            tasks.extend(items)
            audit_errors.extend(errors)
    tasks.sort(key=lambda task: (task["dataset"], task["symbol"], task["period"]))
    summary = _summary(tasks)
    return {
        "schema_version": 1,
        "created_utc": date.today().isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "research_market": "Binance USD-M perpetual public archives",
        "universe": {"source_file": str(Path(universe_file).resolve()),
                     "sha256": source_sha, "symbols": list(symbols),
                     "selection_note": "Original round-4 cohort is a hindsight survivor cohort; source file describes Binance Spot, while this research acquisition uses USD-M Futures."},
        "open_interest_symbols": list(MAJORS),
        "metrics_time_policy": "Raw create_time label retained. conservative_available_ms is label+1h proxy only, not an exchange publication timestamp.",
        "summary": summary,
        "listing_errors": audit_errors,
        "tasks": tasks,
    }


def _summary(tasks: list[dict]) -> dict:
    result = {"tasks": len(tasks), "available": 0, "missing": 0,
              "unverified": 0, "downloaded": 0, "failed": 0,
              "expected_bytes": 0, "unknown_size": 0, "by_dataset": {},
              "missing_classification": {}}
    for task in tasks:
        status = task["status"]
        if status in result:
            result[status] += 1
        if status == "error":
            result["failed"] += 1
        availability_class = task.get("availability_class")
        if status == "missing" and availability_class:
            result["missing_classification"][availability_class] = result["missing_classification"].get(availability_class, 0) + 1
        size = task.get("expected_bytes")
        if size is None:
            result["unknown_size"] += 1
        else:
            result["expected_bytes"] += size
        group = result["by_dataset"].setdefault(task["dataset"],
                                                {"tasks": 0, "available": 0, "missing": 0, "unverified": 0,
                                                 "expected_bytes": 0, "unknown_size": 0})
        group["tasks"] += 1
        if status in group:
            group[status] += 1
        if size is None:
            group["unknown_size"] += 1
        else:
            group["expected_bytes"] += size
    return result


def _write_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def save_plan(path: str | Path, plan: dict) -> dict:
    path = Path(path)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        old_tasks = {item["id"]: item for item in old.get("tasks", [])}
        for item in plan["tasks"]:
            previous = old_tasks.get(item["id"])
            if previous and previous.get("status") == "downloaded":
                item.update({key: previous.get(key) for key in
                             ("status", "sha256", "cached_path", "actual_bytes", "error")})
            elif previous and previous.get("status") == "error" and item["status"] == "available":
                item["error"] = None
        plan["summary"] = _summary(plan["tasks"])
    _write_json(path, plan)
    return plan


def download_batch(manifest_path: str | Path, *, limit: int = 32,
                   workers: int = MAX_WORKERS,
                   store: ArchiveStore | None = None,
                   datasets: set[str] | None = None,
                   symbols: set[str] | None = None,
                   periods: set[str] | None = None) -> dict:
    if limit < 1 or not 1 <= workers <= MAX_WORKERS:
        raise ValueError("limit must be positive and workers must be from 1 to 4")
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    store = store or ArchiveStore()
    available = [task for task in manifest["tasks"]
                 if task["status"] in {"available", "error"}
                 and (datasets is None or task["dataset"] in datasets)
                 and (symbols is None or task["symbol"] in symbols)
                 and (periods is None or task["period"] in periods)]
    batch = available[:limit]
    lock = threading.Lock()
    progress_path = Path(str(path) + ".progress.jsonl")
    completed = 0
    def fetch_one(task: dict) -> dict:
        try:
            cache_path = store.fetch(task["dataset"], task["symbol"], task["period"], task["interval"])
            digest = hashlib.sha256(cache_path.read_bytes()).hexdigest()
            return {"status": "downloaded", "sha256": digest,
                    "cached_path": str(cache_path), "actual_bytes": cache_path.stat().st_size,
                    "error": None}
        except Exception as exc:
            return {"status": "error", "sha256": None, "cached_path": None,
                    "actual_bytes": None, "error": f"{type(exc).__name__}: {exc}"}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, task): task for task in batch}
        for future in as_completed(futures):
            task = futures[future]
            result = future.result()
            with lock:
                task.update(result)
                manifest["summary"] = _summary(manifest["tasks"])
                event = {"event": "archive", "id": task["id"], **result}
                progress_path.parent.mkdir(parents=True, exist_ok=True)
                with progress_path.open("a", encoding="utf-8") as log:
                    log.write(json.dumps(event, sort_keys=True) + "\n")
                    log.flush()
                completed += 1
                if completed % 100 == 0:
                    _write_json(path, manifest)
                show_progress = completed == 1 or completed % 100 == 0 or completed == len(batch)
                progress_snapshot = {"event": "batch_progress", "completed": completed,
                                     "batch_size": len(batch), "summary": manifest["summary"]}
            if show_progress:
                print(json.dumps(progress_snapshot, sort_keys=True), flush=True)
    _write_json(path, manifest)
    return manifest


def iter_cached_archives(manifest_path: str | Path, *, dataset: str,
                         symbol: str | None = None,
                         interval: str | None = None):
    """Yield verified downloaded manifest rows for downstream preprocessing."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    for task in manifest.get("tasks", []):
        if task.get("status") != "downloaded" or task.get("dataset") != dataset:
            continue
        if symbol is not None and task.get("symbol") != symbol:
            continue
        if interval is not None and task.get("interval") != interval:
            continue
        cached = Path(task["cached_path"])
        if not cached.is_file():
            raise FileNotFoundError(f"manifest cache file is missing: {cached}")
        digest = hashlib.sha256(cached.read_bytes()).hexdigest()
        if digest != task.get("sha256"):
            raise ValueError(f"manifest cache checksum mismatch: {cached}")
        yield task


def _default_manifest(start: date, end: date) -> Path:
    return Path("data/portfolio/manifests") / f"train_{start.isoformat()}_to_{end.isoformat()}.json"


def _print_summary(manifest: dict) -> None:
    print(json.dumps({"window": manifest["window"],
                      "universe_count": len(manifest["universe"]["symbols"]),
                      "universe_sha256": manifest["universe"]["sha256"],
                      "summary": manifest["summary"],
                      "listing_errors": len(manifest["listing_errors"])}, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan", help="audit expected training archive partitions")
    plan_parser.add_argument("--universe-file", required=True)
    plan_parser.add_argument("--start", default=DEFAULT_START.isoformat())
    plan_parser.add_argument("--end", default=DEFAULT_END.isoformat())
    plan_parser.add_argument("--manifest")
    plan_parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    dl_parser = sub.add_parser("download", help="fetch a bounded resumable batch")
    dl_parser.add_argument("--manifest", required=True)
    dl_parser.add_argument("--limit", type=int, default=32)
    dl_parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    dl_parser.add_argument("--dataset", action="append", dest="datasets")
    dl_parser.add_argument("--symbol", action="append", dest="symbols")
    dl_parser.add_argument("--period", action="append", dest="periods")
    status_parser = sub.add_parser("status", help="summarize an acquisition manifest")
    status_parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        plan = build_plan(args.universe_file, start, end, audit_workers=args.workers)
        path = Path(args.manifest) if args.manifest else _default_manifest(start, end)
        plan = save_plan(path, plan)
        _print_summary(plan)
        print(f"manifest: {path}")
        return 0 if not plan["listing_errors"] else 2
    if args.command == "download":
        manifest = download_batch(
            args.manifest, limit=args.limit, workers=args.workers,
            datasets=set(args.datasets) if args.datasets else None,
            symbols=set(args.symbols) if args.symbols else None,
            periods=set(args.periods) if args.periods else None)
        _print_summary(manifest)
        return 0 if not manifest["listing_errors"] else 2
    if args.command == "status":
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        _print_summary(manifest)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
