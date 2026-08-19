# QuantOS — Risk and Execution Specification

## Document Status

**Status:** Frozen V1 Risk and Execution Specification
**Version:** 1.0
**Depends On:** `000_READ_FIRST.md`, `001_PRODUCT_REQUIREMENTS.md`, `002_SYSTEM_ARCHITECTURE.md`, `003_DATA_ARCHITECTURE.md`, `004_FEATURE_AND_MODEL_SPECIFICATION.md`

---

# 1. Purpose

This document defines the V1 risk-management and order-execution requirements for QuantOS.

It defines:

* trade approval
* position sizing
* account constraints
* risk limits
* order validation
* Binance execution
* order-state handling
* failure handling
* reconciliation
* paper execution
* live execution
* execution safety

The central rule is:

> **The Alpha Engine proposes. The Risk Engine decides. The Execution Engine executes.**

No component may bypass this sequence.

---

# 2. V1 Execution Scope

V1 supports:

* Binance Spot
* BTCUSDT
* ETHUSDT
* 1-minute primary decision timeframe

V1 does not support:

* futures
* margin
* leverage
* options
* liquidation management
* short selling through derivatives

The execution model must remain compatible with Binance Spot account mechanics.

---

# 3. Risk-First Architecture

The production decision path is:

```text id="7z4b3n"
Alpha Proposal
      ↓
Risk Validation
      ↓
Risk Approval / Rejection
      ↓
Execution Validation
      ↓
Order Submission
      ↓
Order State Tracking
      ↓
Fill
      ↓
Reconciliation
```

Risk approval is mandatory.

Execution must reject any request that does not contain valid risk approval.

---

# 4. Risk Engine Authority

The Risk Engine has final authority over whether a proposed trade may proceed.

The Risk Engine may:

* approve
* reduce
* reject

a proposed trade.

The Alpha Engine cannot override Risk.

The Execution Engine cannot override Risk.

---

# 5. Trade Proposal

The Alpha Engine produces a trade proposal.

A proposal should contain, where applicable:

* proposal ID
* timestamp
* symbol
* direction
* model output
* model version
* feature version
* proposed action
* intended quantity or sizing information
* strategy version
* relevant decision metadata

A proposal is not an order.

A proposal has no authority to modify exchange state.

---

# 6. Risk Decision

The Risk Engine converts a proposal into a risk decision.

Conceptually:

```text id="n1k4r2"
Trade Proposal
      ↓
Account State
      +
Market State
      +
Risk Configuration
      ↓
Risk Evaluation
      ↓
Approved / Rejected
```

An approved decision must contain enough information for Execution to understand exactly what was approved.

---

# 7. Risk Decision Identity

Every risk decision must have a unique identity.

The decision should reference:

* proposal ID
* timestamp
* symbol
* approved action
* approved quantity
* applicable risk limits
* account state used
* risk configuration version
* approval/rejection result
* rejection reason where applicable

This provides an audit trail from Alpha to Execution.

---

# 8. Position Sizing

Position sizing must be deterministic.

Sizing must consider:

* available account balance
* configured risk limit
* current exposure
* market price
* volatility where used
* transaction costs
* exchange constraints

The resulting quantity must satisfy Binance Spot requirements.

---

# 9. Small-Account Constraint

V1 is designed to operate with a small starting balance.

The initial target is:

**20 USDT**

The system must therefore treat small-account constraints as first-class requirements.

The system must not assume that arbitrary order sizes are executable.

---

# 10. Minimum Order Constraints

Before approval, the system must verify applicable Binance constraints including:

* minimum quantity
* minimum notional
* quantity precision
* price precision
* symbol status
* trading availability

An order that cannot satisfy exchange constraints must be rejected.

The system must never depend on the exchange to correct an invalid order.

---

# 11. Available Balance

Risk must verify that sufficient balance exists for the proposed action.

The system must account for:

* available balance
* existing locked balance
* pending orders
* expected fees
* required minimum remaining balance where configured

If the required balance is unavailable:

```text id="1x4l8k"
Reject Trade
```

---

# 12. Maximum Position Risk

The system must enforce a maximum amount of capital that may be exposed by a trade.

The exact percentage/value must be externally configurable.

The value must be recorded in the configuration used for the relevant run.

The system must not silently change risk limits during operation.

---

# 13. Maximum Exposure

The system must enforce a maximum total exposure.

Exposure must consider relevant existing holdings and pending orders.

A new trade must not cause the account to exceed the configured exposure limit.

---

# 14. Maximum Daily Loss

V1 must support a maximum daily-loss control.

If the configured daily-loss threshold is reached:

```text id="b2h0yq"
No New Trades
```

The system must not automatically resume trading simply because a later trade opportunity appears.

Resume behavior must follow explicit session/reset rules.

---

# 15. Maximum Drawdown

V1 must support a maximum drawdown safety control.

If the configured maximum drawdown is exceeded:

```text id="h0k5ks"
Trading Halt
```

New trades must be blocked until the configured recovery/approval process permits resumption.

---

# 16. Volatility-Aware Risk

Where volatility-aware sizing is part of the approved V1 strategy, the Risk Engine may reduce position size during unusually high volatility.

The objective is:

```text id="q8w9st"
Higher Risk
   ↓
Smaller Position
```

and:

```text id="p7b8zv"
Lower Risk
   ↓
Potentially Larger Position
```

subject to all other limits.

Volatility-aware sizing must remain simple and deterministic.

---

# 17. Transaction-Cost Buffer

Risk calculations must account for expected transaction costs.

The system must not approve a trade that is only marginally executable after considering:

* fees
* expected slippage
* minimum order constraints

Where appropriate, a configurable safety buffer may be applied.

---

# 18. Risk Checks

Before approval, the Risk Engine should evaluate at minimum:

```text id="xj7q2a"
1. Proposal validity
2. Symbol validity
3. Market-data freshness
4. Account-state availability
5. Available balance
6. Current exposure
7. Position-size limit
8. Daily-loss limit
9. Drawdown limit
10. Volatility/risk condition
11. Transaction-cost condition
12. Exchange constraints
13. Trading-mode permission
```

Failure of a critical check results in rejection.

---

# 19. Risk Rejection

A rejected trade must contain a reason.

Examples:

* insufficient balance
* maximum exposure exceeded
* daily loss exceeded
* drawdown limit exceeded
* stale market data
* invalid symbol
* invalid quantity
* minimum notional failure
* excessive volatility
* invalid proposal
* missing account state
* missing model state
* trading disabled

Risk rejection must be recorded.

---

# 20. Risk Configuration

Risk parameters must be externally configurable.

Examples include:

* maximum trade risk
* maximum exposure
* maximum daily loss
* maximum drawdown
* volatility thresholds
* minimum trade value
* cost assumptions
* stale-data threshold

Every production run must identify the risk configuration version used.

---

# 21. Risk Configuration Immutability

A live risk configuration must not change silently.

A configuration change must be:

1. explicit
2. recorded
3. versioned
4. observable

Changing a material risk parameter creates a new configuration state.

---

# 22. Risk Determinism

Given identical:

* proposal
* market state
* account state
* risk configuration

the Risk Engine must produce the same decision.

Risk decisions must not depend on undocumented external state.

---

# 23. Risk Fail-Safe Behavior

If the Risk Engine cannot determine whether a trade is safe:

```text id="s8s9f1"
Reject Trade
```

Examples:

* missing account balance
* stale market state
* corrupted risk configuration
* invalid proposal
* unavailable position state
* calculation failure

Risk must fail closed.

---

# 24. Execution Authority

Only the Execution Engine may submit orders to Binance.

No other module may directly call Binance order-placement endpoints.

This includes:

* Alpha
* Risk
* Evaluation
* research tools
* notebooks
* model-training processes

---

# 25. Execution Modes

Execution must support three operational execution modes relevant to V1:

```text id="h3q0e2"
BACKTEST
PAPER
LIVE
```

The execution implementation differs by mode, but the conceptual order lifecycle remains consistent.

---

# 26. Backtest Execution

Backtest execution must simulate:

* order submission
* fills
* price movement
* fees
* slippage
* account balance
* holdings
* trade history

Backtest execution must not access Binance order-placement APIs.

---

# 27. Paper Execution

Paper execution must simulate real trading without submitting live orders.

It should use:

* live or replayed market data
* production Alpha
* production Risk
* simulated order execution
* simulated account state

Paper execution must not have permission to submit real Binance orders.

---

# 28. Live Execution

Live execution submits approved orders to Binance Spot.

The live path must verify:

* live mode is explicitly enabled
* exchange configuration is correct
* API credentials are available
* symbol is supported
* risk approval is valid
* order parameters are valid
* market data is sufficiently fresh
* account state is available

If a required condition fails:

```text id="f93v9d"
Do Not Submit Order
```

---

# 29. Live Mode Protection

Live trading must require explicit activation.

The default mode must not be live.

The system must make the live mode obvious in logs and runtime state.

The system should require an explicit configuration or command-level activation rather than inferring live mode from the presence of API credentials.

---

# 30. Binance Credential Requirements

Binance credentials must be provided through secure configuration.

Credentials must not be:

* hardcoded
* committed to source control
* stored in datasets
* written to logs
* included in research artifacts

The system should use only the permissions required for trading.

Withdrawal permission is not required for V1.

---

# 31. Order Construction

Execution converts an approved risk decision into an exchange-valid order.

Before submission, Execution must verify:

* symbol
* side
* order type
* quantity
* price where applicable
* precision
* minimum quantity
* minimum notional
* client/order identity
* current exchange constraints

The resulting order must correspond exactly to the approved risk decision.

---

# 32. Order Identity

Every submitted order must have an internal order identity.

Where Binance provides an exchange order identity, the system must record the relationship:

```text id="4p7y1w"
Internal Order ID
      ↕
Binance Order ID
```

This is necessary for reconciliation.

---

# 33. Client Order Identity

Where supported, QuantOS should use a deterministic or uniquely generated client order identity.

The purpose is to reduce ambiguity during retries and reconciliation.

A retry must not accidentally create a second live position when the first order may already have been accepted.

---

# 34. Order Lifecycle

The order lifecycle is:

```text id="h2e7g4"
CREATED
   ↓
SUBMITTED
   ↓
ACKNOWLEDGED
   ↓
OPEN / PARTIALLY_FILLED
   ↓
FILLED
```

Alternative terminal states include:

```text id="1p6l7n"
CANCELLED
REJECTED
EXPIRED
FAILED
```

The exact Binance statuses must be mapped into a stable internal representation.

---

# 35. Order-State Tracking

The system must track the current state of every live order.

The state must include, where applicable:

* internal order ID
* Binance order ID
* symbol
* side
* quantity
* executed quantity
* price
* status
* timestamps
* fees
* rejection/error information

---

# 36. Partial Fills

The system must correctly handle partial fills.

A partial fill is not equivalent to a complete fill.

The system must track:

```text id="xv7k4b"
Original Quantity
      ↓
Executed Quantity
      +
Remaining Quantity
```

Risk/account state must reflect actual execution rather than assuming the entire order was filled.

---

# 37. Fees

Execution must record applicable trading fees where available.

Fees must be associated with the relevant order/fill/trade.

Backtest and paper execution must also model fees according to their configured assumptions.

---

# 38. Slippage

Live execution may experience slippage.

The system must record actual execution prices.

Backtest and paper execution must use explicit slippage assumptions.

Slippage assumptions must be recorded with the relevant research or evaluation run.

---

# 39. Execution Failure

Execution failures must be classified.

Examples:

### Temporary

* network interruption
* temporary API failure
* rate limitation

### Permanent/Validation

* invalid quantity
* invalid symbol
* insufficient balance
* exchange rejection

### Ambiguous

* timeout after submission
* connection failure after request transmission
* uncertain order state

Ambiguous failures require reconciliation before another order is submitted.

---

# 40. Retry Rules

Retries must be conservative.

The system must never blindly retry an order-submission request when the original submission may have succeeded.

Safe retry behavior is:

```text id="p8x2zq"
Submission Result Uncertain
        ↓
Query / Reconcile Order State
        ↓
Determine Actual State
        ↓
Retry Only If Safe
```

The objective is to prevent duplicate orders.

---

# 41. Idempotency

Order submission must be designed to minimize duplicate execution.

The system must maintain enough identity information to determine whether an intended order already exists.

Duplicate-order prevention takes priority over immediate retry speed.

---

# 42. Cancellation

The Execution Engine must support cancellation where required by the approved strategy.

Cancellation must be explicit.

The system must record:

* cancellation request
* cancellation response
* final order state

A cancellation request does not automatically mean the order is cancelled.

Final state must be confirmed.

---

# 43. Account State

The execution system must maintain a representation of relevant account state.

This may include:

* available balances
* locked balances
* holdings
* open orders
* executed trades

The local state must be periodically or event-driven reconciled against Binance.

---

# 44. Reconciliation

Reconciliation compares:

```text id="g0h4j8"
QuantOS State
      ↕
Binance State
```

Reconciliation must detect discrepancies in:

* balances
* open orders
* fills
* holdings
* trade history

---

# 45. Startup Reconciliation

Before live trading begins, the system must reconcile current exchange state.

Startup reconciliation should confirm:

* account access
* balances
* open orders
* relevant holdings
* previously recorded orders
* previously recorded fills

If critical state cannot be reconciled:

```text id="4b7q1r"
Live Trading Blocked
```

---

# 46. Runtime Reconciliation

The system should periodically reconcile live state.

Runtime reconciliation is especially important after:

* network failures
* process restarts
* exchange API failures
* ambiguous order submissions
* unexpected order-state changes

---

# 47. Restart Recovery

If QuantOS restarts during live operation, it must not assume that all prior state was lost.

The system must reconstruct relevant state from:

* persistent local records
* Binance account state
* Binance order state
* reconciliation

The system must reconcile before submitting new orders.

---

# 48. Unknown State

If the system cannot determine whether a live order exists or has been filled:

```text id="6r0h0n"
Trading Halt
```

The system must resolve the unknown state before submitting another order that could duplicate exposure.

---

# 49. Emergency Trading Halt

The system must support a trading halt.

A trading halt must prevent new orders.

Triggers may include:

* maximum drawdown breach
* maximum daily loss breach
* critical reconciliation failure
* stale market data
* repeated execution failure
* system integrity failure
* manual operator halt

Existing orders may require separate cancellation/reconciliation behavior.

---

# 50. Halt State

The system should distinguish between:

```text id="qv4q0d"
RUNNING
PAUSED
HALTED
ERROR
```

A halted state must not automatically transition to live trading without explicit recovery conditions.

---

# 51. Manual Override

Manual intervention must not bypass Risk.

An operator may:

* stop trading
* cancel orders
* resolve state
* restart the system

But manual operation must not create an execution path that bypasses the normal safety boundaries.

---

# 52. Execution Audit Trail

Every live order must be traceable to:

```text id="4t6q0j"
Trade Proposal
      ↓
Risk Decision
      ↓
Order
      ↓
Binance Response
      ↓
Fill
      ↓
Account State
```

This chain is mandatory for debugging and post-trade analysis.

---

# 53. Correlation Identity

The system should use a correlation identity linking:

* Alpha proposal
* Risk decision
* order
* fill
* trade

This allows an operator to answer:

> Why did this particular Binance order exist?

---

# 54. Paper/Live Behavioral Consistency

Paper trading should use the same:

* Alpha logic
* Risk logic
* position-sizing logic
* order-validation logic

as live trading wherever practical.

Only the execution boundary changes.

Conceptually:

```text id="e9a2c4"
             ┌── Paper Execution
Risk ────────┤
             └── Live Execution
```

This reduces the risk that paper results differ from actual production behavior because of different decision logic.

---

# 55. Backtest Execution Consistency

Backtest execution should implement the same conceptual constraints as live execution:

* balance
* position state
* order validity
* fees
* slippage
* trade lifecycle

The exact market-fill simulation will differ from Binance execution, but the accounting rules must remain consistent.

---

# 56. Spot-Specific Accounting

Because V1 uses Binance Spot, the account model must respect asset balances.

For example:

```text id="5u6m0c"
USDT Balance
BTC Balance
ETH Balance
```

A buy requires sufficient quote-asset balance.

A sell requires sufficient base-asset balance.

The system must not use futures-style position accounting for Spot.

---

# 57. Position and Exposure Calculation

Exposure must be calculated from actual account/position state.

The system must not assume that:

```text id="l8s4m9"
Submitted Order = Filled Position
```

Instead:

```text id="2p9q2n"
Submitted
   ↓
Executed Quantity
   ↓
Actual Holdings
```

must determine actual exposure.

---

# 58. Risk Before Execution

Risk approval must occur immediately before execution using sufficiently current information.

If material market or account state changes between Risk approval and order submission, Execution must revalidate the order.

If the order is no longer valid:

```text id="t1j3k8"
Do Not Submit
```

---

# 59. Execution Safety Window

Risk approval must not be considered permanently valid.

An approval should have a limited validity window where appropriate.

If the approval becomes stale:

```text id="w2j5q0"
Re-evaluate Risk
```

The exact validity period must be configurable and recorded.

---

# 60. Exchange Constraint Refresh

Exchange trading constraints may change.

The system should obtain current applicable symbol constraints rather than relying indefinitely on stale values.

Relevant constraints include:

* minimum quantity
* minimum notional
* price precision
* quantity precision
* symbol trading status

---

# 61. Rate Limits

The Execution and Market Data infrastructure must respect Binance API limits.

The system must not generate uncontrolled request loops.

Rate-limit failures must be handled explicitly.

Repeated rate-limit failures may trigger a trading pause.

---

# 62. Network Failure

Network failures must be treated conservatively.

If a request result is unknown:

```text id="9j5w2q"
Unknown State
      ↓
Reconcile
      ↓
Determine Actual State
```

The system must not assume that a failed network response means the exchange rejected the request.

---

# 63. Exchange Maintenance

If Binance becomes unavailable or the relevant symbol becomes unavailable:

```text id="0m3p6a"
No New Trades
```

The system should preserve local state and resume only after connectivity and state have been verified.

---

# 64. Order Reconciliation After Failure

After an execution failure, the system must determine:

1. Was the order submitted?
2. Does Binance know the order?
3. Was it accepted?
4. Is it open?
5. Was it partially filled?
6. Was it filled?
7. Was it cancelled?
8. Was it rejected?

Only after determining the state may the system safely continue.

---

# 65. Execution Logging

Execution logs must record:

* order creation
* validation
* submission
* exchange response
* status changes
* fills
* fees
* cancellations
* errors
* reconciliation

Logs must not contain API secrets.

---

# 66. Risk Metrics

The Risk Engine must maintain enough information to evaluate:

* current exposure
* daily P&L
* drawdown
* available capital
* trade count
* recent execution failures

These metrics must be calculated consistently.

---

# 67. Daily-Loss Reset

The daily-loss measurement must use an explicit timezone convention.

Internal calculations should use UTC.

The reset boundary must be defined and consistent.

A daily-loss limit must not accidentally reset because the workstation timezone changes.

---

# 68. Drawdown Reference

Maximum drawdown must be calculated relative to an explicitly defined equity reference.

The reference must remain consistent across:

* backtest
* paper trading
* live trading

The system must record the relevant equity history needed to reproduce drawdown calculations.

---

# 69. Risk and Model Separation

The Risk Engine must not modify the model.

Risk may reject or size a trade.

Risk must not:

* retrain the model
* modify model parameters
* modify features
* select a different model
* optimize thresholds

This keeps Alpha and Risk responsibilities separate.

---

# 70. Risk and Execution Separation

Risk determines whether a trade is allowed.

Execution determines how the approved trade is submitted and tracked.

Execution must not create a new trading decision.

Execution may reject an order if the order is invalid or unsafe to submit.

---

# 71. Qlib Boundary

Qlib is not part of the risk or execution system.

Qlib must not:

* approve trades
* calculate live risk
* submit Binance orders
* manage account state
* reconcile orders
* control live trading

Qlib-inspired research metadata remains entirely outside the live execution path.

---

# 72. Testing Requirements

## Risk Unit Tests

Must test:

* position sizing
* balance checks
* exposure limits
* daily-loss limits
* drawdown limits
* volatility controls
* exchange constraints
* rejection behavior

## Execution Unit Tests

Must test:

* order construction
* precision handling
* order validation
* state mapping
* partial fills
* cancellation
* error handling

## Integration Tests

Must test:

* Binance adapter
* account-state retrieval
* order lifecycle
* reconciliation
* failure recovery

## End-to-End Tests

Must verify:

```text id="v3k6l1"
Alpha
 ↓
Risk
 ↓
Execution
 ↓
Simulated Exchange
 ↓
Fill
 ↓
Account State
```

before enabling live execution.

---

# 73. Live Trading Readiness

Live execution must not be enabled until the following are verified:

* data pipeline works
* features work
* model loads
* Alpha produces valid proposals
* Risk rejects invalid trades
* paper execution works
* order lifecycle is tested
* reconciliation works
* failure handling works
* credentials are valid
* symbol constraints are known
* live configuration is explicit

---

# 74. First-Live Safety

Because V1 targets a small initial balance, the first live deployment must prioritize operational correctness over trading performance.

The system should begin with:

* one supported symbol at a time where practical
* conservative risk
* small order sizes
* close monitoring
* explicit trading halt capability

The objective of the first live stage is to prove:

```text
Data → Decision → Risk → Execution → Reconciliation
```

works correctly with real exchange state.

---

# 75. Risk and Execution Acceptance Criteria

The V1 Risk and Execution system is compliant when:

* Alpha cannot bypass Risk.
* Risk can reject every proposal.
* Risk fails closed.
* Execution cannot submit without valid Risk approval.
* Binance Spot is the only live execution venue.
* BTCUSDT and ETHUSDT are supported.
* order constraints are validated before submission.
* available balance is checked.
* exposure limits are enforced.
* daily-loss protection exists.
* drawdown protection exists.
* stale data prevents new trades.
* ambiguous order state triggers reconciliation.
* duplicate-order risk is controlled.
* partial fills are handled.
* fees are recorded.
* slippage is accounted for in simulation.
* startup reconciliation occurs before live trading.
* restart recovery is supported.
* emergency trading halt exists.
* paper execution cannot place live orders.
* backtest execution cannot place live orders.
* Qlib has no role in live execution.
* all important execution events are auditable.
* the system fails closed when safety cannot be established.

---

# 76. Final Risk and Execution Statement

The V1 trading safety model is intentionally simple:

```text id="2x2qsl"
ALPHA
  │
  │ proposes
  ▼
RISK
  │
  │ approves / rejects
  ▼
EXECUTION
  │
  │ submits
  ▼
BINANCE
  │
  │ confirms
  ▼
RECONCILIATION
```

The most important rule is:

> **No valid Risk approval means no live order.**

The second most important rule is:

> **Unknown exchange state means stop and reconcile.**

QuantOS V1 must prefer missing a trade over executing an unsafe or duplicate trade.

The purpose of the execution system is not to trade as frequently as possible.

Its purpose is to execute **only valid, approved, correctly sized trades while maintaining an accurate representation of real exchange state**.
