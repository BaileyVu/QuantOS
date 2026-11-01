# xs_rank_ret_v1: Weekly cross-sectional market-neutral long/short on past-return rank (momentum or reversal)

Follows EDGE_SPEC v1.0. Code `~/Desktop/PythonProject/edge_data/block4_2020/run.py` (one command: `cci_study/.venv/bin/python run.py`, seconds), pre-registration `PREREG.txt` (dated 2026-11-01, before any run), tables `sweep_F1.csv`, `summary.json`.

## 0. Status
`dead`, 2026-11-01. Finalist failed the pre-registered BUILD gates; HELD-OUT 2023-07..2024-12 was therefore never evaluated.

## 1. Identity
edge_id `xs_rank_ret_v1`; owner Claude for bailey; origin: block4 search for families not on the dead list (all earlier work was time-series/single-asset/event).

## 2. Hypothesis and mechanism
Alts herd on attention: winners keep winning (slow diffusion) or overreact and revert. Market-neutral construction removes BTC beta. Both directions in grid.

## 3. Market and instruments
29 Binance USDT-M perps (survivor cohort, hindsight bias noted), hourly klines, 7d/3d holds.

## 4. Signal rule
Rank 29 perps by return over L days (closes to bar i-1); long top k, short bottom k (or reverse for rev); weights 1/(2k); hold 7d; rebalance Mon 00:00 UTC; fill open i+1. Signal at close of bar i-1, fill at open of bar i+1 (one extra bar). Grid of 24 variants, see PREREG.txt.

## 5. Data
`edge_data/crash_episodes_2020/data/klines/*.csv` (hourly o/h/l/c); funding from QuantOS manifest zips (read-only, 2021-10+, used only in F2). No 2025+ data.

## 6. Cost and fill model
Taker 4bp + slippage 5bp (BTC/ETH) or 10bp (others) per side on turnover; stress taker 5bp. Funding ignored in F1/F3 (neutral book; caveat), included in F2.

## 7. Pre-registered pass criteria
BUILD CI90 lower>0; DSR z>0 at N=4056 (ledger lines + 81; SR0 from the family's cross-variant SR variance); stress mean>=0; >=100 trades; beats random-leg placebo at 90th pct; both neighbours positive; then HELD-OUT CI90>0. Date 2026-11-01.

## 8. Results (BUILD 2020-01..2023-06)
Finalist `F1_L7_k5_signmom_H7`: mean 103.0 bp per weekly portfolio (gross-1 notional), CI90 [31.3, 178.8], trades 1690, stress 101.5 bp, DSR z -3.73 (SR0 0.438/period), placebo 90th pct 28.6 bp, neighbour means [73.4, 29.3].
Gates: {"ci_lo": true, "dsr": false, "stress": true, "trades": true, "placebo": true, "nb": true}.
Verdict: `dead` (failed: dsr). HELD-OUT: not evaluated.

## 9. Capacity and liquidity
n/a - not tradable; perps are liquid but edge not established.

## 10. Correlation with other edges
n/a - dead.

## 11. Search history / multiple testing
72 variants in this block (24 in this family), all appended to `edge_vault/search_ledger.jsonl` (family `block4_2020/xs_rank_ret_v1`). Deflation N = 4056.

## 12. Risks and failure modes
Survivor universe (hindsight cohort), 2020-21 bull regime dominates BUILD, few independent weekly observations (~169), cross-variant SR variance inflates SR0 when both signs are in the grid.

## 13. Kill criteria
n/a - dead; do not forward-test.

## 14. Reproduction
`cd ~/Desktop/PythonProject/edge_data/block4_2020 && ~/Desktop/PythonProject/cci_study/.venv/bin/python run.py` (note: re-running appends duplicate ledger lines).

## 15. Paper / live tracking
n/a - dead.

## 16. Change log
2026-11-01 created, dead.
