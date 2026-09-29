# V1-F1 perpetual Alpha preregistration

This registers one **economic factor simulation**, not historical exchange-order
replay. The authoritative methodology amendment is 000 §0.1. Historical filters
do not gate this experiment. No strategy PnL was inspected before this document,
configuration, source and data identities were sealed. The external
`preregistration.lock.json` binds their exact SHA-256 values; evaluation refuses
changed inputs and creates a one-shot development-consumption marker.

## Sources, chronology and universe

Use Binance public USD-M monthly `klines/1h`, `indexPriceKlines/1h`,
`markPriceKlines/1h`, and `fundingRate`, each ZIP bound to its published checksum.
Keep raw bytes and immutable canonical Parquet outside Git; DuckDB remains an
available read-only analytical layer. Listing, file and canonical hashes are
preserved. Current exchangeInfo is never an input to historical selection.
Discovery may enumerate archive names through the present; a contract contributes
only after enough of its actual historical observations have arrived.

All times are UTC, with completed hourly bars. Data begins September 2019 for
warmup. 2020 is development/warmup; annual walk-forward test folds are 2021,
2022, 2023 and 2024. Each uses expanding prior history and causal rolling state;
no fitted coefficients or thresholds are retrained. No outcomes crossing a fold
boundary are included. Evaluate the eight configurations together once, with
the registered cost sensitivities; never tune to an individual test fold.
2025 is untouched pre-final. Final holdout is 2026-01-01 through 2026-09-01
exclusive (complete monthly archives through August). Those observations are
not acquired/evaluated unless earlier gates are earned.

Pre-outcome coverage audit established 9,670 valid perp partitions spanning 394
historical symbols. Context requests cover 3,100 symbol-months / 226 candidate
symbols: 9,259 archives succeeded, 41 absent archives returned 404; invalid-row
quarantine count was zero. At least 15 context-eligible contracts exist at
1,074/1,095 (2021), 1,083/1,095 (2022), 1,089/1,095 (2023), and 1,098/1,098
(2024) scheduled 8h decisions. This supports the frozen 2021–2024 test periods.
The machine config binds perp/context manifest and candidate-table identities.

At UTC 00/08/16 for 8h or 00 for 24h, rank contracts by mean daily quote volume
over exactly the preceding 720 valid complete perp hours. Require at least
10 million USDT/day. Candidate pool is the top 30, ties by symbol ascending.
Only ASCII alphanumeric USDT archive names without delivery suffixes qualify;
exclude the stablecoin bases listed in the machine config. This is a fixed
instrument-class rule, not a current-survivor filter.

Within the top 30, require the latest 25 complete index/mark hours and a known
funding observation available at least one hour before the decision. Latest
funding cannot be older than its recorded interval plus one hour. Take the
first 25 eligible by liquidity. Fewer than 15 means cash for that rebalance.
Future entry prices, future funding or future survival cannot affect selection.
Missing required observations affect the decisions whose dependencies use them;
they do not invalidate unrelated symbols or the entire research interval.
The earlier minute-level January gaps do not invalidate authoritative hourly
data; any remaining invalid hourly dependencies are excluded individually.

## Exactly eight configurations; no model search

Variants A/B × horizons 8h/24h × portfolios 1x1/2x2. A ranks
`(index_close - perp_close) / perp_close`. Long highest, short lowest.
B is nested in A: 0.5 × basis rank + 0.2 × negative historical funding rank
+ 0.2 × perp 24h momentum rank + 0.1 × taker imbalance rank. Use average
cross-sectional ranks normalized to [0,1], computed only within current eligible
contracts. Ties in final selection use symbol ascending; a flat top/bottom score
separation results in cash. No scaling fitted globally or on future periods.

Funding score is minus latest actual rate × 8 / its interval hours. Momentum is
latest completed perp close / close 24 hours earlier - 1. Imbalance is twice
24h taker-buy quote volume / total quote volume - 1. Also record causal index/
perp relative 24h return, 168h hourly-log-return sample volatility × sqrt(24),
and 30d mean quote liquidity for diagnostics/risk. Seven features total; no OI,
inferred funding forecasts, labels, ML fitting, extra lookbacks or score tuning.

## Portfolio, execution, funding and missing data

Gross entry notional is 1× reference equity: half long/half short; equal legs
within sides. Continuous quantities use target notional / reference entry price.
Decision at t enters at the authoritative perp open at t+1h and normally exits
at t+1h+horizon. Fully close/reopen each horizon, including unchanged names;
no turnover-netting advantage is assumed. Entry failure of any leg cancels the
whole hypothetical basket. No naked partially executed research basket is kept.

Charge signed actual historical funding events strictly after entry and through
exit inclusive. Funding PnL = -signed quantity × mark × actual rate. **Mark is
the authoritative mark open of the event's containing hour**, an explicit hourly
valuation proxy, not a claim to the precise exchange settlement mark. Keep actual
event timestamps and interval changes. Future settlement rates never enter
features. Funding receipt/payment is separated from price PnL and trading costs.

For a held portfolio, first missing required perp/mark hour or funding older
than the latest interval + 1h triggers a coordinated exit of all legs at the
last trustworthy perp close before the missing hour. If failure is discovered
in the entry hour, exit at that known open. Exit slippage is max(scenario,10 bps).
This last-observation convention is conservative within the authorized factor
layer; it is a retrospective data-loss convention, not a production fill claim.
Record these events and keep the portfolio in cash until the next scheduled
decision. Never forward-fill market values or fabricate absent funding.

Price PnL is signed quantity × reference exit-minus-entry price. Slippage is
adverse on each fill. Fees use the actual slippage-adjusted fill notionals.
Freeze the complete Cartesian sensitivity: fees 5/7/10 bps × slippage 0/2/10 bps
per fill. Baseline 5+2; selection stress 7+10; severe 10+10. Fee-only rows use zero
slippage, except mandatory data-loss exit friction. No discounts/maker benefits.
Compound equity between episodes; retain hourly mark valuation between fills,
daily returns, unit-equity episode returns and equity-weighted USDT components.
The initial reporting equity is 20 USDT; historical exchange minimums are ignored
in this economic layer. Quantity does not compound inside a holding episode.

## Gate, reporting and selection

Report every fold/configuration/cost case: return, episode expectancy, PF, daily
Sharpe/Sortino (365-day annualization), hourly maxDD, daily worst return/CVaR5%,
turnover, long/short/net contributions, funding, fees/slippage, per-symbol PnL,
symbol concentration, BTC 30d-up/down regimes, daily beta to BTC, gross/net
exposure drift, insufficient-universe decisions and data-loss/entry failures.
Zero-return cash days remain in daily statistics. Undefined ratios are null.

All conditions are required at 1x: baseline/stress/severe mean daily return >0;
selection-stress lower one-sided 99.375% mean-daily confidence bound >0;
at least 3/4 stress folds positive; stress hourly maxDD <=35%; >=300 episodes;
>=80% scheduled-decision coverage; data-loss exits <=1% of episodes; largest
symbol share of absolute per-symbol PnL <=35%. CI uses 10,000 circular moving
7-day block bootstrap samples, seed 20260930 + configuration ordinal. Alpha
0.05/8 accounts for the eight structural candidates. This is the 1x significance
check, not a claim that leveraged liquidation Monte Carlo has been completed.

No qualifying configuration means **NO_GO_ALPHA** on actual failed economic
evidence and stops economic development. Do not try leverage or open holdouts.
If multiple qualify, choose by stress lower confidence bound, positive folds,
tail robustness, drawdown, PF/Sharpe, capital efficiency, then simplicity
(pure basis before composite; 1x1 before 2x2; 24h before 8h for final ties).
Select at most one final configuration. No gate revisions after outcomes.

## Conditional risk, holdouts and deployment

Only qualified Alpha enters the registered 1/2/3/5/8/10/15/20x gross surface,
0.5/1/2/3/5% equity-risk budgets, 20/35/50% free-collateral reserves and 2/3/4
liquidation/adverse-exit safety multiples. The machine config fixes sizing,
conservative 5% maintenance margin plus 1.5% liquidation fee (10% maintenance
stress), tail gates, impairment/ruin definitions, deterministic 7-day block
10,000-path Monte Carlo and the development-derived drawdown-throttle formula.
For a risk surface, gross notional/equity is capped by both its leverage limit
and equity-risk-budget / planned-adverse-distance, then by the throttle. Split
available isolated margin, equity × (1-reserve), equally across legs; no cross
margin or automatic collateral top-ups. Deduct costs and funding from the
corresponding isolated margin. A leg liquidates conservatively when margin plus
mark unrealized PnL is no greater than (maintenance + liquidation-fee rate) ×
mark notional. Use hourly mark high/low adverse excursions; if planned exit and
liquidation are both possible within an unresolved bar, record liquidation.
Reject initial liquidation distance <= safety multiple × adverse distance.
Near-liquidation means remaining distance <=1.25 × planned adverse distance.
Conditional Monte Carlo resamples whole seven-day blocks with their associated
leg excursions/funding and applies the same margin logic, not scaled terminal
returns alone. Ruin is equity <=10% of starting capital; impairment is minimum
path equity below its registered fractions. Unrecovered paths report censored
recovery duration at horizon end. These conditional studies are not run on a
failed 1x factor.
Historical bracket uncertainty remains explicit; no precise exchange-liquidation
replay claim is authorized. Planned adverse exits always precede liquidation.
At each surface record terminal equity, DD/CVaR/worst day, margin/free collateral,
near/actual modeled liquidations, funding, 20/35/50/70% impairment probabilities,
ruin and recovery time. Highest leverage must pass all survival gates, never
win solely on final equity. Policy is fixed before pre-final/final evaluation.

The selected frozen configuration alone may enter 2025, then the final 2026
window, once each. Require baseline/stress/severe positive daily expectancy,
stress PF >1, maxDD <=35%, no liquidation, data-loss <=1%, decision coverage
>=80%; no retuning or refitting on those periods. Only then query current filters
and brackets for exactly 20 USDT deployment. No safe executable configuration
means ALPHA_PASS_CURRENTLY_NOT_EXECUTABLE_AT_20_USDT. Otherwise minimally adapt
Execution, offline safety tests, then appropriate-credential Futures Testnet.
Credentials missing is not an Alpha failure. Futures Paper follows earned gates.
Risk-before-Execution, final rejection, account ownership, reconciliation and
restart safety remain mandatory. Mainnet stays **LOCKED / NOT_APPROVED**.
