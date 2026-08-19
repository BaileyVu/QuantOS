# QuantOS — Feature and Model Specification

## Document Status

**Status:** Frozen V1 Feature and Model Specification
**Version:** 1.0
**Depends On:** `000_READ_FIRST.md`, `001_PRODUCT_REQUIREMENTS.md`, `002_SYSTEM_ARCHITECTURE.md`, `003_DATA_ARCHITECTURE.md`

---

# 1. Purpose

This document defines how QuantOS V1 transforms validated market data into model inputs and ultimately into a trading proposal.

It defines:

* feature-generation requirements
* feature integrity rules
* feature versioning
* label construction
* model-training requirements
* model-evaluation requirements
* production-model requirements
* strategy-decision requirements
* anti-overfitting controls
* research reproducibility

The goal is to produce a **small, deterministic, testable, and robust signal-generation system**.

---

# 2. V1 Objective

The Feature and Alpha system must answer one question:

> Given only information that was available at time `t`, is there sufficient evidence to justify a trading decision at time `t`?

The system must not attempt to predict every market movement.

The V1 objective is to identify a limited set of repeatable market conditions where the expected trading outcome remains positive after realistic costs.

---

# 3. Feature Budget

The production feature set must remain deliberately small.

Target:

**10–15 features**

Maximum:

**20 features**

The feature count includes all production model inputs.

A feature must not be added simply because it improves historical performance.

Every feature must have:

* a defined calculation
* a defined purpose
* a defined input source
* a defined timestamp relationship
* a documented reason for inclusion

---

# 4. Feature Categories

The V1 production feature set should be built from a small number of interpretable categories.

Permitted categories include:

1. Price/return behavior
2. Volatility
3. Volume/activity
4. Momentum
5. Market-state information

The exact final feature list must remain within the V1 feature budget.

The system must avoid creating multiple highly correlated versions of essentially the same signal.

---

# 5. Feature Design Principles

Features should satisfy the following principles:

* simple
* deterministic
* interpretable
* inexpensive to calculate
* robust across market conditions
* resistant to leakage
* useful for the selected model
* economically meaningful

Complexity must have a demonstrated justification.

---

# 6. Price and Return Features

The production feature set may include price-derived return information such as:

* recent percentage return
* short-horizon return
* medium-horizon return
* return acceleration where justified

Returns must be calculated exclusively from historical observations available at the prediction timestamp.

Future prices must never enter feature calculation.

---

# 7. Volatility Features

The production feature set may include a limited number of volatility measurements.

Examples include:

* rolling return volatility
* realized volatility
* range-based volatility

Volatility windows must be defined before evaluation.

The system must avoid adding many overlapping volatility windows solely to maximize in-sample performance.

---

# 8. Volume Features

The production feature set may include a limited number of volume/activity features.

Examples include:

* relative volume
* rolling volume change
* volume relative to historical baseline

Volume features must use only observations available before or at the prediction timestamp according to the defined candle convention.

---

# 9. Momentum Features

Momentum may be represented using a small number of deterministic indicators or return transformations.

The system must avoid constructing a large library of overlapping momentum indicators.

The purpose is to capture meaningful market behavior, not to maximize feature count.

---

# 10. Market-State Features

A small amount of market-state information may be included where justified.

Examples include:

* volatility regime
* trend state
* relative distance from a rolling reference

Market-state features must remain deterministic.

They must not use future observations to classify the current state.

---

# 11. Feature Normalization

Feature normalization must be applied only when required by the selected model.

If normalization is used, the transformation parameters must be learned only from the permitted training period.

For time-series validation:

```text
Training
   ↓
Fit transformation
   ↓
Validation/Test
   ↓
Apply training transformation
```

Validation or test data must never be used to fit normalization parameters.

---

# 12. Rolling Calculations

Rolling calculations must respect temporal availability.

For a prediction at time `t`, a rolling feature may use:

```text
[t - N + 1, ..., t]
```

or another explicitly defined historical window consistent with the candle execution convention.

It must never use:

```text
[t + 1, ...]
```

Any feature implementation that accesses future observations is invalid.

---

# 13. Look-Ahead Bias

Look-ahead bias is a critical failure condition.

The Feature Engine must prevent information from the future from entering model inputs.

Potential leakage sources include:

* future candles
* future returns
* future volume
* future labels
* future normalization parameters
* future rolling statistics
* future data imputation
* future-selected features
* test-period information

A feature that leaks future information must never be promoted to production.

---

# 14. Candle Availability Convention

The system must explicitly define when a candle becomes available to the strategy.

For a closed-candle strategy:

```text
Candle t closes
      ↓
Features calculated
      ↓
Model prediction
      ↓
Risk evaluation
      ↓
Potential order
```

The model must not use information from a candle that has not yet closed unless the V1 strategy explicitly defines intrabar execution.

V1 should prefer closed-candle decision-making to reduce ambiguity and leakage risk.

---

# 15. Feature Alignment

Every feature row must correspond to a specific prediction timestamp.

The dataset must maintain:

```text
timestamp
symbol
feature_1
feature_2
...
feature_N
```

Feature rows must remain correctly aligned with the target label.

A one-row shift can materially change model validity and must therefore be tested.

---

# 16. Feature Missing Values

Feature generation may naturally produce missing values at the beginning of rolling windows.

These rows must be explicitly identified.

The system must not silently replace unavailable historical information with arbitrary values.

Acceptable approaches include:

* dropping rows that cannot contain valid features
* using a deterministic transformation explicitly defined in the feature specification

The chosen behavior must remain consistent across research and production.

---

# 17. Feature Versioning

Every production feature set must have a version.

A feature version identifies:

* feature definitions
* calculation parameters
* input schema
* normalization rules
* missing-value behavior
* timestamp convention

Changing a feature definition must produce a new feature version.

Existing research results must continue to reference the original version.

---

# 18. Feature Determinism

Feature generation must be deterministic.

Given:

```text
same dataset
+
same feature version
+
same configuration
```

the Feature Engine must produce the same result.

Feature generation must not depend on:

* current time
* random state
* network responses
* machine-specific hidden state
* mutable external datasets

---

# 19. Feature Validation

The Feature Engine must validate generated features.

Validation must check:

* expected columns
* data types
* timestamp alignment
* missing values
* infinite values
* numerical range where appropriate
* deterministic output
* absence of future information

Invalid feature datasets must not be passed to model training or production inference.

---

# 20. Feature Correlation and Redundancy

Feature selection must consider redundancy.

Highly correlated features should not be included merely because each produces a small historical performance improvement.

The goal is to maintain a compact information set.

Feature selection should favor:

* distinct information
* stability
* economic interpretation
* robustness

over:

* feature count
* in-sample score
* complexity

---

# 21. Feature Selection Process

Candidate features may be evaluated during research.

However, feature selection must follow chronological validation.

The process should be:

```text
Candidate Features
        ↓
Training
        ↓
Validation
        ↓
Robustness Analysis
        ↓
Selection
        ↓
Protected Final Test
```

The final test period must not be repeatedly searched for the best feature combination.

---

# 22. Label Definition

The model target must be explicitly defined.

The label must represent a future trading outcome that the model is attempting to predict.

Examples may include:

* future return over a defined horizon
* future direction
* cost-adjusted future return

The exact production label must be fixed before final model evaluation.

---

# 23. Label Leakage Boundary

Labels are allowed to use future information because they represent the future outcome being predicted.

However:

**Labels must never become model inputs.**

The system must maintain a strict boundary:

```text
Past / Current Information
        ↓
Features
        ↓
Model
        ↓
Prediction
        ↓
Future Outcome
        ↓
Label
```

Not:

```text
Future Outcome
      ↓
Feature
```

---

# 24. Prediction Horizon

The production model must use one explicitly defined prediction horizon.

The horizon must be selected before final evaluation.

It must be consistent across:

* training
* validation
* backtesting
* paper trading
* live trading

Changing the prediction horizon creates a new model/strategy version.

---

# 25. Target Transformation

If the target requires transformation, the transformation must be deterministic and documented.

Examples include:

* return calculation
* binary classification
* thresholded classification

The transformation must not depend on future test-period statistics.

---

# 26. Model Selection Philosophy

QuantOS V1 must favor robustness over model complexity.

The system must not assume that a more sophisticated model is automatically better.

A model is valuable only if its predictive behavior survives:

* out-of-sample testing
* realistic costs
* parameter variation
* market-condition variation
* robustness analysis

---

# 27. Production Model

V1 supports exactly one production model.

Candidate models may be evaluated during research.

Only one model may be promoted to production.

The production model must have:

* model identity
* model version
* training configuration
* feature version
* dataset identity
* training period
* validation period
* final test period
* random seed where applicable
* evaluation results

---

# 28. Preferred Model Complexity

The production model should be relatively low-complexity.

Tree-based gradient boosting, including XGBoost or an equivalent implementation, is an acceptable candidate because it can model nonlinear relationships while remaining practical for the V1 feature budget.

However:

**The architecture does not require XGBoost specifically.**

The production model must be selected by empirical out-of-sample evidence.

A simpler model must be preferred if it produces comparable robust performance.

---

# 29. Model Training

Training must be reproducible.

A training run must record:

* dataset identity
* feature version
* label definition
* model type
* model parameters
* training period
* validation period
* random seed where applicable
* code revision
* configuration version

The resulting model artifact must be uniquely identifiable.

---

# 30. Model Artifact

A production model artifact must contain or reference enough metadata to determine:

* model type
* model version
* feature version
* training dataset
* training period
* label definition
* model parameters
* preprocessing configuration

The model must not be deployable without knowing which feature specification it expects.

---

# 31. Model / Feature Compatibility

The production model and production feature version must be explicitly compatible.

Conceptually:

```text
Model vX
   ↓
requires
   ↓
Feature Version Y
```

If the running Feature Engine produces an incompatible version, inference must fail safely.

The system must never silently substitute incompatible features.

---

# 32. Model Inference

Inference must be deterministic under the same model and feature inputs.

The Alpha Engine must record enough information to identify:

* model version
* feature version
* prediction timestamp
* symbol
* model output

Inference failures must not generate a valid trade proposal.

---

# 33. Prediction Output

The model output must be transformed into a normalized internal prediction representation.

The representation may include:

* timestamp
* symbol
* prediction
* probability where applicable
* expected return where applicable
* model version
* feature version

The exact representation must remain aligned with the selected production model.

---

# 34. Trading Decision Rule

The model prediction alone does not automatically create a trade.

The Alpha Engine must apply the production decision rule.

Conceptually:

```text
Features
   ↓
Model Prediction
   ↓
Decision Threshold / Rule
   ↓
Trade Proposal
```

The decision rule must be fixed and versioned.

It must not be dynamically optimized during live operation.

---

# 35. Threshold Selection

If a prediction threshold is used, it must be selected using training/validation data.

The final test period must remain protected.

Threshold selection must account for:

* transaction costs
* trading frequency
* false signals
* expected edge
* robustness

A threshold that produces excellent in-sample performance but fails out-of-sample must be rejected.

---

# 36. Trading Frequency

The production strategy should avoid excessive trading.

Higher trading frequency increases:

* transaction costs
* slippage
* execution complexity
* noise exposure

The system must prefer only signals with sufficient expected edge after realistic costs.

---

# 37. Long / Flat / Short Scope

V1 is Binance Spot.

Therefore, the production strategy must operate within Spot constraints.

The strategy must not assume:

* leverage
* short selling through futures
* margin
* liquidation mechanics

The production trading state must remain compatible with Spot account mechanics.

---

# 38. Position State

The Alpha Engine must be aware of relevant current trading state where required for decision generation.

However:

* Risk owns risk limits.
* Execution owns order state.
* Account/exchange state is sourced through the appropriate infrastructure boundary.

The Alpha Engine must not independently invent account state.

---

# 39. Strategy Simplicity

V1 uses one production strategy.

The strategy should contain only the minimum logic required to transform model output into an actionable proposal.

The system must avoid combining many independent signal generators.

The following are not required:

* strategy ensembles
* multiple alpha engines
* regime-specific strategy trees
* dozens of entry rules
* automatic strategy switching

---

# 40. Overfitting Controls

Overfitting is a primary design risk.

The following controls are mandatory:

1. Small feature set
2. Single production model
3. Single production strategy
4. Chronological validation
5. Protected final test period
6. Realistic transaction costs
7. Robustness testing
8. Limited parameter search
9. Reproducible experiments
10. No repeated tuning against the final test period

---

# 41. Parameter Search

Parameter optimization must remain constrained.

The system must not perform unlimited searches over:

* feature combinations
* indicator periods
* thresholds
* model hyperparameters
* trading rules

A large search space can produce apparently strong results by chance.

Research must prioritize a small number of economically motivated hypotheses.

---

# 42. Multiple-Testing Awareness

If many candidate features, models, thresholds, or strategies are evaluated, the probability of finding a false positive increases.

Research records should therefore preserve the history of meaningful experiments.

A single successful backtest must not automatically be treated as evidence of a durable edge.

---

# 43. Out-of-Sample Requirement

A candidate production model must demonstrate acceptable performance on data that was not used to fit its parameters.

Out-of-sample results must be evaluated separately from:

* training results
* feature-selection results
* parameter-search results

The final test period must remain protected.

---

# 44. Walk-Forward Compatibility

The Feature and Model system must support walk-forward training and inference.

Conceptually:

```text
Window 1
Train → Validate → Test

Window 2
Train → Validate → Test

Window 3
Train → Validate → Test
```

Each window must respect chronological ordering.

Future information must not enter earlier windows.

---

# 45. Model Stability

The production model should demonstrate stability across reasonable market periods.

Evaluation must consider whether performance is concentrated in:

* one short period
* one market regime
* a small number of trades
* a single symbol
* a small number of extreme outcomes

A strategy dependent on a tiny number of exceptional trades should be treated as fragile.

---

# 46. Cross-Symbol Consideration

V1 supports:

* BTCUSDT
* ETHUSDT

The system must distinguish between:

* genuinely generalizable behavior
* behavior that exists only on one symbol

A model may be evaluated jointly or separately depending on the frozen V1 strategy design, but the chosen approach must be explicit and reproducible.

The system must not silently mix symbol behavior.

---

# 47. Transaction-Cost Awareness

The model/strategy evaluation must consider realistic transaction costs.

A signal must not be considered useful merely because:

```text
gross expected return > 0
```

The relevant question is:

```text
expected return
− fees
− slippage
− execution effects
> 0
```

where appropriate.

---

# 48. Model Promotion

Promotion must follow:

```text
Candidate Model
      ↓
Training
      ↓
Validation
      ↓
Walk-Forward Testing
      ↓
Robustness Testing
      ↓
Protected Final Test
      ↓
Paper Trading
      ↓
Production Approval
```

A model must not skip validation because of strong historical performance.

---

# 49. Model Rejection Conditions

A model must be rejected when evidence indicates:

* data leakage
* unstable performance
* poor out-of-sample performance
* unacceptable drawdown
* negative cost-adjusted expectancy
* excessive parameter sensitivity
* dependence on a small number of trades
* failure under reasonable robustness tests
* inability to reproduce results
* feature/model incompatibility

---

# 50. Research Run Requirements

Every meaningful model experiment should create a research-run record.

The record must identify:

```text
Dataset
Feature Version
Label Version
Model Type
Model Parameters
Training Period
Validation Period
Test Period
Code Revision
Configuration
Metrics
Result
```

This is the primary Qlib-inspired addition to the V1 research workflow.

The purpose is to make experiments reproducible rather than to reproduce Qlib's entire framework.

---

# 51. Research Artifact Separation

Research artifacts must be separated from production artifacts.

Conceptually:

```text
Research
├── Candidate Features
├── Candidate Models
├── Experiments
└── Evaluation Results

Production
├── Approved Feature Version
├── Approved Model
└── Approved Strategy Configuration
```

Research artifacts must never automatically enter production.

---

# 52. Production Inference Path

The production inference path must remain small:

```text
Validated Market Data
        ↓
Feature Engine
        ↓
Production Feature Version
        ↓
Production Model
        ↓
Production Decision Rule
        ↓
Trade Proposal
        ↓
Risk Engine
```

No research experiment manager, hyperparameter search, notebook, or Qlib runtime may sit in this path.

---

# 53. Failure Behavior

The Alpha system must fail safely.

If any of the following occurs:

* feature calculation failure
* invalid feature
* model loading failure
* incompatible feature version
* inference failure
* invalid prediction
* missing required input

then:

```text
No Trade Proposal
```

The failure must be logged.

---

# 54. Testing Requirements

Feature tests must include:

* calculation correctness
* timestamp alignment
* rolling-window correctness
* missing-value handling
* deterministic output
* leakage detection

Model tests must include:

* artifact loading
* feature compatibility
* deterministic inference
* invalid-input behavior
* output validation

Strategy tests must include:

* threshold behavior
* trade-proposal generation
* boundary conditions
* no-signal conditions

---

# 55. Feature Leakage Test

The system should include automated leakage tests.

A useful test principle is:

> Changing future observations must not change a feature value that occurs before those observations become available.

For example:

```text
Modify data at t + 1
        ↓
Recalculate feature at t
        ↓
Feature at t must remain unchanged
```

If it changes, the feature implementation is leaking future information.

---

# 56. Reproducibility Test

A research run should be reproducible by rerunning:

```text
Same Dataset
+
Same Feature Version
+
Same Model Configuration
+
Same Random Seed
+
Same Code Revision
```

The resulting model and evaluation should be equivalent within explicitly defined numerical tolerance where exact bitwise equality is not practical.

---

# 57. Feature and Model Acceptance Criteria

The V1 Feature and Model system is compliant when:

* production features remain within the 10–20 feature limit.
* every production feature is deterministic.
* every production feature is versioned.
* look-ahead bias is prevented.
* labels are separated from features.
* train/validation/test periods remain chronologically separated.
* normalization does not use future data.
* one production model exists.
* one production strategy exists.
* the model and feature versions are explicitly compatible.
* model artifacts are reproducible and identifiable.
* transaction costs are included in evaluation.
* model selection uses out-of-sample evidence.
* robustness testing is required.
* parameter search remains constrained.
* research experiments are recorded.
* research artifacts cannot silently enter production.
* Qlib is optional and offline only.
* Qlib is not required for live inference.
* model/feature failures result in no trade proposal.
* the complete Alpha path remains small and understandable.

---

# 58. Final Feature and Model Statement

QuantOS V1 deliberately avoids building a complicated machine-learning platform.

The production signal system is:

```text
Validated Market Data
        ↓
Small Deterministic Feature Set
        ↓
One Validated Model
        ↓
One Simple Decision Rule
        ↓
Trade Proposal
        ↓
Risk
```

The objective is not to maximize model sophistication.

The objective is to determine whether a **small, reproducible, cost-aware model can produce a durable trading edge without relying on leakage, excessive optimization, or fragile complexity**.

The Feature Engine, Alpha Engine, and Model workflow must therefore favor:

**less complexity, fewer parameters, stronger validation, and better evidence.**

The system should only become more sophisticated after V1 demonstrates that the simpler system actually works.
