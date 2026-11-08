# Historical ML1h feature port

`quantos.paper.research.ml1h_features.extract_features` is a pure, stdlib-only
port of the 32 ordered candle features in the hash-pinned reference
`research/reference/patml_v1/run.py`
(`5a6470bfb28848ea31e21e7a821a7b69db410f63acf332262b2e7245253f6577`). It reads only the
timestamp and OHLCV arrays supplied by its caller. It does not import the
reference, read files, construct labels, train, or calculate trading costs.

Feature columns, in order: `b0,uw0,lw0,rg0,cl0` through lag 2; `engulf`,
`c_vs_h1`, `c_vs_l1`, `ups5`, `r3`, `r12`, `r48`, `d_vwap`, `d_dopen`,
`d_ema20`, `d_ema50`, `d_ema200`, `vz`, `rz`, `hs`, `hc`, `dow`. ATR is the
14-bar simple mean of high-low. The 48-observation z-score uses sample standard
deviation, matching Pandas rolling `std()` defaults.

The reference blanks the first 200 rows per symbol after constructing features;
this port does the same. Undefined values are `NaN`, including the warm-up and
zero-variance z-scores. Bars must be ordered, valid OHLCV observations. Gaps
remain missing; windows count observed rows like the reference, so the caller
must separately decide how gaps affect its study.

Timestamp boundary policy belongs to the caller: provide only completed 1-hour
bars. For the current BUILD end boundary `2023-07-01T00:00:00Z`, include a bar
only when its `close_exclusive_ms` is strictly earlier than the boundary; the
bar opened at `2023-06-30T23:00:00Z` closes exactly at the boundary and must be
excluded. This module does not apply a date filter or create future labels.

The original script's 48-bar targets, fee/slippage assumptions, threshold
calibration, and walk-forward logic are intentionally outside this feature
module. Do not use the historical loader or its outcomes as the new augmentation
runner; it accesses protected raw paths and calculates labels before period
selection. Main must preregister and implement the BUILD-only walk-forward and
unchanged L2 cost evaluation separately.
