# QuantOS Core — 008_IMPLEMENTATION_GUIDE.md

Version: 1.0.0-V1
Status: Replacement baseline
Last Updated: 2026-08-19

## 1. Implementation Rule

Build the smallest system that satisfies documents 000–007.

Do not add features, modules, services, providers, strategies, or infrastructure because they appear useful.

If implementation appears to require a new product capability, stop and update the specification first.

## 2. Repository Shape

Use a single Python application organized by Clean Architecture boundaries.

Conceptually:

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

The exact package names may be adjusted during implementation only if ownership and boundaries remain unchanged.

## 3. Build Order

### Milestone 1 — Market Data

Implement:

- Binance historical ingestion;
- Binance live market data;
- canonical candle normalization;
- validation;
- Parquet storage;
- DuckDB querying.

Acceptance: reliable historical and live data.

### Milestone 2 — Feature Engine

Implement:

- versioned feature definitions;
- deterministic calculations;
- causal alignment;
- tests.

Acceptance: identical inputs produce identical feature vectors.

### Milestone 3 — Alpha Engine

Implement:

- target definition;
- training workflow;
- LightGBM candidate;
- model artifact;
- single strategy decision path;
- explanation metadata.

Acceptance: reproducible predictions and decisions.

### Milestone 4 — Evaluation

Implement:

- event-driven backtest;
- cost model;
- performance metrics;
- walk-forward;
- Monte Carlo;
- experiment metadata.

Acceptance: no look-ahead and reproducible validation.

### Milestone 5 — Risk + Paper Execution

Implement:

- risk controls;
- simulated execution;
- order-state tracking;
- continuous paper trading.

Acceptance: safe end-to-end operation with real market data and no real orders.

### Milestone 6 — Live Execution

Implement:

- Binance Spot live adapter;
- explicit live configuration;
- reconciliation;
- safe failure behavior.

Acceptance: live mode is technically capable but remains disabled until validation approval.

## 4. Testing Strategy

### Unit Tests

Test each domain rule independently.

### Integration Tests

Test boundaries between:

- data and features;
- features and alpha;
- alpha and risk;
- risk and execution;
- execution and exchange adapter;
- evaluation and storage.

### Deterministic Replay

The same input dataset and configuration must reproduce the same outputs.

### Failure Tests

Explicitly test:

- missing data;
- stale data;
- malformed candles;
- network failure;
- exchange errors;
- invalid configuration;
- model artifact failure;
- duplicate order prevention;
- risk-limit breach.

## 5. Configuration

No business rule should require source-code modification to change.

Use external configuration for:

- symbols;
- runtime mode;
- risk limits;
- costs;
- model parameters;
- data paths;
- validation windows;
- API credentials.

## 6. Logging

Use structured logs with:

- timestamp;
- level;
- component;
- event type;
- correlation/run identifier where applicable;
- relevant symbol/order/model identifiers.

Do not log secrets.

## 7. Research Artifacts

A research run should produce enough metadata to reproduce it:

- run ID;
- configuration;
- dataset identity;
- code version;
- feature version;
- strategy version;
- model version;
- metrics;
- validation result.

Persist model artifacts and validation reports.

## 8. Qlib Boundary

Qlib may be studied or used as a research reference, but V1 must not depend on Qlib for runtime execution.

Only its useful discipline is retained:

- reproducible datasets;
- experiment tracking;
- temporal evaluation;
- standardized evaluation artifacts.

## 9. Definition of Done

Do not declare V1 complete until:

- all six production modules operate;
- unit/integration tests pass;
- historical ingestion works;
- live data works;
- deterministic features work;
- one model is reproducibly trainable;
- event-driven backtest works;
- walk-forward works;
- Monte Carlo works;
- paper trading works;
- risk controls are enforced;
- live execution is explicitly gated;
- documentation matches implementation.

## 10. Final Guardrail

The implementation is successful when it is boring, deterministic, testable, explainable, and safe.

It is not successful merely because it contains more indicators, more models, more strategies, more services, or a higher backtest return.
