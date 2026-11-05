# Portfolio research results — 5 November 2026

**This study does not support a trading configuration.** The portfolio selected
using 2022–2024 data could not be evaluated on the sealed 2025–2026 window because
of an unresolved AXS funding-cadence discrepancy. All 48 completed primary
sweep cells that actually traded lost money in that window. The highest completed
sweep return was 0%, tied among twelve zero-trade EMA cells. That is an
exploratory cash outcome, not evidence of a trading edge. The frozen pick was
not replaced, and no trading process was launched.

The [decision record](research/PORTFOLIO_RECOMMENDATION_20261105.json) explicitly
disables execution; it is not a runtime configuration. The
[complete metrics](research/PORTFOLIO_RESULTS_20261105.json) contain every one of
159 training cells and 176 held-out jobs, including rejection counts, funding,
fees, pause times and unavailable results. Undefined metrics remain null.

## What was fixed before evaluation

The initial simulated account was $200. Training covered 1 January 2022 through
31 December 2024, with Q4 2021 warmup. The held-out window covered 1 January 2025
through 28 October 2026 inclusive, with Q4 2024 warmup. Previous research already
examined 2025–2026 for some candidates, so this window is **not fully untouched**.

The [selection protocol](PORTFOLIO_SELECTION_PROTOCOL.md) first evaluated 147
individual settings, then twelve combinations of training-selected settings.
This was a staged search, not every possible portfolio cross-product. Eligibility
required positive training growth, both hourly MTM and adverse-bar stress
drawdowns below 50%, and configured risk no greater than half the empirical
training Kelly estimate. No-loss/undefined Kelly estimates were ineligible.

The three candidates were:

| Candidate | Rules and fixed comparison settings |
|---|---|
| Crash flush | Nine named majors; closed-hour OI flush plus price decline and distance below the prior high; 24/48/72h holds; 1/2/4/6/8% risk |
| ALT dip | Original 29-coin survivor universe; 0.5%/1% pooled 24h-return quantile calibrated in the 90 days before each quarter, excluding its last three days; 12/24/72h holds; 1/2/4/6/8% risk |
| EMA level | Timestamp-aligned 2h bars; EMA 12/26 and causal source-derived zones; 0.25 ATR touch/stop buffers, 1.5R target; 0.5/1/2/3% risk |

Crash and ALT used the approved 25% catastrophe stop with time exits primary.
The 15% stop was sensitivity-only. All candidates swept leverage ceilings 1/2/3;
the retained conservative liquidation buffer ordinarily limited wide-stop
positions to 1x. A ceiling is not promised used leverage. The isolated research
account retained three-position, exposure, margin, cost, cooldown and permanent
10% daily-loss/50% drawdown breakers. No risk rule was relaxed after outcomes.

## Training result and frozen choice

| Setting | Final equity | CAGR | MTM DD | Trades | Net PF |
|---|---:|---:|---:|---:|---:|
| ALT, q0.005, 24h, 4% risk, 1x | $369.97 | 22.75% | 15.90% | 209 | 1.94 |
| Crash, 48h, 4% risk, 1x | $280.43 | 11.92% | 11.54% | 203 | 1.40 |
| **Frozen ALT + crash portfolio** | **$373.17** | **23.10%** | **15.83%** | **336** | **1.53** |
| EMA, 0.5% risk, 1x diagnostic | $200.00 | 0% | 0% | 0 | undefined |

The portfolio's net growth was 86.59%; adverse-bar stress DD was 16.17%. Worst
three-day and thirty-day returns were −14.23% and −10.17%; 29.98% of overlapping
three-day windows were positive. Frequency was 2.146 trades/week. Its five best
MTM days contributed $78.79, 45.50% of net profit. Six catastrophe stops (1.79%
of closed trades) contributed −$61.60 net PnL, including $0.1892 fees. This is
stop-trade PnL, not a causal estimate of the incremental cost of stops.

The empirical Kelly estimate was 44.13%, half-Kelly 22.06%; 4% configured risk
passed the fixed constraint. These sample estimates do not establish a reliable
population Kelly value. The EMA candidate generated only four training signals:
three failed net reward/risk, one the exchange minimum. Its zero-trade result
was ineligible for selection.

Seventeen training diagnostic jobs (four reused, thirteen newly simulated) were
completed before the held-out wave. The frozen portfolio finished at $349.31
with doubled slippage, $377.17 with one-hour delayed entry, and $185.74 with a
15% stop. The last variant hit a permanent breaker in May 2022. Narrowing the
stop also increases position size under percent-risk sizing; this is not a
stop-only causal comparison. At the fixed crash settings, the price-only
baseline finished at $197.33 and symbol/month-matched random entries at $244.24.
The [training report](PORTFOLIO_TRAINING_20261029.md) preserves more detail.

## Held-out coverage and the funding failure

The single sealed wave finished **67 COMPLETE, 105 INVALID_DATA and four
NOT_EVALUABLE** jobs. Of the 159 primary grid cells, 60 completed and 99 failed
funding validation. Twelve completed EMA cells had no trades; all 48 traded
primary cells lost money. The remaining jobs were fixed diagnostics and four
volume-universe comparisons.

Price and mark coverage for the primary universe was complete after exact
source repairs: 527,568 trade bars, no remaining price/mark gaps or duplicates.
The overlay supplied 2,112 rows: 59 daily datasets derived losslessly from
public API responses (1,416 rows), plus 29 actual Binance Vision daily mark
archives (696 rows). Raw API responses, requests and hashes were preserved;
API-derived ZIPs are not represented as downloaded Vision archives. No prices
were interpolated.

For AXS on 24 June 2026, recorded funding events jump from 00:00:00.005 to
08:00:00.005 UTC while adjacent archive interval metadata reports four hours.
The independent public funding-history API also returns no 04:00 event. There
is insufficient evidence to invent a payment, assume a zero rate, or silently
change the recorded interval.

The sealed simulator validates the full evaluation funding sequence for every
symbol present in a cell's signals **before executing any trades**. Thus its
rejection is broader than a missing event during an occupied position. All
ALT-containing cells, including the frozen portfolio, fail this conservative
preflight. Their returns, drawdowns, trade metrics and Monte Carlo results are
unavailable, not zero. This clarifies the validation scope without changing the
sealed model or removing AXS after seeing outcomes.

## Valid held-out diagnostics

The individually frozen crash setting (48h, 4% risk, 1x) remains evaluable because
its nine-major universe excludes AXS. It is a diagnostic, not a substitute for
the invalid portfolio.

| Fixed crash setting / diagnostic | Final equity | CAGR | MTM DD | Trades | Net PF |
|---|---:|---:|---:|---:|---:|
| Base | $157.55 | −12.27% | 21.23% | 55 | 0.40 |
| Doubled slippage | $154.27 | −13.27% | 22.87% | 54 | 0.36 |
| Entry delayed one hour | $162.65 | −10.72% | 19.55% | 55 | 0.50 |
| 15% catastrophe stop | $129.43 | −21.23% | 35.28% | 57 | 0.46 |
| Price-only crash baseline | $132.89 | −20.08% | 35.13% | 110 | 0.49 |
| Symbol/month-matched random | $152.89 | −13.70% | 25.49% | 82 | 0.48 |

These six paths paused permanently at **10 October 2025 21:59:59.999 UTC**.
CAGR and calendar-window metrics include the subsequent cash period; these are
not continuously trading samples through October 2026. Gap losses can exceed a
breaker threshold. Random matching uses generated symbol/month event counts,
not executed trade counts; risk gates and cooldowns change actual fills. One
fixed random seed (28102026) does not establish statistical significance for OI.

The base crash path lost 21.23% net. Worst three-day/thirty-day returns were
−19.61%/−20.09%; 10.24% of overlapping three-day windows were positive, with
0.578 trades/week. The five best days contributed $16.39; their share of net
profit is undefined because total PnL is negative. Gross PnL −$41.0666, fees
$1.4200 and net funding credit $0.0329 reconcile to −$42.4538 net. Adverse-bar
stress DD was 21.23%. Rejections included 115 cooldown, 96 below minimum,
33 existing exposure, 27 portfolio risk cap, 13 existing symbol exposure and
331 paused/unreconciled signals; the last count reflects the permanent pause.

| Crash diagnostic | Catastrophe stops | Frequency | Net PnL of stop trades |
|---|---:|---:|---:|
| Base | 2 | 3.64% | −$38.44 |
| Doubled slippage | 2 | 3.70% | −$37.18 |
| One-hour delay | 3 | 5.45% | −$45.84 |
| 15% stop | 7 | 12.28% | −$95.46 |
| Price-only | 4 | 3.64% | −$49.62 |
| Matched random | 3 | 3.66% | −$46.88 |

The **exploratory** highest completed primary cell was EMA at 1% risk and a 3x
ceiling: $200, zero trades, zero CAGR, tied with eleven other EMA settings. Both
held-out EMA signals failed net reward/risk. The canonical-ID tie-break chose
this row; it is not an evidence-backed strategy recommendation. Among cells with
trades, the best exploratory result was crash 72h/1%/1x: $193.22, −1.87% CAGR,
5.70% MTM DD, 79 trades, PF 0.75. Its four stops cost −$11.61 net. It also does
not replace the frozen pick.

## Robustness, existing library and source audit

The fixed December-2024 volume universe has 29 members, including UXLINK. Its
snapshot status is SETTLING, so the presealed current-filter policy makes all
four full-cohort comparisons NOT_EVALUABLE. UXLINK was neither removed nor
replaced, and its historical existence is not denied. No successful volume
robustness conclusion can be drawn. Choosing this cohort in December 2024 also
uses information unavailable in 2022; it would be a separate robustness row,
not a contemporaneously selected training universe.

| Existing V4 strategy | Full-window status | Missing evidence / incompatibility |
|---|---|---|
| ema_atr_ref | NOT_EVALUABLE | Original USDC history and 5m LIMIT/90-second fill evidence |
| donchian_atr_ref | NOT_EVALUABLE | Original USDC history and 5m STOP/90-second fill evidence |
| tsmom_4h | NOT_EVALUABLE | Original USDC cohort has no supported full 2022+ history |
| turtle_55 | NOT_EVALUABLE | Original USDC history and STOP/90-second fill evidence |
| vol_squeeze_breakout | NOT_EVALUABLE | Original USDC history and STOP/90-second fill evidence |
| delist_short_e17 | NOT_EVALUABLE | Historical announcement/contract availability and event timing |

These six statuses are separate from the 176-job candidate wave. There was no
USDT substitution, fabricated early USDC history or hourly-extreme assumption
that a production LIMIT/STOP order filled within its TTL.

The short [archived EMA audit](PORTFOLIO_EMA_ARCHIVED_AUDIT.md) used source 9/20,
3% stop/cross exit and timestamp-aligned 2h bars. Its fee-only source scorer
returned $290.43 from $200 with 32.56% realized DD and 1,465 level signals, not
1,465 filled trades. It lacks this study's slippage, funding and MTM accounting
and is not the requested 12/26 candidate. It does not reproduce or validate the
archived corrected 12/26 walk-forward growth claims of 1.12–1.27. The source
mismatch was recorded and not optimized away.

## Conditional Monte Carlo

Each reported resample uses 1,000 draws, seed 28102026. Training-pick 50%-DD
frequencies were 0/1,000 for trade reshuffling and 1/1,000 for seven-day circular
daily-return blocks. The frozen portfolio's held-out result is **unavailable**.
The zero-trade exploratory EMA row has no trade-reshuffle estimate; its constant
cash path gives 0/1,000 day-block events, which says nothing about a trading edge.

For the valid fixed crash diagnostics, trade reshuffling produced 0/1,000 in each
case. Seven-day block frequencies were 55/1,000 base, 61/1,000 doubled slippage,
34/1,000 delayed entry, 287/1,000 with the 15% stop, 175/1,000 price-only and
62/1,000 matched random. [Machine-readable MC](research/PORTFOLIO_MC_20261105.json)
records the settings. These are conditional resamplings of observed paths, not
future ruin probabilities. Trade reshuffling ignores simultaneous positions;
day blocks cannot create unseen regimes and can repeat loss episodes without
reapplying the original strategy's breaker logic.

## Provenance, verification and limits

The held-out seal SHA256 is
`4368181fdc4c8e8dc03697174d24e11b59b5db69a69a3dfbeea921a341d41084`.
Its runtime was commit `9d14f81`, source-content hash
`e4f94baea5fedce905946a5e14066f0d5ab7c29aa4380ac4bd950aa317d540f5`.
The repaired held-out data hash is
`96955dd82a67cc723288028343b978b666604fd36c31b662be7be1589b259afd`;
signal payload hash
`0ca75bae370226690a2a4472e70f80dfde6f58d40c2fbc5106f96aa3a20a2e5c`.
The wave claim hash is
`9157638826c045f249f6cc69dedc468597a9a04ea9cbde968766fd1118dcf177`.
Training was generated under `834e30b`, as recorded in the training report.
Later unrelated main-branch changes were not used to recompute these outcomes.

Ledger checks reconciled 22,320 primary training trades, 2,296 diagnostic trades
and 3,698 trades across the 67 completed held-out jobs, with zero discrepancies.
These are repeated observations across settings, not independent market trades.
Saved results were audited without rerunning outcome cells. Runtime tests passed
246 with four conditional skips; the retained legacy suite passed 1,001 tests.
A clean Python 3.12 wheel install, synthetic demo and public-data doctor passed.
These validate software behavior, not profitability or live fill equivalence.

The primary list was chosen in hindsight from surviving coins. Original source
research used Spot; this experiment uses USD-M. OI archive labels use an explicit
one-hour availability lag. Current exchange filters are a historical proxy,
not point-in-time rules. Synthetic hourly fills charge 0.15% adverse slippage
and 0.05% fees per side; doubled slippage uses 0.30% per side. Funding uses actual
recorded payments with causal hourly mark-open proxies. Conservative stop and
liquidation bars can exit at adverse extremes. Hourly bars cannot recover real
spread, tick ordering, impact or intra-hour breaker timing. These assumptions
and the risk caps are fixed in the [execution protocol](PORTFOLIO_EXECUTION_PROTOCOL.md).

The final research decision is to withhold a trading allocation. Resolving the
funding metadata or changing the model would require a separately declared new
study; it cannot retroactively turn this frozen portfolio into a validated pick.
