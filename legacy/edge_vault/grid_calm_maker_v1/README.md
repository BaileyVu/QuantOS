# grid_calm_maker_v1: Calm-regime symmetric maker grid around SMA(100) on BTC and ETH

Follows EDGE_SPEC v1.0. Full tables: `~/Desktop/PythonProject/edge_data/calm_market_2020/REPORT.md`; raw: `results.json`, trades CSV.

## 0. Status
`dead`, 2026-10-31. The pre-registered HELD-OUT pass rule FAILED (CI below zero, negative under stress fees, DSR not positive).

## 1. Identity
| field | content |
|---|---|
| edge_id | `grid_calm_maker_v1` |
| Name | Calm-regime symmetric maker grid around SMA(100) on BTC and ETH |
| Status | dead (2026-10-31) |
| Owner / author | Claude research subagent for bailey |
| Origin | Calm-market revisit motivated by a USDC-perp maker-fee promotion; sibling ``range_mr_maker_v1`` and the 81 CCI trials (`cbb_cci_breakout_v1`) |
| Created / frozen | 2026-10-31 / 2026-10-31 (PREREG.txt written before HELD-OUT was computed) |

## 2. Hypothesis and mechanism
- Hypothesis: In a calm regime a symmetric grid of post-only limit orders at 0.5 x ATR spacing around SMA(100) collects the spread between levels and gives positive net return per round trip.
- Why it should exist: Range-bound price oscillating between levels pays a maker who is passively long/short at each; the regime filter tries to switch the grid off in trends. The test shows stop-outs at taker prices outweigh the small level-to-level gains.
- Why it isn't arbitraged away: n/a - no edge found.
- What would kill the mechanism: n/a - dead.

## 3. Market and instruments
- Venue: Binance USDT-M perps (research proxy for the owner's USDC-M perps). Universe: BTC ETH. Fixed in advance, but these are coins that survive to 2024 (survivorship bias, not corrected).
- Side: both (buy below, sell above the center). Contract details: n/a - research on hourly OHLC; tick assumed 1e-5 of price; min notional ignored.

## 4. Signal rule
Decision clock: hourly bars, UTC, decide at close of bar t, orders live from bar t+1. ATR(14) = 14-bar simple mean of true range; regime = ATR(14)/close below its own rolling 720-bar (30-day) median AND |close/SMA(100) - 1| < 3%.
Grid starts at the close of bar t when the regime turns on (and price is within +-4 levels of the center): center = SMA(100)[t], step = 0.5 x ATR(14)[t], fixed for the grid's life. Levels +-1..+-3: buy 1 unit at each level below the center, sell 1 unit at each level above (post-only; an order is armed only if on the passive side of the last close; re-armed the bar after a close). A level fills if the bar trades through it by 1 tick; at most one fill per level per bar; a level cannot close on the bar it filled.
Exit: each fill closes at the next level toward the center (maker limit, same through-by-1-tick rule). Stop-out when the bar touches +-4 levels: flatten everything taker at the +-4 level or the worse open; entries that fill on that same bar are kept (conservative); closes on that bar are ignored. Regime off at close of t: flatten everything at the next open (taker).
Sizing: unit notional $50/3 = $16.67 (at most 3 units one side = $50, 1x of a $50 account).
Concurrency / session stops: One unit per level per side; a new grid starts only on a bar where the regime is on and the grid is not active; after a stop-out wait until the regime has been off once.
Code pointer: `~/Desktop/PythonProject/edge_data/calm_market_2020/run.py` (code wins over this text).

## 5. Data
- Sources: `edge_data/crash_episodes_2020/data/prices.npz` (hourly OHLC of 29 Binance USDT-M perps, built earlier from public klines). Resolution 1h. Range 2020-01-01 to 2024-12-31 UTC. No 2025+ data touched.
- Split: DEVELOP 2020-01-01..2023-06-30, HELD-OUT 2023-07-01..2024-12-31, fixed in advance. A DEVELOP-only sanity run was looked at before the final run (see change log); HELD-OUT was computed once, in the final run.
- Frozen copy: the prices.npz above (read only). Known gaps: SOL and XRP have 120 hourly gap bars each (trades hitting a gap are dropped); coins start at different dates (SOL 2020-09).
- Leakage checks: indicators use bars <= t only; signal at close of t, orders live from t+1; targets use SMA20 of the previous close; fills never on the bar they were placed.

## 6. Cost and fill model
- Fees (PLACEHOLDER pending owner's real numbers): maker 0.00%, taker 0.04% per side. STRESS: maker 0.02%, taker 0.05%.
- Slippage: 0 on maker fills; taker fills 0.05% (BTC/ETH) or 0.10% (others), per side.
- Fill model: a post-only limit fills only if a later bar trades THROUGH by 1 tick (1e-5 of price; buy: low < p*(1-1e-5), sell: high > p*(1+1e-5)); fill at the limit price. No queue model, no partial fills (optimistic).
- Fill ladder / fill rate: reported below; no real-print or 1-min check was done (n/a - status is `dead` before that stage).
- Stops, time exits and regime-off flatten are taker; stop fills at the stop level, or at the worse open on a gap. Stops are checked on hourly high/low, stop first when ambiguous.
- Funding / borrow: n/a - not modelled (short holds); a stated limitation.

## 7. Pre-registered pass criteria (fixed in `PREREG.txt` before the HELD-OUT run)
- Rule: HELD-OUT net mean per trade (placeholder fees) > 0 with 90% week-block bootstrap CI lower bound > 0; mean >= 0 under STRESS fees; >= 100 trades; real mean above 90th percentile of 200-seed placebo; deflated Sharpe positive (SR > SR0, null SR variance 1/n) with 2 trials; stricter separate line with 83 trials (2 + 81 CCI).
- Date fixed: 2026-10-31. Unit of the t-stat/CI: calendar-week blocks (trades in a week are correlated). Multiple-testing: 2 trials (83 stricter).
- Verdict (copied from run output): `{mean_pos_ci_excl0: False, stress_mean_ge0: False, trades_ge_100: True, placebo_gt_p90: True, dsr2_pos: False} -> FAIL`. File: `results.json`.

## 8. Results (HELD-OUT first, placeholder fees; DEVELOP in brackets)
- n unit round-trips 1395 [3278]; independent events: n/a - unit trades inside one grid are strongly dependent (weekly blocks used); grids total 1563 over 2020-2024, 1315 ended in stop-out, 1051 had at least one fill.
- Mean net per trade -8.1 bp [-5.0]; median +17.7 bp [+31.3]; stress-fee mean -11.6 bp [-8.5]; gross mean -3.7 bp [-0.4]. 90% CI [-12.0, -4.4] bp [[-8.1, -2.0]].
- Win rate 56.0% [56.8%]; exits target 50.8% / stop-out 42.4% / regime-off 6.8%. Max drawdown of cumulative PnL -$19.3 [-$30.2]; total -$18.8 [-$27.1]; -$0.034 per day [-$0.021].
- Placebo (random regime times, random side, limit 1-3 steps from close, target 1 step, stop 4-k steps, regime flatten; 200 seeds; approximation of the grid): placebo mean -10.8 bp, real percentile 98.0 [100]. DSR prob 0.0000 (SR/trade -0.14 vs SR0 +0.014; 83-trial line also 0).
- Adverse selection vs random regime bars (bp, +1/+4/+12 bars): HELD +2.5 / +0.5 / -8.7 [DEVELOP -2.7 / -2.7 / +3.7].
- By symbol (HELD): BTC -6.3 bp, ETH -9.9 bp. By level (HELD): L1 -7.4, L2 -6.6, L3 -10.7 bp. Per-year and parameter neighbourhood: n/a - fixed parameters, none run.

## 9. Capacity and liquidity
n/a - no edge; sizes of $50 notional were used only to express PnL.

## 10. Correlation with other edges
n/a - no edge. Within the edge: BTC and ETH are highly correlated; several grids and levels fail together on a trend.

## 11. Search history / multiple testing
2 trials in this study (this and `range_mr_maker_v1`); stricter line 83 (adds the 81 CCI trials). No parameters tuned: one fixed configuration (one implementation fix after a DEVELOP-only look, see change log). Adjusted significance: not significant (negative mean). Siblings: `range_mr_maker_v1` FAIL, `cbb_cci_breakout_v1` dead. Not tested: premium reversion and liquidity-vacuum rebound (definitions not available).

## 12. Risks and failure modes
- Backtest limits: hourly bars, no order book/queue, generous through-by-1-tick fills, intrabar order unknown, no funding, USDT-M prices not USDC-M, survivor universe.
- Regime risk: only 2020-2024 seen. Tail risk: gap through stop (modelled at worse open), delisting not modelled. Operational/rule risk: the maker-fee promotion can end - n/a for a dead edge.

## 13. Kill criteria
Fired at the backtest stage (HELD-OUT FAIL). No live/paper criteria needed.

## 14. Reproduction
`cd ~/Desktop/PythonProject/edge_data/calm_market_2020 && ~/Desktop/PythonProject/cci_study/.venv/bin/python run.py` (offline, about 5 s; python 3 with pandas, numpy, scipy). Files: `run.py`, `PREREG.txt`, `results.json`, `trades_*.csv`, `REPORT.md`. Expected output: `results.json` in the same folder.

## 15. Paper / live tracking
n/a - dead, never started.

## 16. Change log
- 2026-10-31: PREREG.txt written; funding_carry_v1 dropped by the coordinator before any test.
- 2026-10-31: DEVELOP-only sanity run showed grid orders were being filled on the wrong (marketable) side of price at grid start. Fixed as an implementation bug (post-only orders are armed only when on the passive side of the last close; a grid starts only if price is inside +-4 levels). No parameter was changed. HELD-OUT was not computed before this fix. The earlier DEVELOP number was seen.
- 2026-10-31: final run; both candidates FAIL; status `dead`.
- 2026-10-31: TP variants tested on DEVELOP 2026-10-31: none eligible (range_mr_tp_band, range_mr_tp_1p5atr, grid_tp2, grid_tp3; see calm_market_2020/REPORT.md addendum). HELD-OUT not computed. Status stays dead.
- 2026-10-31: DEVELOP-only 144-variant sweep (108 range_mr + 36 grid), N=233 trials for DSR: 0 candidates, 0 variants with CI lower bound > 0; best grid +2.6 bp with CI spanning zero. Status stays dead. All variants in search_ledger.jsonl. See calm_market_2020/SWEEP.md.
