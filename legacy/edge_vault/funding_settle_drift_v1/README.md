# funding_settle_drift_v1: Funding-settlement drift: price drift 1-2h around 00/08/16 UTC conditioned on extreme prior funding

Follows EDGE_SPEC v1.0. Code `edge_data/block4_2020/run2.py`, pre-registration `PREREG2.txt` (2026-11-01, before any run), tables `sweep_B.csv`, `summary2.json`.

## 0. Status
`dead`, 2026-11-01. Finalist failed BUILD gates (ci_lo, dsr, stress, placebo, nb); HELD-OUT never evaluated.
## 1. Identity
edge_id `funding_settle_drift_v1`; owner Claude for bailey; origin: coordinator block 2.
## 2. Hypothesis and mechanism
Paying side pares before the stamp and re-adds after. Direction fixed: high funding pre=short/post=long, low mirrored.
## 3. Market and instruments
Binance USDT-M perps on disk (29 perps), hourly klines.
## 4. Signal rule
Prev settlement rate vs trailing 90 settlements, tail q; pre: enter open T-w exit open T; post: enter open T exit T+w; taker.
## 5. Data
klines `edge_data/crash_episodes_2020/data/klines`; Binance funding from QuantOS manifest zips (read-only, 2021-10+). No 2025+.
## 6. Cost and fill model
Taker 4bp + slippage 5bp BTC/ETH, 10bp others per side; stress taker 5bp. Funding ignored in A.
## 7. Pre-registered pass criteria
BUILD CI90>0, DSR z>0 at N=4098, stress>=0, >=100 trades, placebo>p90, neighbours>0, then HELD-OUT CI90>0. See PREREG2.txt.
## 8. Results
Finalist {'t': 'post', 'w': 2, 'q': 0.1, 'tail': 'high'}: -24.7 bp/trade, CI90 [-38.1, -11.4], trades 1603, stress -26.7, DSR z -8.18 (SR0 0.34), placebo p90 -22.4, neighbours [-24.9, -30.5]. Gates {'ci_lo': False, 'dsr': False, 'stress': False, 'trades': True, 'placebo': False, 'nb': False}. Verdict dead.
## 9. Capacity and liquidity
n/a - dead.
## 10. Correlation with other edges
n/a - dead.
## 11. Search history / multiple testing
24 variants in this family, all in edge_vault/search_ledger.jsonl (family block4_2020/funding_settle_drift). Skipped as duplicates: premium extremes (round 7), OI-surge breakout (loops 57-106).
## 12. Risks and failure modes
Cross-coin correlated events (bootstrapped by day); costs dominate.
## 13. Kill criteria
n/a - dead.
## 14. Reproduction
`cci_study/.venv/bin/python run2.py` in edge_data/block4_2020 (appends ledger lines each run).
## 15. Paper / live tracking
n/a - dead.
## 16. Change log
2026-11-01 created, dead.
