# V3 public market adapter

`quantos.paper.market.PublicMarket` makes unauthenticated GET requests to a
fixed `https://fapi.binance.com` host and a closed allowlist of market data
paths. It does not accept URLs, keys, signatures, or write methods. Injected
transports support deterministic tests; callers must still treat data errors as
unavailable market state.

Supported calls return `Instrument`, `Candle`, `Quote`, `Trade`, and `Funding`
contracts defined in `quantos.paper.types`. Exchange metadata is limited to
USDC margined USDC perpetuals and skips any candidate lacking a required
quantity, price, notional, market quantity, or percent-price filter. Candles
accept only 1m and 5m intervals, discard open rows, and reject gaps, duplicates,
and stale tails. Quotes combine public bookTicker bid/ask values with the
premiumIndex mark and server timestamp; receipt time is recorded locally.
Aggregate trade pages preserve their returned events and reject malformed or
unordered data; no missing trade IDs are synthesized. Funding history paginates
actual funding records within the caller's inclusive time range.

Requests use a five-second timeout and at most four attempts for transient
server/network failures. HTTP 418/429 immediately raises `RateLimited` carrying
the full Retry-After delay (at least 60 seconds when absent or shorter), without
blocking the caller; the runtime must honor this before requesting again. 5xx
and transient URL/timeout failures use capped exponential delay. Consumers
should obtain `server_time()` and pass a fresh `now_ms` when requesting candles.

Source references: Binance official USDⓈ-M Futures REST API, [Market Data
endpoints](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data),
including Exchange Information, Check Server Time, Kline/Candlestick Data,
Symbol Order Book Ticker, Mark Price, Aggregate Trades, and Get Funding Rate
History.
