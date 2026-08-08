# 007 — VALIDATION & BACKTESTING

## 1. Purpose

Validation & Backtesting defines the formal methodology by which QuantOS determines whether an Alpha Engine strategy is sufficiently reliable to progress from research into paper trading and ultimately live trading.

This document establishes the validation contract between:

* historical market data,
* the Data Layer,
* the Feature Engine,
* the Alpha Engine,
* Risk & Execution,
* the backtesting environment,
* paper trading,
* and live production.

The purpose of validation is not to demonstrate that a strategy can produce an attractive historical equity curve.

The purpose is to determine whether the complete QuantOS decision process demonstrates sufficient evidence of robustness, statistical credibility, implementation correctness, and operational safety to justify deployment with real capital.

Backtesting therefore exists as an engineering control mechanism, not as a performance-presentation mechanism.

A strategy that cannot survive rigorous validation must not be promoted to live trading regardless of its historical profitability.

---

# 2. V1 Validation Scope

Validation V1 is constrained to the same production scope defined throughout the QuantOS architecture.

The validation system MUST support:

* Binance Spot market data
* BTCUSDT
* ETHUSDT
* the production Data Layer
* the production Feature Engine
* the production Alpha Engine
* the production Risk & Execution logic
* deterministic historical simulation
* reproducible experiment execution
* paper/live promotion gates

The validation environment MUST NOT introduce strategy logic, feature transformations, risk rules, execution assumptions, or data processing behavior that does not exist in the production system.

The central principle is:

> **The strategy being validated must be materially identical to the strategy that will trade.**

Research-specific experimentation may exist outside the production path, but any strategy promoted toward production MUST pass through the same canonical production components.

---

# 3. Validation Philosophy

## 3.1 Backtesting Is Not Proof of Profitability

A profitable backtest does not establish that a strategy will be profitable in production.

Historical profitability can result from:

* overfitting,
* data leakage,
* survivorship effects,
* unrealistic execution assumptions,
* incorrect timestamp handling,
* unavailable historical information,
* excessive parameter optimization,
* underestimated transaction costs,
* underestimated slippage,
* regime-specific behavior,
* implementation differences between research and production,
* or statistical noise.

Therefore QuantOS MUST NOT treat historical performance as sufficient evidence for deployment.

Validation evaluates whether the observed strategy behavior remains credible under increasingly difficult tests.

---

# 4. Validation Hierarchy

QuantOS V1 uses a layered validation hierarchy.

A strategy MUST progress through the hierarchy in order.

### Layer 1 — Data Integrity

Establish that the historical dataset is complete, correctly timestamped, correctly ordered, and suitable for simulation.

### Layer 2 — Feature Integrity

Establish that every feature is calculated using only information available at the decision timestamp.

### Layer 3 — Signal Integrity

Establish that Alpha Engine decisions are deterministic, reproducible, and correctly generated from the approved feature set.

### Layer 4 — Risk Integrity

Establish that all simulated positions and orders pass through the same risk constraints used by production.

### Layer 5 — Execution Integrity

Establish that fills, fees, slippage, latency assumptions, order constraints, and position transitions are simulated realistically.

### Layer 6 — Statistical Validation

Establish whether strategy performance is sufficiently robust across different periods and market conditions.

### Layer 7 — Out-of-Sample Validation

Establish whether strategy behavior persists on data that was not used to develop or optimize the strategy.

### Layer 8 — Paper Trading

Establish whether the complete production decision pipeline behaves correctly against live market conditions without risking capital.

### Layer 9 — Live Promotion

Establish whether sufficient evidence exists to authorize deployment with real capital.

Failure at any layer MUST prevent advancement to the next layer.

---

# 5. The Validation Contract

Every strategy entering validation MUST have a fixed validation contract.

The contract MUST define:

* strategy version,
* Alpha Engine version,
* Feature Engine version,
* feature configuration version,
* data source,
* dataset version,
* market symbols,
* timeframe,
* historical period,
* training period,
* validation period,
* test period,
* parameter configuration,
* risk configuration,
* execution assumptions,
* fee assumptions,
* slippage assumptions,
* initial capital,
* position limits,
* experiment identifier,
* and software revision.

Once an official validation run begins, these inputs MUST be immutable.

Changing any material component creates a new validation run.

A validation result MUST therefore be tied to a specific system state rather than simply to a strategy name.

---

# 6. Deterministic Reproducibility

The same validation inputs MUST produce the same validation outputs.

Given identical:

* source data,
* data version,
* feature configuration,
* alpha configuration,
* risk configuration,
* execution configuration,
* initial capital,
* timestamps,
* and software revision,

QuantOS MUST produce materially identical:

* signals,
* orders,
* fills,
* positions,
* equity,
* PnL,
* drawdown,
* and validation metrics.

Any non-deterministic component capable of materially changing validation results MUST be explicitly controlled.

Randomized processes, where required by an approved validation procedure, MUST use recorded seeds and MUST be reproducible.

---

# 7. Production-Path Principle

The backtester MUST NOT become a second implementation of the trading system.

Duplicated business logic creates a structural validation failure because the system being tested can diverge from the system that eventually trades.

Where practical, the backtesting environment MUST invoke the same canonical components used by production:

```text
Historical Data
      ↓
Data Layer
      ↓
Feature Engine
      ↓
Alpha Engine
      ↓
Risk & Execution
      ↓
Simulated Exchange
      ↓
Portfolio State
      ↓
Metrics / Validation
```

The simulated exchange is the principal boundary between strategy logic and historical execution.

The backtester simulates market interaction; it MUST NOT silently replace production decision logic with simplified research equivalents.

---

# 8. No Lookahead Principle

The most important validation invariant is temporal causality.

At any decision timestamp `t`, QuantOS MUST only use information that was actually available by `t`.

This applies to:

* raw market data,
* derived market data,
* features,
* rolling statistics,
* normalization,
* volatility estimates,
* regime classification,
* signal generation,
* position sizing,
* risk decisions,
* and execution decisions.

Information from timestamps greater than `t` MUST NOT influence any decision made at `t`.

This requirement applies equally to:

* training,
* validation,
* backtesting,
* paper trading,
* and live trading.

A strategy producing exceptional historical performance through temporal leakage is considered invalid regardless of its reported returns.

---

# 9. Single Source of Truth

The Feature Engine established in document 004 remains the authoritative source for production features.

The Alpha Engine established in document 005 MUST consume the canonical feature representation.

The backtesting system MUST NOT independently recreate production features using separate formulas merely for convenience.

Likewise, Risk & Execution established in document 006 remains authoritative for simulated portfolio and order decisions.

This produces the following validation invariant:

> **Historical simulation MUST execute the same decision graph that production executes, with only the market/exchange boundary replaced by a deterministic simulator.**

---

# 10. Research / Validation Separation

QuantOS distinguishes between:

### Research

Used to:

* investigate hypotheses,
* explore features,
* test candidate signals,
* inspect market behavior,
* identify potential strategy improvements.

Research is exploratory.

### Validation

Used to determine whether a defined strategy satisfies the production standard.

Validation is controlled.

A researcher MUST NOT repeatedly modify a strategy based on observations from the official test set and continue to report the same test set as out-of-sample evidence.

Once an out-of-sample dataset has influenced strategy development, it is no longer considered clean out-of-sample evidence.

---

# 11. Validation Dataset Separation

Historical data MUST be conceptually separated into:

```text
Development
    ↓
Validation
    ↓
Final Out-of-Sample Test
```

The development dataset may be used for strategy construction.

The validation dataset may be used for controlled parameter and design evaluation.

The final out-of-sample dataset MUST remain isolated from strategy development until the strategy is considered frozen.

The final test exists to answer a specific question:

> **Does the finalized strategy continue to behave acceptably on data that did not influence its construction?**

Performance on the development dataset MUST NOT be treated as equivalent evidence to final out-of-sample performance.

---

# 12. Validation Must Test Failure, Not Only Success

QuantOS validation MUST actively search for conditions under which the strategy fails.

A validation process that only measures average profitability is incomplete.

Validation MUST investigate:

* maximum drawdown,
* losing streaks,
* clustered losses,
* volatility shocks,
* trend reversals,
* low-volatility periods,
* high-volatility periods,
* abnormal market conditions,
* execution degradation,
* increased transaction costs,
* increased slippage,
* and changes in market regime.

The objective is to understand the strategy's failure envelope.

A strategy is not considered robust merely because its average return is attractive.

The system MUST establish whether the strategy fails in ways that remain compatible with the risk budget defined by Risk & Execution.

---

# 13. Capital Preservation Priority

QuantOS V1 prioritizes capital preservation over theoretical maximum return.

Validation therefore evaluates performance within the risk constraints defined by document 006.

A strategy that produces higher returns by violating the intended risk profile is not considered superior.

Examples include:

* excessive drawdown,
* excessive concentration,
* uncontrolled exposure,
* unrealistic leverage assumptions,
* unacceptable loss clustering,
* or execution behavior incompatible with the production account.

The validation system MUST reject the assumption that greater historical return automatically represents better strategy quality.

---

# 14. Validation Output

Every official validation run MUST produce an auditable result containing, at minimum:

* validation identifier,
* strategy identifier,
* software revision,
* data version,
* feature version,
* configuration versions,
* validation period,
* initial capital,
* final equity,
* realized PnL,
* return,
* maximum drawdown,
* trade count,
* win/loss statistics,
* transaction costs,
* slippage impact,
* exposure statistics,
* and validation status.

The output MUST be sufficient for another engineer to identify exactly what configuration produced the result.

A metric without its corresponding configuration and dataset provenance is not considered an authoritative QuantOS validation result.

---

# 15. Validation Status

Every official validation run MUST terminate in an explicit status.

Allowed V1 statuses are:

* `PASS`
* `FAIL`
* `INVALID`
* `INCOMPLETE`

### PASS

The validation completed successfully and all required gates were satisfied.

### FAIL

The validation completed correctly, but the strategy failed one or more acceptance criteria.

### INVALID

The validation result cannot be trusted because of a methodological or implementation defect.

Examples include:

* data leakage,
* corrupted data,
* incorrect timestamp alignment,
* production/backtest divergence,
* invalid execution assumptions,
* or reproducibility failure.

### INCOMPLETE

The validation did not complete all required tests and therefore cannot be considered evidence for promotion.

Only `PASS` may be used as evidence for progression to the next validation stage.

---

# 16. Core Validation Requirements

### REQ-VAL-001 — Production Equivalence

The official backtest MUST execute the canonical production Data, Feature, Alpha, Risk, and Execution logic wherever applicable.

### REQ-VAL-002 — Determinism

Identical validation inputs MUST produce materially identical outputs.

### REQ-VAL-003 — Temporal Integrity

No decision at timestamp `t` MAY use information unavailable at timestamp `t`.

### REQ-VAL-004 — Dataset Isolation

Official out-of-sample data MUST remain isolated from strategy development until the strategy is frozen.

### REQ-VAL-005 — Configuration Immutability

An official validation run MUST reference immutable configuration and version identifiers.

### REQ-VAL-006 — Full Execution Simulation

Backtests MUST account for the execution mechanics required by the production strategy rather than assuming frictionless fills.

### REQ-VAL-007 — Risk Enforcement

Simulated trading MUST enforce the same production risk constraints defined by Risk & Execution.

### REQ-VAL-008 — Failure Analysis

Official validation MUST evaluate strategy failure behavior, not only aggregate profitability.

### REQ-VAL-009 — Auditability

Every official validation result MUST be traceable to its exact data, code, feature, alpha, risk, and execution configuration.

### REQ-VAL-010 — Promotion Control

A strategy MUST NOT progress toward live deployment without satisfying the required validation gates.

---

# 17. V1 Validation Principle

QuantOS V1 does not attempt to prove that a strategy will always make money.

That standard is impossible.

The objective is narrower and operationally meaningful:

> **Demonstrate that the exact production trading system behaves deterministically, causally, realistically, and robustly enough across historical and live validation stages to justify risking a small amount of real capital.**

The final authority is not the backtest.

The final authority is the complete chain:

```text
Correct Data
    ↓
Correct Features
    ↓
Correct Alpha
    ↓
Correct Risk
    ↓
Realistic Execution
    ↓
Robust Historical Validation
    ↓
Out-of-Sample Evidence
    ↓
Paper Trading
    ↓
Controlled Live Deployment
```

Every stage exists to eliminate a different class of failure.

QuantOS V1 is considered validated only when the entire chain remains coherent.

---

# PART 2 — BACKTESTING ARCHITECTURE & MARKET SIMULATION

## 18. Backtesting Architecture

The QuantOS backtesting system MUST simulate the complete production trading decision loop while replacing only the external exchange interaction with a deterministic historical simulator.

The canonical V1 flow is:

```text
Historical Market Data
        │
        ▼
┌───────────────────┐
│     Data Layer    │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│   Feature Engine  │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│    Alpha Engine   │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Risk & Execution  │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Simulated Exchange│
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Portfolio / State │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Metrics / Audit   │
└───────────────────┘
```

The backtesting environment MUST preserve the same logical ordering as production.

The backtester MUST NOT allow future market information to enter the decision path merely because the complete historical dataset is already available in memory.

---

# 19. Event-Driven Simulation

V1 backtesting MUST use an event-driven model.

The simulator processes historical market events sequentially according to their timestamps.

Conceptually:

```text
event(t0)
    ↓
update market state
    ↓
update features
    ↓
generate alpha
    ↓
evaluate risk
    ↓
generate order
    ↓
simulate execution
    ↓
update portfolio
    ↓
record event

event(t1)
    ↓
...
```

The system MUST NOT calculate the complete future feature matrix and expose it to the strategy as a precomputed unrestricted dataset.

Precomputation MAY be used internally for performance optimization only when temporal access remains strictly controlled.

---

# 20. Simulation Clock

The backtester MUST maintain a single authoritative simulation clock.

The simulation clock determines:

* current market timestamp,
* available market information,
* feature state,
* signal timestamp,
* order timestamp,
* execution timestamp,
* portfolio state,
* and accounting state.

All components participating in the backtest MUST derive temporal state from the simulation clock.

Components MUST NOT independently use wall-clock time during historical simulation.

---

# 21. Event Ordering

When multiple events share or overlap timestamps, QuantOS MUST use a deterministic event ordering.

The ordering MUST prevent an event that occurs later in the simulated market sequence from influencing an earlier decision.

V1 SHOULD use the following conceptual ordering:

```text
1. Market data becomes available
2. Market state is updated
3. Feature state is updated
4. Alpha decision is generated
5. Risk constraints are evaluated
6. Orders are created
7. Orders become eligible for execution
8. Simulated fills occur according to execution rules
9. Portfolio state is updated
10. Accounting and metrics are recorded
```

The implementation MUST document any deviations from this ordering.

---

# 22. Decision Timestamp vs Execution Timestamp

QuantOS MUST distinguish between:

* **decision timestamp**
* **order timestamp**
* **execution timestamp**

These are not necessarily identical.

For example:

```text
09:00:00
Market observation available

09:00:01
Alpha decision generated

09:00:01
Order submitted

09:00:02
Order becomes executable

09:00:03
Simulated fill occurs
```

A backtest that assumes instantaneous observation, decision, submission, and execution risks overstating strategy performance.

The simulator MUST therefore preserve the distinction between information availability and execution.

---

# 23. Historical Data Contract

The Data Layer remains responsible for transforming historical source data into the canonical market-data representation defined in document 003.

The backtester MUST consume canonical data rather than bypassing the Data Layer with strategy-specific parsing.

Each historical dataset MUST have identifiable:

* source,
* symbol,
* timeframe,
* timestamp convention,
* timezone,
* data version,
* ingestion version,
* completeness status,
* and coverage period.

A dataset with unknown provenance MUST NOT be used for an official validation run.

---

# 24. Historical Data Integrity

Before a dataset enters an official backtest, the validation system MUST verify basic integrity.

At minimum:

* timestamps are ordered,
* timestamps are parseable,
* timestamps use the expected timezone convention,
* duplicate records are identified,
* missing records are identified,
* OHLC relationships are valid where applicable,
* prices are positive,
* volumes are non-negative,
* symbol identity is correct,
* and the requested validation period is covered.

The system MUST distinguish between:

1. **expected market gaps**, and
2. **unexpected data gaps**.

A missing candle is not automatically a market event.

It may represent a data-ingestion failure.

---

# 25. Dataset Versioning

Historical datasets MUST be versioned.

A dataset version identifies the exact data used by a validation run.

If historical data is:

* corrected,
* re-ingested,
* backfilled,
* normalized differently,
* or materially modified,

the dataset MUST receive a new version identifier.

Historical results MUST NOT silently change because an underlying dataset was replaced.

The original validation result MUST remain reproducible against the original dataset version.

---

# 26. Market Data Granularity

The backtesting engine MUST operate at a resolution appropriate to the strategy's execution requirements.

For V1, the selected resolution MUST be sufficient to determine:

* signal timing,
* order eligibility,
* position changes,
* stop/exit behavior,
* transaction costs,
* and reasonable execution assumptions.

The system MUST NOT claim execution precision that the historical data cannot support.

For example, if only candle-level data is available, the simulator MUST NOT assume knowledge of the exact intrabar path unless that information is independently available.

---

# 27. OHLC Ambiguity

OHLC candles contain incomplete information about the sequence of intrabar prices.

For a candle:

```text
Open
High
Low
Close
```

the simulator generally does not know whether price moved:

```text
Open → High → Low → Close
```

or:

```text
Open → Low → High → Close
```

or another valid sequence.

Therefore QuantOS MUST NOT fabricate intrabar ordering when it is unknown.

This is particularly important for:

* stop-losses,
* take-profit orders,
* trailing exits,
* multiple simultaneous price levels,
* and limit orders.

When exact intrabar ordering cannot be established, the simulator MUST use an explicitly documented conservative rule.

The rule MUST be deterministic.

---

# 28. Conservative Execution Principle

When historical data cannot determine whether an order would have executed, QuantOS SHOULD favor assumptions that do not artificially improve strategy performance.

Examples include:

* conservative fill prices,
* delayed execution,
* unfavorable ordering when multiple barriers are touched,
* realistic spread assumptions,
* and explicit slippage.

The purpose is not to make the backtest artificially pessimistic.

The purpose is to avoid creating profits from information the simulator does not actually possess.

---

# 29. Simulated Exchange Boundary

The simulated exchange is the primary abstraction separating strategy logic from historical market interaction.

Production:

```text
Risk & Execution
       ↓
Real Exchange
```

Backtest:

```text
Risk & Execution
       ↓
Simulated Exchange
```

The interface SHOULD remain structurally similar.

This allows the same higher-level trading logic to operate against:

* historical simulation,
* paper trading,
* and live execution.

The simulator MUST emulate only the exchange behavior required by V1.

It SHOULD NOT attempt to reproduce every exchange feature.

---

# 30. Order Model

The V1 simulator MUST support the order types required by the production strategy.

At minimum, the architecture MUST be capable of representing:

* market orders,
* limit orders,
* protective exits where required,
* order creation,
* order cancellation,
* order rejection,
* partial fills where applicable,
* filled quantity,
* fill price,
* fees,
* and execution timestamps.

Unsupported order behavior MUST result in an explicit simulation error or rejection rather than silently falling back to an unrealistic assumption.

---

# 31. Market Orders

A market order MUST NOT automatically be modeled as:

```text
fill_price = current_close
```

unless the production decision point explicitly occurs at that close and the execution model supports such an assumption.

The simulator MUST account for the distinction between:

* observed market price,
* decision price,
* executable price,
* and final fill price.

A market order MAY be modeled using:

```text
reference_price
+ spread
+ slippage
```

with the exact model defined by the V1 execution configuration.

The model MUST be deterministic.

---

# 32. Limit Orders

A limit order MUST execute only when the historical market data establishes that the order price was reachable under the selected simulation resolution.

The simulator MUST NOT assume a fill simply because the closing price crossed the limit.

For example, a buy limit at `100` requires evidence that the market traded at or below `100` within the executable interval.

However, when the historical dataset does not establish the order's exact queue position or intrabar sequence, the simulator MUST NOT claim certainty about the actual fill.

The fill model MUST therefore be explicitly documented.

---

# 33. Partial Fills

If V1 does not require detailed order-book simulation, partial fills MAY be simplified.

However, the simplification MUST be explicit.

The simulator MUST NOT simultaneously claim:

* full execution certainty,
* realistic market microstructure,
* and candle-level historical data.

The selected abstraction must match the available data.

For V1, a deterministic full-fill model MAY be used where appropriate, provided that:

* liquidity constraints are respected,
* trade size is sufficiently small relative to assumed market liquidity,
* slippage is included,
* and the limitation is recorded in the validation report.

---

# 34. Fees

Trading fees MUST be included in official backtests.

The fee model MUST be configurable and versioned.

At minimum, the simulator MUST account for:

```text
gross trade value
        ↓
transaction fee
        ↓
net trade value
```

Fees MUST be applied consistently to all applicable executions.

The backtester MUST report:

* gross PnL,
* fees,
* and net PnL.

A strategy that is profitable only before transaction costs MUST NOT be considered production-ready.

---

# 35. Slippage

Slippage MUST be explicitly modeled.

The V1 simulator SHOULD support at least:

```text
fixed slippage
```

and SHOULD allow future extension to:

```text
volatility-dependent slippage
liquidity-dependent slippage
spread-based execution
```

without requiring architectural redesign.

Slippage assumptions MUST be recorded as part of the validation configuration.

The simulator MUST distinguish between:

* theoretical signal price,
* expected execution price,
* and realized simulated fill price.

---

# 36. Stressing Execution Assumptions

Official validation SHOULD evaluate more than one execution assumption.

At minimum, the validation framework SHOULD support:

```text
Base Case
Adverse Slippage Case
Higher Cost Case
```

The purpose is to determine whether strategy profitability depends critically on favorable execution.

A strategy whose edge disappears under modestly worse execution assumptions MUST be flagged as fragile.

---

# 37. Portfolio Accounting

The backtester MUST maintain an explicit portfolio state.

At minimum:

```text
cash
asset quantity
average entry price
realized PnL
unrealized PnL
fees
equity
exposure
```

Portfolio state MUST be updated after every simulated execution.

The system MUST maintain the distinction between:

* realized PnL,
* unrealized PnL,
* and total equity.

---

# 38. Equity Calculation

At simulation timestamp `t`:

```text
Equity(t)
=
Cash(t)
+
MarketValue(Positions, t)
```

The valuation price MUST be defined explicitly.

The same valuation convention MUST be used consistently when calculating:

* equity curves,
* returns,
* drawdown,
* exposure,
* and portfolio statistics.

The simulator MUST NOT use future prices to value historical portfolio states.

---

# 39. Position State

Position state MUST be derived from executed fills rather than intended orders.

For example:

```text
Signal
  ↓
Order
  ↓
Accepted
  ↓
Filled
  ↓
Position Updated
```

An unfilled order MUST NOT change the portfolio position.

A rejected order MUST NOT change the portfolio position.

A cancelled order MUST NOT change the portfolio position.

This distinction is essential for accurate backtesting.

---

# 40. Trade Ledger

Every simulated execution MUST produce an immutable trade record.

A trade/fill record SHOULD contain:

```text
trade_id
timestamp
symbol
side
order_type
requested_quantity
filled_quantity
requested_price
fill_price
fee
slippage
order_id
strategy_version
```

The trade ledger becomes the authoritative source for reconstructing:

* positions,
* PnL,
* fees,
* execution statistics,
* and audit history.

---

# 41. Order Ledger

Orders MUST also be auditable independently from fills.

An order record SHOULD include:

```text
order_id
timestamp
symbol
side
order_type
quantity
price
status
rejection_reason
fill_quantity
average_fill_price
```

This permits analysis of:

* generated orders,
* rejected orders,
* unfilled orders,
* cancelled orders,
* and executed orders.

The system MUST NOT report only successful trades while hiding failed execution attempts.

---

# 42. Simulation Reproducibility

An official backtest MUST be reproducible from its immutable inputs.

At minimum, the following MUST identify a run:

```text
run_id
code_revision
dataset_version
strategy_version
feature_version
risk_version
execution_version
configuration_hash
random_seed
```

If no randomness exists, the seed MAY be recorded as `N/A`.

The validation artifact MUST allow an engineer to determine exactly which system state generated the result.

---

# 43. Backtest Run Lifecycle

A backtest SHOULD follow this lifecycle:

```text
CREATE RUN
    ↓
FREEZE CONFIGURATION
    ↓
LOAD DATASET
    ↓
VALIDATE DATA
    ↓
INITIALIZE STATE
    ↓
RUN SIMULATION
    ↓
GENERATE LEDGERS
    ↓
CALCULATE METRICS
    ↓
RUN VALIDATION CHECKS
    ↓
STORE ARTIFACTS
    ↓
ASSIGN STATUS
```

A failed data-integrity check MUST prevent the simulation from being treated as a valid official run.

---

# 44. Backtest Artifacts

An official run MUST produce sufficient artifacts for investigation.

At minimum:

```text
run metadata
configuration
dataset identity
signals
orders
fills
positions
equity curve
trade ledger
portfolio metrics
validation metrics
warnings
errors
final status
```

Large raw datasets do not necessarily need to be duplicated into every run artifact.

Instead, the run MUST reference an immutable dataset version.

---

# 45. Backtest Logs

The simulator MUST provide structured logs suitable for debugging.

Important events SHOULD include:

* data initialization,
* feature initialization,
* signal generation,
* risk rejection,
* order creation,
* order execution,
* order cancellation,
* portfolio changes,
* validation warnings,
* and fatal simulation errors.

Logs MUST be timestamped using simulation time rather than wall-clock time where the event relates to simulated market activity.

---

# 46. Error Handling

The simulator MUST distinguish between:

### Expected trading events

Examples:

* order rejected by risk,
* insufficient available balance,
* limit order not filled,
* strategy produces no signal.

These are valid simulation outcomes.

### Validation errors

Examples:

* missing required feature,
* invalid timestamp,
* impossible price,
* corrupted portfolio state,
* inconsistent ledger.

These MUST invalidate the run where they can affect result correctness.

### System errors

Examples:

* unexpected exception,
* corrupted state,
* failed persistence,
* unavailable required component.

These MUST terminate the run safely.

The system MUST NOT silently continue after an error that can compromise result integrity.

---

# 47. Backtest Performance Optimization

Performance optimization is permitted provided that it does not alter temporal semantics.

Examples of acceptable optimization include:

* vectorized calculations inside the Feature Engine,
* cached historical data,
* precomputed immutable transformations,
* efficient event indexing,
* batched metric calculations.

Optimization MUST NOT:

* expose future observations,
* reorder causally dependent events,
* bypass risk logic,
* bypass execution logic,
* or alter production behavior.

Correctness takes precedence over simulation speed.

---

# 48. V1 Simulation Boundary

QuantOS V1 deliberately avoids unnecessary microstructure complexity.

The initial simulator does NOT need to model:

* full exchange order books,
* individual queue position,
* market-maker inventory,
* hidden orders,
* latency arbitrage,
* matching-engine internals,
* or sub-millisecond market microstructure.

Those capabilities MAY be introduced later if strategy scale or execution characteristics justify them.

For V1, the simulator must instead provide a **credible, conservative, deterministic approximation** of the execution environment relevant to the actual strategy.

The principle is:

> **Model the risks that can materially change the V1 strategy's result. Do not build complexity that cannot improve the decision.**

---

# 49. Backtesting Acceptance Requirements

### REQ-VAL-011 — Event Driven

The V1 simulator MUST process market events in deterministic chronological order.

### REQ-VAL-012 — Simulation Clock

All simulated decisions MUST use the authoritative simulation clock.

### REQ-VAL-013 — Production Decision Path

Historical simulation MUST execute the canonical Feature, Alpha, Risk, and Execution path.

### REQ-VAL-014 — Data Provenance

Every official run MUST identify the exact historical dataset version used.

### REQ-VAL-015 — Temporal Ordering

The simulator MUST prevent future observations from influencing prior decisions.

### REQ-VAL-016 — Execution Separation

Decision, order, and execution timestamps MUST be represented separately where applicable.

### REQ-VAL-017 — Costs

Official backtests MUST include applicable trading fees.

### REQ-VAL-018 — Slippage

Official backtests MUST include an explicit slippage assumption.

### REQ-VAL-019 — Portfolio Accounting

Portfolio state MUST be derived from actual simulated fills.

### REQ-VAL-020 — Trade Auditability

Every simulated execution MUST be reconstructable from the trade and order ledgers.

### REQ-VAL-021 — Reproducibility

Official validation runs MUST be reproducible from immutable inputs.

### REQ-VAL-022 — Failure Visibility

Simulation errors that can compromise result integrity MUST invalidate or terminate the run rather than being silently ignored.

### REQ-VAL-023 — Conservative Ambiguity

When historical data cannot establish execution ordering, the simulator MUST use a documented deterministic assumption that does not rely on unavailable future information.

### REQ-VAL-024 — Production Promotion

A strategy MUST NOT be considered validated solely because a backtest completes successfully. It MUST satisfy the statistical and robustness requirements defined in subsequent sections of this document.

---

# 50. Part 2 Design Principle

The QuantOS backtester is not a separate trading system.

It is a controlled historical environment around the production trading system.

The architectural goal is:

```text
                 ┌───────────────────────┐
                 │   Production Logic    │
                 │                       │
Historical ────► │ Data → Features      │
Market Data      │       → Alpha        │
                 │       → Risk         │
                 │       → Execution     │
                 └──────────┬────────────┘
                            │
                     Simulated Exchange
                            │
                            ▼
                     Portfolio State
                            │
                            ▼
                    Validation Evidence
```

The simulator therefore exists to answer:

> **“If the exact QuantOS production decision process had encountered this historical market sequence, under explicit and conservative execution assumptions, what would have happened?”**

That question—not an idealized strategy equity curve—is the foundation of QuantOS V1 backtesting.

----

# PART 3 — STATISTICAL VALIDATION, WALK-FORWARD TESTING & ROBUSTNESS

## 51. Purpose

Part 3 defines how QuantOS determines whether historical performance represents a credible trading edge rather than an artifact of:

* overfitting,
* data leakage,
* parameter selection,
* favorable market conditions,
* unrealistic execution,
* insufficient sample size,
* or statistical noise.

The core principle is:

> **A strategy is not validated because it performed well once. It is validated when its behavior remains credible under independent data, changing market conditions, adverse assumptions, and repeated testing.**

Validation MUST therefore examine both:

1. **performance**, and
2. **the stability of the process producing that performance**.

---

# 52. Dataset Partitioning

QuantOS MUST distinguish between three conceptual datasets.

```text
┌──────────────────────┐
│ Development Dataset  │
│                      │
│ Strategy construction│
│ Feature research     │
│ Parameter exploration│
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│ Validation Dataset   │
│                      │
│ Controlled selection │
│ Robustness testing   │
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│ Final Test Dataset   │
│                      │
│ Never used for       │
│ strategy development │
└──────────────────────┘
```

The exact percentage allocation MUST NOT be treated as a universal constant.

The correct split depends on:

* strategy frequency,
* available history,
* market regime coverage,
* parameter count,
* and sample size.

For V1, chronological separation MUST be preferred over random splitting.

---

# 53. Chronological Integrity

Financial time series are inherently temporal.

QuantOS MUST NOT randomly shuffle observations when constructing training and evaluation datasets for the production strategy.

The system MUST preserve chronological ordering.

For example:

```text
2021 ──► 2022 ──► 2023 ──► 2024 ──► 2025 ──► 2026
  │        │        │        │        │        │
  └────────────── Development ─────────┘
                                   └── Test ──┘
```

Future information MUST never influence a decision about an earlier period.

This applies not only to raw prices but also to:

* normalization,
* scaling,
* feature distributions,
* volatility estimates,
* threshold selection,
* model parameters,
* regime classification,
* and hyperparameter selection.

---

# 54. Purging and Embargo

Where training or validation observations overlap temporally through lookback windows, labels, holding periods, or other dependencies, QuantOS MUST prevent contamination between datasets.

For example:

```text
Training observation
       │
       ├──── lookback ────┐
       │                  │
       ▼                  ▼
   information         future label
                          │
                    Validation begins
```

If information from the training period extends into the validation period through feature windows or outcome labels, the affected observations MUST be removed or separated.

Where necessary, an embargo period SHOULD be applied between datasets.

The embargo duration MUST be based on the strategy's maximum information dependency rather than an arbitrary fixed value.

---

# 55. Feature Leakage Prevention

The Feature Engine MUST calculate features as they would exist in production.

For every feature `F` at time `t`:

```text
F(t) = f(X(≤ t))
```

and never:

```text
F(t) = f(X(> t))
```

Examples of leakage include:

* centered rolling averages,
* future-aware normalization,
* using the full dataset to calculate scaling parameters,
* using future volatility,
* using future highs/lows,
* filling missing values using future observations,
* or using future labels indirectly in feature construction.

Any discovered leakage invalidates the affected validation result.

---

# 56. Global Statistics Leakage

Global statistics are especially dangerous.

For example, calculating:

```text
mean = mean(all historical prices)
std  = std(all historical prices)
```

and then using these values to normalize earlier observations introduces future information.

If normalization is required, the transformation MUST be fitted only on information available at the relevant point in time.

The same principle applies to:

* scalers,
* distributions,
* thresholds,
* clustering,
* regime models,
* feature selection,
* and dimensionality reduction.

---

# 57. Parameter Selection

Parameters MUST NOT be optimized against the final out-of-sample dataset.

Parameters may be adjusted using development and controlled validation data.

Once the final strategy configuration is frozen:

```text
strategy_version
feature_version
risk_version
execution_version
```

the final test MUST be executed without further optimization based on its results.

If the final test causes the strategy to be changed, the final test becomes development evidence and MUST no longer be treated as a clean final out-of-sample result.

---

# 58. Parameter Simplicity

QuantOS V1 favors a small number of economically meaningful parameters.

Excessive parameterization increases the probability of fitting historical noise.

For example:

```text
Parameter A
Parameter B
Parameter C
Parameter D
Parameter E
...
Parameter Z
```

may produce an excellent historical fit while having little predictive value.

The preferred structure is:

```text
few parameters
+
clear rationale
+
stable behavior
```

rather than:

```text
many parameters
+
maximum historical performance
```

A parameter MUST have a defensible reason for existing.

---

# 59. Parameter Stability

QuantOS MUST evaluate whether reasonable changes in parameter values materially change strategy behavior.

For example, if a strategy requires:

```text
threshold = 0.7314
```

to produce acceptable performance but fails at:

```text
0.72
0.74
```

the strategy is likely overfit.

A more credible strategy should demonstrate a region of acceptable performance:

```text
       Performance
            ▲
            │
        ███████
      ███████████
    ███████████████
────────────┼────────────► Parameter
            │
         viable
          region
```

The objective is not to find the perfect parameter.

The objective is to identify whether a stable parameter region exists.

---

# 60. Walk-Forward Validation

QuantOS MUST support walk-forward validation.

The fundamental structure is:

```text
Train → Test
      ↓
  Roll Forward
      ↓
Train → Test
      ↓
  Roll Forward
      ↓
Train → Test
```

A simplified example:

```text
Period 1:
[ TRAIN ][ TEST ]

Period 2:
[   TRAIN   ][ TEST ]

Period 3:
[      TRAIN      ][ TEST ]

Period 4:
[          TRAIN          ][ TEST ]
```

Each test period represents information that was not available during the corresponding training/development period.

---

# 61. Rolling vs Expanding Windows

The validation framework SHOULD support both:

### Expanding Window

```text
[────────────── TRAIN ──────────────][TEST]
[──────────────── TRAIN ────────────────][TEST]
[──────────────────── TRAIN ───────────────────][TEST]
```

The training set grows over time.

### Rolling Window

```text
[──── TRAIN ────][TEST]
      [──── TRAIN ────][TEST]
            [──── TRAIN ────][TEST]
```

The training window remains approximately fixed.

The appropriate method depends on the strategy's assumed market-memory horizon.

For V1, expanding-window validation SHOULD be preferred unless there is a strong reason to assume older information becomes structurally irrelevant.

---

# 62. Walk-Forward Outputs

Every walk-forward segment MUST produce independent results.

At minimum:

```text
window_id
training_period
test_period
strategy_configuration
trade_count
return
PnL
maximum_drawdown
fees
slippage
```

The system MUST preserve individual window results rather than reporting only the aggregate.

This allows QuantOS to determine whether profitability comes from:

```text
many moderately successful periods
```

or:

```text
one exceptional period
+
many weak periods
```

These are fundamentally different strategies.

---

# 63. Cross-Regime Validation

A strategy MUST be evaluated across materially different market conditions.

For V1 crypto validation, the historical dataset SHOULD include periods representing conditions such as:

* strong bull trends,
* strong bear trends,
* prolonged sideways markets,
* high-volatility periods,
* low-volatility periods,
* sharp reversals,
* liquidity stress,
* and major market shocks where available.

The purpose is not to guarantee profitability in every regime.

The purpose is to understand:

> **Which regimes does the strategy depend on, and how does it fail outside them?**

---

# 64. Regime Attribution

Performance SHOULD be attributable to observable market conditions.

For example:

```text
Regime          Return     Drawdown
-----------------------------------
Trending Bull     +X%       -Y%
Trending Bear     +X%       -Y%
Sideways          -X%       -Y%
High Volatility   +X%       -Y%
Low Volatility    -X%       -Y%
```

This allows the Alpha Engine to be understood as a conditional system rather than a single aggregate number.

If a strategy produces nearly all of its historical profitability from one narrow regime, that dependency MUST be explicitly documented.

---

# 65. Minimum Sample Size

Performance statistics based on very few trades are unreliable.

QuantOS MUST therefore report sample size alongside performance.

At minimum:

* total trades,
* winning trades,
* losing trades,
* and active trading periods

MUST be reported.

The system SHOULD flag statistically weak results when the number of observations is insufficient to support meaningful conclusions.

No arbitrary minimum trade count should be interpreted as a universal proof threshold.

The required sample size depends on:

* strategy frequency,
* return distribution,
* holding period,
* variance,
* and evaluation horizon.

---

# 66. Core Performance Metrics

Every official backtest MUST calculate, where applicable:

### Return

```text
Return = (Final Equity / Initial Equity) - 1
```

### Net PnL

```text
Net PnL =
Gross PnL
- Fees
- Execution Costs
- Slippage Impact
```

### Maximum Drawdown

For equity `E(t)`:

```text
Peak(t) = max(E(τ)), τ ≤ t

Drawdown(t) =
(E(t) / Peak(t)) - 1

Max Drawdown =
min(Drawdown(t))
```

### Trade Count

Total number of completed trades.

### Win Rate

```text
Win Rate =
Winning Trades / Total Closed Trades
```

Win rate MUST NOT be interpreted independently from payoff distribution.

---

# 67. Expectancy

The backtester MUST calculate trade expectancy where sufficient trade-level data exists.

Conceptually:

```text
Expectancy =
P(win) × Average Win
-
P(loss) × Average Loss
```

A strategy can have a low win rate and still have positive expectancy.

Likewise, a strategy can have a high win rate and still lose money.

Expectancy therefore provides more useful information than win rate alone.

---

# 68. Profit Factor

Where applicable:

```text
Profit Factor =
Gross Winning PnL
/
Gross Losing PnL
```

A value above `1.0` indicates gross winning PnL exceeds gross losing PnL.

Profit factor MUST always be interpreted alongside:

* trade count,
* drawdown,
* costs,
* and out-of-sample behavior.

A high profit factor based on very few trades is weak evidence.

---

# 69. Risk-Adjusted Metrics

The validation framework SHOULD support standard risk-adjusted metrics such as:

* Sharpe ratio,
* Sortino ratio,
* Calmar ratio.

These metrics MUST be calculated from clearly defined return series and annualization assumptions.

QuantOS MUST NOT present a risk-adjusted metric without documenting:

* sampling frequency,
* annualization factor,
* treatment of cash,
* and treatment of missing periods.

Metrics are measurement tools, not acceptance criteria by themselves.

---

# 70. Drawdown Analysis

Maximum drawdown is necessary but insufficient.

QuantOS SHOULD additionally measure:

* drawdown duration,
* average drawdown,
* number of drawdown periods,
* recovery duration,
* worst daily/periodic loss,
* and loss clustering.

Example:

```text
Equity
  ▲
  │       peak
  │      /\
  │     /  \
  │    /    \________
  │   /               \
  │__/                 \____
  │
  └──────────────────────────► time

       ← drawdown →
       ← recovery ──→
```

A strategy with a tolerable maximum drawdown but extremely long recovery periods may still be unsuitable for V1.

---

# 71. Tail Loss Analysis

QuantOS MUST inspect the distribution of losses rather than relying only on average behavior.

The validation report SHOULD include:

* worst trade,
* worst day,
* worst period,
* largest consecutive loss sequence,
* and tail loss concentration.

This is especially important for crypto markets where return distributions can be highly non-normal.

---

# 72. Loss Clustering

Losses that occur independently are materially different from losses that cluster during a market event.

QuantOS SHOULD identify:

* consecutive losing trades,
* consecutive losing periods,
* high-loss clusters,
* and drawdown acceleration.

The objective is to determine whether the strategy's risk model can tolerate realistic adverse sequences.

---

# 73. Cost Sensitivity

A strategy MUST be tested against changes in transaction costs.

For example:

```text
Scenario A — Base Fees
Scenario B — 1.5× Fees
Scenario C — 2× Fees
```

The exact stress levels MAY be configurable.

The purpose is to determine whether the strategy's edge is large enough to survive reasonable cost deterioration.

A strategy whose profitability disappears under modest cost increases MUST be flagged as execution-sensitive.

---

# 74. Slippage Sensitivity

The same principle applies to slippage.

At minimum, validation SHOULD compare:

```text
Base Slippage
Moderately Adverse Slippage
Severely Adverse Slippage
```

The strategy SHOULD remain economically credible under reasonable adverse assumptions.

A strategy that requires near-perfect execution MUST NOT be considered robust.

---

# 75. Parameter Perturbation

Validation SHOULD perturb non-structural strategy parameters within reasonable ranges.

For example:

```text
Baseline
-5%
-2.5%
+2.5%
+5%
```

The exact ranges depend on parameter meaning.

The objective is to identify:

```text
stable strategy
```

versus:

```text
knife-edge strategy
```

A knife-edge strategy requires additional evidence before deployment.

---

# 76. Bootstrap and Resampling

Where appropriate, QuantOS MAY use bootstrap or resampling methods to estimate uncertainty around observed performance.

For example, trade-level resampling may help estimate the range of possible outcomes given the observed trade distribution.

However, resampling MUST NOT be treated as equivalent to genuinely independent future market data.

Bootstrap analysis answers:

> “How uncertain is the observed sample?”

It does not answer:

> “Will the market behave the same way in the future?”

Both questions remain distinct.

---

# 77. Monte Carlo Analysis

The validation framework MAY support Monte Carlo analysis over trade sequences.

Possible analyses include:

* randomized trade ordering,
* slippage perturbation,
* execution perturbation,
* return perturbation,
* and drawdown distribution estimation.

The objective is to estimate the range of plausible outcomes.

For example:

```text
Historical sequence:
+ + - + - - + + - ...

Possible reordered sequences:
- + + - + - + - + ...
+ - - + + - + + - ...
```

This can reveal whether observed historical performance depends heavily on a favorable sequence of returns.

Monte Carlo results MUST NOT be presented as forecasts.

They are stress-analysis tools.

---

# 78. Benchmark Comparison

Strategy performance SHOULD be compared against appropriate passive or simple benchmarks.

For BTCUSDT and ETHUSDT, relevant comparisons may include:

* buy-and-hold,
* cash/no-trade baseline,
* simple trend baseline,
* or other explicitly defined reference strategies.

The purpose is not to require the Alpha Engine to outperform every benchmark.

The purpose is to determine whether the complexity of the strategy produces sufficient incremental value.

A strategy that generates significant complexity but produces no meaningful improvement over a trivial benchmark requires justification.

---

# 79. Exposure Analysis

The validation system MUST report exposure behavior.

At minimum:

* average exposure,
* maximum exposure,
* time in market,
* long exposure,
* short exposure where supported,
* and idle/cash time.

For V1 Spot trading, the system MUST respect the fact that short exposure may not be available through the selected production execution environment.

Historical validation MUST NOT assume capabilities unavailable to production.

---

# 80. Turnover Analysis

The validation system SHOULD calculate turnover.

High turnover can create:

* larger fees,
* larger slippage,
* execution sensitivity,
* and operational complexity.

Turnover MUST therefore be evaluated alongside gross strategy edge.

A strategy with a small gross edge and extreme turnover should be treated as fragile.

---

# 81. Capacity Awareness

V1 is designed for small capital deployment.

The backtester MUST NOT assume that historical execution behavior scales indefinitely with capital.

For larger future deployments, capacity analysis MAY need to incorporate:

* liquidity,
* order-book depth,
* participation rate,
* market impact,
* and execution latency.

V1 does not require full institutional capacity modeling.

However, the validation framework MUST preserve the ability to introduce these constraints later.

---

# 82. Benchmarking Strategy Complexity

QuantOS SHOULD evaluate the relationship between:

```text
strategy complexity
        vs
incremental performance
```

Additional:

* features,
* parameters,
* models,
* filters,
* or execution rules

should be justified by measurable improvement in robustness rather than only improvement in in-sample return.

The preferred V1 strategy is the simplest strategy that satisfies the required validation gates.

---

# 83. Multiple Testing Risk

Repeatedly testing many strategy variations increases the probability that one appears profitable by chance.

For example:

```text
Strategy A → FAIL
Strategy B → FAIL
Strategy C → FAIL
...
Strategy N → PASS
```

A single PASS among a large number of experiments may represent selection bias rather than genuine edge.

QuantOS SHOULD record material strategy experiments where practical.

The validation process MUST recognize that:

> **The more hypotheses tested, the stronger the evidence required for the final strategy.**

The system SHOULD preserve experiment lineage so the final strategy's development history can be reconstructed.

---

# 84. Strategy Version Lineage

Every strategy MUST have a lineage.

Conceptually:

```text
Alpha v0.1
   ↓
Alpha v0.2
   ↓
Alpha v0.3
   ↓
Alpha v1.0
   ↓
Validation Candidate
```

A production candidate MUST identify:

* parent version,
* changes,
* rationale,
* affected features,
* affected parameters,
* and validation status.

This prevents accidental loss of experimental history.

---

# 85. Validation Evidence Hierarchy

Not all evidence carries equal weight.

QuantOS SHOULD interpret evidence approximately in the following hierarchy:

```text
Exploratory Backtest
        ↓
In-Sample Performance
        ↓
Controlled Validation
        ↓
Walk-Forward Testing
        ↓
Final Out-of-Sample Test
        ↓
Paper Trading
        ↓
Live Trading
```

Evidence becomes more valuable as it becomes:

* more independent,
* more realistic,
* less influenced by development,
* and closer to actual production conditions.

---

# 86. Promotion Gates

A strategy MUST satisfy explicit gates before progressing.

### Gate A — Technical Validity

Required:

* no known data leakage,
* deterministic execution,
* reproducible results,
* valid accounting,
* valid feature calculations,
* valid order simulation.

Failure blocks promotion.

### Gate B — Historical Robustness

Required:

* acceptable drawdown,
* positive net economics after costs,
* sufficient sample size,
* acceptable loss behavior,
* reasonable parameter stability.

Failure blocks promotion.

### Gate C — Walk-Forward Robustness

Required:

* strategy remains credible across multiple test windows,
* performance is not dependent on one isolated period,
* and major regime changes do not produce unacceptable behavior.

Failure blocks promotion.

### Gate D — Final Out-of-Sample

Required:

* final test remains economically viable,
* no material implementation anomalies,
* no unexpected risk violations.

Failure blocks promotion.

### Gate E — Paper Trading

Required:

* production pipeline executes correctly,
* live data ingestion is stable,
* signals match expectations,
* risk controls behave correctly,
* execution reconciliation works,
* and operational monitoring is functional.

Failure blocks live deployment.

---

# 87. Acceptance Criteria Philosophy

QuantOS MUST NOT define success as:

```text
"Backtest made money."
```

Instead:

```text
Data integrity
AND
Temporal integrity
AND
Production equivalence
AND
Positive net economics
AND
Acceptable drawdown
AND
Robustness
AND
Out-of-sample credibility
AND
Operational correctness
```

must collectively support promotion.

Exact numerical thresholds SHOULD be defined as V1 configuration rather than hard-coded into the architecture.

This permits thresholds to evolve without redesigning the validation engine.

---

# 88. Validation Report Structure

Every official validation report SHOULD contain:

```text
1. Run Identity
2. Strategy Identity
3. Dataset Identity
4. Configuration
5. Data Integrity Results
6. Backtest Results
7. Trade Statistics
8. Risk Statistics
9. Cost Analysis
10. Walk-Forward Results
11. Out-of-Sample Results
12. Regime Analysis
13. Robustness Tests
14. Failure Analysis
15. Benchmark Comparison
16. Warnings
17. Promotion Gate Results
18. Final Status
```

The report MUST distinguish measured facts from interpretation.

---

# 89. Validation Result Classification

A strategy MAY be classified as:

### `ROBUST`

Evidence remains credible across independent periods, reasonable execution stress, parameter perturbation, and out-of-sample testing.

### `CONDITIONAL`

The strategy is viable only under clearly identified conditions or regimes.

Conditional strategies MAY require additional risk controls before deployment.

### `FRAGILE`

Small changes in assumptions, parameters, costs, or market conditions materially degrade performance.

Fragile strategies SHOULD NOT be promoted to live trading without substantial additional evidence.

### `INVALID`

The validation process contains a methodological or implementation defect.

Invalid results MUST NOT be used as evidence.

---

# 90. Failure Analysis

Every failed validation MUST answer:

1. **What failed?**
2. **When did it fail?**
3. **Why did it fail?**
4. **Was the failure expected?**
5. **Did Risk & Execution contain the failure?**
6. **Can the failure be corrected without overfitting?**
7. **Does the correction require a new strategy version?**

A failure MUST NOT automatically trigger parameter optimization.

Sometimes the correct conclusion is:

> **The strategy does not work.**

That conclusion is a successful outcome of the validation process.

---

# 91. No Backtest Optimization Loop

The validation framework MUST prevent the following cycle from being treated as independent evidence:

```text
Backtest
   ↓
Observe failure
   ↓
Change parameters
   ↓
Backtest again
   ↓
Repeat
   ↓
Select best result
```

This process is useful during research.

It is not valid as final validation evidence.

Once the final validation dataset is designated as out-of-sample, repeated optimization against it invalidates its independence.

---

# 92. Robustness Over Maximum Return

When choosing between candidate strategy configurations, QuantOS SHOULD prefer:

```text
lower return
+
lower drawdown
+
stable parameters
+
stable regimes
+
stable execution
```

over:

```text
maximum historical return
+
high sensitivity
+
large drawdown
+
execution dependence
```

The V1 objective is survival and repeatability.

Maximum theoretical return is not the optimization target.

---

# 93. Validation Acceptance Requirements

### REQ-VAL-025 — Chronological Splitting

Official validation datasets MUST preserve chronological ordering.

### REQ-VAL-026 — Dataset Isolation

The final out-of-sample dataset MUST remain isolated from strategy development.

### REQ-VAL-027 — Leakage Prevention

Feature and model transformations MUST NOT use information unavailable at the decision timestamp.

### REQ-VAL-028 — Walk-Forward Testing

The validation framework MUST support walk-forward evaluation.

### REQ-VAL-029 — Window-Level Results

Walk-forward results MUST be retained independently rather than only as an aggregate.

### REQ-VAL-030 — Cost Sensitivity

Official validation SHOULD test reasonable increases in transaction costs.

### REQ-VAL-031 — Slippage Sensitivity

Official validation SHOULD test reasonable adverse execution assumptions.

### REQ-VAL-032 — Parameter Stability

Important strategy parameters SHOULD be evaluated for sensitivity.

### REQ-VAL-033 — Regime Analysis

Validation SHOULD evaluate performance across materially different market conditions.

### REQ-VAL-034 — Sample Size

Performance results MUST report sufficient trade and time-period statistics to contextualize their reliability.

### REQ-VAL-035 — Drawdown Analysis

Validation MUST report maximum drawdown and SHOULD report drawdown duration and recovery behavior.

### REQ-VAL-036 — Trade-Level Analysis

Validation MUST preserve trade-level information sufficient to reconstruct PnL and analyze loss behavior.

### REQ-VAL-037 — Experiment Awareness

The validation process SHOULD preserve material experiment lineage to reduce multiple-testing bias.

### REQ-VAL-038 — Benchmarking

Strategy evaluation SHOULD include an appropriate baseline where one exists.

### REQ-VAL-039 — Promotion Gates

A strategy MUST satisfy all required validation gates before progressing toward live deployment.

### REQ-VAL-040 — Failure Classification

Validation failures MUST be classified as methodological, implementation, statistical, execution, or strategy failures where possible.

---

# 94. V1 Validation Standard

The V1 standard can be summarized as:

```text
                ┌─────────────────┐
                │ Correct System  │
                └────────┬────────┘
                         ↓
                ┌─────────────────┐
                │ Correct History │
                └────────┬────────┘
                         ↓
                ┌─────────────────┐
                │ No Leakage      │
                └────────┬────────┘
                         ↓
                ┌─────────────────┐
                │ Walk Forward    │
                └────────┬────────┘
                         ↓
                ┌─────────────────┐
                │ Robustness      │
                └────────┬────────┘
                         ↓
                ┌─────────────────┐
                │ OOS Test        │
                └────────┬────────┘
                         ↓
                ┌─────────────────┐
                │ Paper Trading   │
                └────────┬────────┘
                         ↓
                ┌─────────────────┐
                │ Small Live      │
                └─────────────────┘
```

The goal is not to manufacture confidence.

The goal is to eliminate reasons **not** to trust the system.

---

# 95. Part 3 Design Principle

The most important property of QuantOS validation is independence.

A strategy should not be trusted because QuantOS can repeatedly find a configuration that worked in the past.

It should be trusted only when:

```text
the strategy was defined
        ↓
tested on unseen data
        ↓
tested across different periods
        ↓
tested under adverse assumptions
        ↓
tested with realistic execution
        ↓
tested without future information
        ↓
and continued to behave acceptably
```

The central V1 rule is therefore:

> **Optimize during research. Validate without optimization.**

This distinction protects the system from turning historical data into a source of false certainty.

----

# PART 4 — PAPER TRADING, LIVE PROMOTION & VALIDATION GOVERNANCE

## 96. Purpose

Historical validation establishes whether the QuantOS strategy is credible under controlled historical conditions.

It does not establish that the complete production system is operationally ready.

Part 4 defines the final validation stages:

```text id="qv9m2s"
Historical Validation
        ↓
Out-of-Sample Validation
        ↓
Paper Trading
        ↓
Controlled Live Deployment
        ↓
V1 Production
```

The purpose of this stage is to verify that:

* the production implementation matches the validated system,
* live market data behaves as expected,
* signals are generated correctly,
* risk controls operate correctly,
* orders are handled correctly,
* portfolio accounting reconciles,
* operational failures are contained,
* and real-world execution remains consistent with validation assumptions.

The objective is not to prove profitability before going live.

The objective is to ensure that QuantOS can safely operate with real capital.

---

# 97. Paper Trading

Paper trading is the first environment in which QuantOS interacts with live market conditions without risking production capital.

The paper-trading environment MUST use:

* live market data,
* production Data Layer,
* production Feature Engine,
* production Alpha Engine,
* production Risk & Execution,
* production monitoring,
* and production state management.

The primary substitution is:

```text id="j3r5xk"
Real Exchange
     ↓
Paper Execution / Simulator
```

The strategy itself MUST NOT be modified simply because the environment changes from historical to paper trading.

---

# 98. Paper Trading Objectives

Paper trading MUST validate operational behavior rather than merely reproduce backtest performance.

At minimum, paper trading MUST verify:

* market-data ingestion,
* timestamp handling,
* feature generation,
* signal generation,
* risk decisions,
* order creation,
* order state transitions,
* simulated execution,
* portfolio accounting,
* PnL calculation,
* position state,
* monitoring,
* persistence,
* restart behavior,
* and error handling.

Paper trading is therefore an integration test against the live market.

---

# 99. Paper Trading Duration

There MUST NOT be a universal requirement that paper trading last a fixed number of days.

The required duration depends on:

* strategy frequency,
* expected trade count,
* market regime,
* system stability,
* and observed operational failures.

The paper phase SHOULD continue until QuantOS has accumulated sufficient evidence that the production pipeline behaves correctly.

A strategy producing no trades during paper trading cannot be considered operationally validated merely because the system remained online.

---

# 100. Paper Trading Evidence

Paper trading MUST produce evidence comparable to live operation.

At minimum:

```text id="s6v2da"
signals
orders
fills
positions
portfolio state
equity
PnL
fees
execution assumptions
system health
errors
warnings
reconciliation status
```

The system SHOULD compare paper behavior against the expected behavior from the validated strategy configuration.

Unexpected divergence MUST be investigated.

---

# 101. Backtest-to-Paper Comparison

QuantOS SHOULD compare the behavior of the same strategy under historical and live conditions.

The objective is not to require identical returns.

The objective is to identify implementation or execution discrepancies.

Examples:

```text id="o0h2s8"
Expected:
Signal at T

Paper:
Signal at T + Δ
```

or:

```text id="j7t9mc"
Expected order quantity:
0.010 BTC

Paper order quantity:
0.007 BTC
```

or:

```text id="4e3w5c"
Expected risk decision:
ALLOW

Paper risk decision:
REJECT
```

Differences MUST be explainable.

Unexplained divergence blocks promotion.

---

# 102. Live Data Validation

The live Data Layer MUST be validated independently.

The system MUST detect:

* stale market data,
* missing updates,
* duplicate events,
* timestamp anomalies,
* unexpected symbol changes,
* malformed records,
* and connection failures.

A strategy MUST NOT continue trading normally when required market data is stale or invalid.

This requirement connects directly to the Data Layer defined in document 003.

---

# 103. Feature Validation in Production

The Feature Engine MUST expose enough observability to determine whether live features are being calculated correctly.

For critical features, QuantOS SHOULD be able to inspect:

```text id="p1x1yx"
raw input
      ↓
intermediate state
      ↓
feature value
      ↓
timestamp
```

A production feature MUST correspond to the same definition used during validation.

A feature implementation change requires a new feature version and appropriate revalidation.

---

# 104. Signal Validation

The Alpha Engine MUST produce auditable decisions.

For each actionable signal, the system SHOULD retain:

```text id="t9q1f6"
timestamp
symbol
feature snapshot/reference
signal direction
signal strength
decision
strategy version
```

The exact internal feature representation need not always be persisted in full, but enough information MUST exist to reconstruct why the signal occurred.

The Alpha Engine MUST NOT silently change behavior between validation and production.

---

# 105. Risk Validation

Risk & Execution remains the final authority over whether an intended strategy action can become an executable order.

The following relationship MUST remain intact:

```text id="m8u8ad"
Alpha
  ↓
Intent
  ↓
Risk
  ↓
Approved / Rejected
  ↓
Execution
```

The Alpha Engine MUST NOT bypass Risk & Execution.

Paper trading MUST explicitly test:

* position limits,
* exposure limits,
* available capital,
* duplicate order prevention,
* cooldowns where applicable,
* emergency stops,
* and other V1 risk controls.

---

# 106. Execution Reconciliation

Where paper or live execution occurs, QuantOS MUST reconcile:

```text id="c7ik2b"
Internal Order
      ↕
Exchange Order
      ↕
Exchange Fill
      ↕
Internal Position
      ↕
Internal Portfolio
```

Any mismatch MUST be detectable.

Examples include:

* missing fills,
* unexpected quantities,
* unexpected prices,
* stale order state,
* duplicated orders,
* or position mismatches.

Reconciliation failure MUST be treated as a production-risk event.

---

# 107. Position Reconciliation

The internal QuantOS position MUST periodically reconcile against the authoritative external account state.

Conceptually:

```text id="u8cx3w"
Internal Position
        │
        ├──── compare ────► External Position
        │
        ▼
     MATCH?
      /   \
    YES    NO
     │      │
 continue   halt / investigate
```

The exact recovery procedure is defined in Risk & Execution and implementation documentation.

The important invariant is:

> **QuantOS must know when its internal belief about the portfolio differs from the actual portfolio.**

---

# 108. Restart Validation

QuantOS MUST be able to recover from process interruption without corrupting portfolio state.

Restart testing SHOULD verify:

* state persistence,
* order recovery,
* fill recovery,
* position recovery,
* risk state recovery,
* feature state recovery where required,
* and correct continuation of the trading loop.

A restart MUST NOT create:

* duplicate orders,
* duplicate positions,
* missing fills,
* or incorrect PnL.

---

# 109. Failure Recovery

Production validation MUST include controlled failure tests.

Examples:

```text id="a9z3uj"
Market data disconnect
Exchange API timeout
Order submission timeout
Process restart
Database/storage failure
Malformed market event
Unexpected exchange response
```

The system MUST fail safely.

The desired behavior is generally:

```text id="3d8o7v"
uncertainty
    ↓
reduce activity
    ↓
preserve capital
    ↓
recover
    ↓
reconcile
    ↓
resume only when safe
```

The system MUST NOT assume that an unknown order state means the order did not execute.

---

# 110. Kill Switch

QuantOS V1 MUST provide an explicit emergency stop mechanism.

The kill switch MUST prevent new trading activity.

Depending on the failure mode, it MAY also require:

* cancellation of open orders,
* position reduction,
* manual intervention,
* or complete strategy shutdown.

The exact behavior belongs to Risk & Execution.

Validation MUST demonstrate that the mechanism actually works.

A kill switch that exists only as code but has never been tested is not considered validated.

---

# 111. Capital Isolation

V1 live deployment MUST use deliberately limited capital.

The live capital allocation MUST be treated as a validation budget rather than as the maximum amount the strategy could theoretically trade.

The purpose is to validate:

* execution,
* accounting,
* operational behavior,
* real transaction costs,
* and real market interaction

while limiting the consequence of unknown defects.

Capital SHOULD increase only after successful evidence accumulation.

---

# 112. Controlled Live Deployment

The first live deployment MUST be treated as an additional validation stage.

The recommended progression is:

```text id="q4ap9h"
Paper
  ↓
Minimal Live Capital
  ↓
Observe
  ↓
Reconcile
  ↓
Validate
  ↓
Increase Capital Carefully
```

The initial capital SHOULD be small enough that an unexpected system failure does not materially threaten the overall project.

The exact amount belongs to deployment configuration rather than this architectural document.

---

# 113. Live Shadow Comparison

During initial deployment, QuantOS SHOULD maintain a comparison between:

```text id="t75qdf"
Expected Simulation / Reference Behavior
              vs
Actual Live Behavior
```

Differences SHOULD be categorized as:

* market movement,
* execution difference,
* slippage,
* fees,
* timing,
* data discrepancy,
* strategy discrepancy,
* or system error.

This creates a feedback loop between validation and production.

---

# 114. Live Execution Metrics

The live system SHOULD track:

* expected fill price,
* actual fill price,
* slippage,
* fees,
* execution latency,
* rejected orders,
* cancelled orders,
* partial fills,
* and reconciliation errors.

These measurements SHOULD feed future validation assumptions.

For example:

```text id="f4y0kz"
Backtest Slippage Assumption
          ↓
Paper Observation
          ↓
Live Observation
          ↓
Updated Execution Model
          ↓
Future Validation
```

This creates an empirical execution model rather than relying indefinitely on assumptions.

---

# 115. Validation Feedback Loop

Validation is not a one-time event.

QuantOS SHOULD operate a controlled feedback loop:

```text id="bqj6ro"
Research
   ↓
Validation
   ↓
Paper
   ↓
Live
   ↓
Production Evidence
   ↓
Research / Model Revision
   ↓
New Version
   ↓
Revalidation
```

Any material strategy or infrastructure change MUST create a new version and restart the relevant validation process.

---

# 116. What Requires Revalidation

A new validation cycle MUST be triggered by material changes.

Examples include:

* Alpha Engine logic changes,
* Feature Engine logic changes,
* feature definitions,
* strategy parameters,
* risk limits,
* execution model,
* order behavior,
* market-data processing,
* timeframe,
* symbol universe,
* exchange,
* or material infrastructure changes affecting trading behavior.

Purely non-functional changes MAY avoid full strategy revalidation if they are proven behaviorally equivalent.

The determination MUST be documented.

---

# 117. Validation Configuration

V1 validation thresholds MUST be configuration-driven.

The configuration SHOULD include:

```text id="2h0p3w"
minimum_sample_size
maximum_drawdown
minimum_expectancy
minimum_profit_factor
maximum_cost_sensitivity
maximum_slippage_sensitivity
walk_forward_requirements
paper_trading_requirements
live_promotion_requirements
```

Exact thresholds MUST be treated as explicit V1 policy.

They MUST NOT be hidden inside arbitrary implementation logic.

---

# 118. Threshold Philosophy

QuantOS SHOULD avoid optimizing acceptance thresholds around a single historical strategy.

Thresholds exist to protect the system.

They should therefore be:

* simple,
* understandable,
* conservative,
* measurable,
* and difficult to game.

For example, a threshold SHOULD answer:

> “What level of drawdown are we willing to tolerate?”

rather than:

> “What drawdown produces the highest historical return?”

The first is risk governance.

The second is optimization.

---

# 119. V1 Promotion Checklist

Before a strategy reaches live capital, the following checklist MUST be satisfied.

### Technical

* [ ] Code version frozen
* [ ] Configuration version frozen
* [ ] Dataset version recorded
* [ ] Feature version recorded
* [ ] Alpha version recorded
* [ ] Risk version recorded
* [ ] Execution version recorded
* [ ] Backtest reproducible

### Historical Validation

* [ ] No known leakage
* [ ] Data integrity passed
* [ ] Production path verified
* [ ] Costs included
* [ ] Slippage included
* [ ] Walk-forward validation passed
* [ ] Out-of-sample validation passed
* [ ] Robustness tests completed
* [ ] Failure analysis completed

### Paper Trading

* [ ] Live data stable
* [ ] Signals correct
* [ ] Risk controls verified
* [ ] Orders simulated correctly
* [ ] Portfolio accounting correct
* [ ] Reconciliation correct
* [ ] Restart behavior verified
* [ ] Failure recovery verified

### Live Readiness

* [ ] Kill switch tested
* [ ] Capital limit configured
* [ ] Monitoring active
* [ ] Alerts active
* [ ] Exchange credentials verified
* [ ] Order reconciliation verified
* [ ] Emergency procedures documented
* [ ] Promotion decision recorded

---

# 120. Promotion Decision

A live promotion MUST be an explicit decision.

The system SHOULD produce a promotion record containing:

```text id="4s0h5j"
strategy_version
validation_run_id
paper_run_id
code_revision
configuration_hash
decision
decision_timestamp
decision_owner
reason
```

Allowed decisions:

```text id="qzlybj"
APPROVE
REJECT
DEFER
```

No implicit promotion is allowed.

A strategy finishing a backtest or paper session MUST NOT automatically become live.

---

# 121. Rejection Is a Valid Outcome

QuantOS MUST treat rejection as a successful validation outcome when evidence indicates that deployment is unsafe.

Examples:

```text id="p5x8q1"
Backtest profitable
BUT
OOS failed
→ REJECT
```

or:

```text id="0t3o5b"
Historical validation passed
BUT
paper reconciliation failed
→ REJECT
```

or:

```text id="w0x7r8"
Strategy profitable
BUT
drawdown exceeds risk budget
→ REJECT
```

The purpose of validation is to prevent capital from being deployed when evidence is insufficient.

---

# 122. Validation Audit Trail

All official validation decisions MUST be auditable.

The audit trail SHOULD preserve:

* run identifiers,
* configuration hashes,
* code revisions,
* datasets,
* metrics,
* warnings,
* failures,
* promotion decisions,
* and operator decisions.

Historical validation results MUST NOT be silently overwritten.

Corrections SHOULD create a new record rather than modifying the original evidence.

---

# 123. Reproducibility Standard

An engineer starting from a clean environment SHOULD be able to reproduce an official validation result using:

```text id="4n8j7a"
Code Revision
+
Dataset Version
+
Configuration
+
Run Metadata
```

The resulting:

* signals,
* orders,
* fills,
* positions,
* equity curve,
* and primary metrics

SHOULD match the original result within explicitly documented numerical tolerances.

If reproducibility fails, the validation result MUST be flagged.

---

# 124. Numerical Tolerances

Floating-point calculations may introduce tiny differences across:

* hardware,
* libraries,
* execution environments,
* or numerical implementations.

QuantOS MAY therefore define explicit tolerances for reproducibility.

The tolerance MUST be:

* documented,
* small,
* deterministic,
* and insufficient to conceal meaningful trading differences.

Large discrepancies MUST NOT be dismissed as floating-point variation.

---

# 125. Validation Environment

The official validation environment SHOULD be reproducible.

The implementation SHOULD record:

* operating environment,
* dependency versions,
* runtime version,
* configuration,
* code revision,
* and relevant system information.

Containerization or environment locking MAY be used where useful.

The objective is not infrastructure complexity.

The objective is the ability to recreate the environment that generated an important result.

---

# 126. Validation Artifacts

The minimum official artifact set SHOULD contain:

```text id="f3k0p7"
validation/
├── run.json
├── configuration.json
├── dataset.json
├── metrics.json
├── signals.*
├── orders.*
├── fills.*
├── positions.*
├── equity.*
├── validation_report.*
├── warnings.log
└── errors.log
```

The exact serialization format belongs to implementation documentation.

The conceptual requirement is that the result remains inspectable and reproducible.

---

# 127. Separation of Evidence and Presentation

Performance dashboards MUST NOT become the authoritative validation record.

Charts and dashboards are presentation layers.

The authoritative evidence consists of:

* immutable run metadata,
* raw ledgers,
* metrics,
* configuration,
* and validation status.

This prevents a dashboard from becoming the only source of truth.

---

# 128. Monitoring After Promotion

Validation does not end at live deployment.

The live system MUST monitor whether observed behavior remains within the validated envelope.

Important monitoring dimensions include:

* realized drawdown,
* trade frequency,
* execution cost,
* slippage,
* signal frequency,
* exposure,
* order rejection,
* reconciliation failures,
* and system health.

The system SHOULD compare live observations against expected validation ranges.

---

# 129. Live Drift Detection

QuantOS SHOULD detect material deviations from validated behavior.

Potential drift indicators include:

```text id="g6e7q8"
Trade frequency ↑↑
Slippage ↑↑
Win rate ↓
Drawdown ↑
Exposure ↑
Execution latency ↑
Data quality ↓
```

A drift signal does not automatically mean the strategy is broken.

It means investigation is required.

The response SHOULD be governed by Risk & Execution.

---

# 130. Automatic Degradation Controls

Where practical, QuantOS SHOULD support automated protective responses to severe operational or risk deviations.

Examples:

```text id="3y1d8n"
Normal
  ↓
Warning
  ↓
Restricted Trading
  ↓
Kill Switch
```

The thresholds MUST be explicit and versioned.

Automatic controls MUST be conservative.

A false positive that pauses trading is generally preferable to continuing to trade under unknown system conditions.

---

# 131. Strategy Retirement

A strategy MUST have a defined retirement path.

Retirement may occur because:

* edge disappears,
* drawdown becomes unacceptable,
* execution conditions change,
* market structure changes,
* operational complexity increases,
* or a superior validated strategy replaces it.

Retirement MUST NOT require evidence that the strategy has completely failed.

A strategy can be retired because its expected future value is no longer sufficient.

---

# 132. Strategy Revalidation After Retirement

A retired strategy MAY be reactivated only through a new validation process.

Historical performance from its previous deployment MUST NOT automatically qualify it for reactivation.

The system MUST treat reactivation as a new deployment decision.

---

# 133. V1 Go / No-Go Framework

The final QuantOS V1 decision can be expressed as:

```text id="7z0s7b"
                    ┌──────────────┐
                    │ Data Valid?  │
                    └──────┬───────┘
                           │ YES
                           ▼
                    ┌──────────────┐
                    │ No Leakage?  │
                    └──────┬───────┘
                           │ YES
                           ▼
                    ┌──────────────┐
                    │ Backtest     │
                    │ Valid?       │
                    └──────┬───────┘
                           │ YES
                           ▼
                    ┌──────────────┐
                    │ Walk-Forward │
                    │ Robust?      │
                    └──────┬───────┘
                           │ YES
                           ▼
                    ┌──────────────┐
                    │ OOS Valid?   │
                    └──────┬───────┘
                           │ YES
                           ▼
                    ┌──────────────┐
                    │ Paper Stable?│
                    └──────┬───────┘
                           │ YES
                           ▼
                    ┌──────────────┐
                    │ Live Controls│
                    │ Verified?    │
                    └──────┬───────┘
                           │ YES
                           ▼
                    ┌──────────────┐
                    │ SMALL LIVE   │
                    │ DEPLOYMENT   │
                    └──────────────┘
```

Any critical `NO` results in:

```text
STOP
INVESTIGATE
CORRECT
REVALIDATE
```

There is no shortcut around a failed validation gate.

---

# 134. V1 Real-Capital Philosophy

QuantOS V1 is intentionally designed to prove the system with a small amount of real capital.

The objective is not:

```text
maximize leverage
maximize trade frequency
maximize return
```

The objective is:

```text
prove the complete system works
        ↓
prove it survives real market interaction
        ↓
prove accounting is correct
        ↓
prove execution assumptions are credible
        ↓
prove risk controls work
        ↓
then scale
```

This is the correct sequence for a system whose ultimate objective is reliable autonomous trading.

---

# 135. Validation Requirements — Final Set

### REQ-VAL-041 — Paper Trading

The production strategy MUST be tested against live market data in a non-capital environment before live deployment.

### REQ-VAL-042 — Production Equivalence

Paper trading MUST use the production Data, Feature, Alpha, Risk, and Execution path.

### REQ-VAL-043 — Live Data Integrity

The system MUST detect stale, missing, duplicated, or invalid live market data.

### REQ-VAL-044 — Execution Reconciliation

Orders, fills, positions, and internal portfolio state MUST be reconcilable.

### REQ-VAL-045 — Restart Safety

The system MUST recover safely from process interruption without creating duplicate or inconsistent state.

### REQ-VAL-046 — Failure Recovery

Critical infrastructure failures MUST produce a safe response.

### REQ-VAL-047 — Kill Switch

The live system MUST provide and validate an emergency trading shutdown mechanism.

### REQ-VAL-048 — Capital Isolation

Initial live deployment MUST use deliberately constrained capital.

### REQ-VAL-049 — Explicit Promotion

Live deployment MUST require an explicit promotion decision.

### REQ-VAL-050 — Auditability

Promotion decisions MUST reference the exact validation and implementation versions supporting them.

### REQ-VAL-051 — Revalidation

Material changes to trading behavior MUST trigger appropriate revalidation.

### REQ-VAL-052 — Live Monitoring

Production MUST monitor risk, execution, system health, and behavioral drift.

### REQ-VAL-053 — Drift Response

Material deviations from validated behavior MUST trigger investigation and, where necessary, restricted trading or shutdown.

### REQ-VAL-054 — Retirement

Strategies MUST have an explicit retirement mechanism.

### REQ-VAL-055 — Reactivation

Retired strategies MUST undergo new validation before reactivation.

---

# 136. 007 Final Architecture

The complete QuantOS validation lifecycle is:

```text id="3t5m2p"
                    ┌──────────────────┐
                    │   DATA LAYER     │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ FEATURE ENGINE   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  ALPHA ENGINE    │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ RISK & EXECUTION │
                    └────────┬─────────┘
                             │
                ┌────────────┴────────────┐
                │                         │
                ▼                         ▼
        ┌───────────────┐         ┌───────────────┐
        │  BACKTESTER   │         │ LIVE EXCHANGE │
        └───────┬───────┘         └───────┬───────┘
                │                         │
                ▼                         ▼
        ┌───────────────┐         ┌───────────────┐
        │ VALIDATION    │         │ RECONCILIATION │
        └───────┬───────┘         └───────┬───────┘
                │                         │
                ▼                         ▼
        ┌───────────────┐         ┌───────────────┐
        │ WALK-FORWARD  │         │ LIVE MONITOR   │
        └───────┬───────┘         └───────┬───────┘
                │                         │
                ▼                         ▼
        ┌───────────────┐         ┌───────────────┐
        │ OUT-OF-SAMPLE │         │ REAL CAPITAL   │
        └───────┬───────┘         └───────────────┘
                │
                ▼
        ┌───────────────┐
        │ PAPER TRADING │
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │ LIVE PROMOTION│
        └───────────────┘
```

The same production decision path is preserved throughout.

Only the market interaction boundary changes.

---

# 137. QuantOS V1 Validation Contract

The complete validation contract can be reduced to ten rules:

1. **Never use future information.**
2. **Never trust a single backtest.**
3. **Never optimize against the final test set.**
4. **Always include realistic costs and execution assumptions.**
5. **Always use the production decision path.**
6. **Always preserve reproducibility.**
7. **Always analyze failure, not only profitability.**
8. **Always validate against unseen data.**
9. **Always paper trade before risking capital.**
10. **Always start live deployment with constrained capital.**

These rules form the minimum standard for QuantOS V1.

---

# 138. Relationship to Other QuantOS Documents

Document 007 does not replace the systems defined in documents 000–006.

It governs how those systems are evaluated.

```text id="4z0xkq"
000 READ_FIRST
      │
      ▼
001 PRD
      │
      ▼
002 Architecture
      │
      ▼
003 Data
      │
      ▼
004 Feature Engine
      │
      ▼
005 Alpha Engine
      │
      ▼
006 Risk & Execution
      │
      ▼
007 Validation & Backtesting
      │
      ▼
008 Implementation Guide
```

The dependency relationship is:

* **003** defines what market data means.
* **004** defines what production features mean.
* **005** defines how signals are generated.
* **006** defines how risk and execution control those signals.
* **007** defines how the entire system is proven correct and sufficiently robust.
* **008** defines how the system is actually implemented.

Document 007 therefore acts as the **quality gate between architecture and real capital**.

---

# 139. Final V1 Principle

QuantOS should never ask:

> “Can we make the backtest look profitable?”

It should ask:

> **“What evidence would convince us that this exact system can safely operate with real money?”**

That distinction defines the entire validation architecture.

The desired progression is:

```text
Hypothesis
   ↓
Implementation
   ↓
Historical Evidence
   ↓
Independent Evidence
   ↓
Operational Evidence
   ↓
Real-Market Evidence
   ↓
Controlled Capital
   ↓
Scale
```

At every stage, evidence must be earned.

No stage inherits trust from the previous stage automatically.

The final V1 objective is therefore not maximum historical performance.

It is:

> **A deterministic, reproducible, causally correct, realistically simulated, statistically credible, operationally verified trading system capable of entering the real market with small controlled capital and a clearly defined path toward scaling.**

That is the validation standard for QuantOS V1.
