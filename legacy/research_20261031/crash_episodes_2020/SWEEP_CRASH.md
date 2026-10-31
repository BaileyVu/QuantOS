# SWEEP_CRASH: pre-registered improvement sweep for crash_flush and alt_dip (research only)
Pre-registration: `PREREG_CRASH.txt` (written before any sweep code ran). Code: `sweep_crash.py` (`sweep` then `select`). All variants: `sweep_crash.csv` and `edge_vault/search_ledger.jsonl` (family prefix `crash_episodes_2020/`). Existing scripts untouched; episode set, price/OI data unchanged. Sanity: my vectorised OI z-score matches the library function (max diff 1e-13), my alt_dip cutoffs match `results.json`, and the current rule reproduces the old trade counts exactly (148/32, 100/21, 691/70, 372/21 trades/episodes).

## What was run
Full factorial on BUILD (TRAIN, 110 episodes), no sampling: crash_flush 2592 variants (hold 4 x trail 3 x cat-stop 2 x entry 2 x BTC gate 2 x max-pos 3 x OI-z 3 x drawdown 3), alt_dip 864 variants (same exit/entry/gate/max-pos grid x cutoff quantile 3). Costs: maker 0%, taker 0.04%/side, slippage on taker fills 0.05% BTC/ETH and 0.10% others, maker ladder fills only on trade-through; stress = maker 0.02% / taker 0.05%. Unit = per-episode mean net return; CI = 90% bootstrap over episodes (2000, seed 1).
**N for multiple testing = 3900 ledger lines at the end + 81 prior = 3981** (3460 are mine: 3456 sweep + 4 current-rule reference runs; about 440 were written by the other process). DSR used N=3985 (ledger at selection + reserve of 8); SR0 = 0.78 (crash_flush), 0.85 (alt_dip) per-episode Sharpe.

## Result: NO FINALISTS in either family (both v2 sweeps dead)
| family | variants | CI90 low > 0 | >=30 eps | stress >= 0 | DSR z > 0 | best DSR z (prob) | best per-episode Sharpe vs SR0 |
|---|---|---|---|---|---|---|---|
| crash_flush | 2592 | 263 | 1872 | 969 | **0** | -0.61 (0.27) | 0.67 vs 0.78 |
| alt_dip | 864 | 150 | 864 | 404 | **0** | -2.07 (0.02) | 0.54 vs 0.85 |
No variant cleared the deflated-Sharpe gate, so the placebo and neighbour tests were never reached and no variant was evaluated on HELD-OUT (pre-registered; looking at HELD for near-misses would be a new trial on contaminated data). Best BUILD variants for the record: crash_flush = ladder entry, hold 12h, trail arm 5%/3%, OI z -2, dd 8%, max-pos 5: +56bp per episode, CI90 low +32bp, 32 episodes. alt_dip = hold 12h, no trail, cat 25%, BTC gate, q 0.5%, max-pos 5: +370bp, CI low +208bp, 48 episodes (cutoff is stricter so fewer episodes, no neighbour pass).

Where the factors matter (mean of per-episode bp over all cells; descriptive only):
- Trailing stops hurt alt_dip badly (none +236bp vs trail -71/-53) and crash_flush (none +10 vs -41/-44): the bounce is a slow drift that a trail cuts off.
- crash_flush: shorter holds are better (12h +34, 24h +28, 48h -59, 72h -104); the ladder beats market (+4 vs -54bp) but the ladder gain is mostly unfilled third-rungs shrinking exposure; BTC gate and max-pos do not help (gate -22 -> -29bp).
- alt_dip: catastrophe stop 25% beats 15% (+77 vs -2): tighter stops are hit by the crash noise then miss the rebound. Hold, gate and max-pos barely matter.
Nothing here supports changing the current rules; the pre-registered gate says the 3456-cell search found no improvement distinguishable from selection luck.

## Current production rules, same cost model (maker/taker as above, market entry, 24h, 25% stop, no gate, unlimited positions)
| rule | split | trades | episodes | mean/episode | CI90 | stress mean |
|---|---|---|---|---|---|---|
| crash_flush (z-2, dd 8%) | BUILD | 148 | 32 | +130bp | [-17, +275] | +128bp |
| crash_flush | HELD-OUT | 100 | 21 | +292bp | [+116, +460] | +290bp |
| alt_dip (q 1%) | BUILD | 691 | 70 | +283bp | [+141, +424] | +280bp |
| alt_dip | HELD-OUT | 372 | 21 | +253bp | [+85, +423] | +251bp |
(REPORT.md numbers differ slightly because it used 0.40% round-trip flat cost; the new model is cheaper: 0.08% fee plus 0.10-0.20% slippage.)
Forward-test spec: none written (no finalist beat the current rule). If the current rules are paper-traded, use the existing production definitions, not a v2 variant.

## Honesty
- Survivor universe: the 29 symbols are today's survivors (hindsight-selected); delisted or collapsed coins are absent. This inflates every dip-buying number, alt_dip most. Cost model also assumes fills at bar open/close after a crash, optimistic for small alts.
- Correlated episodes: 110 BUILD episodes cluster in time (e.g. 12 in 2021-05/06) and trades inside one episode are one bet; the bootstrap resamples episodes but not clusters of episodes, so CIs are optimistic. Overlapping holds on the same symbol across episodes (72h hold, 72h cooldown) are not netted. crash_flush has only 32-40 episodes (OI era from 2021-12), so even a real improvement is hard to detect.
- Designed with 2021-24 in view: crash_flush and alt_dip were chosen by people who had seen 2021-2024; the sweep grid itself was written knowing which direction the existing rules lean. HELD-OUT (2023-07..2024-12) is therefore not clean for the current rules, and BUILD includes the period the rules were designed on. No 2025+ data was used.
- Trials: 3456 sweep variants + 4 reference runs in this sweep; N = 3981 with the 81 prior trials (excludes un-ledgered tinkering, e.g. the setup of fee model and the OI-z check). Variants are highly correlated (cat stop 15 vs 25 often identical), so true independent trials are fewer, but the DSR used the dispersion of Sharpe across all cells, including bad ones (hold 72h, trails), which inflates SR0; a stricter-sample sensitivity was not run (would be a new trial).
- Fill model: ladder fill at the limit price on a low through it (no queue position), conservative same-bar ordering for stops/trails; stops fill at min(open, stop) plus 0.05-0.10% slippage. No funding costs included.
