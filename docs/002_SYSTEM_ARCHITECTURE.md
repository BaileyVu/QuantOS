# QuantOS Core — 002_SYSTEM_ARCHITECTURE.md

Version: 1.0.0-V1
Status: Replacement baseline
Last Updated: 2026-08-19

## 1. Architectural Decision

QuantOS V1 is a Clean Architecture Modular Monolith.

It is not a microservice system.

The six production modules are:

`Market Data → Feature Engine → Alpha Engine → Risk Engine → Execution Engine → Evaluation Engine`

Infrastructure implementations sit behind interfaces and include Binance connectivity, Parquet, DuckDB, configuration, logging, and process/runtime concerns.

## 2. Layering

```text
Presentation / CLI
        |
Application orchestration
        |
Domain modules
        |
Infrastructure adapters
```

The domain must not depend on Binance SDKs, DuckDB, Parquet libraries, network clients, or other infrastructure details.

## 3. Module Responsibilities

### 3.1 Market Data

Owns:

- Binance Spot connectivity;
- historical ingestion;
- live market streams;
- candle normalization;
- timestamp normalization;
- symbol metadata;
- data quality checks;
- delivery of canonical market events.

Does not:

- generate alpha;
- decide position size;
- submit orders.

### 3.2 Feature Engine

Owns:

- feature definitions;
- feature calculation;
- feature validation;
- feature versioning;
- causal/temporal enforcement.

Does not:

- submit orders;
- choose risk;
- train production models implicitly during live execution.

### 3.3 Alpha Engine

Owns:

- the single approved production strategy;
- model loading;
- prediction;
- signal generation;
- signal explanation;
- strategy state.

Does not:

- override risk;
- submit exchange orders;
- own account balances.

### 3.4 Risk Engine

Owns:

- position sizing;
- exposure limits;
- daily loss limits;
- drawdown protection;
- volatility-aware sizing;
- edge-after-cost checks;
- approval/rejection.

Risk rejection is final.

### 3.5 Execution Engine

Owns:

- order construction;
- order type selection within policy;
- order submission;
- cancellation;
- retry handling;
- fill/order-state tracking;
- exchange acknowledgement normalization.

It is the only module permitted to send trading instructions to Binance.

### 3.6 Evaluation Engine

Owns:

- backtesting;
- portfolio accounting for simulation;
- performance metrics;
- walk-forward orchestration;
- Monte Carlo analysis;
- experiment/run records;
- validation reports.

## 4. Runtime Flow

```text
Market event
   ↓
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
Binance
   ↓
Execution/fill event
   ↓
Portfolio/account state
   ↓
Evaluation/observability
```

A rejected risk decision stops the execution path.

## 5. Historical Flow

```text
Binance historical data
        ↓
Market Data ingestion
        ↓
immutable Parquet
        ↓
DuckDB query layer
        ↓
Feature Engine
        ↓
Alpha Engine
        ↓
Evaluation Engine
```

## 6. State Ownership

- Market state: Market Data
- Feature state: Feature Engine
- Strategy/model state: Alpha Engine
- Risk state: Risk Engine
- Order/execution state: Execution Engine
- Evaluation state: Evaluation Engine
- Persistent datasets: storage infrastructure

No module may silently mutate another module's state.

## 7. Interfaces

Interfaces must use explicit domain contracts rather than provider-specific objects.

Examples:

- `MarketEvent`
- `Candle`
- `FeatureVector`
- `AlphaDecision`
- `RiskDecision`
- `OrderRequest`
- `ExecutionReport`
- `PortfolioSnapshot`
- `EvaluationResult`

Exact implementation types belong in the technical implementation, but the ownership and semantics must remain stable.

## 8. Failure Policy

Any uncertainty that could result in uncontrolled trading must fail closed.

Examples:

- stale market data;
- invalid symbol metadata;
- missing features;
- model artifact failure;
- risk-state failure;
- exchange authentication failure;
- unknown order state.

The system must never compensate for missing safety information by guessing.

## 9. Qlib-Inspired Research Boundary

Research workflows may use Qlib-like concepts:

- dataset identity;
- temporal dataset partitions;
- experiment IDs;
- artifact manifests;
- reproducible evaluation.

These are workflow concepts, not a new architecture.

Qlib is not required to run QuantOS V1.
