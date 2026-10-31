# calm_market_2020: maker-entry range mean reversion and calm-regime grid (USDT-M perps, hourly)
Run 2026-10-31. Two trials: `range_mr_maker_v1`, `grid_calm_maker_v1`. Pre-registration: `PREREG.txt` (written before any HELD-OUT number).
Reproduce: `~/Desktop/PythonProject/cci_study/.venv/bin/python run.py` (offline, ~5 s). Outputs: `results.json`, `trades_range_mr.csv`, `trades_grid.csv`.
`funding_carry_v1` was dropped by the coordinator (already rejected earlier) and was NOT re-tested; its download/outputs were deleted.
"Round 7" near-misses (premium reversion, liquidity-vacuum rebound) were requested but their definitions are not on this machine, so they were NOT tested.
FEES ARE PLACEHOLDERS pending the owner's real numbers: maker 0.00%, taker 0.04%/side, taker slippage 0.05% (BTC/ETH) / 0.10% (others), maker slippage 0. STRESS: maker 0.02%, taker 0.05%.
Splits: DEVELOP 2020-01-01..2023-06-30, HELD-OUT 2023-07-01..2024-12-31. Data: hourly OHLC only (no 2025+).

## Pass rule (pre-registered)
HELD-OUT net mean/trade > 0 with 90% week-block bootstrap CI above 0; mean >= 0 under STRESS fees; >= 100 trades; real mean above 90th placebo percentile (200 seeds);
deflated Sharpe positive with 2 trials (stricter separate line: 83 trials = 2 + 81 earlier CCI trials).

## range_mr_maker_v1 (8 coins, long+short, post-only limit at signal close, 3-bar life, maker SMA20 target, taker stop/time)
| metric | DEVELOP | HELD-OUT |
|---|---|---|
| filled trades (orders placed) | 692 (694) | 421 (424) |
| fill rate | 99.7% | 99.3% |
| win rate | 46.7% | 46.1% |
| mean / median net per trade | -27.4 bp / -87.1 bp | -23.0 bp / -61.5 bp |
| mean net under STRESS fees | -30.9 bp | -26.5 bp |
| 90% CI of mean (week blocks) | [-39.1, -16.4] bp | [-37.5, -9.7] bp |
| exits: stop / target / time | 52.5% / 47.5% / 0% | 51.8% / 48.0% / 0.2% |
| $50 notional per trade: total, per day | -$94.9, -$0.074/day | -$48.5, -$0.088/day |
| max drawdown of cumulative PnL | -$98.4 | -$59.6 |
| placebo mean (random times, random side) / real percentile | -53.8 bp / 100 | -42.9 bp / 100 |
| DSR (SR/trade -0.17; prob, 2 trials) | 0.0000 | 0.0000 (SR0 +0.025) |
| DSR 83-trial line | 0.0000 | 0.0000 |
Verdict line: `{mean_pos_ci_excl0: False, stress_mean_ge0: False, trades_ge_100: True, placebo_gt_p90: True, dsr2_pos: False} -> FAIL`.
Every symbol has a negative HELD-OUT mean; shorts -10 bp, longs -35 bp.

## grid_calm_maker_v1 (BTC, ETH; levels 0.5 ATR around SMA100, +-3 levels, stop beyond +-4)
| metric | DEVELOP | HELD-OUT |
|---|---|---|
| unit round-trips | 3278 | 1395 |
| win rate | 56.8% | 56.0% |
| mean / median net per trade | -5.0 bp / +31.3 bp | -8.1 bp / +17.7 bp |
| mean GROSS per trade | -0.4 bp | -3.7 bp |
| mean net under STRESS fees | -8.5 bp | -11.6 bp |
| 90% CI of mean | [-8.1, -2.0] bp | [-12.0, -4.4] bp |
| exits: target / stop-out / regime-off | 49.8% / 42.2% / 8.1% | 50.8% / 42.4% / 6.8% |
| unit = $16.67 ($50/3): total, per day | -$27.1, -$0.021/day | -$18.8, -$0.034/day |
| max drawdown of cumulative PnL | -$30.2 | -$19.3 |
| placebo mean / real percentile | -14.5 bp / 100 | -10.8 bp / 98.0 |
| DSR prob (SR/trade -0.053 / -0.140), 2 trials; 83 trials | 0.0002; 0.0000 | 0.0000; 0.0000 |
Verdict line: `{mean_pos_ci_excl0: False, stress_mean_ge0: False, trades_ge_100: True, placebo_gt_p90: True, dsr2_pos: False} -> FAIL`.
Whole-sample grid counts (both coins): 1563 grids, 1051 with at least one fill, 1315 ended by stop-out (some with no fills). About half of unit trades hit the small maker target (+about 20 bp), but 42% are stop-outs at taker prices that cost more than the targets earn; the median is positive and the mean negative (many small wins, few large losses).

## Adverse selection (mean signed return after fill, vs random same-side regime bars in the same period; from fill-bar close, bp; se in brackets)
| strategy / period | +1 bar | +4 bars | +12 bars |
|---|---|---|---|
| range_mr DEVELOP (n 692) | +5.2 (2.5) | -11.5 (5.4) | -14.5 (9.8) |
| range_mr HELD (n 421) | +2.3 (2.5) | -5.8 (5.4) | -33.6 (9.7) |
| grid DEVELOP (n 3278) | -2.7 (1.4) | -2.7 (2.9) | +3.7 (5.0) |
| grid HELD (n 1395) | +2.5 (1.4) | +0.5 (2.8) | -8.7 (5.0) |
Range-MR fills are followed by further adverse drift at 4-12 bars (the band-extreme signal keeps running against the position); a small bounce at +1 bar is not enough. Grid fills show little drift on average. (Observations overlap in time, so the se is understated.)

## Verdict (plain language)
Both rules are dead on this data. Neither is positive net of placeholder fees in DEVELOP or HELD-OUT; the CIs sit entirely below zero in both periods. Beating the placebo (100th/98th percentile) only means the signals are better than random entries, which are even worse; it does not make them profitable. Making entries free (maker 0%) did not rescue them: gross is already negative for both (HELD-OUT gross mean per trade: -16.8 bp range_mr, -3.7 bp grid), because the damage comes from stops and taker exits. No candidate passes, so there is no $50/day figure to quote (the $50 books above lose $0.03-0.09 per day at the stated sizing).

## What this does not show
- Fees are placeholders; only maker 0% / 0.02% and taker 0.04% / 0.05% were tried. A rebate (negative maker fee) was not modelled and would not fix a negative gross mean.
- Hourly bars only: no order book, no queue position, no partial fills. "Filled if the bar trades through 1 tick" is generous: range_mr fills 99% of the time because the limit sits at the signal close, which a real queue would not give. Real fill rates would be lower, and the missing fills would likely be the favourable ones (adverse selection above).
- Survivor universe (29 coins that still trade on Binance USDT-M; SOL/XRP etc. start 2020-2021, with 120 hourly gap bars in SOL and XRP). Tested on USDT-M prices, not USDC-M.
- Within-bar order of high/low is unknown; stops are assumed first, so intrabar bounces are not credited. Funding was not included (positions are short-lived).
- Small samples in HELD-OUT (421 and 1395 trades, 18 months, one regime sequence); trades within a week are correlated (hence week blocks).
- Grid placebo is an approximation (limit k steps from close, target 1 step, stop 4-k steps) rather than the same state machine.

## Addendum 2026-10-31: TP variants, DEVELOP only (pre-registered in PREREG.txt appendix; HELD-OUT not computed; fees maker 0% / taker 0.04% confirmed by owner)
| variant | trades | win | mean / median net bp | 90% CI bp | stress mean bp | stop share | placebo pct |
|---|---|---|---|---|---|---|---|
| range_mr_tp_band | 687 | 35.7% | -30.8 / -119.6 | [-46.0, -15.4] | -34.2 | 64.2% | 1.5 |
| range_mr_tp_1p5atr | 695 | 51.1% | -23.5 / +41.3 | [-34.8, -12.4] | -27.0 | 48.9% | 3.0 |
| grid_tp2 | 2697 | 45.1% | -2.2 / -38.3 | [-6.6, +2.1] | -5.6 | 53.9% | 100 |
| grid_tp3 | 2430 | 38.6% | +0.9 / -49.7 | [-4.3, +6.2] | -2.3 | 61.5% | 100 |
Decision rule (mean>0, CI lower bound>0, stress mean>=0): none eligible. The range_mr targets are worse than the SMA20 target and below their placebo; the grid variants are about break-even with CIs spanning zero (grid_tp3 +$3.8 total over 3.5 years at $16.67 units). Output: `results_tp_variants_develop.json`, code `variants.py`.

## Addendum 2: 144-variant sweep, DEVELOP only: 0 candidates (see `SWEEP.md`, `sweep.csv`; N=233 trials incl. 81 CCI via ledger count)
