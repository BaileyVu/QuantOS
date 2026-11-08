"""Resumable, causal TimesFM median-drift feature generation.

This module performs no model loading or data acquisition. Callers inject a
predictor over a completed-close prefix; only the 4/12/24h median drifts are
persisted. It is a research feature generator, not an order or risk path.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import fcntl
from pathlib import Path
import time
from typing import Callable, Sequence
from contextlib import contextmanager

from .registry import canonical_json, sha256_bytes
from .timesfm_experiment import BUILD_END_MS, BUILD_START_MS, HOUR_MS
from .timesfm_model import MODEL_ID, MODEL_REVISION, WEIGHTS_SHA256

CONTEXT = 512
HORIZONS = (4, 12, 24)
MAX_TIMEOUT_SECONDS = 86_400
ZERO_HASH = "0" * 64


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def validate_bars(bars: Sequence[tuple[int, float]]) -> list[tuple[int, float]]:
    """Validate BUILD-only completed hourly closes before any prediction."""
    result = []
    for index, row in enumerate(bars):
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise ValueError(f"bar {index} must be (timestamp_ms, close)")
        ts, close = row
        if isinstance(ts, bool) or not isinstance(ts, int) or ts % HOUR_MS:
            raise ValueError(f"bar {index} timestamp must be an hourly UTC close")
        if not BUILD_START_MS <= ts < BUILD_END_MS:
            raise ValueError(f"bar {index} is outside BUILD")
        close = _finite(close, "close")
        if close <= 0:
            raise ValueError("close must be positive")
        if result and ts - result[-1][0] != HOUR_MS:
            raise ValueError("bars must be strictly contiguous hourly closes")
        result.append((ts, close))
    return result


def drift_features(prefix: Sequence[tuple[int, float]], predictor: Callable) -> dict:
    """Predict from exactly the last 512 closes and extract median log drift."""
    if len(prefix) != CONTEXT:
        raise ValueError("predictor context must contain exactly 512 completed bars")
    ts, close = prefix[-1]
    forecast = predictor(tuple(prefix))
    if getattr(forecast, "origin_ms", None) != ts:
        raise ValueError("forecast origin must equal the latest completed close")
    medians = getattr(forecast, "median", None)
    if not isinstance(medians, dict) and not hasattr(medians, "__getitem__"):
        raise ValueError("forecast median horizons are missing")
    row = {"origin_ms": ts}
    for horizon in HORIZONS:
        try:
            median = _finite(medians[horizon], f"median_{horizon}h")
        except (KeyError, TypeError):
            raise ValueError(f"missing median horizon {horizon}") from None
        if median <= 0:
            raise ValueError("forecast medians must be positive")
        row[f"drift_{horizon}h"] = math.log(median / close)
    return row


def plan_document(*, symbol: str, manifest_sha256: str, source: dict,
                  dependencies: dict, timeout_seconds: int = MAX_TIMEOUT_SECONDS,
                  created_at: float | None = None,
                  deadline_epoch: float | None = None) -> tuple[dict, str]:
    """Build immutable provenance identity and a persisted absolute deadline."""
    if not symbol or not isinstance(symbol, str):
        raise ValueError("symbol is required")
    if len(manifest_sha256) != 64 or any(c not in "0123456789abcdef" for c in manifest_sha256):
        raise ValueError("manifest_sha256 must be lowercase SHA-256")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds must be in [1, 86400]")
    created = time.time() if created_at is None else _finite(created_at, "created_at")
    deadline = created + timeout_seconds if deadline_epoch is None else _finite(deadline_epoch, "deadline_epoch")
    if not created < deadline <= created + min(timeout_seconds, MAX_TIMEOUT_SECONDS):
        raise ValueError("deadline must be after start and within 86400 seconds")
    plan = {
        "protocol": "timesfm25_ml1h_drift_v1", "symbol": symbol,
        "manifest_sha256": manifest_sha256,
        "timestamp_semantics": "completed_close_exclusive_ms", "timeframe": "1h",
        "context": CONTEXT, "horizons_hours": list(HORIZONS),
        "feature": "log(median_future_price / completed_close)",
        "model": {"id": MODEL_ID, "revision": MODEL_REVISION,
                  "weights_sha256": WEIGHTS_SHA256, "device": "cpu"},
        "source": source, "dependencies": dependencies,
        "timeout_seconds": timeout_seconds, "created_at_epoch": created,
        "deadline_epoch": deadline,
    }
    identity = json.loads(canonical_json(plan))
    # HEAD is audit metadata; identical relevant source bytes remain resumable.
    if isinstance(identity.get("source"), dict):
        identity["source"].pop("commit", None)
    # The initial absolute timeout is immutable audit metadata, but its
    # generated timestamp must not make an exact resume appear to be a new run.
    identity.pop("created_at_epoch", None)
    identity.pop("deadline_epoch", None)
    digest = sha256_bytes(canonical_json(identity).encode())
    audit = sha256_bytes(canonical_json(plan).encode())
    return {"plan": plan, "plan_sha256": digest, "audit_sha256": audit}, digest


def persist_plan(output: Path, document: dict) -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "drift_plan.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        saved = existing.get("plan")
        if not isinstance(saved, dict):
            raise ValueError("invalid saved drift plan")
        expected, _ = plan_document(
            symbol=saved.get("symbol"), manifest_sha256=saved.get("manifest_sha256"),
            source=saved.get("source"), dependencies=saved.get("dependencies"),
            timeout_seconds=saved.get("timeout_seconds"), created_at=saved.get("created_at_epoch"),
            deadline_epoch=saved.get("deadline_epoch"))
        if (expected != existing or existing["plan_sha256"] != document["plan_sha256"]):
            raise ValueError("output directory belongs to a different or tampered frozen plan")
        return existing
    if any(item.name != ".drift.lock" for item in output.iterdir()):
        raise ValueError("refusing nonempty output without a matching drift plan")
    target.write_text(json.dumps(document, sort_keys=True, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    return document


@contextmanager
def output_lock(output: Path):
    """Acquire a nonblocking exclusive lock on one output directory."""
    output = Path(output)
    if output.exists() and not output.is_dir():
        raise ValueError("output-dir must be a directory")
    output.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output / ".drift.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another drift writer holds the output lock") from None
        yield
    finally:
        os.close(descriptor)


def _record(payload: dict, previous: str) -> dict:
    entry = {**payload, "previous_sha256": previous}
    entry["record_sha256"] = sha256_bytes(canonical_json(entry).encode())
    return entry


def read_progress(path: Path, *, plan_hash: str, expected: set[int]) -> tuple[dict[int, dict], str, float]:
    completed, previous, elapsed = {}, ZERO_HASH, 0.0
    if not path.exists():
        return completed, previous, elapsed
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            entry = json.loads(line)
            claimed = entry.get("record_sha256")
            unhashed = dict(entry)
            unhashed.pop("record_sha256", None)
            if (entry.get("previous_sha256") != previous
                    or claimed != sha256_bytes(canonical_json(unhashed).encode())):
                raise ValueError(f"progress hash-chain mismatch at line {line_number}")
            if entry.get("plan_sha256") != plan_hash:
                raise ValueError("progress belongs to a different frozen plan")
            origin = entry.get("origin_ms")
            if isinstance(origin, bool) or not isinstance(origin, int) or origin not in expected:
                raise ValueError("progress contains unknown origin")
            if origin in completed:
                raise ValueError("duplicate completed origin")
            features = entry.get("features")
            if not isinstance(features, dict) or set(features) != {"origin_ms", "drift_4h", "drift_12h", "drift_24h"}:
                raise ValueError("progress feature schema mismatch")
            if features["origin_ms"] != origin:
                raise ValueError("progress feature origin mismatch")
            for horizon in HORIZONS:
                _finite(features[f"drift_{horizon}h"], f"drift_{horizon}h")
            elapsed = _finite(entry.get("elapsed_seconds"), "elapsed_seconds")
            if elapsed < 0 or elapsed < (next(reversed(completed.values()))["elapsed_seconds"] if completed else 0):
                raise ValueError("elapsed_seconds must be monotonic and nonnegative")
            completed[origin] = {"features": features, "elapsed_seconds": elapsed}
            previous = claimed
    return completed, previous, elapsed


def generate(bars: Sequence[tuple[int, float]], predictor: Callable, *,
             output_dir: str | Path, symbol: str, manifest_sha256: str,
             source: dict, dependencies: dict,
             timeout_seconds: int = MAX_TIMEOUT_SECONDS,
             created_at_epoch: float | None = None,
             deadline_epoch: float | None = None,
             clock: Callable[[], float] = time.time) -> list[dict]:
    """Generate causal features; resumes exact completed origins only.

    Deadline is absolute in the frozen plan and elapsed time is persisted with
    every row. Supply one shared created_at_epoch/deadline_epoch pair when
    coordinating multiple symbol outputs. A predictor call already in progress
    cannot be interrupted.
    """
    rows = validate_bars(bars)
    if len(rows) < CONTEXT:
        return []
    origins = {rows[i][0] for i in range(CONTEXT - 1, len(rows))}
    output = Path(output_dir)
    with output_lock(output):
        plan_start = clock() if created_at_epoch is None else created_at_epoch
        doc, plan_hash = plan_document(
            symbol=symbol, manifest_sha256=manifest_sha256, source=source,
            dependencies=dependencies, timeout_seconds=timeout_seconds,
            created_at=plan_start, deadline_epoch=deadline_epoch)
        saved = persist_plan(output, doc)
        plan = saved["plan"]
        progress = output / "drift_progress.jsonl"
        completed, tail, elapsed_before = read_progress(progress, plan_hash=plan_hash, expected=origins)
        # A complete cache is valid output even after its generation deadline.
        if set(completed) == origins:
            return [completed[rows[i][0]]["features"] for i in range(CONTEXT - 1, len(rows))]
        if clock() >= plan["deadline_epoch"]:
            raise TimeoutError("drift experiment deadline has expired")
        started = clock()
        output_rows = []
        for index in range(CONTEXT - 1, len(rows)):
            ts = rows[index][0]
            cached = completed.get(ts)
            if cached is None:
                if clock() >= plan["deadline_epoch"]:
                    raise TimeoutError("drift experiment deadline reached")
                feature = drift_features(rows[index - CONTEXT + 1:index + 1], predictor)
                elapsed = elapsed_before + max(0.0, clock() - started)
                entry = _record({"plan_sha256": plan_hash, "origin_ms": ts,
                                 "features": feature, "elapsed_seconds": elapsed}, tail)
                with progress.open("ab") as handle:
                    handle.write((canonical_json(entry) + "\n").encode())
                    handle.flush()
                    os.fsync(handle.fileno())
                tail = entry["record_sha256"]
                completed[ts] = {"features": feature, "elapsed_seconds": elapsed}
            output_rows.append(completed[ts]["features"])
        return output_rows
