# QuantOS Core — 008_IMPLEMENTATION_GUIDE.md

Version: 1.0.0-V1
Status: Final MVP implementation guide
Last Updated: 2026-08-19

## 1. Purpose

Build the first working QuantOS MVP as quickly as possible.

The priority is:

> Get QuantOS running end-to-end first, then improve it safely.

Do not build the ultimate quant platform before the first complete system works.

## 2. Implementation Rule

Implement only what is required by documents `000–007`.

Do not introduce new production modules, exchanges, strategies, models, services, APIs, cloud infrastructure, distributed infrastructure, or speculative abstractions.

If a problem appears to require a new product capability, stop and resolve it against the specifications before adding it.

## 3. Repository Structure

Use one Python application with clear internal boundaries:

```text
src/
  domain/
    market_data/
    features/
    alpha/
    risk/
    execution/
    evaluation/
  application/
  infrastructure/
    binance/
    storage/
    models/
    configuration/
    logging/
  interfaces/

tests/
  unit/
  integration/
  validation/

configs/
data/
models/
experiments/
docs/
```

Exact package names may change if ownership boundaries remain unchanged.

Do not create one deployable application per module.

## 4. Build Order

### Phase 1 — Foundation

Implement:

- Python project setup;
- configuration;
- structured logging;
- domain contracts;
- test framework;
- CLI/runtime entry point.

Acceptance: application starts, configuration loads, tests run, logging works.

### Phase 2 — Market Data

Implement:

- Binance historical ingestion;
- Binance live market data;
- 1-minute candle normalization;
- validation;
- Parquet persistence;
- DuckDB querying.

Acceptance: BTCUSDT and ETHUSDT history works, data passes validation, live data can be consumed, and local queries work.

### Phase 3 — Feature Engine

Implement the small approved feature set.

Target 10–15 production features; maximum 20.

Implement deterministic calculations, versioning, temporal alignment, missing-data behavior, and tests.

Acceptance: identical inputs produce identical feature vectors and no feature uses future information.

### Phase 4 — Alpha Engine

Implement:

- target definition;
- training dataset construction;
- one production model;
- preferred initial model: LightGBM;
- model artifact saving/loading;
- one production strategy;
- signal generation;
- decision metadata.

Acceptance: training is reproducible, the model can be loaded, predictions are deterministic for identical inputs/configuration, and the strategy produces BUY/SELL/HOLD.

### Phase 5 — Evaluation Engine

Implement:

- event-driven backtester;
- simulated account state;
- simulated orders/fills;
- fees;
- slippage;
- performance metrics;
- walk-forward validation;
- Monte Carlo analysis;
- experiment metadata.

Acceptance: historical evaluation runs without look-ahead, includes costs, is reproducible, and produces validation reports.

### Phase 6 — Risk Engine

Implement:

- position sizing;
- exposure limits;
- daily loss limit;
- drawdown protection;
- volatility-aware sizing;
- expected-edge-after-cost check;
- safe rejection behavior.

Acceptance: invalid/excessive trades are rejected and risk rejection prevents execution.

### Phase 7 — Paper Trading

Connect:

```text
Live Binance Market Data
        ↓
Feature Engine
        ↓
Alpha Engine
        ↓
Risk Engine
        ↓
Simulated Execution
        ↓
Paper Account
        ↓
Evaluation
```

Acceptance:

- QuantOS runs continuously using live market data;
- no real order is submitted;
- signals, decisions, simulated fills, balances, and metrics are recorded.

This is the first major MVP milestone.

## 5. Live Trading Comes Last

Only after the previous phases work should the real Binance execution adapter be enabled.

Live mode requires:

- explicit runtime configuration;
- valid Binance credentials;
- successful paper operation;
- passing validation;
- explicit live enablement.

Live trading must never be the default.

## 6. Minimum Viable End-to-End Path

The first complete MVP must be able to execute:

```text
Binance 1m Data
      ↓
Canonical Candle
      ↓
Feature Vector
      ↓
Model Prediction
      ↓
BUY / SELL / HOLD
      ↓
Risk Check
      ↓
Simulated Order
      ↓
Position / Balance Update
      ↓
Performance Metrics
```

If this works reliably, QuantOS has achieved its first MVP.

Do not delay this milestone for secondary features.

## 7. Testing Strategy

### Unit Tests

Test candle validation, feature calculations, signal rules, risk rules, position sizing, cost calculations, and metrics.

### Integration Tests

Test Binance adapter, storage, feature pipeline, model artifact loading, risk/execution boundary, and evaluation pipeline.

### End-to-End Test

Run:

```text
data → features → alpha → risk → simulated execution → evaluation
```

over a small deterministic historical dataset.

### Deterministic Replay

The same dataset and configuration should produce the same research result, except for explicitly documented nondeterminism.

## 8. Failure Tests

Explicitly test:

- malformed market data;
- missing candles;
- stale data;
- network failure;
- Binance authentication failure;
- exchange errors;
- missing model artifact;
- incompatible feature/model versions;
- risk-limit breach;
- duplicate order submission;
- unknown execution state.

Safety-critical failures must fail closed.

## 9. Configuration

Keep outside source code:

- symbols;
- timeframe;
- runtime mode;
- data paths;
- risk limits;
- transaction costs;
- model parameters;
- validation periods;
- API credentials.

Secrets must never be committed.

## 10. Logging

Structured logs should record:

- startup/shutdown;
- data ingestion;
- data validation;
- feature generation;
- model version;
- alpha decisions;
- risk decisions;
- order requests;
- execution responses;
- fills;
- errors;
- validation runs.

Never log secrets.

## 11. Research Artifacts

Each research/validation run should preserve:

- run ID;
- dataset ID;
- code version;
- feature version;
- strategy version;
- model version;
- configuration;
- time windows;
- random seed where applicable;
- metrics;
- validation result.

Model artifacts and validation reports must be persistable and reloadable.

## 12. Qlib Boundary

Qlib may be used as a research reference or optionally during experimentation, but it is not a required V1 runtime dependency.

Retain only the useful concepts:

- reproducible datasets;
- experiment identity;
- temporal evaluation;
- artifact tracking;
- standardized evaluation.

Do not import Qlib's larger architecture into QuantOS.

## 13. Definition of Done

The first QuantOS MVP is complete when:

- the application runs locally;
- Binance data can be ingested;
- BTCUSDT and ETHUSDT 1-minute data works;
- features are generated deterministically;
- one model can be trained and loaded;
- one strategy produces signals;
- risk controls are enforced;
- event-driven backtesting works;
- transaction costs are modeled;
- walk-forward validation works;
- Monte Carlo analysis works;
- paper trading works with live data;
- important decisions are logged;
- live execution can be enabled without rewriting the core architecture;
- no unapproved architecture or feature has been added.

## 14. What Not to Optimize Yet

Do not spend MVP development time on:

- dozens of indicators;
- model ensembles;
- deep learning;
- reinforcement learning;
- automated strategy discovery;
- portfolio optimization;
- multiple exchanges;
- cloud deployment;
- distributed computing;
- UI dashboards;
- mobile applications;
- high-frequency execution;
- elaborate orchestration.

First make the core loop work.

## 15. Final Principle

The first QuantOS should be:

- small;
- deterministic;
- understandable;
- testable;
- observable;
- safe;
- runnable.

A working simple QuantOS is more valuable than an unfinished sophisticated QuantOS.
