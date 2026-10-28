# Archived EMA backtest source audit

This audit describes the archived `backtest_edge.py` configuration. It is not a
candidate rule and its results are not part of candidate selection or evidence
for the new 12/26 strategy.

## Archived run configuration

| Setting | Value |
|---|---|
| Bars | 2h, resampled from 1h source rows |
| EMA | 9/20 |
| Entry filter | `level` (does not enable `context_ok`) |
| Stop | 3% of entry price |
| Exit | Opposite EMA cross, stop, or end of the 60-bar path |
| Slots | 4 |
| Leverage | 1x |
| Starting equity | $200 |
| Fee in `optimize.outcome` | 0.05% per side; no slippage or funding in this archived scorer |

The archive calls `optimize.build`, which constructs the path for each signal
with 60 following bars. It instantiates `Params` with `near_atr=3`,
`retest_win=12`, `tol_atr=1.0`, `zone_body=True`, and `lookback=96`; other
`Params` defaults include 9/20 for this run, ATR(14), five-bar level pivots,
two prior touches, 1 ATR departure between pivot touches, 0.2 ATR stop buffer,
and 1.5R target. The archived `outcome` uses the fixed 3% stop and cross exit,
so the configured zone stop and 1.5R target are not used for this archived run.

The source README's reported 12/26 walk-forward growth of 1.12–1.27x is an
owner-supplied claim. The README itself was not attached, so its fold details and
correction are not independently verified. Earlier source familiarity also
means 2025–26 cannot be treated as clean OOS for this project.

## Timing/data audit

`optimize.resample` buckets by UTC epoch timestamps, which avoids grouping every
second row as a 2h bar. It does not require every bucket to contain two hourly
bars and does not reject missing-hour gaps, so partial 2h bars can enter the
path. The new candidate validates complete, contiguous, epoch-aligned 2h bars.
The archived fetcher uses Binance Spot klines, while the portfolio research
data path uses USD-M archives; that venue mismatch remains material.

## Callable source pieces for a later controlled reproduction

After a training-period configuration is frozen and supplied with immutable
1h rows, a separate source audit can call these functions at controlled scope:

- `optimize.resample(rows, 2)` for the archived grouping behavior, with an
  independent strict-coverage check around the input and output;
- `strategy.ema`, `strategy.atr`, `strategy.pivots`, `strategy.find_level`, and
  `strategy.signal` for the archived indicator and entry semantics;
- `optimize.build` only after replacing its implicit file read with a supplied
  array or a small wrapper around the immutable data; it precomputes signal
  paths and must not select parameters on OOS data;
- `optimize.outcome(row, 0.03, "cross")` for the archived exit calculation;
- `optimize.portfolio(trades, 4, 1)` for the archived four-slot, 1x accounting;
- `optimize.score_combo` only if its train/test split and ranking are separately
  audited before any test result is revealed.

No archived script, optimizer, or OOS evaluation was executed for this source
review. In particular, do not run the top-level `backtest_edge.py` or
`optimize.py` while completing candidate selection.

## Controlled wrapper status

`research/archived_ema.py` now parses the attached source files with Python AST
and compiles only the reviewed pure strategy definitions plus the isolated
`outcome`/`portfolio` scorer definitions. It verifies the source-level CFG,
level parameter overrides, fee, warmup, and 60-bar path, and records hashes for
`strategy.py`, `optimize.py`, and `backtest_edge.py`. It never imports either
archived module. Candidate construction uses current USD-M filter metadata and
strict UTC epoch-aligned complete 2h bars; the decision window is 2022-01-01
through 2024-12-31. The earliest available 2021 training bars seed the source
EMA recurrence. This venue and timestamp adaptation is a controlled audit, not
an exact reproduction of the archived Spot run. It uses only the optimizer's
literal nine-symbol `COINS` universe. Candidate timestamps are the completed
2h close boundary, while the archived scorer orders trades by source candle
open time; this source clock convention is retained for the score path.

The wrapper and its tests do not load historical bars or score outcomes.
`score_archived_candidates` is a separately callable, source-extracted scorer;
it requires main-agent review before any controlled audit results are computed.
