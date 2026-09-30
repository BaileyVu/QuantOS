# QuantOS V1 Implementation Guide

Version: 2.0.0-V1
Status: Authorized guidance

Implementation order:

1. Parse Binance USDⓈ-M exchange rules and completed BTCUSDT candles.
2. Implement causal regime and a small strategy library.
3. Select deterministic LONG/SHORT/HOLD candidates.
4. Implement Futures Risk sizing, leverage, breakers, and liquidation buffer.
5. Implement deterministic paper execution and position management.
6. Expose accelerated replay and live-market paper commands.
7. Add authenticated testnet and mainnet shadow reconciliation.
8. Add live capability only after lifecycle evidence and explicit approval.

Keep exactly six business modules. Binance clients live under infrastructure, orchestration under application, configuration under infrastructure/configuration, and commands under interfaces. Reuse canonical Candle/storage where semantics match.

Commands emit machine-readable JSON: `quantos futures exchange-info`, `download`, `replay`, `paper`, and eventually `shadow`. Paper is safe default; shadow cannot submit; live requires explicit approval.

Focus tests on filters/rounding/notional, LONG/SHORT/HOLD and conflicts, Risk rejection, leverage caps, stop/liquidation buffer, one-position and loss breakers, idempotency, paper fills, and reconciliation. Run large replay experiments locally and never commit credentials, raw data, databases, or generated reports.

The Futures configuration selects any supported subset of 1m, 3m, 5m, 15m, 30m, and 1h signal intervals. It also owns the consecutive-loss threshold, cooldown minutes, and optional next-UTC-day reset. Replay and live paper must use the same aggregation, selection, breaker, and Risk implementations.
