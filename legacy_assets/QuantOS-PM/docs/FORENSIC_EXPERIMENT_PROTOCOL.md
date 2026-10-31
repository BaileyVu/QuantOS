# Fixed experiment protocol for the forensic audit

This protocol specifies separate sensitivity experiments. It does not authorize
changing production defaults, recalibrating the estimator, loosening a gate, or
selecting parameters from their results. All artifacts stay inside QuantOS-PM.
There is no live execution or assumed bid-price liquidation.

## Data status and comparison rule

`fixtures/btc_5m.jsonl` is six deterministic synthetic mechanics cases, already
inspected during development and this audit. It is **not held-out data**. Its
resolution events permit exact realized accounting, but its frequencies are not
empirical market probabilities. The separate favorable synthetic calibration is
not evidence of expectancy in the fixture or in live markets.

Use the existing fixture to verify mechanics and report descriptive sensitivity
results, explicitly labeled in-sample synthetic. Do not rename a retrospective
subset, transformed fixture, or new hand-built fixture as held out. A genuine
held-out comparison requires a new, untouched dataset supplied or captured after
freezing the candidate manifest. Until then, that part of the task remains
unverified.

Run the unchanged control and each candidate on exactly the same events, receipt
order, capital grid ($200/$350/$500), and execution models (optimistic/baseline/
pessimistic). Start each run with fresh engine state. Preserve the calibration,
fill rules, latency multipliers, queue depletion, fee assumptions, risk halts,
venue minima, one-entry rule, and conservative marking. Do not stack candidates.
Report the full grid, including zero-trade and adverse results; do not choose a
winner from the same evaluation data.

## Fixed candidates

The following modest values are design choices for one-factor experiments, not
values optimized to the six observed outcomes. Record their exact implementation
and hash before evaluating any new holdout.

| ID | Single changed policy | Exact definition |
|---|---|---|
| control | None | Original configurations and engine rules. |
| quote_5 | Smaller quote ceiling | Replace only `quote_size=20` with `5`. Existing equity sizing, minimum size checks, and all risk checks still apply. Never round upward to meet a minimum. |
| inventory_75pct | Tighter unmatched budget | Multiply only the original unmatched budgets by 0.75: per-market 2% → 1.5% of current equity, aggregate 3% → 2.25%. Apply the same 1.5% budget in proposed size sizing, then recheck the original conservative projected one-leg reservation using the tighter bounds. Matched budgets remain 10%/20%. Sizing is an implementation of the tighter budget, not a second independent optimization. |
| combined_097 | Stricter entry price | Require UP bid + DOWN bid ≤ $0.97, replacing the current $0.99 ceiling. Keep the original positive calibrated lower-bound gate. Do not alter quote prices, tick handling, or fills. |
| liquidity_queue | Minimum displayed liquidity and queue filter | At entry, require displayed best-bid size and best-ask size on **each** leg ≥ twice that market's minimum order size. Require raw visible bid queue at/above each proposed price ≤ twice proposed share size. Keep the calibration's original maximum raw queue of 20 and original pessimistic queue multiplier. Reject missing/unverified observations. This is a proxy filter, not proof of queue position or executable exit liquidity. |
| exposure_deadline_5s | Earlier response to outstanding inventory | On the first unmatched fill, record an exposure episode. If unmatched inventory persists for five seconds, request cancellation of all remaining market orders at the first actual event at/after the deadline. Keep holdings, mark conservatively, and settle only on the supplied resolution. Never fabricate a sale. Report actual reaction latency and exposure-duration violations; this policy cannot guarantee a five-second holding period. |
| expiry_120s | Earlier cessation | Reject new quotes and request cancellation when remaining time is ≤120 seconds, replacing the original 60-second cutoff. Existing cancellation delay and fill races remain effective. |
| cancel_dominant | Conservative inventory-increase cancellation | After processing each event and merging, if one outcome quantity exceeds the other, immediately request cancellation of still-pending orders on that dominant side only. Leave the deficit-side order eligible for real simulated fills under original prices, queue, TTL and risk checks. Do not replace, chase, enlarge, or cross orders. Retain full original cancellation lag and reservations until acknowledgement. |

The shorter-exposure and cancellation variants are deliberately distinct. A
shorter quote lifetime does not establish a shorter inventory holding period.
The engine has no sell-order or measured executable-liquidation mechanism; an
experiment promising a hard maximum holding duration is not implementable with
the existing evidence. Its feasibility and cost require additional observations.

The original fixture contains identical 0.49/0.49 initial quotes, thin uniform
books, one early entry per tradable market, and no later replenishment of quote
eligibility. Consequently a threshold variant can skip every market, and the
liquidity/expiry/deadline variants may produce little or no change. These are
coverage limitations, not evidence that a filter works or fails generally.

## Isolated execution and preservation

1. Hash original input configurations, calibration, fixture, reports and journals
   before work; verify hashes afterward. Existing reports and journals are inputs
   only. No baseline evidence is overwritten.
2. Store the fixed manifest, policy adapter, implementation hash, original input
   hashes and generated outputs under a new audit evidence directory. Prefer an
   immutable dataclass replacement for the quote ceiling and audit-only strategy,
   risk and cancellation adapters for the other variants. Do not modify default
   config files or original simulation economics.
3. Apply only the selected adapter. A gate/cancel request must use currently
   observable data, never future fixture trades or resolution. If an audit hook
   introduces a new timer schedule, use the identical causal schedule for control
   and every candidate, label this as a new control, and separately reproduce the
   historical control. Do not silently improve old cancellation timing.
4. Preserve cancellation-pending fills, uncertain state, and remaining-order
   collateral reservations. Selective dominant-side cancellation requires an
   explicit single-order cancel request; calling the existing market-wide cancel
   function is not equivalent. Journal request time, due time, acknowledgement,
   reason, and policy ID.
5. Reconstruct each result from its journal with the same independent accounting
   reconciler as the original nine runs. Confirm the control reproduces all
   original outcomes before interpreting sensitivities.

## Required outputs and decision criteria

For every capital/model/policy combination report realized pair profit,
unmatched UP/DOWN settlement P&L, open-position marking, fee/slippage/operating
allowance treatment, ending equity, reconciliation residual, orders and fills,
matched/unmatched quantities, time-weighted and maximum exposure, utilization,
and actual holding/reaction duration. Separate actual ledger costs from
estimator-only allowances; show any hypothetical cost overlay as a distinct net
scenario and apply it consistently to all candidates.

Define market skip percentage as markets with no submitted orders divided by all
markets in that evaluation dataset. Also show the denominator, reasons, and the
percentage among markets eligible for the unchanged control. Give both
unconditional return per supplied market and conditional return per entered
market. An all-skipped policy has zero trading P&L and no trading-edge evidence.

The completed-pair payoff and four fill categories must retain partial-fill
amounts. "Both legs had some fill" is not "both full" and can leave directional
risk. Report event counts and share quantities together. Never use the synthetic
calibration's 2,100 rows as the evaluated fixture's outcome counts.

A positive in-sample result does not establish positive expectancy. For a new
holdout, freeze candidate code and manifest, hash the holdout before execution,
record acquisition time/provenance, run every candidate once, and report all
outcomes. Uncertainty must be clustered by independent market/day blocks; six
mechanics cases are inadequate for inference or promotion.

## Next empirical evidence

Obtain a sufficiently broad chronological capture of verified BTC five-minute
markets with complete token/rule/fee metadata, sequenced snapshots and deltas,
aggressor-identified trades, event and receipt times, reconnect/gap records,
independent resolutions, and enough variation near entry/expiry and queue
conditions. Public displayed depth alone cannot establish queue rank or fills.
Validate queue/latency assumptions independently and record cancellation races.
To test bounded holding time, obtain executable opposite-side depth and trade
evidence over the proposed exit interval, with measured delay, slippage and fees;
do not impute executions from displayed bids. Collect no rewards/rebates unless
their amounts and eligibility are verified. Keep calibration and evaluation
periods separate and never retrain on the final holdout.
