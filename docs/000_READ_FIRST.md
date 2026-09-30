# QuantOS Core — V1 Source of Truth

Version: 2.0.0-V1
Status: Authorized V1 specification
Last Updated: 2026-10-01

## Mission

QuantOS V1 is a local autonomous multi-strategy trading system for Binance USDⓈ-M Futures. It observes completed market data, classifies regime, evaluates compatible strategies, selects LONG/SHORT/HOLD, obtains independent Risk approval, and lets Execution manage orders and positions.

Runtime trading is independent of Codex, Astra, or per-candle LLM calls. Profitability is an objective, never a guarantee. Capital preservation and operational correctness are mandatory.

## Initial scope

- BTCUSDT perpetual Futures; completed one-minute candles
- one-way position mode; isolated margin; one simultaneous position
- deterministic regime classification and a small multi-strategy library
- risk-governed position sizing and dynamic leverage
- mandatory protective stops
- approximately 20 USDT reference capital
- local workstation; Parquet plus DuckDB
- mainnet submission disabled unless explicitly approved

Spot/research components may remain but are not the V1 trading target.

## Exact production modules

V1 has exactly six business modules: Market Data, Feature Engine, Alpha Engine, Risk Engine, Execution Engine, and Evaluation Engine. Regime and strategy selection belong to Alpha. Position management belongs to Execution constrained by Risk. Storage, configuration, logging, exchange adapters, and interfaces are infrastructure.

## Non-negotiable safety

Risk evaluates every exposure before Execution; rejection is final. Only Execution submits orders. Fail closed on incomplete/stale data, invalid exchange rules or precision, unreconciled account/position/order state, duplicate intent, or uncertain execution outcome.

Every position requires a stop. Leverage is derived after risk-sized notional and is only a capital-efficiency mechanism. No martingale, averaging down, or loss-driven margin increases. Estimated liquidation must remain beyond the stop by a configured buffer.

## Data and lifecycle

All timestamps are UTC. Historical and runtime modes share completed-candle, Alpha, Risk, and accounting rules. Canonical sources are immutable/versioned and exact financial arithmetic uses Decimal.

Research → Replay/Backtest → Walk-Forward → Monte Carlo → Live Paper → Testnet → Shadow Mainnet → Explicit Live Approval

No automated result grants live authority. Priorities are capital preservation, correctness, robustness, simplicity, explainability, performance, then profitability.
