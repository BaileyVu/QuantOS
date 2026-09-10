# QuantOS

QuantOS V1 is a small, research-driven quantitative trading engine for Binance Spot. Its frozen specification prioritizes reproducibility, capital preservation, and one explainable production strategy.

## Current implementation

Phase 1 — Foundation is implemented. Phase 2A adds provider-independent Market Data dataset identity and deterministic canonical-candle sequence validation. Phase 2B adds Binance Spot historical-kline normalization, safe provider-specific range pagination, orchestration into validated in-memory canonical sequences, checksum-verified in-memory normalization of individual daily archives, and whole-day multi-day archive acquisition. Phase 2C1 adds the canonical immutable Parquet persistence primitive. Phase 2C2 adds Application orchestration for persisting acquired history, immutable incremental dataset versions, and typed DuckDB range queries over one explicitly selected canonical Parquet file. Trading, evaluation, and live market data are not implemented yet; Phase 2 is not complete.

`BinanceSpotDailyArchiveRangeFetcher` accepts BTCUSDT or ETHUSDT, interval `1m`, and an increasing range of UTC midnight bounds with an exclusive end. It calls the single-day archive adapter once per date in ascending order and concatenates its rows unchanged, including duplicates, gaps, and provider ordering. Empty days contribute no rows; an archive failure stops acquisition immediately. The existing single-day adapter owns checksum verification and timestamp-era normalization. Pass the range fetcher to `ingest_historical_range` with explicit source, schema version, and ingestion version for dataset identity, canonical validation, and range completeness checks. The range fetcher itself adds no persistence, repairs, retries, or REST fallback.

`ParquetCandleDatasetStore(root: Path)` stores only `ValidatedCandleSequence` values. Canonical validation binds the dataset identity's start/end times to the actual first/last candle open times, after checking sequence integrity. Use `dataset_path(identity)` to calculate a path, `write(sequence)` to persist, and `read(path)` to reconstruct and canonically validate a stored dataset. The root is supplied by the caller, such as the existing configuration's `data_dir`.

Historical acquisition can now be persisted through Application orchestration with an injected canonical dataset writer. Incremental extension starts exactly one minute after the selected base dataset's final open time, fetches only the requested extension, concatenates it without sorting or repair, validates the complete combined sequence, and publishes it under an explicit new ingestion version. Extension creates a new canonical path; the base dataset remains unchanged and authoritative if acquisition, validation, or publication fails.

Storage uses the explicit `parquet-v1` schema: non-null columns, UTC microsecond timestamps, exact `decimal128(38,18)` market values, and `int64` trade counts. Values that cannot be represented exactly are rejected. Timestamp inputs must be built-in UTC `datetime` objects; extended timestamp types are rejected rather than silently losing sub-microsecond precision. All eight identity dimensions are stored as `quantos.*` metadata. Their SHA-256 digest uses UTF-8 JSON with sorted keys, compact separators, ASCII escaping, and UTC ISO timestamps with six fractional digits and `+00:00`.

Canonical paths are `<root>/market_data/candles/parquet-v1/{symbol}/{timeframe}/{full-sha256}.parquet`. Free-form identity metadata is never used as a directory name. Writes use Parquet 2.6, Zstandard compression, stored Arrow schema, and page checksums. A unique same-directory `.tmp` file is flushed, read back with checksum verification, checked against the physical schema and identity, canonically validated, and compared with the input before atomic hard-link publication. Existing identical datasets are idempotent successes without rewriting; different contents raise `DatasetCollisionError`, and corrupt existing files are never replaced. Filesystems without atomic hard-link support fail closed. Temporary files are cleaned when possible, and cleanup failures are surfaced.

DuckDB is a read-only analytical query layer over one explicitly selected canonical Parquet dataset. Each typed `[start_open_time, end_open_time_exclusive)` query first passes the selected file through the complete Parquet integrity reader, then uses a private in-memory DuckDB connection with parameterized bounds, explicit columns, and deterministic open-time ordering. Results retain exact Decimal values and UTC microsecond timestamps and are canonically validated. DuckDB does not scan or combine dataset versions, maintain a persistent database or catalog, create an authoritative copy, or replace Parquet as canonical truth.

## Specification

[000_READ_FIRST.md](./docs/000_READ_FIRST.md) is the highest-priority V1 Source of Truth. Documents [001](./docs/001_PRODUCT_REQUIREMENTS.md) through [007](./docs/007_VALIDATION_BACKTESTING.md) are coequal frozen specifications; [008](./docs/008_IMPLEMENTATION_GUIDE.md) is subordinate implementation guidance.

## Structure

```text
src/quantos/
  domain/          # Six V1 business ownership areas and canonical contracts
  application/     # Runtime coordination
  infrastructure/  # Binance adapters, Parquet storage, DuckDB queries, configuration, and logging
  interfaces/      # Local CLI
configs/           # Safe example configuration
tests/             # Unit, integration, and validation tests
```

## Setup and verification

QuantOS requires Python 3.11 or later. Its storage dependencies are pinned to `pyarrow==25.0.1` and `duckdb==1.5.5`.

```bash
python -m pip install -e .
python -m unittest discover -s tests -t . -v
python -m quantos --config configs/default.toml
```

The default configuration starts in paper mode, logs startup and shutdown as JSON, and exits without connecting to Binance or performing any trading action.
