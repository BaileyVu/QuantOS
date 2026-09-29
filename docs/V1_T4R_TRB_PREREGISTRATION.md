# V1-T4R immutable TRB preregistration

Frozen before any TRB strategy outcome is observed. This is research authorization,
not selection, paper, Testnet or Mainnet approval. TSMOM's archived NO_GO and
preregistration are immutable. No AF4 inputs or outputs are used.

## Published family and exact rules

Gerritsen, Bouri, Ramezanifar and Roubaud (2020), The profitability of technical
trading rules in the Bitcoin market, Finance Research Letters 34, 101263:
https://doi.org/10.1016/j.frl.2019.08.011
https://dirkgerritsen.nl/uploads/gerritsen_et_al_2020_bitcoin_trading_rules.pdf
This motivates the family, not an assumption of profit or an exact replication
of every published experiment. The user's long/cash interpretation governs here.

Exactly N=50,150,200; no other variants or optimization. On completed day t,
resistance=max(close[t-1]..close[t-N]); support=min(close[t-1]..close[t-N]).
CASH requests BUY only when close[t]>resistance. LONG requests SELL only when
close[t]<support. Equality and all non-decision minutes HOLD. No shorting,
pyramiding, hidden Alpha economic state or filters. Execution owns holdings.
Current close is excluded from its own range. Decisions occur at UTC midnight
following a completed day, with fills only through the existing V1-T2 path.
An incomplete initial day never creates a daily close. Version trb-daily-v1;
strategy btc-trading-range-breakout-v1-Nd; metadata deterministic-trb-no-ml-v1.

## Data and periods frozen without outcomes

The archive index begins 2017-08-17 04:00 UTC. Backward continuity discovery found
an actual missing interval 2023-03-24 12:40 through 13:59 UTC. No filling,
interpolation or silent repair is permitted. The connected canonical dataset
starts 2023-03-24 14:00 UTC and ends 2026-09-27 23:59 UTC, with every old
2024-2026 candle required to match exactly. Publication identity is bound in the
immutable run manifest before calibration. This shorter history reduces power;
it never lowers the evidence requirement.

Training: [2023-03-24 14:00 UTC, 2025-01-01 00:00 UTC).
2025 validation: four chronological quarterly folds, Jan1-Apr1, Apr1-Jul1,
Jul1-Oct1, Oct1-Jan1 2026, with expanding training ending at each fold start.
Each fold begins flat, uses only previous training calibration and all permitted
pre-fold daily warmup. Last day decisions are assigned by their midnight decision
timestamp, so the training end close cannot produce a training execution at the
validation boundary. Full pre-2026 calibration is required for any holdout winner.
Historical backtest is the first 2025 quarter through V1-T2; walk-forward covers
all four quarters (the first is the same run, not new independent evidence).
No 2026 performance before all candidate comparisons and selection are frozen.
Holdout: [2026-01-01 00:00 UTC, 2026-09-28 00:00 UTC). One selected candidate,
once; no retuning. Final open positions remain marked, not fabricated exits.

## Costs and economic calibration

Fee per fill 0.001; adverse slippage baseline 0.002, stress 0.004 per fill.
No BNB discount; both entry and exit costs count. Capital20 USDT, entry9.90,
maximum position notional10, exposure0.51, daily loss0.02, drawdown0.10,
sell fraction1. Historical quantity step0.00001 is a simulation assumption,
not an exchange filter; Testnet/real orders use current exchange information.

Training samples are non-overlapping complete CASH-to-LONG-to-CASH signal episodes
at completed daily closes, with terminal unfinished episodes explicitly censored.
Warmup is not a trade. Episode gross return=exit_close/entry_close-1; it is not
breakout magnitude. All outcomes end before calibration becomes available.
Minimum12 completed episodes per training window. Twelve is a minimum safeguard
against a handful of long-duration cycles being presented as repeatable evidence;
minutes or days in a position are never counted as independent trades. Small
samples below this count fail, regardless of attractive mean returns.

Use deterministic stationary block bootstrap of episode returns: 10000 draws,
mean block length2 episodes, fixed seed240929, same sample length as original.
Report arithmetic sample mean, sample count, censoring, and the lower empirical
1/60 quantile of bootstrap means (familywise one-sided 5% / three candidates).
This is an empirical robustness estimate under local dependence, not a guaranteed
distribution-free confidence bound; the sample gate and later chronological tests
remain required. Block sampling accommodates dependence between neighboring
trend episodes. This replaces the prior TSMOM dispersion formula for the different
event structure, before any TRB outcomes. No retry with another estimator.

Reserve stressed exit drag (1-0.004)*(1-0.001) in the calibrated estimate:
gross_edge_rate=(1+lower_bootstrap_mean)*(1-0.004)*(1-0.001)-1.
Risk separately charges entry cost0.005004 under stress. This conservative
expected gross entry-return estimate reserves exit cost; it is not a prediction
of tomorrow's return. If the minimum sample count fails, or calibrated edge
<=0.005004, no economic entry is eligible. Persist artifacts; do not replace
missing evidence with a constant. A failed initial calibration prevents promotion;
subsequent stages are explicitly NOT_RUN where they depend on that calibration.

## Validation and selection

Causal fixtures must pass first. Each eligible candidate then uses V1-T2 Backtest,
quarterly chronological WF, stress, MC in that order. Initial and every expanding
calibration must pass. Baseline and stress aggregate net profit and mean completed
trade PnL must be positive, profit factor>1, maximum drawdown<=0.10, at least
4 completed out-of-sample trades pooled across 2025, at least3 of4 profitable
folds, and no single fold contributing over75% of total positive fold profit.
Report net return, expected trade PnL, profit factor, drawdown, Sharpe, Sortino,
trade count, win rate, fees, slippage, exposure and fold stability. Undefined
required metrics fail, never become zero or infinity silently.

MC uses existing V1-T2 stationary return bootstrap, 1000 simulations, seed240929,
mean block length43200 minutes (30days), lower-tail5%; positive outcome fraction
>=0.60 and 95th-percentile drawdown<=0.10. No trade-order shuffle ignoring duration.
Among passing candidates choose lowest worst stress-fold drawdown, then highest
stress aggregate net profit, then smallest N. Win rate is reported, not ranked.
Holdout must meet positive net profit/mean trade PnL, profit factor>1, drawdown
<=0.10 and >=2 completed trades under baseline and stress; no second candidate
may be tried after a holdout failure. Continuous paper and Testnet remain separate.

If all fail: NO_GO_ALPHA. Track B continues independently. Mainnet economic
trading is forbidden throughout this recovery sprint.
