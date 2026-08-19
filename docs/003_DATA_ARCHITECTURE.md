# QuantOS — Data Architecture

## Document Status

**Status:** Frozen V1 Data Architecture
**Version:** 1.0
**Depends On:** `000_READ_FIRST.md`, `001_PRODUCT_REQUIREMENTS.md`, `002_SYSTEM_ARCHITECTURE.md`

---

# 1. Purpose

This document defines the V1 data architecture for QuantOS.

The data architecture must provide a reliable foundation for:

* historical research
* model training
* feature generation
* backtesting
* walk-forward validation
* robustness testing
* paper trading
* live trading

The architecture must prioritize:

1. Data correctness
2. Temporal correctness
3. Reproducibility
4. Deterministic processing
5. Local accessibility
6. Simple operation
7. Traceability

The data architecture must remain consistent with the six-module Modular Monolith defined by `002_SYSTEM_ARCHITECTURE.md`.

---

# 2. V1 Data Scope

V1 primarily uses Binance Spot market data.

Required trading pairs:

* BTCUSDT
* ETHUSDT

Primary timeframe:

* 1-minute candles

The architecture may support derived higher-timeframe data where required by the approved feature specification.

Additional exchanges and asset classes are outside V1.

---

# 3. Data Architecture Overview

The V1 data lifecycle is:

```text
Binance
   ↓
Acquisition
   ↓
Raw Validation
   ↓
Immutable Raw Storage
   ↓
Normalization / Dataset Preparation
   ↓
Validated Dataset
   ↓
Feature Engine
   ↓
Research / Backtest / Paper / Live
```

The same validated data foundation must support research and production wherever practical.

---

# 4. Storage Foundation

V1 uses:

* Parquet
* DuckDB
* Local filesystem

## Parquet

Parquet is the primary durable storage format for historical market data.

It is used because it provides:

* efficient columnar storage
* efficient analytical access
* local portability
* deterministic files
* compatibility with Python data tooling
* easy archival

## DuckDB

DuckDB is the primary local analytical/query engine.

It is used for:

* querying historical datasets
* filtering time ranges
* joining datasets where required
* research preparation
* backtesting
* validation
* data inspection

DuckDB is not a distributed database.

---

# 5. No Distributed Data Platform

V1 does not require:

* Kafka
* distributed streaming platforms
* cloud data lakes
* Spark
* distributed databases
* data warehouses
* feature-store infrastructure
* object-storage infrastructure
* data orchestration platforms
* remote data-processing clusters

The data system must operate on one local workstation.

---

# 6. Data Layers

The V1 data architecture uses three conceptual data layers:

```text
Raw Data
   ↓
Validated / Normalized Data
   ↓
Research / Derived Data
```

These layers must remain distinguishable.

---

# 7. Raw Data

Raw data represents data received from Binance or another explicitly approved source.

Raw data must preserve enough information to reconstruct what was received.

Raw data must not be silently overwritten after ingestion.

If the source provides data in a different schema from the internal representation, the original source representation should remain recoverable where practical.

---

# 8. Raw Data Immutability

Once raw historical data has been accepted into the raw-data layer, it must be treated as immutable.

Corrections must not silently rewrite historical raw files.

If a correction is necessary:

1. identify the affected dataset
2. identify the reason for correction
3. create a new derived/validated dataset
4. preserve the original raw source
5. update the dataset identity/version

This protects research reproducibility.

---

# 9. Normalized Data

The normalized dataset provides a consistent internal representation for QuantOS.

The primary candle representation should contain, at minimum:

* timestamp
* open
* high
* low
* close
* volume

Where available and required, additional exchange-provided fields may be retained.

The normalized schema must define:

* field names
* data types
* timestamp semantics
* units
* symbol representation
* ordering requirements

---

# 10. Candle Timestamp Semantics

Timestamp handling must be explicit.

QuantOS must define whether candle timestamps represent:

* candle open time
* candle close time

The internal representation must use one consistent convention.

All modules must use the same convention.

Conversions between exchange timestamps and internal timestamps must be explicit and deterministic.

---

# 11. Time Standard

All persisted market-data timestamps must use:

**UTC**

Local timezone display may be used for human-readable reports, but internal data processing must remain UTC.

The system must not use the local workstation timezone as the source of truth for market-data timestamps.

---

# 12. Ordering

Historical candles must be ordered chronologically.

For a given:

* exchange
* symbol
* timeframe

records must not contain unexpected backwards time movement.

The validation process must detect:

* duplicate timestamps
* backwards timestamps
* overlapping ranges
* invalid interval spacing

---

# 13. Data Validation

Data validation is mandatory before data enters research or production workflows.

Validation has four primary categories:

1. Structural validation
2. Temporal validation
3. Semantic validation
4. Completeness validation

---

# 14. Structural Validation

Structural validation verifies that the dataset conforms to the expected schema.

Checks include:

* required columns exist
* data types are valid
* timestamps are parseable
* numeric fields are numeric
* required identifiers exist
* no unexpected schema corruption exists

A structurally invalid dataset must be rejected.

---

# 15. Temporal Validation

Temporal validation verifies that the dataset behaves correctly over time.

Checks include:

* chronological ordering
* duplicate timestamps
* expected candle interval
* missing intervals
* overlapping data
* invalid timestamp values

For 1-minute data, the expected interval is one minute unless the dataset explicitly records a known interruption.

---

# 16. Completeness Validation

The system must identify missing expected observations.

A missing candle must not automatically be treated as a zero-volume candle.

The system must distinguish between:

* actual zero-volume market activity where legitimately supplied
* missing data
* invalid data
* known exchange downtime or data-source gaps

Missing data must be recorded explicitly.

---

# 17. Semantic Validation

Semantic validation verifies that market values are logically valid.

At minimum:

```text
high >= max(open, close)
low  <= min(open, close)
high >= low
volume >= 0
```

Prices must be positive.

Invalid numerical values must be rejected.

NaN and infinite values must not enter validated market-data datasets.

---

# 18. Duplicate Handling

Duplicate observations must be detected using the appropriate dataset identity fields.

For candle data, the primary uniqueness boundary is:

```text
exchange + symbol + timeframe + timestamp
```

Duplicate records must not silently produce multiple competing observations for the same candle.

The ingestion process must either:

* reject the duplicate
* deterministically resolve it according to a documented rule
* flag it for review

The behavior must be reproducible.

---

# 19. Missing Data Handling

Missing candles must be detected before a dataset is used.

The system must not blindly fill missing candles.

Forward-filling market prices is prohibited when it could create artificial trading information.

If a gap exists:

* record the gap
* identify its duration
* identify the affected symbol/timeframe
* determine whether it is acceptable for the intended research or trading task

A research/backtest run must know whether its input data contains gaps.

---

# 20. Data Acquisition

Historical data acquisition must support:

* configurable date ranges
* BTCUSDT
* ETHUSDT
* 1-minute candles
* resumable downloads
* deterministic storage
* validation after acquisition

The acquisition process should be restartable without unnecessarily downloading already validated data.

---

# 21. Historical Data Recovery

If historical acquisition is interrupted:

1. determine the last successfully stored range
2. determine the missing range
3. retrieve only the required data
4. validate the new data
5. merge deterministically
6. revalidate the resulting dataset

The process must not silently create duplicates or gaps.

---

# 22. Data Acquisition Metadata

Each acquisition operation should record:

* exchange
* symbol
* timeframe
* requested time range
* actual received time range
* source
* acquisition timestamp
* validation status
* dataset identity/version
* errors or gaps

This metadata supports reproducibility and operational debugging.

---

# 23. Dataset Identity

Every important validated dataset must have a stable identity.

The identity must represent the dataset contents and relevant preparation parameters.

At minimum, dataset metadata must identify:

* exchange
* symbol
* timeframe
* time range
* schema version
* source
* validation status
* preparation/version identity

A dataset identity must be recorded in research runs.

---

# 24. Dataset Versioning

Dataset changes must produce distinguishable versions.

Examples of changes requiring a new dataset identity include:

* new source data
* corrected source data
* changed normalization
* changed filtering rules
* changed schema
* changed transformation logic

A historical experiment must continue to reference the dataset version it originally used.

---

# 25. Dataset Reproducibility

A dataset used for research must be reconstructable from recorded information.

The system must record enough information to determine:

```text
Source
+
Time Range
+
Symbol
+
Timeframe
+
Schema Version
+
Preparation Version
=
Dataset Identity
```

The exact implementation of dataset identity may use a deterministic hash or equivalent mechanism.

The important requirement is that two materially different datasets must not appear identical.

---

# 26. Dataset Boundaries

A dataset must explicitly define its temporal boundaries.

For example:

```text
Dataset:
BTCUSDT
1-minute
2024-01-01 00:00 UTC
through
2024-12-31 23:59 UTC
```

Research runs must explicitly identify the time windows used for:

* training
* validation
* testing

This prevents accidental mixing of future information.

---

# 27. Train / Validation / Test Separation

The data architecture must support strict temporal separation.

Conceptually:

```text
Past
──────────────────────────────────────→ Future

Training | Validation | Final Test
```

Training data must occur before validation data.

Validation data must occur before final test data.

The final test period must remain protected from repeated strategy tuning.

Randomly shuffling time-series observations must not replace chronological validation.

---

# 28. Walk-Forward Data Preparation

The data architecture must support walk-forward validation.

Each walk-forward period must have explicitly defined:

* training window
* validation window where used
* test window
* feature availability boundary

The preparation process must prevent future data from entering an earlier training period.

---

# 29. Feature Data Boundary

The data architecture ends at validated market/dataset inputs.

The Feature Engine owns feature calculation.

However, the data architecture must provide sufficient metadata to ensure feature generation can determine:

* exact timestamp
* symbol
* timeframe
* dataset version
* available history

Feature generation must not silently access future datasets.

---

# 30. Research Dataset Boundary

Research datasets are derived from validated market data.

They may contain:

* model inputs
* labels
* derived variables
* training metadata

Research datasets must remain traceable to:

* source dataset
* feature version
* label definition
* time boundaries
* preparation configuration

---

# 31. Labels

Labels are research-derived information.

Labels must never be treated as live market features.

A label may use future information relative to the prediction timestamp only because it defines the future outcome being predicted.

The label must never leak into:

* live features
* historical features
* risk inputs
* execution decisions

The boundary must be explicit.

---

# 32. Market Data Replay

The data layer must support deterministic historical replay.

A replay must be able to provide market observations in chronological order as though they were arriving over time.

Replay is required for:

* backtesting
* simulation
* debugging
* regression testing

Replay must not expose future observations earlier than their simulated availability time.

---

# 33. Live Market Data

Live market data must enter the same conceptual market-data boundary as historical data.

The system must validate live data before downstream processing where practical.

The live data path must detect:

* stale data
* missing updates
* malformed messages
* timestamp anomalies
* connection failures

Critical market-data problems must prevent unsafe new trades.

---

# 34. Stale Data

The system must define a staleness condition for live market data.

If market data is older than the allowed threshold:

```text
No New Trade
```

The threshold must be configurable and recorded.

Stale data must not be treated as current data.

---

# 35. Data Normalization

Exchange-specific data must be normalized before being consumed by domain/application logic.

Normalization must include, where applicable:

* timestamp normalization
* symbol normalization
* numeric type normalization
* field naming
* unit normalization
* ordering

Exchange-specific formats must remain isolated within the infrastructure boundary.

---

# 36. Exchange Adapter Boundary

Binance-specific data acquisition must be isolated behind an adapter.

Conceptually:

```text
Market Data Module
       ↓
Market Data Interface
       ↓
Binance Adapter
       ↓
Binance API
```

Business logic must not depend directly on Binance response formats.

V1 does not require a generalized multi-exchange data platform.

The abstraction exists to maintain clean boundaries.

---

# 37. Local Filesystem Layout

The implementation should maintain clear separation between raw, validated, derived, and research data.

A conceptual layout is:

```text
data/
├── raw/
│   └── binance/
│       ├── BTCUSDT/
│       └── ETHUSDT/
│
├── validated/
│   ├── BTCUSDT/
│   └── ETHUSDT/
│
├── derived/
│   ├── datasets/
│   └── research/
│
└── metadata/
```

The exact directory structure may be refined by `003` implementation specifications, but the conceptual separation must remain.

---

# 38. Parquet Partitioning

Historical datasets should be partitioned to support efficient local access without creating excessive fragmentation.

A practical partitioning scheme may use:

```text
symbol
timeframe
date
```

The partitioning strategy must avoid creating thousands of unnecessarily small files.

The final implementation should prefer predictable, manageable file sizes.

---

# 39. DuckDB Usage

DuckDB should be used for analytical access to local Parquet datasets.

Typical operations include:

* time-range selection
* symbol filtering
* dataset inspection
* research preparation
* backtest input preparation
* validation queries
* aggregation

DuckDB must not become a second source of truth for raw market data.

Parquet remains the durable market-data representation.

---

# 40. Data Integrity and Hashing

Important datasets and artifacts may use deterministic hashes or equivalent integrity identifiers.

Hashing may be used to identify:

* dataset contents
* configuration
* feature specification
* model artifacts
* research outputs

The purpose is to detect unintended changes.

A hash must never be treated as a replacement for human-readable metadata.

---

# 41. Research Run Integration

The data architecture must integrate with the V1 Research Run concept.

Every important research run must reference:

```text
Dataset Identity
Feature Version
Model Version
Configuration Version
Code Revision
Time Windows
```

The data layer is responsible for providing a stable dataset identity.

The Evaluation/Research workflow is responsible for recording that identity with the run.

---

# 42. Qlib-Inspired Data Discipline

QuantOS may adopt selected ideas inspired by Qlib's dataset and experiment workflow.

The useful V1 concepts are:

* explicit dataset definitions
* reproducible dataset preparation
* versioned research inputs
* deterministic data access
* clear time boundaries
* experiment-to-dataset traceability

QuantOS does not require Qlib's data infrastructure.

The following are outside V1:

* Qlib data server
* distributed data services
* Qlib-specific production APIs
* Qlib-specific runtime dependencies
* Qlib-managed live market data

QuantOS remains responsible for its own production data path.

---

# 43. Data Lineage

Important derived data must maintain lineage.

Conceptually:

```text
Raw Binance Data
       ↓
Validated Dataset
       ↓
Research Dataset
       ↓
Feature Version
       ↓
Model Run
       ↓
Evaluation Result
```

The system must be able to determine which source dataset contributed to an important research result.

---

# 44. Data Retention

V1 should retain raw and important validated historical data locally when storage permits.

The user environment is expected to have sufficient local storage for substantial historical datasets.

Automatic deletion must not remove datasets required to reproduce important experiments.

If cleanup is implemented later, deletion must be explicit and must not silently invalidate recorded research runs.

---

# 45. Data Quality Reporting

Validation should produce a clear quality result.

A quality report should identify:

* dataset identity
* rows/records
* time range
* symbols
* timeframe
* missing intervals
* duplicates
* invalid values
* validation status
* warnings
* errors

A dataset with critical validation errors must not be marked valid.

---

# 46. Data Quality States

A dataset should have an explicit quality state.

Conceptually:

```text
RAW
  ↓
VALIDATING
  ↓
VALID
```

Failure states may include:

```text
INVALID
INCOMPLETE
CORRUPTED
```

Only an acceptable validated state may be consumed by research/backtest workflows.

---

# 47. No Silent Repair

The data pipeline must not silently repair suspicious market data.

If a transformation or repair is required:

1. record the issue
2. record the transformation
3. create a new dataset version
4. preserve the source dataset
5. record the resulting dataset identity

This is essential for reproducibility.

---

# 48. Error Handling

Data errors must be classified.

Examples:

### Recoverable

* temporary network failure
* interrupted download
* temporary API rate limitation

### Data Quality

* missing candles
* duplicates
* malformed values
* schema mismatch

### Critical

* corrupted dataset
* impossible timestamps
* invalid prices
* unexplained data inconsistency

Recoverable errors may be retried safely.

Critical data-quality errors must stop downstream use until resolved.

---

# 49. Idempotent Ingestion

Historical ingestion should be idempotent.

Running the same acquisition operation twice should not produce duplicated market observations.

The system must detect already-ingested ranges and avoid unnecessary duplication.

Dataset results must remain deterministic.

---

# 50. Data Access Interfaces

Downstream modules should consume market data through clear interfaces rather than directly manipulating storage implementation details.

Conceptually:

```text
Feature Engine
      ↓
Market Data Interface
      ↓
Dataset Reader
      ↓
Parquet / DuckDB
```

The same principle applies to:

* backtesting
* research
* validation
* replay

This keeps storage details outside business logic.

---

# 51. Performance Requirements

V1 data infrastructure must be performant enough for local research and backtesting.

The system should prioritize:

* columnar reads
* time-range filtering
* symbol filtering
* efficient DuckDB queries
* avoiding unnecessary data copies
* manageable Parquet file sizes

Distributed processing is not required.

---

# 52. Data Security

Historical market data is not considered secret.

However:

* API credentials must never be stored with market data
* credentials must never appear in dataset metadata
* secrets must never be written into Parquet
* secrets must never be included in research artifacts

---

# 53. Data Architecture Acceptance Criteria

The V1 data architecture is compliant when:

* Binance Spot is supported.
* BTCUSDT and ETHUSDT are supported.
* 1-minute candles are the primary dataset.
* timestamps use UTC.
* raw data is immutable.
* normalized data has a defined schema.
* duplicate candles are detected.
* missing candles are detected.
* invalid OHLCV values are detected.
* datasets have identifiable versions.
* research runs reference dataset identities.
* train/validation/test boundaries are explicit.
* future data cannot enter earlier research periods.
* historical replay is deterministic.
* live data staleness can be detected.
* Parquet provides durable local storage.
* DuckDB provides local analytical access.
* data lineage is traceable.
* ingestion is idempotent.
* critical data errors stop downstream processing.
* no distributed data platform is required.
* Qlib is not required for production.
* Qlib is not in the live data path.
* the entire data architecture can operate on one local workstation.

---

# 54. Final Data Architecture Statement

QuantOS V1 uses a deliberately simple local data architecture:

```text
Binance
   ↓
Validated Market Data
   ↓
Parquet
   ↓
DuckDB
   ↓
Deterministic Research / Feature Inputs
```

The data system exists to provide reliable information to the six core QuantOS modules.

It is not a general-purpose data platform.

Its primary responsibilities are:

**correct data, correct time, correct lineage, and reproducible access.**

If the data foundation cannot prove what information was available at a given point in time, the trading system cannot reliably prove that its results are valid.

Therefore:

> **Data correctness and temporal integrity take priority over data complexity.**
