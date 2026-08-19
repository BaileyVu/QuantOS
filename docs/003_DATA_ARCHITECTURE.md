# QuantOS Core — 003_DATA_ARCHITECTURE.md

Version: 1.0.0-V1
Status: Replacement baseline
Last Updated: 2026-08-19

## 1. Data Objective

Provide reliable, immutable, reproducible market data for research, backtesting, paper trading, and live operation.

## 2. V1 Data Scope

Only Binance Spot is supported.

Symbols:

- BTCUSDT
- ETHUSDT

Primary dataset:

- 1-minute OHLCV candles

Supporting data:

- latest price;
- exchange symbol metadata;
- execution/fill data;
- account state required for trading.

Trade stream and order-book data are not required inputs to the first production strategy.

## 3. Canonical Candle

| Field | Meaning |
|---|---|
| symbol | Trading pair |
| interval | `1m` |
| open_time | UTC opening timestamp |
| close_time | UTC closing timestamp |
| open | opening price |
| high | highest price |
| low | lowest price |
| close | closing price |
| volume | base-asset volume |
| quote_volume | quote-asset volume |
| trade_count | number of trades |

Provider-specific fields are not part of the production canonical schema.

## 4. Data Integrity

Ingestion must detect:

- duplicate candles;
- missing timestamps;
- out-of-order records;
- invalid OHLC relationships;
- invalid volume;
- unsupported symbols;
- invalid timestamps;
- unexpected interval changes.

Invalid records must not silently enter the canonical dataset.

## 5. Immutability

Raw downloaded market data is immutable.

Corrections are represented as a new ingestion/version rather than destructive modification.

Derived datasets may be regenerated from immutable source data.

## 6. Storage

### Parquet

Parquet is the canonical file format for market datasets.

It provides compact local storage and deterministic batch access.

### DuckDB

DuckDB is the local analytical query layer over Parquet.

DuckDB is not the authoritative source of raw market truth; Parquet datasets remain reproducible source artifacts.

## 7. Dataset Identity

Each research dataset must be identifiable by:

- symbol;
- interval;
- start/end time;
- source;
- schema version;
- ingestion version;
- data quality status.

## 8. Temporal Rules

All timestamps use UTC.

For a decision at time `t`, only information available at or before `t` may be used.

Feature windows, labels, training sets, validation sets, and backtests must obey this rule.

## 9. Train / Validation / Test

Splits are chronological, never random.

A research run must record:

- training interval;
- validation interval;
- test interval;
- feature version;
- target definition;
- model configuration.

The test period is not used for model selection.

## 10. Live Data

Live market data must be normalized into the same canonical semantics used by historical data.

The live feature path must not use a different calculation definition from the backtest path.

## 11. Qlib-Inspired Research Discipline

QuantOS adopts reproducible dataset identity, temporal partitioning, and experiment metadata.

It does not adopt Qlib's runtime architecture or require Qlib as a dependency.

## 12. Data Retention

Historical data may be retained for multiple years because the local target environment supports large storage.

V1 should prefer useful, validated history over unnecessary data-source expansion.
