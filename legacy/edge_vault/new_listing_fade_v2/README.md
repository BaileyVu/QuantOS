# new_listing_fade_v2: short fresh USD-M listings, full archive universe incl. delisted

Follows EDGE_SPEC v1.0. Supersedes `new_listing_fade_v1` (25 surviving coins). Pre-reg `edge_data/block4_2020/PREREG3.txt` (+Amendment 1, frozen before kline/5m/funding files were loaded), code `run3.py`, `fetch3.py`, tables `sweep_L2.csv`, `summary3.json`.

## 0. Status
`dead`, 2026-11-01. Finalist failed BUILD gates (ci_lo, dsr, nb); HELD-OUT 2023-07..2024-12 listings not run.
## 1-3. Identity / mechanism / market
edge_id `new_listing_fade_v2`. Launch hype and thin float lift new perps; supply erodes it. 396 USDT perps with first archive month 2020-01..2024-12 (data.binance.vision), delisted included.
## 4. Signal rule
Short at open of L+24h*(1+d), d<W, hold H days, filter none/up; 18-variant grid. Stop 2.5% above entry (5%/2x cap) on 5m highs; funding paid/received included; leverage 2x; delist-announcement overlap +-7d excluded in primary set (secondary includes).
## 5. Data
Archive 1h+5m klines and fundingRate, first 2 months per symbol, <=2024-12 (listings_raw/). Delist announcements from edge_data/announcements/binance_161.csv (coverage from 2022-02 only).
## 6-7. Cost and pass rule
Taker 4bp + 10bp slip (5bp BTC/ETH) each side, stress taker 5bp. BUILD CI90>0, DSR z>0 (N=4170), stress>=0, >=100 trades, placebo>p90, neighbours>0, then HELD-OUT.
## 8. Results
Finalist {'W': 14, 'H': 7, 'f': 'up'}: 18.7 bp/trade, CI90 [-30.1,70.1], 1177 trades over 144 coins, stress 16.7, DSR z -1.79 (SR0 0.40), placebo p90 17.5, neighbours [-5.6, -9.6]. Stop hit rate 85%; 1h-high fallback 0.17%. Including delist-overlap trades: 19.6 bp on 1183 (effect small). 0/18 variants CI>0.
USDC reality: only 24/209 BUILD and 10/187 HELD listings ever had a USDC perp; at entry USDC-tradable trades in finalist = 0.
## 9-10. Capacity / correlation
n/a - dead.
## 11. Search history
18 primary + 18 secondary variants in search_ledger.jsonl (families block4_2020/new_listing_fade_v2, ..._incl_delist). v1's 18 variants (35-trade finalist, survivors only) were an artefact of small n and survivor bias.
## 12. Risks
Tight stop is hit 85% of the time; new listings are volatile; first 2 months only.
## 13-15. Kill / reproduction / tracking
n/a - dead. Reproduce: `python fetch3.py; python run3.py` in edge_data/block4_2020 (appends ledger lines).
## 16. Change log
2026-11-01 created, dead.
