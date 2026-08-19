# QuantOS Core — 002_SYSTEM_ARCHITECTURE.md

Version: 1.0.0-V1
Status: Final MVP architecture
Last Updated: 2026-08-19

## 1. Purpose

QuantOS V1 is a **Clean Architecture Modular Monolith** running locally. It is intentionally small and is not a microservice platform.

The architecture exists to get the first complete QuantOS MVP running quickly while preserving clean internal boundaries.

## 2. V1 Architecture

Six production business modules:

1. Market Data
2. Feature Engine
3. Alpha Engine
4. Risk Engine
5. Execution Engine
6. Evaluation Engine

Supporting infrastructure provides Binance connectivity, Parquet/DuckDB storage, configuration, logging, model/artifact persistence, and runtime/CLI entry points.

These supporting concerns are not additional business modules.

## 3. High-Level Flow

```text
Market Data
    ↓
Feature Engine
    ↓
Alpha Engine
    ↓
Risk Engine
    ↓
Execution Engine
    ↓
Binance Spot
```

The Evaluation Engine exercises the same core logic for backtesting, walk-forward validation, Monte Carlo analysis, paper trading, and reporting.

## 4. Clean Architecture

```text
Interface / CLI
      ↓
Application
      ↓
Domain
      ↓
Infrastructure Adapters
```

Domain code must not depend on Binance SDKs, HTTP clients, DuckDB, Parquet libraries, filesystem details, environment handling, or provider-specific objects.

Application code coordinates use cases. Infrastructure implements external adapters.

## 5. Module Responsibilities

### Market Data
Owns historical/live Binance Spot data, candle normalization, timestamps, validation, symbol validation, and market events.

Does not generate signals, size positions, or submit orders.

### Feature Engine
Owns approved feature definitions, calculations, validation, versioning, and temporal alignment.

Does not submit orders, make risk decisions, or silently train models during live execution.

### Alpha Engine
Owns the single approved production strategy, one active production model, prediction, signal generation, strategy state, and decision explanation.

Does not bypass risk or submit exchange orders.

### Risk Engine
Owns position sizing, exposure limits, daily-loss limits, drawdown protection, volatility-aware sizing, expected-edge-after-cost checks, and approval/rejection.

Risk rejection is final.

### Execution Engine
Owns order construction, approved order-type selection, Binance submission, cancellation, retries, order status, fills, and exchange-error normalization.

It is the only module permitted to cause a real exchange order side effect.

### Evaluation Engine
Owns event-driven backtesting, simulated execution, metrics, walk-forward validation, Monte Carlo robustness analysis, paper-trading evaluation, and experiment metadata.

## 6. Runtime Modes

The same core modules operate in:

1. Research
2. Backtest
3. Paper Trading
4. Live Trading

Paper trading is the default. Live mode requires explicit enablement.

Paper and live modes must not duplicate business logic. The execution side effect is what changes.

## 7. Core Runtime Flow

```text
Market Event
     ↓
Feature Vector
     ↓
Alpha Decision
     ↓
Risk Decision
     ↓
Order Intent
     ↓
Execution
     ↓
Execution Report
     ↓
Account / Position State
```

A risk rejection terminates the execution path.

## 8. Historical Evaluation Flow

```text
Historical Data
     ↓
Market Data
     ↓
Feature Engine
     ↓
Alpha Engine
     ↓
Risk Engine
     ↓
Simulated Execution
     ↓
Evaluation Engine
     ↓
Metrics / Validation Report
```

Historical evaluation must exercise the same strategy, feature, and risk logic used by paper trading wherever practical.

## 9. State Ownership

| State | Owner |
|---|---|
| Market/candle state | Market Data |
| Feature state | Feature Engine |
| Strategy/model state | Alpha Engine |
| Risk state | Risk Engine |
| Order/execution state | Execution Engine |
| Evaluation state | Evaluation Engine |
| Persistent datasets/artifacts | Infrastructure |

A module must not silently mutate another module's internal state.

## 10. Core Contracts

Use explicit internal contracts for:

- `Candle`
- `MarketEvent`
- `FeatureVector`
- `AlphaDecision`
- `RiskDecision`
- `OrderRequest`
- `ExecutionReport`
- `Position`
- `AccountSnapshot`
- `EvaluationResult`

Provider-specific objects are translated at infrastructure boundaries.

## 11. Failure Policy

QuantOS must fail closed whenever uncertainty could create uncontrolled trading.

Examples include stale data, invalid candles, missing features, incompatible model artifacts, unavailable risk state, exchange authentication failure, and unknown order state.

The system must never guess through a safety-critical failure.

## 12. Execution Reconciliation

When an order result is uncertain:

1. preserve the request/event record;
2. reconcile exchange state;
3. determine authoritative order state;
4. prevent duplicate exposure;
5. fail closed if safe reconciliation is impossible.

## 13. Infrastructure Boundaries

Binance is accessed only through an exchange adapter.

Parquet is the canonical historical-data format and DuckDB is the local analytical query layer.

Configuration is external to business logic and secrets are never hardcoded.

Logging is centralized through structured application logging.

## 14. Explicit V1 Exclusions

Do not add:

- microservices;
- API gateway;
- message queues;
- distributed cache;
- Kubernetes;
- cloud deployment;
- portfolio-management service;
- AI integration service;
- strategy marketplace;
- multiple live strategies;
- multiple production models;
- reinforcement learning;
- autonomous agents;
- futures;
- leverage;
- options;
- market making;
- cross-exchange execution.

These are outside V1.

## 15. Architectural Definition of Done

The architecture is correctly implemented when:

- QuantOS runs as one local application;
- the six production modules are clearly separated;
- domain code is independent of infrastructure;
- Binance access is isolated behind an adapter;
- historical and live data use canonical contracts;
- the same strategy logic can be evaluated and paper traded;
- risk can reject a trade before execution;
- live execution is explicitly gated;
- critical state transitions are observable;
- no unapproved service or module architecture is introduced.
