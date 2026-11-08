# Fixed BUILD OHLCV input for TimesFM ml1h

`quantos.paper.research.ml1h_dataset` creates a separate, checksum-bound 1h
OHLCV feature input for the preregistered TimesFM drift experiment. The earlier
2022 three-asset volatility export is not reusable for this purpose: it has no
open or volume fields, begins after the required 512-hour warm-up, and covers
only BTC/ETH/SOL. This exporter instead uses the fixed 16-symbol C21 corpus
intersection and the BUILD selection from 2021-10-01 through, but excluding,
2023-07-01.

Rows preserve both the source `open_ms` and the completed-candle boundary
`time_ms = close_exclusive_ms`. Therefore the first output row is the candle
opened at 2021-10-01 00:00 UTC and completed at 01:00 UTC. The exporter requires
exactly 15,311 contiguous rows per symbol for all 16 fixed symbols; missing
coverage fails the export instead of dropping a symbol. It uses the previously
declared base and overlay manifest SHA-256 values and calls `build_index` once
with the strict BUILD bounds. It is a historical USDT proxy feature dataset,
not USDC-M execution evidence or a promotion result.

The CLI requires explicit `--manifest`, `--overlay`, and a new `--output-dir`.
It has not been run against the local archive corpus as part of this source-only
change.
