# QuantOS V1 Data Architecture

Version: 2.0.0-V1
Status: Authorized

The canonical initial dataset is BTCUSDT USDⓈ-M Futures completed one-minute OHLCV. Timestamps are timezone-aware UTC, minute-aligned, ordered, unique, and validated. Open candles never enter decisions. Historical partitions are immutable and versioned in Parquet; DuckDB supports local queries.

Each session obtains Futures exchangeInfo and constructs exact symbol rules: trading status, quantity step/min/max, price tick/min/max, and minimum notional. Limits are never hardcoded. Mark price, funding, balances, positions, margin/position modes, leverage, orders, and order status carry provider timestamps and reconciliation state.

Provider decimal strings are parsed directly to Decimal. Quantity rounds down to step; prices round to tick according to intent; validation follows rounding. Float conversions are prohibited in exchange, Risk, and accounting paths.

Replay, live paper, testnet, shadow, and live use the same Candle, Alpha, Risk, and accounting semantics. If funding is omitted, output records that limitation.
