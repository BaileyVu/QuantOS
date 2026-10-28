# Portfolio candidate source notes

Date checked: 2026-10-28

## Original strategy source

The request identified these source paths in `claude-quantos`:

- `edges/ema-level-2h/strategy.py`
- `edges/ema-level-2h/optimize.py`
- `edges/ema-level-2h/backtest_edge.py`
- `shootout/simple_baseline.py`
- `shootout/wf.py`
- `shootout/features2.py`

They were not present in the targeted local Desktop/PythonProject search or
accessible GitHub search. The user later attached six named files at the Desktop
root; these were read as text only and were not imported or executed:

| File | SHA-256 |
|---|---|
| `strategy.py` | `6049ef13b93495de8612b45d8fc53b11e2ce56a903032fdb12392b3aea9f1fd5` |
| `optimize.py` | `f8c0c45a08bab58f780adb9de1692cc87b52e744bc4216c693d19565561c5e7b` |
| `backtest_edge.py` | `10926768c88cd3a0cf371d87a4aa219e6b7d15c0b93491d8d865ff4af744ae1f` |
| `simple_baseline.py` | `ddc132b942e11fc5de4b65d5bfaf6692a23e1f74d39bf8f8fa22ac289512988a` |
| `features2.py` | `e2edc5e3cc2281e0fedc2aff3071168ef562f9bdea36cbc5c76271f4a27f5128` |
| `wf.py` | `a11a44bb621ed1fa75053dce75bc6c57b8363e92430bde9054dba20fe9243ec7` |

No source files for `crash_flush_e2` were attached or found; its rule remains
based on the owner-supplied candidate description and adaptation approval.

### EMA source rule and parameter conflicts

`strategy.py` describes a close-of-cross-candle 9/20 EMA strategy. Its default
parameters are ATR EMA length 14; pivot half-width 5; major-pivot half-width 12;
level lookback 288 bars; pivot clustering tolerance 0.5 ATR; two previous pivot
touches; 1 ATR leave distance between touches; 12-bar retest window; maximum
close distance from level 1 ATR; stop buffer 0.2 ATR; 1.5R target; 288-bar
maximum hold; and two enabled filters. The enabled trend/Fibonacci filter
requires HH/HL for longs or LH/LL for shorts and entry on the discount/premium
side of the last major swing leg. It includes both same-side S/R levels and
opposite-side flip zones (two rejections, a close through, then a held retest).
Pivots are used only after `pivot_index + half_width <= current_index`.

`optimize.py` searches 1h/2h/4h/6h/8h/12h bars; EMA pairs include 9/20 and 12/26.
Its level search overrides the strategy defaults with 1 ATR clustering
tolerance, 3 ATR near-level allowance, wick/body zones enabled, 96-bar level
lookback, 12-bar retest window, and otherwise retains pivot widths 5/12, two
touches, 1 ATR leave spacing, 0.2 ATR stop buffer, and 1.5R target. The archived
`backtest_edge.py` configuration is 2h, 9/20, level filter, 3% stop, opposite
cross exit, four slots, and 1x leverage. It is distinct from the requested
12/26, ATR-zone-stop, 1.5R candidate.

The user-approved adaptation currently specifies 2h 12/26 crosses,
confirmed swing-pivot levels, 0.25 ATR touch tolerance, two prior distinct
touches, 0.25 ATR stop buffer, and 1.5R target. These approved parameters
conflict with source defaults and with the optimizer's overrides. Whether to
keep flip zones and the major-swing/Fibonacci context filter is not specified by
the adaptation and must be resolved before finalizing the candidate rule.

### ALT source baseline

`simple_baseline.py` pools cross-sectional coin-hour 24h returns and uses a
calibration interval `[quarter_start - 90 days, quarter_start - 3 days)`. It
tests bottom-quantile cutoffs of 0.5% and 1%, with 12h, 24h, and 72h holding
horizons, and blocks a coin from another entry until its prior horizon ends.
The candidate module uses that original three-day embargo endpoint as a
source-faithful provisional preregistration; the adaptation's “90 days before
each quarter” could instead mean ending calibration at quarter start. The exact
cutoff boundary still needs final preregistration. The `fetch1h.UNIVERSE` list
was not among the attached files, so the exact 29-symbol cohort is unresolved.
The file calculates one comparator from each test
quarter's realized per-coin mean forward return, which is not a valid
pre-decision random-entry baseline and is not adopted as evaluation evidence.

### Feature/evaluation notes

`features2.py` builds 1h price and derivatives features for a 29-coin universe;
its OI z-scores use rolling full-series helpers, not the approved prior-only
720-change z-score primitive. Its grid uses BTC timestamps and forward-fills
missing OHLC, so those data-handling rules are not copied.

`wf.py` runs quarterly folds, but the supplied script and prior 2025–26 exposure
mean its reported 2025–26 figures are not clean out-of-sample evidence for this
task. This source review does not reproduce any result. No source code in these
files defines `crash_flush_e2`.

## Evidence status

The prior performance figures in the research prompt are owner-supplied claims,
not independently verified results. The user disclosed that earlier source code
was authored/seen in 2025–26; any purported 2025–26 out-of-sample interval is
therefore partially contaminated by prior exposure. No original backtests,
historical evaluation, or OOS evaluation were run as part of this implementation.
These candidates remain research-only until a frozen, preregistered evaluation
has been run on data not used to develop or inspect them.

## Approved adaptation parameters

### `crash_flush_e2`

- Universe: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK.
- Input cadence: closed 1h bars, with UTC epoch-aligned 4h features.
- Entry: long on the 1h close when the 4h OI-value change z-score is below -2,
  price has fallen over 4h, and the close is at least 8% below the trailing 24h
  high.
- OI change primitive currently uses fractional change
  `(OI[t] / OI[t-4h]) - 1`; the z-score uses the preceding 720 hourly changes,
  excluding the current change. First availability is after 724 hours of
  observations. This denominator convention is provisional pending source-data
  documentation.
- Primary exit: 24h time exit. A 25% catastrophe stop is approved; the 15% stop
  is a sensitivity run only.
- Risk sizing sweep is controlled by the portfolio harness: 1%, 2%, 4%, 6%, 8%.

### `alt_dip_reversal`

- Calibration: pool cross-sectional coin-hour 24h returns over the 90 days
  (2160 hours) preceding each calendar quarter; freeze the selected quantile
  threshold for that entire quarter. Do not use a rolling 720h cutoff.
- Candidate cutoffs: bottom 0.5% and bottom 1% (separate preregistered variants).
- Holding-period variants: 12h, 24h, and 72h.
- A 25% catastrophe stop is approved. The 15% stop is a sensitivity run only.
- Exact coin-universe membership and tie/threshold-boundary handling remain to
  be recorded before evaluation.

### `ema_level_2h`

- Closed 2h candles aligned to UTC exchange epoch boundaries.
- EMA periods: 12 and 26.
- Levels are based on confirmed 2h swing pivots. Touch tolerance is 0.25 ATR;
  stop buffer is 0.25 ATR beyond the zone; target is 1.5R.
- Entry requires the EMA cross at a level with two prior distinct touches, so
  the signal is on the third distinct touch.
- Pivot left/right widths, ATR lookback and smoothing, zone clustering, and
  touch-separation semantics still require explicit preregistration. No implicit
  defaults should be used for these rules.

## Feature helper conventions

`quantos.paper.strategies.candidate_features` provides causal primitives only:
hourly-to-2h/4h aggregation by UTC epoch bucket with an explicit `as_of_ms`,
24h close-to-close fractional returns, prior-only rolling quantiles using linear
interpolation, prior-only OI-change z-scores, and swing pivots carrying both the
pivot time and the time the pivot became confirmed. It does not specify entry
rules, universe selection, calibration policy, stop behavior, or zone semantics.
