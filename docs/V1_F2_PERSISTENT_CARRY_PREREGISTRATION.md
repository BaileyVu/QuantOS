# V1-F2 persistent perpetual carry preregistration

The explicit scheduled user instruction authorizes this research sprint on
`codex/v1-f2-persistent-carry` from `1498a463460060960c161c134133f43f1845ead4`.
It replaces the V1-F1 research-family restriction for this sprint only; all six
production modules, safety boundaries and promotion requirements remain intact.
Frozen 000–008 documents and AF4B/AF4C files are not modified. This is research
authorization, not production-strategy or trading approval.

## Evidence and chronology

Reuse the exact checksum-bound V1-F1 hourly perp/index/mark and actual funding
archives and trailing-liquidity candidate table. The machine configuration binds
their identities. No unchanged bulk data is reacquired; no current symbol list
is projected backward. Research is an **economic factor simulation**, with
continuous notionals and 20 USDT initial reporting equity, not exchange-order
replay. Historical filters are not an Alpha prerequisite.

2020 is warmup. Four causal annual tests cover 2021–2024, starting flat each year
and closing at the final available hourly open (December 31 23:00 UTC). Capital
compounds across folds in combined reporting. Data after 2024 is inaccessible
to this evaluator. These years were already observed in V1-F1: this adaptive
follow-up is development evidence, not independent confirmation. A positive
result must earn untouched 2025 pre-final and January–August 2026 final tests.
No fitted score, random split, new lookback search or test-fold tuning.

## Universe and exactly five carry inputs

Reuse top 30 by prior 720 complete perp hours' quote volume, >=10M USDT/day.
Within that list require 720 complete perp hours, 168 complete index/mark hours,
and at least 15 actual funding settlements in the preceding 168 hours. Funding
is available with a one-hour lag; its last observation must be no older than
its recorded interval + one hour. Reject settlement gaps exceeding the larger
adjacent interval + one hour. Keep at most 25 valid symbols; require >=15.
Missing dependencies exclude that symbol at that decision, not unrelated data.

At each 8h UTC decision compute only:

1. Negative latest settled funding normalized to 8h: `-rate * 8 / interval`.
2. Negative arithmetic mean of normalized funding observations over 168h.
3. Funding persistence: mean negative sign of those normalized observations.
4. Latest completed basis `(index_close - perp_close) / perp_close`.
5. Basis persistence: mean sign of the preceding 168 hourly basis observations.

Average tied cross-sectional ranks are in [0,1]. Carry score is the weighted
sum with weights **0.20 / 0.35 / 0.15 / 0.20 / 0.10**. No other signal, momentum,
TA, ML or parameter search. Funding smoothing is event-weighted; actual interval
changes remain in accounting. Symbol order resolves selection ties. A completely
flat cross-section cannot initiate a basket.

## Exactly three portfolios

- **A:** two highest-score long slots and two lowest-score short slots;
  half gross on each side, equal within side.
- **B:** two carry long slots plus a BTCUSDT short beta hedge.
- **C:** two carry long slots plus equal-notional BTCUSDT/ETHUSDT short beta hedges.

For B/C exclude hedge symbols from carry selection. Estimate each eligible
contract's beta using prior 719 hourly log returns and sample covariance /
sample variance against BTC returns (B), or the arithmetic mean of BTC/ETH log
returns (C). Require beta strictly >0 and <=3, positive benchmark variance,
complete trailing prices and valid hedge context. Require >=15 candidates after
these exclusions. Beta is a risk hedge estimate, never a momentum Alpha feature.
Basket beta is the average long-slot beta. Target long gross is `1/(1+beta)`;
hedge gross is `beta/(1+beta)`. No fourth variant or alternate hedge lookback.

## Persistence, hysteresis and switching hurdle

Decide every eight hours; apply changes one hour later at perp open. Each side
enters from its extreme ceil(20% of eligible count), minimum two. A held slot
inside its extreme ceil(40%) band remains. Outside the hold band, propose the
best unheld entry-band replacement, in slot order. Replace only if its forecast
incremental funding over **21 eight-hour intervals (seven days)** exceeds twice
the full stress switch friction: `2 * (2 fills * 15 bps) = 60 bps` per switched
notional. The comparison is strict. Forecast uses the observed 168h mean rate,
with the correct long/short sign. It assumes no basis convergence or future
known funding. It is a fixed forecast horizon, not a forced seven-day exit.

If the hurdle fails, retain the existing eligible slot, even outside the hold
band. No time-based close/reopen. Missing held eligibility or an insufficient
universe forces the whole portfolio flat; initial entry may be considered at
the next decision. Safety exits are never blocked by the economic hurdle.

A second, whole-portfolio check is mandatory for a replacement: use completed
reference prices and the same causal mean funding rates (including BTC/ETH
hedges) to compare seven-day forecast funding of the proposed weights against
current holdings. Incremental carry must exceed twice estimated costs of **all**
net quantity changes, including retained-leg resizing and hedge changes, at
5+10 bps with adverse fee notionals. Otherwise retain membership. Thus the
slot-level hurdle cannot ignore a costly hedge adjustment. This is a prospective
estimate at decision time; execution prices arrive one hour later.

Unchanged membership preserves quantities and hedge ratio. Beta target weights
are refreshed only when membership changes. Changes trade only net quantity
deltas; retained quantities are not artificially closed/reopened. A rebalance
also proportionally reduces quantities if gross exceeds equity, with an explicit
cost reserve; it never increases quantities solely to restore drifted exposure.
Membership changes size a gross <=1 basket after reserving conservative maximum
fill friction on old and target notionals. Gross can drift between decisions;
report both gross and net drift, not continuous dollar/beta neutrality.

## Accounting and failures

Hourly price PnL is quantity times successive perp reference-price changes.
Equity additionally marks the current perp/mark difference. Charge every actual
funding event to pre-trade holdings; an event at entry is excluded and one at
exit is included. Exact rates and timestamps are retained; the containing-hour
mark open remains the disclosed funding valuation proxy inherited from V1-F1.
Never claim the precise exchange settlement mark.

Each delta pays adverse slippage and taker fees on its adjusted fill notional.
Crossing through zero attributes the closing cost to the former side and opening
cost to the new side. Keep price/funding/fees/slippage and long/short components
separate and reconcile them to terminal equity. Initial capital compounds only
through actual evolving positions; no invented repeated trade returns.

If a new leg lacks valid entry data, cancel the entire adjustment before any
fill, retaining only previously valid holdings. Missing held perp/mark or stale
funding forces all legs out at the previous trustworthy close with >=10 bps exit
slippage. Include actual funding before that exit boundary, excluding the missing
hour's boundary. No forward fill, synthetic funding or naked partial basket.
The last-observation data-loss exit is a retrospective research convention,
not a realizable-production-fill assertion. Last-trustworthy failure stops.

Costs per fill are exactly baseline **5 fee + 2 slip**, stress **5 + 10**, and
severe **7 + 10** bps. No maker discount, BNB benefit or other cost search.

## Reporting and 1x gates

Report every variant/cost/fold: compounded return, daily arithmetic expectancy,
daily PF, Sharpe/Sortino (365-day annualization), hourly maxDD, daily CVaR5/worst
day, components, actual fills/notionals, rebalance timestamps with economic
quantity changes, average completed position-spell duration, active-hour coverage,
data-loss exits and gross/net drift. Cash days remain. Undefined ratios are null.
Position spells continue through partial reductions and end on full exit/sign
change. Daily PF replaces arbitrary closed-trade episodes for persistent holdings.

Compare turnover to archived V1-F1 basis-24h-2x2 at matching costs, never rerun
V1-F1. F2 normalized turnover sums traded notional / pre-adjustment equity;
F1's stored comparator uses episode-start equity for its entry and exit. The
ratio is an explicitly approximate normalized comparison, not an identical
execution-path counterfactual. Also report actual fill counts and notionals.

All gates must pass: positive mean daily and compounded net return in all three
cost cases; stress daily PF >1; stress one-sided mean-daily block-bootstrap lower
bound >0; >=3 of four stress folds positive in both mean and compounded return;
stress maxDD <=35%; active hours >=80%; normalized turnover reduction >=50%;
data-loss exits <=1% of economic-change timestamps. Bootstrap uses 10,000 circular
seven-day blocks, seed 20260930 + zero-based A/B/C ordinal, alpha .05/3 per variant.
This three-way adjustment does not erase the V1-F1 adaptive-development context.
No post-result gate changes. Failure of every variant means **NO_GO_ALPHA** and
stops economic development; no leverage or holdout rescue.

If multiple qualify, select largest stress lower confidence bound, then more
positive folds, smaller CVaR loss, smaller DD, higher PF/Sharpe, less turnover,
and A before B before C. At most one finalist.

## Conditional risk and later stages

Machine config freezes inherited gross surfaces 1/2/3/5/8/10/15/20, risk budgets
0.5/1/2/3/5%, free-collateral reserves 20/35/50%, and safety multiples 2/3/4.
As specified in the V1-F1 preregistration conditional-risk section, planned
adverse distance is clipped volatility-based, isolated margin is equally split
without top-ups, historical maintenance assumptions are 5% plus 1.5% liquidation
fee and stressed at 10%, and adverse hourly mark excursions determine possible
liquidations before optimistic stops. These are uncertain historical assumptions,
not exchange-bracket replay. Apply the same persistent delta execution/accounting
to risk surfaces; never substitute forced rebalance turnover.

Only after 1x qualification: implement these conditional studies; drawdown
throttle from development 1x block MC; 10,000 seven-day block paths retaining
intraday excursions/funding; impairment 20/35/50/70%, ruin <=10% initial equity,
recovery and margin stress. Require zero historical liquidations, MC liquidation
and ruin probabilities <=0.1%, loss50 probability <=1%, MC95 maxDD <=35%.
Select highest leverage passing all survival gates, preferring lower risk budget,
larger reserve and safety multiple on ties, never highest terminal return.

Earn 2025 then Jan–Aug 2026 once, selected frozen configuration only, all three
cost cases mean/compounded positive, stress PF>1, maxDD<=35%, zero modeled
liquidations, >=80% coverage, <=1% data-loss decisions. No retuning on holdouts.
Then query actual current filters/brackets/fees for exactly 20 USDT; do not raise
risk merely for minimum notional. No safe deployment means
ALPHA_PASS_CURRENTLY_NOT_EXECUTABLE_AT_20_USDT. Only a qualified strategy may
earn minimal Futures execution/offline safety, appropriate-credential Testnet
and Paper. No Mainnet credentials or orders; **LOCKED / NOT_APPROVED**.

Source, configuration, this document and dataset identities are hashed and
archived before economic access. A one-shot marker protects the consumed test
periods. All generated evidence stays outside Git under the V1-F1 data root's
`v1f2-persistent-carry` subdirectory. No push, merge, V1-F1 branch modification,
AF4B/AF4C access or other strategy-family work is authorized.
