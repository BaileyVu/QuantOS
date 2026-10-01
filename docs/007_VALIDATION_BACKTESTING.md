# QuantOS HFT V1 Validation and Backtesting

Version: 3.0.0-V1
Status: Authorized

HFT replay consumes sequenced L2, bookTicker, trade, mark/funding, and latency
events and reuses the live-paper book, Feature, Alpha, Risk, queue, accounting,
and Evaluation logic. Candle replay cannot validate HFT profitability.

Acceptance requires valid bootstrap/reconstruction, zero tolerated sequence-gap
corruption, conservative queue-aware fills, post-only entry, actual configured
fees, bounded inventory, durable state, live latency measurements, and adverse
markout diagnostics. Zero economically valid quotes is an honest result and
must not cause threshold weakening.

Minimum metrics are quotes submitted/cancelled, maker fills, taker exits, fill
rate, trades and round trips per hour, gross spread capture, directional Alpha
PnL, gross/net PnL, maker/taker fees, funding, net PnL per fill, net bps per
round trip, inventory turnover, average holding seconds, queue wait, latency
median/p90/p99, maximum drawdown, kill-switch events, and 1/5/10/30/60-second
markouts.

Markouts are reported by Alpha bucket, OBI bucket, and volatility regime with
average, median, and positive/negative proportions. Sharpe uses an explicitly
identified appropriate time aggregation. Queue diagnostics include quantity
ahead at placement, partial quantity, fill time/probability diagnostics, and
cancelled-before-fill count.

hftbacktest-compatible export is authorized for independent queue/latency
research. QuantOS must document differences in timestamp, queue, fee, and
inventory semantics and never substitute institutional rebate or sizing
assumptions.

Research → deterministic HFT replay → walk-forward/robustness validation →
live-market paper → authenticated testnet → mainnet shadow → explicit live
approval. No test, paper result, or external notebook enables real orders.
