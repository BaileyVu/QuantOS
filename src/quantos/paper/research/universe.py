"""Freeze a Dec-2024 USD-M USDT-perpetual robustness universe.

Selection uses only public archive metadata through 2024-12 and December 2024
1h bars. It is a retrospective pre-2025 calibration cohort, not a point-in-time
tradable universe: the December ranking itself looks ahead within 2024.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from decimal import Decimal
from typing import Iterable
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from urllib.request import Request

from .acquire import MAJORS
from .data import ArchiveStore, S3_LIST_URL, parse_klines

ASOF_END = date(2024, 12, 31)
MONTHS = ("2024-09", "2024-10", "2024-11", "2024-12")
MAX_WORKERS = 4
EXPECTED_DECEMBER_HOURS = 31 * 24
SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,30}USDT$")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".universe-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
        Path(temp).replace(path)
    finally:
        Path(temp).unlink(missing_ok=True)


def list_usdt_symbols(store: ArchiveStore | None = None) -> tuple[str, ...]:
    """List all symbols with an archived monthly USD-M kline prefix.

    This is an archive-name listing, not current exchangeInfo, so delisted
    symbols remain eligible when their historical monthly objects remain listed.
    """
    store = store or ArchiveStore()
    token = None
    symbols: set[str] = set()
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    for _ in range(100):
        params = {"list-type": "2", "prefix": "data/futures/um/monthly/klines/",
                  "delimiter": "/", "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        request = Request(S3_LIST_URL + "?" + urlencode(params),
                          headers={"User-Agent": "QuantOS-public-research/1"})
        response = store.opener(request, timeout=store.timeout)
        try:
            body = response.read(2_000_001)
        finally:
            response.close()
        if len(body) > 2_000_000:
            raise ValueError("S3 symbol listing exceeded response limit")
        root = ET.fromstring(body)
        for node in root.findall("s3:CommonPrefixes/s3:Prefix", ns):
            symbol = (node.text or "").rstrip("/").rsplit("/", 1)[-1]
            if SYMBOL_RE.fullmatch(symbol):
                symbols.add(symbol)
        if root.findtext("s3:IsTruncated", namespaces=ns) != "true":
            return tuple(sorted(symbols))
        token = root.findtext("s3:NextContinuationToken", namespaces=ns)
        if not token:
            raise ValueError("truncated symbol listing lacks continuation token")
    raise ValueError("S3 symbol listing page limit exceeded")


def _save_progress(path: Path, state: dict) -> None:
    _write_json(path, state)


def build_universe(path: str | Path = "data/portfolio/manifests/robustness_dec2024.json",
                   *, store: ArchiveStore | None = None,
                   symbols: Iterable[str] | None = None,
                   reuse_manifest: str | Path = "data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json",
                   workers: int = MAX_WORKERS) -> dict:
    """List archives, fetch December bars with at most four workers, rank and save.

    Existing downloaded task records are checksum-verified and reused on resume.
    Only December 2024 1h archives are downloaded. September-November are
    metadata-only history evidence; December's 744 rows are checked exactly.
    """
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError("workers must be between 1 and 4")
    target = Path(path)
    store = store or ArchiveStore()
    prior = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    symbol_list = tuple(sorted(set(symbols or prior.get("symbols") or list_usdt_symbols(store))))
    if any(not SYMBOL_RE.fullmatch(symbol) for symbol in symbol_list):
        raise ValueError("symbols must be public USD-M USDT pairs")
    prior_markets = prior.get("markets", prior.get("market_metadata", []))
    prior_rows = (dict(prior_markets) if isinstance(prior_markets, dict) else
                  {item["symbol"]: item for item in prior_markets})
    markets: dict[str, dict] = dict(prior_rows)

    def metadata(symbol: str) -> tuple[str, dict]:
        refs = store.list_archives("klines", symbol, "1h", end=ASOF_END,
                                   granularity="monthly")
        periods = [ref.period for ref in refs if ref.period <= "2024-12"]
        return symbol, {"periods_through_dec2024": periods,
                        "first_partition": periods[0] if periods else None,
                        "december_available": "2024-12" in periods}

    pending_metadata = [symbol for symbol in symbol_list if symbol not in markets or
                        "periods_through_dec2024" not in markets[symbol]]
    if pending_metadata:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(metadata, symbol): symbol for symbol in pending_metadata}
            for future in as_completed(futures):
                symbol, result = future.result()
                markets[symbol] = {**markets.get(symbol, {}), **result}
                _save_progress(target, {"schema_version": 1, "asof": ASOF_END.isoformat(),
                                        "symbols": list(symbol_list), "markets": markets,
                                        "status": "metadata_in_progress"})

    tasks = {item["symbol"]: item for item in prior.get("december_archives", [])}
    reusable: dict[str, dict] = {}
    source_manifest = Path(reuse_manifest)
    if source_manifest.exists():
        source = json.loads(source_manifest.read_text(encoding="utf-8"))
        reusable = {item["symbol"]: item for item in source.get("tasks", [])
                    if item.get("dataset") == "klines" and item.get("period") == "2024-12"
                    and item.get("status") == "downloaded"}
    for symbol in symbol_list:
        if not markets[symbol].get("december_available"):
            continue
        task = tasks.setdefault(symbol, {"symbol": symbol, "status": "available",
                                         "dataset": "klines", "interval": "1h",
                                         "period": "2024-12", "quote_volume": None,
                                         "rows": None, "sha256": None, "cached_path": None,
                                         "error": None})
        if task["status"] == "downloaded":
            cached = Path(task["cached_path"])
            if not cached.is_file() or hashlib.sha256(cached.read_bytes()).hexdigest() != task["sha256"]:
                task.update(status="available", error="cached archive missing or checksum mismatch")
        if task["status"] != "downloaded" and symbol in reusable:
            source_task = reusable[symbol]
            cached = Path(source_task["cached_path"])
            digest = hashlib.sha256(cached.read_bytes()).hexdigest() if cached.is_file() else None
            if digest and digest == source_task.get("sha256"):
                task.update(status="downloaded", cached_path=str(cached), sha256=digest,
                            error=None, reused_training_cache=True)

    candidates_to_fetch = [symbol for symbol in symbol_list
                           if markets[symbol].get("december_available")
                           and markets[symbol].get("first_partition") is not None
                           and markets[symbol]["first_partition"] <= "2024-09"
                           and all(month in markets[symbol].get("periods_through_dec2024", []) for month in MONTHS)
                           and tasks.get(symbol, {}).get("status") != "downloaded"]

    def fetch(symbol: str) -> tuple[str, Path, str]:
        cached_path = store.fetch("klines", symbol, "2024-12", "1h")
        digest = hashlib.sha256(cached_path.read_bytes()).hexdigest()
        return symbol, cached_path, digest

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, symbol): symbol for symbol in candidates_to_fetch}
        for future in as_completed(futures):
            symbol = futures[future]
            task = tasks[symbol]
            try:
                _, cached_path, digest = future.result()
                bars = parse_klines(cached_path, symbol, "1h")
                open_times = [bar.open_ms for bar in bars]
                dec_start = int(datetime(2024, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)
                complete = (len(bars) == EXPECTED_DECEMBER_HOURS and open_times[0] == dec_start
                            and all(b - a == 3_600_000 for a, b in zip(open_times, open_times[1:])))
                if not complete:
                    task.update(status="invalid", error=f"December row coverage {len(bars)}/{EXPECTED_DECEMBER_HOURS}")
                else:
                    quote_volume = sum((bar.quote_volume for bar in bars if bar.quote_volume is not None), start=bars[0].quote_volume * 0)
                    task.update(status="downloaded", cached_path=str(cached_path), sha256=digest,
                                rows=len(bars), quote_volume=str(quote_volume), error=None)
            except Exception as exc:
                task.update(status="error", error=f"{type(exc).__name__}: {exc}")
            _save_progress(target, {"schema_version": 1, "asof": ASOF_END.isoformat(),
                                    "symbols": list(symbol_list), "markets": markets,
                                    "december_archives": list(tasks.values()),
                                    "status": "download_in_progress"})

    # Verify reused cached rows too, and calculate their quote-volume sum.
    for symbol, task in tasks.items():
        if task.get("status") != "downloaded" or task.get("quote_volume") is not None:
            continue
        bars = parse_klines(task["cached_path"], symbol, "1h")
        open_times = [bar.open_ms for bar in bars]
        dec_start = int(datetime(2024, 12, 1, tzinfo=timezone.utc).timestamp() * 1000)
        complete = (len(bars) == EXPECTED_DECEMBER_HOURS and open_times[0] == dec_start
                    and all(b - a == 3_600_000 for a, b in zip(open_times, open_times[1:])))
        if not complete:
            task.update(status="invalid", error=f"December row coverage {len(bars)}/{EXPECTED_DECEMBER_HOURS}")
        else:
            quote_volume = sum((bar.quote_volume for bar in bars if bar.quote_volume is not None), start=bars[0].quote_volume * 0)
            task.update(rows=len(bars), quote_volume=str(quote_volume))

    ranked = []
    for symbol in symbol_list:
        market = markets[symbol]
        task = tasks.get(symbol, {})
        months = set(market.get("periods_through_dec2024", []))
        history_ok = all(month in months for month in MONTHS)
        dec_ok = task.get("status") == "downloaded" and task.get("rows") == EXPECTED_DECEMBER_HOURS
        first_period = market.get("first_partition")
        # Any nonempty Sep archive implies a first row no later than Sep 30,
        # which is >90d before 2025-01-01. Earlier months also qualify.
        first_90d = first_period is not None and first_period <= "2024-09"
        ranked.append({"symbol": symbol,
                       "dec2024_quote_volume": task.get("quote_volume"),
                       "first_partition": first_period,
                       "metadata_months_sep_dec": [month for month in MONTHS if month in months],
                       "history_eligibility": "eligible_metadata_proxy" if history_ok and first_90d and dec_ok else "ineligible",
                       "eligibility_reason": None if history_ok and first_90d and dec_ok else (
                           "missing_december_archive_or_full_row_coverage" if not dec_ok else
                           "missing_september_november_metadata_partition" if not history_ok else
                           "first_archive_later_than_2024-09"),
                       "december_rows": task.get("rows")})
    ranked.sort(key=lambda row: (-(Decimal(row["dec2024_quote_volume"])
                                   if row["dec2024_quote_volume"] is not None else Decimal("-1")),
                                 row["symbol"]))
    eligible_nonmajors = [row for row in ranked if row["history_eligibility"] == "eligible_metadata_proxy"
                          and row["symbol"] not in MAJORS]
    selected_nonmajors = eligible_nonmajors[:20]
    selected_symbols = tuple(MAJORS) + tuple(row["symbol"] for row in selected_nonmajors)
    payload = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "selection_asof": "2024-12-31",
        "market": "Binance USD-M perpetual, USDT-quoted",
        "method": "9 fixed majors plus 20 highest Dec-2024 summed 1h quote_volume among other listed USDT symbols; deterministic quote-volume descending then symbol ascending tie-break",
        "selection_bias": "Retrospective pre-2025 calibration cohort. December 2024 ranking uses the full selection month; it is not an investable universe known on 2024-10-01.",
        "history_policy": "Provisional eligibility requires listed Sep-Dec 2024 monthly archives and exact contiguous 744-row December coverage. The earliest archive actual first row and Sep-Nov row continuity are not verified; confirm earliest row >=90 days before 2025 before treating the cohort as frozen.",
        "symbols": list(symbol_list),
        "selected_symbols": list(selected_symbols),
        "majors": list(MAJORS),
        "selected_nonmajors": selected_nonmajors,
        "ranked_markets": ranked,
        "december_archives": list(tasks.values()),
        "market_metadata": markets,
        "source_symbols_sha256": hashlib.sha256("\n".join(symbol_list).encode()).hexdigest(),
        "status": "pending_first_row_verification" if len(selected_nonmajors) == 20 and all(
            next((r["history_eligibility"] == "eligible_metadata_proxy" for r in ranked if r["symbol"] == m), False)
            for m in MAJORS) else "incomplete",
    }
    _save_progress(target, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/portfolio/manifests/robustness_dec2024.json")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args(argv)
    result = build_universe(args.manifest, workers=args.workers)
    print(json.dumps({"status": result["status"], "symbols": len(result["symbols"]),
                      "selected_symbols": result["selected_symbols"],
                      "eligible_nonmajors": len(result["selected_nonmajors"]),
                      "manifest": args.manifest}, sort_keys=True))
    return 0 if result["status"] in {"complete", "pending_first_row_verification"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
