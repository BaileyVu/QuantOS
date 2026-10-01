# QuantOS HFT V1 Implementation Guide

Version: 3.0.0-V1
Status: Authorized guidance

Implementation order:

1. define HFT event, L2 book, feature, quote, queue, fill, inventory, and metric
   contracts;
2. implement exact REST-snapshot plus buffered-delta reconstruction and
   fail-closed recovery;
3. implement VAMP, OBI, microprice, trade flow, volatility, and latency;
4. implement the explicit fee/cost hurdle and bounded HFT Risk policy;
5. implement post-only paper orders, conservative queue fills, partial fills,
   maker-first exits, safety exits, markouts, and accounting;
6. implement separate durable state and compressed replayable recording;
7. expose `quantos futures hft-paper` and `hft-paper-reset`;
8. add deterministic event replay/export before any authenticated testnet work.

Binance network clients remain under infrastructure, orchestration under
application, configuration under infrastructure/configuration, commands under
interfaces, and business rules under their existing domain boundaries. HFT
packages are authorized; avoid speculative services or distributed systems.

The initial runtime supports one symbol, one WebSocket connection set, one
working directional entry, one inventory, and simple causal Alpha. It prints
symbol plus maker/taker fees prominently at startup. BTCUSDC is preferred only
after live exchange-info eligibility; BTCUSDT is the explicit fallback.

The recorder writes depth snapshots/deltas, bookTicker, aggregate trades,
mark/funding, receive/exchange timestamps, and reconstructed checkpoints under
a separate ignored HFT data root. State defaults to
`artifacts/futures-paper/hft-btc-paper.db`. Restart cancels stale quotes and
waits for a fresh book.

Focused tests cover bootstrap, sequencing/gaps/duplicates, crossed books, VAMP,
OBI/trade flow, per-contract costs, post-only and reversal cancellation,
queue-ahead/no-touch-fill/partials, maker/taker accounting, markouts, inventory,
1% risk, daily/adverse-fill breakers, stale-data kill switch, restart, and the
absence of real submission.

The retained candle commands and configuration remain available for historical
comparison. HFT code must not modify their strategy behavior or state.

Large HFT experiments are run locally by the user. Never commit credentials,
raw event data, databases, generated metrics, or machine-specific artifacts.
