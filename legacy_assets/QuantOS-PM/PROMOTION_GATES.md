# Promotion gates — QuantOS-PM V0

## Current disposition

Offline mechanics and risk tests are executable; public capture has been observed.
**No strategy edge is demonstrated. No live or shadow-quoting promotion.**
Neither passing tests nor profitable synthetic cases can authorize real capital.

## Offline mechanics → eligible shadow quoting

- Keep the independent project, virtual environment, data root, public endpoint
  allowlist, no signing dependencies, and denied live flag.
- Run all unit/integration/randomized tests and reproduce all three balances in
  optimistic, baseline and pessimistic models from immutable captures.
- Independently validate current BTC five-minute condition/token mappings,
  Chainlink TWAP settlement rules, fee schedule, tick and minimum metadata.
- Resolve the observed legacy base-fee versus taker-only metadata discrepancy.
- Establish a verifiable live book synchronization/continuity method. Local
  receive counters are not exchange sequence evidence; current public L2 alone
  does not pass this gate.
- Validate aggressor, queue priority, latency, cancellation races, partial fills,
  disconnect/reconciliation and settlement assumptions against forward evidence.
- Replace synthetic calibration with past-only, provenance-linked observations
  in queue/size/price/time regimes; use held-out future days and whole-market/day
  block bootstrap. Reject unsupported regimes and nonpositive lower bounds.
- Reconcile every journal, inventory and collateral state. No unexplained fill
  or cancellation uncertainty can authorize new orders.

## Shadow evidence → future live review (not implemented)

- Pre-register an adequate independent-day sample and evaluation period before
  observing outcomes. Report skipped markets and capital tied up, not just fills.
- A pessimistic after-cost lower confidence bound must remain positive for each
  tested capital level, with liquidity, partial exposure, slippage, chain costs
  and cancellation delays. Incentives count only when independently credited.
- Pass stress tests for thin books, extreme prices, expiry, abrupt losses,
  connection gaps, failed cancels, clock drift and restart.
- Demonstrate risk caps under worst-case outstanding fills and daily/weekly/peak
  halts. Audit production fee schedules again immediately before any review.
- Full wallet attribution is required for any claim of exact replication; API
  cursor exhaustion alone is insufficient. Resolve transfers, fees, cost basis,
  redemption burns and Polygon receipts with unexplained differences disclosed.
- Early loser exits need conditional reversal estimates using outcome price,
  seconds to expiry, BTC reference, oracle/TWAP, volatility and price gap. An upper
  reversal bound must be below net executable exit price by a predeclared robust
  margin. A 90% or 95% win rate does not satisfy this gate.
- A separate reviewed live implementation and separate explicit owner approval
  are mandatory. No V0 flag can grant signing/submission capability.

These gates do not authorize testing against S199, its state, processes, ports,
credentials, accounts or capital. All further paper/shadow work stays isolated.
