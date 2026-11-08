# patml_v1 historical source reference

These two files are a hash-pinned, immutable-by-convention copy of the local
historical `edge_data/patml_v1` source, captured for review:

- `run.py` SHA256: `5a6470bfb28848ea31e21e7a821a7b69db410f63acf332262b2e7245253f6577`
- `PREREG.txt` SHA256: `cf9601a9ac2ff3c96d3bd7f85e0c8c089f7a5b536dd58c470e1f5c441514f843`

This is reference material only. Do not import or execute `run.py`: its
module-level code immediately loads local caches or globs the protected
`edge_data/ml5m` raw tree, builds labels over the full series before selecting
evaluation quarters, and writes generated caches/results. Do not place this
reference on a runtime import path. No data, model, cache, or result artifacts
are included here.

## Source behavior to preserve or explicitly replace

The current feature builder emits 32 ordered features: 15 normalized fields
(body, upper/lower wick, range, close location for the current and prior two
bars); engulfing; close versus prior high and low; up-closes over five bars;
log returns over 3/12/48 bars; daily VWAP and daily open distances; EMA 20/50/200
distances; volume and range z-scores over 48 bars; hour sine/cosine; weekday.
ATR14 is a rolling mean. The code uses only data through the scored bar, but
`np.roll` wraps at the start of each symbol series; the first 200 rows are then
blanked. Reimplementation should freeze and test the exact feature order and
causal alignment rather than importing this module.

The target uses a maker close-limit entry filled when the next bar crosses it,
1.5 ATR target/stop, TP checks starting at t+2, stop checks starting t+1, and a
48-bar time exit. The current costs are incomplete for current QuantOS policy:
4 bp plus symbol slippage is deducted on stop and time exit, while TP has no
explicit fee and entry fee is absent. Do not reuse these labels as a fully
costed result without a new preregistered cost model.

Quarterly expanding folds train before the next test-quarter start with a 48-bar
purge. Score thresholds are calibrated from predictions on the final 200,000
rows of the training index; because rows are concatenated by symbol, this tail
is symbol-order biased and is in-sample. Replace it with a declared chronological
OOF calibration scheme. Keep every 48-bar outcome within BUILD: for a BUILD
cutoff at `2023-07-01T00:00:00Z`, entry timestamps must be at least 48 bars
before the cutoff so label exits cannot reach HELD.

The historical loader is USDT-M proxy data. It does not establish USDC listing
availability or USDC execution costs. A safe BUILD implementation must instead
consume the checksummed, date-filtered archive index and record this proxy
limitation; it must not read HELD or sealed 2025+ data.

## Vendored scope

Only `run.py` and `PREREG.txt` are copied. There is no lock or environment file
in the source directory; the exact original versions of NumPy, pandas,
scikit-learn, and LightGBM are therefore not pinned by this reference. Current
repo research extras are not proof of the original training environment.
