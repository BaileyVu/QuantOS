"""Strict, bounded index of checksum-verified cached training archives.

Missing rows are reported as evidence and never imputed. The default index is
restricted to the supplied 2021-10-01--2024-12-31 training interval.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import TypeVar

from .acquire import iter_cached_archives
from .data import Bar, FundingRate, OpenInterestMetric, parse_funding, parse_klines, parse_metrics

TRAIN_START = date(2021, 10, 1)
TRAIN_END = date(2024, 12, 31)
BAR_MS = 3_600_000
T = TypeVar("T")


@dataclass(frozen=True)
class SeriesAudit:
    symbol: str
    dataset: str
    rows: int
    duplicates: tuple[int, ...]
    gaps: tuple[tuple[int, int], ...]
    outside_training: int
    missing_values: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class DatasetIndex:
    bars: dict[str, tuple[Bar, ...]]
    marks: dict[str, tuple[Bar, ...]]
    funding: dict[str, tuple[FundingRate, ...]]
    metrics: dict[str, tuple[OpenInterestMetric, ...]]
    audit: tuple[SeriesAudit, ...]
    # O(1) exact timestamp lookup; absent bars remain absent.
    bar_by_open_ms: dict[str, dict[int, Bar]]
    mark_by_open_ms: dict[str, dict[int, Bar]]
    window: tuple[str, str]
    content_hash: str
    unavailable_partitions: tuple[dict, ...]


def _window_bounds(start: str, end: str) -> tuple[int, int]:
    lo = int(datetime.combine(date.fromisoformat(start), datetime.min.time(), timezone.utc).timestamp() * 1000)
    hi_date = date.fromisoformat(end).fromordinal(date.fromisoformat(end).toordinal() + 1)
    hi = int(datetime.combine(hi_date, datetime.min.time(), timezone.utc).timestamp() * 1000)
    return lo, hi


def _series_audit(symbol: str, dataset: str, rows: list[T], timestamp,
                  outside_training: int = 0) -> SeriesAudit:
    ordered = sorted(rows, key=timestamp)
    times = [timestamp(row) for row in ordered]
    duplicates = tuple(sorted(ts for ts, count in Counter(times).items() if count > 1))
    gaps = tuple((left, right) for left, right in zip(times, times[1:])
                 if dataset in {"klines", "markPriceKlines"} and right - left != BAR_MS)
    missing: Counter[str] = Counter()
    for row in ordered:
        for field in getattr(row, "missing_fields", ()):
            missing[field] += 1
    return SeriesAudit(symbol, dataset, len(rows), duplicates, gaps, outside_training,
                       tuple(sorted(missing.items())))


def build_index(manifest_path: str | Path, *, symbols: tuple[str, ...] | None = None,
                training_only: bool = True) -> DatasetIndex:
    """Parse each cached archive once, verify it through the manifest iterator,
    then sort and audit raw timestamps. Gaps/duplicates remain visible.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if training_only and (manifest.get("window") != {"start": TRAIN_START.isoformat(), "end": TRAIN_END.isoformat()}):
        raise ValueError("manifest is outside the approved training window")
    window = manifest.get("window", {})
    if not window.get("start") or not window.get("end"):
        raise ValueError("manifest window is required")
    bounds = _window_bounds(window["start"], window["end"])
    wanted = set(symbols or manifest.get("universe", {}).get("symbols", []))
    grouped: dict[str, dict[str, list]] = {
        "klines": defaultdict(list), "markPriceKlines": defaultdict(list),
        "fundingRate": defaultdict(list), "metrics": defaultdict(list),
    }
    audit: list[SeriesAudit] = []
    hasher = hashlib.sha256()
    unavailable: list[dict] = []
    for item in sorted(manifest.get("tasks", []), key=lambda x: x.get("id", "")):
        if item.get("symbol") not in wanted:
            continue
        hasher.update(f"{item.get('id')}:{item.get('status')}:{item.get('sha256') or ''}:{item.get('availability_class', '')}\n".encode())
        if item.get("status") in {"missing", "error", "unverified", "available"}:
            unavailable.append({key: item.get(key) for key in
                                ("id", "dataset", "symbol", "period", "status", "availability_class", "error")})
    for dataset in grouped:
        for row in iter_cached_archives(manifest_path, dataset=dataset,
                                        interval="1h" if dataset in {"klines", "markPriceKlines"} else None):
            symbol = row["symbol"]
            if symbol not in wanted:
                continue
            path = row["cached_path"]
            if dataset in {"klines", "markPriceKlines"}:
                parsed = parse_klines(path, symbol, "1h")
                timestamp = lambda x: x.open_ms
                timestamp = lambda x: x.open_ms
            elif dataset == "fundingRate":
                parsed = parse_funding(path, symbol)
                timestamp = lambda x: x.event_ms
            else:
                parsed = parse_metrics(path, symbol)
                timestamp = lambda x: x.label_ms
            outside = sum(not (bounds[0] <= timestamp(x) < bounds[1]) for x in parsed)
            if outside:
                raise ValueError(f"archive rows outside declared manifest window: {symbol} {dataset} ({outside})")
            grouped[dataset][symbol].extend(parsed)
    for dataset, by_symbol in grouped.items():
        for symbol, rows in by_symbol.items():
            ts = (lambda x: x.open_ms) if dataset in {"klines", "markPriceKlines"} else (
                (lambda x: x.event_ms) if dataset == "fundingRate" else (lambda x: x.label_ms))
            audit.append(_series_audit(symbol, dataset, rows, ts))
            rows.sort(key=ts)
            if dataset in {"klines", "markPriceKlines"}:
                if len({ts(row) for row in rows}) != len(rows):
                    raise ValueError(f"duplicate timestamps prevent exact lookup: {symbol} {dataset}")
    return DatasetIndex(
        bars={k: tuple(v) for k, v in grouped["klines"].items()},
        marks={k: tuple(v) for k, v in grouped["markPriceKlines"].items()},
        funding={k: tuple(v) for k, v in grouped["fundingRate"].items()},
        metrics={k: tuple(v) for k, v in grouped["metrics"].items()},
        audit=tuple(audit),
        bar_by_open_ms={k: {x.open_ms: x for x in v} for k, v in grouped["klines"].items()},
        mark_by_open_ms={k: {x.open_ms: x for x in v} for k, v in grouped["markPriceKlines"].items()},
        window=(window["start"], window["end"]),
        content_hash=hasher.hexdigest(),
        unavailable_partitions=tuple(unavailable),
    )


def persist_audit(index: DatasetIndex, path: str | Path) -> None:
    """Persist row-level audit evidence (caller chooses ignored output path)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"window": list(index.window), "content_hash": index.content_hash,
               "series": [{"symbol": x.symbol, "dataset": x.dataset, "rows": x.rows,
                           "duplicates": list(x.duplicates), "gaps": [list(g) for g in x.gaps],
                           "outside_training": x.outside_training,
                           "missing_values": dict(x.missing_values)} for x in index.audit],
               "unavailable_partitions": list(index.unavailable_partitions)}
    target.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--audit", default="data/portfolio/audits/train_2021-10-01_to_2024-12-31.json")
    args = parser.parse_args(argv)
    index = build_index(args.manifest)
    persist_audit(index, args.audit)
    print(json.dumps({"symbols": len(index.bars), "bars": sum(map(len, index.bars.values())),
                      "marks": sum(map(len, index.marks.values())),
                      "funding": sum(map(len, index.funding.values())),
                      "metrics": sum(map(len, index.metrics.values())),
                      "window": index.window, "content_hash": index.content_hash,
                      "audit_file": args.audit}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
