# QuantOS Core — 005_ALPHA_ENGINE.md

Version: 1.0.0-V1
Status: Frozen V1
Last Updated: 2026-08-19

## 1. Objective

The Alpha Engine converts the approved feature vector into one production trading decision.

V1 has exactly one live strategy.

The system must not operate multiple independent live strategies or fuse a collection of unrelated strategy modules.

## 2. Strategy Shape

The production strategy is a compact, rule-controlled ML-assisted strategy:

```text
Market state
   ↓
Feature Vector
   ↓
Production Model
   ↓
Model Score
   ↓
Strategy Decision Rules
   ↓
Alpha Decision
```

The model informs the strategy; it does not bypass deterministic controls.

## 3. Production Model

Preferred model:

- LightGBM

Only one model artifact is active in production.

Candidate models may be benchmarked in research, but research candidates never affect live execution until explicitly selected, validated, versioned, and promoted.

## 4. Target Definition

The model target must represent a future trading outcome that can be defined without ambiguity.

Target construction must:

- use future data only for label creation;
- never expose future labels as features;
- remain fixed for a given experiment;
- include the intended prediction horizon;
- be documented with the model artifact.

## 5. Signal Semantics

The Alpha Engine produces:

- `BUY`
- `SELL/EXIT`
- `HOLD`

The exact action must be derived from the approved strategy configuration and model output.

A low-confidence or ambiguous prediction must resolve to `HOLD`, not forced trading.

## 6. Expected Edge

The strategy must consider expected return after estimated transaction costs.

A raw positive model prediction is insufficient.

The strategy should trade only when:

`Expected Gross Edge > Estimated Fees + Estimated Slippage + Safety Margin`

The Risk/Execution path performs the authoritative final cost and risk checks.

## 7. Explainability

Each alpha decision records:

- decision timestamp;
- symbol;
- strategy version;
- model version;
- feature version;
- model score;
- threshold/state used;
- resulting action;
- reason for HOLD/REJECT where applicable.

## 8. Regime Handling

V1 may include one compact regime/context state when it is derived from approved features and validated.

Regime classification must not become a collection of separate strategies.

The purpose is to prevent the production model from acting under clearly unsuitable market conditions, not to create a strategy zoo.

## 9. Training Discipline

Training must be chronological.

No random shuffling across time for the final evaluation workflow.

A training run records:

- dataset identity;
- feature version;
- target version;
- training window;
- validation window;
- model parameters;
- random seed;
- software version;
- metrics;
- artifact identity.

## 10. Overfitting Controls

Mandatory controls:

- limited feature count;
- limited model complexity;
- chronological validation;
- walk-forward testing;
- untouched test periods;
- parameter stability checks;
- performance-after-cost analysis;
- Monte Carlo robustness;
- paper trading before live promotion.

A more complicated strategy is not preferred merely because it improves one backtest.

## 11. Production Promotion

A model/strategy candidate must demonstrate:

- positive expected value after costs;
- stable out-of-sample performance;
- acceptable drawdown;
- acceptable trade count;
- robustness across validation windows;
- no material leakage;
- reproducible results.

## 12. Runtime Safety

The Alpha Engine may request a trade.

It cannot:

- bypass Risk Engine;
- submit orders;
- alter account state;
- override execution rejection.
