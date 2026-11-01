# QuantOS Core — 003_DATA_ARCHITECTURE.md

Version: 1.1.0-V1
Status: Final MVP data architecture
Last Updated: 2026-09-21

Amendment: 2026-09-21 — Human-approved DE1A research-only aggregate-trade scope; production semantics unchanged.

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

## 2.1 Approved research-only market-event data

QuantOS may implement storage and processing of explicitly approved research-only
market-event information without changing the V1 production decision cadence.
The sole information family permitted by this subsection is Binance Spot
aggregate trades (`aggTrades`) for BTCUSDT and ETHUSDT. Historical archives and
live-collected observations are eligible research sources only within separately
authorized phases.

This permission does not include individual-trade ingestion, order-book/L2
infrastructure, derivatives data or trading, additional instruments or exchanges,
unrelated alternative data, sub-minute strategy decisions, high-frequency trading
(HFT), or market making. Those exclusions remain in force. Approval of this
subsection does not itself authorize implementation, acquisition, collection,
experiment execution, feature admission, paper trading, production usage, or
access to any research interval or data role.

Raw provider records, normalized canonical research events, derived
completed-minute state, and production feature eligibility are distinct layers.
Canonical research event contracts belong to the existing Market Data ownership
boundary and must remain separate from `Candle` and its completed-candle
`MarketEvent`. Existing candle `DatasetIdentity` semantics remain unchanged.
Provider payloads remain outside domain contracts. Exact event fields and
normalization rules require separate contract approval and provider conformance
evidence.

Event datasets must identify their event granularity explicitly and must not
masquerade as `1m` candle datasets. Their manifests must bind provider and market,
symbol, event family, requested and observed coverage, timestamp units, schema
and adapter versions, source provenance, validation state, and content hashes.
Raw-content and canonical-content identities must be independently identifiable.
Corrections, changed normalization, or source revisions create explicit successor
identities/versions; existing source datasets and previously published canonical
versions must never be silently rewritten. Canonical historical event datasets
remain subject to the Parquet storage and DuckDB query architecture.

Temporal contracts must distinguish event occurrence time, provider
message/emission time where supplied, and local observation or ingestion time
where applicable. Preserve source timestamp units and sufficient precision for
lossless UTC normalization. Missing availability timestamps remain explicitly
unknown; neither an event timestamp nor an archive ingestion timestamp may be
substituted as proof of availability at an earlier decision.

Before canonical publication, the approved contract must define source-scoped
aggregate-trade identity and a deterministic total ordering with deterministic
equal-timestamp resolution. It must establish through provider evidence and
conformance fixtures how event time, aggregate ID, underlying trade-ID ranges and
source order relate. No claim of ID continuity or timestamp monotonicity may be
assumed without that evidence. Canonical sorting must not conceal source
timestamp regressions or other integrity failures.

Duplicate IDs, conflicting records for an ID, missing ranges, timestamp
regressions and malformed maker/aggressor semantics must be detected as applicable.
Identical duplicates may be handled only under an explicitly approved,
deterministic and recorded idempotency policy. Conflicting or unresolved records
must fail canonical acceptance or be retained in an explicitly invalid or
incomplete research dataset. Such a dataset must not be represented as complete
or silently converted into valid derived state. Absence of events is not by
itself proof of a valid zero-activity interval.

Derived state must use explicit UTC intervals `[minute_start, minute_end)`, with
inclusive starts and exclusive ends. State for minute `t` may become eligible
for a decision only after that minute has completed and its required completeness
and point-in-time availability conditions are satisfied. The mapping to existing
completed-candle decision and close-time semantics must be explicit and validated.
Events outside the interval, events occurring after a decision boundary, and
information unavailable at the decision time must not influence that decision.
Late arrivals and corrections must not retrospectively rewrite the information
attributed to a past decision.

SourceHealthManifest and provider-conformance work required by document 010 remain
prerequisites before predictive use. Source health must record applicable
provenance, coverage, watermarks, counts, duplicates, gaps, checksum status,
validation state and freshness against an explicit recorded as-of time. Unknown
health fields remain explicitly unknown. Final contract, completeness, ordering
and availability policies must be reviewed before acquisition or evaluation.

Production decisions remain based on completed one-minute information. Event
arrivals do not trigger production decisions. Research evidence does not establish
historical/live parity or production eligibility. Before any event-derived
feature can be proposed for production, equivalent live inputs, compatible
normalization, deterministic aggregation, completion semantics and point-in-time
availability must be demonstrated. Production admission still requires separate
approval and the full existing promotion lifecycle.

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

- tick-data storage, except for the specifically authorized research-only aggregate-trade scope in §2.1;
- full historical order-book infrastructure;
- news feeds;
- social sentiment;
- on-chain data;
- alternative-data pipelines, except for the specifically authorized research-only aggregate-trade scope in §2.1;
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
