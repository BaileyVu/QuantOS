"""Public archive acquisition for V1-T5; no strategy outcomes."""
from pathlib import Path
from datetime import datetime, timedelta
from hashlib import sha256
from quantos.domain.market_data import DatasetIdentity, validate_candle_sequence

def acquire_archive_days(root: Path, start: datetime, end: datetime,
                         *, workers: int = 6) -> list[dict]:
    """Acquire checksum-verified daily partitions with immutable raw provenance.

    Return per-day partition identities or explicit errors. Missing minutes split
    canonical partitions; duplicate/reordered rows fail that entire day. Reuses
    the existing provider adapter and canonical validation/publication machinery.
    """
    from concurrent.futures import ThreadPoolExecutor
    import json
    from urllib.request import urlopen
    from quantos.infrastructure.binance.daily_archive import BinanceSpotDailyArchiveAdapter
    from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore, dataset_id

    raw = root / "raw_daily"
    raw.mkdir(parents=True, exist_ok=True)
    store = ParquetCandleDatasetStore(root)

    def one(day):
        records = []
        def get(url, timeout):
            path = raw / url.rsplit("/", 1)[-1]
            if path.exists():
                payload = path.read_bytes()
            else:
                with urlopen(url, timeout=timeout) as response:
                    payload = response.read()
                with path.open("xb") as target:
                    target.write(payload)
            records.append({"url": url, "sha256": sha256(payload).hexdigest(), "path": str(path)})
            return payload
        report = {"day": day.isoformat(), "resources": records, "partitions": []}
        try:
            candles = BinanceSpotDailyArchiveAdapter(http_get=get, timeout_seconds=30).fetch_daily_klines(
                symbol="BTCUSDT", interval="1m", archive_date=day)
            cuts = [0]
            for i in range(1, len(candles)):
                distance = candles[i].open_time - candles[i - 1].open_time
                if distance <= timedelta(0):
                    raise ValueError("duplicate or reordered archive row")
                if distance != timedelta(minutes=1):
                    cuts.append(i)
            cuts.append(len(candles))
            for left, right in zip(cuts[:-1], cuts[1:], strict=True):
                group = candles[left:right]
                if not group:
                    continue
                identity = DatasetIdentity("BTCUSDT", "1m", group[0].open_time, group[-1].open_time,
                                           "binance-spot-daily-archive", "candle-v1",
                                           "v1t5-checksum-daily-v1")
                validated = validate_candle_sequence(identity, group)
                path = store.write(validated)
                report["partitions"].append({"path": str(path), "dataset_id": dataset_id(validated.identity),
                    "start": identity.start_time.isoformat(), "end": identity.end_time.isoformat(),
                    "rows": len(group)})
            report["rows"] = len(candles)
            report["status"] = "validated" if candles else "empty"
        except Exception as error:
            report["status"] = "failed"
            report["error"] = str(error)
        return report

    days = [start.date() + timedelta(days=i) for i in range((end.date() - start.date()).days)]
    reports = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for report in pool.map(one, days):
            reports.append(report)
            evidence = root / "data_integrity" / f"archive-{report['day']}.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            if not evidence.exists():
                with evidence.open("x") as target:
                    json.dump(report, target, indent=2)
            if len(reports) % 50 == 0:
                print(f"validated archive attempts {len(reports)}/{len(days)} through {report['day']}", flush=True)
    return reports
