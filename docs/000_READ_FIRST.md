# QuantOS Core — 000_READ_FIRST.md

Version: 1.0.0-V1
Status: Frozen V1 Source of Truth
Last Updated: 2026-08-19

## 1. Purpose

This document is the highest-priority specification for QuantOS Version 1.

QuantOS is a small, research-driven quantitative trading engine designed to discover, validate, and safely execute one statistically robust trading strategy on Binance Spot.

The goal is not maximum model sophistication. The goal is the smallest production-quality system that can survive live trading, remain reproducible, and be maintained by one developer.

All other documents in `docs/` must remain consistent with this document.

## 2. V1 Mission

Build a production-quality engine that can:

1. ingest historical and live Binance Spot market data;
2. create deterministic features;
3. produce one explainable trading signal;
4. apply strict risk controls;
5. execute orders safely;
6. evaluate and validate the strategy;
7. progress from research to backtest, walk-forward validation, Monte Carlo, paper trading, and finally explicitly enabled live trading.

Target:

- Exchange: Binance Spot
- Symbols: BTCUSDT, ETHUSDT
- Initial capital: 20 USDT
- Primary timeframe: 1 minute
- Deployment: local workstation
- Storage: Parquet + DuckDB
- Architecture: Clean Architecture + Modular Monolith
- Production model: exactly one, except the authorized no-ML V1-T4 strategy in §6
- Production strategy: exactly one
- Production features: evidence-justified complexity, without a fixed numerical ceiling

## 3. Non-Negotiable Principles

### 3.1 Risk before profit

Capital preservation has priority over profitability.

### 3.2 Simplicity before sophistication

Every component must justify its existence with measurable value.

### 3.3 No backtest optimization

Backtests are validation instruments, not proof of profitability.

### 3.4 Reproducibility

Identical data, configuration, model artifact, and execution assumptions must produce identical research and simulation results.

### 3.5 No hidden state

State must be explicit, persisted where required, and observable.

### 3.6 No duplicated business logic

A rule belongs in exactly one authoritative component.

### 3.7 No look-ahead

No feature, label, model, or simulator may use information unavailable at the decision timestamp.

## 4. Exact V1 Production Modules

V1 contains exactly six production modules:

1. Market Data
2. Feature Engine
3. Alpha Engine
4. Risk Engine
5. Execution Engine
6. Evaluation Engine

Parquet/DuckDB storage, configuration, logging, exchange connectivity, and test tooling are infrastructure supporting these modules. They are not additional production business modules.

No new production module may be introduced without an approved specification change.

## 5. Research Discipline

QuantOS may borrow research-discipline ideas from Qlib, but Qlib is not part of the V1 runtime architecture and is not a required dependency.

Adopt only:

- reproducible datasets;
- explicit train/validation/test periods;
- experiment/run metadata;
- saved model and configuration artifacts;
- deterministic feature generation;
- standardized signal and backtest evaluation.

Do not adopt:

- Qlib runtime architecture;
- distributed infrastructure;
- model zoo;
- reinforcement learning;
- autonomous agents;
- portfolio optimization;
- multi-strategy production;
- multi-exchange infrastructure.

## 6. Model Constraint

V1 has exactly one selected production Alpha/model, with historical deterministic
exceptions preserved below. The V1-T5 amendment governs current model eligibility.

Amendment: 2026-09-29 — Human-authorized V1-T5 supervised Alpha research.
QuantOS V1 may use any supervised statistical or machine-learning architecture,
including deep neural networks, provided the exact production candidate is
preregistered before economic evaluation, all inputs and transformations are
causal, fitting/selection occurs only on permitted training data, chronological
out-of-sample validation is performed, realistic trading costs are included,
canonical Risk/Execution semantics are preserved, and the candidate passes every
applicable promotion gate. No tree model or model architecture is preferred by
specification. Supervised autoencoders, representation learning, learned embeddings
and internal latent representations, MLP classifiers/regressors, neural feature
compression, fractional-differentiation features and triple-barrier supervised
learning are eligible under these conditions.

There is no arbitrary fixed feature-count ceiling. Feature/model complexity must
be justified by causal provenance, fold-local fitting, reproducibility,
out-of-sample evidence, stability, ablation/importance evidence where meaningful,
and operational feasibility. More features or greater complexity are not
automatically preferred.

The current V1-T5 tournament contains exactly two authorized families:
`sae-tbl-30m-longcash-v1` and `cost-aware-hourly-xgb-v1`. Select exactly one
production Alpha/model if every applicable gate passes, or declare `NO_GO`.
A third strategy family requires later user authorization. This is not an
open-ended strategy/model search. A different supervised architecture within the
authorized scope does not itself require another authorization; the exact
candidate must still be preregistered before economic evaluation.

This amendment supersedes earlier model-class and feature-count restrictions,
including the T4/T4R-only research-family restrictions for the current sprint.
Historical T4/T4R evidence, criteria, outcomes and holdout records remain unchanged.
Risk-before-Execution, final Risk rejection, Execution-only submission and account
state ownership, deterministic/idempotent execution, reconciliation, duplicate-order
protection, causal completed-candle data, train/validation/test separation, realistic
fees/slippage, walk-forward and Monte Carlo validation, 2026 holdout protection,
Paper, Binance Testnet, and secrets/security requirements remain mandatory.
Spot only: no leverage, futures, margin, short selling, martingale, uncontrolled
pyramiding or withdrawal access. Mainnet remains `LOCKED / NOT_APPROVED` and
requires explicit approval after all applicable evidence gates.

The following T4/T4R clauses document historical phase scope only.

Amendment: 2026-09-29 — Human-authorized V1-T4 deterministic Alpha exception.
`published-tsmom-v1` is the sole authorized production-strategy family for this
phase, limited to preregistered 7/14/30 completed-UTC-day lookbacks and exactly one
selected production strategy. It requires no trained ML model. This exception
removes the ML prerequisite for the constrained momentum baseline; it permits no
additional lookbacks, indicators, thresholds, feature searches or strategy families.
The historical phase did not authorize ML. Stable `model_version`
metadata remains `deterministic-tsmom-no-ml-v1`; this does not represent a trained
model. Reproducible pre-holdout entry-edge calibration remains mandatory; momentum
is not expected edge. Insufficient evidence of positive edge after realistic costs
means `NO_GO_FOR_LIVE`. All safety, six-module ownership, canonical-data and
promotion requirements remain in force, including Backtest, Walk-Forward, Monte
Carlo, Paper, Binance Testnet and explicit Mainnet approval. This amendment grants
none of those approvals and makes no profitability claim.

A benchmark does not become production merely because it has a higher in-sample score.

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
approval. Current V1-T5 eligibility is governed by the amendment above.

## 7. Validation Gate

No live trading is permitted until the same strategy passes:

`Research → Backtest → Walk-Forward → Monte Carlo → Paper Trading → Live Approval`

Skipping a stage is prohibited.

## 8. V1 Exclusions

Excluded from V1:

- other exchanges;
- futures;
- options;
- leverage;
- market making;
- cross-exchange arbitrage;
- portfolio optimization;
- multi-strategy production;
- reinforcement learning;
- autonomous agents;
- news or social sentiment;
- on-chain analytics;
- cloud deployment;
- Kubernetes;
- distributed computing;
- high-frequency trading;
- automatic strategy discovery.

## 9. Decision Hierarchy

When requirements conflict, use this order:

1. Capital preservation
2. Correctness
3. Robustness
4. Simplicity
5. Explainability
6. Performance
7. Profitability

## 10. Definition of Done

A V1 capability is complete only when:

- implementation exists;
- unit tests pass;
- integration tests pass where applicable;
- configuration is documented;
- logging is implemented;
- deterministic behavior is verified;
- validation requirements are satisfied;
- documentation matches the implementation;
- no unapproved feature has been introduced.
