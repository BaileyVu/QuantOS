# SWEEP: 144-variant full-factorial twist, DEVELOP only (2020-01-01..2023-06-30)
Pre-registered in `PREREG.txt` (appendices 2 and 3) before running. HELD-OUT bars were never loaded. Code `sweep.py`, per-variant table `sweep.csv`, every variant also appended to `~/Desktop/PythonProject/edge_vault/search_ledger.jsonl` (152 lines from this run).
Fees: maker 0%, taker 0.04% (+ taker slippage 0.05% BTC/ETH, 0.10% others), stress maker 0.02% / taker 0.05%. Fill model as in REPORT.md (post-only limits, trade-through by 1 tick, no queue).

## Grid searched
- range_mr (108): band k {2.0, 2.5, 3.0} x RSI {25/75, 30/70} x target {SMA20, opposite band, 1.5 ATR} x stop {2 ATR, 3 ATR, none + 48-bar time exit} x regime filter {on, off}. 8 coins, long and short.
- grid (36): spacing {0.5, 1.0} ATR x levels {3, 5} x close-at {1, 2, 3} levels x stop-out {+-4, +-6 levels, none + regime-off flatten}. BTC, ETH.
- Reproduction check: `mr_k2.0_rsi30_sma_stop2_regon` gives 690 trades, -27.3 bp, identical to range_mr_maker_v1 DEVELOP.

## Selection rule and trial count
Candidate only if DSR > 0, CI lower bound > 0 (base fees), stress mean >= 0, >= 200 trades, all one-step neighbours have mean net > 0. Rank by DSR, at most 3.
DSR: per-trade Sharpe, N = ledger lines (152 mine + 0 others at run time) + 81 CCI = 233 trials, variance of Sharpe across the 144 variants = 0.0051, giving SR0 = +0.20 per trade.

## Result: NO CANDIDATES (0 of 144)
| count | value |
|---|---|
| variants with mean net > 0 (base fees) | 3 of 144 (all grid) |
| variants with CI lower bound > 0 | 0 |
| variants with CI entirely < 0 | 118 |
| variants with DSR > 0 | 0 |
| candidates | 0 |
- range_mr: all 108 negative. Median -35.8 bp per trade, best `mr_k3.0_rsi25_atr_stop2_regoff` -7.6 bp (1027 trades, CI [-33.9, +19.3], stress -11.1 bp). The stop share is 45-75% when a stop exists and the regime filter did not help (regime-off variants are not systematically worse or better in a way that rescues them).
- grid: median -12.5 bp. The three positive means are tiny: `grid_sp0.5_L5_m3_stop4` +2.6 bp (3710 trades, CI lower -1.1, stress -0.6, stop share 75%), `grid_sp0.5_L3_m3_stop4` +0.9 bp (CI lower -4.3), `grid_sp0.5_L5_m2_stop4` +0.3 bp (CI lower -2.7). All have CI spanning zero, negative stress means, and ~$10 maximum drawdown on $16.67 units for under $4 total profit in 3.5 years.
- With 144 variants tried, finding 3 small positive means among the least-bad grid settings is what chance alone would produce; none is distinguishable from zero.

## Forward-test spec
None. No variant met the pre-registered rule, so there is nothing to code as a paper profile. All edge docs stay `dead`.

## What this does not show
Same limits as REPORT.md: hourly bars, no order book or queue, generous through-by-1-tick maker fills, survivor universe, USDT-M prices not USDC-M, no funding, one 3.5-year DEVELOP window. The sweep is within the specified family only; other targets, timeframes (e.g. 15m), or symbols were not tried. "L=5 with stop +-4" is kept as specified (levels 4 and 5 are never usable).
