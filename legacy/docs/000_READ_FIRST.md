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
- Production model: exactly one
- Production strategy: exactly one
- Production features: target 10–15, hard maximum 20

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

V1 has one production model.

LightGBM is the preferred candidate because it fits tabular market features, is fast on commodity hardware, and remains comparatively explainable.

Other models may be benchmarked during research only. A benchmark does not become production merely because it has a higher in-sample score.

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
- deep learning;
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
