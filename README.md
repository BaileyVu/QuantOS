# QuantOS

QuantOS V1 is a small, research-driven quantitative trading engine for Binance Spot. Its frozen specification prioritizes reproducibility, capital preservation, and one explainable production strategy.

## Current implementation

Phase 1 — Foundation is implemented. **Phase 2 — Market Data COMPLETE**: historical acquisition, immutable persistence, typed queries, and real public live-stream acceptance have passed. Phase 2A adds provider-independent Market Data dataset identity and deterministic canonical-candle sequence validation. Phase 2B adds Binance Spot historical-kline normalization, safe provider-specific range pagination, orchestration into validated in-memory canonical sequences, checksum-verified in-memory normalization of individual daily archives, and whole-day multi-day archive acquisition. Phase 2C1 adds the canonical immutable Parquet persistence primitive. Phase 2C2 adds Application orchestration for persisting acquired history, immutable incremental dataset versions, and typed DuckDB range queries over one explicitly selected canonical Parquet file. Phase 2D adds Binance Spot live BTCUSDT/ETHUSDT 1m kline streaming. Phase 3A adds the deterministic candidate Feature Engine described below. Alpha/model, trading, and evaluation behavior remain unimplemented.

`BinanceSpotLiveMarketDataAdapter(symbols=["BTCUSDT", "ETHUSDT"], interval="1m")` is an async iterator of the existing canonical `MarketEvent` contract. It supports either symbol individually or both through one combined connection. Its deterministic provider-owned URL uses the public market-data-only endpoint `wss://data-stream.binance.vision/stream?streams=btcusdt@kline_1m/ethusdt@kline_1m&timeUnit=MICROSECOND`, with UTC stream names. Construction performs no network I/O; iteration starts the connection.

Only an explicit Binance closed flag (`k.x == true`) can produce a canonical Candle and MarketEvent. Partial/open candles are never exposed downstream, even if a local clock or the event timestamp appears to be past close. The combined envelope, subscription, event type, symbols, interval, and required JSON field types are checked strictly. Prices/volumes are converted directly from decimal strings; E/t/T are converted from integer Unix microseconds without floats. Candle validation enforces canonical OHLCV rules, open times must be exact UTC minutes, supplied close times are preserved, and each MarketEvent timestamp is the provider E timestamp at or after candle close.

Live continuity is independent per symbol. After the first valid completion, each new candle must start exactly one minute after the previous one. An identical canonical Candle at the same open time is a transport duplicate and emits nothing, even if its provider event time changed. Conflicting duplicates, older completions, missing minutes, or invalid payloads fail closed and terminate the session. Partial updates do not change completed-candle state. No sorting, candle synthesis, historical backfill, or automatic gap repair occurs.

Transport disconnects, normal connection expiry, and the documented combined `!serverShutdown` event reconnect with delays of 1, 2, 4, 8, 16, then at most 30 seconds. New completed candles reset the delay; merely opening a connection or receiving duplicates/partials does not. The same session preserves continuity state across reconnects and keeps at most one active connection. Protocol/payload failures and fatal handshake failures are surfaced as `BinanceLiveMarketDataError`. Cancellation propagates and closes the connection. Client keepalive pings are disabled while library responses to server Ping frames remain active. Lifecycle, completions, reconnects, and integrity failures use the existing structured logging path.

Use `contextlib.aclosing` when consuming a bounded number of events so early exit closes the connection:

```python
from contextlib import aclosing
from quantos.infrastructure.binance import BinanceSpotLiveMarketDataAdapter

async def first_completed_event():
    async with aclosing(BinanceSpotLiveMarketDataAdapter(symbols=["BTCUSDT"])) as events:
        return await anext(events)
```

The live adapter is public market data only: it uses no credentials, account streams, or order functionality, and does not persist live candles. Historical Parquet/DuckDB behavior is unchanged. Offline tests use injected fake connections; real Binance network acceptance is separate.

`BinanceSpotDailyArchiveRangeFetcher` accepts BTCUSDT or ETHUSDT, interval `1m`, and an increasing range of UTC midnight bounds with an exclusive end. It calls the single-day archive adapter once per date in ascending order and concatenates its rows unchanged, including duplicates, gaps, and provider ordering. Empty days contribute no rows; an archive failure stops acquisition immediately. The existing single-day adapter owns checksum verification and timestamp-era normalization. Pass the range fetcher to `ingest_historical_range` with explicit source, schema version, and ingestion version for dataset identity, canonical validation, and range completeness checks. The range fetcher itself adds no persistence, repairs, retries, or REST fallback.

`ParquetCandleDatasetStore(root: Path)` stores only `ValidatedCandleSequence` values. Canonical validation binds the dataset identity's start/end times to the actual first/last candle open times, after checking sequence integrity. Use `dataset_path(identity)` to calculate a path, `write(sequence)` to persist, and `read(path)` to reconstruct and canonically validate a stored dataset. The root is supplied by the caller, such as the existing configuration's `data_dir`.

Historical acquisition can now be persisted through Application orchestration with an injected canonical dataset writer. Incremental extension starts exactly one minute after the selected base dataset's final open time, fetches only the requested extension, concatenates it without sorting or repair, validates the complete combined sequence, and publishes it under an explicit new ingestion version. Extension creates a new canonical path; the base dataset remains unchanged and authoritative if acquisition, validation, or publication fails.

Storage uses the explicit `parquet-v1` schema: non-null columns, UTC microsecond timestamps, exact `decimal128(38,18)` market values, and `int64` trade counts. Values that cannot be represented exactly are rejected. Timestamp inputs must be built-in UTC `datetime` objects; extended timestamp types are rejected rather than silently losing sub-microsecond precision. All eight identity dimensions are stored as `quantos.*` metadata. Their SHA-256 digest uses UTF-8 JSON with sorted keys, compact separators, ASCII escaping, and UTC ISO timestamps with six fractional digits and `+00:00`.

Canonical paths are `<root>/market_data/candles/parquet-v1/{symbol}/{timeframe}/{full-sha256}.parquet`. Free-form identity metadata is never used as a directory name. Writes use Parquet 2.6, Zstandard compression, stored Arrow schema, and page checksums. A unique same-directory `.tmp` file is flushed, read back with checksum verification, checked against the physical schema and identity, canonically validated, and compared with the input before atomic hard-link publication. Existing identical datasets are idempotent successes without rewriting; different contents raise `DatasetCollisionError`, and corrupt existing files are never replaced. Filesystems without atomic hard-link support fail closed. Temporary files are cleaned when possible, and cleanup failures are surfaced.

DuckDB is a read-only analytical query layer over one explicitly selected canonical Parquet dataset. Each typed `[start_open_time, end_open_time_exclusive)` query first passes the selected file through the complete Parquet integrity reader, then uses a private in-memory DuckDB connection with parameterized bounds, explicit columns, and deterministic open-time ordering. Results retain exact Decimal values and UTC microsecond timestamps and are canonically validated. DuckDB does not scan or combine dataset versions, maintain a persistent database or catalog, create an authoritative copy, or replace Parquet as canonical truth.

## Phase 3A — Candidate Feature Engine

`quantos.domain.features.compute_feature_vector(candles, *, decision_time)` is a stateless Domain API shared by historical replay and later live consumers. It takes a non-empty built-in tuple of canonical `Candle` objects for one V1 symbol and interval `1m`, with strictly ascending opens exactly one minute apart. Every Candle contract is revalidated. Input order is authoritative: no sorting, deduplication, repair, or future-candle filtering occurs. Market Data contracts are unchanged.

The explicit `decision_time` must be a built-in timezone-aware UTC `datetime`. Every supplied candle must already be complete at that timestamp; the final input is the current decision candle. The resulting immutable `FeatureVector.timestamp` equals the supplied timestamp. There is no wall-clock access, provider/storage coupling, network I/O, or feature persistence.

`FEATURE_VERSION = "candidate-v1"` defines exactly ten candidate features, always in this `FEATURE_NAMES` order:

| Feature | Calculation at final candle t |
|---|---|
| `return_1m` | `close[t] / close[t-1] - 1` |
| `return_15m` | `close[t] / close[t-15] - 1` |
| `realized_volatility_20m` | Decimal square root of the mean squared simple return over 20 transitions |
| `efficiency_ratio_20m` | Absolute 20-minute net close change divided by the sum of absolute changes |
| `sma_spread_5_20` | Mean of last 5 closes divided by mean of last 20 closes, minus 1 |
| `momentum_balance_14` | Sum of last 14 close changes divided by their absolute sum; not RSI |
| `volume_ratio_20` | Current volume divided by mean volume over last 20 candles, minus 1 |
| `true_range_pct` | Maximum of high-low, absolute high-previous-close and absolute low-previous-close, divided by previous close |
| `range_position_20` | Current close minus lowest low, divided by highest high minus lowest low, over last 20 candles |
| `completed_5m_return` | Last close divided by first open of the same latest fully completed UTC five-minute bucket, minus 1 |

`MIN_HISTORY = 21`: twenty close-to-close transitions require exactly 21 candles. A complete exact UTC five-minute bucket is also required. For minute-aligned, contiguous completed inputs, 21 candles already contain that context; no additional padding is imposed. A bucket beginning at UTC minute M divisible by five contains exactly opens M through M+4. It is available only once its final candle is complete at `decision_time`. While M+5 through M+8 are forming the next bucket, the prior complete bucket supplies context. Shifted opens are never rounded into a UTC bucket, and a partial bucket is never aggregated.

Insufficient history, absent complete UTC context, or any exactly zero mathematical denominator returns `None` for the entire vector. No partial vector, sentinel, NaN, zero imputation, or forward/backward fill is emitted. Malformed candles and causality violations raise `FeatureEngineError`, including during warm-up. Callers must treat unavailable features as controlled HOLD/NO_TRADE input; Phase 3A adds no trading behavior.

All calculations use Decimal arithmetic in a fresh explicit local context: precision 34, `ROUND_HALF_EVEN`, `Emin=-999999`, `Emax=999999`, `capitals=1`, `clamp=0`, cleared flags, and traps for invalid operations, division by zero, overflow, inexact underflow, and float operations. Other traps are disabled. Ambient and default Decimal settings are neither inherited nor mutated. Arithmetic failures raise `FeatureEngineError`; every ready value is a finite built-in Decimal, without display quantization. Identical canonical inputs, decision time, and version produce identical ordered Decimal values.

These are candidate definitions with **no claim of proven predictive value**. Production promotion and pruning require later temporal/out-of-sample evaluation of incremental usefulness, stability, and redundancy. Removing or replacing features requires a new feature version. Phase 3A includes no labels, targets, training, model, or strategy behavior.

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

QuantOS requires Python 3.11 or later. Runtime dependencies are pinned to `pyarrow==25.0.1`, `duckdb==1.5.5`, and `websockets==17.1`.

```bash
python -m pip install -e .
python -m unittest discover -s tests -t . -v
python -m quantos --config configs/default.toml
```

The default configuration starts in paper mode, logs startup and shutdown as JSON, and exits without connecting to Binance or performing any trading action.
