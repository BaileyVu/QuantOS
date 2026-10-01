# QuantOS HFT V1 Product Requirements

Version: 3.0.0-V1
Status: Authorized

QuantOS provides a local paper-only workflow for BTCUSDC or BTCUSDT USDⓈ-M
perpetual directional maker trading:

public exchange events → validated L2 book → microstructure features → VAMP
fair value → fee-aware Alpha intent → Risk decision → queue-aware simulated
Execution → inventory/markout evaluation.

The HFT runtime must consume fastest-practical supported diff-depth plus
bookTicker, aggregate trades, and mark-price/funding streams. It bootstraps from
a REST depth snapshot, buffers deltas, applies Binance update-ID rules exactly,
and resynchronizes after every ambiguous or invalid state.

VAMP is the primary fair-value estimator. Mid price, microprice, static and
standardized OBI, signed trade flow, short realized volatility, spread,
inventory, event age, and latency are required features or diagnostics. No
one-minute indicator generates HFT Alpha.

Each possible quote exposes predicted displacement and every applicable cost in
basis points. A quote is allowed only when expected Alpha strictly clears
round-trip costs plus adverse-selection allowance and safety buffer. Entry is
post-only. Normal exits are maker-first; taker exits are reserved for explicit
safety conditions.

Starting paper equity is 100 units of quote currency, maximum leverage is 5x,
and normal inventory risk is capped at 1% of equity. HFT V1 permits one symbol,
one directional inventory, and the smallest valid initial quantity, subject to
configured notional/quantity ceilings and account breakers.

Paper fills are queue-aware and conservative. State, fills, fees, funding,
inventory, PnL, latencies, quote lifecycle, markouts, and breakers are durable
and auditable. Live session events are recorded as replayable research data.
Real order submission is not implemented or reachable.

The retained candle Futures commands remain available only for comparison.
