"""Sealed, bounded scaffold for the single 2025+ public-data evaluation wave.

Nothing in this module performs network or filesystem acquisition until a
locally verified protocol seal has been validated against the frozen window
and symbol cohorts. It uses only public Binance USD-M endpoints.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from . import acquire
from .data import ArchiveStore, archive_ref, _atomic_write
from .protocol import fingerprint, read_seal

WARMUP_START = "2024-10-01T00:00:00Z"
EVALUATION_START = "2025-01-01T00:00:00Z"
EVALUATION_END_EXCLUSIVE = "2026-10-29T00:00:00Z"
MAX_WORKERS = 4
MAJORS = acquire.MAJORS
OCTOBER_FUNDING_API = "https://fapi.binance.com/fapi/v1/fundingRate"


def _symbols(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"seal must contain {label}")
    result = tuple(value)
    if any(not isinstance(s, str) or not s.endswith("USDT") or
           not s.isascii() or not s.isalnum() or s != s.upper() for s in result):
        raise ValueError(f"invalid {label}")
    if len(set(result)) != len(result):
        raise ValueError(f"duplicate symbol in {label}")
    return result


def validate_seal(path: str | Path) -> tuple[dict, tuple[str, ...], tuple[str, ...]]:
    """Verify seal bytes, fixed dates, and the two frozen cohorts."""
    sealed = read_seal(Path(path))
    payload = sealed.get("payload", {})
    if payload.get("schema") != 1 or fingerprint(payload) != sealed.get("sha256"):
        raise ValueError("protocol seal content hash is invalid")
    plan = payload.get("plan", {})
    selection = payload.get("selection", {})
    if plan.get("evaluation_start") != EVALUATION_START:
        raise ValueError("seal evaluation start does not match the fixed window")
    if plan.get("evaluation_end_exclusive") != EVALUATION_END_EXCLUSIVE:
        raise ValueError("seal evaluation end does not match the fixed window")
    if plan.get("warmup_start") != WARMUP_START:
        raise ValueError("seal warmup does not match the fixed window")
    primary = _symbols(selection.get("primary_symbols"), "primary_symbols")
    volume = _symbols(selection.get("verified_volume_symbols"), "verified_volume_symbols")
    if len(primary) != 29 or len(volume) != 29:
        raise ValueError("sealed cohorts must each contain 29 symbols")
    if not selection.get("pick"):
        raise ValueError("seal must contain the frozen pick")
    return sealed, primary, volume


def _month_periods(first_year: int, first_month: int, last_year: int, last_month: int):
    year, month = first_year, first_month
    while (year, month) <= (last_year, last_month):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month == 13:
            year, month = year + 1, 1


def _days(first: date, last: date):
    day = first
    while day <= last:
        yield day.isoformat()
        day += timedelta(days=1)


def _task(ref, status="available"):
    row = acquire._item(ref, status)
    row["evaluation_source"] = "archive"
    return row


def _expected_monthly():
    return tuple(_month_periods(2024, 10, 2026, 9))


def _expected_tasks(primary: tuple[str, ...], volume: tuple[str, ...]) -> dict[str, dict]:
    """Return the exact permitted task catalog, independent of archive listing."""
    symbols = tuple(sorted(set(primary) | set(volume)))
    expected = {}

    def add(ref, phase, source="archive"):
        row = _task(ref)
        row["phase"] = phase
        row["evaluation_source"] = source
        expected[row["id"]] = row

    for dataset in ("klines", "markPriceKlines", "fundingRate"):
        for symbol in symbols:
            for period in _expected_monthly():
                phase = "warmup" if period < "2025-01" else "evaluation"
                interval = "1h" if dataset != "fundingRate" else None
                add(archive_ref(dataset, symbol, period, interval), phase)
    for dataset in ("klines", "markPriceKlines"):
        for symbol in symbols:
            for day in _days(date(2026, 10, 1), date(2026, 10, 28)):
                add(archive_ref(dataset, symbol, day, "1h"), "evaluation")
    for symbol in symbols:
        start_ms = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime(2026, 10, 29, tzinfo=timezone.utc).timestamp() * 1000) - 1
        url = OCTOBER_FUNDING_API + "?" + urlencode({"symbol": symbol,
            "startTime": start_ms, "endTime": end_ms, "limit": 1000})
        key = f"fundingRate-api:{symbol}:2026-10"
        expected[key] = {"id": key, "dataset": "fundingRate", "symbol": symbol,
            "interval": None, "period": "2026-10", "url": url,
            "expected_bytes": None, "status": "available", "sha256": None,
            "cached_path": None, "actual_bytes": None, "error": None,
            "evaluation_source": "public_funding_api", "phase": "evaluation",
            "request_start_ms": start_ms, "request_end_ms": end_ms}
    for symbol in MAJORS:
        for day in _days(date(2024, 10, 1), date(2026, 10, 28)):
            phase = "warmup" if day < "2025-01-01" else "evaluation"
            add(archive_ref("metrics", symbol, day, None), phase)
    return expected


def _period_is(period: object, pattern: str) -> bool:
    if not isinstance(period, str) or re.fullmatch(pattern, period) is None:
        return False
    try:
        date.fromisoformat(period + "-01" if len(period) == 7 else period)
    except ValueError:
        return False
    return True


def build_evaluation_plan(seal_path: str | Path, *, primary_symbols=None,
                          verified_volume_symbols=None, store: ArchiveStore | None = None,
                          audit_workers: int = MAX_WORKERS,
                          warmup_manifest_path: str | Path =
                          "data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json") -> dict:
    """Build a resumable listing plan; seal validation precedes all listing calls.

    Optional symbol inputs are assertions against the sealed cohorts, never a
    way to alter them. Listing calls are grouped by dataset/symbol/range.
    Warmup task checksums are reused from the existing training manifest when
    the referenced files still match their recorded hashes.
    """
    sealed, primary, volume = validate_seal(seal_path)
    if primary_symbols is not None and tuple(primary_symbols) != primary:
        raise ValueError("primary symbols differ from sealed cohort")
    if verified_volume_symbols is not None and tuple(verified_volume_symbols) != volume:
        raise ValueError("volume symbols differ from sealed cohort")
    if not 1 <= audit_workers <= MAX_WORKERS:
        raise ValueError("audit workers must be from 1 to 4")
    store = store or ArchiveStore()
    trade_symbols = tuple(sorted(set(primary) | set(volume)))
    expected = _expected_tasks(primary, volume)
    rows = {key: dict(task) for key, task in expected.items()}
    errors = []

    # Reuse exact, checksum-verified warmup rows from the existing local
    # training manifest; no historical archive metadata scan is performed.
    warmup_manifest = Path(warmup_manifest_path)
    warmup_manifest_sha = None
    if warmup_manifest.is_file():
        warmup_bytes = warmup_manifest.read_bytes()
        warmup_manifest_sha = hashlib.sha256(warmup_bytes).hexdigest()
        prior = json.loads(warmup_bytes)
        for item in prior.get("tasks", []):
            key = item.get("id")
            target = Path(item.get("cached_path") or "")
            if key in rows and rows[key]["phase"] == "warmup" and \
                    item.get("status") == "downloaded" and target.is_file():
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest == item.get("sha256"):
                    rows[key].update({k: item.get(k) for k in
                                      ("status", "sha256", "cached_path", "actual_bytes")})
                    rows[key]["warmup_reused_from"] = str(warmup_manifest)
                    rows[key]["warmup_source_manifest_sha256"] = warmup_manifest_sha
                    rows[key]["warmup_source_task_id"] = item["id"]

    # group=(dataset,symbol,interval,granularity,start,end,periods)
    groups = {}
    monthly = _expected_monthly()
    for dataset in ("klines", "markPriceKlines", "fundingRate"):
        for symbol in trade_symbols:
            interval = "1h" if dataset != "fundingRate" else None
            missing_warmup = [p for p in monthly[:3]
                              if rows[f"{dataset}:{symbol}:{interval or '-'}:{p}"]["status"] != "downloaded"]
            if missing_warmup:
                groups[(dataset, symbol, interval, "monthly", date(2024, 10, 1), date(2024, 12, 31),
                        tuple(monthly[:3]))] = "warmup"
            groups[(dataset, symbol, interval, "monthly", date(2025, 1, 1), date(2026, 9, 30),
                    tuple(monthly[3:]))] = "evaluation"
    for dataset in ("klines", "markPriceKlines"):
        for symbol in trade_symbols:
            days = tuple(_days(date(2026, 10, 1), date(2026, 10, 28)))
            groups[(dataset, symbol, "1h", "daily", date(2026, 10, 1), date(2026, 10, 28), days)] = "evaluation"
    for symbol in MAJORS:
        warmup_days = tuple(_days(date(2024, 10, 1), date(2024, 12, 31)))
        missing_warmup = [day for day in warmup_days
                          if rows[f"metrics:{symbol}:-:{day}"]["status"] != "downloaded"]
        if missing_warmup:
            groups[("metrics", symbol, None, "daily", date(2024, 10, 1), date(2024, 12, 31),
                    warmup_days)] = "warmup"
        evaluation_days = tuple(_days(date(2025, 1, 1), date(2026, 10, 28)))
        groups[("metrics", symbol, None, "daily", date(2025, 1, 1), date(2026, 10, 28),
                evaluation_days)] = "evaluation"

    def list_group(group):
        dataset, symbol, interval, granularity, start, end, periods = group
        refs = store.list_archives(dataset, symbol, interval, start, end,
                                   granularity=granularity)
        if len({ref.period for ref in refs}) != len(refs):
            raise ValueError("archive listing contains duplicate period references")
        found = {ref.period: ref for ref in refs}
        return group, found, None

    with ThreadPoolExecutor(max_workers=audit_workers) as pool:
        futures = {pool.submit(list_group, group): group for group in groups}
        for future in as_completed(futures):
            group = futures[future]
            dataset, symbol, interval, granularity, start, end, periods = group
            try:
                _, found, _ = future.result()
                for period in periods:
                    key = f"{dataset}:{symbol}:{interval or '-'}:{period}"
                    if rows[key]["status"] == "downloaded":
                        continue
                    if period in found:
                        rows[key].update(_task(found[period], "available"))
                    else:
                        rows[key]["status"] = "missing"
                        rows[key]["error"] = "partition absent from bounded archive listing"
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                errors.append({"dataset": dataset, "symbol": symbol,
                               "interval": interval, "granularity": granularity,
                               "start": start.isoformat(), "end": end.isoformat(),
                               "error": message})
                for period in periods:
                    key = f"{dataset}:{symbol}:{interval or '-'}:{period}"
                    if rows[key]["status"] != "downloaded":
                        rows[key]["status"] = "unverified"
                        rows[key]["error"] = message
    task_rows = sorted(rows.values(), key=lambda x: (x["dataset"], x["symbol"], x["period"]))
    return {"schema_version": 1, "seal_sha256": sealed["sha256"],
            "window": {"start": "2024-10-01", "end": "2026-10-28",
                       "evaluation_start": "2025-01-01",
                       "evaluation_end_exclusive": "2026-10-29"},
            "warmup_reuse": {"manifest": str(Path(warmup_manifest_path)),
                             "from": WARMUP_START, "through_exclusive": EVALUATION_START,
                             "acquire_again": False},
            "universe": {"primary_symbols": list(primary),
                         "verified_volume_symbols": list(volume),
                         "symbols": list(trade_symbols)},
            "selection_provenance": sealed["payload"]["selection"],
            "code_sha": sealed["payload"]["code_sha"],
            "training_data_sha": sealed["payload"]["training_data_sha"],
            "open_interest_symbols": list(MAJORS), "listing_errors": errors,
            "tasks": task_rows, "seal_path": str(Path(seal_path).resolve())}


def validate_seal_from_plan(plan: dict) -> dict:
    """Revalidate local seal binding and frozen cohorts without network access."""
    sealed_path = plan.get("seal_path")
    if not sealed_path:
        raise ValueError("evaluation plan must retain its local seal path")
    sealed, primary, volume = validate_seal(sealed_path)
    if plan.get("seal_sha256") != sealed["sha256"]:
        raise ValueError("plan is not bound to this local seal")
    if plan.get("selection_provenance") != sealed["payload"].get("selection") or \
            plan.get("code_sha") != sealed["payload"].get("code_sha") or \
            plan.get("training_data_sha") != sealed["payload"].get("training_data_sha"):
        raise ValueError("manifest provenance differs from local seal")
    expected_window = {"start": "2024-10-01", "end": "2026-10-28",
                       "evaluation_start": "2025-01-01",
                       "evaluation_end_exclusive": "2026-10-29"}
    if plan.get("window") != expected_window:
        raise ValueError("manifest window differs from the fixed acquisition scope")
    warmup = plan.get("warmup_reuse", {})
    if warmup.get("from") != WARMUP_START or \
            warmup.get("through_exclusive") != EVALUATION_START or \
            warmup.get("acquire_again") is not False or not warmup.get("manifest"):
        raise ValueError("manifest warmup reuse policy is invalid")
    universe = plan.get("universe", {})
    if universe.get("primary_symbols") != list(primary) or \
            universe.get("verified_volume_symbols") != list(volume):
        raise ValueError("plan cohorts differ from local seal")
    expected = _expected_tasks(primary, volume)
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("manifest task ledger is empty or invalid")
    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest has duplicate task IDs")
    if set(ids) != set(expected):
        raise ValueError("manifest task ledger is incomplete or contains extra tasks")
    immutable = ("dataset", "symbol", "interval", "period", "url", "evaluation_source", "phase")
    for task in tasks:
        reference = expected[task["id"]]
        if any(task.get(key) != reference.get(key) for key in immutable):
            raise ValueError("manifest task differs from the fixed period/date scope")
        if task.get("evaluation_source") == "public_funding_api" and any(
                task.get(key) != reference.get(key)
                for key in ("request_start_ms", "request_end_ms")):
            raise ValueError("manifest contains invalid public funding bounds")
        status = task.get("status")
        if status not in {"available", "downloaded", "missing", "unverified", "error"}:
            raise ValueError("manifest task has an invalid status")
        if status in {"missing", "unverified", "error"} and not task.get("error"):
            raise ValueError("unavailable task lacks failure evidence")
        if "warmup_source_manifest_sha256" in task:
            digest = task.get("warmup_source_manifest_sha256")
            if reference["phase"] != "warmup" or not isinstance(digest, str) or \
                    re.fullmatch(r"[0-9a-f]{64}", digest) is None or \
                    task.get("warmup_source_task_id") != task["id"] or \
                    not task.get("cached_path") or task.get("sha256") is None:
                raise ValueError("warmup cache reuse provenance is incomplete")
        expected_format = (r"\d{4}-\d{2}" if task.get("evaluation_source") == "public_funding_api" or
                           len(reference["period"]) == 7 else r"\d{4}-\d{2}-\d{2}")
        if not _period_is(task.get("period"), expected_format):
            raise ValueError("manifest has invalid period syntax")
    return sealed


def save_evaluation_plan(path: str | Path, plan: dict) -> dict:
    """Persist a seal-bound manifest and retain completed tasks on resume."""
    validate_seal_from_plan(plan)
    path = Path(path)
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("seal_sha256") != plan.get("seal_sha256"):
            raise ValueError("existing manifest belongs to a different seal")
        previous = {task["id"]: task for task in old.get("tasks", [])}
        for task in plan["tasks"]:
            prior = previous.get(task["id"])
            if prior and prior.get("status") == "downloaded":
                task.update({key: prior.get(key) for key in
                             ("status", "sha256", "cached_path", "actual_bytes", "error")})
    acquire._write_json(path, plan)
    return plan


def download_evaluation_batch(seal_path: str | Path, manifest_path: str | Path, *,
                              limit: int = 32, workers: int = MAX_WORKERS,
                              store: ArchiveStore | None = None) -> dict:
    """Revalidate the seal before any bounded archive or public API fetch."""
    sealed, _, _ = validate_seal(seal_path)
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("seal_sha256") != sealed["sha256"]:
        raise ValueError("manifest is not bound to this protocol seal")
    if not 1 <= workers <= MAX_WORKERS or limit < 1:
        raise ValueError("invalid batch bounds")
    validate_seal_from_plan(manifest)
    store = store or ArchiveStore()
    pending = [task for task in manifest["tasks"]
               if task.get("status") in {"available", "error"}]
    batch = pending[:limit]

    def fetch(task):
        try:
            if task.get("evaluation_source") == "public_funding_api":
                content = store._get(task["url"], store.max_archive_bytes)
                rows = json.loads(content)
                if not isinstance(rows, list) or not rows:
                    raise ValueError("October funding API returned no events")
                if len(rows) >= 1000:
                    raise ValueError("October funding API page is full; pagination is required")
                start_ms = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
                end_ms = int(datetime(2026, 10, 29, tzinfo=timezone.utc).timestamp() * 1000)
                if any(row.get("symbol") != task["symbol"] or
                       not isinstance(row.get("fundingTime"), int) or
                       not start_ms <= row["fundingTime"] < end_ms or
                       "fundingRate" not in row for row in rows):
                    raise ValueError("malformed October funding API response")
                digest = hashlib.sha256(content).hexdigest()
                target = store.root / "sha256" / digest / f"{task['symbol']}-fundingRate-2026-10.json"
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ValueError("cached funding API hash mismatch")
                if not target.exists():
                    _atomic_write(target, content)
                return {"status": "downloaded", "sha256": digest,
                        "cached_path": str(target), "actual_bytes": len(content), "error": None}
            target = store.fetch(task["dataset"], task["symbol"], task["period"], task["interval"])
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            return {"status": "downloaded", "sha256": digest, "cached_path": str(target),
                    "actual_bytes": target.stat().st_size, "error": None}
        except Exception as exc:
            return {"status": "error", "sha256": None, "cached_path": None,
                    "actual_bytes": None, "error": f"{type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, task): task for task in batch}
        for future in as_completed(futures):
            futures[future].update(future.result())
            acquire._write_json(path, manifest)
    return manifest
