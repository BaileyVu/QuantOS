"""Build and fetch exact daily archive repairs from a saved row-gap audit.

Repairs are stored as separate checksum-addressed daily ZIPs. Monthly sources
are never rewritten; the dataset index can apply this manifest as an overlay.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .data import ArchiveStore, parse_klines
from .dataset import manifest_content_hash

HOUR_MS = 3_600_000
MAX_WORKERS = 4
DATASETS = {"klines", "markPriceKlines"}


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".repair-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
        Path(temp_name).replace(path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def tasks_from_audit(audit: dict) -> list[dict]:
    """Derive only absent hourly timestamps present in the saved gap pairs."""
    by_day: dict[tuple[str, str, str], set[int]] = {}
    for series in audit.get("series", []):
        dataset = series.get("dataset")
        if dataset not in DATASETS:
            continue
        for left, right in series.get("gaps", []):
            if right <= left or (right - left) % HOUR_MS:
                raise ValueError(f"invalid hourly gap pair for {series['symbol']} {dataset}")
            for opened in range(left + HOUR_MS, right, HOUR_MS):
                day = datetime.fromtimestamp(opened / 1000, timezone.utc).date().isoformat()
                by_day.setdefault((dataset, series["symbol"], day), set()).add(opened)
    tasks = []
    for (dataset, symbol, day), times in sorted(by_day.items()):
        tasks.append({"id": f"{dataset}:{symbol}:1h:{day}", "dataset": dataset,
                      "symbol": symbol, "interval": "1h", "period": day,
                      "status": "unlisted", "missing_open_ms": sorted(times),
                      "cached_path": None, "sha256": None, "rows": None,
                      "error": None})
    return tasks


def _validate_daily(task: dict, path: Path) -> tuple[str, int]:
    bars = parse_klines(path, task["symbol"], "1h")
    expected = set(task["missing_open_ms"])
    observed = {bar.open_ms for bar in bars}
    if len(observed) != len(bars):
        raise ValueError("daily repair archive contains duplicate timestamps")
    if not expected.issubset(observed):
        raise ValueError(f"repair archive misses {len(expected - observed)} required timestamps")
    day_start = int(datetime.combine(date.fromisoformat(task["period"]), datetime.min.time(), timezone.utc).timestamp() * 1000)
    if any(not day_start <= timestamp < day_start + 24 * HOUR_MS for timestamp in observed):
        raise ValueError("daily archive contains a row outside its named UTC day")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, len(bars)


def build_repair_manifest(audit_path: str | Path = "data/portfolio/audits/train_2021-10-01_to_2024-12-31.json",
                          output: str | Path = "data/portfolio/manifests/daily_repairs_2021-10-01_to_2024-12-31.json",
                          *, store: ArchiveStore | None = None,
                          base_manifest_path: str | Path = "data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json",
                          workers: int = MAX_WORKERS) -> dict:
    """List and checksum-fetch only daily archives needed by audited gaps.

    A progress manifest is atomically saved after every listing/download result,
    so interruption is resumable without refetching completed tasks.
    """
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError("workers must be between 1 and 4")
    audit_path, output = Path(audit_path), Path(output)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    base_manifest_path = Path(base_manifest_path)
    base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    base_window = base_manifest.get("window", {})
    if [base_window.get("start"), base_window.get("end")] != audit.get("window"):
        raise ValueError("saved audit window differs from source manifest")
    store = store or ArchiveStore()
    prior = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    base_hash = manifest_content_hash(base_manifest)
    source_audit_hash = audit["content_hash"]
    prior_audit_hash = prior.get("source_audit_hash", prior.get("base_content_hash"))
    if prior and prior_audit_hash != source_audit_hash:
        raise ValueError("repair manifest belongs to a different base audit")
    tasks = {x["id"]: x for x in prior.get("tasks", [])}
    for item in tasks_from_audit(audit):
        tasks.setdefault(item["id"], item)
    ordered = [tasks[key] for key in sorted(tasks)]

    def persist(status: str) -> None:
        _atomic_json(output, {"schema_version": 1,
                              "window": audit["window"],
                              "base_manifest_path": str(base_manifest_path.resolve()),
                              "base_audit_path": str(audit_path.resolve()),
                              "base_content_hash": base_hash,
                              "source_audit_hash": source_audit_hash,
                              "repair_policy": "daily archives overlay exact timestamps from saved gap audit; monthly raw blobs immutable; duplicates/conflicts fail closed",
                              "status": status, "tasks": ordered})

    # Resume already listed/downloaded tasks; verify cached archive bytes before reuse.
    for task in ordered:
        if task["status"] == "downloaded":
            path = Path(task["cached_path"])
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == task.get("sha256"):
                continue
            task.update(status="available", error="cached daily archive missing or checksum mismatch")

    def list_one(task: dict) -> tuple[dict, bool]:
        day = date.fromisoformat(task["period"])
        refs = store.list_archives(task["dataset"], task["symbol"], "1h",
                                   day, day, granularity="daily")
        return task, any(ref.period == task["period"] for ref in refs)

    pending = [task for task in ordered if task["status"] == "unlisted"]
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(list_one, task) for task in pending]):
                task, listed = future.result()
                task["status"] = "available" if listed else "missing"
                if not listed:
                    task["error"] = "daily archive is not listed"
                persist("listing_in_progress")

    pending = [task for task in ordered if task["status"] in {"available", "error"}]

    def fetch_one(task: dict) -> tuple[dict, Path, str, int]:
        path = store.fetch(task["dataset"], task["symbol"], task["period"], "1h")
        digest, count = _validate_daily(task, path)
        return task, path, digest, count

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_one, task): task for task in pending}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    _, path, digest, count = future.result()
                    task.update(status="downloaded", cached_path=str(path), sha256=digest,
                                rows=count, error=None)
                except Exception as exc:
                    task.update(status="error", error=f"{type(exc).__name__}: {exc}")
                persist("download_in_progress")
    status = "complete" if all(task["status"] == "downloaded" for task in ordered) else "incomplete"
    persist(status)
    return json.loads(output.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", default="data/portfolio/audits/train_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--manifest", default="data/portfolio/manifests/daily_repairs_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--base-manifest", default="data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args(argv)
    result = build_repair_manifest(args.audit, args.manifest,
                                   base_manifest_path=args.base_manifest, workers=args.workers)
    print(json.dumps({"status": result["status"], "tasks": len(result["tasks"]),
                      "downloaded": sum(x["status"] == "downloaded" for x in result["tasks"]),
                      "manifest": args.manifest}, sort_keys=True))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
