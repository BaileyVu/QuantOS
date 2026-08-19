# QuantOS — System Architecture

## Document Status

**Status:** Frozen V1 Architecture
**Version:** 1.0
**Depends On:** `000_READ_FIRST.md`, `001_PRODUCT_REQUIREMENTS.md`

---

# 1. Purpose

This document defines the system architecture for QuantOS V1.

It describes how the product requirements are organized into a single production system.

The architecture must remain consistent with:

* `000_READ_FIRST.md`
* `001_PRODUCT_REQUIREMENTS.md`

No lower-level document may introduce architecture that conflicts with this document.

The purpose of this architecture is to provide the smallest practical system capable of supporting the complete V1 workflow:

```text
Research
    ↓
Backtest
    ↓
Walk-Forward Validation
    ↓
Robustness Testing
    ↓
Paper Trading
    ↓
Live Trading
```

---

# 2. Architecture Principles

QuantOS V1 follows these principles:

1. Clean Architecture
2. Modular Monolith
3. Single local deployment
4. Explicit module boundaries
5. Dependency inversion
6. Deterministic processing
7. Risk-first execution
8. Reproducible research
9. Minimal infrastructure
10. Safe failure

The architecture must optimize for:

* correctness
* testability
* maintainability
* observability
* reproducibility
* operational simplicity

Complexity must not be introduced merely for future scalability.

---

# 3. Architectural Style

QuantOS V1 is a:

**Clean Architecture Modular Monolith**

All production modules execute within one application/deployment boundary.

The system is not divided into independently deployed microservices.

The following are explicitly NOT required by V1:

* microservices
* API gateway
* service mesh
* distributed message broker
* event bus
* Kubernetes
* cloud orchestration
* distributed cache
* service discovery
* independently deployed databases
* independently deployed strategy services

These technologies may be appropriate for a future system at significantly larger scale, but they are not part of V1.

---

# 4. High-Level Architecture

The system consists of six core production modules:

```text
┌─────────────────────────────────────────────┐
│                  QuantOS                    │
│                                             │
│  ┌───────────────┐                          │
│  │ Market Data   │                          │
│  └───────┬───────┘                          │
│          ↓                                  │
│  ┌───────────────┐                          │
│  │ Feature       │                          │
│  │ Engine        │                          │
│  └───────┬───────┘                          │
│          ↓                                  │
│  ┌───────────────┐                          │
│  │ Alpha Engine  │                          │
│  └───────┬───────┘                          │
│          ↓                                  │
│  ┌───────────────┐                          │
│  │ Risk Engine   │                          │
│  └───────┬───────┘                          │
│          ↓                                  │
│  ┌───────────────┐                          │
│  │ Execution     │                          │
│  │ Engine        │                          │
│  └───────┬───────┘                          │
│          ↓                                  │
│       Binance                               │
│                                             │
│  ┌─────────────────────────────┐            │
│  │ Evaluation Engine           │            │
│  └─────────────────────────────┘            │
│                                             │
└─────────────────────────────────────────────┘
```

The six modules are:

1. Market Data
2. Feature Engine
3. Alpha Engine
4. Risk Engine
5. Execution Engine
6. Evaluation Engine

These are the only V1 business modules.

---

# 5. Module Responsibilities

## 5.1 Market Data

The Market Data module is responsible for acquiring, validating, normalizing, storing, and providing market data.

Responsibilities include:

* historical Binance data acquisition
* live market-data acquisition
* schema validation
* timestamp validation
* duplicate detection
* missing-data detection
* data normalization
* dataset identity
* data replay for research/backtesting

The Market Data module must not contain trading strategy logic.

---

## 5.2 Feature Engine

The Feature Engine transforms validated market data into deterministic model inputs.

Responsibilities include:

* feature calculation
* timestamp alignment
* feature validation
* feature versioning
* leakage prevention
* deterministic feature generation

The Feature Engine must not:

* place orders
* make risk decisions
* directly communicate with Binance for execution
* contain strategy-selection logic

---

## 5.3 Alpha Engine

The Alpha Engine converts validated features into a trade proposal.

Responsibilities include:

* loading the production model
* generating model predictions
* applying the production decision rule
* producing trade proposals
* recording model/version information

The Alpha Engine must not:

* place live orders
* bypass Risk
* manage account balances
* modify exchange state

The Alpha Engine produces a proposal.

It does not have final authority over whether the proposal is executed.

---

## 5.4 Risk Engine

The Risk Engine determines whether a proposed trade is allowed.

Responsibilities include:

* position sizing
* risk-limit enforcement
* available-balance validation
* maximum-loss checks
* drawdown checks
* volatility-aware risk controls
* transaction-cost awareness
* exchange constraint checks
* trade approval
* trade rejection

The Risk Engine has authority to reject any Alpha proposal.

The Risk Engine must not be bypassable by the Execution Engine.

---

## 5.5 Execution Engine

The Execution Engine is responsible for converting an approved trade decision into a Binance Spot order and tracking its result.

Responsibilities include:

* order construction
* exchange constraint handling
* order submission
* acknowledgement
* order-state tracking
* fill tracking
* cancellation
* retry handling where safe
* execution error handling
* reconciliation support

Only the Execution Engine may submit live orders to Binance.

---

## 5.6 Evaluation Engine

The Evaluation Engine measures system and trading performance.

Responsibilities include:

* backtest evaluation
* paper-trading evaluation
* live-trading evaluation
* performance metrics
* drawdown analysis
* cost analysis
* trade statistics
* robustness results
* validation reporting

Evaluation must use consistent calculations across comparable runs.

---

# 6. Supporting Infrastructure

The six core modules operate using shared technical infrastructure.

Supporting infrastructure is not considered additional business modules.

V1 supporting infrastructure includes:

* configuration
* logging
* persistence
* model artifact storage
* research-run metadata
* Binance adapters
* time utilities
* validation utilities
* testing infrastructure

These components exist to support the six core modules.

They must not evolve into independent production services.

---

# 7. Clean Architecture

QuantOS follows Clean Architecture principles.

The architecture is divided conceptually into:

```text
Domain
  ↑
Application
  ↑
Infrastructure
```

Dependencies must point inward.

The domain must not depend directly on:

* Binance SDKs
* HTTP clients
* databases
* filesystem implementations
* external ML frameworks
* exchange-specific infrastructure

Infrastructure implements interfaces required by the application/domain layers.

---

# 8. Domain Layer

The Domain layer contains business concepts and rules that should remain independent of infrastructure.

Examples include:

* MarketData
* Candle
* FeatureVector
* Prediction
* TradeProposal
* RiskDecision
* Order
* Fill
* Position/Balance State
* Trade
* Performance Result

Domain objects must represent business meaning rather than external API formats.

Binance-specific response structures must not become domain objects directly.

---

# 9. Application Layer

The Application layer coordinates business workflows.

Examples include:

* ingesting market data
* generating features
* generating trade proposals
* evaluating risk
* executing approved orders
* running backtests
* running validation
* recording research runs
* calculating evaluation results

Application workflows coordinate domain rules and infrastructure interfaces.

They must not contain unnecessary framework-specific logic.

---

# 10. Infrastructure Layer

The Infrastructure layer provides concrete implementations of external dependencies.

Examples include:

* Binance REST client
* Binance market-data client
* Parquet storage
* DuckDB access
* model serialization
* filesystem access
* logging implementation
* configuration loading

Infrastructure may depend on external libraries.

Domain logic must not depend on those libraries.

---

# 11. Dependency Direction

Dependencies must follow:

```text
Infrastructure
      ↓
Application
      ↓
Domain
```

Conceptually:

```text
Domain
  ↑
Application
  ↑
Infrastructure
```

The important rule is:

**Business logic must not depend on infrastructure details.**

For example:

```text
Risk Rule
    ↓
Risk Interface
    ↓
Binance / Database / Runtime Implementation
```

rather than:

```text
Risk Rule
    ↓
Binance SDK
```

---

# 12. Production Runtime Flow

The production runtime flow is:

```text
Market Data
      ↓
Validated Market State
      ↓
Feature Engine
      ↓
Feature Vector
      ↓
Alpha Engine
      ↓
Trade Proposal
      ↓
Risk Engine
      ↓
Risk Decision
      ↓
Execution Engine
      ↓
Binance Spot
      ↓
Order / Fill Result
      ↓
Recorded State
      ↓
Evaluation
```

Every stage must validate its inputs.

Invalid data must not continue through the pipeline.

---

# 13. Alpha-to-Risk Boundary

The Alpha Engine produces a proposal.

Example conceptual flow:

```text
Prediction
   ↓
Decision Rule
   ↓
Trade Proposal
```

The proposal may contain:

* symbol
* timestamp
* direction
* proposed quantity or sizing input
* model output
* expected edge where applicable
* model version
* feature version
* decision metadata

The Alpha Engine does not determine final execution authority.

---

# 14. Risk-to-Execution Boundary

The Risk Engine produces a decision:

```text
Trade Proposal
      ↓
Risk Evaluation
      ↓
Approved / Rejected
```

Only an approved decision may be passed to Execution.

A rejected proposal must terminate the live trading path.

Execution must reject attempts to submit an order without valid risk approval.

---

# 15. Execution-to-Binance Boundary

Binance must be isolated behind an exchange adapter.

The production system must not spread Binance-specific API calls throughout business logic.

Conceptually:

```text
Execution Engine
       ↓
Exchange Interface
       ↓
Binance Adapter
       ↓
Binance
```

This keeps exchange-specific behavior isolated while still allowing V1 to remain Binance-only.

V1 does not require a multi-exchange abstraction framework.

The abstraction exists to protect the business layer from exchange-specific implementation details.

---

# 16. Market Data Storage

V1 uses local storage.

The intended storage foundation is:

**Parquet + DuckDB**

Parquet provides durable market-data storage.

DuckDB provides local analytical access.

The system does not require a distributed database.

The system does not require a cloud data lake.

Raw market data must remain immutable.

Derived datasets must be traceable to their source data.

---

# 17. Dataset Identity

A dataset used in research or validation must have an identifiable version or identity.

At minimum, the identity must account for:

* exchange
* symbol
* timeframe
* time range
* source
* schema/version
* validation state

Research runs must reference the dataset identity used.

This prevents a historical experiment from becoming ambiguous after the underlying data changes.

---

# 18. Feature Versioning

The Feature Engine must expose a feature specification/version identity.

A research run must be able to answer:

> Exactly which feature definition produced this result?

Changing a production feature definition must create a distinguishable version.

Historical experiment results must not silently change because a feature implementation was modified.

---

# 19. Model Artifact Boundary

The Alpha Engine must load an explicitly identified production model artifact.

The model artifact must have an identifiable version or identity.

A research model must not automatically become the production model.

Promotion requires explicit validation.

Conceptually:

```text
Research Model
      ↓
Validation
      ↓
Approved Model Artifact
      ↓
Production Model
```

---

# 20. Research Architecture

Research uses the same fundamental domain concepts as production wherever practical.

The research workflow is:

```text
Dataset
   ↓
Feature Specification
   ↓
Model Training
   ↓
Model Artifact
   ↓
Signal Generation
   ↓
Backtest
   ↓
Evaluation
   ↓
Validation
```

Research must not create a separate production architecture.

---

# 21. Qlib-Inspired Research Workflow

QuantOS may implement a lightweight research-run workflow inspired by Microsoft Qlib.

The purpose is reproducibility, not architectural dependency.

A research run should record:

* run identity
* code revision
* dataset identity
* feature version
* model version
* configuration
* training period
* validation period
* test period
* random seed where applicable
* evaluation configuration
* metrics
* validation outcome
* model artifact identity

A completed run must remain identifiable after the experiment has finished.

---

# 22. Qlib Boundary

Qlib is optional for V1 research use.

Qlib must not be required for:

* live trading
* paper trading
* risk decisions
* order execution
* Binance connectivity
* production feature generation
* production account management

If Qlib is used, it must remain isolated to offline research tooling.

QuantOS owns the production interfaces.

The system must remain functional without Qlib installed.

---

# 23. Backtest Architecture

The backtest should reuse the same core decision concepts as live trading.

Conceptually:

```text
Historical Market Data
        ↓
Feature Engine
        ↓
Production Model
        ↓
Trade Proposal
        ↓
Risk Engine
        ↓
Simulated Execution
        ↓
Account / Trade State
        ↓
Evaluation
```

The main difference is that execution is simulated rather than sent to Binance.

This minimizes the risk of research behavior diverging from production behavior.

---

# 24. Paper Trading Architecture

Paper trading should use the production decision path while replacing real exchange execution with a simulated execution boundary.

Conceptually:

```text
Live Market Data
      ↓
Features
      ↓
Production Model
      ↓
Risk
      ↓
Paper Execution
      ↓
Paper Account State
      ↓
Evaluation
```

Paper trading must not submit real orders.

---

# 25. Live Architecture

Live trading uses the same core production path:

```text
Binance Market Data
      ↓
Features
      ↓
Production Model
      ↓
Risk
      ↓
Execution
      ↓
Binance
```

The live path must not contain research-only components.

Qlib, exploratory models, notebooks, experiment search, and candidate strategies must never sit in the live order path.

---

# 26. Operating Mode Isolation

The system must clearly distinguish:

```text
RESEARCH
BACKTEST
PAPER
LIVE
```

The selected mode must determine which execution boundary is active.

A research or paper workflow must not accidentally submit a live order.

Live mode must require explicit activation.

---

# 27. State and Persistence

The system must persist important operational state.

Relevant state includes:

* datasets
* research-run metadata
* model artifacts
* configuration identity
* trades
* orders
* fills
* account state
* evaluation results
* validation results

Persistent state must be sufficient to reconstruct important historical decisions.

---

# 28. Reconciliation

The architecture must support reconciliation between local state and Binance state.

The reconciliation boundary includes:

```text
Local Recorded State
        ↕
Binance State
```

The system must be able to identify discrepancies involving:

* balances
* orders
* fills
* holdings
* recorded trades

Critical discrepancies must prevent unsafe trading until resolved or safely handled.

---

# 29. Failure Handling

Failures must propagate through explicit error handling.

Critical failures include:

* invalid market data
* stale data
* missing data
* invalid feature values
* model failure
* configuration failure
* Binance API failure
* network failure
* execution uncertainty
* reconciliation failure
* persistence failure
* risk-engine failure

The safe response to a critical trading-path failure is:

**Do not place a new order.**

The system must not silently continue with invalid or incomplete information.

---

# 30. Observability Architecture

Logging and audit information should be available across the six core modules.

Important events include:

```text
Market Data Event
Feature Event
Alpha Decision
Risk Decision
Execution Event
Evaluation Result
System Error
Reconciliation Event
```

The architecture must make it possible to trace a live trade across these stages.

A useful conceptual identifier is a trade/decision correlation identity that connects:

```text
Market Input
    ↓
Feature Generation
    ↓
Model Decision
    ↓
Risk Decision
    ↓
Order
    ↓
Fill
    ↓
Evaluation
```

---

# 31. Security Boundary

Secrets and exchange credentials belong to infrastructure/configuration.

They must not enter:

* domain objects
* model features
* research datasets
* logs
* source control

Binance API credentials must use the minimum required permissions.

Withdrawal permissions are not required for V1 trading.

---

# 32. Configuration Boundary

Configuration must be external to core business logic.

The architecture should allow configuration of:

* runtime mode
* exchange
* symbols
* timeframe
* risk limits
* feature parameters
* model parameters
* execution settings
* cost assumptions
* storage paths

Configuration changes must be observable and traceable for research and production runs.

---

# 33. Testing Architecture

Testing must occur at multiple levels.

## Unit Tests

Test domain and application rules independently.

Examples:

* risk calculations
* position sizing
* feature calculations
* model decision rules
* evaluation metrics

## Integration Tests

Test boundaries between components.

Examples:

* Parquet/DuckDB
* Binance adapters
* model artifact loading
* persistence
* execution lifecycle

## End-to-End Tests

Verify the complete workflow:

```text
Data
 ↓
Features
 ↓
Alpha
 ↓
Risk
 ↓
Execution
 ↓
Evaluation
```

End-to-end tests must include safe simulated execution before live deployment.

---

# 34. No Hidden Production Modules

The following must not be introduced as separate production modules:

* Portfolio Manager
* AI Service
* Strategy Service
* Model Service
* API Gateway
* Notification Service
* Market Regime Service
* Confidence Service
* Signal Service
* Data Service
* Research Service

Their required responsibilities must remain inside the six approved modules or supporting infrastructure.

This rule prevents accidental architectural expansion.

---

# 35. No Distributed Infrastructure

V1 does not require:

* message queues
* event brokers
* distributed caches
* service discovery
* load balancers
* container orchestration
* Kubernetes
* cloud databases
* distributed object stores

The system must operate on one local workstation.

---

# 36. Production Strategy Boundary

The architecture supports exactly one production strategy.

Research may contain candidate experiments.

Only the approved production strategy may enter the live runtime.

The production architecture must not contain a generic strategy orchestration framework unless required by the frozen V1 specification.

---

# 37. Production Model Boundary

The architecture supports exactly one production model.

Candidate models remain research artifacts until explicitly validated and promoted.

The live system must load one explicitly identified production model artifact.

The model must not be dynamically replaced during live execution without an explicit controlled deployment/change process.

---

# 38. Feature Complexity Boundary

The architecture must support the V1 feature budget:

**Target: 10–15**

**Maximum: 20**

The architecture must not encourage automatic generation of large feature sets.

Feature engineering must remain deterministic and versioned.

---

# 39. Data Flow Summary

The complete architecture can be summarized as:

```text
                 ┌──────────────────┐
                 │   Market Data    │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ Feature Engine   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │  Alpha Engine    │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │   Risk Engine    │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ Execution Engine │
                 └────────┬─────────┘
                          ↓
                     ┌─────────┐
                     │ Binance │
                     └────┬────┘
                          ↓
                 ┌──────────────────┐
                 │   Evaluation     │
                 │     Engine       │
                 └──────────────────┘
```

Supporting the entire system:

```text
Configuration
Persistence
Logging
Model Artifacts
Research Runs
Validation
Testing
```

These are supporting capabilities, not additional business modules.

---

# 40. Deployment Model

V1 is deployed locally as one application.

The intended environment is:

```text
Local Workstation
│
├── QuantOS Application
├── Local Configuration
├── Local Logs
├── Parquet Data
├── DuckDB
├── Model Artifacts
└── Research Results
```

External connectivity is required only where the application interacts with Binance or other explicitly required external resources.

---

# 41. Scaling Philosophy

V1 is not designed for distributed horizontal scaling.

The architecture should instead optimize for:

* low operational complexity
* deterministic execution
* easy debugging
* simple deployment
* local data access
* clear module ownership

If future scale requirements emerge, the modular boundaries should provide a foundation for later extraction.

However, future scalability must not complicate V1.

---

# 42. Architecture Decision Rules

When implementing a new component, ask:

1. Is it required by a frozen V1 requirement?
2. Does it belong to one of the six approved modules?
3. Can it remain inside the Modular Monolith?
4. Does it improve correctness, safety, reproducibility, or testability?
5. Does it introduce unnecessary complexity?
6. Does it increase overfitting or operational risk?

If a component is not required, it should not be added.

---

# 43. V1 Architecture Acceptance Criteria

The architecture is considered compliant when:

* QuantOS runs as a Modular Monolith.
* Clean Architecture dependency direction is maintained.
* Exactly six core production modules exist.
* Binance Spot is the only production exchange.
* BTCUSDT and ETHUSDT are supported.
* 1-minute data is the primary trading timeframe.
* One production strategy exists.
* One production model exists.
* Production features remain within the 10–20 feature budget.
* Risk controls sit between Alpha and Execution.
* Only Execution can submit Binance orders.
* Research cannot directly execute live orders.
* Qlib is not required for production.
* Qlib is not in the live execution path.
* Research runs are reproducible.
* Dataset, feature, model, and configuration versions are traceable.
* Parquet and DuckDB provide the local data foundation.
* Backtest and paper execution can reuse the production decision path.
* Critical failures default to safe behavior.
* The entire system can run on one local workstation.
* No unnecessary distributed infrastructure is required.

---

# 44. Final Architecture Statement

QuantOS V1 is intentionally a **small Clean Architecture Modular Monolith**.

Its architecture is built around six responsibilities:

```text
Market Data
Feature Engine
Alpha Engine
Risk Engine
Execution Engine
Evaluation Engine
```

The system must be capable of moving from research to live trading without creating separate architectures for each stage.

Research reproducibility is strengthened using Qlib-inspired experiment discipline, but Qlib itself is not part of the production architecture.

The architecture must remain simple enough that the entire V1 system can be understood, tested, debugged, and operated by a small team on a local workstation.

**Do not build infrastructure for a future QuantOS until the V1 trading system has first proven itself.**
