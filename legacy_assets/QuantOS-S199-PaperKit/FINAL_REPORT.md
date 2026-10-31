# Final S199 Repair and Alpha Optimization

## Outcome

Engineering is **GO for paper-only forward observation**. Strategy evidence is **NO EDGE**.

The unsupported Nautilus live sandbox path was replaced by one standalone loop using public Gamma discovery, CLOB snapshots and WebSocket updates, and Binance trade/depth streams. The integrated path performs model inference, fee-aware two-share book walking, virtual fills, restart-safe accounting, official TWAP settlement, risk checks, diagnostics, and clean shutdown. It contains no wallet, signing, or live-order capability.

Twenty-four deterministic tests pass. A 30-second live public-data smoke discovered three markets, validated six CLOB books, connected both public WebSockets, made no fill, and shut down cleanly.

## Frozen candidate

The one-time predeclared grid used 73 chronological train, 24 validation, and 25 untouched test observations. It compared raw, shrinkage, and train-only Platt probabilities; four fee-adjusted thresholds; two ask caps; two pair-cost filters; and three momentum rules. Validation selection included a multiple-testing penalty.

The frozen candidate uses raw S199 probability, 2 shares, 60 seconds to expiry, 2% minimum net edge after the token fee, ask 0.10–0.75, minimum selected probability 0.65, pair ask cost at most 1.01, and no momentum filter.

The untouched test had zero qualifying trades: net PnL 0, drawdown 0, and Brier 0.100673 versus market-implied Brier 0.105661. With no test trades there is no PnL confidence interval and no credible profitability claim. The frozen original also made zero test trades. The correct conclusion is **NO EDGE**.
