# QuantOS-PM V0 — P&L forensic audit

**Decision: C — unmatched-leg settlement losses overwhelm completed-pair profits.**
Across the nine original runs, identified accounting-error contribution is **$0**
and terminal conservative-marking contribution is **$0**. Every position is
settled and every reserved dollar is released. This conclusion concerns the
audited fixture and execution paths; it is not a claim of live profitability.

The audit independently reconstructs fills, cost basis, merges, settlement,
cash, collateral and marks at all **576 recorded event checkpoints**. All residuals
are exactly zero. Original core code, configurations, fixture, calibration,
comparison and run artifacts are unchanged: **79 original files hash-verified**.
QuantOS S199 and the original QuantOS project were not accessed or changed.

## Exact terminal attribution

Amounts are USD. Realized total equals pair profit plus settlement P&L. Terminal
unrealized P&L, fees, posted slippage and posted operating costs are **zero for
every row**. Starting equity is the capital column; ending equity is its sum
with total P&L. Positive/negative values retain their signs.

| Capital | Execution model | Pair gross profit | Unmatched UP P&L | Unmatched DOWN P&L | Realized total / total P&L | Ending equity |
|---:|---|---:|---:|---:|---:|---:|
| 200 | optimistic | 0.48365722 | −4.00326521 | 0 | −3.51960799 | 196.48039201 |
| 200 | baseline | 0.48365722 | −4.00326521 | 0 | −3.51960799 | 196.48039201 |
| 200 | pessimistic | 0.48365722 | −4.00326521 | 0 | −3.51960799 | 196.48039201 |
| 350 | optimistic | 0.84640016 | −7.00571424 | 0 | −6.15931408 | 343.84068592 |
| 350 | baseline | 0.84640016 | −7.00571424 | 0 | −6.15931408 | 343.84068592 |
| 350 | pessimistic | 0.80594284 | −7.00571424 | −0.49 | −6.68977140 | 343.31022860 |
| 500 | optimistic | 1.20 | −9.80 | 0 | −8.60 | 491.40 |
| 500 | baseline | 1.20 | −9.80 | 0 | −8.60 | 491.40 |
| 500 | pessimistic | 1.04 | −9.80 | −0.49 | −9.25 | 490.75 |

The [numerical appendix](NUMERICAL_APPENDIX.md) includes every requested share,
order, exposure, utilization and cash-reconciliation field for all nine runs;
54 per-market attribution rows; exact losing orders/timestamps/condition IDs;
and all 72 control/candidate combinations. Full precision, individual orders and
checkpoint timelines are in [baseline_attribution.json](baseline_attribution.json).

## Orders causing the losses

**Market 2:** condition ID
`0x0000000000000000000000000000000000000000000000000000000000000002`,
UP order `virtual-00000003`, buys at $0.49 and settles worthless when DOWN wins
on **2026-07-02 00:05:01.010 UTC**. Losing quantities are **8.169929 / 14.297376 /
20 shares** for $200/$350/$500, costing **$4.00326521 / $7.00571424 / $9.80**.
Optimistic/baseline fills occur at 00:00:03.010; pessimistic fills occur at
00:00:03.010 and 00:00:05.010. Opposite order `virtual-00000004` receives no fill.

**Market 4:** condition ID
`0x0000000000000000000000000000000000000000000000000000000000000004`,
pessimistic $350/$500 buys **12 UP and 13 DOWN**, merges 12 pairs for **+$0.24**,
and loses **$0.49** on the residual DOWN share when UP wins at
**2026-07-04 00:05:01.010 UTC**. DOWN order `virtual-00000008` fills at
00:00:04.010 and 00:00:06.010; the residual is attributable to the latter fill
under FIFO attribution. Market net is **−$0.25**. Source trade timestamps are
10 ms earlier than all listed receipt timestamps.

Relative to optimistic/baseline, pessimism costs another **$0.53045732** at
$350: $0.04045732 less pair profit plus $0.49 residual loss. At $500 the difference
is **$0.65**: $0.16 less pair profit plus $0.49 residual loss. At $200 eventual
fill quantities are unchanged, although partial-fill events and exposure timing differ.

Stale-book marking temporarily records the unpaid cost as unrealized loss.
Resolution transfers that same loss to realized P&L without charging it again.
Pending-order reservations never reduce cash or realized P&L.

## Costs and expectancy

These are simulated maker fills at their limit prices, with recorded fees and
slippage of zero. The estimator's **$0.001 per quoted share combined
slippage/operations allowance** is an admission budget, not a posted cash debit.
There is no supported split between those two costs. Charging it once per
accepted paired opportunity would lower pessimistic P&L to **−$3.559980387 /
−$6.760401446 / −$9.35**. No rebates or rewards are counted.

The simulator is deterministic. Per-market observed indicators for
**both-any / UP-only / DOWN-only / neither** are: markets **1, 3, 4 = 1/0/0/0**;
market **2 = 0/1/0/0**; markets **5, 6 = 0/0/0/1**. Market 5 entered but did not
fill; market 6 was skipped because its minimum size exceeds the risk budget.
These indicators are not independently estimated stochastic probabilities.

Among five entered markets the descriptive frequencies are **60% / 20% / 0% /
20%**; among all six, **50% / 16⅔% / 0% / 33⅓%**. In pessimistic $350/$500 runs,
only 40% of entered markets fill both orders fully; the other 20% both-any case
retains the losing DOWN share. Actual net means per entered market are
**−$0.703921598 / −$1.33795428 / −$1.85** under pessimism. Other models and
unconditional means are in the appendix.

At equal full quote sizes, the observed outcome mix gives
`0.60 × $0.02 − 0.20 × $0.49 = −$0.086` per quoted share before allowances.
This simplified calculation omits unequal sizing and the partial imbalance;
the exact journal-based means above include both. One losing unmatched share
costs $0.49 and needs **24.5 profitable pair-share units** at $0.02 each to
break even, or 25 whole units. An equal-size full one-leg losing episode therefore
requires roughly **25 equal-size successful paired episodes**, versus three
successes per UP-only loss in this fixture. The principal observed UP losses
require **200.1632605 / 350.285712 / 490** completed pair-share units respectively;
actual pessimistic completions total only **24.182861 / 40.297142 / 52**.

Admission calibration uses **1,996 both-full, four one-leg and 100 neither
outcomes out of 2,100 synthetic observations**: 95.0476% both-full and 0.1905%
one-leg. That favorable calibration does not describe the evaluated fixture.
Its positive estimated lower bound does not establish economic edge. Under the
supplied outcome mix, directional losses structurally dominate the $0.02 spread;
real-market expectancy remains unmeasured.

## Separate fixed policy diagnostics

All candidates use the same six fixture markets, fill rules, queue assumptions,
latencies, calibration and original risk gates. Each changes one policy only;
the tighter-budget candidate also sizes consistently with that tighter cap.
No production defaults were changed and no candidate was selected by performance.
As authorized, **held-out validation is unavailable**. Detailed definitions are in
the [fixed protocol](../../docs/FORENSIC_EXPERIMENT_PROTOCOL.md).

| Policy | Pessimistic P&L: $200 | $350 | $500 | Markets skipped |
|---|---:|---:|---:|---:|
| Control | −3.51960799 | −6.68977140 | −9.25 | 16⅔% |
| Quote ceiling 5 | −2.15 | −2.15 | −2.15 | 16⅔% |
| Unmatched budgets ×0.75 | −2.63794153 | −4.61639775 | −7.14675391 | 16⅔% |
| Combined price ≤0.97 | 0 | 0 | 0 | 100% |
| Minimum depth / queue filter | −3.51960799 | −6.68977140 | −9.25 | 16⅔% |
| Cancel after 5 seconds unmatched | −3.51960799 | −6.68977140 | −9.25 | 16⅔% |
| Stop quoting 120 seconds before expiry | −3.51960799 | −6.68977140 | −9.25 | 16⅔% |
| Cancel dominant-inventory orders | −2.33 | −2.33 | −2.33 | 16⅔% |

**No trading candidate has positive net P&L in any of the 63 candidate runs.**
The price filter earns zero by rejecting everything. Smaller size lowers dollars
lost without repairing expectancy. Dominant-side cancellation also gives up pair
completion and creates residual DOWN losses: pessimistic totals are +$0.12 pair
profit −$0.98 UP losses −$1.47 DOWN losses = −$2.33.

The five-second policy cannot enforce a five-second holding limit. Its first
available reaction is at 31 seconds, **23 seconds after the deadline**; it only
cancels remaining orders. Inventory persists until resolution, with maximum
observed exposure duration **298 seconds** in every trading policy. No bid-price
liquidation is assumed. The liquidity and expiry variants are uninformative here:
books are uniform, entries are early, and existing stale-book rules already cancel
orders. [candidate_experiments.json](candidate_experiments.json) retains every
candidate's full input/effect trace, independent audit, skip reasons and reaction times.

## Verification and next evidence

The updated suite covers $0.98/$1 complete pairs, full-cost losses on either
unmatched side, staged cost basis, repeated merge/redemption, mark/realized
separation, reservation release, all original checkpoint balances, and missing
settlement/extra-fee/checkpoint-corruption detection. Candidate checks preserve
cancellation delay and racing fills, original events, original controls and
input files. See [tests.txt](tests.txt), [verification.json](verification.json)
and [VALIDATION.md](VALIDATION.md).

The next useful evidence is a chronological, untouched capture of verified BTC
five-minute markets: complete metadata and independent resolutions, sequenced
books, verified aggressor trades, event/receipt times, gaps/reconnects and
cancellation timing. Queue/fill assumptions need independent validation. Testing
short holding periods additionally needs executable exit evidence across the
exit window with measured delay, fees and slippage. Displayed bids alone are
insufficient. Freeze these candidates before the new capture; keep calibration
and evaluation periods separate and evaluate all candidates once on the same
holdout. No live-trading promotion is supported by this audit.

Reproduce from the independent project, using its Python environment:

```bash
cd /Users/bailey12/Desktop/PythonProject/QuantOS-PM
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m quantos_pm.pnl_audit --experiments
```

The second command verifies historical evidence read-only and regenerates only
audit outputs. It stops on a changed preserved input, nonempty SQLite WAL,
broken hash chain, accounting mismatch or historical-control mismatch.
