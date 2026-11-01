# trend_2020: tsmom_4h sweep (288 variants, BUILD 2020-01..2023-06; 2 finalists + current config once on HELD-OUT 2023-07..2024-12)
Pre-registration `PREREG.txt` (before any run). Code `sweep.py` (BUILD only), `held.py` (one-shot HELD-OUT), table `sweep.csv`, `results_heldout.json`, `build_summary.json`. Offline, ~10 s.
Universe: 29 survivor USDT-M perps, hourly -> 4h causal bars; max 5 concurrent, one per symbol, ranked by |return|. Fees maker 0% / taker 0.04% (+slippage); stress 0.02% / 0.05%. All 288 variants logged in `edge_vault/search_ledger.jsonl`.
Deflated Sharpe: N = 152 (ledger before) + 288 + 81 CCI = 521 trials; SR0 = +0.063 per trade (SR variance across variants 0.00042).

## BUILD (selection) results
- 288 of 288 variants have a positive mean net; 228 have a week-block CI lower bound > 0; 166 have DSR > 0; 157 met every finalist rule (including neighbours positive). Median variant +32.7 bp per trade.
- Current production config (L180, EMA50, stop 2 ATR, trail, both sides, market): 2525 trades, win 43.6%, mean +55.2 bp, CI [+0.5, +112.5], stress +53.2, placebo pct 100, DSR prob 0.16 (not > 0).
- Finalists by DSR (rule: at most 2): `ts_L90_E50_st3_tr1_long_vol0_lmt` (1663 trades, +172.4 bp, CI [+89.7, +255], DSR 0.99) and `ts_L90_E100_st3_tr0_long_vol0_lmt` (1669 trades, +166.6 bp, CI lower +83.7, DSR 0.99). Both: long-only, maker-limit entry, 3 ATR stop, lookback 90.
- Warning signs visible in BUILD: median trade is negative (-162 bp current); top 10 trades = 61% of the current config's total PnL (single best trade +131%); by year the current config is +58, +114, +26, -9 bp (2020, 2021, 2022, 2023H1) and the top finalist is +165, +406, -67, +79 bp. Best symbols are AXS, GALA, MANA, DOGE, RUNE (coins that exploded and survived: the universe was chosen with hindsight).

## HELD-OUT (each run once; fees maker 0 / taker 0.04%)
| | trades | win | mean / median bp | 90% CI bp | stress mean | stop share | placebo pct | DSR>0 | max DD ($10/slot) | $/day |
|---|---|---|---|---|---|---|---|---|---|---|
| CURRENT (L180 E50 st2 trail both mkt) | 1171 | 39.8% | +17.8 / -190.4 | [-43.4, +90.5] | +15.8 | 50.3% | 100 | no | -$40.4 | +$0.038 |
| finalist 1 (L90 E50 st3 trail long lmt) | 795 | 44.7% | +60.6 / -98.9 | [-17.8, +144.1] | +57.6 | 33.0% | 86.0 | no | -$47.7 | +$0.088 |
| finalist 2 (L90 E100 st3 notrail long lmt) | 804 | 45.0% | +67.1 / -94.0 | [-18.4, +159.8] | +64.1 | 29.6% | 86.5 | yes (prob 0.53) | -$49.5 | +$0.098 |
$/day = total PnL per day at $10 notional per trade (5 slots = $50 capital), over 549 days.

## Verdict
No finalist is validated: HELD-OUT CI lower bounds are below zero for both finalists and the current config, and both finalists are below the 90th placebo percentile (86%, 86.5%). They did not beat the current config with a CI above 0, so there is no forward-test spec. Means are positive on HELD-OUT but statistically indistinguishable from zero, driven by a few big winners, with max drawdown near the whole $50 capital. Status of `tsmom_4h_v2`: `dead` under the pre-registered rule (not "refuted": the long-bias results are positive, just not significant).

## What this does not show
Survivor universe (the 29 coins that still trade; a lot of BUILD profit is long exposure to coins that went up 10-100x), large dependence on 2020-2021 bull market, 4h bars only (stops checked on 4h highs/lows, stop first), maker-limit fills assumed on trade-through with no queue (fill rate for limit variants not shown), no funding, USDT-M not USDC-M, 5-slot cap uses equal slots rather than the 5% risk sizing, pnl is per unit notional. The sweep is a single family; HELD-OUT is a single 18-month window and a handful of heavy-tail trades.
