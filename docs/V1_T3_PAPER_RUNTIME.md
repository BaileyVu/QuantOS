# V1-T3 continuous paper runtime

The application consumes the existing public Binance Spot live adapter. Completed
1-minute candles pass through a bounded minute barrier, existing Feature Engine,
caller-supplied AlphaEvaluation, RiskEngine, TradingStep, ExecutionEngine with
PaperFillProvider, and the existing Execution ledger. Execution alone owns money,
positions, orders and fills. There are no real orders or authenticated endpoints.

For both symbols, a complete same-minute bundle is required before decisions.
BTCUSDT then ETHUSDT is always the decision order; ETH sees BTC's resulting account.
The configured synchronization deadline does not reset on duplicate arrivals.
Its monotonic clock is injectable for deterministic tests and defaults to the
event-loop monotonic clock, independently of the UTC freshness clock.
Missing partners, conflicts, backward time and gaps stop the runtime. A single
symbol needs no partner. Exact last-committed duplicates are ignored on restart.

The Feature Engine's 21-candle causal window is retained per symbol. Warm-up calls
no Alpha. Insufficient feature inputs continue to produce no decision. The injected
UTC clock checks actual observation freshness against Risk's stale_seconds before
Alpha and again before Risk/Execution. Candle close time, provider MarketEvent time,
and local observation time remain distinct. Required integer runtime configuration
`provider_clock_skew_tolerance_ms = 1000` permits both provider event time and
provider-defined completed-candle close time up to that many milliseconds ahead of
local observation (inclusive); allowed settings are 0-1000 ms. With
`age = local_now - candle.close_time`, `_fresh()` accepts only
`-tolerance <= age <= Risk.stale_seconds`. The stale upper bound is not extended.
Exceeding either provider-time tolerance fails closed. Incomplete Binance klines
remain suppressed: the adapter still requires x=true and canonical minute boundaries.

This validation tolerance introduces no sleep, buffering or intentional execution
delay: completed events are processed immediately on arrival. Provider time never
advances local last_observation or the monotonic clock, and never changes system
time. Backward local clocks still fail closed. No server-time polling or NTP is used.
RiskContext continues to use candle close time; Risk safety checks are unchanged.
The tolerance is part of runtime identity, so changing it blocks restart. The old
`event_clock_skew_tolerance_ms` key is rejected, without an alias; configurations
must use the new required key, and prior checkpoint identities are incompatible.

## Composition and operation

Run from the repository with the package installed:
`python -m quantos paper --runtime-config configs/paper_runtime.toml --non-trading-smoke`.
This explicitly labeled HOLD-only mode tests feed operation; it is not a production
strategy or lifecycle approval. Without this flag CLI fails safely because no final
production Alpha is selected. The former default paper no-op now also fails safely.

Final Alpha plugs into `quantos.interfaces.paper_runtime.run_paper` as a callable
FeatureVector -> AlphaEvaluation, with an explicit stable alpha_implementation_id.
It supplies expected gross edge and optional volatility, never inferred from score.
Alpha must reproduce its decision from the feature and stable implementation; private
mutable strategy state is outside this recovery contract. Callbacks are synchronous;
they should be bounded and must not perform blocking I/O.

Strict runtime TOML references the existing paper/risk TOML. The paper_config path
is relative to runtime TOML; persistence paths are relative to the invoking working
directory, matching existing paper configuration. All artifact paths are external
configuration; use a dedicated location per run. Live mode is rejected. Costs,
Decimal sizing and quote-currency fee accounting are unchanged from V1-T1.

## Persistence and recovery

A canonical ASCII JSON checkpoint with checksum holds bounded candles, last minute,
day-start/peak equity, Alpha versions, configuration identity, evaluation fee basis
and totals, and Execution/evidence identities. Session locks protect both the
checkpoint and Execution ledger, including callers using a different checkpoint
path for the same ledger. The ledger session companion is .runtime.lock; it is
distinct from Execution's existing per-append lock. Save writes a flushed/fsynced temporary file, then atomic
replacement (Windows write-through; POSIX directory fsync).

Append-only canonical JSONL minute evidence uses sequence and previous-record hashes.
It records EquityPoint, Alpha/Risk/results, fees, slippage, completed trades and
running totals. Existing V1-T2 marking and trade accounting are reused; persisted
points/trades provide inputs for later metric calculation. Structured logs include
start, warm-up, recovery, minute, failure, cancellation and stop events.
Recovery sums recorded execution costs and completed-trade PnLs, checks cumulative
totals, and compares the final observation and fee basis with the checkpoint.
Current equity is independently checked against the reconstructed Execution account.

Each minute first saves a processing marker, then uses the Execution ledger,
appends minute evidence and finally saves a ready checkpoint. These files are NOT
a cross-file transaction. An unfinished marker, damaged file, missing/truncated
evidence, changed identity, stale lock/temp file, or Execution mismatch blocks
restart. Never delete/rewind state and blindly retry: preserve all files and inspect
the authoritative execution evidence before an explicit operator recovery decision.
This phase intentionally supplies detection, not an automatic ambiguity repair tool.
For stale session locks, first establish that no owning process is active, preserve
the checkpoint/ledger/evidence together, and inspect their consistency. Only an
explicit operator action may remove a proven stale lock. Removing a lock does not
make a processing/blocked checkpoint safe or bypass reconciliation.

A clean restart replays/reconciles the Execution ledger, checks its identity, restores
the bounded history and causal equity references, and accepts only the next minute
or exact last-minute duplicates. UTC day-start equity resets on the first processed
candle close timestamp in a new day. Peak includes marked open positions.

Request StopSignal.request(reason) on the runtime event loop to stop; cross-thread
callers must marshal through loop.call_soon_threadsafe. Stop prevents further
Alpha/Risk/Execution, closes the feed, and never liquidates. Ctrl+C/task cancellation
propagates through cleanup. Partial unprocessed bundles are discarded; persisted
last-minute continuity still applies on restart. A restart after missed minutes
therefore fails closed; no backfill or forward-fill is supplied here.

No public network smoke test is required for acceptance. Offline connector tests
exercise the real adapter and composition. Actual trading operation remains blocked
on selecting and explicitly composing an Alpha under the existing promotion
lifecycle. No credentials, live execution, dashboard or new production module exists.
