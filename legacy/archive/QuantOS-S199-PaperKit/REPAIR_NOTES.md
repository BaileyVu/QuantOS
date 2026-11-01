# S199-V2 Repair Notes

- Preserved pinned vendor SHA, S199 weights/profile, archived session, and V1 files.
- Reproduces the authoritative 231 evaluation records / 222 markets and final-decision counts before any V2 work.
- Replaces flat-notional fee accounting in the V2 authoritative ledger with `shares * rate * price * (1-price)`, Decimal arithmetic, USDC units, and 5-decimal half-up rounding. Fee metadata is fail-closed unless verified.
- Sizes market buys in base shares. A 2-share target at 0.73 costs 1.46 USDC before fee; it is never submitted as a 1.46 quote-quantity order. Venue minimums, increments, visible depth, cash, and partial results are explicit.
- Adds a deterministic book-health state machine requiring an initial snapshot after every connect, contiguous sequences, fresh uncrossed nonempty books, and both outcome legs. Reconnect delay is exponentially bounded at 30 seconds.
- Adds a transactional SQLite WAL ledger with unique event/fill/settlement IDs, restart identity checks, deduplication, virtual cash, unresolved risk, payouts, realized P&L, and a $6 aggregate open-cost guard.
- Adds atomic JSON checkpoints and SIGINT/SIGTERM handling primitives.
- Adds a public Gamma acquisition and scoring pipeline. Labels without exact closed outcome prices and contemporaneous Chainlink/Data Streams/TWAP methodology remain UNKNOWN. Unknown labels are excluded.
- The pinned Nautilus sandbox client has no injectable live-sandbox fee model, and the upstream WebSocket client does not expose sequence/re-subscribe hooks through its public configuration. Those components are tested in isolation but not yet wired into the running node. Phase 3 therefore remains NO-GO.
