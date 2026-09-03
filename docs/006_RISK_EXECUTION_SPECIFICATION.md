# QuantOS Core — 006_RISK_EXECUTION_SPECIFICATION.md

Version: 1.0.0-V1
Status: Frozen V1
Last Updated: 2026-08-19

## 1. Objective

This document defines the safety boundary between an alpha decision and an exchange order.

The combined responsibility is implemented through the separate V1 modules:

- Risk Engine
- Execution Engine

Risk always precedes execution.

## 2. Risk Decision

Input:

- Alpha Decision
- current market state;
- account/position state;
- configuration;
- volatility;
- estimated transaction costs.

Output:

- approved order intent; or
- explicit rejection with reason.

## 3. Mandatory Risk Controls

### Position Risk

Position size must respect configured maximum risk.

### Exposure

The system must prevent exposure beyond configured limits.

### Daily Loss

Trading must stop when the configured daily loss limit is reached.

### Drawdown

Trading must stop or enter a protected state when maximum drawdown is breached.

### Volatility

Position sizing must adapt to market volatility where the approved strategy requires it.

### Data Safety

No trade may proceed with stale, missing, invalid, or inconsistent market/risk state.

### Edge After Costs

A trade is rejected if expected edge after fees and expected slippage is not positive.

## 4. Risk Precedence

```text
Alpha decision
      ↓
Risk checks
      ↓
REJECT ─────────→ no order
      ↓
APPROVE
      ↓
Execution
```

No later component can turn a risk rejection into an order.

## 5. Execution Responsibilities

Execution owns:

- order construction;
- market/limit selection within approved policy;
- exchange request;
- acknowledgement;
- cancellation;
- retry handling;
- order status;
- fill tracking.

Execution does not generate alpha or evaluate strategy risk.

## 6. Market vs Limit

Both Market and Limit orders are supported by product requirements.

The execution policy must choose based on:

- expected execution quality;
- current market conditions;
- urgency;
- estimated slippage;
- order-size constraints.

If neither order type provides acceptable execution quality, reject the order.

## 7. Fees and Slippage

Backtest, paper, and live decision logic must use explicit transaction-cost assumptions.

At minimum track:

- estimated fee;
- estimated slippage;
- gross edge;
- net expected edge.

## 8. Binance Safety

The exchange adapter must normalize Binance-specific errors into internal error categories.

Credentials:

- external only;
- never hardcoded;
- no withdrawal permission required.

## 9. Failure Handling

On uncertain execution state:

- do not blindly duplicate an order;
- reconcile order state with the exchange;
- preserve the event trail;
- fail closed if reconciliation is unavailable.

## 10. Idempotency

Every submitted order must have a stable internal identity.

Retries must not unintentionally create duplicate exposure.

## 11. Logging

Record:

- risk input;
- risk result;
- rejection reason;
- order intent;
- order request;
- exchange response;
- order status;
- fill;
- reconciliation event.

## 12. Paper Mode

Paper trading must exercise the same logical risk and execution path as live trading, replacing the real exchange order side effect with a deterministic simulator.

## 13. Live Enablement

Live trading requires:

- explicit runtime mode;
- valid credentials;
- passing validation state;
- enabled live configuration.

Live mode must never be the default.
