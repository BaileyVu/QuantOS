# QuantOS V1 Risk and Execution Specification

Version: 2.0.0-V1
Status: Authorized

Risk runs before Execution and rejection is final.

risk_amount = equity × allowed_risk_fraction
raw_quantity = risk_amount ÷ absolute(entry - stop)

Quantity rounds down to exchange step and is then validated against quantity, notional, precision, and trading status. Required leverage derives from approved notional divided by allocated margin; leverage never sets loss budget. Configuration owns normal/max risk, daily/consecutive-loss breakers, leverage/emergency ceilings, margin allocation, and liquidation buffer.

Risk rejects absent/wrong-side stops, unreconciled state, non-TRADING symbol, existing position, active breaker, invalid rounded quantity/notional, excessive leverage, or liquidation too close to the stop.

Only Execution submits/cancels/queries orders. It enforces isolated margin, one-way mode, one position, client-order idempotency, protective stops, and reconciliation. Paper fills use configured adverse slippage and fees. If stop and target touch in one candle, use the conservative outcome. Accounting includes realized/unrealized PnL, fees, used margin, and equity.

Shadow emits the exact validated order plan but never submits. Mainnet submission remains disabled by default and requires explicit approval after the validation lifecycle.
