# V1-F3 carry Risk preregistration

This user-authorized USD-M research experiment branches from F2 final
`350c3d5f5cc190bb7f5fd86f2bc05c9e62b3e202`. The user's F2/F3 instructions
authorize the persistent-carry research family after the F1 scope amendment.
This sprint does not edit frozen specifications, production modules, Execution, AF4B/AF4C, or live
authority. Mainnet remains **LOCKED / NOT_APPROVED**. No exchange orders.

## Alpha and data freeze

Use unchanged `research/v1f2/carry.py`, its Variant A only, and the unchanged
F2 preregistration. No new signal, rank, membership or switching parameter.
The F2 simulator supplies a chronological shadow intent stream. Risk consumes
only fills through the current execution hour. Actual Risk equity, vetoes and
resizing never feed back into Alpha membership or its switching hurdle. Alpha
continues while Risk is flat; Risk can resume its existing intent later.
This explicit separation preserves identical Alpha for all twelve candidates.

Use validated immutable F1 data, point-in-time candidate universe and F2 costs.
The 92 missing mark rows in the frozen universe recovery audit are overlays on
copied arrays; original Parquet remains unchanged. Verify raw response hashes,
authoritative daily archive CHECKSUM and agreement with REST OHLC. No recovered
funding events were found. All five staleness cases retain original F2 semantics.
Report original F2 and recovered F2 separately. Recovery can legitimately change
subsequent eligibility, so replay frozen Alpha on recovered data after sealing.

## Exactly twelve configurations

Cartesian grid: annual volatility target **20/30/40%**, throttle
**moderate/defensive**, carry-per-risk threshold **0/0.10**. No other search.
Risk quantities are a single nonnegative scalar times existing Alpha weights.
Uniform scaling preserves leg names, directions and relative weights; no hedge,
replacement strategy, fourth portfolio variant, or leverage above 1x is added.

At each unchanged eight-hour decision t, use hourly closes in `[t-720,t)`;
719 log returns, sample covariance annualized by 8760. Portfolio volatility is
sqrt(w' covariance w). Annual expected carry is -1095 times the sum of each
weight times the unchanged smoothed eight-hour funding estimate. Require
strictly positive carry and carry/volatility >= the configured threshold.
No additional basis forecast is invented. Missing history means Risk flat.
Execution is at t+1 as in F2; current execution quotes are used for sizing only.

For marked equity drawdown bands <10%, 10–20%, 20–30%, 30–35%, >=35%:
moderate multipliers are **1, .75, .5, .25, 0**; defensive are
**1, .5, .25, .1, 0**. At >=35%, flatten and lock permanently for that
experiment. Development high-water mark and lock carry across annual folds;
only the frozen F2 Alpha positions reset at fold boundaries. Holdout periods
each begin at fresh 1x capital/high-water mark, with the selected configuration.

Uniform scale is the minimum of: 1, target/volatility, .25/max symbol absolute
weight, (.4*target)/max absolute Euler volatility contribution, .5/max cluster
gross, 1/gross, .15/absolute net BTC beta, .10/absolute net weight, .55/long
gross and .55/short gross. Zero denominators do not bind. Multiply this minimum
by the drawdown throttle. Correlation clusters are connected components of
absolute trailing correlation >=.80. BTC beta uses the same completed returns.
Caps are tested after costs when scheduled orders are submitted. Marked gross
<=1 and the drawdown lock are additionally monitored hourly; passive price
drift can change other exposures between decisions. A discrete price jump can
cross the 35% boundary: record the full realized drawdown, never clip it.

Reserve conservatively for delta fees/slippage and basis when sizing. Charge
only actual quantity changes. Actual historical funding uses F2 event timing,
entry-exclusive/exit-inclusive boundaries and conservative data-loss exits.
No maker assumptions. Turnover is traded perp notional divided by pre-trade
settlement equity, accumulated across timestamps; also report absolute USDT
notional. Economic changes count unique timestamps, including all emergency
and end-fold exits. Holding spells persist through same-direction resizes.

## Gates and selection, fixed before outcomes

Development is 2021–2024 only. Preserve the F2 non-DD safeguards: positive mean
and compounded return at all three costs; stress daily PF >1; at least three
positive stress folds; active >=80%; turnover reduction >=50% versus F1
basis-24h-2x2. Require stress maxDD <=35%, no risk-rule violation at any cost,
and a positive seven-day circular block bootstrap lower bound on mean stress
daily return (10000 paths, seed20260930, one-sided alpha .05/12). This accounts
for the bounded twelve-candidate search. Daily net returns define expectancy
and PF consistently with F2; CVaR is mean worst 5% daily returns.

Do not relax the 1% data-loss gate. At every cost require loss-exit timestamps
/ min(actual economic-change timestamps, original F2 same-cost timestamps)
<=1%. The original denominators are baseline824, stress821, severe819.
Extra Risk rebalancing cannot manufacture a larger denominator.

Among complete passes, minimize stress maxDD, then CVaR loss; maximize number
of positive folds, then retained proportion of original F2 stress return;
prefer moderate throttle, lower eligibility threshold and lower vol target on
remaining ties. Positive expectancy is a prerequisite, never sacrificed for
low drawdown. Select exactly ONE, or return NO_GO_ALPHA without opening holdouts.
Do not retune failures or rerun consumed economic periods.

## Conditional untouched periods and leverage

Seal the chosen candidate and development report hash before acquiring or
opening calendar2025. Evaluate it once with fresh capital under the same
causal history, costs and risk code. Require positive stress compounded return,
positive daily expectancy, PF>1, maxDD<=35%, no risk violation and loss exits
<=1% of actual economic changes. Failure returns NO_GO_ALPHA, leaving2026 shut.
Only a 2025 pass permits Jan1–Sep1(exclusive)2026 once, with identical gates.
No retuning, additional candidates or threshold changes after either access.

Only both passes permit leverage1/2/3/5/8/10/15/20. Retain F2 preregistered
isolated-margin, maintenance-margin stress, liquidation buffers, collateral
reserves, 10000 seven-day block Monte Carlo paths, severe-impairment and ruin
limits. Also run stationary blocks with mean length seven days. Choose the
lowest leverage that meets the intended risk limits; a higher terminal return
alone is not grounds to raise leverage. Dynamic policy uses the selected
frozen DD throttle. Verify current20-USDT exchange minimums only if earned.
No Testnet execution implementation before Alpha + Risk qualification.

All source/configuration/data/audit identities are sealed externally before
the first new economic result. Generated market data, ledgers and results stay
outside Git. The evaluator fails closed on repeated access or seal mismatch.
