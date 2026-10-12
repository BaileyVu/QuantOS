# QuantOS HFT V1 Data Architecture

Version: 3.0.0-V1
Status: Authorized

Canonical HFT inputs are BTCUSDC or BTCUSDT USDⓈ-M depth snapshots, diff-depth
deltas, bookTicker events, aggregate trades, and mark-price/funding events.
Each record contains provider event/update identity, exchange timestamp, local
receive timestamp, and enough raw fields to reproduce normalization. Internal
timestamps are timezone-aware UTC. Decimal strings are parsed directly to
Decimal in financial paths.

The book bootstrap algorithm buffers WebSocket deltas while fetching a REST
snapshot, drops updates whose final update ID is not newer than the snapshot,
requires the first retained update to bridge the snapshot ID, then accepts only
updates whose previous/final IDs are contiguous under Binance USDⓈ-M rules.
Duplicates already fully applied are ignored. Gaps, invalid levels, crossed
books, reconnect ambiguity, or stale events invalidate the book and require a
fresh snapshot.

Exchange metadata determines contract type, trading status, tick, quantity
step/min/max, and minimum notional. BTCUSDC is eligible only when metadata
confirms a trading perpetual contract and an exchange-valid minimum order.
BTCUSDT is the configured fallback. Rules and fee configuration are captured in
session identity.

Raw events and periodic reconstructed-book checkpoints are written under a
separate HFT data root in compressed Parquet partitions suitable for replay.
Published partitions are immutable/versioned; DuckDB may query them. State
recovery never treats a recorder file as permission to quote.

Replay and live paper reuse the same normalization, book, features, Alpha,
Risk, queue, accounting, and metrics logic. Feed and order latency are explicit
inputs. hftbacktest export maps event timestamps, local timestamps, side,
price, quantity, and update/trade identity, with semantic differences
documented.

Completed one-minute candle datasets remain valid only for the retained candle
comparison runtime; they are not canonical inputs to HFT Alpha.
