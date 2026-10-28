"""Checksummed public Binance USD-M archive access for offline research.

No credentials or trading endpoints are used. Raw archives are cached by
SHA-256, so a later upstream correction creates a new immutable blob instead
of silently rewriting the prior bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Callable, Iterator
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile
import csv

from ..types import Candle, D

BASE_URL = "https://data.binance.vision/data/futures/um"
S3_LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DATASETS = {"klines", "markPriceKlines", "fundingRate", "metrics"}
INTERVAL_MS = {"5m": 300_000, "1h": 3_600_000}
_SYMBOL = re.compile(r"^[A-Z0-9]{3,30}$")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _decimal(value: str, name: str, *, nonnegative: bool = False) -> Decimal:
    try:
        result = D(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"invalid {name}") from None
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError(f"invalid {name}")
    return result


def _integer(value: str, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {name}") from None
    if str(result) != value.strip():
        raise ValueError(f"invalid {name}")
    return result


@dataclass(frozen=True)
class Bar:
    """A normalized archive bar; close_ms is Binance's inclusive close time."""

    symbol: str
    interval: str
    open_ms: int
    close_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None

    @property
    def close_exclusive_ms(self) -> int:
        """UTC boundary immediately after the completed candle."""
        return self.close_ms + 1

    def to_candle(self) -> Candle:
        return Candle(self.open_ms, self.close_ms, self.open, self.high,
                      self.low, self.close, self.volume)


@dataclass(frozen=True)
class FundingRate:
    """A realized funding event; mark is deliberately not inferred here."""

    symbol: str
    event_ms: int
    interval_hours: int
    rate: Decimal


@dataclass(frozen=True)
class OpenInterestMetric:
    """Metrics archive row retaining Binance's literal label without re-timing."""

    symbol: str
    label_ms: int
    sum_open_interest: Decimal
    sum_open_interest_value: Decimal
    other_fields: tuple[tuple[str, Decimal], ...]
    missing_fields: tuple[str, ...] = ()

    @property
    def conservative_available_ms(self) -> int:
        """A one-hour safety proxy; Binance does not publish row availability time.

        This is deliberately not called an observation timestamp. The raw label
        is preserved and upstream metrics label semantics remain unresolved.
        """
        return self.label_ms + 3_600_000


@dataclass(frozen=True)
class ArchiveRef:
    dataset: str
    symbol: str
    period: str
    url: str
    interval: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class Coverage:
    dataset: str
    symbol: str
    interval: str | None
    periods: tuple[str, ...]

    @property
    def first_period(self) -> str | None:
        return self.periods[0] if self.periods else None

    @property
    def last_period(self) -> str | None:
        return self.periods[-1] if self.periods else None


def archive_ref(dataset: str, symbol: str, period: str,
                interval: str | None = None) -> ArchiveRef:
    """Build an archive URL from a closed, validated set of path components."""
    if dataset not in DATASETS:
        raise ValueError("unsupported archive dataset")
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("invalid symbol")
    if dataset in {"klines", "markPriceKlines"}:
        if interval not in INTERVAL_MS:
            raise ValueError("interval must be 5m or 1h")
    elif interval is not None:
        raise ValueError("interval is not used for this dataset")
    daily = dataset == "metrics" or (dataset in {"klines", "markPriceKlines"} and _DAY.fullmatch(period))
    if daily:
        if not _DAY.fullmatch(period):
            raise ValueError("daily archives require YYYY-MM-DD")
        date.fromisoformat(period)
        granularity = "daily"
    else:
        if not _MONTH.fullmatch(period):
            raise ValueError("archive requires YYYY-MM")
        if dataset == "metrics":
            raise ValueError("metrics archives require YYYY-MM-DD")
        granularity = "monthly"
    if dataset == "fundingRate" and daily:
        raise ValueError("funding archives require YYYY-MM")
    if dataset in {"klines", "markPriceKlines"}:
        filename = f"{symbol}-{interval}-{period}.zip"
        path = f"{granularity}/{dataset}/{symbol}/{interval}/{filename}"
    elif dataset == "fundingRate":
        filename = f"{symbol}-fundingRate-{period}.zip"
        path = f"{granularity}/{dataset}/{symbol}/{filename}"
    else:
        filename = f"{symbol}-metrics-{period}.zip"
        path = f"{granularity}/{dataset}/{symbol}/{filename}"
    return ArchiveRef(dataset, symbol, period, f"{BASE_URL}/{path}", interval)


class ArchiveStore:
    """Small bounded downloader with content-addressed immutable ZIP blobs."""

    def __init__(self, root: str | Path = "data/portfolio/raw",
                 *, opener: Callable = urlopen, timeout: float = 20,
                 max_archive_bytes: int = 128 * 1024 * 1024,
                 retries: int = 3, sleep: Callable[[float], None] = time.sleep):
        if max_archive_bytes <= 0 or retries < 1 or timeout <= 0:
            raise ValueError("invalid download bounds")
        self.root = Path(root)
        self.opener = opener
        self.timeout = timeout
        self.max_archive_bytes = max_archive_bytes
        self.retries = retries
        self.sleep = sleep

    def _get(self, url: str, limit: int) -> bytes:
        delay = 0.2
        last_error = None
        for attempt in range(self.retries):
            try:
                request = Request(url, headers={"User-Agent": "QuantOS-public-research/1"})
                response = self.opener(request, timeout=self.timeout)
                try:
                    body = response.read(limit + 1)
                finally:
                    close = getattr(response, "close", None)
                    if close:
                        close()
                if len(body) > limit:
                    raise ValueError("download exceeds configured byte limit")
                return body
            except ValueError:
                raise
            except (OSError, URLError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    self.sleep(delay)
                    delay = min(2.0, delay * 2)
        raise RuntimeError("public archive request failed") from last_error

    def fetch(self, dataset: str, symbol: str, period: str,
              interval: str | None = None) -> Path:
        ref = archive_ref(dataset, symbol, period, interval)
        checksum_text = self._get(ref.url + ".CHECKSUM", 4096).decode("ascii", "strict")
        parts = checksum_text.strip().split()
        if not parts or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            raise ValueError("malformed Binance SHA-256 checksum")
        expected = parts[0].lower()
        filename = ref.url.rsplit("/", 1)[-1]
        target = self.root / "sha256" / expected / filename
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("cached archive checksum mismatch")
            return target
        content = self._get(ref.url, self.max_archive_bytes)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("archive checksum mismatch")
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if archive.testzip() is not None:
                    raise ValueError("archive ZIP integrity check failed")
                csv_members = [name for name in archive.namelist()
                               if name.lower().endswith(".csv") and not name.startswith("/")]
                if len(csv_members) != 1:
                    raise ValueError("archive must contain exactly one CSV")
        except zipfile.BadZipFile:
            raise ValueError("invalid ZIP archive") from None
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
        _atomic_write(target.with_suffix(target.suffix + ".sha256"),
                      f"{expected}  {filename}\n".encode("ascii"))
        return target

    def list_archives(self, dataset: str, symbol: str,
                      interval: str | None = None,
                      start: date | None = None,
                      end: date | None = None,
                      granularity: str = "both") -> tuple[ArchiveRef, ...]:
        """List archive partitions from public S3 metadata, without fetching data."""
        if granularity not in {"both", "monthly", "daily"}:
            raise ValueError("granularity must be both, monthly, or daily")
        if dataset not in DATASETS or not _SYMBOL.fullmatch(symbol):
            raise ValueError("unsupported dataset or invalid symbol")
        if dataset in {"klines", "markPriceKlines"}:
            if interval not in INTERVAL_MS:
                raise ValueError("interval must be 5m or 1h")
            prefix = f"data/futures/um/monthly/{dataset}/{symbol}/{interval}/"
            daily_prefix = f"data/futures/um/daily/{dataset}/{symbol}/{interval}/"
        elif dataset == "metrics":
            if interval is not None:
                raise ValueError("interval is not used for metrics")
            prefix = f"data/futures/um/daily/metrics/{symbol}/"
            daily_prefix = None
        else:
            if interval is not None:
                raise ValueError("interval is not used for funding")
            prefix = f"data/futures/um/monthly/fundingRate/{symbol}/"
            daily_prefix = None
        token = None
        refs: list[ArchiveRef] = []
        if dataset == "metrics":
            prefixes = (prefix,) if granularity in {"both", "daily"} else ()
        elif dataset == "fundingRate":
            prefixes = (prefix,) if granularity in {"both", "monthly"} else ()
        else:
            prefixes = ((prefix, daily_prefix) if granularity == "both" else
                        (prefix,) if granularity == "monthly" else (daily_prefix,))
        for listing_prefix in prefixes:
            if listing_prefix is None:
                continue
            token = None
            for _ in range(100):
                params = {"list-type": "2", "prefix": listing_prefix, "max-keys": "1000"}
                if token:
                    params["continuation-token"] = token
                request = Request(S3_LIST_URL + "?" + urlencode(params),
                                  headers={"User-Agent": "QuantOS-public-research/1"})
                response = self.opener(request, timeout=self.timeout)
                try:
                    body = response.read(2_000_001)
                finally:
                    response.close()
                if len(body) > 2_000_000:
                    raise ValueError("S3 listing response exceeded limit")
                root = ET.fromstring(body)
                ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
                for content_node in root.findall("s3:Contents", ns):
                    node = content_node.find("s3:Key", ns)
                    key = node.text if node is not None and node.text else ""
                    if not key.endswith(".zip"):
                        continue
                    name = key.rsplit("/", 1)[-1]
                    date_match = re.search(r"-(\d{4}-\d{2}(?:-\d{2})?)\.zip$", name)
                    if not date_match:
                        continue
                    date_token = date_match.group(1)
                    try:
                        period_date = date.fromisoformat(date_token + "-01") if len(date_token) == 7 else date.fromisoformat(date_token)
                    except ValueError:
                        continue
                    monthly_partition = len(date_token) == 7
                    if start and period_date < (start.replace(day=1) if monthly_partition else start):
                        continue
                    if end and period_date > end:
                        continue
                    url = f"{BASE_URL}/{key.removeprefix('data/futures/um/')}"
                    size_text = content_node.findtext("s3:Size", namespaces=ns)
                    size = int(size_text) if size_text and size_text.isdigit() else None
                    refs.append(ArchiveRef(dataset, symbol, date_token, url, interval, size))
                truncated = root.findtext("s3:IsTruncated", namespaces=ns) == "true"
                token = root.findtext("s3:NextContinuationToken", namespaces=ns)
                if not truncated:
                    break
                if not token:
                    raise ValueError("truncated S3 listing lacks continuation token")
            else:
                raise ValueError("S3 listing page limit exceeded")
        return tuple(sorted(refs, key=lambda ref: ref.period))

    def coverage(self, dataset: str, symbol: str,
                 interval: str | None = None,
                 start: date | None = None,
                 end: date | None = None) -> Coverage:
        refs = self.list_archives(dataset, symbol, interval, start, end)
        return Coverage(dataset, symbol, interval, tuple(ref.period for ref in refs))


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".download-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _csv_rows(source: str | Path | bytes,
              headerless_schema: tuple[str, ...] | None = None) -> Iterator[dict[str, str]]:
    if isinstance(source, bytes):
        if not source.startswith(b"PK\x03\x04"):
            yield from _dict_rows(io.TextIOWrapper(io.BytesIO(source), encoding="utf-8-sig", newline=""), headerless_schema)
            return
        raw = io.BytesIO(source)
        with zipfile.ZipFile(raw) as archive:
            members = [name for name in archive.namelist()
                       if name.lower().endswith(".csv") and not name.startswith("/")]
            if len(members) != 1:
                raise ValueError("archive must contain exactly one CSV")
            stream = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8-sig", newline="")
            yield from _dict_rows(stream, headerless_schema)
        return
    path = Path(source)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist()
                       if name.lower().endswith(".csv") and not name.startswith("/")]
            if len(members) != 1:
                raise ValueError("archive must contain exactly one CSV")
            with archive.open(members[0]) as raw:
                yield from _dict_rows(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""), headerless_schema)
    else:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield from _dict_rows(stream, headerless_schema)


def _dict_rows(stream, headerless_schema: tuple[str, ...] | None = None) -> Iterator[dict[str, str]]:
    if headerless_schema is not None:
        reader = csv.reader(stream)
        first = next(reader, None)
        if first is None:
            return
        if tuple(first) == headerless_schema:
            first = next(reader, None)
        if first is not None:
            if len(first) != len(headerless_schema):
                raise ValueError("malformed CSV row")
            yield dict(zip(headerless_schema, first))
        for values in reader:
            if len(values) != len(headerless_schema):
                raise ValueError("malformed CSV row")
            yield dict(zip(headerless_schema, values))
        return
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        raise ValueError("CSV is missing a header")
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("malformed CSV row")
        yield row


def parse_klines(source: str | Path | bytes, symbol: str,
                 interval: str) -> tuple[Bar, ...]:
    if not _SYMBOL.fullmatch(symbol) or interval not in INTERVAL_MS:
        raise ValueError("invalid symbol or interval")
    width = INTERVAL_MS[interval]
    bars: list[Bar] = []
    previous = None
    expected_header = ["open_time", "open", "high", "low", "close", "volume",
                       "close_time", "quote_volume", "count", "taker_buy_volume",
                       "taker_buy_quote_volume", "ignore"]
    rows = iter(_csv_rows(source, tuple(expected_header)))
    # DictReader has consumed its header; the row keys expose source schema.
    for row in rows:
        opened = _integer(row["open_time"], "open time")
        closed = _integer(row["close_time"], "close time")
        if opened % width or closed != opened + width - 1:
            raise ValueError("misaligned or incomplete archive candle")
        if previous is not None and opened <= previous:
            raise ValueError("unordered or duplicate archive candles")
        o, h, lo, c = (_decimal(row[key], key) for key in ("open", "high", "low", "close"))
        vol = _decimal(row["volume"], "volume", nonnegative=True)
        quote_vol = _decimal(row["quote_volume"], "quote volume", nonnegative=True)
        if min(o, h, lo, c) <= 0 or h < max(o, c, lo) or lo > min(o, c, h):
            raise ValueError("invalid OHLC bounds")
        bars.append(Bar(symbol, interval, opened, closed, o, h, lo, c, vol, quote_vol))
        previous = opened
    return tuple(bars)


def parse_funding(source: str | Path | bytes, symbol: str) -> tuple[FundingRate, ...]:
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("invalid symbol")
    result = []
    previous = None
    for row in _csv_rows(source):
        if set(row) != {"calc_time", "funding_interval_hours", "last_funding_rate"}:
            raise ValueError("unexpected funding CSV schema")
        event_ms = _integer(row["calc_time"], "funding event time")
        hours = _integer(row["funding_interval_hours"], "funding interval")
        rate = _decimal(row["last_funding_rate"], "funding rate")
        if hours <= 0 or (previous is not None and event_ms <= previous):
            raise ValueError("invalid or unordered funding event")
        result.append(FundingRate(symbol, event_ms, hours, rate))
        previous = event_ms
    return tuple(result)


def parse_metrics(source: str | Path | bytes,
                  expected_symbol: str) -> tuple[OpenInterestMetric, ...]:
    """Parse metrics while preserving the raw `create_time` as a label.

    Binance has changed or may change metrics timestamp conventions; this
    routine does not shift, sort, deduplicate, or claim point-in-time availability.
    """
    if not _SYMBOL.fullmatch(expected_symbol):
        raise ValueError("invalid symbol")
    result = []
    required = {"create_time", "symbol", "sum_open_interest", "sum_open_interest_value"}
    for row in _csv_rows(source):
        if not required.issubset(row):
            raise ValueError("unexpected metrics CSV schema")
        if row["symbol"] != expected_symbol:
            raise ValueError("metrics row symbol mismatch")
        try:
            timestamp = datetime.strptime(row["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("invalid metrics label time") from None
        fields = []
        missing_fields = []
        for name, value in row.items():
            if name not in required:
                if value == "":
                    missing_fields.append(name)
                else:
                    fields.append((name, _decimal(value, name)))
        result.append(OpenInterestMetric(
            expected_symbol, int(timestamp.timestamp() * 1000),
            _decimal(row["sum_open_interest"], "sum_open_interest", nonnegative=True),
            _decimal(row["sum_open_interest_value"], "sum_open_interest_value", nonnegative=True),
            tuple(fields), tuple(missing_fields)))
    return tuple(result)
