"""Public-source feasibility audit only; no Alpha, returns, or order endpoints.

Raw responses and an immutable manifest are written outside Git. Archive listings
are discovery evidence, not historical trading-status or filter evidence. This
probe intentionally cannot authorize economic evaluation or holdout access.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


ARCHIVE = "https://data.binance.vision/"
BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
FAMILIES = ("klines", "indexPriceKlines", "markPriceKlines", "fundingRate")
PROBE_MONTHS = ("2020-01", "2024-12")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def publish(path: Path, data: bytes) -> None:
    """No replacement of existing evidence, including corrupted evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"evidence collision: {path}")


def fetch(root: Path, url: str) -> tuple[bytes, dict]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "data.binance.vision", "s3-ap-northeast-1.amazonaws.com", "fapi.binance.com"
    }:
        raise ValueError("public source outside audit allowlist")
    if parsed.hostname == "fapi.binance.com" and (
        parsed.path != "/fapi/v1/exchangeInfo" or parsed.query
    ):
        raise ValueError("only unsigned current exchangeInfo is allowed")
    started = datetime.now(timezone.utc).isoformat()
    request = urllib.request.Request(url, headers={"User-Agent": "QuantOS-V1F1-source-audit"})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
        record = {
            "url": url, "requested_at_utc": started,
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": response.status, "sha256": sha256(data), "bytes": len(data),
            "last_modified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
            "date": response.headers.get("Date"),
        }
    publish(root / "raw" / record["sha256"], data)
    return data, record


def parse_listing(data: bytes) -> tuple[list[str], str | None]:
    tree = ET.fromstring(data)
    if tree.tag != "{http://s3.amazonaws.com/doc/2006-03-01/}ListBucketResult":
        raise ValueError("unexpected listing response")
    prefixes = [node.text for node in tree.findall("s:CommonPrefixes/s:Prefix", NS)]
    truncated = tree.findtext("s:IsTruncated", namespaces=NS)
    if truncated not in {"true", "false"} or any(not x for x in prefixes):
        raise ValueError("invalid listing pagination")
    marker = tree.findtext("s:NextMarker", namespaces=NS)
    if truncated == "true" and not marker:
        raise ValueError("truncated listing lacks continuation")
    return prefixes, marker if truncated == "true" else None


def listing(root: Path, prefix: str) -> tuple[list[str], list[dict]]:
    found, records, seen = [], [], set()
    marker = None
    while True:
        params = {"delimiter": "/", "prefix": prefix}
        if marker:
            params["marker"] = marker
        data, record = fetch(root, BUCKET + "?" + urllib.parse.urlencode(params))
        records.append(record)
        page, marker = parse_listing(data)
        if any(not value.startswith(prefix) for value in page):
            raise ValueError("listing escaped requested prefix")
        found.extend(page)
        if marker is None:
            break
        if marker in seen:
            raise ValueError("repeated listing continuation")
        seen.add(marker)
    if len(found) != len(set(found)):
        raise ValueError("duplicate listing prefixes")
    return sorted(found), records


def archive_path(symbol: str, family: str, month: str) -> str:
    if not re.fullmatch(r"[A-Z0-9]+USDT", symbol) or family not in FAMILIES:
        raise ValueError("invalid probe")
    # Coverage probes never consume pre-final/final observations.
    if month not in PROBE_MONTHS:
        raise ValueError("month outside the development-only source audit")
    suffix = "fundingRate" if family == "fundingRate" else "1m"
    folder = "" if family == "fundingRate" else "1m/"
    return f"data/futures/um/monthly/{family}/{symbol}/{folder}{symbol}-{suffix}-{month}.zip"


def validate_archive(data: bytes, checksum: bytes, filename: str, family: str) -> dict:
    fields = checksum.decode("ascii").strip().split()
    if (len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0])
            or fields[1].lstrip("*") != filename or sha256(data) != fields[0].lower()):
        raise ValueError("archive checksum/name mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != [filename.removesuffix(".zip") + ".csv"]:
            raise ValueError("unexpected archive members")
        with archive.open(archive.namelist()[0]) as stream:
            rows = list(csv.reader(io.TextIOWrapper(stream, encoding="utf-8-sig")))
    if not rows:
        raise ValueError("empty archive")
    header = None
    if not rows[0][0].isdigit():
        header = rows.pop(0)
    if not rows:
        raise ValueError("empty observations")
    times = []
    for row in rows:
        timestamp = int(row[0])
        if not 1_500_000_000_000 <= timestamp < 1_735_689_600_000:
            raise ValueError("unexpected time units or non-development observation")
        if times and timestamp <= times[-1]:
            raise ValueError("duplicate/out-of-order observation")
        times.append(timestamp)
        try:
            if family == "fundingRate":
                if len(row) != 3 or header != ["calc_time", "funding_interval_hours", "last_funding_rate"]:
                    raise ValueError("unknown funding schema")
                values = [Decimal(row[1]), Decimal(row[2])]
                if not all(x.is_finite() for x in values) or values[0] <= 0:
                    raise ValueError("invalid funding observation")
            else:
                if len(row) != 12 or timestamp % 60_000 or int(row[6]) != timestamp + 59_999:
                    raise ValueError("invalid completed-minute bounds/schema")
                o, h, low, c = (Decimal(x) for x in row[1:5])
                if not all(x.is_finite() and x > 0 for x in (o, h, low, c)):
                    raise ValueError("invalid price")
                if not low <= min(o, c) <= max(o, c) <= h:
                    raise ValueError("invalid OHLC")
                if family == "klines":
                    volume, quote, taker_base, taker_quote = (Decimal(row[i]) for i in (5, 7, 9, 10))
                    if not all(x.is_finite() and x >= 0 for x in (volume, quote, taker_base, taker_quote)):
                        raise ValueError("invalid volume")
                    if taker_base > volume or taker_quote > quote or int(row[8]) < 0:
                        raise ValueError("invalid trade/volume accounting")
        except InvalidOperation as exc:
            raise ValueError("invalid numeric observation") from exc
    gaps = sum(b - a != 60_000 for a, b in zip(times, times[1:])) if family != "fundingRate" else None
    return {
        "rows": len(rows), "header": header, "first_event_ms": times[0],
        "last_event_ms": times[-1], "minute_discontinuities": gaps,
        "checksum_verified": True,
        "partition_status": "QUARANTINED_GAPS" if gaps else "SAMPLE_SCHEMA_VALID",
        "scope": "sample only; not full-period coverage or availability certification",
    }


def probe(root: Path, symbol: str, family: str, month: str) -> dict:
    path = archive_path(symbol, family, month)
    records = []
    try:
        checksum, record = fetch(root, ARCHIVE + path + ".CHECKSUM")
        records.append(record)
        data, record = fetch(root, ARCHIVE + path)
        records.append(record)
        validation = validate_archive(data, checksum, path.rsplit("/", 1)[1], family)
        return {"symbol": symbol, "family": family, "month": month,
                "sources": records, "validation": validation}
    except Exception as exc:
        # Preserve failed-partition evidence; never silently replace or repair it.
        return {"symbol": symbol, "family": family, "month": month,
                "sources": records, "validation": {"partition_status": "QUARANTINED",
                "error_type": type(exc).__name__, "error": str(exc)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    repo = Path(__file__).resolve().parents[2]
    if root == repo or repo in root.parents:
        raise ValueError("audit evidence must be outside Git")
    source = Path(__file__).read_bytes()
    publish(root / "source" / (sha256(source) + ".py"), source)
    report = {"schema": "v1f1-source-audit-v1", "source_sha256": sha256(source),
              "economic_evaluation_performed": False, "holdout_accessed": False,
              "historical_metadata_validated": False, "sources": []}
    catalogs = {}
    for granularity in ("monthly", "daily"):
        entries, records = listing(root, f"data/futures/um/{granularity}/")
        report["sources"].extend(records)
        catalogs[granularity] = entries
    report["archive_families"] = catalogs
    symbols, records = listing(root, "data/futures/um/monthly/klines/")
    report["sources"].extend(records)
    report["archive_symbol_prefixes"] = symbols
    current, record = fetch(root, "https://fapi.binance.com/fapi/v1/exchangeInfo")
    report["sources"].append(record)
    current = json.loads(current)
    report["current_metadata"] = {
        "server_time_ms": current.get("serverTime"),
        "symbol_count": len(current["symbols"]),
        "historical_validity": "UNKNOWN; current observation only",
        "filter_examples": {x["symbol"]: x["filters"] for x in current["symbols"]
                            if x["symbol"] in {"BTCUSDT", "ETHUSDT"}},
    }
    tasks = [(root, symbol, family, month) for symbol in ("BTCUSDT", "ETHUSDT")
             for month in PROBE_MONTHS for family in FAMILIES]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        report["probes"] = list(pool.map(lambda task: probe(*task), tasks))
    report["economic_access"] = "DENIED: historical trading status/filter evidence not validated"
    report["limitations"] = [
        "Archive prefixes include historical instruments, but do not prove point-in-time tradability.",
        "Current exchangeInfo cannot establish historical filters or order minimums.",
        "Sample validation is not a complete 15-30 symbol history audit.",
        "No universe selection, return calculation, model fitting, or holdout evaluation occurred.",
    ]
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    path = root / "manifests" / (sha256(encoded) + ".json")
    publish(path, encoded)
    print(json.dumps({"manifest": str(path), "sha256": sha256(encoded),
                      "source_sha256": sha256(source),
                      "probes": len(report["probes"]), "economic_access": report["economic_access"]}))


if __name__ == "__main__":
    main()
