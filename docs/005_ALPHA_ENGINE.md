# QuantOS Core — 005_ALPHA_ENGINE.md

Version: 1.0.0-V1
Status: Frozen V1
Last Updated: 2026-08-19

V1-F1 amendment (2026-09-30): 000 §0 supersedes prior sole-family and no-ML
restrictions below. The only newly eligible family is
`perp-cross-sectional-basis-v1`, comparing pure basis and its nested price/volume
composite at exactly 8h/24h and 1x1/2x2. Basis is `(index - perp) / perp` or log
equivalent; long high ranks, short low ranks. Factor scores, regularized linear
ranking or gradient boosting are permitted inside that family. Select at most
one final strategy and zero or one trained model. Alpha emits ranking, expected
relative return and uncertainty; Risk alone approves direction and sizing.
The dated-futures motivation does not establish a perpetual edge. Require
independent 1x OOS after-cost/funding qualification before leverage study. Do not
reopen earlier failed experiments. This amendment grants no trading approval.

## 1. Objective

The Alpha Engine converts the approved feature vector into one production trading decision.

V1 has exactly one live strategy.

The system must not operate multiple independent live strategies or fuse a collection of unrelated strategy modules.

## 2. Strategy Shape

Amendment: 2026-09-29 — Human-authorized V1-T4 deterministic Alpha exception.
`published-tsmom-v1` is the sole authorized production-strategy family for this
phase, limited to preregistered 7/14/30 completed-UTC-day lookbacks and exactly one
selected production strategy. It requires no trained ML model. This exception
removes the ML prerequisite for the constrained momentum baseline; it permits no
additional lookbacks, indicators, thresholds, feature searches or strategy families.
Future introduction of ML requires separate authorization. Stable `model_version`
metadata remains `deterministic-tsmom-no-ml-v1`; this does not represent a trained
model. Reproducible pre-holdout entry-edge calibration remains mandatory; momentum
is not expected edge. Insufficient evidence of positive edge after realistic costs
means `NO_GO_FOR_LIVE`. All safety, six-module ownership, canonical-data and
promotion requirements remain in force, including Backtest, Walk-Forward, Monte
Carlo, Paper, Binance Testnet and explicit Mainnet approval. This amendment grants
none of those approvals and makes no profitability claim.

Amendment: 2026-09-29 — V1-T4R Recovery Sprint, explicitly authorized by the user.
The preserved published-tsmom-v1 experiment remains NO_GO_FOR_LIVE, with its
criteria and holdout unchanged. For this recovery phase only, the sole newly
eligible production family is btc-trading-range-breakout-v1, restricted to
50/150/200 prior completed UTC daily closes, strict support/resistance breakouts,
and long/cash position persistence. Select at most one strategy. It has no trained
ML model; deterministic-trb-no-ml-v1 is metadata only. This separate authorization
supersedes the T4-only family restriction for TRB and no other family or parameter.
Freeze calibration, costs and selection before outcomes; a failed Alpha is
NO_GO_ALPHA and does not stop independent Spot execution/Testnet infrastructure.
Synthetic Testnet intents validate infrastructure only and cannot be selected for
Mainnet. All safety and validation gates remain mandatory. Mainnet economic
trading is prohibited in this sprint; later enablement requires successful Alpha,
2026 holdout, continuous Paper, Testnet, reconciliation and explicit micro-live
approval. Future ML or any further strategy family requires separate authorization.

Outside that exception, the production strategy is a compact, rule-controlled ML-assisted strategy:

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

Only one model artifact is active in production when ML is separately authorized. V1-T4 has no trained model artifact; it persists strategy and entry-edge calibration artifacts with explicit deterministic version metadata.

Candidate models may be benchmarked in research, but research candidates never affect live execution until explicitly selected, validated, versioned, and promoted.

## 4. Target Definition

The model-target requirements in this section apply when a trained model is authorized. V1-T4 instead calibrates completed-trade gross entry returns using permitted chronological training data only.

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

The exact action must be derived from the approved strategy configuration and model output, or the authorized V1-T4 deterministic momentum score and immutable account context.

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

Training must be chronological. For V1-T4 this applies to entry-edge calibration; it does not authorize ML training.

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
