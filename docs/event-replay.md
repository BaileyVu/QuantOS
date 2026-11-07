# Runtime event replay

`quantos event-replay` consumes schema-2 `Runtime.record` files and drives the
same `runtime.dispatch` and `PaperEngine` paths used by the paper service. The
recorded scan snapshots carry closed bars and announcement observations; quote
events carry mark price and top-of-book quantities; feed events carry trades
for LIMIT fills. The engine remains the only fill/cost implementation, so its
existing 500 ms minimum latency, strict trade-through, top-book depth checks,
and depth-impact pricing apply to LIMIT, STOP, and MARKET orders.

Example (against copies of the desired recorded files):

```sh
quantos event-replay --profile pD \
  --input /path/to/market/2026-11-01.jsonl.gz /path/to/market/2026-11-02.jsonl.gz \
  --state /tmp/quantos-replay \
  --expected-journal /path/to/copied/journal_pD.db
```

The natural journal is opened read-only. The result records exact equality of
scan, signal, risk, funding, order-state, fill/protection, and stop-update events
(operational health/heartbeat rows are excluded), event and scan counts, and
whether all scans carry captured sentiment context. New runtime scan records now include the exact sentiment
verdicts and fail-closed setting used by dispatch. Older recordings without that
context can still be explored, but the result marks sentiment incomplete and
will not claim pD journal parity. Replay output is evidence of deterministic
execution, not independent strategy validation.

## Evidence still needed

No pD period recording/journal pair was supplied for this change, so pD parity
remains unverified. For a valid run, use the original schema-2 market capture
and a read-only copy of its matching `journal_pD.db`; both must cover the same
run and the capture must include sentiment context. Do not select on 2025+
data.

Turtle and volatility-squeeze L2 results were not produced here. They require
versioned 1h/4h USDC candles, quote/depth/mark observations, funding and
announcement inputs, and a dataset manifest whose BUILD range is 2020-01 to
2023-06 and HELD range is 2023-07 to 2024-12. Report BUILD against the RISK_LAYER
gate (mean > 0, CI90 lower bound > 0, above placebo/random p90, stress >= 0,
and at least 150 trades or 30 episodes), then evaluate HELD once per family.
Replay does not synthesize these inputs or assert an L2 pass.

`configs/strategies/*.toml` remains a backtest-only locked manifest check, not
the runtime strategy source. Replacing that with configuration-driven strategy
construction is deferred: the files include USDT research variants while the
live library wraps selected rules as USDC-mapped versions with different IDs'
distinct version contracts. Deleting the files would remove the existing
`backtest --config` interface; a safe source-of-truth change needs a separate
versioned-config design decision.
