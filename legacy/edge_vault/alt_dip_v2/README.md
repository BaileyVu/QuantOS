# alt_dip_v2: alt_dip sweep v2 (exit/entry/threshold sweep of the current rule)
Follows EDGE_SPEC v1.0. Full numbers: `~/Desktop/PythonProject/edge_data/crash_episodes_2020/SWEEP_CRASH.md`, `sweep_crash.csv`, pre-registration `PREREG_CRASH.txt`.

## 0. Status
`dead`, 2026-10-31. Pre-registered selection (DSR>0 at N=3981, CI>0, stress>=0, >=30 episodes, placebo, neighbours) found no finalist among all 864 BUILD variants; no variant was evaluated on HELD-OUT. The current rule (v1) is unchanged by this doc.

## 1. Identity
edge_id `alt_dip_v2`; owner Claude for bailey; origin: improvement sweep of crash-episode rules (siblings: v1 rule, bounce_momentum, cbb_cci_breakout_v1); created/frozen 2026-10-31.

## 2. Hypothesis and mechanism
Hypothesis: tuning hold, trailing stop, catastrophe stop, maker-ladder entry, BTC gate, max positions and thresholds improves net return per crash episode versus the current rule. Mechanism: forced liquidations overshoot, price mean-reverts within hours; a maker ladder buys the overshoot cheaper. Not supported by the data.

## 3. Market and instruments
Binance USDT-M perps; 29 USDT-M perps (ALT_SOURCE_UNIVERSE); quarterly cutoff None before 2020-07. Survivor universe (today's listings), long only; perps funding not modelled.

## 4. Signal rule
Decision hourly UTC at close t in [episode, +48h]; 24h return <= quarterly pooled quantile cutoff Q in {0.5,1,2}% (window [S-90d,S-3d)); entry/exit/gate/max-pos options as crash_flush_v2. Code: `sweep_crash.py:events,cutoffs`.

## 5. Data
data.binance.vision hourly klines 2020-01..2024-12 + OI archive (2021-10/12 onward). BUILD = episodes starting <= 2023-06-30 (110); HELD-OUT = 2023-07..2024-12 (22), untouched by this sweep. Frozen: `edge_data/crash_episodes_2020/data/prices.npz`. Leakage checks: signals use bars <= t, entry at t+1 open, OI available with 1h lag, alt_dip cutoff from [S-90d,S-3d).

## 6. Cost and fill model
Maker 0%, taker 0.04%/side, slippage 0.05% BTC/ETH else 0.10% on taker fills; maker rungs fill only if low < limit; stress maker 0.02%/taker 0.05%. Stops fill at min(open, stop). No funding.

## 7. Pre-registered pass criteria
`PREREG_CRASH.txt` (dated before the run). Unit: per-episode mean; CI90 episode bootstrap; DSR z>0 at N=3985. Verdict: FAIL (zero finalists; see `sweep_crash.csv` verdict column).

## 8. Results
Best BUILD cell +370bp per episode (CI low +208bp, 48 episodes) but DSR z -2.07 (prob 0.02), Sharpe 0.54 below SR0 0.85. 0 of 864 pass DSR. Current rule BUILD +283bp [+141,+424] (70 eps); HELD-OUT +253bp [+85,+423] (21 eps), stress +251bp, new cost model.

## 9-10. Capacity, correlation
n/a: no variant passed. Episodes cluster in time; positions within an episode share one crash.

## 11. Search history
3456 sweep variants in this sweep (both families together) plus 4 reference runs; ledger `edge_vault/search_ledger.jsonl`; N = 3981 incl. 81 prior. Selection used BUILD only.

## 12. Risks
Survivorship, correlated episodes, rules designed with 2021-24 in view, optimistic bar-level fills.

## 13. Kill criteria
n/a: dead. The v1 rule's own monitoring is out of scope here.

## 14. Reproduction
`cd ~/Desktop/PythonProject/edge_data/crash_episodes_2020 && ~/Desktop/PythonProject/QuantOS/.venv/bin/python sweep_crash.py sweep && ... select` (offline, under 10s; note select appends to the ledger again, so re-run only for review).

## 15. Paper / live tracking
n/a: dead.

## 16. Change log
- 2026-10-31: pre-registered, swept, no finalist, status dead.
