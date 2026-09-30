# QuantOS V1 Product Requirements

Version: 2.0.0-V1
Status: Authorized

QuantOS provides a runnable local workflow for autonomous BTCUSDT USDⓈ-M Futures trading from immutable historical data through explicitly approved live execution.

Completed market data → regime classification → strategy evaluation → deterministic selection → Risk decision → leverage/quantity plan → Execution → protected position management → exit/reconciliation.

Exactly one mode is active: research, accelerated replay, live-market paper, Binance Futures testnet, mainnet shadow, or explicitly approved live mainnet. Paper is the safe default. Shadow reads state but never submits.

Market Data supplies completed one-minute Futures candles, exchange information, mark price, and funding when available. Alpha supplies deterministic regimes and a small library covering trend continuation/pullback, breakout/expansion, breakout-retest, and range mean reversion. Signals include direction, strategy, compatibility, entry, stop, target, strength, evidence, timestamp, and rationale.

Risk sizes from equity, risk fraction, and stop distance; derives leverage from notional and allocated margin; and enforces exchange filters, one-position policy, mandatory stop, loss breakers, leverage limits, and liquidation buffer.

Paper Execution models fees, adverse slippage, leverage/margin, conservative stop/target ordering, and realized/unrealized PnL. Evaluation reports equity, PnL, trades, wins/losses, win rate, average win/loss, expectancy, profit factor, drawdown, fees, and strategy/regime attribution.

Business logic is deterministic, auditable, UTC-normalized, and provider-independent. Credentials never appear in logs or repository files.
