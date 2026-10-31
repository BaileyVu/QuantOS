# new_listing_fade_v1: New-listing fade: short USD-M perps over days 1-15 after listing

Follows EDGE_SPEC v1.0. Code `edge_data/block4_2020/run2.py`, pre-registration `PREREG2.txt` (2026-11-01, before any run), tables `sweep_A.csv`, `summary2.json`.

## 0. Status
`dead`, 2026-11-01. Finalist failed BUILD gates (trades); HELD-OUT never evaluated.
## 1. Identity
edge_id `new_listing_fade_v1`; owner Claude for bailey; origin: coordinator block 2.
## 2. Hypothesis and mechanism
Launch hype/thin float lifts new perps; supply erodes it over days.
## 3. Market and instruments
Binance USDT-M perps on disk (25 post-2020 listings, survivors only), hourly klines.
## 4. Signal rule
Short at daily entries L+24h*(1+d), d<W, hold H days, optional filter close>first open. Taker, cost 2*(4+10)bp.
## 5. Data
klines `edge_data/crash_episodes_2020/data/klines`; onboardDate from one exchangeInfo GET (exinfo.json). No 2025+.
## 6. Cost and fill model
Taker 4bp + slippage 5bp BTC/ETH, 10bp others per side; stress taker 5bp. Funding ignored in A.
## 7. Pre-registered pass criteria
BUILD CI90>0, DSR z>0 at N=4098, stress>=0, >=100 trades, placebo>p90, neighbours>0, then HELD-OUT CI90>0. See PREREG2.txt.
## 8. Results
Finalist {'W': 3, 'H': 1, 'f': 'up'}: 322.4 bp/trade, CI90 [68.7, 611.6], trades 35, stress 320.4, DSR z 0.12 (SR0 0.60), placebo p90 86.4, neighbours [114.2, 189.6]. Gates {'ci_lo': True, 'dsr': True, 'stress': True, 'trades': False, 'placebo': True, 'nb': True}. Verdict dead.
## 9. Capacity and liquidity
n/a - dead.
## 10. Correlation with other edges
n/a - dead.
## 11. Search history / multiple testing
18 variants in this family, all in edge_vault/search_ledger.jsonl (family block4_2020/new_listing_fade). Skipped as duplicates: premium extremes (round 7), OI-surge breakout (loops 57-106).
## 12. Risks and failure modes
Survivor bias, only 25 listings, clustered by coin, small-n variant selected by DSR.
## 13. Kill criteria
n/a - dead.
## 14. Reproduction
`cci_study/.venv/bin/python run2.py` in edge_data/block4_2020 (appends ledger lines each run).
## 15. Paper / live tracking
n/a - dead.
## 16. Change log
2026-11-01 created, dead.

## Update 2026-11-01
Superseded by `new_listing_fade_v2` (full universe incl. delisted, 5m stops, funding): dead there as well.
