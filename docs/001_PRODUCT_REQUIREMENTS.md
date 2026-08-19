# QuantOS — Product Requirements

## Document Status

**Status:** Frozen V1 Product Requirements
**Version:** 1.0
**Depends On:** `000_READ_FIRST.md`

---

# 1. Purpose

This document defines the functional and operational requirements for QuantOS V1.

It defines **what the product must accomplish**.

It does not define detailed implementation architecture. Those decisions belong in `002_SYSTEM_ARCHITECTURE.md` and the subsequent technical specifications.

All requirements in this document must remain consistent with `000_READ_FIRST.md`.

If a requirement conflicts with `000_READ_FIRST.md`, `000_READ_FIRST.md` takes precedence.

---

# 2. Product Objective

QuantOS V1 must provide a complete, reproducible, risk-controlled quantitative trading workflow for Binance Spot.

The system must support the progression:

```
Historical Research
        ↓
Backtesting
        ↓
Walk-Forward Validation
        ↓
Robustness Testing
        ↓
Paper Trading
        ↓
Live Approval
        ↓
Controlled Live Trading
```

The product must be capable of completing this workflow without requiring a separate production system for each stage.

---

# 3. V1 Scope

## 3.1 Exchange

V1 supports:

**Binance Spot**

No other exchange is required.

---

## 3.2 Trading Pairs

V1 supports:

* BTCUSDT
* ETHUSDT

Additional symbols are outside the required V1 scope.

---

## 3.3 Capital

The initial operating target is:

**20 USDT**

The product must correctly operate under small-account constraints.

The system must account for:

* available balance
* minimum order quantity
* minimum order notional where applicable
* price precision
* quantity precision
* trading fees
* insufficient balance
* order rejection

The product must reject trades that cannot be safely or validly executed.

---

## 3.4 Primary Timeframe

The primary trading timeframe is:

**1-minute candles**

Higher-timeframe information may be derived only when it can be calculated without introducing look-ahead bias.

---

# 4. Product Operating Modes

QuantOS V1 must support four distinct operating modes:

1. Research
2. Backtest
3. Paper Trading
4. Live Trading

The mode must be explicitly identifiable by the system.

Live trading must never be entered accidentally.

Paper trading must be the default safe mode for operational testing.

---

# 5. Market Data Requirements

The system must obtain and maintain historical market data required by V1.

At minimum, the system must support:

* Binance Spot
* BTCUSDT
* ETHUSDT
* 1-minute candles

Historical data must be validated before being used for research or model training.

The system must detect, where applicable:

* malformed records
* duplicate records
* missing candles
* invalid timestamps
* invalid prices
* invalid volumes
* unexpected time ordering
* inconsistent intervals

Invalid or incomplete data must not silently enter the research or trading pipeline.

---

# 6. Dataset Reproducibility

Every dataset used for an important research or validation run must be identifiable.

A dataset identity must allow the system to determine:

* exchange
* symbol
* timeframe
* covered time range
* source
* schema/version
* validation state

Derived datasets must remain traceable to the source data and transformation configuration that produced them.

Historical datasets must not silently change after they have been used for a recorded experiment.

---

# 7. Feature Requirements

The Feature Engine must generate deterministic features from validated market data.

The production feature set must remain small.

Target:

**10–15 production features**

Absolute maximum:

**20 production features**

Every production feature must have a documented purpose.

Features that provide substantially redundant information should not be included solely because they improve an in-sample result.

---

## 7.1 Feature Integrity

A feature must use only information that would have been available at the prediction timestamp.

The system must prevent:

* future candles
* future prices
* future returns
* future volume
* future labels
* future-derived statistics
* improperly forward-filled future information
* information from protected validation/test periods

Feature generation must be deterministic.

Given identical inputs and configuration, the resulting feature values must be reproducible.

---

# 8. Alpha Requirements

QuantOS V1 must contain one production trading strategy and one production model.

The Alpha Engine must transform validated market information into a trade proposal.

A trade proposal may contain information such as:

* timestamp
* symbol
* predicted direction
* model output
* expected edge where applicable
* proposed action
* model version
* feature version
* relevant decision metadata

The Alpha Engine must not execute orders.

The Alpha Engine must not bypass the Risk Engine.

---

# 9. Model Requirements

Candidate models may be evaluated during research.

However:

**Only one validated model may become the V1 production model.**

The production model must be selected using out-of-sample evidence rather than in-sample performance alone.

Model evaluation must consider:

* predictive performance
* stability
* robustness
* transaction costs
* trading performance
* drawdown
* sensitivity to reasonable parameter changes
* out-of-sample behavior

The product must prioritize robustness over model complexity.

---

# 10. Risk Requirements

The Risk Engine must be able to reject any proposed trade.

Risk controls must be applied before live order submission.

V1 risk management must include, where applicable:

* position sizing
* maximum position risk
* maximum daily loss
* maximum drawdown
* available-balance checks
* volatility-aware sizing
* transaction-cost awareness
* exchange constraint checks
* trade rejection

Risk decisions must be recorded.

A rejected trade must not reach live execution.

---

# 11. Execution Requirements

Only the Execution Engine may submit orders to Binance.

The execution process must support the required Binance Spot order lifecycle.

The system must track, where applicable:

* order creation
* order submission
* exchange acknowledgement
* order status
* fills
* cancellations
* rejections
* execution price
* executed quantity
* fees
* execution timestamps

Execution failures must be handled safely.

The system must not repeatedly submit orders because of an ambiguous network or API response without first determining the state of the original order.

---

# 12. Transaction Costs and Slippage

Backtesting and validation must include realistic trading costs.

The product must account for:

* trading fees
* expected slippage
* execution costs

A strategy must not be considered successful merely because it is profitable before costs.

All important performance results must distinguish between gross and cost-adjusted performance where appropriate.

---

# 13. Backtesting Requirements

The system must support historical backtesting using the same essential production decision path as the live system.

Backtesting must include:

* historical market data
* deterministic feature generation
* model inference
* trading decisions
* risk rules
* transaction costs
* slippage assumptions
* position/account state
* trade recording
* performance evaluation

The backtest must not use information that would not have been available at the simulated decision time.

---

# 14. Walk-Forward Validation

Walk-forward validation is required.

The purpose is to determine whether the trading system remains effective when evaluated on unseen future periods.

The validation process must maintain chronological separation between:

* training data
* validation data
* out-of-sample test data

The final out-of-sample period must not be repeatedly used to tune the strategy.

Model and feature decisions must be based on information available before the final evaluation period.

---

# 15. Robustness Testing

QuantOS V1 must perform robustness testing before live approval.

At minimum, robustness testing must evaluate whether results remain acceptable under reasonable changes to assumptions.

This may include:

* trade-order randomization where appropriate
* cost/slippage variation
* parameter perturbation
* return/trade resampling
* Monte Carlo analysis

The objective is not to prove a precise future return.

The objective is to identify whether the strategy's apparent historical advantage is fragile.

A strategy that fails reasonable robustness checks must not be promoted to live trading.

---

# 16. Paper Trading Requirements

Paper trading is mandatory before live trading.

Paper trading must use the production decision path as closely as practical.

The system must record:

* market inputs
* generated features
* model decisions
* trade proposals
* risk decisions
* simulated orders
* simulated fills
* simulated fees/costs
* account state
* performance

Paper trading must provide evidence that the system behaves correctly under live market conditions before real capital is exposed.

---

# 17. Live Trading Requirements

Live trading must require explicit activation.

The system must verify required preconditions before allowing live execution.

At minimum, live readiness must confirm:

* valid configuration
* valid Binance credentials
* correct exchange environment
* supported symbol
* valid market data
* valid model
* valid feature configuration
* risk controls enabled
* sufficient account information
* system health
* successful reconciliation where required

If a required condition fails, the system must not trade.

---

# 18. Live Safety Requirements

The system must fail safely.

Examples include:

* stale market data
* missing market data
* invalid feature values
* model failure
* configuration failure
* Binance API failure
* network failure
* order-state uncertainty
* risk-engine failure
* execution failure
* corrupted local state

The safe default for an unresolved critical condition is:

**Do not place a new trade.**

---

# 19. Reconciliation Requirements

The system must maintain consistency between its recorded state and Binance account/order state.

Reconciliation must be able to identify discrepancies involving:

* balances
* open orders
* filled orders
* cancelled orders
* positions or holdings
* recorded trades

A detected critical discrepancy must prevent unsafe new trading until the state is understood or safely recovered.

---

# 20. Evaluation Requirements

The Evaluation Engine must provide consistent evaluation of research, backtest, and paper/live results.

Core performance measurements must include, where applicable:

* net profit
* return
* Sharpe ratio
* Sortino ratio
* maximum drawdown
* profit factor
* win rate
* trade count
* average trade
* exposure
* transaction costs
* slippage impact

Metrics must be calculated consistently across comparable runs.

Performance reports must distinguish between in-sample and out-of-sample results.

---

# 21. Research Run Requirements

QuantOS V1 must provide a lightweight reproducible research-run concept.

This requirement is inspired by research workflow practices used by systems such as Microsoft Qlib.

Qlib itself is not required as a V1 production dependency.

Each important experiment must record enough information to reproduce and identify the result.

A research run must record, at minimum:

* run identity
* timestamp
* code revision
* configuration/version
* dataset identity/version
* feature version
* model/version
* training period
* validation period
* test period
* random seed where applicable
* transaction-cost assumptions
* slippage assumptions
* evaluation metrics
* validation result
* model artifact identity where applicable

The research-run record must be immutable after completion.

The same experiment must be identifiable later without relying on memory or manually reconstructed settings.

---

# 22. Research and Production Separation

Research experimentation must not automatically modify the production trading system.

A candidate:

* feature
* model
* parameter
* strategy change
* configuration

must be explicitly validated before it can enter production.

A research experiment must never silently change the live trading configuration.

Production must always reference an explicitly identified model and feature version.

---

# 23. Qlib Integration Boundary

QuantOS may use Qlib-inspired concepts or optional offline tooling during research.

However:

* Qlib is not required for live trading.
* Qlib is not part of the Binance execution path.
* Qlib does not become a QuantOS production module.
* QuantOS remains responsible for its own production data, features, model, risk, execution, and evaluation interfaces.
* Qlib must not introduce additional V1 features or architecture.

The purpose of adopting Qlib-inspired discipline is reproducibility and research quality, not architectural expansion.

---

# 24. Configuration Requirements

Important runtime behavior must be configurable without modifying application source code.

Configuration must cover, where applicable:

* operating mode
* exchange
* symbols
* timeframes
* risk limits
* model configuration
* feature configuration
* execution parameters
* cost assumptions
* slippage assumptions
* storage locations

Secrets must not be hardcoded into source code.

---

# 25. Logging and Audit Requirements

The system must record sufficient information to reconstruct important decisions.

The following events must be observable:

* startup
* shutdown
* data ingestion
* data validation
* feature generation
* model inference
* trade proposal
* risk decision
* order submission
* exchange response
* fills
* cancellations
* errors
* reconciliation
* system state changes

For every live trade, the system should be able to answer:

1. What market information was available?
2. What features were generated?
3. Which model/version made the proposal?
4. What did the model propose?
5. Why did Risk approve or reject it?
6. What order was submitted?
7. What did Binance return?
8. What was actually filled?
9. What fees/costs occurred?

---

# 26. Determinism and Reproducibility

The following must be reproducible:

* dataset preparation
* feature generation
* research configuration
* model training where deterministic behavior is possible
* backtesting
* evaluation

Where randomness is unavoidable, the relevant random seed and configuration must be recorded.

A research result must not depend on undocumented local state.

---

# 27. Testing Requirements

V1 must include automated tests for critical functionality.

Testing must cover, at minimum:

### Data

* schema validation
* timestamp validation
* duplicate detection
* missing-data detection
* deterministic dataset preparation

### Features

* calculation correctness
* timestamp alignment
* leakage prevention
* deterministic output

### Alpha

* model input validation
* prediction behavior
* trade proposal generation

### Risk

* position sizing
* limit enforcement
* trade rejection
* insufficient balance
* invalid proposals

### Execution

* order construction
* Binance constraint handling
* order-state handling
* failure behavior
* reconciliation

### Evaluation

* metric calculation
* backtest accounting
* cost calculation
* drawdown calculation

---

# 28. Production Promotion Gate

A production strategy/model must pass all required stages before live trading:

Research
   ↓
Historical Backtest
   ↓
Walk-Forward Validation
   ↓
Robustness Testing
   ↓
Paper Trading
   ↓
Live Approval


Each stage must produce recorded evidence.

A failure at any stage blocks promotion.

A strong historical backtest alone is never sufficient.

---

# 29. V1 Non-Functional Requirements

QuantOS V1 must prioritize:

## Reliability

The system must handle expected failures without uncontrolled trading.

## Reproducibility

Important results must be traceable to their exact inputs and configuration.

## Determinism

Identical inputs should produce identical results wherever deterministic behavior is expected.

## Safety

Risk controls must take precedence over trading opportunity.

## Observability

Important decisions and failures must be visible through logs and recorded state.

## Simplicity

The implementation must remain small enough to understand, test, debug, and operate locally.

---

# 30. Explicitly Out of Scope

The following are not V1 product requirements:

* multiple exchanges
* futures
* margin
* leverage
* options
* multiple production strategies
* strategy ensembles
* multiple production models
* portfolio optimization
* reinforcement learning
* autonomous trading agents
* automatic strategy discovery
* automatic feature discovery
* deep-learning infrastructure
* high-frequency trading infrastructure
* distributed deployment
* microservices
* Kubernetes
* cloud-native infrastructure
* multi-user functionality
* social/news trading
* institutional portfolio management

These must not be introduced indirectly through lower-level specifications.

---

# 31. V1 Success Criteria

QuantOS V1 is successful when it can demonstrate the complete workflow:

Binance Historical Data
        ↓
Validated Dataset
        ↓
Deterministic Features
        ↓
One Production Model
        ↓
Backtest
        ↓
Walk-Forward Validation
        ↓
Robustness Testing
        ↓
Paper Trading
        ↓
Risk-Controlled Live Approval
        ↓
Binance Spot Execution
        ↓
Reconciliation
        ↓
Evaluation


The system must be able to reproduce its important research results, explain its trading decisions, reject unsafe trades, and operate without requiring unnecessary infrastructure.

---

# 32. Final Requirement

QuantOS V1 must favor **a small system that works end-to-end** over a large system containing many partially validated capabilities.

When two approaches satisfy the same requirement, prefer the approach that has:

1. fewer moving parts
2. fewer parameters
3. fewer dependencies
4. lower overfitting risk
5. easier testing
6. easier debugging
7. clearer failure behavior

V1 should prove the trading system before expanding the trading system.
