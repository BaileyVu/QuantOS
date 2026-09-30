# QuantOS V1 Validation and Backtesting

Version: 2.0.0-V1
Status: Authorized

Validation reuses the completed-candle Alpha, Risk, and accounting path intended for runtime.

Research → Replay/Backtest → Walk-Forward → Monte Carlo → Live Paper → Binance Testnet → Mainnet Shadow → Explicit Live Approval

Replay is deterministic and includes exchange rules, adverse slippage, fees, leverage/margin, stops/targets, and causal ordering. Funding is modeled when reliable data is supplied; otherwise the limitation is recorded.

Minimum output: starting/ending equity, net PnL, trades, wins/losses, win rate, average win/loss, expectancy, profit factor, maximum drawdown, fees/funding, strategy/regime attribution, rejection reasons, and configuration identity.

Replay output also reports timeframe attribution and candidate/selection/rejection counts by timeframe; consecutive-loss breaker triggers, resets, blocked candidates, and disabled duration; daily-loss breaker triggers; no-signal decisions; one-position rejections; and exchange-rule rejections.

Walk-forward preserves temporal isolation. Monte Carlo reports assumptions and never implies a guarantee. No test or automated gate enables mainnet live trading.
