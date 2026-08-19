# QuantOS — Backtesting and Evaluation Specification

## Document Status

**Status:** Frozen V1 Backtesting and Evaluation Specification
**Version:** 1.0
**Depends On:** `000_READ_FIRST.md`, `001_PRODUCT_REQUIREMENTS.md`, `002_SYSTEM_ARCHITECTURE.md`, `003_DATA_ARCHITECTURE.md`, `004_FEATURE_AND_MODEL_SPECIFICATION.md`, `005_RISK_AND_EXECUTION_SPECIFICATION.md`

---

# 1. Purpose

This document defines how QuantOS V1 evaluates trading strategies before paper trading and live deployment.

The evaluation system must answer:

> Does the strategy demonstrate evidence of a repeatable trading edge after realistic costs, without using information that would not have been available at the time?

The evaluation system must prioritize:

1. Temporal correctness
2. Realistic execution assumptions
3. Out-of-sample performance
4. Robustness
5. Reproducibility
6. Simplicity
7. Risk-adjusted results

A visually impressive backtest is not sufficient evidence for deployment.

---

# 2. Evaluation Scope

V1 evaluation covers:

* BTCUSDT
* ETHUSDT
* Binance Spot assumptions
* 1-minute primary data
* historical backtesting
* walk-forward evaluation
* robustness testing
* paper-trading comparison
* production-readiness evaluation

The system does not require a distributed backtesting platform.

---

# 3. Evaluation Philosophy

QuantOS must distinguish between:

```text
Research Result
```

and:

```text
Evidence of Robustness
```

A strategy that performs well on one historical period is not automatically robust.

A strategy should only be considered credible when its behavior remains acceptable across:

* unseen data
* different market periods
* realistic costs
* reasonable parameter changes
* different market conditions

---

# 4. Backtest Architecture

The V1 backtest path is:

```text id="j3x9a1"
Historical Dataset
       ↓
Deterministic Replay
       ↓
Feature Engine
       ↓
Model
       ↓
Alpha Decision
       ↓
Risk Engine
       ↓
Simulated Execution
       ↓
Portfolio State
       ↓
Performance Evaluation
```

The backtest must use the same conceptual Alpha and Risk logic as production.

---

# 5. No Future Information

The backtest must never use information that would not have been available at the simulated decision time.

This applies to:

* features
* labels
* normalization
* model training
* thresholds
* risk calculations
* execution prices
* market state
* account state

Any future-information dependency invalidates the backtest.

---

# 6. Event-Time Principle

Backtesting must operate in chronological event order.

Conceptually:

```text id="2z4xq7"
Market Observation at t
        ↓
Information Available at t
        ↓
Feature Calculation
        ↓
Model Prediction
        ↓
Risk Decision
        ↓
Order Decision
        ↓
Execution
        ↓
Portfolio State Update
        ↓
Move to t + 1
```

The simulator must not calculate results using future portfolio information.

---

# 7. Closed-Candle Convention

V1 should use closed candles for strategy decisions.

Conceptually:

```text id="q7k2m4"
Candle closes
      ↓
Features become available
      ↓
Prediction
      ↓
Risk
      ↓
Order
```

The backtest must use the same convention as paper and live trading.

---

# 8. Backtest Data Boundary

Each backtest must explicitly identify:

* dataset identity
* symbol
* timeframe
* start time
* end time
* feature version
* model version
* strategy version
* risk configuration
* execution assumptions

A backtest without identifiable inputs is not reproducible.

---

# 9. Train / Validation / Test

Backtesting must distinguish:

```text id="1f0n9d"
Training
Validation
Final Test
```

Training is used to fit the model.

Validation is used for model/parameter decisions.

Final Test is used for final evaluation.

The final test period must not be repeatedly used to optimize the strategy.

---

# 10. Protected Final Test

The final test dataset is a protected evaluation period.

Once the production configuration is selected:

* feature definitions must not be tuned against the final test
* model parameters must not be tuned against the final test
* thresholds must not be tuned against the final test
* strategy rules must not be tuned against the final test

If the final test is used for further optimization, it becomes part of the development process and loses its protected status.

---

# 11. Walk-Forward Evaluation

Walk-forward testing is the preferred V1 validation method.

Conceptually:

```text id="h2p6v9"
Train ──→ Validate ──→ Test
                ↓
              Move
                ↓
Train ──→ Validate ──→ Test
                ↓
              Move
                ↓
Train ──→ Validate ──→ Test
```

Each test period must occur after its corresponding training period.

---

# 12. Walk-Forward Training

For each walk-forward window:

1. select historical training data
2. fit preprocessing using training data
3. fit the model using training data
4. evaluate configuration using validation data where required
5. freeze the resulting configuration
6. evaluate on the forward test period
7. record the result
8. move the window forward

Future test observations must not influence earlier training.

---

# 13. Walk-Forward Feature Processing

Any learned transformation must be fitted independently within each training window.

This includes:

* normalization
* scaling
* learned feature transformations
* imputation parameters where applicable

The process is:

```text id="n8j3f4"
Training Data
      ↓
Fit Transformation
      ↓
Validation/Test
      ↓
Apply Transformation
```

No future-period statistics may be used.

---

# 14. Walk-Forward Model Lifecycle

Each walk-forward window may produce a distinct model artifact.

Each model must be identifiable by:

* model version
* training period
* feature version
* configuration
* code revision

The evaluation system must preserve the relationship between the model and its test period.

---

# 15. Execution Simulation

Backtest execution must simulate realistic order behavior.

At minimum, it must model:

* available balance
* holdings
* order quantity
* fees
* slippage
* order execution timing
* order constraints

The simulator must not assume every order executes at the most favorable possible price.

---

# 16. Transaction Fees

Fees must be included in backtest results.

The fee assumption must be explicit and versioned.

The system must distinguish:

```text id="8p3r2v"
Gross P&L
```

from:

```text id="x9k4q1"
Net P&L
```

Production decisions must be evaluated using net results.

---

# 17. Slippage

Backtests must include a configurable slippage assumption.

Slippage should be applied consistently with the simulated order direction.

For example:

```text id="6k3w2m"
Buy → less favorable execution price
Sell → less favorable execution price
```

The exact slippage model must be documented and versioned.

---

# 18. Execution Delay

If the production strategy cannot execute instantaneously after a signal, the backtest must model the relevant delay.

The backtest must not use a price that was unavailable when the order would realistically have been submitted.

---

# 19. Market Gaps

The simulator must handle missing market data explicitly.

It must not invent an executable price for an interval where valid market information is unavailable.

If a required observation is missing:

* record the condition
* apply the defined data policy
* do not fabricate a favorable fill

---

# 20. Exchange Constraints

Backtests must respect relevant Binance Spot constraints.

These include:

* minimum order quantity
* minimum notional
* quantity precision
* price precision
* available balance

A trade that could not realistically be submitted should not be treated as executable.

---

# 21. Small-Account Simulation

Backtests must support the V1 starting capital assumption:

**20 USDT**

This is important because a strategy that appears profitable at large capital may not be executable with a very small account.

The simulator must account for:

* minimum order size
* fees
* available balance
* position sizing
* capital utilization

---

# 22. Portfolio Accounting

The backtest must maintain a simulated account state.

For Spot trading this includes:

* USDT balance
* BTC balance
* ETH balance
* locked balance where applicable
* open simulated orders where applicable
* realized P&L
* unrealized P&L

Portfolio equity must be calculated consistently.

---

# 23. Position Accounting

The simulator must distinguish between:

```text id="n5j7q2"
Order
```

and:

```text id="m3w8r1"
Actual Filled Position
```

Only executed quantities modify holdings.

---

# 24. Trade Lifecycle

A simulated trade should maintain:

```text id="z8x4c3"
Signal
  ↓
Risk Approval
  ↓
Order
  ↓
Fill
  ↓
Position
  ↓
Exit
  ↓
Completed Trade
```

Each stage must remain traceable.

---

# 25. Benchmark

Backtest results must be compared against a simple benchmark.

The benchmark should represent a passive alternative appropriate to the evaluated asset and period.

For BTCUSDT/ETHUSDT, an appropriate baseline may be:

* buy-and-hold
* cash/USDT baseline where relevant

The benchmark must be clearly identified in the evaluation report.

---

# 26. Baseline Strategy

The system should support a simple baseline strategy for comparison.

The purpose is not to prove that the model can beat every possible strategy.

The purpose is to determine whether the model adds meaningful value beyond a simple alternative.

---

# 27. Performance Metrics

The evaluation system must calculate at minimum:

* total return
* net P&L
* annualized return where the test period permits
* maximum drawdown
* volatility where meaningful
* Sharpe ratio where meaningful
* Sortino ratio where meaningful
* win rate
* profit factor
* number of trades
* average trade return
* average winning trade
* average losing trade
* fees paid
* estimated slippage
* exposure

---

# 28. Total Return

Total return must be calculated from the simulated portfolio equity.

The result must be based on net portfolio value after applicable fees and execution assumptions.

---

# 29. Maximum Drawdown

Maximum drawdown must measure the largest decline from an equity peak to a subsequent trough.

Conceptually:

```text id="v4m2p8"
Peak Equity
     ↓
Decline
     ↓
Trough
```

The metric must be calculated from the complete equity curve.

---

# 30. Risk-Adjusted Metrics

Risk-adjusted metrics may include:

* Sharpe ratio
* Sortino ratio

These metrics must not be treated as sufficient evidence by themselves.

A high Sharpe ratio with:

* very few trades
* unrealistic fills
* extreme parameter sensitivity

must not be considered robust.

---

# 31. Trade Count

Trade count must always be reported.

A strategy producing a high return from only a handful of trades must be treated with caution.

The evaluation system must not hide low sample size.

---

# 32. Statistical Significance Awareness

QuantOS must not assume that historical profitability automatically proves statistical significance.

The evaluation report should provide enough information to assess:

* trade count
* average trade outcome
* outcome dispersion
* consistency
* confidence where appropriate

The system should avoid making strong conclusions from very small samples.

---

# 33. Profit Factor

Profit factor should be reported where sufficient trades exist.

It is:

```text id="u7c4m1"
Gross Winning P&L
-----------------
Gross Losing P&L
```

A strong profit factor with very few trades must not be treated as conclusive evidence.

---

# 34. Win Rate

Win rate must be reported but must not be optimized independently.

A high win rate does not necessarily imply positive expectancy.

Evaluation must consider:

```text id="c4j7v8"
Win Rate
+
Average Win
+
Average Loss
+
Costs
```

together.

---

# 35. Expectancy

The system should calculate average trade expectancy.

Conceptually:

```text id="a7q2z5"
Expectancy =
Probability of Win × Average Win
-
Probability of Loss × Average Loss
-
Expected Costs
```

The exact implementation must remain consistent across backtest and evaluation.

---

# 36. Exposure

The evaluation report must show how much capital was exposed over time.

This helps distinguish:

* high return with low exposure
* high return requiring constant exposure
* high return achieved through excessive risk

Exposure must be interpreted alongside drawdown.

---

# 37. Turnover

Turnover should be reported.

Excessive turnover may indicate:

* over-trading
* weak signal quality
* excessive sensitivity
* cost vulnerability

The strategy must remain economically viable after transaction costs.

---

# 38. Cost Sensitivity

Robustness testing must evaluate higher transaction costs than the baseline assumption.

For example:

```text id="m5v8c2"
Baseline Cost
      ↓
Higher Cost
      ↓
Higher Slippage
```

A strategy that becomes unprofitable under a small increase in costs should be considered fragile.

---

# 39. Slippage Sensitivity

The strategy should be evaluated under multiple reasonable slippage assumptions.

The purpose is to determine whether the edge depends on unrealistically favorable execution.

---

# 40. Parameter Sensitivity

Important parameters should be tested around their selected values.

For example:

```text id="j7n2k4"
Selected Parameter
      ± reasonable variation
```

A robust strategy should not collapse because a parameter changes slightly.

This does not mean performing unlimited parameter searches.

The goal is sensitivity analysis, not optimization.

---

# 41. Feature Sensitivity

The evaluation process may test whether removing individual features causes catastrophic performance degradation.

A model that depends entirely on one fragile feature should be treated cautiously.

Feature ablation must remain controlled and must not contaminate the protected final test.

---

# 42. Market-Regime Analysis

Performance should be evaluated across different market conditions where sufficient data exists.

Examples include:

* trending markets
* ranging markets
* high-volatility periods
* low-volatility periods
* major drawdown periods

The objective is not to guarantee profitability in every regime.

The objective is to determine where the strategy's edge exists and where it fails.

---

# 43. Symbol-Level Evaluation

BTCUSDT and ETHUSDT results must be reported separately.

The report should identify:

```text id="w4k9p3"
BTCUSDT Performance
ETHUSDT Performance
Combined Performance
```

A combined result must not hide a complete failure on one symbol.

---

# 44. Time-Period Evaluation

Performance must also be evaluated by meaningful time periods where possible.

Examples:

* monthly
* quarterly
* yearly

This helps identify whether the strategy's results are concentrated in a narrow period.

---

# 45. Drawdown Duration

Where practical, evaluation should report how long major drawdowns last.

Two strategies with the same maximum drawdown may have very different operational characteristics if one recovers quickly and the other remains underwater for a long period.

---

# 46. Equity Curve

Every completed backtest must produce an equity curve.

The equity curve should show:

* portfolio value over time
* drawdowns where practical
* major trade events where useful

The equity curve is a diagnostic tool, not merely a presentation artifact.

---

# 47. Trade Distribution

The evaluation should inspect the distribution of trade outcomes.

It should identify:

* large winners
* large losers
* median trade
* average trade
* clusters of losses
* clusters of wins

This helps determine whether results depend excessively on a small number of exceptional trades.

---

# 48. Consecutive Losses

The system should report maximum consecutive losses.

This is important for:

* risk planning
* drawdown understanding
* psychological/operational expectations
* paper-to-live comparison

---

# 49. Recovery Factor

Where appropriate, evaluation may report recovery factor:

```text id="y4n1s7"
Net Profit
-----------
Maximum Drawdown
```

This must be treated as a supporting metric rather than a primary decision criterion.

---

# 50. Robustness Test Suite

A candidate production strategy should pass a reasonable robustness suite including:

1. Higher fees
2. Higher slippage
3. Parameter perturbation
4. Time-period analysis
5. Symbol-level analysis
6. Market-regime analysis
7. Trade-distribution analysis
8. Walk-forward testing

The exact thresholds must remain aligned with the approved V1 risk/reward requirements.

---

# 51. No Optimization During Robustness Testing

Robustness testing must not become another optimization loop.

Once a candidate strategy is selected:

```text id="f3j8m2"
Freeze Candidate
      ↓
Run Robustness Tests
      ↓
Evaluate
```

Do not modify the strategy after each robustness result until the evaluation process is complete.

Otherwise robustness testing becomes hidden parameter optimization.

---

# 52. Final Test Integrity

If the strategy fails the final test:

```text id="q8n2k6"
Do Not Promote
```

The correct response is not to repeatedly modify the strategy until the final test becomes profitable.

Instead:

1. record the failure
2. return to research
3. create a new candidate/version
4. use a new protected test period when appropriate

---

# 53. Backtest Reproducibility

Every backtest must be reproducible from recorded inputs.

The run must identify:

```text id="n4k7s2"
Dataset Identity
Feature Version
Model Version
Strategy Version
Risk Configuration
Execution Configuration
Code Revision
Time Range
Random Seed where applicable
```

---

# 54. Qlib-Inspired Experiment Tracking

QuantOS may use Qlib-inspired experiment discipline.

Each meaningful experiment should record:

* experiment identity
* dataset identity
* feature version
* model version
* strategy version
* configuration
* evaluation metrics
* artifacts
* code revision
* timestamp

This allows experiments to be compared without requiring Qlib itself.

Qlib remains optional for offline research.

---

# 55. Experiment Immutability

Completed research runs should be treated as immutable records.

If an experiment is changed materially:

```text id="u3x6b1"
New Experiment
```

must be created rather than silently overwriting the old result.

This preserves the research history.

---

# 56. Evaluation Artifact

Each important evaluation should produce an artifact containing:

* configuration
* metrics
* equity curve
* trade list
* drawdown information
* cost assumptions
* dataset identity
* model identity
* feature identity
* evaluation period

The artifact must be traceable to the experiment that produced it.

---

# 57. Trade List

The backtest must produce a trade list.

Each completed trade should identify, where applicable:

* symbol
* entry timestamp
* entry price
* entry quantity
* exit timestamp
* exit price
* exit quantity
* gross P&L
* fees
* slippage assumption
* net P&L
* strategy/model version

---

# 58. Reconciliation of Backtest Accounting

The evaluation system should verify that:

```text id="m9q2c4"
Starting Equity
+
Net Trading P&L
+
Other Account Effects
=
Ending Equity
```

within the defined accounting model.

Accounting inconsistencies must cause the evaluation to fail.

---

# 59. Benchmark Comparison

Every production-candidate evaluation must compare against at least one simple baseline.

The report should show:

```text id="x2v7k3"
Strategy
Benchmark
Difference
```

The purpose is to determine whether the model creates meaningful incremental value.

---

# 60. Paper-Trading Validation

Before live deployment, the production configuration should operate in paper mode.

Paper trading should compare:

* predicted signals
* approved trades
* simulated fills
* expected costs
* actual market prices
* simulated equity

The purpose is to identify implementation differences between historical simulation and real-time operation.

---

# 61. Backtest-to-Paper Comparison

The system should compare expected and observed behavior.

Differences should be investigated in:

* signal frequency
* feature values
* model predictions
* risk decisions
* execution assumptions
* slippage
* fees

A major unexplained discrepancy must block live promotion.

---

# 62. Paper-to-Live Gate

The strategy must not automatically transition from paper to live.

Live deployment requires explicit approval after:

* backtest validation
* robustness testing
* paper testing
* execution verification
* reconciliation verification

---

# 63. Evaluation Failure Conditions

An evaluation must be rejected or marked invalid if:

* future data is used
* data boundaries are unclear
* model training leaks test data
* normalization uses future information
* execution uses impossible prices
* fees are omitted without justification
* slippage is unrealistically favorable
* accounting is inconsistent
* dataset identity is missing
* model identity is missing
* feature identity is missing
* final-test contamination occurs

---

# 64. Minimum Production Evidence

Before a strategy can be considered for live deployment, it should have evidence from:

1. Historical backtest
2. Walk-forward testing
3. Protected out-of-sample testing
4. Cost sensitivity
5. Slippage sensitivity
6. Parameter sensitivity
7. Symbol-level analysis
8. Market-period analysis
9. Paper trading

No single metric is sufficient.

---

# 65. Production Evaluation Report

The final evaluation report should summarize:

### Configuration

* dataset
* feature version
* model version
* strategy version
* risk configuration
* execution assumptions

### Performance

* total return
* net P&L
* drawdown
* Sharpe
* Sortino
* profit factor
* win rate
* expectancy
* trade count

### Robustness

* cost sensitivity
* slippage sensitivity
* parameter sensitivity
* time-period stability
* symbol stability
* regime behavior

### Operational

* paper-trading results
* execution differences
* reconciliation status
* known limitations

---

# 66. Evaluation Decision

The final evaluation must produce an explicit decision.

Possible states:

```text id="s5w8n1"
PASS
FAIL
INCONCLUSIVE
```

### PASS

Evidence is sufficient to proceed to the next controlled stage.

### FAIL

Evidence indicates insufficient robustness or unacceptable risk.

### INCONCLUSIVE

Evidence is insufficient because of:

* insufficient data
* insufficient trades
* unresolved implementation issues
* unclear behavior

An inconclusive result is not a pass.

---

# 67. No Automatic Promotion

Backtest success must never automatically activate live trading.

The evaluation system may produce:

```text id="k4q7b2"
READY_FOR_REVIEW
```

but only explicit deployment controls may activate live execution.

---

# 68. Overfitting Protection

The evaluation process must actively discourage overfitting.

The following are prohibited:

* repeatedly tuning against the final test
* unlimited feature searches
* unlimited parameter searches
* selecting strategies solely by best historical return
* ignoring transaction costs
* cherry-picking profitable periods
* removing losing periods without justification
* changing the evaluation methodology after seeing results

---

# 69. Research Budget

Research should operate under a deliberate complexity budget.

The number of:

* features
* parameters
* model types
* strategy variants

should remain limited.

The objective is not to search the entire possible strategy space.

The objective is to determine whether a small hypothesis survives out-of-sample testing.

---

# 70. Multiple-Experiment Awareness

If many candidate experiments are run, some will appear successful by chance.

Therefore:

> The best backtest is not automatically the best strategy.

Experiment history should be retained so that the selection process remains visible.

A candidate that wins only because hundreds of alternatives were tried must be treated with caution.

---

# 71. Simplicity Preference

When two candidate systems demonstrate comparable robust performance:

**prefer the simpler system.**

Prefer:

* fewer features
* fewer parameters
* simpler model
* simpler strategy
* fewer dependencies

The simpler candidate is generally easier to:

* validate
* debug
* reproduce
* operate
* monitor

---

# 72. Evaluation Performance Requirements

The backtesting system must be efficient enough for practical local research.

It should support:

* repeated historical runs
* walk-forward evaluation
* multiple symbols
* long historical periods
* trade-level output

However, performance optimization must not compromise temporal correctness.

---

# 73. Deterministic Replay

Given:

```text id="c7m2x5"
Same Dataset
+
Same Configuration
+
Same Code Revision
```

the backtest should produce equivalent results.

Randomized models must record their random seeds.

---

# 74. Backtest Isolation

A backtest must not modify:

* live account state
* live Binance orders
* production model artifacts
* production configuration
* live risk state

Backtesting is strictly isolated from live execution.

---

# 75. Paper Isolation

Paper trading must not modify real exchange state.

Paper mode must not have permission to:

* place live orders
* cancel live orders
* modify account state

Paper execution uses a simulated account.

---

# 76. Evaluation Logging

Evaluation logs should record:

* run start
* dataset
* configuration
* model
* feature version
* major execution events
* warnings
* errors
* completion status

Large trade-level datasets should be stored as structured artifacts rather than flooding application logs.

---

# 77. Testing Requirements

The backtesting system must include tests for:

### Temporal correctness

* no future data access
* correct candle ordering
* correct feature alignment

### Execution

* fees
* slippage
* balances
* precision
* minimum notional
* partial fills where simulated

### Accounting

* P&L
* equity
* drawdown
* position state

### Reproducibility

* deterministic results
* dataset identity
* configuration identity

### Validation

* walk-forward boundaries
* protected test behavior
* leakage detection

---

# 78. Acceptance Criteria

The V1 Backtesting and Evaluation system is compliant when:

* historical replay is deterministic.
* future information cannot enter decisions.
* closed-candle timing is enforced.
* train/validation/test boundaries are explicit.
* walk-forward testing is supported.
* final test data is protected.
* fees are included.
* slippage is included.
* exchange constraints are respected.
* small-account behavior can be simulated.
* portfolio accounting is consistent.
* trade-level results are recorded.
* equity curves are produced.
* drawdown is measured.
* trade count is reported.
* cost sensitivity is tested.
* parameter sensitivity is tested.
* symbol-level results are visible.
* time-period behavior is visible.
* robustness testing is separated from optimization.
* experiments are reproducible.
* Qlib is not required for execution.
* paper trading is isolated from live trading.
* live promotion requires explicit approval.
* failed or inconclusive evaluations cannot automatically reach production.

---

# 79. Final Backtesting and Evaluation Statement

QuantOS V1 does not consider a profitable backtest sufficient evidence of a trading edge.

The validation hierarchy is:

```text id="f8x4m2"
Correct Data
     ↓
Correct Temporal Logic
     ↓
Realistic Execution
     ↓
Historical Backtest
     ↓
Walk-Forward Testing
     ↓
Protected Out-of-Sample Test
     ↓
Robustness Testing
     ↓
Paper Trading
     ↓
Controlled Live Deployment
```

The purpose of this system is not to find the most profitable historical strategy.

It is to determine whether a **simple strategy has enough evidence of robustness to justify risking real capital**.

Therefore:

> **A mediocre but robust result is more valuable than an exceptional but fragile backtest.**

And:

> **If QuantOS cannot reproduce and explain a result, QuantOS must not trust that result.**
