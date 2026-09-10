# QuantOS

QuantOS V1 is a small, research-driven quantitative trading engine for Binance Spot. Its frozen specification prioritizes reproducibility, capital preservation, and one explainable production strategy.

## Current implementation

**Phase 1 — Foundation COMPLETE. Phase 2 — Market Data COMPLETE. Phase 3 — Feature Engine COMPLETE. Phase 4A — Targets and training datasets COMPLETE.** Historical acquisition, immutable persistence, typed queries, live public market data, deterministic features, and real-data target/training-dataset acceptance have passed. Phase 2A adds provider-independent Market Data dataset identity and deterministic canonical-candle sequence validation. Phase 2B adds Binance Spot historical-kline normalization, safe provider-specific range pagination, orchestration into validated in-memory canonical sequences, checksum-verified in-memory normalization of individual daily archives, and whole-day multi-day archive acquisition. Phase 2C1 adds the canonical immutable Parquet persistence primitive. Phase 2C2 adds Application orchestration for persisting acquired history, immutable incremental dataset versions, and typed DuckDB range queries over one explicitly selected canonical Parquet file. Phase 2D adds Binance Spot live BTCUSDT/ETHUSDT 1m kline streaming. Phase 3A implements the deterministic candidate Feature Engine. Phase 4A adds causal targets and supervised dataset construction. Phase 4B implements candidate model infrastructure. Full Phase 4 remains incomplete until strategy decision logic is added.

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

## Phase 4A — Causal targets and supervised datasets

`quantos.domain.alpha.build_training_dataset(sequence)` accepts one actual `ValidatedCandleSequence`. It revalidates the source identity, validation status, every Candle contract, symbol/interval agreement, and exact ascending one-minute continuity. It retains the exact validated source `DatasetIdentity`, including all eight provenance dimensions. No source data is sorted, repaired, filled, or queried from storage.

The immutable Alpha-owned `TargetLabel` is a regression label with `TARGET_VERSION = "gross-next-open-to-close-5m-v1"` and `TARGET_HORIZON_MINUTES = 5`. For decision candle index t:

```text
value = candle[t+5].close / candle[t+1].open - 1
decision_time = candle[t].close_time
entry_reference_time = candle[t+1].open_time
exit_reference_time = candle[t+5].close_time
```

The next candle's opening price is the execution reference. The target is gross, with no fee or slippage adjustment, threshold, classification, or trading action. All label timestamps are built-in UTC datetimes. Entry must be strictly after the decision timestamp; input whose decision close reaches or overlaps the next open fails closed. Actual exit close timestamps are preserved without assuming a provider-era precision. Target arithmetic uses a fresh precision-34, `ROUND_HALF_EVEN` Decimal context with the same fixed exponent limits and traps as Phase 3; it does not inherit or mutate ambient/default settings. Targets are finite built-in Decimals, without float conversion or display quantization.

An immutable `TrainingExample` pairs the existing FeatureVector with its separate label. Symbols and decision timestamps must match; feature names/order and versions are checked. Only the final 21 candles ending at t are passed to the existing production Feature Engine, with decision time equal to candle[t].close_time. Future entry/exit values and label metadata never enter features.

Candidates run from **t=20 through t=N-6**, inclusive: `max(0, N-25)` decisions. A 25-candle source has zero candidates; 26 candles has one. For 1,440 candles, there are 1,415 candidates, with decision opens 00:20 through 23:54 for a midnight start. The final label enters at 23:55 and exits at the actual 23:59 candle close. Adjacent labels may overlap; candidates are not spaced five minutes apart. No purging, embargo, splitting, or random shuffling is introduced.

`TrainingDataset` contains the source identity, feature/target versions, horizon, an ordered tuple of examples, and an ordered `unavailable_feature_timestamps` tuple. Its `candidate_decision_count` equals examples plus unavailable decisions. Duplicate, out-of-range, mismatched, or unaccounted candidates are rejected. A valid source shorter than 26 candles returns an empty dataset. Feature Engine `None` results are explicitly recorded and processing continues, without partial or imputed examples.

A zero target entry open raises `TrainingDataError` and aborts construction, even if that candidate's features would be unavailable. Malformed inputs, incompatible/forged contracts, causality violations, and invalid target arithmetic also fail closed. Neither features nor targets are persisted by this Domain builder.

Phase 4A makes **no predictive-value claim**. Candidate features remain candidates pending temporal/out-of-sample evaluation. Its Domain builder produces no model artifact, strategy threshold, or BUY/SELL/HOLD decision. Phase 4B consumes these unchanged contracts.

## Phase 4B — Candidate model infrastructure

`build_purged_temporal_split(datasets, *, train_start, validation_start, validation_end_exclusive)` in Domain Alpha requires exactly one BTCUSDT and one ETHUSDT TrainingDataset and built-in UTC boundaries. It revalidates and snapshots both contracts, canonicalizes source order by symbol, and orders actual rows by `(feature.timestamp, symbol)`. The split retains source snapshots for revalidation; only its actual training and validation rows enter model matrices. Reversing source input order produces identical model inputs and artifacts.

Training decisions lie in `[train_start, validation_start)` and are retained only when the label exits **strictly before validation_start**. Otherwise their symbol/timestamp is recorded in `purged_training_boundary`. Validation decisions lie in `[validation_start, validation_end_exclusive)` and are retained only when the label exits **strictly before validation_end_exclusive**; crossing rows go into `purged_validation_tail`. No additional embargo is imposed. Historical feature lookback remains available. Unavailable features are recorded separately for each decision window without imputation. Both symbols must contribute actual rows to both matrices. Rows at or after validation end supply no training, validation metric, early stopping, or artifact input values.

`quantos.infrastructure.models` owns LightGBM, NumPy conversion, training, artifacts, and prediction. The model family is `lightgbm-gross-return-regressor-v1`: one shared BTC/ETH regression model predicting the unchanged five-minute gross next-open-to-close target. The matrix has exactly ten float64 columns in explicit `FEATURE_NAMES` order. Labels are a separate float64 vector; symbol is metadata. Nonfinite conversion fails closed. There is no fitted preprocessing, scaling, feature pruning, or extra symbol feature.

`LightGBMConfig` requires caller-supplied Decimal learning rate and L2 penalty, integer leaf/depth/minimum-row limits, boosting-round and early-stopping limits, and a random seed. Rates must be in `(0, 1]`, the L2 penalty nonnegative, leaves in `[2, 131072]`, other limits positive signed 32-bit integers, and the seed in `[0, 2147483647]`. Early stopping cannot exceed the boosting limit. The initial candidate configuration is `learning_rate=Decimal("0.05"), num_leaves=15, max_depth=5, min_data_in_leaf=50, lambda_l2=Decimal("1"), num_boost_round=200, early_stopping_rounds=20, random_seed=20260911`; it is not an optimal-parameter claim.

`train_model(split, config, *, code_version)` uses native `lightgbm.Dataset` and `lightgbm.train`, regression/RMSE/GBDT, CPU, one thread, deterministic column-wise histograms, full feature and bagging fractions, zero bagging frequency, L1=0, and max_bin=255. The caller seed explicitly controls seed, feature-fraction seed, bagging seed, and data seed. Validation is used only for RMSE and early stopping. The serialized model contains only the best iteration. The caller supplies code provenance; production code does not invoke Git. Training, publication, loading, and debug-level predictions use the existing structured logging path.

The returned immutable `ModelArtifact` holds native UTF-8 model bytes and canonical JSON metadata. Metadata records both complete source identities, windows, per-symbol row/exclusion counts and timestamps, feature/target versions, ordered feature names, caller configuration, resolved parameters, seed, best iteration, RMSE, code version, and Python/LightGBM/NumPy/SciPy versions. It contains no creation clock, host/user identity, or automatic repository path. RMSE uses the canonical round-trip float text representation.

`MODEL_ARTIFACT_SCHEMA_VERSION = "lightgbm-artifact-v1"`. The full SHA-256 artifact ID hashes canonical UTF-8 metadata (sorted keys, compact separators, ASCII escapes, UTC timestamps with six fractional digits), including the exact model-byte SHA-256. Both derived fields `artifact_id` and `model_version` are excluded from that hash to avoid circularity. `model_version` is `lightgbm-gross-return-regressor-v1:<full-artifact-id>`.

`ModelArtifactStore(root: Path).write(artifact)` requires a caller-selected root outside source control. It writes `<root>/models/lightgbm-artifact-v1/<full-artifact-id>/model.txt` and `metadata.json`. Each file is flushed and fsynced inside a unique temporary sibling directory; both are read and verified before atomic publication. On Windows, no-overwrite directory rename publishes the pair. Platforms without the implemented no-overwrite primitive fail closed. Existing identical artifacts succeed without rewriting or changing file mtimes; corrupt/different artifacts are never replaced. Temporary directories are cleaned on failure when possible.

`load_model(path)` verifies exactly the two expected regular files, rejects linked/reparse artifact paths, checks canonical metadata and directory identity, recomputes both hashes, validates versions/configuration/provenance/accounting, and verifies the native Booster's feature order, regression objective, and exact best-iteration tree count. There is no fallback model, global singleton, registry, pickle, or joblib. `artifact.load()` provides the same verification before persistence.

The resulting `VerifiedModel.predict(feature)` requires an actual, compatible FeatureVector with all ten finite Decimal values. One row crosses to float64 inside Infrastructure. A single finite raw regression output crosses back using `Decimal(repr(float(score)))`, without quantization. Repeated identical inputs produce exactly equal Decimal tuples. Same-software CPU training is tested for identical model bytes, metadata, identities, and predictions; cross-version, compiler, OS, or CPU-architecture artifact-hash equality is not promised.

This remains a **candidate model**. Validation RMSE is model-development metadata, not profitability or predictive-value evidence. No final test has been consumed, no model has been promoted, and no BUY/SELL/HOLD thresholds or after-cost strategy decisions exist. Phase 5 retains final-test, walk-forward, after-cost evaluation, and promotion evidence ownership.

## Specification

[000_READ_FIRST.md](./docs/000_READ_FIRST.md) is the highest-priority V1 Source of Truth. Documents [001](./docs/001_PRODUCT_REQUIREMENTS.md) through [007](./docs/007_VALIDATION_BACKTESTING.md) are coequal frozen specifications; [008](./docs/008_IMPLEMENTATION_GUIDE.md) is subordinate implementation guidance.

## Structure

```text
src/quantos/
  domain/          # Six V1 business ownership areas and canonical contracts
  application/     # Runtime coordination
  infrastructure/  # Binance, storage/query, model artifacts, configuration, and logging
  interfaces/      # Local CLI
configs/           # Safe example configuration
tests/             # Unit, integration, and validation tests
```

## Setup and verification

QuantOS requires Python 3.11 or later. Direct runtime dependencies are pinned to `pyarrow==25.0.1`, `duckdb==1.5.5`, `websockets==17.1`, `lightgbm==4.7.0`, `numpy==2.4.6`, and `scipy==1.17.1`. LightGBM also installs its required transitive `narwhals` dependency; no dataframe/sklearn extras are requested.

```bash
python -m pip install -e .
python -m unittest discover -s tests -t . -v
python -m quantos --config configs/default.toml
```

The default configuration starts in paper mode, logs startup and shutdown as JSON, and exits without connecting to Binance or performing any trading action.
