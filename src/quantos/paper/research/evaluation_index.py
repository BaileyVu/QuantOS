"""Checksummed row index for the seal-bound evaluation-data manifest.

This module reads existing public archives only. It never fetches, fills gaps,
or runs an evaluation; missing inputs remain explicit and auditable.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

from .data import Bar, FundingRate, OpenInterestMetric, parse_funding, parse_klines, parse_metrics
from .dataset import DatasetIndex, SeriesAudit, _series_audit, _window_bounds, manifest_content_hash

EVAL_START = "2024-10-01"
EVAL_END = "2026-10-28"  # inclusive day in the seal-bound manifest
EVALUATION_START = "2025-01-01"
EVALUATION_END_EXCLUSIVE = "2026-10-29"
HOUR_MS = 3_600_000
FUNDING_TOLERANCE_MS = 1_000


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _funding_api_rows(rows: Any, symbol: str, interval_hours: int) -> tuple[FundingRate, ...]:
    """Parse a public API row list, preserving event timestamps and rates."""
    if not isinstance(rows, list) or interval_hours <= 0:
        raise ValueError("funding API payload must be a list and have a September interval seed")
    result = []
    previous = None
    for item in rows:
        if not isinstance(item, dict) or not {"fundingTime", "fundingRate"}.issubset(item):
            raise ValueError("unexpected public funding JSON row")
        if item.get("symbol", symbol) != symbol:
            raise ValueError("funding API symbol mismatch")
        try:
            event_ms = int(item["fundingTime"])
            rate = Decimal(str(item["fundingRate"]))
        except (TypeError, ValueError, ArithmeticError):
            raise ValueError("invalid funding API event") from None
        if (event_ms < 0 or not rate.is_finite() or
                previous is not None and event_ms <= previous):
            raise ValueError("unordered, duplicate, or invalid funding API event")
        result.append(FundingRate(symbol, event_ms, interval_hours, rate))
        previous = event_ms
    return tuple(result)


def _json_funding(path: Path, symbol: str, interval_hours: int) -> tuple[FundingRate, ...]:
    """Parse public funding-history JSON, preserving actual event timestamps/rates."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("data", raw) if isinstance(raw, dict) else raw
    return _funding_api_rows(rows, symbol, interval_hours)


def _validate_window(manifest: dict) -> None:
    window = manifest.get("window", {})
    expected = {"start": EVAL_START, "end": EVAL_END,
                "evaluation_start": EVALUATION_START,
                "evaluation_end_exclusive": EVALUATION_END_EXCLUSIVE}
    if any(window.get(key) != value for key, value in expected.items()):
        raise ValueError("evaluation manifest has an unexpected training/evaluation boundary")
    warmup = manifest.get("warmup_reuse", {})
    if (warmup.get("from") != "2024-10-01T00:00:00Z"
            or warmup.get("through_exclusive") != "2025-01-01T00:00:00Z"
            or warmup.get("acquire_again") is not False or not warmup.get("manifest")):
        raise ValueError("evaluation manifest has an unexpected warmup reuse contract")


def _funding_audit(symbol: str, rows: list[FundingRate]) -> SeriesAudit:
    ordered = sorted(rows, key=lambda x: x.event_ms)
    times = [x.event_ms for x in ordered]
    duplicates = tuple(sorted(t for t, count in Counter(times).items() if count > 1))
    gaps = []
    for left, right, a, b in zip(times, times[1:], ordered, ordered[1:]):
        delta = right - left
        expected = (a.interval_hours * HOUR_MS, b.interval_hours * HOUR_MS)
        if min(abs(delta - value) for value in expected) > FUNDING_TOLERANCE_MS:
            gaps.append((left, right))
    return SeriesAudit(symbol, "fundingRate", len(rows), duplicates, tuple(gaps), 0)


def _task_rows(manifest: dict) -> tuple[dict, ...]:
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("evaluation manifest tasks must be a list")
    ids = [x.get("id") for x in tasks]
    if any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("evaluation task IDs must be unique nonempty strings")
    return tuple(tasks)


def _verify_warmup(task: dict, training_manifest: dict, training_sha: str) -> dict:
    if task.get("warmup_source_manifest_sha256") != training_sha:
        raise ValueError("warmup archive does not bind to the supplied training manifest")
    source_id = task.get("warmup_source_task_id")
    source = next((row for row in training_manifest.get("tasks", ()) if row.get("id") == source_id), None)
    if source is None or source.get("status") != "downloaded":
        raise ValueError(f"warmup source task is absent or unavailable: {source_id}")
    for field in ("dataset", "symbol", "period", "interval", "sha256"):
        if task.get(field) != source.get(field):
            raise ValueError(f"warmup source metadata mismatch for {source_id}: {field}")
    if Path(task.get("cached_path", "")).resolve() != Path(source.get("cached_path", "")).resolve():
        raise ValueError(f"warmup archive must reuse its exact source path: {source_id}")
    return source


def build_evaluation_index(manifest_path: str | Path, *,
                           training_manifest_path: str | Path | None = None,
                           training_overlay_manifest: str | Path | None = None,
                           symbols: tuple[str, ...] | None = None) -> DatasetIndex:
    """Build immutable evaluation rows from the exact evaluation manifest.

    Warmup-source references are verified against the primary training
    manifest. Optional daily repairs are accepted only through their saved,
    checksummed overlay manifest and only for exact Q4-2024 timestamps.
    """
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    from .evaluation_data import validate_seal_from_plan
    validate_seal_from_plan(manifest)
    _validate_window(manifest)
    window = manifest["window"]
    universe = manifest.get("universe", {}).get("symbols")
    if not isinstance(universe, list) or not universe or len(universe) != len(set(universe)):
        raise ValueError("evaluation manifest needs a unique frozen symbol universe")
    wanted = set(symbols) if symbols is not None else set(universe)
    if not wanted or not wanted <= set(universe):
        raise ValueError("requested symbols must be a nonempty subset of the frozen evaluation universe")
    tasks = _task_rows(manifest)
    bounds = _window_bounds(EVAL_START, EVAL_END)

    training_manifest: dict | None = None
    training_sha: str | None = None
    source_by_id: dict[str, dict] = {}
    if training_manifest_path is not None:
        training_path = Path(training_manifest_path)
        training_bytes = training_path.read_bytes()
        training_sha = hashlib.sha256(training_bytes).hexdigest()
        training_manifest = json.loads(training_bytes)
        source_by_id = {row.get("id"): row for row in training_manifest.get("tasks", ())}
    if any(task.get("status") == "downloaded" and
           (task.get("warmup_source_task_id") is not None or
            task.get("warmup_source_manifest_sha256") is not None) for task in tasks) and training_manifest is None:
        raise ValueError("warmup reuse requires the bound primary training manifest")

    grouped: dict[str, dict[str, list]] = {
        "klines": defaultdict(list), "markPriceKlines": defaultdict(list),
        "fundingRate": defaultdict(list), "metrics": defaultdict(list),
    }
    unavailable: list[dict] = []
    accepted: list[dict] = []
    # Ensure the complete September funding archive is parsed before any
    # October API payload needs its cadence seed, regardless of manifest order.
    tasks = tuple(sorted(tasks, key=lambda task: task.get("evaluation_source") == "public_funding_api"))
    for task in tasks:
        if task.get("symbol") not in wanted:
            continue
        dataset, status = task.get("dataset"), task.get("status")
        if dataset not in grouped:
            raise ValueError(f"unsupported evaluation dataset: {dataset}")
        if status != "downloaded":
            unavailable.append({key: task.get(key) for key in
                ("id", "dataset", "symbol", "period", "status", "phase", "availability_class", "error")})
            continue
        if task.get("warmup_source_task_id") is not None or task.get("warmup_source_manifest_sha256") is not None:
            assert training_manifest is not None and training_sha is not None
            _verify_warmup(task, training_manifest, training_sha)
        cached = Path(task["cached_path"])
        if not cached.is_file():
            raise FileNotFoundError(f"evaluation archive is missing: {cached}")
        actual_sha = _sha(cached)
        if actual_sha != task.get("sha256"):
            raise ValueError(f"evaluation archive checksum mismatch: {task.get('id')}")
        symbol = task["symbol"]
        if task.get("evaluation_source") == "public_funding_api":
            if dataset != "fundingRate" or not str(task.get("period", "")).startswith("2026-10"):
                raise ValueError("public funding API source is only accepted for October 2026 funding")
            september = [row for row in grouped["fundingRate"][symbol]
                         if datetime.fromtimestamp(row.event_ms / 1000, timezone.utc).strftime("%Y-%m") == "2026-09"]
            if not september:
                raise ValueError(f"October funding API lacks September interval seed for {symbol}")
            last_september = max(september, key=lambda x: x.event_ms)
            parsed = _json_funding(cached, symbol, last_september.interval_hours)
            timestamps = [row.event_ms for row in parsed]
        elif dataset in {"klines", "markPriceKlines"}:
            interval = task.get("interval")
            if interval != "1h":
                raise ValueError("evaluation index requires 1h trade and mark bars")
            parsed = parse_klines(cached, symbol, interval)
            timestamps = [row.open_ms for row in parsed]
        elif dataset == "fundingRate":
            parsed = parse_funding(cached, symbol)
            timestamps = [row.event_ms for row in parsed]
        else:
            parsed = parse_metrics(cached, symbol)
            timestamps = [row.label_ms for row in parsed]
        if any(not (bounds[0] <= timestamp < bounds[1]) for timestamp in timestamps):
            raise ValueError(f"archive row outside the sealed data window: {task.get('id')}")
        grouped[dataset][symbol].extend(parsed)
        accepted.append({"id": task.get("id"), "sha256": actual_sha,
                         "phase": task.get("phase"), "evaluation_source": task.get("evaluation_source"),
                         "warmup_source_task_id": task.get("warmup_source_task_id")})

    overlay_hash = None
    overlay_path = None
    overlay_rows = 0
    if training_overlay_manifest is not None:
        overlay_file = Path(training_overlay_manifest)
        repair = json.loads(overlay_file.read_text(encoding="utf-8"))
        if repair.get("status") != "complete":
            raise ValueError("training daily repair overlay is not complete")
        if training_manifest is None:
            raise ValueError("training repair overlay requires its primary source manifest")
        if repair.get("window") != ["2021-10-01", "2024-12-31"]:
            raise ValueError("training repair overlay has an unexpected window")
        if repair.get("base_content_hash") != manifest_content_hash(training_manifest):
            raise ValueError("training repair overlay does not match source manifest")
        repair_digest = hashlib.sha256()
        q4_lo, q4_hi = _window_bounds("2024-10-01", "2024-12-31")
        for row in sorted(repair.get("tasks", []), key=lambda x: x.get("id", "")):
            symbol, dataset = row.get("symbol"), row.get("dataset")
            if symbol not in wanted or dataset not in {"klines", "markPriceKlines"}:
                continue
            expected_times = {x for x in row.get("missing_open_ms", ()) if q4_lo <= x < q4_hi}
            if not expected_times:
                continue
            if row.get("status") != "downloaded":
                unavailable.append({"id": row.get("id"), "dataset": dataset, "symbol": symbol,
                                    "period": row.get("period"), "status": row.get("status"),
                                    "phase": "warmup_repair"})
                continue
            cached = Path(row["cached_path"])
            digest = _sha(cached)
            if digest != row.get("sha256"):
                raise ValueError(f"warmup repair checksum mismatch: {row.get('id')}")
            bars = parse_klines(cached, symbol, "1h")
            current = {bar.open_ms: bar for bar in grouped[dataset].get(symbol, [])}
            additions = []
            found = set()
            for bar in bars:
                if bar.open_ms in expected_times:
                    if bar.open_ms in current:
                        raise ValueError(f"daily repair duplicates existing warmup row: {symbol}@{bar.open_ms}")
                    additions.append(bar)
                    found.add(bar.open_ms)
                elif bar.open_ms in current:
                    if current[bar.open_ms] != bar:
                        raise ValueError(f"conflicting daily repair row: {symbol}@{bar.open_ms}")
                else:
                    raise ValueError(f"daily repair contains unrequested warmup row: {symbol}@{bar.open_ms}")
            if found != expected_times:
                raise ValueError(f"daily repair did not supply exact Q4 warmup gaps: {row.get('id')}")
            grouped[dataset][symbol].extend(additions)
            overlay_rows += len(additions)
            repair_digest.update(f"{row.get('id')}:{digest}\n".encode())
        overlay_hash = repair_digest.hexdigest()
        overlay_path = str(overlay_file.resolve())

    audits: list[SeriesAudit] = []
    for dataset, by_symbol in grouped.items():
        for symbol, rows in by_symbol.items():
            if dataset == "klines" or dataset == "markPriceKlines":
                stamp = lambda x: x.open_ms
                audit = _series_audit(symbol, dataset, rows, stamp)
            elif dataset == "fundingRate":
                audit = _funding_audit(symbol, rows)
            else:
                stamp = lambda x: x.label_ms
                audit = _series_audit(symbol, dataset, rows, stamp)
            audits.append(audit)
            rows.sort(key=lambda x: x.open_ms if hasattr(x, "open_ms") else
                      x.event_ms if hasattr(x, "event_ms") else x.label_ms)

    manifest_fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    content = hashlib.sha256()
    content.update(manifest_fingerprint.encode())
    content.update(json.dumps(sorted(accepted, key=lambda x: x["id"]), sort_keys=True,
        separators=(",", ":")).encode())
    content.update((training_sha or "").encode())
    content.update((overlay_hash or "").encode())
    # Keep ambiguous duplicate timestamps visible in audit, and absent from O(1)
    # lookup so no row is silently selected by dict insertion order.
    def unique_map(values: dict[str, list[Bar]]) -> dict[str, dict[int, Bar]]:
        result = {}
        for symbol, rows in values.items():
            counts = Counter(row.open_ms for row in rows)
            result[symbol] = {row.open_ms: row for row in rows if counts[row.open_ms] == 1}
        return result
    return DatasetIndex(
        bars={k: tuple(v) for k, v in grouped["klines"].items()},
        marks={k: tuple(v) for k, v in grouped["markPriceKlines"].items()},
        funding={k: tuple(v) for k, v in grouped["fundingRate"].items()},
        metrics={k: tuple(v) for k, v in grouped["metrics"].items()},
        audit=tuple(audits), bar_by_open_ms=unique_map(grouped["klines"]),
        mark_by_open_ms=unique_map(grouped["markPriceKlines"]),
        window=(EVAL_START, EVAL_END), content_hash=content.hexdigest(),
        unavailable_partitions=tuple(unavailable), overlay_manifest=overlay_path,
        overlay_hash=overlay_hash, overlay_rows=overlay_rows)
