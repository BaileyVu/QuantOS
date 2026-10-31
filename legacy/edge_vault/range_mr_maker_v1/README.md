# range_mr_maker_v1: Calm-regime Bollinger/RSI mean reversion with post-only maker entry and target

Follows EDGE_SPEC v1.0. Full tables: `~/Desktop/PythonProject/edge_data/calm_market_2020/REPORT.md`; raw: `results.json`, trades CSV.

## 0. Status
`dead`, 2026-10-31. The pre-registered HELD-OUT pass rule FAILED (CI below zero, negative under stress fees, DSR not positive).

## 1. Identity
| field | content |
|---|---|
| edge_id | `range_mr_maker_v1` |
| Name | Calm-regime Bollinger/RSI mean reversion with post-only maker entry and target |
| Status | dead (2026-10-31) |
| Owner / author | Claude research subagent for bailey |
| Origin | Calm-market revisit motivated by a USDC-perp maker-fee promotion; sibling ``grid_calm_maker_v1`` and the 81 CCI trials (`cbb_cci_breakout_v1`) |
| Created / frozen | 2026-10-31 / 2026-10-31 (PREREG.txt written before HELD-OUT was computed) |

## 2. Hypothesis and mechanism
- Hypothesis: In a calm regime, a close beyond the Bollinger(20,2) band with RSI(14) beyond 30/70 reverts to SMA(20), giving positive net return per trade with maker entry and exit.
- Why it should exist: Over-extended moves in a quiet range may revert as liquidity providers earn the spread; with a zero maker fee the cost drag is small. The HELD-OUT result shows continued drift against the position instead (adverse selection).
- Why it isn't arbitraged away: n/a - no edge found.
- What would kill the mechanism: n/a - dead.

## 3. Market and instruments
- Venue: Binance USDT-M perps (research proxy for the owner's USDC-M perps). Universe: BTC ETH SOL BNB XRP ADA DOGE LINK. Fixed in advance, but these are coins that survive to 2024 (survivorship bias, not corrected).
- Side: both. Contract details: n/a - research on hourly OHLC; tick assumed 1e-5 of price; min notional ignored.

## 4. Signal rule
Decision clock: hourly bars, UTC, decide at close of bar t, orders live from bar t+1. ATR(14) = 14-bar simple mean of true range; regime = ATR(14)/close below its own rolling 720-bar (30-day) median AND |close/SMA(100) - 1| < 3%.
Entry: long if regime and close < lower band(20, 2.0, population std) and RSI(14, Wilder) < 30; short if close > upper band and RSI > 70. Post-only limit at min(band, signal close) for buys / max(band, close) for sells, which equals the signal close; valid for the next 3 bars; unfilled = missed (fill rate reported).
Exit: maker limit sell/buy at SMA(20) of the previous close, only from the bar after the fill; stop 2 x ATR(14) (of the signal bar) from entry, taker; after 24 bars exit at the next open (taker).
Sizing: $50 notional per trade (1x of a $50 account, concurrency up to 8 ignored).
Concurrency / session stops: One position (or pending order) per symbol; signals while busy are ignored.
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
- n trades 421 [692]; fill rate 99.3% [99.7%]; independent events: n/a - about one per signal per symbol (weekly blocks used for CI).
- Mean net per trade -23.0 bp [-27.4]; median -61.5 bp [-87.1]; stress-fee mean -26.5 bp [-30.9]. 90% CI [-37.5, -9.7] bp [[-39.1, -16.4]]. Gross mean -16.8 bp.
- Win rate 46.1% [46.7%]; exits stop 51.8% / target 48.0%. Max drawdown of cumulative PnL (at $50 notional) -$59.6 [-$98.4]; total -$48.5 [-$94.9]; -$0.088 per day [-$0.074].
- Placebo (random regime times, random side, same mechanics, 200 seeds): placebo mean -42.9 bp, real percentile 100 (better than random, still negative). DSR prob 0.0000 (SR/trade -0.17 vs SR0 +0.025; 83-trial line also 0).
- Adverse selection vs random regime bars (bp, +1/+4/+12 bars): HELD +2.3 / -5.8 / -33.6 [DEVELOP +5.2 / -11.5 / -14.5].
- Per symbol (HELD): all 8 negative, from -12.6 bp (DOGE) to -34.4 bp (SOL). By side: short -10.4 bp, long -35.1 bp. Per-year and parameter neighbourhood: n/a - parameters were fixed, no neighbours run. Benchmark: random-entry placebo above.

## 9. Capacity and liquidity
n/a - no edge; sizes of $50 notional were used only to express PnL.

## 10. Correlation with other edges
n/a - no edge. Within the edge: 8 coins move together; positions are not independent bets.

## 11. Search history / multiple testing
2 trials in this study (this and `grid_calm_maker_v1`); stricter line 83 (adds the 81 CCI trials). No parameters tuned: one fixed configuration. Selection used no test data. Adjusted significance: not significant (negative mean). Siblings: `grid_calm_maker_v1` FAIL, `cbb_cci_breakout_v1` dead. Not tested: premium reversion and liquidity-vacuum rebound (definitions not available).

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
