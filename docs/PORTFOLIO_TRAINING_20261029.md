# Portfolio training results — preliminary, not a released recommendation

The preregistered 2022–2024 batch completed 159 cells (147 individuals and
12 derived combinations), with zero invalid-data cells. No 2025+ outcomes have
been acquired or evaluated. The selection below is fixed by the published
training algorithm; diagnostic comparisons cannot change it.

| Saved setting | Final equity from $200 | CAGR | Hourly MTM DD | Adverse-bar stress DD | Trades | Net PF |
|---|---:|---:|---:|---:|---:|---:|
| ALT q0.005, 24h, 4% risk, 1× ceiling | $369.97 | 22.75% | 15.90% | 16.24% | 209 | 1.94 |
| Crash flush 48h, 4% risk, 1× ceiling | $280.43 | 11.92% | 11.54% | 13.18% | 203 | 1.40 |
| EMA 12/26, 0.5% risk, 1× diagnostic setting | $200.00 | 0.00% | 0.00% | 0.00% | 0 | undefined |
| **Training pick: ALT + crash settings above** | **$373.17** | **23.10%** | **15.83%** | **16.17%** | **336** | **1.53** |

Both selected wide-stop strategies use the approved 25% catastrophe stop and
primary time exit. Leverage is a ceiling, with the original conservative
liquidation checks retained. Four percent is initial stop-risk budgeting, not
40% account leverage or a promise of a bounded realized loss on gaps.

The EMA row is an ineligible diagnostic setting: four generated signals were
rejected, three by net reward/risk and one by the exchange minimum. Zero trades
is an actual risk-gated result here, unlike the six existing V4 strategies whose
full-window historical inputs remain unsupported. No filters were relaxed to
manufacture trades. The short archived EMA source audit is separate in
`PORTFOLIO_EMA_ARCHIVED_AUDIT.md`; its fee-only source scorer is not this candidate.

For the training pick, net growth was 86.59%; the worst three-day return was
−14.23%, worst thirty-day return −10.17%, and 29.98% of overlapping three-day
windows were positive. Frequency was 2.146 trades/week. The five best MTM days
contributed $78.79, or 45.50% of total net profit. Six catastrophe exits occurred
(1.79% of closed trades), with aggregate net PnL −$61.60 and $0.1892 fees on those
trades. This is realized stop-trade PnL, not a causal estimate of the incremental
cost of adding stops. The fixed 15% sensitivity is reported separately.

The pick's empirical training Kelly estimate was 44.13% of stop risk, with
22.06% half-Kelly; configured 4% per member passed this preregistered cap. This
sample-dependent estimate ignores parameter uncertainty and is not a reliable
population Kelly estimate or an invitation to increase risk.

Main-agent ledger reconciliation checked all 159 saved cells, spanning 22,320
trades across parameter settings. Every trade reconciled gross PnL minus fees
plus funding to net PnL; every recorded funding event was assigned to its trade;
initial risk and leverage remained within configured limits; final account
equity equalled $200 plus closed net PnL and matched the saved equity path.
There were no discrepancies. No outcome cells were rerun for this audit.

For the selected training path, 1,000 resamples with seed 28102026 produced
50%-drawdown frequencies of 0/1,000 under trade-return reshuffling and 1/1,000
under a circular seven-day daily-return block bootstrap. These are conditional
historical stress results, not future ruin probabilities. Trade reshuffling
does not reconstruct simultaneous portfolio positions or intraday liquidation.

## Evidence and remaining work

Ignored local artifacts: `data/portfolio/training/primary_20261029_v1`,
`data/portfolio/audits/primary_training_ledger_20261029.json`, and
`data/portfolio/audits/primary_training_mc_20261029.json`. The primary generation
source was commit `834e30b`, with source-content hash
`07316395a5824f404de5febc0412090beab800b30e1908b77170ef7bda3cffbb`.
The training pick key is
`ff17ca540d737dc6df6189e612e34e723df070038b848a3f93f905ef004d504b`.

Training baseline/sensitivity execution, final evaluation-adapter review,
selection seal, one held-out wave, final report/configuration and a PR remain.
The held-out endpoint is preregistered as 2026-10-29 00:00 UTC exclusive. All
159 primary cells are exploratory in that window; the training pick remains the
candidate recommendation regardless of which held-out cell wins.

The primary 29-coin universe was selected with hindsight from survivors. The
separate December-2024 volume cohort is fixed at 29 and will not drop an
inconvenient member. Its current public filter snapshot includes UXLINK with
status SETTLING. Historical availability and filter limitations must be resolved
or reported as not evaluable before presenting that robustness row.

Prior research already inspected 2025–2026 for some candidates, so that window
is not fully untouched. Supplied ALT/EMA sources used Spot, whereas this study
uses USD-M futures. Current exchange rules are an explicit historical proxy,
not point-in-time rules. Hourly synthetic market fills, conservative stop/mark
stress, lagged archive OI availability and funding mark-open proxies are scenario
assumptions documented in `PORTFOLIO_EXECUTION_PROTOCOL.md`. No actual trading
performance, profitability guarantee or paper launch is implied by these results.
