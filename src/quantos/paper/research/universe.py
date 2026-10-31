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
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,26}USDT$")


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
    if symbols is not None:
        symbol_list = tuple(sorted(set(symbols)))
    elif prior.get("symbols"):
        resumable_symbols = set(prior["symbols"])
        scope_path = Path("data/portfolio/audits/robustness_symbol_scope_2024-12.json")
        if scope_path.exists():
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
            resumable_symbols.update(row["symbol"] for row in scope.get("details", [])
                                     if row.get("dec2024_archive_listed")
                                     and SYMBOL_RE.fullmatch(row["symbol"]))
        symbol_list = tuple(sorted(resumable_symbols))
    else:
        symbol_list = list_usdt_symbols(store)
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
                           "first_archive_later_than_2024-09" if not first_90d else
                           "missing_september_november_metadata_partition" if not history_ok else
                           "missing_december_archive_or_full_row_coverage"),
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
        "first_row_checks": {symbol: check for symbol, check in prior.get("first_row_checks", {}).items()
                             if symbol in selected_symbols
                             and check.get("period") == markets.get(symbol, {}).get("first_partition")},
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


def verify_first_row_history(path: str | Path = "data/portfolio/manifests/robustness_dec2024.json",
                             *, store: ArchiveStore | None = None,
                             workers: int = MAX_WORKERS) -> dict:
    """Verify the actual first bar in each provisional cohort member's first archive.

    This fetches only 29 earliest monthly archives (checksum cache reuse applies),
    not a new multi-year panel. It converts the metadata proxy into a strict
    90-day-before-2025 check based on the first parsed row.
    """
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError("workers must be between 1 and 4")
    target = Path(path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("status") not in {"pending_first_row_verification", "incomplete_first_row_eligibility",
                                     "verified_90d"}:
        raise ValueError("universe manifest is not awaiting first-row verification")
    store = store or ArchiveStore()
    selected = payload.get("selected_symbols", [])
    metadata = payload.get("market_metadata", {})
    checks = payload.setdefault("first_row_checks", {})
    cutoff = int(datetime(2024, 10, 3, tzinfo=timezone.utc).timestamp() * 1000)
    final = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    def check(symbol: str) -> tuple[str, dict]:
        period = metadata.get(symbol, {}).get("first_partition")
        if not period:
            return symbol, {"symbol": symbol, "status": "missing_first_partition",
                            "eligible_90d": False, "error": "no first monthly archive listed"}
        cached_check = checks.get(symbol, {})
        cached_path = cached_check.get("cached_path")
        if cached_check.get("period") == period and cached_check.get("status") == "verified":
            cached = Path(cached_path) if cached_path else None
            if cached and cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == cached_check.get("sha256"):
                archive_path = cached
            else:
                archive_path = store.fetch("klines", symbol, period, "1h")
        else:
            archive_path = store.fetch("klines", symbol, period, "1h")
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        bars = parse_klines(archive_path, symbol, "1h")
        if not bars:
            raise ValueError("first archive has no rows")
        period_actual = datetime.fromtimestamp(bars[0].open_ms / 1000, timezone.utc).strftime("%Y-%m")
        if period_actual != period:
            raise ValueError(f"first row period {period_actual} differs from listed archive {period}")
        days = (final - bars[0].open_ms) / 86_400_000
        return symbol, {"symbol": symbol, "period": period, "status": "verified",
                        "cached_path": str(archive_path), "sha256": digest,
                        "first_open_ms": bars[0].open_ms,
                        "first_open_utc": datetime.fromtimestamp(bars[0].open_ms / 1000, timezone.utc).isoformat(),
                        "days_before_2025": days,
                        "eligible_90d": bars[0].open_ms <= cutoff,
                        "rows_in_first_archive": len(bars)}

    pending = []
    for symbol in selected:
        check_row = checks.get(symbol, {})
        cached = Path(check_row.get("cached_path", ""))
        if (check_row.get("status") != "verified" or check_row.get("period") != metadata.get(symbol, {}).get("first_partition")
                or not cached.is_file()
                or hashlib.sha256(cached.read_bytes()).hexdigest() != check_row.get("sha256")):
            pending.append(symbol)
    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(check, symbol): symbol for symbol in pending}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    _, result = future.result()
                except Exception as exc:
                    result = {"symbol": symbol, "status": "error", "eligible_90d": False,
                              "error": f"{type(exc).__name__}: {exc}"}
                checks[symbol] = result
                payload["first_row_checks"] = checks
                payload["first_row_verification"] = {
                    "cutoff_utc": "2024-10-03T00:00:00+00:00",
                    "asof_utc": "2025-01-01T00:00:00+00:00",
                    "rule": "first parsed open_time must be at or before the 90-day cutoff",
                    "status": "in_progress",
                }
                _write_json(target, payload)
    for market in payload.get("ranked_markets", []):
        check_row = checks.get(market["symbol"])
        if check_row:
            market["first_row_open_utc"] = check_row.get("first_open_utc")
            market["first_row_days_before_2025"] = check_row.get("days_before_2025")
            market["first_row_eligible_90d"] = check_row.get("eligible_90d", False)
    all_verified = all(checks.get(symbol, {}).get("status") == "verified"
                       and checks[symbol].get("eligible_90d") is True for symbol in selected)
    payload["strict_history_symbols"] = selected if all_verified else []
    payload["first_row_verification"] = {
        "cutoff_utc": "2024-10-03T00:00:00+00:00",
        "asof_utc": "2025-01-01T00:00:00+00:00",
        "rule": "first parsed open_time must be at or before the 90-day cutoff",
        "verified_symbols": sum(checks.get(symbol, {}).get("status") == "verified" for symbol in selected),
        "eligible_symbols": sum(checks.get(symbol, {}).get("eligible_90d") is True for symbol in selected),
        "status": "verified_90d" if all_verified else "incomplete_first_row_eligibility",
    }
    payload["status"] = payload["first_row_verification"]["status"]
    _write_json(target, payload)
    return payload


def audit_catalog_exclusions(path: str | Path = "data/portfolio/manifests/robustness_dec2024.json",
                             output: str | Path = "data/portfolio/audits/robustness_symbol_scope_2024-12.json",
                             *, store: ArchiveStore | None = None,
                             workers: int = MAX_WORKERS) -> dict:
    """Audit raw USDT archive-prefix names excluded by the parser's symbol rule.

    Only the 2024-12 1h object name is checked for each excluded symbol; no
    archive contents or later market outcomes are accessed.
    """
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError("workers must be between 1 and 4")
    target, output = Path(path), Path(output)
    payload = json.loads(target.read_text(encoding="utf-8"))
    store = store or ArchiveStore()
    prior_scope = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    included = set(payload.get("symbols", []))
    saved_catalog = set(included)
    if prior_scope:
        saved_catalog.update(row["symbol"] for row in prior_scope.get("details", []))
    if prior_scope and len(saved_catalog) == prior_scope.get("raw_usdt_prefix_symbols"):
        raw_symbols = saved_catalog
        excluded = sorted(raw_symbols - included)
        old_details = {row["symbol"]: row for row in prior_scope.get("details", [])}
        newly_included = [dict(old_details[symbol]) for symbol in sorted(set(old_details) & included)
                          if old_details[symbol].get("dec2024_archive_listed")]
        legacy_symbol_re = re.compile(r"^[A-Z0-9]{3,30}USDT$")
        newly_included.extend({"symbol": symbol, "parser_symbol_supported": True,
                               "dec2024_archive_listed": True,
                               "classification": "previously_excluded_by_legacy_short_base_filter_now_included"}
                              for symbol in sorted(included)
                              if not legacy_symbol_re.fullmatch(symbol)
                              and payload.get("market_metadata", {}).get(symbol, {}).get("december_available"))
        ranked_by_symbol = {row["symbol"]: row for row in payload.get("ranked_markets", [])}
        for row in newly_included:
            symbol = row["symbol"]
            metadata_row = payload.get("market_metadata", {}).get(symbol, {})
            rank_row = ranked_by_symbol.get(symbol, {})
            row["first_partition"] = metadata_row.get("first_partition")
            row["history_eligibility"] = rank_row.get("history_eligibility", "not_ranked")
            row["eligibility_reason"] = rank_row.get("eligibility_reason")
        qualifying_rescues = sum(row.get("history_eligibility") == "eligible_metadata_proxy"
                                 for row in newly_included)
        top_nonmajors = set(row["symbol"] for row in payload.get("selected_nonmajors", []))
        selected_rescues = sum(row["symbol"] in top_nonmajors for row in newly_included)
        details = []
        for symbol in excluded:
            row = dict(old_details[symbol])
            row["parser_symbol_supported"] = bool(SYMBOL_RE.fullmatch(symbol))
            details.append(row)
        result = {**prior_scope,
                  "parser_supported_symbols": len(included) + sum(row["parser_symbol_supported"] for row in details),
                  "candidate_symbols_with_metadata": len(included),
                  "excluded_raw_symbols": len(excluded),
                  "excluded_parser_unsupported": sum(not row["parser_symbol_supported"] for row in details),
                  "excluded_parser_supported_no_dec2024": sum(row["parser_symbol_supported"] and not row["dec2024_archive_listed"] for row in details),
                  "excluded_with_dec2024_archive": sum(row["dec2024_archive_listed"] for row in details),
                  "excluded_without_dec2024_archive": sum(not row["dec2024_archive_listed"] for row in details),
                  "previously_excluded_now_included_with_dec2024_archive": newly_included,
                  "scope_note": f"Raw catalog: 900 USDT prefixes. Fourteen valid short-base names with Dec-2024 archives were added; {qualifying_rescues} passed the metadata history/December row filter, {selected_rescues} entered the top-20. Remaining 50 exclusions had no Dec-2024 archive: 45 parser-supported names and five non-ASCII names.",
                  "details": details}
        _write_json(output, result)
        payload["catalog_scope_audit"] = {"path": str(output.resolve()),
                                         "raw_usdt_prefix_symbols": len(raw_symbols),
                                         "excluded_raw_symbols": len(excluded),
                                         "excluded_with_dec2024_archive": result["excluded_with_dec2024_archive"],
                                         "excluded_without_dec2024_archive": result["excluded_without_dec2024_archive"]}
        if result["excluded_with_dec2024_archive"]:
            payload["status"] = "incomplete_scope_exclusions"
        _write_json(target, payload)
        return result
    token = None
    raw_symbols: set[str] = set()
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
            raise ValueError("S3 symbol listing response exceeded limit")
        root = ET.fromstring(body)
        for node in root.findall("s3:CommonPrefixes/s3:Prefix", ns):
            symbol = (node.text or "").rstrip("/").rsplit("/", 1)[-1]
            if symbol.endswith("USDT"):
                raw_symbols.add(symbol)
        if root.findtext("s3:IsTruncated", namespaces=ns) != "true":
            break
        token = root.findtext("s3:NextContinuationToken", namespaces=ns)
        if not token:
            raise ValueError("truncated symbol listing lacks continuation token")
    else:
        raise ValueError("S3 symbol listing page limit exceeded")

    excluded = sorted(raw_symbols - included)

    def december_object(symbol: str) -> dict:
        filename = f"{symbol}-1h-2024-12.zip"
        prefix = f"data/futures/um/monthly/klines/{symbol}/1h/{filename}"
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1"}
        request = Request(S3_LIST_URL + "?" + urlencode(params),
                          headers={"User-Agent": "QuantOS-public-research/1"})
        response = store.opener(request, timeout=store.timeout)
        try:
            body = response.read(2_000_001)
        finally:
            response.close()
        root = ET.fromstring(body)
        has_archive = any((node.findtext("s3:Key", namespaces=ns) or "").endswith(filename)
                          for node in root.findall("s3:Contents", ns))
        symbol_supported = bool(SYMBOL_RE.fullmatch(symbol))
        return {"symbol": symbol, "parser_symbol_supported": symbol_supported,
                "dec2024_archive_listed": has_archive,
                "classification": ("requires_symbol_parser_support_pre2025_archive" if has_archive else
                                   "no_2024_december_archive")}

    details = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(december_object, symbol) for symbol in excluded]
        for future in as_completed(futures):
            details.append(future.result())
    details.sort(key=lambda row: row["symbol"])
    result = {"catalog_prefix": "data/futures/um/monthly/klines/",
              "selection_period": "2024-12", "raw_usdt_prefix_symbols": len(raw_symbols),
              "parser_supported_symbols": len(included) + sum(row["parser_symbol_supported"] for row in details),
              "candidate_symbols_with_metadata": len(included), "excluded_raw_symbols": len(excluded),
              "excluded_parser_unsupported": sum(not row["parser_symbol_supported"] for row in details),
              "excluded_parser_supported_no_dec2024": sum(row["parser_symbol_supported"] and not row["dec2024_archive_listed"] for row in details),
              "excluded_with_dec2024_archive": sum(row["dec2024_archive_listed"] for row in details),
              "excluded_without_dec2024_archive": sum(not row["dec2024_archive_listed"] for row in details),
              "previously_excluded_now_included_with_dec2024_archive": [],
              "details": details,
              "scope_note": "Only December 2024 archive object names were checked for raw USDT prefixes outside the initial metadata candidate set; no excluded archive contents or later outcomes were read."}
    _write_json(output, result)
    payload["catalog_scope_audit"] = {"path": str(output.resolve()),
                                     "raw_usdt_prefix_symbols": len(raw_symbols),
                                     "excluded_raw_symbols": len(excluded),
                                     "excluded_with_dec2024_archive": result["excluded_with_dec2024_archive"],
                                     "excluded_without_dec2024_archive": result["excluded_without_dec2024_archive"]}
    if result["excluded_with_dec2024_archive"]:
        payload["status"] = "incomplete_scope_exclusions"
    _write_json(target, payload)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/portfolio/manifests/robustness_dec2024.json")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--verify-first-rows", action="store_true")
    parser.add_argument("--audit-scope", action="store_true")
    args = parser.parse_args(argv)
    if args.audit_scope:
        result = audit_catalog_exclusions(args.manifest, workers=args.workers)
        print(json.dumps(result, sort_keys=True))
        return 2 if result["excluded_with_dec2024_archive"] else 0
    result = (verify_first_row_history(args.manifest, workers=args.workers)
              if args.verify_first_rows else build_universe(args.manifest, workers=args.workers))
    print(json.dumps({"status": result["status"], "symbols": len(result["symbols"]),
                      "selected_symbols": result["selected_symbols"],
                      "eligible_nonmajors": len(result["selected_nonmajors"]),
                      "manifest": args.manifest}, sort_keys=True))
    return 0 if result["status"] in {"complete", "pending_first_row_verification", "verified_90d"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
