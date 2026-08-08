# 008 — IMPLEMENTATION GUIDE

## Part 1 — Repository & Software Architecture

**Project:** QuantOS Core
**Document:** 008 — Implementation Guide
**Part:** 1 of 4
**Status:** Engineering Specification
**Version:** V1

---

# 1. Purpose

This document is the implementation bridge between the QuantOS specifications and the actual repository.

The preceding specifications define:

* what QuantOS is;
* what V1 must accomplish;
* how the system is architected;
* how market data is represented and validated;
* how features are generated;
* how alpha is produced;
* how risk and execution are controlled;
* how strategies are validated and backtested.

This document defines how those specifications become software.

The implementation must preserve the architectural and behavioral contracts established by documents `000` through `007`.

The objective is not to build the largest possible trading platform.

The objective is to build the **smallest production-grade implementation capable of executing the V1 strategy safely and deterministically in real time**.

The implementation therefore follows these principles:

1. **Specifications are authoritative.**
2. **Domain contracts are explicit.**
3. **Research and live trading share the same core logic wherever possible.**
4. **Infrastructure must not contain trading decisions.**
5. **Trading decisions must not depend on infrastructure implementation details.**
6. **Every production-critical boundary must be testable independently.**
7. **Determinism and reproducibility are first-class requirements.**
8. **Fail closed rather than fail open.**
9. **Paper trading and live trading must use the same execution contracts.**
10. **V1 must remain operationally simple.**

---

# 2. Implementation Philosophy

QuantOS should be implemented as a **modular monolith** for V1.

It is not a microservices system.

The repository should have strong internal boundaries without introducing unnecessary network boundaries.

For V1:

```text
                    ┌──────────────────────┐
                    │      QuantOS         │
                    │    Application       │
                    └──────────┬───────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
   Market Data             Strategy             Execution
        │                      │                      │
        ▼                      ▼                      ▼
   Data Engine          Feature/Alpha/Risk      Broker Adapter
```

The major subsystems remain independently testable, but they execute inside one controlled application.

This gives V1:

* fewer deployment failure modes;
* simpler debugging;
* easier state management;
* lower operational complexity;
* easier reproduction of research results;
* fewer distributed-system problems;
* faster iteration.

Distributed infrastructure may be introduced later if justified by actual scale.

It must not be introduced merely because it is architecturally fashionable.

---

# 3. Repository as the System Boundary

The repository is the canonical implementation of QuantOS.

The implementation should be organized around **domain boundaries**, not around arbitrary technical layers.

Recommended V1 structure:

```text
QuantOS/
│
├── docs/
│   ├── 000_READ_FIRST.md
│   ├── 001_PRD.md
│   ├── 002_ARCHITECTURE.md
│   ├── 003_DATA.md
│   ├── 004_FEATURE_ENGINE.md
│   ├── 005_ALPHA_ENGINE.md
│   ├── 006_RISK_EXECUTION.md
│   ├── 007_VALIDATION_BACKTESTING.md
│   └── 008_IMPLEMENTATION_GUIDE.md
│
├── src/
│   └── quantos/
│       │
│       ├── core/
│       ├── data/
│       ├── features/
│       ├── alpha/
│       ├── portfolio/
│       ├── risk/
│       ├── execution/
│       ├── backtest/
│       ├── validation/
│       ├── broker/
│       ├── config/
│       ├── storage/
│       ├── observability/
│       └── application/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── backtest/
│   ├── regression/
│   └── fixtures/
│
├── configs/
│   ├── base/
│   ├── development/
│   ├── paper/
│   └── production/
│
├── scripts/
│
├── data/
│   ├── raw/
│   ├── normalized/
│   ├── features/
│   └── backtests/
│
├── notebooks/
│
├── migrations/
│
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
└── Makefile
```

The exact filenames may evolve during implementation, but the architectural separation must remain intact.

---

# 4. Package Responsibilities

## 4.1 `core`

`core` contains the smallest set of domain primitives shared by the system.

It must not depend on:

* brokers;
* databases;
* exchanges;
* notebooks;
* web frameworks;
* cloud providers;
* strategy-specific implementations.

Example:

```text
core/
├── enums.py
├── identifiers.py
├── time.py
├── money.py
├── quantities.py
├── events.py
├── errors.py
└── types.py
```

Typical primitives include:

```text
Symbol
Timestamp
Price
Quantity
Notional
Currency
Side
OrderType
OrderStatus
Position
AccountState
MarketEvent
Signal
Order
Fill
```

These objects represent domain concepts rather than implementation details.

---

# 5. Data Package

```text
data/
├── models/
├── ingestion/
├── normalization/
├── quality/
├── providers/
├── clocks/
└── repository/
```

The Data subsystem implements the contracts defined by `003_DATA`.

Its responsibilities include:

* receiving market data;
* validating incoming data;
* normalizing provider-specific representations;
* assigning canonical timestamps;
* detecting missing or invalid data;
* exposing canonical market-data objects;
* persisting data where required.

The data package must **not**:

* generate alpha;
* calculate trading signals;
* determine position size;
* place orders.

The dependency direction is:

```text
Provider
   │
   ▼
Ingestion
   │
   ▼
Normalization
   │
   ▼
Quality Validation
   │
   ▼
Canonical Data
```

Provider-specific logic must remain isolated from the rest of QuantOS.

---

# 6. Feature Package

```text
features/
├── definitions/
├── indicators/
├── transforms/
├── registry.py
├── engine.py
└── validation.py
```

The Feature Engine implements `004_FEATURE_ENGINE`.

Its primary responsibility is:

```text
Market Data
     │
     ▼
Feature Engine
     │
     ▼
Canonical Feature Set
```

A feature implementation should behave as a deterministic transformation.

Conceptually:

```python
features = feature_engine.compute(
    market_state=market_state,
    timestamp=timestamp,
)
```

Feature code must not:

* submit orders;
* access broker APIs;
* change portfolio state;
* contain execution logic;
* secretly access future observations.

A feature should be independently testable using historical inputs.

---

# 7. Alpha Package

```text
alpha/
├── models/
├── strategies/
├── signals/
├── registry.py
├── engine.py
└── lifecycle.py
```

The Alpha Engine implements `005_ALPHA_ENGINE`.

Its responsibility is to transform validated feature/state information into an explicit trading intent.

Conceptually:

```text
Features
   │
   ▼
Alpha Engine
   │
   ▼
Signal / Trading Intent
```

For example:

```python
signal = alpha_engine.generate(
    market_state=market_state,
    features=features,
    portfolio_state=portfolio_state,
)
```

The alpha subsystem may express:

* direction;
* expected edge;
* confidence;
* desired exposure;
* signal strength;
* entry/exit intent.

It must not directly control the broker.

This boundary is critical.

The Alpha Engine says:

> "This is what the strategy wants."

It does not say:

> "Send this order to the exchange."

That decision belongs downstream.

---

# 8. Portfolio Package

```text
portfolio/
├── models.py
├── state.py
├── accounting.py
└── exposure.py
```

The portfolio subsystem represents the current trading state.

It is responsible for maintaining the canonical representation of:

* cash;
* equity;
* positions;
* average entry;
* realized PnL;
* unrealized PnL;
* exposure;
* available capital;
* portfolio-level state.

The portfolio state must be derived from authoritative events wherever possible.

Example:

```text
Market Data
     +
Fills
     +
Account Events
     ↓
Portfolio State
```

The portfolio package must not decide whether a trade is desirable.

It answers:

> "What does the portfolio currently look like?"

---

# 9. Risk Package

```text
risk/
├── limits/
├── sizing/
├── checks/
├── state.py
└── engine.py
```

The Risk subsystem implements `006_RISK_EXECUTION`.

It sits between trading intent and executable orders.

Conceptually:

```text
Alpha Signal
     │
     ▼
Risk Engine
     │
     ├── allowed?
     ├── position size?
     ├── exposure allowed?
     ├── loss limits?
     ├── portfolio constraints?
     └── trading state?
     │
     ▼
Approved Order Intent
```

Risk is a **hard control boundary**.

An alpha signal cannot bypass it.

The implementation must make bypassing the Risk Engine structurally difficult.

Bad:

```python
broker.place_order(alpha_signal)
```

Correct conceptual flow:

```python
signal = alpha.generate(...)
decision = risk.evaluate(signal, portfolio_state)

if decision.approved:
    execution.submit(decision.order_intent)
```

Risk checks should fail closed.

If the system cannot determine whether an order is permitted, the default result must be:

```text
DO NOT TRADE
```

---

# 10. Execution Package

```text
execution/
├── models/
├── orders/
├── state/
├── router.py
├── manager.py
└── reconciliation.py
```

Execution implements the execution requirements from `006`.

The execution subsystem owns:

* order lifecycle;
* order submission;
* cancellation;
* order status;
* fills;
* retries where explicitly permitted;
* reconciliation;
* execution state.

Execution receives an approved order intent.

It does not independently generate alpha.

Conceptually:

```text
Approved Order Intent
        │
        ▼
Execution Manager
        │
        ▼
Order Router
        │
        ▼
Broker Adapter
        │
        ▼
Exchange / Broker
```

Execution must maintain a distinction between:

```text
Order Intent
Order
Broker Order
Fill
Position
```

These are not interchangeable objects.

---

# 11. Broker Package

```text
broker/
├── interfaces.py
├── models.py
├── adapters/
│   ├── paper/
│   └── live/
└── reconciliation.py
```

The broker package isolates external execution infrastructure.

The rest of QuantOS should interact with an abstract broker interface.

Example:

```python
class Broker(Protocol):

    def submit_order(self, order: Order) -> BrokerOrder:
        ...

    def cancel_order(self, order_id: OrderId) -> None:
        ...

    def get_order(self, order_id: OrderId) -> BrokerOrder:
        ...

    def get_positions(self) -> list[Position]:
        ...

    def get_account(self) -> AccountState:
        ...
```

The exact interface will be finalized during Part 2.

The critical principle is:

```text
QuantOS Core
     │
     ▼
Broker Interface
     │
     ├── Paper Adapter
     │
     └── Live Adapter
```

The strategy must not know which broker is being used.

This enables:

* backtesting;
* simulation;
* paper trading;
* live trading;

without rewriting the strategy itself.

---

# 12. Backtest Package

```text
backtest/
├── engine.py
├── clock.py
├── simulator.py
├── portfolio.py
├── execution.py
└── results.py
```

The Backtest Engine implements `007_VALIDATION_BACKTESTING`.

The backtest system should simulate the same conceptual lifecycle as live trading:

```text
Historical Market Data
        │
        ▼
Feature Engine
        │
        ▼
Alpha Engine
        │
        ▼
Risk Engine
        │
        ▼
Simulated Execution
        │
        ▼
Portfolio
        │
        ▼
Performance Results
```

The backtest engine may simulate market mechanics.

It must not introduce strategy behavior that does not exist in live execution.

The objective is **behavioral parity**, not identical infrastructure.

---

# 13. Validation Package

```text
validation/
├── datasets/
├── checks/
├── leakage/
├── metrics/
├── reports/
└── gates/
```

Validation implements the testing and statistical validation requirements of `007`.

It should answer questions such as:

* Is the dataset valid?
* Is the experiment reproducible?
* Is there leakage?
* Does the strategy pass required validation gates?
* Are results statistically and economically meaningful?
* Does the strategy remain viable under realistic costs?
* Does performance survive out-of-sample testing?

Validation is separate from Backtesting.

Backtesting answers:

> "What would have happened under this simulation?"

Validation answers:

> "Should we trust the conclusion?"

That distinction should remain explicit in code.

---

# 14. Configuration Package

```text
config/
├── models.py
├── loader.py
├── validation.py
└── defaults.py
```

Configuration must be explicit and environment-aware.

The system should distinguish at minimum:

```text
development
paper
production
```

Configuration should control operational parameters, not secretly redefine strategy behavior.

Examples:

```yaml
environment: paper

market:
  symbol: BTCUSDT
  timeframe: 1m

risk:
  max_position: ...
  max_daily_loss: ...

execution:
  mode: paper
  order_timeout: ...

logging:
  level: INFO
```

Secrets must never be committed to the repository.

Credentials must come from environment variables or an appropriate secret-management mechanism.

---

# 15. Storage Package

```text
storage/
├── interfaces.py
├── models.py
├── repositories/
├── serializers/
└── migrations/
```

Storage provides persistence abstractions.

The domain layer should not directly depend on SQL queries, filesystem paths, or a specific database.

For example:

```python
class MarketDataRepository(Protocol):
    def write(self, data: MarketData) -> None:
        ...

    def read(self, query: DataQuery) -> Iterable[MarketData]:
        ...
```

This makes the storage implementation replaceable without changing domain logic.

V1 should prefer the simplest storage architecture that satisfies:

* reliability;
* reproducibility;
* sufficient throughput;
* historical research requirements;
* live-state persistence requirements.

---

# 16. Observability Package

```text
observability/
├── logging.py
├── metrics.py
├── events.py
├── health.py
└── alerts.py
```

Observability is part of the trading system, not an afterthought.

At minimum, QuantOS must be able to determine:

```text
Is the process alive?
Is market data arriving?
Is market data valid?
Are features updating?
Is alpha running?
Is risk healthy?
Are orders being acknowledged?
Are fills arriving?
Does internal state match broker state?
Is the system allowed to trade?
```

Every production decision should be explainable from logs/events/state.

The system must not produce a mysterious:

```text
BUY
```

without the ability to reconstruct why it happened.

---

# 17. Application Package

```text
application/
├── runtime.py
├── pipeline.py
├── lifecycle.py
├── dependency.py
└── modes.py
```

The Application layer composes the individual subsystems.

It is responsible for orchestration.

For example:

```text
Market Event
     │
     ▼
Data Validation
     │
     ▼
Feature Computation
     │
     ▼
Alpha Generation
     │
     ▼
Risk Evaluation
     │
     ▼
Execution
     │
     ▼
Portfolio Update
     │
     ▼
Observability
```

The application layer should coordinate these components rather than implement their domain logic.

---

# 18. Dependency Direction

Dependencies must flow inward toward domain abstractions.

Preferred:

```text
                  ┌─────────────┐
                  │    Core     │
                  └──────▲──────┘
                         │
       ┌─────────────────┼──────────────────┐
       │                 │                  │
       │                 │                  │
    Data              Alpha              Risk
       │                 │                  │
       └─────────────────┼──────────────────┘
                         │
                     Execution
                         │
                       Broker
                         │
                    External World
```

Infrastructure may depend on domain interfaces.

Domain logic must not depend on infrastructure implementations.

For example:

```text
GOOD

alpha → core
risk → core
execution → core
broker → execution/core
```

Not:

```text
BAD

alpha → Binance SDK
risk → PostgreSQL
feature → broker
core → exchange API
```

This separation is essential for backtesting and live/paper parity.

---

# 19. Interface-First Design

Production-critical subsystems should be defined through explicit interfaces.

Important interfaces include:

```text
MarketDataProvider
MarketDataRepository
Feature
FeatureEngine
AlphaStrategy
AlphaEngine
RiskRule
RiskEngine
PortfolioState
Broker
ExecutionEngine
Clock
OrderRepository
EventStore
```

The implementation should favor small interfaces.

An interface should represent a meaningful domain capability.

Avoid creating enormous "God Interfaces".

Bad:

```python
QuantOSSystemInterface
```

containing dozens of unrelated methods.

Prefer:

```python
MarketDataProvider
FeatureEngine
AlphaStrategy
RiskEngine
Broker
```

with each interface representing one responsibility.

---

# 20. Clock Abstraction

Time must be treated as an explicit dependency.

The system should not scatter direct calls to:

```python
datetime.now()
time.time()
```

throughout trading logic.

Instead:

```python
class Clock(Protocol):
    def now(self) -> Timestamp:
        ...
```

Implementations can include:

```text
RealClock
HistoricalClock
SimulatedClock
```

This is critical for backtesting.

The same strategy code can therefore operate against:

```text
HistoricalClock
        ↓
Backtest

RealClock
        ↓
Live
```

without changing its trading logic.

---

# 21. Event-Oriented State Changes

Where practical, important state transitions should be represented explicitly as events.

Examples:

```text
MarketDataReceived
FeatureSetComputed
SignalGenerated
RiskApproved
RiskRejected
OrderSubmitted
OrderAccepted
OrderRejected
OrderCancelled
FillReceived
PositionUpdated
RiskLimitBreached
TradingHalted
```

Events should carry enough information to reconstruct system behavior.

However, V1 should not become a full distributed event-sourcing platform.

The goal is **traceability**, not architectural complexity.

---

# 22. Canonical Domain Objects

The following objects should have canonical definitions.

```text
MarketData
FeatureSet
Signal
OrderIntent
Order
BrokerOrder
Fill
Position
PortfolioState
RiskDecision
AccountState
```

The same conceptual object should not have five incompatible representations across the repository.

For example, do not create:

```text
AlphaOrder
RiskOrder
ExecutionOrder
BrokerOrder
LiveOrder
```

when they are actually different lifecycle states of the same domain concept.

Where distinctions are necessary, model them explicitly.

---

# 23. Serialization Boundary

Domain objects should have explicit serialization rules.

This matters for:

* persistence;
* logs;
* debugging;
* testing;
* replay;
* backtest results;
* future interoperability.

Serialization must be deterministic.

For example:

```text
Domain Object
      │
      ▼
Canonical Schema
      │
      ├── JSON
      ├── Database
      └── Event Log
```

The serialized representation must not silently change between runs.

Schema changes should be deliberate and versioned when necessary.

---

# 24. Error Model

Errors should be classified rather than handled generically.

At minimum:

```text
DataError
FeatureError
AlphaError
RiskError
ExecutionError
BrokerError
StorageError
ConfigurationError
ValidationError
SystemError
```

The system should distinguish between:

### Recoverable errors

Examples:

* temporary data-provider disconnect;
* transient broker communication failure;
* delayed market-data message.

### Non-recoverable errors

Examples:

* corrupted configuration;
* invalid risk configuration;
* inconsistent portfolio state;
* impossible order quantity;
* unreconciled broker position;
* corrupted persisted state.

Production behavior must depend on the error class.

A trading system must never blindly retry every exception.

---

# 25. Fail-Closed Architecture

The following conditions should default to **no trading**:

```text
Unknown portfolio state
Unknown broker state
Invalid market data
Stale market data
Failed risk check
Missing required feature
Invalid configuration
Clock failure
Unreconciled position
Exceeded risk limit
Execution state inconsistency
Critical subsystem unhealthy
```

The desired behavior is:

```text
UNKNOWN
  ↓
SAFE STATE
  ↓
NO NEW ORDERS
```

This is one of the most important implementation rules inherited from the Risk & Execution specification.

---

# 26. Research/Production Boundary

Research code must not become an accidental production dependency.

Notebooks may import QuantOS libraries:

```text
Notebook
   │
   ▼
QuantOS APIs
```

But production code must never import:

```text
notebooks/
```

Similarly:

```text
research experiment
```

must not silently mutate:

```text
production state
```

Research generates artifacts.

Production consumes validated, versioned artifacts.

---

# 27. Strategy Boundary

A strategy should be implementable as a replaceable component.

Conceptually:

```python
class AlphaStrategy(Protocol):

    def generate_signal(
        self,
        market_state: MarketState,
        features: FeatureSet,
        portfolio: PortfolioState,
    ) -> Signal:
        ...
```

The application should not care whether the implementation is:

```text
Rule-based
Statistical
ML model
Hybrid
```

The Alpha Engine contract remains stable.

This allows V1 to remain simple while preserving the ability to evolve the research layer later.

---

# 28. Live/Paper/Backtest Parity

QuantOS should maintain a common execution pipeline:

```text
                  ┌───────────────┐
                  │ Strategy Core │
                  └───────┬───────┘
                          │
                   Feature/Alpha
                          │
                          ▼
                       Risk
                          │
                          ▼
                    Order Intent
                          │
             ┌────────────┼────────────┐
             │            │            │
          Backtest      Paper         Live
             │            │            │
        Simulator     Paper API    Broker API
```

The strategy should not contain:

```python
if live:
    ...
elif backtest:
    ...
```

for core trading logic.

Mode-specific behavior belongs at infrastructure boundaries.

This is one of the primary mechanisms for preventing divergence between research and production.

---

# 29. Configuration and Code Separation

Trading parameters should not be hardcoded throughout the implementation.

Bad:

```python
if position > 0.25:
    reject()
```

Better:

```python
if position > config.risk.max_position:
    reject()
```

However, configuration must not become a hidden strategy engine.

Core strategy definitions should remain version-controlled and explicit.

Configuration controls deployment/runtime behavior.

Code defines system behavior.

Both must be versioned.

---

# 30. State Ownership

Every important piece of state must have one authoritative owner.

Example:

```text
Market Data
    → Data subsystem

Feature Values
    → Feature subsystem

Portfolio State
    → Portfolio subsystem

Risk State
    → Risk subsystem

Order Lifecycle
    → Execution subsystem

Broker Reality
    → Broker adapter / reconciliation
```

Multiple components may read state.

They should not independently mutate the same authoritative state.

This prevents contradictory state such as:

```text
Risk Engine says:
Position = 0.5 BTC

Broker says:
Position = 0.7 BTC

Portfolio says:
Position = 0.6 BTC
```

Such discrepancies must be detected and resolved rather than ignored.

---

# 31. Repository Rules

The repository should enforce several architectural rules.

### Rule 1 — No broker calls from strategy code

```text
alpha/
```

must never directly invoke broker APIs.

### Rule 2 — No risk bypass

Every executable order must pass through the Risk Engine.

### Rule 3 — No production imports from notebooks

Production packages cannot depend on research notebooks.

### Rule 4 — No credentials in source

Secrets must never be committed.

### Rule 5 — No hidden time dependencies

Trading logic must use the Clock abstraction.

### Rule 6 — No hidden randomness

Randomized algorithms must accept explicit seeds/configuration.

### Rule 7 — No future-data access

Feature and alpha implementations must respect the information boundary defined by `007`.

### Rule 8 — No unvalidated production strategy

Only strategies satisfying the validation gates may enter the production configuration.

---

# 32. Initial Package Dependency Graph

The intended dependency graph is:

```text
                         core
                          ▲
                          │
          ┌───────────────┼────────────────┐
          │               │                │
         data          features          portfolio
          │               │                │
          └───────┬───────┘                │
                  ▼                        │
                alpha ◄────────────────────┘
                  │
                  ▼
                 risk
                  │
                  ▼
              execution
                  │
                  ▼
                broker
```

Supporting infrastructure:

```text
config ─────────────► application
storage ────────────► data / portfolio / execution
observability ─────► application / all runtime components
validation ────────► backtest / research
backtest ──────────► data / features / alpha / risk / execution
```

The exact import graph should be enforced through tests or static architecture checks where practical.

---

# 33. V1 Architectural Target

The V1 implementation should ultimately look conceptually like:

```text
                    QUANTOS V1
                        │
          ┌─────────────┴─────────────┐
          │                           │
     Research Path               Live Path
          │                           │
       Historical                 Real-time
        Data                        Data
          │                           │
          └──────────┬────────────────┘
                     │
                     ▼
                Data Engine
                     │
                     ▼
              Feature Engine
                     │
                     ▼
                Alpha Engine
                     │
                     ▼
                 Risk Engine
                     │
                     ▼
             Execution Engine
                     │
                     ▼
               Broker Adapter
                     │
                     ▼
                Real Market
```

With validation surrounding the research-to-production transition:

```text
Research
   │
   ▼
Backtest
   │
   ▼
Validation Gates
   │
   ▼
Paper Trading
   │
   ▼
Live Validation
   │
   ▼
V1 Production
```

The implementation guide exists to make this path executable.

---

# 34. Definition of Architectural Completion

Part 1 is considered implemented when the repository has:

* a clear package hierarchy;
* explicit subsystem boundaries;
* canonical domain objects;
* explicit interfaces;
* dependency direction enforced;
* configuration separated from code;
* broker abstraction established;
* clock abstraction established;
* storage boundaries established;
* application orchestration defined;
* observability integrated into the runtime design;
* fail-closed behavior represented architecturally;
* backtest/paper/live modes sharing the same core pipeline.

At this point, QuantOS has the **software skeleton**.

The skeleton is intentionally incomplete.

Part 2 will define the contracts that make the skeleton executable:

```text
interfaces
schemas
events
configuration models
state transitions
data contracts
order contracts
risk contracts
execution contracts
and backtest/live parity
```

Those contracts are the point where the specifications become concrete code-level APIs.

# 35. Purpose of the Contract Layer

Part 1 established the QuantOS repository structure and subsystem boundaries.

This section defines the **interfaces, schemas, state models, and runtime contracts** that connect those subsystems.

The objective is to eliminate implementation ambiguity.

An engineer implementing QuantOS should be able to determine:

* what objects cross subsystem boundaries;
* what each object means;
* which subsystem owns each state;
* which interfaces must exist;
* what inputs and outputs each component accepts;
* how errors propagate;
* how backtest, paper, and live execution share contracts;
* how configuration is represented;
* how runtime events move through the system.

The implementation should prefer explicit, typed contracts over implicit conventions.

---

# 36. Contract Hierarchy

QuantOS uses three broad categories of contracts:

```text
                    QuantOS Contracts
                          │
          ┌───────────────┼────────────────┐
          │               │                │
       Domain          Runtime          External
      Contracts       Contracts         Contracts
          │               │                │
          ▼               ▼                ▼
     Data/Signal      Lifecycle        Broker/API
     Order/Risk       State/Event      Storage
     Portfolio        Config           Provider
```

### 36.1 Domain Contracts

Domain contracts define what an object means.

Examples:

```text
MarketData
FeatureSet
Signal
OrderIntent
Order
Fill
Position
PortfolioState
RiskDecision
```

### 36.2 Runtime Contracts

Runtime contracts define how components interact.

Examples:

```text
MarketDataProvider
FeatureEngine
AlphaStrategy
RiskEngine
ExecutionEngine
Clock
```

### 36.3 External Contracts

External contracts define interaction with systems outside QuantOS.

Examples:

```text
Exchange API
Broker API
Database
Filesystem
Market Data Provider
```

External implementations must adapt to QuantOS contracts rather than forcing provider-specific behavior into the core domain.

---

# 37. Canonical Identifier Types

Production-critical identifiers should use explicit types rather than arbitrary strings.

Core identifiers include:

```text
Symbol
StrategyId
FeatureId
SignalId
OrderId
BrokerOrderId
FillId
PositionId
RunId
ExperimentId
ModelId
```

Conceptually:

```python
class Symbol(str):
    pass

class StrategyId(str):
    pass

class OrderId(str):
    pass
```

The implementation may use stronger value objects where appropriate.

The important requirement is that semantically different identifiers remain distinguishable.

For example:

```text
OrderId ≠ SignalId ≠ StrategyId
```

even if their serialized representations are strings.

---

# 38. Time Contract

Time is a first-class QuantOS domain concept.

Every time-bearing object must make its timestamp semantics explicit.

At minimum, distinguish:

```text
event_time
received_time
processed_time
```

### 38.1 Event Time

`event_time` represents when the event actually occurred in the market or external system.

### 38.2 Received Time

`received_time` represents when QuantOS received the event.

### 38.3 Processed Time

`processed_time` represents when QuantOS processed the event.

For example:

```text
Market Event

event_time      = 10:00:00.125
received_time   = 10:00:00.143
processed_time  = 10:00:00.149
```

This distinction is required for:

* latency measurement;
* data-quality analysis;
* backtesting;
* leakage prevention;
* production debugging.

---

# 39. Clock Interface

Trading logic must not directly depend on wall-clock functions.

The system should expose:

```python
class Clock(Protocol):

    def now(self) -> Timestamp:
        ...
```

Implementations include:

```text
RealClock
HistoricalClock
SimulatedClock
```

### RealClock

Used by production runtime.

### HistoricalClock

Used by deterministic backtesting.

### SimulatedClock

Used by tests and controlled simulations.

This abstraction allows identical strategy code to operate against historical and real-time timelines.

---

# 40. Market Data Schema

The canonical market-data object represents normalized market information.

Conceptually:

```python
@dataclass(frozen=True)
class MarketData:
    symbol: Symbol

    event_time: Timestamp
    received_time: Timestamp | None

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    volume: Decimal

    source: str
    sequence: int | None
```

The exact fields must remain consistent with the canonical data model established in `003_DATA`.

Implementation requirements:

1. Financial values must use appropriate precision.
2. Timestamps must be timezone-aware.
3. Symbols must be canonicalized.
4. Provider-specific representations must not leak into the domain model.
5. Invalid data must be rejected before entering the feature pipeline.

---

# 41. Market Data Provider Interface

The live provider boundary should conceptually expose:

```python
class MarketDataProvider(Protocol):

    def subscribe(
        self,
        symbols: Sequence[Symbol],
    ) -> Iterable[MarketData]:
        ...
```

Historical access should use a separate capability:

```python
class HistoricalDataProvider(Protocol):

    def query(
        self,
        symbol: Symbol,
        start: Timestamp,
        end: Timestamp,
    ) -> Iterable[MarketData]:
        ...
```

Live and historical providers may use completely different transport mechanisms.

They must nevertheless produce the same canonical domain representation.

---

# 42. Market Data Quality Contract

Before data enters the Feature Engine, it must pass the data-quality requirements defined by `003_DATA`.

Checks should include, where applicable:

```text
Timestamp validity
Symbol validity
Price validity
OHLC consistency
Volume validity
Sequence consistency
Duplicate detection
Missing interval detection
Staleness
Provider integrity
```

For OHLC data, basic invariants include:

```text
high >= max(open, close)
low  <= min(open, close)
high >= low
volume >= 0
```

A failed validation must produce a structured data-quality result or event.

Corrupted data must never silently pass downstream.

---

# 43. Feature Contract

Every feature must implement a common contract.

Conceptually:

```python
class Feature(Protocol):

    @property
    def feature_id(self) -> FeatureId:
        ...

    def compute(
        self,
        context: FeatureContext,
    ) -> FeatureValue:
        ...
```

Each feature should declare, where practical:

```text
feature_id
required inputs
lookback
timeframe
output type
validity requirements
version
```

For example:

```text
Feature:
    id: momentum_20
    input: close
    lookback: 20
    output: Decimal
```

Feature metadata should be machine-readable where possible.

---

# 44. Feature Context

The Feature Engine must receive an explicit context.

Conceptually:

```python
@dataclass(frozen=True)
class FeatureContext:
    timestamp: Timestamp
    market_data: MarketDataWindow
    portfolio: PortfolioState | None
```

The context must contain only information legitimately available at the current decision timestamp.

This creates a structural defense against look-ahead bias.

A feature must not independently reach into a future dataset or arbitrary global state.

---

# 45. Feature Set Schema

The Feature Engine produces a canonical feature set:

```python
@dataclass(frozen=True)
class FeatureSet:
    timestamp: Timestamp
    values: Mapping[FeatureId, FeatureValue]
    version: str
```

Example:

```text
timestamp: 2026-08-08T10:00:00Z

features:
    momentum_20: 0.034
    volatility_20: 0.021
    volume_ratio: 1.42

version:
    feature-set-v1
```

Feature computation must be deterministic for identical:

```text
inputs
configuration
feature version
```

---

# 46. Feature Registry

Features should be explicitly registered.

Conceptually:

```python
class FeatureRegistry:

    def register(self, feature: Feature) -> None:
        ...

    def get(self, feature_id: FeatureId) -> Feature:
        ...

    def all(self) -> Sequence[Feature]:
        ...
```

The production feature set must be explicitly defined and versioned.

There should be no hidden feature discovery during production execution.

---

# 47. Alpha Strategy Contract

Every strategy must implement a stable interface.

Conceptually:

```python
class AlphaStrategy(Protocol):

    @property
    def strategy_id(self) -> StrategyId:
        ...

    def generate_signal(
        self,
        context: StrategyContext,
    ) -> Signal:
        ...
```

The strategy context should contain only information legitimately available at the decision timestamp.

Conceptually:

```python
@dataclass(frozen=True)
class StrategyContext:
    timestamp: Timestamp
    market_state: MarketState
    features: FeatureSet
    portfolio: PortfolioState
```

The Alpha Engine remains responsible for transforming information into trading intent.

---

# 48. Signal Schema

A signal is a **strategy-level expression of intent**.

It is not an executable order.

Conceptually:

```python
@dataclass(frozen=True)
class Signal:
    signal_id: SignalId
    strategy_id: StrategyId
    symbol: Symbol

    timestamp: Timestamp

    direction: SignalDirection
    strength: Decimal
    expected_edge: Decimal | None

    target_exposure: Decimal | None

    reason: str | None
```

The final field set must remain consistent with `005_ALPHA_ENGINE`.

The critical lifecycle is:

```text
Signal
   ↓
Risk Decision
   ↓
Order Intent
```

A signal must never directly reach the broker.

---

# 49. Signal Direction

Signal direction must be semantic rather than encoded through magic numbers.

The domain should use explicit values such as:

```text
LONG
SHORT
FLAT
EXIT
```

The final enum must follow the Alpha Engine specification.

Avoid exposing:

```text
1
0
-1
```

as the public semantic representation of direction.

---

# 50. Order Intent

`OrderIntent` represents the executable intention produced after risk approval.

Conceptually:

```python
@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    signal_id: SignalId

    symbol: Symbol

    side: Side
    quantity: Decimal

    order_type: OrderType
    limit_price: Decimal | None

    reduce_only: bool
    time_in_force: TimeInForce
```

The distinction is essential:

```text
"We want to buy 0.1 BTC"
```

is an `OrderIntent`.

It does not mean:

```text
"The exchange accepted order 12345."
```

That is a broker execution state.

---

# 51. Risk Decision Contract

Risk must return a structured decision rather than an unexplained Boolean.

Conceptually:

```python
@dataclass(frozen=True)
class RiskDecision:
    approved: bool

    reason: str

    signal_id: SignalId

    order_intent: OrderIntent | None

    risk_checks: Sequence[RiskCheckResult]
```

Each risk check should be observable.

Example:

```text
max_position       PASS
max_notional       PASS
daily_loss_limit   PASS
market_state       PASS
staleness          PASS
cooldown           PASS
──────────────────────────
FINAL              APPROVED
```

Or:

```text
max_position       FAIL
──────────────────────────
FINAL              REJECTED
```

Risk decisions must be explainable after the fact.

---

# 52. Risk Rule Interface

Individual risk rules should be modular.

Conceptually:

```python
class RiskRule(Protocol):

    @property
    def rule_id(self) -> str:
        ...

    def evaluate(
        self,
        context: RiskContext,
    ) -> RiskCheckResult:
        ...
```

Potential V1 rules include those defined by `006_RISK_EXECUTION`, such as:

```text
MaximumPositionRule
MaximumNotionalRule
MaximumDailyLossRule
MaximumDrawdownRule
MaximumOrderSizeRule
StaleDataRule
TradingHaltRule
ExposureRule
```

No rule should be silently bypassable.

---

# 53. Risk Context

Risk evaluation should operate against a coherent snapshot.

Conceptually:

```python
@dataclass(frozen=True)
class RiskContext:
    timestamp: Timestamp

    signal: Signal

    portfolio: PortfolioState
    account: AccountState

    market: MarketState

    system_state: SystemState
```

Risk evaluation should not independently query changing external state halfway through the decision.

The decision should be reproducible from its input snapshot.

---

# 54. Portfolio State Contract

Portfolio state is the canonical representation of the current internal portfolio.

Conceptually:

```python
@dataclass(frozen=True)
class PortfolioState:
    timestamp: Timestamp

    cash: Decimal
    equity: Decimal

    positions: Mapping[Symbol, Position]

    realized_pnl: Decimal
    unrealized_pnl: Decimal

    gross_exposure: Decimal
    net_exposure: Decimal
```

The precise accounting model follows `006_RISK_EXECUTION`.

Consumers should treat the object as immutable.

State transitions belong to the portfolio/accounting subsystem.

---

# 55. Position Schema

Conceptually:

```python
@dataclass(frozen=True)
class Position:
    symbol: Symbol

    quantity: Decimal
    average_entry_price: Decimal

    realized_pnl: Decimal
    unrealized_pnl: Decimal

    timestamp: Timestamp
```

Long/short conventions must be standardized across the repository.

The system must never have one component interpret positive quantity as long while another interprets it differently.

---

# 56. Order Lifecycle

Order state must be modeled explicitly.

The conceptual state machine is:

```text
                 ┌────────────┐
                 │   CREATED  │
                 └─────┬──────┘
                       │
                       ▼
                 ┌────────────┐
                 │ SUBMITTED  │
                 └─────┬──────┘
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
         ACCEPTED   REJECTED   CANCELLED
             │
             ▼
        PARTIALLY_FILLED
             │
             ▼
           FILLED
```

The actual broker may introduce additional states.

Invalid transitions must be rejected.

For example:

```text
FILLED → CREATED
```

is invalid.

---

# 57. Order Schema

The internal QuantOS order should be distinct from the broker order.

Conceptually:

```python
@dataclass(frozen=True)
class Order:
    order_id: OrderId

    intent_id: str
    symbol: Symbol

    side: Side
    quantity: Decimal

    order_type: OrderType
    limit_price: Decimal | None

    time_in_force: TimeInForce

    status: OrderStatus

    created_at: Timestamp
    updated_at: Timestamp
```

The broker identifier remains separate:

```text
QuantOS OrderId
       │
       ▼
BrokerOrderId
```

---

# 58. Broker Interface

The broker interface is the primary execution boundary.

Conceptually:

```python
class Broker(Protocol):

    def submit_order(
        self,
        order: Order,
    ) -> BrokerOrder:
        ...

    def cancel_order(
        self,
        broker_order_id: BrokerOrderId,
    ) -> None:
        ...

    def get_order(
        self,
        broker_order_id: BrokerOrderId,
    ) -> BrokerOrder:
        ...

    def get_open_orders(self) -> Sequence[BrokerOrder]:
        ...

    def get_positions(self) -> Sequence[Position]:
        ...

    def get_account(self) -> AccountState:
        ...
```

The strategy, feature, and risk layers must never depend directly on a specific broker SDK.

---

# 59. Broker Adapter Contract

A broker adapter translates between the QuantOS domain and an external broker.

```text
QuantOS Order
      │
      ▼
Broker-specific request
      │
      ▼
External API
      │
      ▼
Broker-specific response
      │
      ▼
Canonical BrokerOrder
```

Provider-specific errors must be mapped into QuantOS error types.

Examples include:

```text
ExchangeTimeout
ExchangeRateLimit
ExchangeRejectedOrder
AuthenticationFailure
InvalidOrder
InsufficientBalance
```

The remainder of the system should not need to understand provider-specific exception classes.

---

# 60. Fill Schema

A fill represents actual execution.

Conceptually:

```python
@dataclass(frozen=True)
class Fill:
    fill_id: FillId

    order_id: OrderId
    broker_order_id: BrokerOrderId

    symbol: Symbol
    side: Side

    quantity: Decimal
    price: Decimal

    fee: Decimal
    fee_currency: str

    event_time: Timestamp
    received_time: Timestamp
```

A fill is evidence that execution occurred.

These are not equivalent:

```text
Signal
OrderIntent
Order
Broker acknowledgement
Fill
```

Only the appropriate execution event should update realized portfolio state.

---

# 61. Reconciliation Contract

Live trading requires continuous reconciliation between:

```text
QuantOS internal state
```

and:

```text
Broker/exchange state
```

Conceptually:

```python
class Reconciler(Protocol):

    def reconcile(self) -> ReconciliationResult:
        ...
```

A reconciliation result should be able to identify:

```text
matched
internal_only
broker_only
quantity_mismatch
order_mismatch
cash_mismatch
```

Critical reconciliation failure must transition the system into a safe operational state.

---

# 62. Execution Engine Contract

The Execution Engine owns the order lifecycle.

Conceptually:

```python
class ExecutionEngine(Protocol):

    def submit(
        self,
        intent: OrderIntent,
    ) -> Order:
        ...

    def cancel(
        self,
        order_id: OrderId,
    ) -> None:
        ...

    def reconcile(self) -> ReconciliationResult:
        ...
```

The execution layer coordinates:

```text
Order creation
Broker submission
State transitions
Acknowledgements
Fills
Cancellation
Reconciliation
```

It does not generate alpha or override risk.

---

# 63. Backtest Execution Contract

Backtest execution should conform to the same conceptual execution boundary.

```text
ExecutionEngine
       │
       ├── LiveExecutionEngine
       │
       ├── PaperExecutionEngine
       │
       └── SimulatedExecutionEngine
```

This establishes the core parity principle:

```text
Strategy
   ↓
Risk
   ↓
Order Intent
```

remains the same regardless of execution environment.

The simulator may model:

* fees;
* slippage;
* latency;
* partial fills;
* order constraints;
* execution assumptions;

as required by `007_VALIDATION_BACKTESTING`.

---

# 64. Backtest Run Contract

Every backtest must be represented by an explicit configuration.

Conceptually:

```python
@dataclass(frozen=True)
class BacktestConfig:
    run_id: RunId

    strategy_id: StrategyId

    symbol: Symbol

    start: Timestamp
    end: Timestamp

    initial_capital: Decimal

    feature_version: str
    strategy_version: str
    risk_version: str

    execution_model_version: str

    seed: int | None
```

A backtest must be reproducible from its recorded inputs.

The run must identify:

```text
data version
feature version
strategy version
risk version
execution model
configuration
code revision
```

---

# 65. Backtest Result Contract

Backtest output must contain more than final PnL.

Conceptually:

```python
@dataclass(frozen=True)
class BacktestResult:
    run_id: RunId

    equity_curve: ...
    trades: ...
    fills: ...

    total_return: Decimal
    max_drawdown: Decimal

    sharpe: Decimal | None
    sortino: Decimal | None

    turnover: Decimal
    fees: Decimal
    slippage: Decimal

    metrics: Mapping[str, Decimal]
```

The exact metrics remain governed by `007`.

The implementation requirement is that the complete result is reproducible and auditable.

---

# 66. Validation Gate Contract

Validation should produce machine-readable promotion gates.

Conceptually:

```python
@dataclass(frozen=True)
class ValidationGate:
    gate_id: str

    passed: bool

    metric: str | None
    actual: Decimal | None
    threshold: Decimal | None

    reason: str
```

The system should be able to determine:

```text
PASS
```

or:

```text
FAIL
```

without requiring an engineer to interpret a notebook manually.

---

# 67. Strategy Lifecycle Contract

Strategies should progress through explicit states.

Conceptually:

```text
RESEARCH
   ↓
BACKTESTED
   ↓
VALIDATED
   ↓
PAPER_APPROVED
   ↓
LIVE_APPROVED
   ↓
PRODUCTION
   ↓
SUSPENDED / RETIRED
```

The implementation must not permit an uncontrolled transition such as:

```text
RESEARCH → PRODUCTION
```

without satisfying the required validation and approval gates.

---

# 68. Production Strategy Manifest

Production must explicitly identify what is running.

Conceptually:

```yaml
strategy:
  id: strategy_v1
  version: "1.0.0"

features:
  version: "feature_set_v1"

risk:
  version: "risk_v1"

execution:
  version: "execution_v1"
```

The production runtime must expose this information through status and health interfaces.

An operator must always be able to answer:

> Which exact strategy, feature set, risk configuration, and execution configuration is currently active?

---

# 69. System Runtime State

The application should maintain an explicit runtime state.

Recommended states:

```text
STARTING
INITIALIZING
SYNCING
READY
TRADING
DEGRADED
HALTED
SHUTTING_DOWN
FAILED
```

Normal startup:

```text
STARTING
   ↓
INITIALIZING
   ↓
SYNCING
   ↓
READY
   ↓
TRADING
```

Potential failure paths include:

```text
TRADING → DEGRADED
TRADING → HALTED
READY   → FAILED
```

The system must never enter `TRADING` before all mandatory preconditions are satisfied.

---

# 70. Trading Preconditions

Before live trading is enabled, QuantOS must verify the relevant conditions from `006_RISK_EXECUTION`.

At minimum:

```text
Configuration valid
Market data connected
Market data fresh
Required features available
Strategy loaded
Risk configuration valid
Account synchronized
Positions reconciled
Broker connectivity healthy
Clock healthy
Persistence healthy
Risk limits loaded
Kill switch inactive
```

Only after successful validation may the runtime transition:

```text
READY → TRADING
```

---

# 71. Configuration Schema

Configuration must be strongly typed and validated.

Conceptually:

```yaml
environment: production

market:
  symbols:
    - BTCUSDT
  timeframe: 1m

strategy:
  id: strategy_v1
  version: 1.0.0

risk:
  max_position: ...
  max_notional: ...
  max_daily_loss: ...

execution:
  mode: live
  order_timeout_seconds: ...

storage:
  backend: ...

observability:
  log_level: INFO
```

The exact parameters must come from the preceding specifications.

The implementation must not invent additional strategy or risk behavior simply because the configuration framework supports arbitrary fields.

---

# 72. Configuration Validation

Configuration must be validated before application startup.

Examples:

```text
negative maximum position
→ reject

production mode without broker credentials
→ reject

invalid symbol
→ reject

missing strategy
→ reject

missing risk limit
→ reject

invalid execution mode
→ reject
```

The system should fail during initialization rather than discovering configuration errors after trading begins.

---

# 73. Environment Isolation

V1 should distinguish at minimum:

```text
development
backtest
paper
production
```

Production activation must be explicit.

A conceptual two-step activation is:

```text
ENVIRONMENT=production
TRADING_ENABLED=true
```

The exact mechanism may differ, but the design should require deliberate activation rather than allowing production trading by accidental configuration.

---

# 74. Secret Management

Credentials must be injected at runtime.

Permitted mechanisms include:

```text
Environment variables
Secret manager
Encrypted deployment configuration
```

Forbidden:

```text
Source code
Git repository
Notebook
Committed JSON configuration
Hardcoded credentials
```

`.env.example` may contain placeholders.

Actual secret-bearing files must be excluded from version control.

---

# 75. Event Contract

Important state changes should use a common event envelope.

Conceptually:

```python
@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str

    event_time: Timestamp
    produced_at: Timestamp

    source: str
    version: str

    payload: object
```

Examples include:

```text
MarketDataReceived
FeatureSetComputed
SignalGenerated
RiskDecisionMade
OrderSubmitted
OrderUpdated
FillReceived
PositionChanged
RiskLimitBreached
SystemStateChanged
```

Events that become persistent or externally consumed must be versioned.

---

# 76. Event Ordering

The system must not assume that network arrival order equals market-event order.

Where sequence numbers exist, preserve them.

Where ordering cannot be confidently established, the system must not fabricate an ordering.

Potentially inconsistent state should trigger appropriate quality or reconciliation behavior.

---

# 77. Idempotency

External execution is vulnerable to duplicate requests.

Every order action must therefore have explicit identity.

At minimum, maintain:

```text
intent_id
order_id
client_order_id
```

Where supported, the broker adapter should use a deterministic client order identifier.

Consider:

```text
request sent
     ↓
network timeout
     ↓
unknown whether broker accepted
```

The system must **not blindly submit a second order**.

It must first query or reconcile the broker.

This is a mandatory live-trading safety principle.

---

# 78. Retry Policy

Retries must be classified by operation.

Generally safe-to-retry operations include:

```text
Read market state
Read order status
Read account state
Fetch historical data
```

Potentially dangerous operations include:

```text
Submit order
Cancel order
Modify order
```

Order mutations require idempotency-aware handling.

The core rule is:

> Never retry an order mutation unless the system can determine that the retry cannot create an unintended duplicate action.

---

# 79. Error Model

Errors should be categorized rather than handled as generic exceptions.

The hierarchy should distinguish at minimum:

```text
DataError
FeatureError
AlphaError
RiskError
ExecutionError
BrokerError
StorageError
ConfigurationError
ValidationError
SystemError
```

Errors should also be classified operationally as:

```text
Recoverable
Non-recoverable
Safety-critical
```

Examples of recoverable failures:

```text
temporary provider disconnect
temporary broker timeout
delayed market-data message
```

Examples of safety-critical failures:

```text
invalid risk configuration
unreconciled portfolio state
unknown execution result
corrupted persisted state
```

The system must not blindly retry all exceptions.

---

# 80. Fail-Closed Contract

The following conditions should default to **no new trading activity**:

```text
Unknown portfolio state
Unknown broker state
Invalid market data
Stale market data
Failed risk check
Missing required feature
Invalid configuration
Clock failure
Unreconciled position
Exceeded risk limit
Execution inconsistency
Critical subsystem unhealthy
```

The desired behavior is:

```text
UNKNOWN
   ↓
SAFE STATE
   ↓
NO NEW ORDERS
```

This is an implementation requirement inherited directly from the risk and execution architecture.

---

# 81. Research / Production Boundary

Research code must not become an accidental production dependency.

Allowed:

```text
Notebook
   ↓
QuantOS library
```

Forbidden:

```text
Production package
   ↓
Notebook
```

Similarly, research experiments must not silently mutate production state.

Research produces artifacts.

Production consumes validated and versioned artifacts.

---

# 82. Strategy Boundary

The strategy must remain replaceable.

Conceptually:

```python
class AlphaStrategy(Protocol):

    def generate_signal(
        self,
        market_state: MarketState,
        features: FeatureSet,
        portfolio: PortfolioState,
    ) -> Signal:
        ...
```

The implementation may later be:

```text
Rule-based
Statistical
Machine Learning
Hybrid
```

without changing the surrounding risk and execution architecture.

V1 should exploit this flexibility without prematurely introducing unnecessary model complexity.

---

# 83. Live / Paper / Backtest Parity

The same conceptual trading pipeline must operate in all environments:

```text
                  Strategy Core
                       │
                Feature / Alpha
                       │
                       ▼
                     Risk
                       │
                       ▼
                  Order Intent
                       │
             ┌─────────┼─────────┐
             │         │         │
          Backtest    Paper     Live
             │         │         │
        Simulator   Paper API  Broker API
```

Strategy logic must not become a collection of environment-specific branches.

Avoid:

```python
if live:
    ...
elif backtest:
    ...
```

for core trading decisions.

Mode-specific behavior belongs at infrastructure boundaries.

---

# 84. Decision Snapshot

Every trading decision should operate against a coherent snapshot.

Conceptually:

```python
@dataclass(frozen=True)
class DecisionSnapshot:
    timestamp: Timestamp

    market: MarketState
    features: FeatureSet

    portfolio: PortfolioState
    account: AccountState

    system: SystemState
```

The strategy and risk engine should consume this snapshot rather than independently querying mutable external state.

This reduces race conditions and improves reproducibility.

---

# 85. Serialization Contract

Domain objects must have explicit serialization rules.

Serialization is required for:

```text
Persistence
Logging
Debugging
Replay
Backtest results
Event storage
Audit
```

The serialized representation must be deterministic.

Conceptually:

```text
Domain Object
      │
      ▼
Canonical Schema
      │
      ├── JSON
      ├── Database
      └── Event Log
```

Schema changes must be deliberate.

Persisted schemas should be versioned when compatibility matters.

---

# 86. Versioning Contract

QuantOS must distinguish at minimum:

```text
Code Version
Data Version
Feature Version
Strategy Version
Risk Version
Execution Model Version
Configuration Version
```

A production run must expose these versions.

This allows historical decisions to be traced back to the exact implementation and configuration that generated them.

---

# 87. Audit Chain

Every production trade should be reconstructible through a causal chain:

```text
Market Event
     ↓
Feature Set
     ↓
Signal
     ↓
Risk Decision
     ↓
Order Intent
     ↓
Order
     ↓
Broker Order
     ↓
Fill
     ↓
Portfolio Update
```

Each stage should carry identifiers that allow the chain to be reconstructed.

For any live trade, the operator should be able to answer:

```text
What happened?
When?
Why?
Which strategy?
Which features?
Which configuration?
Which risk decision?
Which order?
Which broker response?
Which fill?
```

---

# 88. Determinism Contract

Where deterministic behavior is possible, it must be enforced.

Given:

```text
same data
same configuration
same strategy version
same feature version
same seed
same execution model
```

the backtest should produce the same result.

Randomized components must receive explicit seeds.

For ML-based components, record:

```text
seed
model version
training dataset version
hyperparameters
```

alongside the resulting artifact.

---

# 89. Interface Stability

Interfaces should remain intentionally small.

Implementation details may evolve behind them.

For example, the broker implementation may move from:

```text
REST
```

to:

```text
REST + WebSocket
```

without changing:

```text
Alpha
Risk
Portfolio
```

Likewise, storage and data-provider implementations may change without forcing domain-level changes.

---

# 90. No Hidden Side Effects

Pure computational components should remain as pure as practical.

For example:

```text
Feature.compute()
Alpha.generate_signal()
RiskRule.evaluate()
```

must not secretly:

* write files;
* place orders;
* mutate global state;
* send notifications;
* modify configuration.

Side effects belong at explicit application and infrastructure boundaries.

This substantially improves testability and reproducibility.

---

# 91. Runtime Pipeline Contract

The central QuantOS runtime pipeline is:

```text
                MARKET EVENT
                     │
                     ▼
              DATA VALIDATION
                     │
                     ▼
              FEATURE ENGINE
                     │
                     ▼
                ALPHA ENGINE
                     │
                     ▼
                RISK ENGINE
                     │
             ┌───────┴────────┐
             │                │
          REJECT            APPROVE
             │                │
             │                ▼
             │          ORDER INTENT
             │                │
             │                ▼
             │           EXECUTION
             │                │
             │                ▼
             │             BROKER
             │                │
             │                ▼
             │              FILL
             │                │
             └────────┬───────┘
                      ▼
                PORTFOLIO STATE
                      │
                      ▼
                OBSERVABILITY
```

This pipeline is the operational heart of QuantOS V1.

Each arrow represents a deliberate contract.

No subsystem should bypass the established layers merely for convenience.

---

# 92. Backtest / Live Contract

The following should remain common across environments:

```text
Feature definitions
Feature computation
Strategy
Risk rules
Portfolio accounting concepts
Signal schema
Order schema
Risk decision schema
```

The following may differ:

```text
Data source
Clock
Execution implementation
Broker implementation
Persistence backend
Observability transport
```

Therefore:

```text
                 COMMON CORE
                      │
        ┌─────────────┼─────────────┐
        │             │             │
    Backtest        Paper         Live
        │             │             │
   Historical       Simulated     Broker
     Clock         Execution     Execution
```

This is the intended V1 architecture.

---

# 93. Contract Testing

Every major interface should have a contract-test suite.

For example, every broker adapter must satisfy the same contract:

```text
submit_order
cancel_order
get_order
get_open_orders
get_positions
get_account
```

A new broker adapter should be required to pass the same broker contract tests.

The same principle applies to:

```text
PaperBroker
LiveBroker
HistoricalDataProvider
LiveDataProvider
SimulatedExecutionEngine
LiveExecutionEngine
```

where equivalent semantics are applicable.

---

# 94. Schema Testing

All important schemas must be tested for:

```text
Required fields
Type correctness
Serialization
Deserialization
Boundary values
Invalid values
Version compatibility
```

Schema changes must not silently invalidate historical artifacts.

---

# 95. Runtime Safety Contract

The following rules are mandatory:

```text
No valid risk decision
        ↓
No order

No valid broker state
        ↓
No new order

No valid market state
        ↓
No new order

Reconciliation failure
        ↓
Trading halt / degraded state

Critical configuration failure
        ↓
No startup

Unknown execution outcome
        ↓
Reconcile before mutation
```

These are implementation-level safety guarantees.

---

# 96. Final Contract Chain

The resulting QuantOS V1 contract chain is:

```text
Market Data Provider
        │
        ▼
MarketData
        │
        ▼
Feature Engine
        │
        ▼
FeatureSet
        │
        ▼
Alpha Strategy
        │
        ▼
Signal
        │
        ▼
Risk Engine
        │
        ▼
RiskDecision
        │
        ▼
OrderIntent
        │
        ▼
Execution Engine
        │
        ▼
Order
        │
        ▼
Broker Adapter
        │
        ▼
BrokerOrder
        │
        ▼
Fill
        │
        ▼
Portfolio
        │
        ▼
Audit / Observability
```

Every boundary is explicit.

Every state transition is owned.

Every production order has a causal chain.

Every external mutation is isolated behind an adapter.

---

# 97. Contract-Layer Completion Criteria

The contract layer is complete when the repository has:

* canonical domain types;
* canonical timestamp semantics;
* explicit clock abstraction;
* market-data contracts;
* data-quality contracts;
* feature contracts;
* feature registry;
* alpha strategy contract;
* signal schema;
* risk decision schema;
* risk rule interface;
* portfolio state schema;
* position schema;
* order-intent schema;
* order lifecycle;
* order schema;
* fill schema;
* broker interface;
* broker adapter boundary;
* execution interface;
* reconciliation contract;
* backtest configuration;
* backtest result contract;
* validation gate contract;
* strategy lifecycle;
* production strategy manifest;
* runtime state machine;
* configuration schema;
* event envelope;
* idempotency rules;
* retry rules;
* error model;
* fail-closed behavior;
* serialization rules;
* versioning rules;
* audit chain;
* deterministic execution requirements;
* contract-testing requirements;
* backtest/live parity rules.

At this point, the QuantOS repository has moved from a **software skeleton** to a system with an explicit internal language.

The remaining question is no longer:

> What should these components mean?

It becomes:

> How do we build, test, integrate, and continuously verify them?

That is the purpose of Part 3.

# 98. Purpose of the Engineering & Verification Layer

Parts 1 and 2 established:

```text
Part 1
Repository structure
Subsystem boundaries
Dependency direction

Part 2
Interfaces
Schemas
State models
Runtime contracts
```

Part 3 defines how those contracts become a **working, continuously verified software system**.

The objective is to prevent QuantOS from reaching a state where:

* code exists but contracts are violated;
* backtests pass but live behavior diverges;
* individual components work but integration fails;
* risk controls work in isolation but can be bypassed;
* data quality problems contaminate research;
* execution logic behaves differently in paper and production;
* a successful experiment cannot be reproduced;
* deployment introduces untested behavior.

The engineering process must therefore make correctness progressively harder to break.

---

# 99. Engineering Principle — Build in Dependency Order

QuantOS must not be implemented as one large feature.

Implementation should follow dependency order:

```text
Core
  ↓
Data
  ↓
Features
  ↓
Portfolio
  ↓
Alpha
  ↓
Risk
  ↓
Execution
  ↓
Backtest
  ↓
Paper Trading
  ↓
Live Trading
```

This order matters.

For example, Alpha should not be finalized before the Feature contracts exist.

Risk should not be finalized before Portfolio state is reliable.

Live execution should not begin before reconciliation and paper execution are proven.

The implementation process must therefore follow the architecture rather than development convenience.

---

# 100. Development Phases

V1 implementation should proceed through the following phases:

```text
Phase 0 — Repository Bootstrap
        ↓
Phase 1 — Core Domain
        ↓
Phase 2 — Data Layer
        ↓
Phase 3 — Feature Engine
        ↓
Phase 4 — Portfolio & Accounting
        ↓
Phase 5 — Alpha Engine
        ↓
Phase 6 — Risk Engine
        ↓
Phase 7 — Execution & Broker
        ↓
Phase 8 — Backtesting
        ↓
Phase 9 — Validation
        ↓
Phase 10 — Paper Trading
        ↓
Phase 11 — Live Readiness
        ↓
Phase 12 — Controlled Production
```

No later phase should be considered complete merely because its code compiles.

Each phase has explicit acceptance criteria.

---

# 101. Phase 0 — Repository Bootstrap

The first implementation phase creates the development environment.

Required:

```text
pyproject.toml
source package
test package
configuration structure
logging foundation
CI configuration
pre-commit configuration
README
environment example
Makefile / task runner
```

The repository must support:

```text
install
lint
format
type-check
test
build
```

from a clean environment.

### Acceptance Criteria

A new engineer should be able to clone the repository and reach a passing baseline without manually configuring undocumented dependencies.

---

# 102. Phase 1 — Core Domain

Implement the foundational domain types:

```text
Symbol
Timestamp
Price
Quantity
Side
OrderType
OrderStatus
Position
MarketData
Signal
OrderIntent
Order
Fill
AccountState
PortfolioState
```

Also implement:

```text
domain errors
identifiers
Clock interface
basic event model
```

These objects should have minimal dependencies.

### Tests

Every domain object should have:

* construction tests;
* validation tests;
* serialization tests where applicable;
* equality tests;
* boundary tests.

### Acceptance Criteria

Core types can be imported without loading:

```text
broker SDKs
database drivers
network clients
```

---

# 103. Phase 2 — Data Layer

Implement:

```text
MarketDataProvider
HistoricalDataProvider
MarketDataRepository
Normalization
Quality validation
Data windows
Clock integration
```

The first provider should be the simplest reliable provider required by V1.

Do not build a provider abstraction for ten exchanges before one provider works correctly.

The abstraction exists to preserve the boundary, not to encourage unnecessary infrastructure.

### Required Tests

```text
timestamp tests
OHLC validity
duplicate detection
missing data
staleness
symbol normalization
provider failures
ordering
serialization
historical replay
```

### Acceptance Criteria

QuantOS can load a known historical dataset and produce a deterministic canonical market-data stream.

---

# 104. Phase 3 — Feature Engine

Implement:

```text
Feature
FeatureContext
FeatureSet
FeatureRegistry
FeatureEngine
feature validation
```

Start with only the features required by the V1 strategy.

Do not build a general-purpose technical-analysis library unless the V1 strategy actually requires it.

Each feature must have:

```text
definition
inputs
lookback
output
version
tests
```

### Required Tests

For every feature:

```text
normal values
edge values
missing inputs
insufficient history
timestamp alignment
look-ahead prevention
determinism
```

### Acceptance Criteria

Given the same market-data window and configuration:

```text
Feature Engine(input)
=
same FeatureSet
```

every time.

---

# 105. Phase 4 — Portfolio & Accounting

Implement:

```text
Position
PortfolioState
AccountState
PnL calculation
Exposure calculation
Fee accounting
Fill application
```

Portfolio accounting must be driven by explicit events.

Conceptually:

```text
Fill
  ↓
Portfolio Accounting
  ↓
Position Update
  ↓
Portfolio State
```

The system must not update a position simply because an order was submitted.

An order is an intention.

A fill is execution.

### Required Tests

Test:

```text
opening position
adding to position
reducing position
closing position
reversing position
fees
realized PnL
unrealized PnL
long positions
short positions
partial fills
multiple fills
```

### Acceptance Criteria

Portfolio accounting must be deterministic and independently testable without a broker.

---

# 106. Phase 5 — Alpha Engine

Implement:

```text
AlphaStrategy
StrategyContext
Signal
AlphaEngine
StrategyRegistry
Strategy lifecycle
```

Only implement the V1 strategy initially.

The strategy must consume:

```text
MarketState
FeatureSet
PortfolioState
```

and produce:

```text
Signal
```

It must not:

```text
place orders
change portfolio state
bypass risk
query broker APIs
```

### Required Tests

Test:

```text
signal generation
signal direction
signal strength
threshold behavior
position-aware behavior
missing feature behavior
boundary conditions
determinism
```

### Acceptance Criteria

A strategy can run against historical data without any broker dependency.

---

# 107. Phase 6 — Risk Engine

Implement the risk contract before live execution.

Components:

```text
RiskContext
RiskRule
RiskCheckResult
RiskDecision
RiskEngine
Risk state
Trading halt state
```

Implement the mandatory V1 risk rules from `006`.

The engine must evaluate every proposed executable intent.

Conceptually:

```text
Signal
  ↓
RiskContext
  ↓
Risk Rules
  ↓
RiskDecision
```

### Required Tests

Each risk rule must have:

```text
pass tests
fail tests
boundary tests
interaction tests
```

Also test:

```text
multiple simultaneous failures
risk state changes
daily loss limits
position limits
exposure limits
stale data
trading halt
missing portfolio state
```

### Critical Acceptance Criterion

It must be impossible for a rejected signal to reach the execution layer through the normal application path.

---

# 108. Phase 7 — Execution & Broker

Implement:

```text
OrderIntent
Order
BrokerOrder
Fill
Broker interface
PaperBroker
ExecutionEngine
Order lifecycle
Reconciliation
```

The first execution implementation should be **paper/simulated**, not live.

The system must prove the complete order lifecycle before connecting real capital.

### Required Tests

Test:

```text
order creation
submission
acknowledgement
rejection
partial fill
full fill
cancellation
unknown broker response
timeout
duplicate submission
reconciliation
```

### Critical Failure Scenario

Simulate:

```text
submit order
    ↓
network timeout
    ↓
broker may have accepted order
```

The system must demonstrate that it reconciles before submitting another order.

---

# 109. Phase 8 — Backtesting Engine

The Backtest Engine should now connect the already-tested components.

Pipeline:

```text
Historical Data
      ↓
Feature Engine
      ↓
Alpha Engine
      ↓
Risk Engine
      ↓
Simulated Execution
      ↓
Portfolio
      ↓
Performance
```

The backtest engine should not reimplement strategy logic.

It orchestrates existing components.

This is critical.

Bad architecture:

```text
Live Strategy
    ≠
Backtest Strategy
```

Required architecture:

```text
Same Strategy
     │
     ├── Backtest execution
     └── Live execution
```

---

# 110. Backtest Regression Tests

Every production strategy should have deterministic regression tests.

A known dataset should produce a known result envelope.

For example:

```text
dataset: fixture_v1
strategy: strategy_v1
configuration: config_v1

expected:
trade_count
return
max_drawdown
fees
turnover
```

Exact numerical tolerances should be defined where floating-point or execution simulation makes exact equality inappropriate.

The purpose is to detect accidental behavioral changes.

If a developer changes:

```text
feature calculation
risk logic
execution assumptions
portfolio accounting
```

the regression suite should reveal whether strategy behavior changed.

---

# 111. Phase 9 — Validation

Backtesting does not automatically mean validation.

The Validation subsystem must execute the gates defined by `007`.

Validation should cover:

```text
Data integrity
Leakage
Temporal correctness
Out-of-sample performance
Cost sensitivity
Robustness
Parameter sensitivity
Drawdown
Risk characteristics
Statistical significance where applicable
```

The exact thresholds must come from `007`.

The implementation must not weaken them merely because the strategy performs poorly.

---

# 112. Look-Ahead / Leakage Testing

Leakage testing must be treated as a first-class test category.

Potential sources include:

```text
future candles
future labels
future normalization statistics
future feature values
future portfolio state
survivorship bias
timestamp misalignment
revised data
```

A useful test pattern is:

```text
Run strategy with information available at T
```

and verify that changing information strictly after `T` does not alter the decision at `T`.

This should be automated wherever possible.

---

# 113. Data Mutation Tests

The validation suite should include deliberate data corruption.

Examples:

```text
remove candle
duplicate candle
shift timestamp
alter future candle
introduce NaN
introduce impossible OHLC
change volume
```

The system should either:

```text
detect and reject
```

or produce an explicitly different result.

Silent acceptance is unacceptable.

---

# 114. Risk Mutation Tests

Risk controls should also be tested through deliberate failure injection.

Examples:

```text
exceed max position
exceed max notional
exceed daily loss
stale data
missing account
invalid position
broker mismatch
trading halt active
```

Expected behavior:

```text
NO NEW ORDER
```

These tests are especially important because risk failures are potentially more dangerous than strategy failures.

---

# 115. Execution Mutation Tests

Execution should be tested against adverse conditions.

Examples:

```text
broker timeout
broker rejection
duplicate acknowledgement
missing acknowledgement
partial fill
late fill
cancel failure
connection loss
reconnect
unknown order state
```

The expected system behavior must be explicitly defined.

The system should never assume:

```text
timeout = order failed
```

A timeout may mean:

```text
order status unknown
```

and therefore require reconciliation.

---

# 116. Testing Pyramid

QuantOS should use a layered testing pyramid.

```text
                 ┌───────────────┐
                 │   Live / E2E  │
                 └───────▲───────┘
                         │
                ┌────────┴────────┐
                │ Integration     │
                └────────▲────────┘
                         │
                ┌────────┴────────┐
                │ Contract Tests  │
                └────────▲────────┘
                         │
                ┌────────┴────────┐
                │   Unit Tests    │
                └─────────────────┘
```

The majority of tests should be unit tests.

Integration tests validate component interactions.

Contract tests validate interchangeable implementations.

End-to-end tests validate the complete runtime.

Live tests should be extremely limited and controlled.

---

# 117. Unit Tests

Unit tests should cover pure and mostly deterministic logic:

```text
domain types
indicators
features
strategy rules
risk rules
portfolio accounting
serialization
configuration validation
state transitions
```

Unit tests should not require:

```text
network
real broker
real database
real market
```

unless explicitly categorized otherwise.

---

# 118. Integration Tests

Integration tests verify component combinations.

Examples:

```text
data → feature
feature → alpha
alpha → risk
risk → execution
execution → portfolio
```

A complete simulated cycle should be tested:

```text
Market Event
   ↓
Feature
   ↓
Signal
   ↓
Risk
   ↓
Order
   ↓
Fill
   ↓
Portfolio
```

This is the minimum meaningful end-to-end internal test.

---

# 119. Contract Tests

Contract tests ensure interchangeable implementations behave according to the same interface.

Examples:

```text
Paper Broker
Live Broker

Historical Data Provider
Live Data Provider

Simulated Execution
Paper Execution
Live Execution
```

A new adapter should not be considered complete until it passes the relevant contract suite.

---

# 120. End-to-End Tests

End-to-end tests should exercise the application runtime.

Example:

```text
start application
      ↓
load configuration
      ↓
connect data
      ↓
initialize strategy
      ↓
initialize risk
      ↓
receive market event
      ↓
generate signal
      ↓
approve/reject risk
      ↓
execute simulated order
      ↓
receive fill
      ↓
update portfolio
      ↓
emit audit events
```

These tests should run against deterministic fixtures.

---

# 121. Regression Test Suite

QuantOS must maintain a permanent regression suite containing:

```text
known market-data fixtures
known feature outputs
known strategy outputs
known risk decisions
known backtest results
known portfolio calculations
known execution state transitions
```

Regression tests protect against accidental behavioral drift.

A refactor is not considered behavior-preserving simply because the code compiles.

---

# 122. Golden Dataset

The repository should maintain a small canonical dataset for deterministic testing.

It should be:

```text
small
versioned
reproducible
known-good
license-safe
```

It should exercise:

```text
normal market behavior
signal generation
position changes
risk rejection
fills
fees
```

The golden dataset should be small enough that the full test suite can execute quickly.

---

# 123. Test Fixtures

Fixtures should exist for:

```text
MarketData
FeatureSet
Signal
PortfolioState
AccountState
RiskContext
OrderIntent
Order
BrokerOrder
Fill
```

Fixtures must avoid hidden mutable global state.

Prefer factory functions or immutable fixture objects.

---

# 124. Property-Based Testing

Where useful, property-based tests should verify invariants.

Examples:

```text
high >= low
quantity >= 0
portfolio accounting conserves value appropriately
filled quantity <= ordered quantity
risk never approves an explicitly forbidden exposure
```

Property-based testing is especially valuable for:

```text
portfolio accounting
order state transitions
data validation
position calculations
```

It should complement, not replace, explicit scenario tests.

---

# 125. Static Analysis

The repository should use static analysis for:

```text
formatting
linting
type checking
unused imports
dead code where detectable
dependency violations
```

Recommended tooling should remain lightweight.

The exact toolchain should be selected during implementation, but the CI contract is:

```text
format check
lint check
type check
```

must pass before merge.

---

# 126. Type Checking

Production interfaces should be strongly typed.

Type checking is especially important for:

```text
domain objects
repository interfaces
broker interfaces
feature interfaces
strategy interfaces
risk interfaces
execution interfaces
configuration models
```

Avoid using `Any` as an escape hatch throughout the system.

If a boundary genuinely has dynamic data, isolate that dynamic representation at the boundary and normalize it immediately.

---

# 127. Formatting and Linting

Formatting should be automated.

Developers should not manually debate formatting in code review.

The repository should provide a single command such as:

```text id="ub5s6m"
make format
```

and a corresponding CI check:

```text id="h2ml6u"
make format-check
```

The same principle applies to linting.

---

# 128. Architecture Checks

The repository should eventually enforce important architectural rules automatically.

Examples:

```text
alpha cannot import broker
features cannot import execution
core cannot import infrastructure
production code cannot import notebooks
risk cannot be bypassed by application code
```

These may be enforced through:

* static dependency checks;
* import rules;
* custom tests;
* CI scripts.

Architectural rules should not exist only in documentation.

---

# 129. CI Pipeline

Every pull request should run a minimum pipeline:

```text
Checkout
   ↓
Install dependencies
   ↓
Format check
   ↓
Lint
   ↓
Type check
   ↓
Unit tests
   ↓
Contract tests
   ↓
Integration tests
   ↓
Regression tests
   ↓
Build/package validation
```

The merge should be blocked if required checks fail.

---

# 130. CI Test Levels

CI should use multiple execution levels.

### Fast CI

Run on every change:

```text
format
lint
type check
unit tests
```

### Full CI

Run on pull requests:

```text
unit
contract
integration
regression
architecture
```

### Extended Validation

Run on relevant strategy/data changes:

```text
full backtest suite
leakage checks
validation suite
performance regression
```

### Release Validation

Before production deployment:

```text
complete test suite
validation gates
paper-trading checks
deployment checks
configuration checks
```

---

# 131. CI Must Not Depend on Live Markets

Normal CI must never depend on:

```text
live exchange availability
live broker credentials
real market conditions
```

Tests must use deterministic fixtures and mocks/simulators.

Live connectivity tests belong in a controlled deployment validation stage.

---

# 132. Secrets in CI

CI must never print secrets.

Production credentials should only be available to jobs that genuinely require them.

Normal unit/integration CI should run without broker credentials.

This reduces the blast radius of CI compromise.

---

# 133. Pull Request Requirements

A production code change should include:

```text
code
tests
documentation update where required
configuration update where required
migration where required
```

The PR should identify:

```text
what changed
why it changed
which specification it affects
what tests prove correctness
whether strategy behavior changed
whether risk behavior changed
whether execution behavior changed
```

---

# 134. Definition of Done — Code

A feature is not done because the implementation exists.

It is done when:

```text
Implementation complete
Tests complete
Types valid
Lint clean
Architecture valid
Documentation updated
Configuration validated
Regression suite passing
```

For trading-critical functionality, the definition of done is stricter.

---

# 135. Definition of Done — Trading Logic

A change to:

```text
features
alpha
risk
portfolio
execution
```

must additionally demonstrate:

```text
deterministic behavior
historical behavior understood
regression behavior understood
risk implications understood
backtest impact measured
```

A strategy change that alters PnL but was merged without reviewing the changed backtest is not complete.

---

# 136. Definition of Done — Risk

Risk changes require dedicated evidence.

At minimum:

```text
unit tests
boundary tests
failure tests
integration tests
mutation tests
```

The engineer must demonstrate:

```text
approved trades remain executable
forbidden trades remain blocked
```

Risk code should receive a higher review standard than ordinary utility code.

---

# 137. Definition of Done — Execution

Execution changes require:

```text
order lifecycle tests
broker contract tests
failure injection
idempotency tests
reconciliation tests
paper execution validation
```

Live broker changes must not be considered production-ready solely because a single test order succeeded.

---

# 138. Development Workflow

The recommended workflow is:

```text
Read specification
      ↓
Define/modify contract
      ↓
Write tests
      ↓
Implement
      ↓
Run local checks
      ↓
Run regression suite
      ↓
Review behavior
      ↓
Commit
      ↓
CI
      ↓
Merge
```

The specifications should be treated as engineering requirements, not optional documentation.

---

# 139. Commit Discipline

Commits should remain understandable.

Prefer:

```text
add canonical market data schema
add feature registry
implement momentum feature
add portfolio accounting
add max position risk rule
implement paper broker
add order reconciliation
```

Avoid giant commits such as:

```text
build quantos
```

Small, coherent commits make debugging and rollback significantly easier.

---

# 140. Implementation Order Within a Component

For each subsystem, follow:

```text
1. Contract
2. Domain model
3. Unit tests
4. Implementation
5. Integration tests
6. Observability
7. Documentation
```

This prevents implementation details from defining the architecture accidentally.

---

# 141. Minimum Vertical Slice

Before implementing the entire platform, QuantOS should prove one complete vertical slice.

The first meaningful milestone is:

```text
Market Data
    ↓
Feature
    ↓
Alpha
    ↓
Risk
    ↓
Order Intent
    ↓
Paper Execution
    ↓
Fill
    ↓
Portfolio
```

This slice should work end-to-end before the system expands horizontally.

This is preferable to building:

```text
20 features
10 strategies
5 broker adapters
```

before a single strategy can complete the entire lifecycle.

---

# 142. Vertical Slice Acceptance Test

The first vertical slice should be able to execute a deterministic scenario such as:

```text
1. Load market fixture.
2. Compute feature set.
3. Generate signal.
4. Evaluate risk.
5. Approve trade.
6. Create order intent.
7. Submit simulated order.
8. Generate fill.
9. Update portfolio.
10. Record complete audit chain.
```

The expected result should be known beforehand.

This becomes the foundational integration test for the system.

---

# 143. Strategy Expansion Rule

After the first V1 strategy is working, additional strategies should be added only if the architecture requires them.

The system should not expand the number of strategies merely to demonstrate flexibility.

The purpose of abstraction is:

```text
one strategy can be replaced
```

not:

```text
many strategies must exist
```

V1 should optimize for reliability rather than strategy count.

---

# 144. Paper Trading Gate

Before production, the exact production pipeline should run in paper mode.

Paper trading must validate:

```text
real-time data
feature computation
signal generation
risk decisions
order lifecycle
reconciliation
portfolio state
logging
monitoring
restart behavior
```

Paper trading should not be a fake separate application.

It should use the same application runtime with a different execution adapter.

---

# 145. Paper Trading Acceptance Criteria

Paper trading should demonstrate:

```text
stable runtime
correct market-data handling
no unexplained signals
correct risk behavior
correct order lifecycle
correct portfolio accounting
successful reconciliation
clean restart
no state corruption
```

The required duration and quantitative gates should come from the validation and deployment requirements established in the preceding specifications.

---

# 146. Restart Testing

A trading system must be tested under restart.

Test:

```text
normal shutdown
unexpected process termination
network interruption
broker disconnect
machine restart
```

After restart, the system should:

```text
load persisted state
connect to broker
query authoritative state
reconcile
restore safe runtime state
```

It must not blindly assume that its pre-crash memory represents reality.

---

# 147. Failure Injection

Before production, deliberately simulate:

```text
market data outage
broker outage
database outage
network timeout
stale data
invalid data
process crash
partial fill
duplicate fill
unknown order status
risk service failure
```

The purpose is not to prove that failures never happen.

The purpose is to prove that failure produces a controlled response.

---

# 148. Operational Readiness Tests

Before live deployment, verify:

```text
startup
shutdown
restart
configuration loading
secret loading
market-data connection
broker connection
state synchronization
reconciliation
logging
metrics
alerts
kill switch
risk limits
```

These are part of the software acceptance criteria.

---

# 149. Performance Testing

V1 does not require extreme low-latency optimization unless the actual strategy demands it.

Performance testing should establish:

```text
market event processing latency
feature computation latency
alpha latency
risk latency
order submission latency
end-to-end decision latency
```

Measure first.

Optimize only when measurements justify it.

The priority remains:

```text
Correctness
Safety
Reliability
Observability
Performance
```

in that order unless the strategy explicitly requires different constraints.

---

# 150. Resource Testing

The runtime should be tested for:

```text
CPU usage
memory usage
disk growth
log growth
network usage
data-storage growth
```

The system should not silently exhaust resources during extended paper trading.

---

# 151. Long-Running Stability Test

Before live deployment, run QuantOS continuously in paper mode long enough to expose:

```text
memory leaks
state accumulation
connection instability
reconnect failures
log growth
data drift
timing issues
reconciliation problems
```

A system that works for ten minutes is not necessarily production-ready.

---

# 152. Release Candidate Process

A V1 release candidate should be created only after:

```text
all required CI checks pass
backtest regression passes
validation gates pass
paper trading is stable
operational tests pass
configuration is frozen
strategy versions are frozen
risk configuration is frozen
```

The release candidate should have an immutable version identifier.

---

# 153. Change Classification

Every change should be classified.

### Type A — Infrastructure

Examples:

```text
logging
storage
configuration
CI
```

### Type B — Domain

Examples:

```text
portfolio
order model
event model
```

### Type C — Strategy

Examples:

```text
feature
alpha
model
```

### Type D — Risk

Examples:

```text
risk limit
position sizing
kill switch
```

### Type E — Execution

Examples:

```text
broker adapter
order handling
reconciliation
```

Risk and execution changes should require the strongest verification.

---

# 154. Behavioral Change Detection

Any change to:

```text
feature calculation
alpha logic
risk logic
portfolio accounting
execution simulation
```

must be treated as potentially behavior-changing.

The engineer must compare:

```text
before
vs.
after
```

using the appropriate regression/backtest suite.

"Refactor" is not sufficient justification for assuming trading behavior is unchanged.

---

# 155. Production Promotion Pipeline

The promotion path should be:

```text
Code
  ↓
CI
  ↓
Backtest
  ↓
Validation
  ↓
Paper
  ↓
Live Readiness
  ↓
Production Approval
  ↓
Controlled Capital
```

A failure at any stage blocks promotion.

The system should never interpret a successful build as evidence that a strategy is ready to trade capital.

---

# 156. Engineering Completion Model

QuantOS should use three different concepts of completion.

### Software Complete

```text
code exists
tests pass
CI passes
```

### Strategy Complete

```text
backtest complete
validation complete
risk reviewed
paper validated
```

### Production Complete

```text
software complete
strategy complete
operational readiness complete
deployment approved
```

These must not be conflated.

---

# 157. Part 3 Completion Criteria

Part 3 is complete when the repository has:

* defined implementation phases;
* dependency-ordered development;
* unit testing strategy;
* integration testing strategy;
* contract testing strategy;
* end-to-end testing;
* regression testing;
* leakage testing;
* data mutation testing;
* risk mutation testing;
* execution failure testing;
* deterministic golden datasets;
* test fixtures;
* property-based tests where useful;
* static analysis;
* type checking;
* formatting;
* architecture checks;
* CI pipeline;
* pull-request requirements;
* secret isolation;
* development workflow;
* commit discipline;
* vertical-slice strategy;
* paper-trading gate;
* restart testing;
* failure injection;
* operational readiness testing;
* performance testing;
* long-running stability testing;
* release-candidate process;
* change classification;
* behavioral regression requirements;
* production promotion pipeline;
* definitions of done.

At this point, QuantOS has not merely been designed.

It has a **repeatable engineering process capable of producing and verifying the system**.

The final remaining problem is operational:

> How do we take this verified codebase and actually move it from an empty repository to a real-time, real-market V1 system trading controlled capital?

That is the purpose of Part 4.

# 158. Purpose of the Productionization Layer

The previous sections established:

```text
Part 1
Repository architecture
Package boundaries
Implementation structure

Part 2
Domain contracts
Schemas
Interfaces
State models

Part 3
Implementation order
Testing
CI
Validation
Paper trading
Release process
```

The final layer answers the most important practical question:

> How does QuantOS become a real, continuously running trading system?

The answer must be incremental.

QuantOS V1 should not jump directly from:

```text
Backtest
    ↓
Real Money
```

The correct progression is:

```text
Repository
    ↓
Local Runtime
    ↓
Deterministic Backtest
    ↓
Validation
    ↓
Paper Runtime
    ↓
Production Readiness
    ↓
Minimal Live Capital
    ↓
Controlled V1
```

Every transition must have explicit evidence.

---

# 159. Repository Realization

The implementation should ultimately produce a repository structurally aligned with the preceding specifications.

A representative V1 structure is:

```text
QuantOS/
│
├── docs/
│   ├── 000_READ_FIRST.md
│   ├── 001_PRD.md
│   ├── 002_ARCHITECTURE.md
│   ├── 003_DATA.md
│   ├── 004_FEATURE_ENGINE.md
│   ├── 005_ALPHA_ENGINE.md
│   ├── 006_RISK_EXECUTION.md
│   ├── 007_VALIDATION_BACKTESTING.md
│   └── 008_IMPLEMENTATION_GUIDE.md
│
├── src/
│   └── quantos/
│       ├── core/
│       ├── data/
│       ├── features/
│       ├── alpha/
│       ├── risk/
│       ├── execution/
│       ├── portfolio/
│       ├── backtest/
│       ├── validation/
│       ├── runtime/
│       ├── config/
│       ├── observability/
│       └── storage/
│
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── regression/
│   ├── e2e/
│   └── fixtures/
│
├── configs/
│   ├── development/
│   ├── backtest/
│   ├── paper/
│   └── production/
│
├── scripts/
│
├── notebooks/
│
├── data/
│
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
└── CI configuration
```

The exact directory names may evolve during implementation, but the architectural boundaries must remain intact.

---

# 160. Dependency Direction

The repository should enforce a one-directional dependency model.

Conceptually:

```text
                    Application / Runtime
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
       Alpha             Risk           Execution
          │                │                │
          └────────────┬───┴────────────────┘
                       ▼
                    Domain
                       ▲
                       │
          ┌────────────┼────────────┐
          │            │            │
        Data        Portfolio     Storage
```

Infrastructure implementations may depend on domain interfaces.

The domain must not depend on infrastructure.

For example:

```text
GOOD

domain
  ↑
broker adapter


BAD

domain
  ↓
specific exchange SDK
```

This rule protects the core system from vendor coupling.

---

# 161. Application Entry Points

QuantOS should expose explicit runtime entry points.

At minimum:

```text
quantos backtest
quantos paper
quantos live
quantos validate
quantos reconcile
quantos status
```

The exact CLI framework is implementation-specific.

The important principle is that operational behavior should be explicit.

For example:

```text
quantos live
```

must unambiguously mean:

> Start the production runtime.

There should be no implicit behavior where an ordinary development command can accidentally start live trading.

---

# 162. Local Developer Workflow

A developer should be able to execute:

```text
install
format
lint
type-check
test
backtest
```

without external production dependencies.

A clean local workflow is:

```text
Clone
  ↓
Install
  ↓
Configure local environment
  ↓
Run tests
  ↓
Run fixture backtest
  ↓
Inspect result
```

The first successful developer experience should require minimal infrastructure.

---

# 163. Local Backtest Command

The repository should provide a canonical backtest command.

Conceptually:

```text
quantos backtest \
    --config configs/backtest/v1.yaml
```

The command should produce:

```text
Run ID
Strategy
Data range
Data version
Feature version
Risk version
Execution model
Performance metrics
Trade count
Validation status
Artifact location
```

A backtest should be an explicit reproducible run, not a notebook side effect.

---

# 164. Backtest Artifacts

Every completed backtest should produce an artifact bundle.

Conceptually:

```text
runs/
└── <run_id>/
    ├── config.yaml
    ├── metadata.json
    ├── metrics.json
    ├── trades.parquet
    ├── equity_curve.parquet
    ├── events.parquet
    └── report.html
```

The exact format may differ.

The important requirement is that the run can be reconstructed later.

---

# 165. Experiment Reproducibility

An experiment must record:

```text
code revision
configuration
dataset version
feature version
strategy version
risk version
execution model
random seed
timestamp
```

A result without provenance is not a production-quality quantitative result.

The goal is:

```text
Result
  ↓
Run ID
  ↓
Configuration
  ↓
Code Revision
  ↓
Data
```

so that the complete chain can be recovered.

---

# 166. Validation Artifact

A validation run should produce a machine-readable artifact.

Conceptually:

```yaml
run_id: ...
status: PASS

gates:
  data_integrity: PASS
  leakage: PASS
  robustness: PASS
  drawdown: PASS
  cost_sensitivity: PASS
  out_of_sample: PASS
```

The exact gate set comes from `007`.

Promotion should consume this artifact rather than relying on human memory.

---

# 167. Promotion Manifest

A validated strategy should produce a promotion manifest.

Conceptually:

```yaml
strategy:
  id: strategy_v1
  version: 1.0.0

data:
  version: dataset_v1

features:
  version: feature_set_v1

risk:
  version: risk_v1

execution:
  version: execution_v1

validation:
  run_id: validation_...

code:
  revision: ...
```

This becomes the immutable description of what is approved for deployment.

---

# 168. Deployment Artifact

The production deployment should be built from a known source revision.

Conceptually:

```text
Git Revision
      ↓
Build
      ↓
Test
      ↓
Package
      ↓
Release Artifact
      ↓
Deploy
```

The running system should expose its release version.

An operator must be able to determine:

```text
What version is running?
```

without inspecting the source repository manually.

---

# 169. Environment Configuration

The production runtime should use a dedicated configuration.

Conceptually:

```text
configs/
└── production/
    ├── strategy.yaml
    ├── risk.yaml
    ├── execution.yaml
    └── runtime.yaml
```

Secrets remain outside these files unless encrypted through an approved secret-management system.

Configuration should be immutable during a trading session unless dynamic configuration is explicitly designed and safely implemented.

---

# 170. Startup Sequence

Production startup should follow a deterministic sequence.

```text
STARTING
   ↓
Load Configuration
   ↓
Validate Configuration
   ↓
Load Secrets
   ↓
Initialize Storage
   ↓
Initialize Data Provider
   ↓
Initialize Broker
   ↓
Load Strategy
   ↓
Load Feature Registry
   ↓
Load Risk Configuration
   ↓
Synchronize Account
   ↓
Synchronize Positions
   ↓
Reconcile Orders
   ↓
Validate Market Data
   ↓
Validate System Health
   ↓
READY
```

Only then may live trading become eligible.

---

# 171. Startup Must Fail Closed

If any mandatory startup condition fails:

```text
READY
```

must not be reached.

Examples:

```text
invalid credentials
missing strategy
invalid risk configuration
broker unavailable
account cannot be synchronized
positions cannot be reconciled
market data unavailable
storage unavailable
```

The system should terminate or remain safely halted according to the failure classification.

---

# 172. Readiness Check

A dedicated readiness check should verify:

```text
configuration
data
features
strategy
risk
execution
portfolio
broker
storage
clock
observability
```

Conceptually:

```python
@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    checks: Sequence[HealthCheck]
```

Example:

```text
configuration       PASS
market_data         PASS
broker              PASS
portfolio           PASS
risk                PASS
strategy            PASS
storage             PASS
observability       PASS
────────────────────────
READY               YES
```

---

# 173. Liveness vs Readiness

These concepts must remain separate.

### Liveness

> Is the process alive?

### Readiness

> Is the system safe and prepared to trade?

A process may be alive while not being ready.

For example:

```text
process alive
broker disconnected
```

means:

```text
LIVENESS = PASS
READINESS = FAIL
```

This distinction is essential for production operations.

---

# 174. Runtime Health

The runtime should continuously monitor:

```text
market-data freshness
broker connectivity
event-loop health
processing latency
portfolio synchronization
risk state
execution state
storage health
memory
CPU
```

A degraded subsystem should cause the appropriate runtime state transition.

---

# 175. Observability

QuantOS should expose three major observability channels:

```text
Logs
Metrics
Events
```

### Logs

Used for detailed diagnostics.

### Metrics

Used for quantitative system health.

### Events

Used for reconstructing state transitions and trading decisions.

These serve different purposes and should not be conflated.

---

# 176. Structured Logging

Production logs should be structured.

A trading-related log should include useful identifiers such as:

```text
timestamp
level
component
event_type
run_id
strategy_id
signal_id
order_id
symbol
```

where relevant.

Avoid logs such as:

```text
"something went wrong"
```

Prefer:

```text
execution.order_submission_failed
order_id=...
symbol=...
reason=...
```

---

# 177. Metrics

At minimum, expose metrics for:

```text
market events processed
data latency
feature latency
alpha latency
risk latency
execution latency
orders submitted
orders rejected
orders filled
partial fills
broker errors
reconciliation mismatches
current exposure
PnL
drawdown
daily loss
runtime state
```

The exact monitoring stack may evolve.

The semantic metrics should remain stable.

---

# 178. Trading Metrics vs System Metrics

Keep financial metrics separate from infrastructure metrics.

### Trading

```text
PnL
return
drawdown
exposure
turnover
fees
slippage
trade count
```

### System

```text
CPU
memory
latency
event rate
error rate
connection status
queue depth
```

Both are required for production operations.

A profitable system with unstable infrastructure is not production-safe.

---

# 179. Alerting

Alerts should correspond to actionable conditions.

Examples:

```text
critical risk breach
broker disconnected
market data stale
reconciliation mismatch
unexpected position
unknown order state
runtime halted
daily loss threshold
system unhealthy
```

Avoid alerting on every informational event.

The objective is:

```text
important condition
      ↓
operator awareness
      ↓
clear action
```

---

# 180. Kill Switch

The kill switch is a mandatory production capability.

It must be able to prevent new trading activity.

Conceptually:

```text
KILL SWITCH = ON
        ↓
NO NEW ORDERS
```

Depending on the risk policy, it may additionally initiate:

```text
cancel open orders
reduce positions
flatten positions
```

The exact behavior must follow `006_RISK_EXECUTION`.

The kill switch must be independently testable.

---

# 181. Kill Switch Priority

The kill switch must operate outside the strategy's authority.

The hierarchy is:

```text
Kill Switch
    ↓
Risk
    ↓
Strategy
```

Strategy logic cannot override an active kill switch.

A signal such as:

```text
BUY BTC
```

must remain rejected while:

```text
TRADING_HALTED = TRUE
```

---

# 182. Manual Override

Manual operational controls should be explicit and auditable.

Examples:

```text
halt trading
resume trading
cancel orders
reconcile
restart
```

Every manual intervention should record:

```text
operator/action identity where applicable
timestamp
action
reason
result
```

No hidden operational commands should exist.

---

# 183. Position Reconciliation

At runtime, QuantOS should periodically compare:

```text
Internal Position
       vs.
Broker Position
```

Expected:

```text
match
```

If mismatched:

```text
DEGRADED / HALTED
```

according to the risk policy.

The system must not continue opening new positions while it does not know its actual exposure.

---

# 184. Order Reconciliation

Open orders must similarly be compared:

```text
Internal Orders
       vs.
Broker Orders
```

Detect:

```text
missing broker order
unexpected broker order
status mismatch
quantity mismatch
```

Unknown order state requires reconciliation before further mutation.

---

# 185. State Persistence

Critical state must survive process restarts.

Depending on architecture, this includes:

```text
orders
fills
positions
portfolio state
runtime state
audit events
configuration/version metadata
```

The exact persistence model comes from `002_ARCHITECTURE` and `003_DATA`.

The implementation must distinguish:

```text
authoritative external state
internal derived state
```

Broker state is authoritative for broker-controlled execution state.

---

# 186. Recovery Procedure

After an unexpected restart:

```text
Process starts
   ↓
Load persisted state
   ↓
Connect broker
   ↓
Fetch authoritative broker state
   ↓
Reconcile
   ↓
Repair internal state
   ↓
Validate risk
   ↓
READY / HALTED
```

Never:

```text
Process starts
   ↓
Assume previous memory was correct
   ↓
Immediately trade
```

---

# 187. Deployment Topology

V1 should favor operational simplicity.

A minimal topology may be:

```text
                 Internet / Exchange
                         │
                         ▼
                  ┌─────────────┐
                  │ QuantOS App │
                  └──────┬──────┘
                         │
                ┌────────┼────────┐
                ▼        ▼        ▼
             Storage   Metrics   Logs
```

Avoid unnecessary distributed infrastructure.

V1 should not require:

```text
Kubernetes
microservice mesh
multiple message brokers
complex orchestration
```

unless a genuine requirement exists.

---

# 188. Single-Process V1 Principle

Where possible, V1 should run as a single coherent application process.

This reduces:

```text
network boundaries
deployment complexity
failure modes
state synchronization problems
operational overhead
```

Internal interfaces should still be clean.

A monolithic runtime does not mean a monolithic architecture.

The code can maintain strong subsystem boundaries while running in one process.

---

# 189. Storage Strategy

V1 should use the simplest storage architecture capable of meeting the requirements.

Separate:

```text
market data
trading state
audit events
backtest artifacts
```

conceptually, even if some share infrastructure.

Storage should prioritize:

```text
correctness
durability
recoverability
queryability
```

over premature scalability.

---

# 190. Deployment Environment

The production host must provide:

```text
stable network
reliable clock
persistent storage
secure credentials
process supervision
resource monitoring
restart capability
```

The machine does not need to be sophisticated.

The objective is predictable operation.

---

# 191. Process Supervision

QuantOS should run under a process supervisor capable of:

```text
automatic restart
startup ordering
log capture
resource limits
health monitoring
```

However:

```text
automatic restart
```

must not imply:

```text
automatic resume trading
```

After a critical crash, QuantOS should restart into a safe state and reconcile before resuming.

---

# 192. Clock Synchronization

Trading systems depend on accurate time.

The production environment should maintain reliable clock synchronization.

QuantOS should detect significant clock anomalies.

If time becomes unreliable:

```text
new trading activity
        ↓
halt
```

The exact tolerance should be defined by the operational requirements.

---

# 193. Network Failure Behavior

When market connectivity fails:

```text
detect
  ↓
mark data stale
  ↓
stop new trading if required
  ↓
maintain safe state
  ↓
reconnect
  ↓
validate fresh data
  ↓
resume only when ready
```

The system must not interpret:

```text
no new data
```

as:

```text
market unchanged
```

---

# 194. Broker Failure Behavior

When broker connectivity fails:

```text
detect
  ↓
stop new order mutations
  ↓
preserve known state
  ↓
reconnect
  ↓
query broker
  ↓
reconcile
  ↓
validate risk
  ↓
resume if safe
```

Unknown execution state must always be reconciled.

---

# 195. Data Failure Behavior

If data becomes:

```text
stale
corrupted
incomplete
out-of-order
```

the appropriate response is:

```text
reject invalid data
       ↓
prevent downstream decisions
       ↓
alert
       ↓
recover
```

No strategy should continue trading from invalid market state.

---

# 196. Production Configuration Freeze

Before live launch:

```text
strategy version
feature version
risk configuration
execution configuration
data configuration
```

should be frozen.

Any change requires a new release or explicitly controlled configuration update.

This prevents production from becoming an uncontrolled research environment.

---

# 197. Production Change Management

A production change should follow:

```text
Change proposed
   ↓
Specification impact identified
   ↓
Implementation
   ↓
Tests
   ↓
Backtest impact
   ↓
Validation
   ↓
Paper verification
   ↓
Release
   ↓
Controlled deployment
```

Risk/execution changes may require stronger gates.

---

# 198. Rollback Strategy

Every production deployment must have a rollback path.

Conceptually:

```text
Release N
   ↓
Release N+1
   ↓
Problem detected
   ↓
Halt / safe state
   ↓
Rollback to N
   ↓
Reconcile
   ↓
Resume only after validation
```

Rollback must account for state.

Simply reverting code is not sufficient if broker positions have changed.

---

# 199. Capital Deployment Strategy

V1 should not begin with maximum intended capital.

The production rollout should be staged.

Conceptually:

```text
$0
 ↓
paper
 ↓
minimal live capital
 ↓
small controlled allocation
 ↓
validated increase
```

The initial live allocation should be deliberately small enough that an unexpected system failure is survivable.

The exact amount is a business/risk decision, not an implementation constant.

---

# 200. First Live Trade

The first live trade is an operational validation event.

Before allowing it, verify:

```text
correct account
correct symbol
correct strategy
correct risk configuration
correct position limits
correct broker
correct environment
kill switch functional
reconciliation functional
logging functional
```

The first live order should be intentionally constrained.

The objective is not profit maximization.

The objective is proving:

```text
Signal
→ Risk
→ Order
→ Broker
→ Fill
→ Portfolio
→ Audit
```

works with real external state.

---

# 201. First Live Session

The first production session should be treated as a controlled experiment.

Monitor:

```text
market data
signal frequency
risk decisions
orders
fills
latency
fees
slippage
portfolio
reconciliation
system health
```

Compare observed behavior against expectations from:

```text
backtest
paper trading
```

Unexpected divergence must be investigated before increasing capital.

---

# 202. Live-vs-Backtest Comparison

Production monitoring should compare:

```text
expected
vs.
observed
```

for:

```text
signal frequency
trade frequency
execution prices
fees
slippage
holding periods
exposure
drawdown
latency
```

Differences are not automatically bugs.

Real markets differ from simulation.

However, unexplained differences must be investigated.

---

# 203. Live-vs-Paper Comparison

During initial deployment, paper and live behavior should be compared where possible.

The comparison should help identify:

```text
execution differences
spread assumptions
slippage
latency
order rejection
market impact
provider differences
```

The purpose is calibration.

---

# 204. V1 Operational Objective

The goal of V1 is not:

```text
maximum strategy sophistication
```

nor:

```text
maximum number of strategies
```

nor:

```text
maximum leverage
```

The V1 objective is:

```text
A small, deterministic, observable,
risk-controlled trading system that
can operate continuously in the real market.
```

That is the first major milestone.

---

# 205. V1 Success Definition

A successful V1 can:

```text
ingest real-time market data
        ↓
compute features
        ↓
generate signals
        ↓
apply risk
        ↓
create orders
        ↓
execute through broker
        ↓
receive fills
        ↓
maintain portfolio state
        ↓
reconcile with broker
        ↓
persist audit trail
        ↓
survive restart
        ↓
operate continuously
```

while remaining:

```text
observable
reproducible
testable
recoverable
risk-controlled
```

---

# 206. Profit Is Not the First Gate

V1 should not be declared successful merely because:

```text
first trade profitable
```

Likewise, it should not be declared a failure because:

```text
first trade loses money
```

The first production milestones are operational.

The system must first demonstrate:

```text
correctness
safety
reliability
```

Then performance can be evaluated.

---

# 207. V1 Financial Evaluation

Once sufficient live data exists, evaluate:

```text
net PnL
return
drawdown
Sharpe / relevant risk-adjusted metrics
turnover
fees
slippage
win/loss characteristics
exposure
capacity
```

These must be compared against:

```text
backtest expectations
validation expectations
paper expectations
```

The objective is to determine whether the research assumptions survive contact with the market.

---

# 208. Performance Drift

A production strategy may degrade.

QuantOS should distinguish:

```text
normal variance
```

from:

```text
structural degradation
```

Monitoring should identify changes in:

```text
signal distribution
feature distribution
execution quality
trade frequency
PnL distribution
drawdown
market regime
```

A strategy should not automatically continue forever simply because it once passed validation.

---

# 209. Strategy Suspension

The system should support explicit suspension.

Triggers may include:

```text
risk breach
validation failure
unexpected behavior
persistent execution degradation
data integrity issue
operational instability
manual operator decision
```

Suspension should result in:

```text
no new trades
```

with state and reason recorded.

---

# 210. Strategy Retirement

A strategy should eventually be retired when:

```text
edge no longer exists
risk profile becomes unacceptable
data dependency becomes unreliable
maintenance cost becomes unjustified
```

Retirement should preserve historical artifacts.

A retired strategy must remain reproducible for audit and research.

---

# 211. V1 Incident Procedure

When a critical production incident occurs:

```text
1. Halt new trading.
2. Determine current broker state.
3. Reconcile orders.
4. Reconcile positions.
5. Determine exposure.
6. Preserve logs/events.
7. Identify root cause.
8. Repair system.
9. Re-run validation.
10. Resume only after approval.
```

Do not optimize for restoring trading speed at the expense of state correctness.

---

# 212. Incident Classification

Incidents should be classified.

```text
P0 — Capital / safety critical
P1 — Trading functionality impaired
P2 — Degraded functionality
P3 — Non-critical operational issue
```

Examples:

### P0

```text
unexpected position
duplicate order
unknown large exposure
risk bypass
```

### P1

```text
broker disconnected
market data unavailable
execution halted
```

### P2

```text
monitoring degradation
delayed non-critical metrics
```

The exact operational policy may evolve.

---

# 213. Post-Incident Review

A production incident should result in:

```text
timeline
root cause
impact
detection mechanism
resolution
corrective action
test added
```

The most important output is often:

```text
new regression test
```

The system should become harder to break after every incident.

---

# 214. Documentation as Operational Memory

The documentation set should evolve with the system.

When behavior changes:

```text
implementation
   +
tests
   +
documentation
```

must remain aligned.

The eight documents form a coherent system:

```text
000 — Engineering Philosophy
001 — Product Requirements
002 — Architecture
003 — Data
004 — Feature Engine
005 — Alpha Engine
006 — Risk & Execution
007 — Validation & Backtesting
008 — Implementation Guide
```

The implementation is complete only when the repository and these specifications describe the same system.

---

# 215. Specification-to-Code Traceability

Every major subsystem should be traceable:

```text
Specification
    ↓
Package
    ↓
Interface
    ↓
Implementation
    ↓
Tests
```

For example:

```text
006_RISK_EXECUTION
        ↓
src/quantos/risk/
        ↓
RiskEngine
        ↓
RiskRule implementations
        ↓
tests/unit/risk/
tests/integration/risk/
```

This traceability makes the specification useful during development rather than merely archival.

---

# 216. The Complete Implementation Sequence

The entire QuantOS build can now be summarized as:

```text
READ SPECIFICATIONS
        ↓
BOOTSTRAP REPOSITORY
        ↓
IMPLEMENT CORE DOMAIN
        ↓
IMPLEMENT DATA
        ↓
IMPLEMENT FEATURES
        ↓
IMPLEMENT PORTFOLIO
        ↓
IMPLEMENT ALPHA
        ↓
IMPLEMENT RISK
        ↓
IMPLEMENT PAPER EXECUTION
        ↓
IMPLEMENT BACKTEST
        ↓
IMPLEMENT VALIDATION
        ↓
RUN REGRESSION SUITE
        ↓
RUN PAPER TRADING
        ↓
RUN FAILURE / RECOVERY TESTS
        ↓
FREEZE V1 MANIFEST
        ↓
DEPLOY PRODUCTION RUNTIME
        ↓
RECONCILE
        ↓
ENABLE MINIMAL CAPITAL
        ↓
VERIFY FIRST LIVE EXECUTION
        ↓
MONITOR
        ↓
INCREASE CAPITAL ONLY AFTER EVIDENCE
```

This is the intended implementation roadmap.

---

# 217. The First Engineering Milestone

The first milestone should not be:

```text
"QuantOS is finished."
```

It should be:

```text
"QuantOS can execute one complete deterministic
trading lifecycle locally."
```

That means:

```text
Market Data
→ Feature
→ Signal
→ Risk
→ Order
→ Fill
→ Portfolio
→ Audit
```

Once this works reliably, the system can grow.

---

# 218. The Second Engineering Milestone

The second milestone is:

```text
"QuantOS can reproduce the same trading lifecycle
against historical data."
```

This proves:

```text
backtest integration
determinism
portfolio accounting
execution simulation
performance calculation
```

---

# 219. The Third Engineering Milestone

The third milestone is:

```text
"QuantOS can operate continuously without human
intervention in paper mode."
```

This proves:

```text
runtime stability
real-time data
reconnection
state persistence
reconciliation
monitoring
```

---

# 220. The Fourth Engineering Milestone

The fourth milestone is:

```text
"QuantOS can safely execute a constrained real trade."
```

This proves:

```text
real broker connectivity
real order lifecycle
real fills
real fees
real portfolio state
real reconciliation
```

This is the first point where the system has crossed from research software into a real trading system.

---

# 221. The Fifth Engineering Milestone

The fifth milestone is:

```text
"QuantOS can operate continuously in production
while preserving capital and system integrity."
```

Only after this milestone should the project prioritize:

```text
strategy expansion
model sophistication
additional markets
larger capital
optimization
automation
```

---

# 222. What V1 Deliberately Does Not Attempt

V1 should deliberately avoid unnecessary complexity.

Do not require:

```text
multiple strategies
multiple exchanges
high-frequency infrastructure
distributed microservices
complex agent systems
large language models in the execution path
automated strategy generation
massive feature libraries
GPU-dependent production logic
```

unless a validated requirement emerges.

The system should first prove that a simple architecture can make and execute a sound decision reliably.

---

# 223. AI / LLM Boundary

If AI or LLM components are introduced later, they should initially remain outside the critical execution path.

A safe conceptual boundary is:

```text
Research / Intelligence
        ↓
Candidate Strategy
        ↓
Validation
        ↓
Approved Strategy
        ↓
Deterministic Runtime
```

rather than:

```text
LLM
  ↓
Direct Broker Order
```

The production trading path must remain deterministic, testable, and governed by risk controls.

This allows future intelligence layers without compromising the V1 safety architecture.

---

# 224. Capital Scaling Rule

Capital should increase only when evidence supports the increase.

Conceptually:

```text
Operational Stability
        +
Execution Stability
        +
Risk Stability
        +
Strategy Stability
        ↓
Capital Increase
```

Capital should never increase merely because:

```text
recent PnL is positive
```

The objective is to distinguish genuine system robustness from short-term luck.

---

# 225. V1 Production Checklist

Before enabling live trading:

```text
ARCHITECTURE
[ ] Repository structure implemented
[ ] Dependency direction verified
[ ] Interfaces implemented
[ ] Contracts tested

DATA
[ ] Live provider tested
[ ] Data validation active
[ ] Staleness detection active
[ ] Timestamp handling verified

FEATURES
[ ] Feature registry complete
[ ] Feature versions defined
[ ] Look-ahead tests pass
[ ] Determinism verified

ALPHA
[ ] Strategy version frozen
[ ] Signal behavior validated
[ ] Regression suite passes

RISK
[ ] Risk limits configured
[ ] Risk tests pass
[ ] Kill switch tested
[ ] Fail-closed behavior verified

EXECUTION
[ ] Broker adapter tested
[ ] Paper execution tested
[ ] Order lifecycle tested
[ ] Idempotency tested
[ ] Reconciliation tested

PORTFOLIO
[ ] Position accounting verified
[ ] PnL verified
[ ] Fees verified
[ ] Restart recovery tested

VALIDATION
[ ] Backtest complete
[ ] Leakage checks pass
[ ] Robustness checks pass
[ ] Validation gates pass

OPERATIONS
[ ] Logging active
[ ] Metrics active
[ ] Alerts active
[ ] Health checks active
[ ] Secrets secured
[ ] Process supervision configured

PAPER
[ ] Continuous paper run completed
[ ] Restart tested
[ ] Failure injection tested
[ ] Reconciliation tested

DEPLOYMENT
[ ] Production artifact built
[ ] Production configuration frozen
[ ] Release version recorded
[ ] Rollback path verified

LIVE
[ ] Account verified
[ ] Capital constrained
[ ] First-order procedure verified
[ ] Operator monitoring active
```

---

# 226. V1 Go-Live Gate

The system may transition from paper to live only when all mandatory gates are satisfied.

Conceptually:

```text
                  V1 GO-LIVE
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
     Software      Strategy       Operations
      Ready         Ready           Ready
        │             │             │
        └─────────────┼─────────────┘
                      ▼
                Capital Ready
                      │
                      ▼
                LIVE APPROVED
```

A failed gate means:

```text
NO LIVE TRADING
```

No amount of promising backtest performance overrides a failed operational gate.

---

# 227. V1 Launch Procedure

The initial launch should follow:

```text
1. Deploy approved release.
2. Verify runtime version.
3. Verify configuration.
4. Verify credentials.
5. Connect market data.
6. Connect broker.
7. Synchronize account.
8. Reconcile positions.
9. Reconcile orders.
10. Verify risk limits.
11. Verify kill switch.
12. Verify readiness.
13. Enable trading.
14. Monitor first decision.
15. Monitor first order.
16. Monitor first fill.
17. Verify portfolio update.
18. Verify audit chain.
19. Continue under constrained capital.
```

Each step should be observable.

---

# 228. First Live Capital Principle

The first real-money deployment should be treated as a **systems test with capital attached**, not as a profit-maximization event.

The immediate objectives are:

```text
correct execution
correct accounting
correct risk
correct reconciliation
correct observability
```

Profit is secondary during the initial operational proving period.

---

# 229. Production Operating Loop

Once V1 is live, the operational loop becomes:

```text
                    ┌───────────────┐
                    │  Market Data  │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │    Features   │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │     Alpha     │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │      Risk     │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │   Execution   │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │     Broker    │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │     Fills     │
                    └───────┬───────┘
                            ▼
                    ┌───────────────┐
                    │   Portfolio   │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │ Observability │
                    └───────┬───────┘
                            │
                            └───────► Risk / Operations
```

This loop runs continuously.

---

# 230. Production Feedback Loop

Production data should eventually feed back into research.

But the direction must remain controlled:

```text
Production
    ↓
Observed Data
    ↓
Research
    ↓
Hypothesis
    ↓
Backtest
    ↓
Validation
    ↓
Paper
    ↓
Production
```

Never:

```text
Production
    ↓
Automatic strategy modification
    ↓
Production
```

without passing through validation.

---

# 231. Research-to-Production Promotion

A new strategy should follow:

```text
Research
   ↓
Candidate
   ↓
Backtest
   ↓
Validation
   ↓
Paper
   ↓
Promotion Review
   ↓
Production
```

A strategy cannot promote itself.

The promotion process must remain external to the strategy logic.

---

# 232. Long-Term Evolution

Once V1 is operational, future versions may introduce:

```text
additional strategies
additional markets
machine learning
LLM research agents
advanced portfolio optimization
alternative data
more sophisticated execution
distributed infrastructure
```

But every future capability should preserve the core principle:

```text
Research may become more sophisticated.
Production execution must remain controlled.
```

---

# 233. Architecture Evolution Rule

Future complexity must be justified by measurable requirements.

Examples:

```text
Need higher throughput
→ optimize pipeline

Need more markets
→ improve data architecture

Need multiple strategies
→ expand strategy registry

Need more execution venues
→ add broker adapters

Need larger scale
→ evaluate distributed architecture
```

Do not introduce infrastructure merely because it is fashionable.

---

# 234. The QuantOS V1 Contract

The complete implementation contract can now be stated simply:

```text
QuantOS V1 MUST:

1. Consume validated market data.
2. Produce deterministic features.
3. Generate explicit signals.
4. Apply mandatory risk controls.
5. Produce explicit order intents.
6. Execute through an isolated broker boundary.
7. Track actual fills.
8. Maintain portfolio state.
9. Reconcile external state.
10. Persist an audit trail.
11. Survive expected failures.
12. Support deterministic backtesting.
13. Validate strategies before promotion.
14. Run continuously in paper mode.
15. Deploy through controlled releases.
16. Start live trading only after readiness checks.
17. Fail closed when critical state is unknown.
18. Operate with constrained initial capital.
19. Remain observable.
20. Remain reproducible.
```

---

# 235. Final Repository Acceptance Test

The repository is considered implementation-complete for V1 only when a clean environment can execute the complete lifecycle:

```text
Clone Repository
      ↓
Install Dependencies
      ↓
Run Test Suite
      ↓
Run Backtest
      ↓
Generate Validation Artifact
      ↓
Start Paper Runtime
      ↓
Process Real-Time Data
      ↓
Generate Signal
      ↓
Apply Risk
      ↓
Generate Order Intent
      ↓
Execute Paper Order
      ↓
Generate Fill
      ↓
Update Portfolio
      ↓
Persist State
      ↓
Restart Application
      ↓
Reconcile State
      ↓
Resume Safely
```

After this passes consistently:

```text
Paper
   ↓
Production Readiness
   ↓
Minimal Live Capital
```

becomes an operational deployment decision rather than an architectural unknown.

---

# 236. Definition of QuantOS V1

QuantOS V1 is complete when:

```text
the system can continuously observe the market,
compute its approved features,
generate its approved strategy decisions,
enforce its approved risk constraints,
execute through its approved broker boundary,
account for real fills,
reconcile external state,
survive expected failures,
and provide enough evidence to explain every trading decision.
```

That is the definition of a production trading system.

Not the number of files.

Not the number of strategies.

Not the sophistication of the models.

Not the size of the codebase.

The system is successful when its behavior is **controlled, observable, reproducible, and economically meaningful**.

---

# 237. Final Implementation Roadmap

The entire eight-document architecture now resolves into one implementation sequence:

```text
000_READ_FIRST
      │
      ▼
Understand Engineering Philosophy
      │
      ▼
001_PRD
      │
      ▼
Understand V1 Requirements
      │
      ▼
002_ARCHITECTURE
      │
      ▼
Implement System Boundaries
      │
      ▼
003_DATA
      │
      ▼
Implement Canonical Data Layer
      │
      ▼
004_FEATURE_ENGINE
      │
      ▼
Implement Features
      │
      ▼
005_ALPHA_ENGINE
      │
      ▼
Implement Strategy
      │
      ▼
006_RISK_EXECUTION
      │
      ▼
Implement Risk + Execution
      │
      ▼
007_VALIDATION_BACKTESTING
      │
      ▼
Prove Strategy Robustness
      │
      ▼
008_IMPLEMENTATION_GUIDE
      │
      ▼
Build Repository
      │
      ▼
Test
      │
      ▼
Integrate
      │
      ▼
Paper Trade
      │
      ▼
Validate Production Runtime
      │
      ▼
Deploy Controlled Capital
      │
      ▼
                 QuantOS V1
```

---

# 238. The Final Engineering Principle

QuantOS should be built with one overriding principle:

```text
DO NOT OPTIMIZE FOR WHAT THE SYSTEM
COULD EVENTUALLY BECOME.

OPTIMIZE FOR PROVING THAT V1
ACTUALLY WORKS.
```

The first objective is therefore not to build the ultimate quantitative platform.

It is to build the smallest complete system capable of:

```text
real data
    ↓
real decision
    ↓
real risk control
    ↓
real execution
    ↓
real accounting
    ↓
real reconciliation
    ↓
real evidence
```

Once that system works reliably, everything else becomes an iteration.

---

# 239. 008 Implementation Guide — Completion Criteria

This document is complete when the repository implementation team can answer all of the following without ambiguity:

### Repository

```text
Where does each subsystem live?
What may each subsystem import?
Where are interfaces defined?
Where are tests defined?
```

### Contracts

```text
What objects cross subsystem boundaries?
What does each object mean?
Who owns each state?
```

### Configuration

```text
How is configuration loaded?
How is it validated?
Where are secrets stored?
How is production activated?
```

### Development

```text
What gets implemented first?
What depends on what?
What is the minimum vertical slice?
```

### Testing

```text
What must be unit tested?
What requires integration tests?
What requires contract tests?
What requires backtest regression?
What requires failure injection?
```

### CI

```text
What blocks a merge?
What blocks a release?
What requires extended validation?
```

### Deployment

```text
How is the runtime built?
How is it started?
How is readiness determined?
How is it restarted?
How is it rolled back?
```

### Production

```text
How is market data monitored?
How is broker state reconciled?
How does the kill switch work?
What happens during failure?
```

### Capital

```text
How does the system move from paper to live?
How is initial capital constrained?
When can capital increase?
```

### V1

```text
What does "working" mean?
What evidence is required?
What prevents premature complexity?
```

If these questions have concrete answers in the repository, the implementation guide has fulfilled its purpose.

---

# 240. Final State

The eight specifications now form a single engineering chain:

```text
                    QuantOS
                       │
        ┌──────────────┴──────────────┐
        │                             │
    REQUIREMENTS                 ENGINEERING
        │                             │
       001                           000
        │                             │
        ▼                             ▼
   ARCHITECTURE                    PHILOSOPHY
        │
       002
        │
        ▼
       DATA
        │
       003
        │
        ▼
     FEATURES
        │
       004
        │
        ▼
       ALPHA
        │
       005
        │
        ▼
   RISK / EXECUTION
        │
       006
        │
        ▼
 VALIDATION / BACKTEST
        │
       007
        │
        ▼
 IMPLEMENTATION GUIDE
        │
       008
        │
        ▼
   REAL REPOSITORY
        │
        ▼
   PAPER MARKET
        │
        ▼
   REAL MARKET
        │
        ▼
    QUANTOS V1
```

The implementation team should now be able to take the eight documents and build QuantOS from an empty repository without having to invent the architecture while coding.

The architecture defines **what exists**.

The interfaces define **how components communicate**.

The validation specification defines **what must be proven**.

The implementation guide defines **how to turn all of it into a running system**.

The final destination is deliberately simple:

```text
BUILD
  ↓
TEST
  ↓
VALIDATE
  ↓
PAPER
  ↓
DEPLOY
  ↓
RECONCILE
  ↓
TRADE
  ↓
MEASURE
  ↓
IMPROVE
```

That is the complete V1 path from specification to production.
