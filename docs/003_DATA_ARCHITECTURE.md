# QuantOS Core — 003_DATA_ARCHITECTURE.md

Version: 1.0.0-V1
Status: Final MVP data architecture
Last Updated: 2026-08-19

## 1. Purpose

Define the minimum local data architecture required for reliable, reproducible research, backtesting, paper trading, and live operation.

## 2. V1 Data Scope

Exchange: Binance Spot

Symbols:
- BTCUSDT
- ETHUSDT

Primary timeframe:
- 1 minute

Primary market dataset:
- OHLCV candles

Supporting runtime data:
- exchange symbol metadata;
- account balances;
- positions;
- orders;
- fills;
- execution events.

V1 does not require additional market-data vendors or alternative-data pipelines.

## 3. Canonical Candle

| Field | Description |
|---|---|
| symbol | Trading pair |
| interval | `1m` |
| open_time | UTC candle start |
| close_time | UTC candle end |
| open | Open price |
| high | High price |
| low | Low price |
| close | Close price |
| volume | Base-asset volume |
| quote_volume | Quote-asset volume |
| trade_count | Number of trades |

Provider-specific fields must not leak into the canonical domain contract without explicit approval.

## 4. Data Pipeline

```text
Binance
   ↓
Raw Response
   ↓
Normalization
   ↓
Validation
   ↓
Canonical Dataset
   ↓
Parquet
   ↓
DuckDB
   ↓
Feature Engine
```

## 5. Storage

Parquet is the canonical storage format for historical market datasets.

DuckDB is the local analytical/query layer over those files. It is not a separate service and is not the authoritative source of market truth.

## 6. Data Immutability

Historical source datasets should be treated as immutable.

Corrections or new downloads create a new ingestion/version rather than silently rewriting research inputs.

## 7. Data Validation

Ingestion must detect:

- duplicate candles;
- missing timestamps;
- out-of-order timestamps;
- invalid OHLC relationships;
- invalid/negative volume;
- unsupported symbols;
- invalid timestamps;
- unexpected intervals.

Invalid records must not silently enter the canonical dataset.

## 8. Timestamp and Causality Rules

All internal timestamps use UTC.

For a decision at time `t`, only information available by `t` may be used.

The rule applies to features, labels, model inputs, backtests, validation, paper trading, and live trading.

## 9. Completed-Candle Rule

The V1 strategy operates on completed 1-minute candles.

A candle must not be treated as final before its close time.

This keeps live feature generation consistent with historical evaluation.

## 10. Historical Data

Historical ingestion supports:

- initial bulk download;
- incremental updates;
- duplicate-safe writes;
- validation;
- deterministic dataset identification.

No additional data vendor is required for the first MVP.

## 11. Live Data

Live market data must be normalized to the same canonical semantics as historical data.

The live path must use the same Feature Engine definitions as the historical path.

## 12. Train / Validation / Test

Splits are chronological.

A research run records:

- dataset identity;
- training period;
- validation period;
- test period;
- feature version;
- target definition;
- model version/configuration.

Random temporal shuffling is prohibited for final evaluation.

The final test period remains untouched during model/parameter selection.

## 13. Dataset Identity

A dataset is identified by:

- symbol;
- timeframe;
- start time;
- end time;
- source;
- schema version;
- ingestion version;
- validation status.

Research runs record the dataset identity they consume.

## 14. Missing Data

Missing market data must be detected and surfaced.

Do not silently manufacture candles unless explicitly defined and safe for the strategy.

If required market data is unavailable:

```text
No valid data
      ↓
No valid feature vector
      ↓
HOLD / NO TRADE
```

## 15. Research Reproducibility

A research result should be reproducible from:

- dataset identity;
- code version;
- feature version;
- strategy version;
- model version;
- configuration;
- random seed where applicable.

## 16. Data Not Required for V1

Do not build:

- tick-data storage;
- full historical order-book infrastructure;
- news feeds;
- social sentiment;
- on-chain data;
- alternative-data pipelines;
- real-time data warehouses;
- distributed data processing.

## 17. Data Architecture Definition of Done

The data layer is complete when:

- BTCUSDT and ETHUSDT 1-minute data can be downloaded;
- data is normalized and validated;
- canonical datasets are stored in Parquet;
- DuckDB can query the datasets;
- duplicate/missing/invalid records are detected;
- timestamps are consistently UTC;
- live data uses the same canonical semantics;
- dataset identity can be recorded;
- future data cannot leak into features or evaluation.
