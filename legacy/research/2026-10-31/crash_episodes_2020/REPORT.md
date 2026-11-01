# Crash episodes 2020-2024: causal episode study (research only)
Re-run: `fetch.py` (archive download) -> `build.py` (tables) -> `episodes.py` -> `evaluate.py`. Python 3.12 venv, numpy (no pandas). Floats throughout; strategy functions imported read-only (Decimal inside them).

## Data coverage
- 29 ALT_SOURCE_UNIVERSE USDT-M perps, hourly, 2020-01-01..2024-12-31 UTC. 2021-10+ from the train manifest zips (39 monthly zips/symbol); 2020-01..2021-09 downloaded from data.binance.vision (461 zips ok, 148 returned 404 = not listed yet, 0 failures). Tables: `data/klines/SYM.csv`, `data/prices.npz`; coverage in `data_coverage.json`.
- Listing starts (first bar): BTC/ETH/BCH 2020-01-01; 13 symbols by 2020-02; 19 by 2020-09; SAND 2021-01, MANA 2021-03, GALA 2021-09-18. Symbols with data: 8 (Jan 2020), 13 (Feb-Mar 2020), ~26 (end 2020), 29 from 2021-09-18.
- Internal gaps: none for 19 symbols. 10 symbols (SOL, XRP, LTC, TRX, NEAR, FIL, XLM, SAND, MANA, VET) lack 72h from 2022-02-26 and 48h from 2022-04-01 (archive holes). Gap bars are NaN and never forward-filled; trades touching a NaN bar are skipped.
- OI (9 MAJORS, exact-hour rows, avail = label+1h): starts 2021-12-01 (BTC 2021-10-01); ~27.0k rows each; 14-41 non-positive rows per symbol (handled by the tolerant z-score). 66 of 132 episodes start in the OI era (>= 2021-12); crash_flush can only ever fire there.

## Episodes (breadth>=0.5 of >=5 available symbols with 24h ret <= -8%, AND equal-weight index 24h <= -7%, 72h cooldown)
- The min-5-available-symbols guard is my addition (only matters Jan 2020; first episode 2020-01-20 has 8 symbols).
- 132 episodes: TRAIN (to 2023-06-30) 110 (2020: 25, 2021: 46, 2022: 31, 2023H1: 8); HELD-OUT 22 (2023H2: 3, 2024: 19). Full list with breadth, index return, 7d drawdown, outcomes: `episodes.csv`; per-symbol forwards: `episode_symbol_fwd.csv`.
- These thresholds are NOT rare on a 29-alt universe (about 2.2 episodes/month); episodes cluster (e.g. 2020-03: 5, 2021-05/06: 12). Episodes are not independent.
- Sensitivity (episode counts total / train / held; the -8% per-symbol threshold fixed):

| breadth \ index24h | -5% | -7% | -10% |
|---|---|---|---|
| 0.4 | 157/129/28 | 150/122/28 | 75/63/12 |
| 0.5 | 134/112/22 | 132/110/22 | 75/63/12 |
| 0.6 | 107/89/18 | 107/89/18 | 75/63/12 |

- Index forward return from entry (open t+1), mean / median: TRAIN 12h +0.56%/+1.19%, 24h +1.05%/+1.57%, 72h +1.60%/+1.46%; HELD 12h +0.85%/+0.95%, 24h +0.58%/+2.48%, 72h +3.55%/+5.71%. Mean 72h MAE: index (close path) TRAIN -7.1%, HELD -5.6%; mean per-symbol low -10.1% / -7.6%. Some episodes (e.g. 2020-03-12: -35.7% at 24h) are catastrophic; the mean hides it.

## Rules as trades (long only, 0.40% round-trip cost, 25% stop on hourly lows, 24h hold, entry = next-hour open)
Cells: mean per-episode net return [90% bootstrap CI over episodes, 2000 resamples, seed 1]. "Eps" = episodes with >=1 trade of the 110 (TRAIN) / 22 (HELD). Trade stats are per trade.

| Rule | Split | Eps | Trades | Mean/trade | Median | Win | Mean/episode [90% CI] | Worst |
|---|---|---|---|---|---|---|---|---|
| crash_flush | TRAIN | 32 | 148 | +0.51% | +0.89% | 57% | +1.16% [-0.30, +2.62] | -25.3% |
| crash_flush | HELD | 21 | 100 | +2.34% | +2.74% | 68% | +2.78% [+1.02, +4.46] | -13.8% |
| alt_dip | TRAIN | 70 | 691 | +2.54% | +3.47% | 66% | +2.70% [+1.28, +4.11] | -25.3% |
| alt_dip | HELD | 21 | 372 | +2.32% | +2.18% | 63% | +2.40% [+0.72, +4.10] | -22.7% |
| bounce_momentum | TRAIN | 110 | 550 | +1.43% | +0.74% | 57% | +1.43% [+0.15, +2.83] | -25.3% |
| bounce_momentum | HELD | 22 | 110 | -0.56% | +1.74% | 57% | -0.56% [-3.47, +2.06] | -25.3% |
| basket at entry | TRAIN | 110 | 2848 | +0.25% | +0.89% | 57% | +0.23% [-0.85, +1.30] | -25.3% |
| basket at entry | HELD | 22 | 638 | +0.15% | +0.53% | 55% | +0.15% [-2.17, +2.33] | -25.3% |

Random non-episode baseline (same count of pseudo-starts per split, excluded from -24h..+96h of real starts; seed 1, plus mean-per-episode averaged over 30 other seeds):

| Rule | Split | Eps/Trades (seed 1) | Mean/episode [90% CI] (seed 1) | 30-seed mean |
|---|---|---|---|---|
| crash_flush | TRAIN | 13/27 | +0.00% [-1.77, +1.61] | +1.34% |
| crash_flush | HELD | 6/27 | +1.07% [-2.20, +4.34] | +2.03% |
| alt_dip | TRAIN | 20/101 | -1.58% [-4.60, +1.11] | +1.73% |
| alt_dip | HELD | 10/74 | -0.51% [-1.90, +0.83] | +1.34% |
| bounce_momentum | TRAIN | 110/550 | +0.34% [-0.29, +1.02] | +0.12% |
| bounce_momentum | HELD | 22/110 | +0.66% [-0.85, +2.53] | -0.47% |
| basket at entry | TRAIN | 110/2799 | +0.71% [+0.24, +1.26] | +0.48% |
| basket at entry | HELD | 22/638 | +0.56% [-0.59, +1.96] | -0.08% |

Notes: crash_flush TRAIN has only 48 of 110 episodes inside the OI era (hence 32 with trades); HELD is fully covered (21 of 22). alt_dip quarterly cutoff (1% pooled quantile) is None for 2020Q1-Q2 (fail-closed: <90d history), so 2020-01..2020-06 episodes get no alt_dip trades; 2022Q2/Q3 cutoffs use 19 symbols (gap holes above). Cutoffs range -7.3% (2023Q4) to -25.3% (2021Q3); in `results.json`. Trades: `trades.csv` (set=episode|random).

## Reading the results
- Buying the whole basket at the episode entry earns ~0 net of costs in both splits: there is no generic "buy the crash" effect at 24h.
- Selective rules (alt_dip, crash_flush) have positive mean per-episode returns in both splits; the HELD 90% CIs exclude zero for crash_flush (+1.0..+4.5%), alt_dip (+0.7..+4.1%). Per the brief this is the only bar for "edge", and it is cleared nominally, but see caveats: N=21 episodes each, not independent, rules not unseen.
- Random-hour controls are inconclusive: the 30-seed random means (+1.3..+2.0%) are conditional on the rule firing at all, and are not far below the episode figures (alt_dip HELD 2.40% vs 1.34%; crash_flush HELD 2.78% vs 2.03%). I did not test the episode-vs-random difference formally; the data do not show episodes clearly matter beyond the dip/OI gates themselves.
- bounce_momentum (new): TRAIN CI just excludes zero (+0.15..+2.83%), HELD negative with a wide CI; not supported.

## What this does NOT show
- Survivor universe: the 29 symbols are today's listings that survived to 2024+ (selected with hindsight); delisted/collapsed coins are absent, which biases "buy the dip" results upward, especially alt_dip and bounce_momentum.
- crash_flush and alt_dip were designed by people who had already seen 2021-2024 data, so HELD (2023-07..2024-12) is not an unseen sample for them; only the pre-2021-10 part of TRAIN (the 2020 data, new here) is genuinely out-of-design, and crash_flush cannot trade there (no OI). alt_dip's 2020H2 episodes are the only fresh test.
- Small N: 22 HELD episodes, 21 with trades; trades within an episode are highly correlated (same crash), and episodes cluster in time, so even episode-level bootstrap CIs are optimistic. Bootstrap ignores overlap of 24h holds and cooldown-chained episodes.
- Multiple looks: 3 rules x 2 splits x two CI types, thresholds frozen a priori and not tuned, but several rule variants were pre-existing selections.
- Cost model is flat (0.40% RT); real slippage in a market-wide crash is likely larger for small alts, and fills at the open after a -8% hour are optimistic. Stop fill assumed at stop minus 0.15%; gap-through-stop would be worse (the worst trades are near -25%).
- Episode detection needs >=5 symbols and the equal-weight index is a mean of log returns (rebalanced hourly), not a tradable basket; 2020 index is dominated by ~8-16 coins.
- Archive holes (2022-02-26, 2022-04-01) and OI non-positive rows are handled by skipping/tolerance, not repaired.
