# V1-T5 prospective two-candidate protocol

This protocol and `configs/v1t5_preregistration.json` are frozen and SHA-256
recorded before any candidate economic evaluation. The amendment commit is
`7a522e2`. No economic outcome may change these rules. This is a bounded QuantOS
adaptation, not a claim to reproduce either source paper's reported results.

## Data and chronology

Use only checksum-verified Binance Spot BTCUSDT canonical 1m Parquet inputs.
The 80-minute 2023-03-24 REST recovery attempt returned the two overlap minutes
only. Do not manufacture the missing interval. Earlier archive gaps also split
segments. Each continuous segment has an ordered partition/content manifest;
adjacent partitions join only after exact continuity and overlap checks. Separate
segments are never joined for features, labels, training or test folds.

Development ends 2025-01-01 exclusive. The untouched prefinal interval is calendar
2025. Final holdout is [2026-01-01, 2026-09-28) UTC, opened once for the selected
candidate only after development, bootstrap and 2025 gates pass. Integrity work
may read 2026 source records but cannot compute strategy performance. Source
coverage through 2026-09-28 is integrity-only beyond the holdout end.

Generate prospective calendar-month boundaries from the validated segment
manifest. A uses 6 months training, 1 validation, 1 test, rolling monthly. B uses
up to 12 months training, 3 validation, 3 test, rolling quarterly; when a segment
does not support 12 training months use all available complete training months,
with a fixed minimum of 6. This is a prospective adaptation to observed data
gaps, not a parameter search. Skip insufficient segments. Initial training starts
at the first full UTC calendar month in each segment. No fold crosses a gap.
All exact eligible boundaries and input hashes are frozen in the dataset/fold
manifest before economics. Each test interval is evaluated once per predeclared
cost scenario, with no parameter changes. Later folds may train on prior OOS
dates as in normal prospective rolling learning; no reported PnL enters fitting.

2025 uses a single frozen prefinal model, only after development passes. A fits
2024-06-01 through 2024-11-30 and selects on December2024; B fits 2023-10-01
through 2024-09-30 and selects on October-December2024. Both training windows
must exist within one segment. No model refit or tuning occurs from 2025.
If access is earned, 2026 uses that exact selected artifact unchanged; there is
no model refit, tuning or family reselection from holdout data.

## Features and supervised models

UTC 30m/60m bars require exactly 30/60 contiguous completed canonical minutes.
Only fully completed bins enter inference. Use the same transformations for
historical and Paper inputs. No external macro inputs or indicator additions.

The finite universe has 39 columns: log-close returns at 1/2/4/8/16/48 bars;
return volatility at 4/16/48; body/range/upper/lower wick divided by close;
log1p-volume changes at 1/4/16/48; log-volume z-score and relative volume at
4/16/48; close location in high/low range and log-close relative to its rolling
mean at 4/16/48; log1p trade count; quote/base VWAP deviation from close; and
64-term fractional differences of log-close and log1p-volume at .25/.5/.75/1.
All rolling windows include the current completed bar and past bars only.

Within each training fold, choose the smallest FD order whose absolute lag-one
correlation is <=.8, otherwise 1. This is an explicitly heuristic persistence
screen, not an ADF stationarity claim. Retain the 31 base columns and these two
FD columns, remove training standard deviation <=1e-12, then greedy redundant
columns with absolute training correlation >.98 in declared feature order.
Normalize by training-only mean/std and clip standardized inputs to [-10,10].
Freeze selected identities, FD orders, scaling state and model parameters in
each artifact. Report cross-fold selection frequencies and importance. Importance
is descriptive, never a test-result feature-selection mechanism.

A: supervised denoising autoencoder with width32 encoder, bottleneck8 or16,
width32 decoder and three-class linear head. Paired configurations are exactly
(horizon4, barrier1, bottleneck8, noise0) and (horizon8, barrier2, bottleneck16,
noise.05). TBL thresholds use trailing16 log-return volatility times sqrt(horizon)
and barrier multiplier; future high/low first passage determines class. Same-bar
double touches resolve negative. Unresolved labels are neutral; insufficient
future windows are excluded. These are labels, not assumptions of barrier fills.
Loss is cross-entropy plus .1 reconstruction MSE, Adam .001, L2 .0001, 15 epochs,
chronological batches256, deterministic CPU seed1729. Select by validation
cross-entropy only, ties first configuration. No validation refit.

B: XGBoost regression of next-hour close/current close minus one. Configurations
are depth2/100 trees and depth3/200 trees; eta .03, minimum child weight20,
subsample/column fraction1, alpha0, lambda10, hist trees, one thread, seed1729.
Select validation MSE only; ties first configuration. No validation refit.

All target windows must finish inside their own training/validation interval.
Purge the full maximum target horizon at each boundary; forward-only folds mean
no future training block exists, so no additional reverse-fold embargo is needed.
Features may use past segment warmup but may not cross gaps. Future perturbation,
fold-local fitting, label purging, reproducibility and replay tests must pass.

## Decisions, costs, accounting and risk

A targets LONG only when positive class is the unique largest probability and
>=.6; otherwise CASH. Calibrate class-conditional gross horizon returns using
purged training observations only. Probability-weighted class returns minus an
exit cost reserve supply the entry-edge estimate to Risk. This is an estimate,
not proof of realized expectancy. B targets LONG iff forecast>0; a change requires
abs(forecast)>2*effective_change_cost*abs(target-current). Reserve exit cost in
its entry edge. Negative class/forecast never permits shorts. No pyramiding.

Decisions use complete bars and execute one completed minute later at that
minute's close with adverse slippage. No instantaneous signal-close or perfect
barrier execution. Risk rechecks that completed minute and authoritative account
state. Canonical TradingStep, RiskEngine, ExecutionEngine, fill accounting and
Evaluation metrics own all economic transitions; no research PnL substitute.
Historical and Paper replay must match. Per-minute marked equity captures
drawdown; report residual open positions separately, never invent closing fills.

Five mandatory cost scenarios are 10/15/20/25/30bps each change; 15 baseline,
30 stress. Explicit fee10bps with no BNB discount, remainder modeled adverse
slippage. Report exact multiplicative fee/slippage costs as well as this nominal
ladder. Market orders only, quantity step .00001 BTC and minimum notional5 USDT
as frozen conservative research filters; these are not claimed historical
exchange-filter observations. Existing Execution owns actual Paper/live filters.

Initial capital20 USDT, fixed baseline buy notional10, exposure cap60%, maximum
position1 BTC, daily loss5%, drawdown20%, no pyramiding. These are one baseline,
not a sizing optimization. Canonical daily-loss/drawdown stops prevent new buys;
they do not promise guaranteed stop-loss execution. Sells require owned balance;
dust remains owned and marked. Risk rejection remains final.

## Evidence gates and selection

Use arithmetic aggregate independent-fold net PnL divided by initial capital
times fold count for aggregate return; also report time-linked fold returns.
Report daily Sharpe/Sortino annualized365, completed-trade expectancy/PF/win rate,
change count, exposure, fees, slippage, drawdown and open-position PnL. A fold
begins flat with20; no unreported transfer of positions across fold boundaries.

Development requires positive baseline and stress aggregate returns, baseline
PF>1.10, baseline Sharpe>.50, stress Sharpe>0, positive completed-trade and
change expectancy, >50% profitable folds, no fold >40% of summed positive fold
PnL and no trade >20% of summed positive trade PnL, maximum drawdown<=20%, and no
causal/parity/Risk failure. No trade-concentration exception is elected.

Stationary bootstrap: 10,000 replications, mean block10 UTC days, seed1729,
resample within folds (never across gaps), aggregate each sampled fold. Require
positive 5th-percentile mean return; with <100 position changes require positive
1st percentile. Report terminal equity, drawdown, loss streak, 5% expected
shortfall, baseline/stress returns and impairment at20/35/50/70%. Bootstrap
cannot rescue a failed mandatory economic gate.

Only candidates passing development proceed to calendar2025 exactly once. 2025
requires positive baseline/stress return and expectancy, PF>1.10, baseline
Sharpe>.50, stress Sharpe>0, drawdown<=20%, bootstrap support and all safety
tests. Stability/concentration gates apply to the combined pre-2026 folds.
Select one by lexicographic stress expectancy, profitable-fold fraction, lower
drawdown, PF, changes/day, then B's simpler architecture. This implements the
specified priorities without a post-hoc weighting fit. Neither passes =>
NO_GO_ALPHA, no rescue family, no 2026 access.

Sizing study only after positive pre-2026 OOS expectancy: allocations10/25/50/75/
100%, identical signals/costs and canonical accounting, with cost-affordable
sizing (100% means cash after fee reserve). Report all requested risk metrics,
including time underwater, worst rolling drawdown, losses and capital impairment.
Fractional Kelly is descriptive only when mean/variance are estimable; never
activate it. Most aggressive defensible research allocation requires drawdown
<=20% and bootstrap probability of20% impairment<10%; otherwise no allocation
recommendation. No sizing result may change the baseline holdout candidate.

Selected candidate's final2026 confirmation requires positive baseline/stress
return and expectancy, drawdown<=20%, no causal/parity/Risk failure. Freeze
artifacts and package for Paper only if it passes. Mainnet LOCKED / NOT_APPROVED.

## Source inspiration

[Bieganowski and Slepaczuk](https://arxiv.org/abs/2411.12753) motivates the SAE,
fractional features and TBL family. [Bysik and Slepaczuk](https://arxiv.org/abs/2606.00060)
motivates hourly prediction and transaction-cost filtering. Their published
performance is not QuantOS evidence; this sprint independently evaluates Spot
long/cash models with explicit execution delay, fees and slippage.

## Frozen input manifest and eligible development folds

Dataset manifest SHA-256: `bf017d59b505e4e6cc7e1d67c0ca763380dc646c80f40e6beb69630746072a22`.
24 validated continuous segments; nine partitions with partial-minute closes are quarantined. The two segments supporting folds start 2021-12-25 and 2023-03-24 14:00 UTC. The REST/archive overlap candles match exactly; the left overlap candle is itself incomplete. No gap was repaired.

| Candidate | Train start | Validation start | Test start | Test end exclusive |
|---|---|---|---|---|
| A | 2022-01-01 | 2022-07-01 | 2022-08-01 | 2022-09-01 |
| A | 2022-02-01 | 2022-08-01 | 2022-09-01 | 2022-10-01 |
| A | 2022-03-01 | 2022-09-01 | 2022-10-01 | 2022-11-01 |
| A | 2022-04-01 | 2022-10-01 | 2022-11-01 | 2022-12-01 |
| A | 2022-05-01 | 2022-11-01 | 2022-12-01 | 2023-01-01 |
| A | 2022-06-01 | 2022-12-01 | 2023-01-01 | 2023-02-01 |
| A | 2022-07-01 | 2023-01-01 | 2023-02-01 | 2023-03-01 |
| A | 2023-04-01 | 2023-10-01 | 2023-11-01 | 2023-12-01 |
| A | 2023-05-01 | 2023-11-01 | 2023-12-01 | 2024-01-01 |
| A | 2023-06-01 | 2023-12-01 | 2024-01-01 | 2024-02-01 |
| A | 2023-07-01 | 2024-01-01 | 2024-02-01 | 2024-03-01 |
| A | 2023-08-01 | 2024-02-01 | 2024-03-01 | 2024-04-01 |
| A | 2023-09-01 | 2024-03-01 | 2024-04-01 | 2024-05-01 |
| A | 2023-10-01 | 2024-04-01 | 2024-05-01 | 2024-06-01 |
| A | 2023-11-01 | 2024-05-01 | 2024-06-01 | 2024-07-01 |
| A | 2023-12-01 | 2024-06-01 | 2024-07-01 | 2024-08-01 |
| A | 2024-01-01 | 2024-07-01 | 2024-08-01 | 2024-09-01 |
| A | 2024-02-01 | 2024-08-01 | 2024-09-01 | 2024-10-01 |
| A | 2024-03-01 | 2024-09-01 | 2024-10-01 | 2024-11-01 |
| A | 2024-04-01 | 2024-10-01 | 2024-11-01 | 2024-12-01 |
| A | 2024-05-01 | 2024-11-01 | 2024-12-01 | 2025-01-01 |
| B | 2022-01-01 | 2022-07-01 | 2022-10-01 | 2023-01-01 |
| B | 2023-04-01 | 2023-10-01 | 2024-01-01 | 2024-04-01 |
| B | 2023-04-01 | 2024-01-01 | 2024-04-01 | 2024-07-01 |
| B | 2023-04-01 | 2024-04-01 | 2024-07-01 | 2024-10-01 |
| B | 2023-07-01 | 2024-07-01 | 2024-10-01 | 2025-01-01 |
