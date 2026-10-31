# cbb_cci_breakout_v1: correction-before-breakout (CCI filter) on Binance perps

Full numbers are in `~/Desktop/PythonProject/cci_study/report.md`. This page follows EDGE_SPEC v1.0.

## 0. Status
`dead`, 2026-10-26. Pre-registered walk-forward OOS and holdout tests failed.

## 1. Identity
| field | value |
|---|---|
| edge_id | `cbb_cci_breakout_v1` |
| Name | Long breakouts only after a CCI correction, plus short mirror, ATR drawdown, trend filters and LightGBM |
| Owner | Claude for bailey |
| Origin | Tomas Nesnidal "16.5M NASDAQ indicators" claim, via `cci_breakout_audited.zip`. Siblings: the earlier spot CBB study (13 coins, −23.6bp OOS) |
| Created / frozen | 2026-10-26 / 2026-10-26 (holdout from 2026-07-26 was unseen until the final run) |

## 2. Hypothesis and mechanism
Hypothesis: a 24h/48h high breakout preceded by a correction (min CCI27 over 18 bars < −100) has a higher net return than an unfiltered breakout. The proposed mechanism is that shaken-out weak hands lead to cleaner continuation. The mechanism was not tested beyond returns.

## 3. Market and instruments
Binance USD-M perps for BTC ETH SOL BNB XRP DOGE ADA LINK LTC AVAX. Tested on USDT-M prices; the target venue is USDC-M (min notional $50 BTC, $20 ETH, $5 others).

## 4. Signal rule
See `cci_study/backtest_harness.py` (`breakout_events`, `exits`), `run.py` (`FILTERS`, `configs`) and the frozen `strategy.py`.

## 5. Data
1h and 15m klines plus funding, 2021-01-01 to 2026-10-25, 0 missing bars. Cached in `cci_study/data/*.parquet`.

## 6. Cost and fill model
- Taker 0.04% per side (stress 0.05%).
- Slippage 2bp per side for BTC/ETH and 5bp for the rest (stress ×2).
- Real funding.
- Entry at the next open. Stops are checked intrabar and assumed hit first.
- Liquidation when MAE reaches the liquidation distance.

## 7. Pre-registered pass criteria
The bailey spec, Step 8 MAINNET-CANDIDATE:
- stitched OOS mean > 0 with a CI that excludes 0
- beats the placebo 95th percentile
- positive under stress costs
- positive in ≥2/3 of years and most symbols
- holdout not negative
- ruin < 5%

## 8. Results
- WF rules: −9.3bp, CI [−34.6, +15.5], placebo p=0.095 (fails), stress −20.1bp, 2/5 years, 4/10 symbols, DSR 0.00.
- LightGBM: −4.4bp OOS.
- Holdout: −29.2bp. **FAIL.**

## 9. Capacity
n/a: there is no edge.

## 10. Correlation
n/a: there is no edge.

## 11. Search history
- 81 configs (A 24, B 48, B2 8, D 1), logged in `cci_study/configs_log.csv`.
- On top of that, the 2 variants in the original zip and the earlier spot CBB grid.

## 12. Risks
n/a: dead.

## 13. Kill criteria
Fired at the backtest stage.

## 14. Reproduction
```
cd ~/Desktop/PythonProject/cci_study && .venv/bin/python data.py 1h 15m && .venv/bin/python run.py build && .venv/bin/python run.py wf && .venv/bin/python run.py holdout
```

## 15. Paper / live
Not started. `paper_trader.py` exists for testnet plumbing only.

## 16. Change log
- 2026-10-26: created and killed.
