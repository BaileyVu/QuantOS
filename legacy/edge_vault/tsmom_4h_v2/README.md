# tsmom_4h_v2: 4h time-series momentum, production config vs a 288-variant sweep

Follows EDGE_SPEC v1.0. Full tables: `~/Desktop/PythonProject/edge_data/trend_2020/REPORT.md`, `sweep.csv`, `results_heldout.json`.

## 0. Status
`dead`, 2026-10-31. Pre-registered finalists did not clear HELD-OUT (CI lower bound < 0, placebo < 90th percentile). The current production config (`tsmom_4h` 1.0.0) is also not distinguishable from zero on HELD-OUT.

## 1. Identity
| field | content |
|---|---|
| edge_id | `tsmom_4h_v2` |
| Name | 4h time-series momentum (return + EMA slope) on 29 USDT-M perps, with sweep of lookback, filter, stop, trail, side, vol-targeting and entry type |
| Owner / author | Claude research subagent for bailey |
| Origin | `QuantOS-mainnet-ready/src/quantos/paper/strategies/tsmom_4h.py` (Moskowitz, Ooi, Pedersen 2012 adaptation). Siblings: `range_mr_maker_v1`, `grid_calm_maker_v1`, `cbb_cci_breakout_v1` |
| Created / frozen | 2026-10-31 / 2026-10-31 (PREREG.txt before the first run) |

## 2. Hypothesis and mechanism
- Hypothesis: after a positive (negative) L-bar 4h return with a rising (falling) EMA, holding long (short) for up to 72h with an ATR stop earns positive net return per trade.
- Why it should exist: slow information diffusion and herding make crypto trends persist (time-series momentum). Counterparty: late stop-out/mean-reversion traders.
- Why it isn't arbitraged away: n/a - not shown to exist out of sample.
- What would kill the mechanism: regime of choppy ranges; fee increase.

## 3. Market and instruments
Binance USDT-M perps (research proxy for USDC-M): 29 symbols BTC ETH SOL BNB XRP DOGE ADA AVAX LINK DOT LTC BCH TRX ATOM NEAR UNI FIL ETC AAVE XLM ALGO SAND MANA AXS CRV RUNE THETA VET GALA. Survivorship: universe is coins that still trade in 2024 (hindsight; NOT corrected, and a large part of the BUILD profit comes from AXS, GALA, MANA, DOGE, RUNE). Side: long, short or both per variant. Contract details: n/a - tick/min notional ignored.

## 4. Signal rule
Decision clock: 4h bars, UTC-aligned, built causally from hourly; decide at bar close i. Signal: LONG if C[i]/C[i-L]-1 > 0 and EMA_E slope (EMA_i - EMA_{i-1}) > 0; SHORT if both < 0. Current config: L=180, E=50. ATR(14) Wilder (ewm approximation).
Entry: market at next bar open, or maker limit at signal close -/+ 0.25 ATR valid 2 bars (fill if a later bar trades through by 1e-5). Filters: side mode (long-only; longs + shorts only when BTC close < EMA200(4h); both).
Exit: stop = signal close -/+ k ATR (k 2 or 3) checked on 4h high/low, stop first, gap = worse open; trailing (current: activate at 1R, stop = best - 2R, tighten only; R = |fill - stop|) or none; time exit 18 bars (72h) at next open.
Sizing: unit notional (weight 1) or vol-targeted weight min(3, 0.01/ATR%); max 5 concurrent, one per symbol, ranked by |return|. Reference sizing for PnL: $10 notional per slot on $50. 5% risk sizing: n/a - no validated variant.
Code pointer: `edge_data/trend_2020/sweep.py` (`run_trade`, `portfolio`) wins over this text.

## 5. Data
Hourly OHLC from `edge_data/crash_episodes_2020/data/prices.npz` (2020-01-01 to 2024-12-31 UTC), resampled to 4h. BUILD 2020-01..2023-06 (selection); HELD-OUT 2023-07..2024-12 touched once, only for 2 finalists and the current config after the finalists were fixed. Gaps: SOL/XRP 120 hourly bars (affected 4h bars NaN, trades touching them dropped). Leakage checks: signals use closed 4h bars; fills at next open or later; no 2025+ data.

## 6. Cost and fill model
- Fees maker 0.00%, taker 0.04% (owner-confirmed); STRESS maker 0.02%, taker 0.05%. Slippage 0 on maker fills, taker fills 0.05% (BTC/ETH) / 0.10% (others).
- Market entry and all stops/time exits taker. Limit fills need trade-through by 1 tick; no queue model (optimistic). Gap stops fill at the worse open.
- Fill rate for limit entries: not reported separately (n/a - not computed); funding: n/a - not modelled.

## 7. Pre-registered pass criteria
Finalist rule (PREREG.txt, 2026-10-31): DSR>0 at N, BUILD week-block CI lower bound>0, stress mean>=0, >=200 trades, placebo pct>90, all one-step neighbours positive, at most 2 by DSR; then HELD-OUT once. Forward-test spec only if a finalist beats current on HELD-OUT with CI>0. Multiple testing: N = 152 + 288 + 81 = 521 trials. Verdict: finalists selected (BUILD) but HELD-OUT FAIL; no forward-test spec.

## 8. Results (HELD-OUT first; BUILD in brackets)
- Current: 1171 trades [2525], mean +17.8 bp [+55.2], median -190 bp, CI [-43.4, +90.5] [[+0.5, +112.5]], stress +15.8, win 39.8%, stop share 50%, placebo pct 100, DSR no, max DD -$40 on $10/slot.
- Finalist 1 `ts_L90_E50_st3_tr1_long_vol0_lmt`: 795 trades [1663], +60.6 bp [+172.4], CI [-17.8, +144.1] [[+89.7, +255]], placebo 86.0, stress +57.6, DD -$47.7, +$0.088/day.
- Finalist 2 `ts_L90_E100_st3_tr0_long_vol0_lmt`: 804 trades [1669], +67.1 bp [+166.6], CI [-18.4, +159.8], placebo 86.5, DSR prob 0.53 (> 0.5 on the SR>SR0 test but CI/placebo fail), DD -$49.5, +$0.098/day.
- Distribution: median trade negative (-94 to -190 bp), edge is a few big winners; top 10 trades = 61% of BUILD PnL for the current config. Per year (BUILD, current): +58, +114, +26, -9 bp (2020, 2021, 2022, 2023H1); finalist 1: +165, +406, -67, +79 bp. Per-symbol: concentrated in AXS, GALA, MANA, DOGE, RUNE.
- Neighbourhood: in BUILD all 288 variants had positive means (median +32.7 bp), i.e. a long-bias bull-market effect rather than a sharp peak.

## 9. Capacity and liquidity
n/a - not validated. 

## 10. Correlation with other edges
29 alts are highly correlated with BTC; long-only variants are essentially leveraged crypto beta. Overlap with other edges: n/a - no live edge.

## 11. Search history / multiple testing
288 variants in one pre-registered grid (+152 earlier ledger lines + 81 CCI = N 521). Winners picked by DSR on BUILD only; HELD-OUT used once for 2 finalists + current. Best-of-search BUILD: +172 bp per trade. Adjusted significance in BUILD: DSR 0.99 for finalists (HELD-OUT then contradicts it).

## 12. Risks and failure modes
Backtest limits: survivor universe, bull-market dependence, 4h-bar stop checks, no queue/funding, heavy tails (single trades +130-170%). Regime risk: 2022 bear (finalist 1 -67 bp). Tail: gap through stop, delisting not modelled. Operational: none (not live).

## 13. Kill criteria
Fired at the HELD-OUT stage. If revived: stop if trailing 50-trade mean < 0 or drawdown > 25% of allocated capital.

## 14. Reproduction
`cd ~/Desktop/PythonProject/edge_data/trend_2020 && ~/Desktop/PythonProject/cci_study/.venv/bin/python sweep.py` (BUILD only, appends 288 lines to the ledger - do not rerun without need) then `held.py` (one-shot HELD-OUT, appends 3 lines). Dependencies: numpy, pandas, scipy. Offline, ~10 s.

## 15. Paper / live tracking
n/a - dead; the production `tsmom_4h` paper strategy is unchanged by this study.

## 16. Change log
- 2026-10-31: PREREG.txt; BUILD sweep (288 variants, N=521); 2 finalists chosen by DSR; HELD-OUT run once for finalists and current config; all FAIL the CI>0 / placebo>90 test; status `dead`.
