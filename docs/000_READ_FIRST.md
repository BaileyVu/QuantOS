# QuantOS Core — 000_READ_FIRST.md

Version: 1.0.0-V1
Status: Frozen V1 Source of Truth
Last Updated: 2026-08-19

## 0. V1-F1 amendment — 2026-09-30

Explicit human authorization changes the V1 production target to **Binance
USD-M USDT perpetual futures**, LONG / SHORT / FLAT, isolated margin, one-way
position mode, a point-in-time multi-symbol universe, and Risk-controlled dynamic
leverage. This section supersedes conflicting Spot-only, BTC/ETH-only,
long/cash-only, no-derivatives, no-leverage, and earlier sole-family/model
restrictions throughout documents 000–008. Earlier phase descriptions below are
retained as historical context; they do not authorize reopening those experiments.
The amendment authorizes research and conditional implementation, not promotion.

### Scope and ownership

- The sole eligible family is `perp-cross-sectional-basis-v1`: pure basis and
  nested basis-plus-price/volume composite. Basis is `(index - perp) / perp`
  (or its log equivalent); long the highest ranks and short the lowest.
- The complete structural search is 8h / 24h horizons and 1x1 / 2x2 equal-weight
  long/short portfolios. Select at most one final configuration.
- Within this family, explicit factor scores, regularized linear ranking, and
  XGBoost/gradient boosting are authorized research choices. Prefer simplicity
  unless complexity earns chronological OOS improvement. Production uses zero
  trained models for a deterministic score or exactly one selected trained model;
  no ensembles or additional strategy families are authorized.
- Use approximately the top 15–30 liquid eligible contracts at each decision,
  with historical listing/tradability, trailing-history/liquidity requirements,
  executable filters/notional, and no stablecoin/stablecoin instruments. Freeze
  the exact rule before outcomes. Current listings and future volume must not
  determine past eligibility.
- Preserve local operation, exactly six business modules, Clean Architecture,
  Parquet + DuckDB, UTC, immutable versioned history, completed 1-minute inputs,
  causal higher-timeframe aggregation, the 20-feature ceiling, Paper default,
  and initial reference equity of **20 USDT**.
- Alpha owns ranking, expected relative return, and uncertainty. Risk owns
  direction approval, notional, leverage, isolated margin, collateral reserves,
  and liquidation buffers. Model confidence alone must not set leverage.
- Execution alone submits orders and owns authoritative account/position state.
  Risk precedes Execution; a rejection is final. Preserve stable identities,
  duplicate suppression, UNKNOWN reconciliation, restart recovery, credential
  security, and fail-closed behavior. Coordinate both portfolio legs; partial-leg
  failure requires deterministic recovery/flattening rather than uncontrolled
  naked leveraged exposure.

### Evidence and promotion

- Before economic results, commit this amendment separately and freeze/hash
  `V1_F1_PERP_ALPHA_PREREGISTRATION.md` plus machine-readable configuration and
  exact evaluated source identity. Determine usable data coverage before freezing
  periods. Prefer development 2020–2024, untouched pre-final 2025, and final 2026
  through an explicit completed date only where data supports those periods.
- Use authoritative Binance historical sources with provenance and checksums
  where available: perp/index/mark/premium klines, funding, trades, volume,
  metadata, and optional historically trustworthy open interest. No invented
  observations or silent outage filling; quarantine invalid partitions.
- Derivatives-native causal basis, realized funding, relative momentum,
  price/volume structure, liquidity, and volatility features are authorized.
  Future funding is never a feature. Account for actual historical funding
  timestamp, position notional, and long/short sign separately from price PnL.
- Total return is price PnL + funding PnL - fees - slippage. Verify the ordinary
  USD-M taker fee before outcomes; initial assumptions are 5 bps fee and 2 bps
  slippage per fill, stress 5 + 10 bps, plus fee-only reporting. No VIP, BNB, or
  unsupported maker-fill benefits.
- Qualify credible positive OOS expectancy at **1x effective gross exposure**
  after baseline costs/funding with stress resilience first. Failed Alpha means
  `NO_GO_ALPHA` and stops economic development. Leverage cannot rescue failure.
- Only after 1x qualification study gross leverage 1/2/3/5/8/10/15/20x, equity
  risk budgets 0.5/1/2/3/5%, collateral reserves 20/35/50%, and liquidation
  safety multiples 2/3/4. Never raise exposure to satisfy exchange minimums or
  silently substitute more than 20 USDT equity.
- Model isolated initial/maintenance margin, brackets, liquidation price/distance,
  unrealized PnL, and funding accrual. Liquidation is never the planned stop.
  Require liquidation distance > safety multiple × planned adverse exit distance.
  Historical bracket uncertainty requires disclosed conservative assumptions;
  current/Testnet execution queries actual filters and brackets.
- Freeze deterministic block/stationary Monte Carlo, impairment/ruin and
  liquidation definitions, costs, seeds, chronological walk-forward, selection,
  and gates before outcomes. Derive dynamic sizing and normal/reduced/minimum-risk/
  flatten-lock drawdown thresholds from development data before final holdout.
  Report component PnL, stability, concentration, tail risk, drawdown, margin,
  liquidation, impairment/ruin probabilities, and recovery times. Select by
  stress expectancy, stability, tail survival, drawdown, risk-adjusted performance,
  capital efficiency, then simplicity; never maximum terminal equity alone.
- No random temporal splits, global scaling, future universe/features, test-fold
  tuning, post-outcome gate changes, or unearned holdout access. Each test period
  is evaluated once. Preserve Research → Backtest → Walk-Forward → Monte Carlo
  → Paper → Explicit Live Approval; require Futures Testnet as well.
- Build only minimal futures Execution adaptation after Alpha and leverage/risk
  qualification. Offline safety tests precede Futures Testnet, which may use only
  appropriate locally available credentials. Missing credentials do not negate
  an earned Alpha pass. Never request secrets in chat.
- **Zero Mainnet economic orders. Do not read/use Mainnet futures credentials.**
  Real capital remains untouched. Mainnet is **LOCKED / NOT_APPROVED** until
  separate explicit authorization after Alpha, risk/leverage, final holdout,
  Futures Testnet, and Futures Paper success.

Cross margin, COIN-M, options, borrowed Spot margin, martingale, grid averaging,
uncontrolled pyramiding, other exchanges, and all unrelated V1 exclusions remain
prohibited. AF4B/AF4C remain independent: do not inspect or reuse their worktrees,
results, or artifacts. Do not reopen TSMOM, TRB, SAE/TBL, Spot hourly XGBoost, or
V1-T5. Store this sprint's data/evidence outside Git under the authorized V1-F1
data root. No push or merge is authorized.

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

V1 has one production model except for the V1-T4 exception below.

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

Outside V1-T4, and only with separate authorization to introduce ML, LightGBM is the preferred candidate because it fits tabular market features, is fast on commodity hardware, and remains comparatively explainable.

Other models may be benchmarked during research only. A benchmark does not become production merely because it has a higher in-sample score.

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
