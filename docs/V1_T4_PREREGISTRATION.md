# V1-T4 preregistration — frozen before observing T4 PnL

Registered 2026-09-29 after specification amendment c317fef. This is a phase
protocol, not a strategy promotion. No final holdout has been opened in T4.

Literature motivates the family, not these exact horizons or guaranteed profit:
[Liu and Tsyvinski, Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877)
and [Moskowitz, Ooi and Pedersen, Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum).
The latter studies futures/forwards; QuantOS uses only owned Binance Spot long/cash.

Exactly H = 7, 14, 30 completed UTC days; close[d]/close[d-H]-1, positive LONG,
otherwise CASH. Only the canonical final minute of a completely observed UTC day
can trigger BUY/SELL. Every other minute is HOLD. At least H+1 complete daily
closes are required. An initial partial day is excluded; any later missing or
conflicting minute fails closed. Exact last-minute duplicates are idempotent.
Initial deployment is BTCUSDT only. Any existing BTC balance prevents additional
BUY, including dust. SELL uses existing Risk's 100% owned-fraction flooring.

Daily schema tsmom-daily-v1 keeps at most 31 closes, one last minute, coverage
count and cumulative minute hash. candidate-v1 is unchanged. Runtime bootstrap
feeds historical minutes through this same state with no economic decisions.
The first live minute must immediately follow bootstrap; the exact last minute
may be replayed without another decision. Bootstrap never invents past trades.

Preferred development ends before 2026-01-01T00:00:00Z; final holdout starts there
and ends at the exclusive midnight after the last fully completed available UTC
day before evaluation. Canonical datasets and exact coverage must be bound before
opening PnL. Only the exact owner-authorized canonical candle Parquet
80f9a175259847fd222fceb05ea15a43197bd0bd1da260bbbcc0d18484fb7a50
may be read from the research tree as market-data input. No research results,
other AF4B/AF4C datasets, processes or outputs may be used. Missing source
coverage is missing evidence, never an invented performance result.

Frozen costs: fee 0.001, adverse slippage 0.002 per order, no BNB discount.
Stress: fee 0.001, slippage 0.004. Initial capital 20 USDT, BUY notional 9.90 USDT
(to retain half-budget headroom including costs), full owned-fraction SELL,
maximum added position notional 10 USDT, exposure 0.51, daily loss 0.02 and drawdown
0.10. Never pyramid. Historical quantity step is an explicit simulation input;
live filters must be retrieved, never inferred from that input. No rebalancing
or forced end-period liquidation. Carried final positions remain marked.

Calibration uses complete positive-momentum entry to nonpositive-momentum exit
price episodes entirely within training; open terminal episodes are excluded.
Record every entry/exit timestamp and gross return. Minimum 30 completed episodes.
Estimator: arithmetic mean, sample standard deviation, lower estimate
mean - sqrt(19)*sample_sd/sqrt(n). This is a conservative nominal one-sided 95%
Cantelli estimate under independent finite-variance observations, not a guarantee
under serial dependence. Walk-forward and block Monte Carlo remain required.
To give Risk a per-entry-order meaning, reserve the stressed exit cost against
the terminal proceeds: entry_edge = (1+lower_estimate)*(1-stress_order_cost)-1.
Risk separately subtracts entry cost. Nonpositive or insufficient calibration
produces HOLD, never an invented BUY edge. Freeze final calibration on pre-holdout
training only; its dataset/window/content identity belongs in the artifact.

Coverage correction before any PnL: the owner identified development coverage as
2024-01-01 through 2025-12-31. The provisional 730/180 schedule could produce no
folds and is superseded, before evaluation, by the following fixed schedule.
Walk-forward: rolling 365-day training / 90-day validation / 90-day step,
chronological disjoint validation folds, independent flat accounts under V1-T2.
At least 3 complete folds and at least 30 pooled OOS completed trades required.
Every fold must have eligible training calibration. Backtest uses an earlier
training window and only evaluates subsequent data; never calibrate on the same
interval whose performance is reported as OOS.

Hard development gates: positive pooled OOS net expectancy and net profit,
profit factor >1 and defined; at least 2/3 folds profitable; no fold drawdown >0.10;
no leakage/accounting/reconciliation failure; stressed pooled profit/expectancy
positive and stress drawdown <=0.10. For each validation fold, V1-T2 stationary
bootstrap: 200 simulations, seed 240929, mean block 10080 minutes; 95th percentile
maximum drawdown <=0.10 and positive-outcome fraction >=0.60.

Rank only passing candidates: more profitable folds; lower worst-fold drawdown;
lower sample SD of fold expectancy; higher pooled net return; ascending horizon
as deterministic final tie-break. Freeze exactly one winner before holdout access.
Holdout runs exactly once at baseline costs with the frozen pre-holdout calibration;
require positive net profit/expectancy, defined profit factor >1, at least 10
completed trades, drawdown <=0.10, and the same Monte Carlo gates. Failure is
NO_GO_FOR_LIVE; no tuning, boundary changes or alternate winner after opening it.
Insufficient history/sample is NO_GO_FOR_LIVE with explicit missing-evidence reason.

This protocol cannot establish paper duration/behavior or Testnet proof. Those
require separate recorded evidence before explicit operator Mainnet approval.

Evaluation order: screen all training calibrations first. If no horizon has
sufficient sample and entry edge exceeding stressed entry costs in every fold
and in the final pre-holdout calibration, return NO_GO_FOR_LIVE before PnL
evaluation. Do not bypass Risk to manufacture backtest trades. In that case
backtest/WF-PnL/Monte-Carlo/holdout are explicitly NOT RUN, not zero or PASS.
Exact calendar coverage and numeric protocol are in configs/tsmom.preregistered.toml.
