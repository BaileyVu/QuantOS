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

from .acquire import iter_cached_archives, period_overlaps_selection
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
    overlay_manifest: str | None = None
    overlay_hash: str | None = None
    overlay_rows: int = 0
    selection_bounds: tuple[str | None, str | None] = (None, None)
