# One TimesFM drift A/B — fixed-cost L2

Owner amendment: 2026-11-08/09. This is the sole remaining TimesFM trial.
Crash/dip is cancelled. The completed width trial is final and is not reused
as a signal. No market inference, fitting or outcome inspection for this A/B
has occurred at this specification stage. Execution requires a frozen JSON
plan with verified data, relevant source, model and full dependency hashes.

## Data and arms

Use the unchanged USDT archive whose base manifest SHA256 is
`e3464f95f66e438c42c78c30187dd639da74014d25fce6b22c60bfe2ac551810`
and repair overlay SHA256 is
`8fbb13eadcfa148c6479d444b7a2a3edf14811a35f3a53fb5afaa3e2c85cee9f`.
Completed hourly closes start 2021-10-01 01:00 UTC and end strictly before
2023-07-01. Never open HELD or 2025+ archive partitions. All hourly rows must
be complete, unique and checksum verified; gaps stop the run, never drop coins.

Universe is the original ml1h C21 intersected with the available archive,
chosen by metadata before outcomes: BTC, ETH, SOL, XRP, DOGE, BNB, LINK, AVAX,
LTC, BCH, FIL, ADA, ETC, TRX, XLM, ATOM (all USDT). EOS, BAT, DASH, IOST and
IOTA are unavailable and excluded before any run. This is a survivor/proxy
cohort, not historical USDC execution evidence. A new OHLCV export is needed:
the completed width pilot export has only three assets, one year and no open
or volume. Do not overwrite or regenerate that pilot export.

A = exact causal 32-feature ml1h port. B = A plus only log median forecast
price/current close at 4, 12 and 24 hours. TimesFM2.5 pinned revision and
weights already used by the width pilot; exactly 512 completed hourly closes,
no surprise/width feature. Both arms use identical eligible origins, including
512-hour model warmup. Original opening timestamps determine candle time/day
features; decision timestamps are completed-close boundaries.

## One walk-forward

Test quarters: 2022Q4, 2023Q1 and 2023Q2. Four elapsed calendar quarters precede
the first test. Expanding fit history excludes the immediately previous quarter,
which is reserved for training-only calibration. Fit labels end strictly before
calibration begins; calibration labels end strictly before the test begins.
Use a 49-hour label end for the primary and one-bar-delay robustness scenarios.
No test label is used for fitting, calibration, feature selection or thresholds.

Per arm, fit LONG and SHORT LightGBM classifiers: 200 trees, 31 leaves,
learning_rate=.05, min_child_samples=500, subsample=.7, subsample_freq=1,
colsample_bytree=.8, n_jobs=2, random_state=17. Threshold is the previous-quarter
98th percentile of out-of-fit scores (runtime ml1h uses top2%). No top1% branch,
distilled rule, hyperparameter search, feature selection or rerun after results.
Both classes must exist in fit/calibration or the trial is not evaluable.

One combined LONG/SHORT policy per arm. At a common origin choose the side
with greatest score-minus-threshold; exact ties prefer LONG. One filled
position per symbol, with a conservative 48-hour lock even after early exit.
A failed maker fill does not become a trade or occupy that lock. Prediction
and side choice must precede fill observation; never choose the other side
because the first selected side did not fill.

## Owner-authorized fixed costs and label scenario

Use the original experiment's maker limit at signal close, strict next-bar
trade-through, target/stop at +/-1.5 high-low ATR14, 48-hour maximum hold.
Target cannot fill on the entry bar. Stop wins ambiguous bars. A timeout uses
the final close. Fixed maker entry and target fee are zero as in that reference;
stop/time exit subtracts 4bp taker +10bp slippage for EVERY asset. Scale both
components by 1,2,3; same trades and predictions in every cost scenario.
This explicit owner amendment applies to this research comparison only.
Production fee/slippage/funding/risk settings remain unchanged. Funding,
queueing, measured depth and historical USDC listing evidence are absent, so
label every result **fixed-cost L2**, never full L2 or portfolio returns.

Delay diagnostic moves order availability one full bar later, keeps original
entry/stop/target prices and uses the next eligible trade-through bar. The
48-hour timeout moves with availability. No delayed data enters the features.

## Frozen statistics and decision

For each arm and each 1x/2x/3x cost report n, mean net trade return, CI90,
quarter means, placebo p90 and daily block Monte Carlo. Use 2,000 circular
seven-calendar-day bootstrap draws, seed17, shared dates across assets and
arms; retain empty days. Paired augmented-minus-baseline mean CI uses the same
draws. Placebo: 200 uniform draws with replacement from eligible filled orders,
matched to each arm's symbol/side/quarter trade counts, seed17. As in the
reference this placebo does not impose a portfolio overlap constraint; disclose
that limitation. No count resampling from observed profitable subsets.

At BOTH 1x and2x require augmented n>=150, mean>0, CI90 lower>0, mean>placebo p90,
at least two of three quarters positive, mean greater than baseline AND paired
improvement CI90 lower>0, and nonnegative delayed mean. Report3x stress without
retuning. Log family DSR and global DSR availability explicitly; historical
family has30 preregistered variants, plus these two arms. Full historical Sharpe
moment distribution/global registry are unavailable: do not invent a DSR value
or claim a DSR-based pass. DSR unavailability must remain an evidence limitation.

A pass earns **L3 shadow on real quotes only**, never promotion. A measured gate
failure rejects TimesFM and closes PR56 with the numbers. Missing/invalid inputs,
time budget expiry or infrastructure failure are NOT measured negative results.

## Compute and reproducibility

One absolute 86,400-second wall deadline for the entire run across all assets,
all fits and all resumes, with a hard process deadline. A restarted process
cannot reset it. Preserve hash-chained per-origin forecasts and immutable
fold predictions; completed work is never recalculated. Checkpoint-only git
commits may change while relevant source bytes remain identical. No new model
download. Existing CPU venv/weights stay unchanged; pinned supplementary
packages live in a separate overlay. Freeze full resolved dependencies in plan.
Register both arms before inference against the combined dataset manifest hash;
record final results and incomplete outcomes idempotently. The prior width
registry remains intact. No services, orders, mainnet or LaunchAgents involved.
