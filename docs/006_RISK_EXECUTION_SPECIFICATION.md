# QuantOS Core

## 006_RISK_EXECUTION_SPECIFICATION.md

**Version:** 0.1.0-alpha
**Status:** Draft V1
**Document:** 006 — Risk & Execution Specification
**Scope:** Version 1
**Primary Exchange:** Binance Spot
**Primary Symbols:** BTCUSDT, ETHUSDT
**Primary Timeframe:** 1 minute

---

# 1. Purpose

This document defines the deterministic risk-control and trade-execution behavior of QuantOS Core.

The Risk and Execution layer is the final control boundary between an Alpha decision and real capital.

Its purpose is not to improve the quality of an alpha signal.

Its purpose is to ensure that:

1. valid alpha is converted into an appropriately sized trade;
2. invalid or unsafe trades are rejected;
3. portfolio exposure remains within defined limits;
4. losses remain bounded;
5. execution behavior is deterministic;
6. exchange state remains synchronized with internal state;
7. operational failures cannot silently create uncontrolled positions;
8. the system can safely stop trading when required.

The central principle is:

> **Alpha proposes. Risk disposes. Execution transacts.**

The Alpha Engine may identify an opportunity.

It does not have authority to decide how much capital may be exposed.

The Risk Engine has final authority over whether a proposed trade is permitted and what maximum position size is allowed.

The Execution Engine has authority to translate an approved order into exchange actions.

No downstream component may bypass the Risk Engine.

---

# 2. Scope

Version 1 implements a deliberately small risk and execution system.

The system is designed for:

* Binance Spot;
* BTCUSDT;
* ETHUSDT;
* one production strategy;
* one production alpha pipeline;
* one local deployment;
* small initial capital;
* systematic trading;
* deterministic execution logic.

The current V1 repository specification defines an initial capital baseline of **20 USDT**. Risk configuration must nevertheless be parameterized so that capital can be changed without modifying business logic.

The Risk and Execution layer shall support both:

```text
Paper Trading
        ↓
Live Trading
```

The same risk rules shall apply to both modes.

The difference between paper and live execution is the final execution adapter.

---

# 3. Design Philosophy

## 3.1 Capital Preservation First

QuantOS shall prefer missing a profitable trade over accepting an uncontrolled loss.

The system is therefore intentionally asymmetric:

```text
False Positive
    ↓
Trade rejected
    ↓
Opportunity missed
```

is preferable to:

```text
False Positive
    ↓
Oversized position
    ↓
Large loss
    ↓
Capital impairment
```

The Risk Engine is therefore allowed to reject trades that the Alpha Engine considers attractive.

This is not an error.

It is a required system behavior.

---

## 3.2 Risk Is a Hard Constraint

Risk controls are not suggestions.

They are hard constraints.

A strategy cannot override:

* maximum position size;
* maximum exposure;
* maximum loss;
* drawdown limits;
* cooldown state;
* circuit-breaker state;
* exchange safety checks;
* execution safety checks.

No confidence score, expected return, model prediction, or alpha strength can bypass a risk constraint.

---

## 3.3 Deterministic Decisions

Given identical:

* portfolio state;
* market state;
* alpha signal;
* configuration;
* risk parameters;

the Risk Engine must produce the same decision.

Example:

```text
Input
    Capital = 20 USDT
    Existing Exposure = 4 USDT
    Alpha = LONG
    Confidence = 0.81
    Volatility = Known
    Risk State = NORMAL

Output
    APPROVED
    Maximum Notional = X
```

Repeating the evaluation with identical inputs must produce the same result.

Randomized position sizing is prohibited in V1.

---

# 4. Risk Architecture

The V1 risk architecture consists of five logical stages.

```text
                Alpha Decision
                      │
                      ▼
              ┌───────────────┐
              │ Trade Request │
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │ Risk Precheck │
              └───────┬───────┘
                      │
             ┌────────┴────────┐
             │                 │
          REJECT             APPROVE
             │                 │
             ▼                 ▼
        No Order        Position Sizing
                               │
                               ▼
                        Risk Validation
                               │
                               ▼
                         Order Proposal
                               │
                               ▼
                       Execution Engine
```

The logical ownership is:

| Component        | Responsibility                                   |
| ---------------- | ------------------------------------------------ |
| Alpha Engine     | Propose trade direction and expected opportunity |
| Risk Engine      | Validate trade and determine permitted exposure  |
| Execution Engine | Submit and manage approved orders                |
| Exchange Adapter | Communicate with Binance                         |
| Portfolio State  | Maintain authoritative account/position state    |

The Risk Engine must not directly submit exchange orders.

The Execution Engine must not independently calculate strategy risk.

---

# 5. Risk Hierarchy

Risk controls operate at multiple levels.

```text
Level 1
System Safety

        ↓

Level 2
Account Risk

        ↓

Level 3
Portfolio Risk

        ↓

Level 4
Symbol Risk

        ↓

Level 5
Trade Risk

        ↓

Level 6
Order Risk

        ↓

Execution
```

A lower-level approval cannot override a higher-level rejection.

For example:

```text
Trade Risk
    APPROVED

but

Account Risk
    HALTED
```

produces:

```text
FINAL RESULT = REJECT
```

The highest applicable restriction always wins.

---

# 6. Risk States

The Risk Engine shall maintain one global risk state.

V1 defines the following states:

```text
NORMAL
CAUTION
HALTED
EMERGENCY
```

## 6.1 NORMAL

Normal operation.

New trades may be evaluated and approved.

```text
New Entries:        Allowed
Position Reduction: Allowed
Position Exit:      Allowed
Emergency Exit:     Allowed
```

---

## 6.2 CAUTION

The system has detected elevated risk but has not reached a full trading halt.

Examples include:

* approaching daily loss limit;
* approaching drawdown limit;
* abnormal execution conditions;
* repeated rejected orders;
* degraded market data;
* temporary exchange instability.

During CAUTION:

```text
New Entries:        Restricted
Position Reduction: Allowed
Position Exit:      Allowed
Emergency Exit:     Allowed
```

V1 may implement CAUTION primarily through reduced position sizing or complete entry rejection depending on the triggering condition.

Risk configuration shall determine the exact behavior.

---

## 6.3 HALTED

New trading is disabled.

Existing positions may still be reduced or closed.

```text
New Entries:        Forbidden
Position Increase:  Forbidden
Position Reduction: Allowed
Position Exit:      Allowed
Emergency Exit:     Allowed
```

HALTED is a protective state.

The system must not automatically resume merely because the triggering metric temporarily improves.

Resume behavior must satisfy the configured recovery condition.

---

## 6.4 EMERGENCY

The system has entered a critical safety state.

Examples:

* internal state cannot be reconciled with exchange state;
* account balance cannot be reliably determined;
* exchange communication becomes unsafe;
* duplicated execution is suspected;
* unexpected position detected;
* critical software invariant violated.

In EMERGENCY:

```text
New Entries:        Forbidden
Position Increase:  Forbidden
Position Reduction: Allowed
Position Exit:      Allowed
```

The system must prioritize restoring a known-safe state.

Automatic strategy continuation is prohibited.

---

# 7. Trade Request Contract

The Alpha Engine does not send an exchange order.

It sends a **Trade Request** to the Risk Engine.

Conceptually:

```text
TradeRequest

symbol
direction
signal_timestamp
alpha_id
confidence
expected_return
expected_loss
expected_holding_time
regime
reference_price
signal_strength
```

The Risk Engine enriches this request with:

```text
account_equity
available_balance
current_position
current_exposure
current_drawdown
daily_pnl
volatility
market_state
risk_state
risk_configuration
```

The resulting object is a risk decision.

Conceptually:

```text
RiskDecision

decision
symbol
direction
approved_notional
approved_quantity
reference_price
risk_budget
risk_state
rejection_reason
configuration_version
decision_timestamp
```

Possible decisions:

```text
APPROVED
REJECTED
REDUCED
HALTED
```

Every decision must contain a reason.

---

# 8. Risk Evaluation Pipeline

Every trade request passes through the same ordered pipeline.

```text
1. Validate Request
2. Validate System State
3. Validate Market State
4. Validate Account State
5. Validate Risk State
6. Validate Symbol Exposure
7. Calculate Base Position Size
8. Apply Risk Limits
9. Apply Volatility Adjustment
10. Apply Portfolio Constraints
11. Apply Capital Availability
12. Apply Exchange Constraints
13. Produce Final Decision
```

The sequence is deterministic.

A rejected request shall terminate processing where further evaluation cannot change the result.

Example:

```text
System HALTED
        ↓
REJECT
```

There is no reason to calculate position size.

---

# 9. Base Position Sizing

Position sizing determines how much capital may be allocated to a trade.

The system must not use:

```text
"Use all available balance."
```

as a sizing rule.

Instead, position size must be derived from explicit constraints.

The conceptual sizing process is:

```text
Base Size
    ↓
Risk Adjustment
    ↓
Volatility Adjustment
    ↓
Portfolio Adjustment
    ↓
Exposure Limit
    ↓
Available Capital Limit
    ↓
Exchange Constraint
    ↓
Final Size
```

The final size is therefore:

```text
Final Position Size
=
min(
    Base Risk Size,
    Exposure Limit,
    Capital Limit,
    Portfolio Limit,
    Exchange Limit
)
```

Additional reductions may be applied.

No calculation may increase the size beyond the strictest limit.

---

# 10. Risk Budget

Each trade receives a maximum permitted risk budget.

Risk budget represents the maximum amount of account equity that the system is willing to expose to the trade under the defined loss model.

Conceptually:

```text
Risk Budget
=
Account Equity
×
Configured Risk Fraction
```

Example:

```text
Account Equity = 20 USDT
Risk Fraction  = 0.5%

Risk Budget = 0.10 USDT
```

The example is illustrative.

The actual production value shall come from configuration.

The Risk Engine must never hard-code a risk percentage inside strategy logic.

---

# 11. Stop-Based Position Sizing

Where the strategy provides a valid protective-loss distance, position sizing may be calculated from:

```text
Position Size
=
Risk Budget
/
Loss Distance
```

For price-based assets:

```text
Quantity
=
Risk Budget
/
|Entry Price - Protective Price|
```

The resulting quantity is then constrained by:

* maximum notional;
* available capital;
* symbol exposure;
* account exposure;
* exchange minimum quantity;
* exchange step size.

If the calculated quantity is below the exchange minimum and cannot be safely increased without violating risk constraints:

```text
REJECT
```

The system must not increase position size merely to satisfy exchange minimum order requirements.

---

# 12. Maximum Position Exposure

Regardless of calculated risk size, each symbol has a maximum permitted notional exposure.

Conceptually:

```text
Maximum Symbol Exposure
=
Account Equity
×
Configured Symbol Exposure Limit
```

Example:

```text
Account Equity
20 USDT

Symbol Exposure Limit
50%

Maximum BTCUSDT Exposure
10 USDT
```

If an existing position already consumes part of that exposure:

```text
Remaining Exposure
=
Maximum Exposure
-
Current Exposure
```

A new order may not exceed the remaining amount.

---

# 13. Account-Level Exposure

The system must also enforce an account-level exposure limit.

For V1, the account exposure model is intentionally simple.

The Risk Engine tracks:

```text
Cash
+
Open Position Notional
+
Pending Order Notional
```

as capital commitments.

Pending orders must count toward exposure.

This prevents multiple simultaneous orders from independently passing risk checks while collectively exceeding the account limit.

Example:

```text
Account Equity = 20 USDT
Maximum Exposure = 80%

Maximum Exposure = 16 USDT

Existing Position = 8 USDT
Pending Orders = 3 USDT

Remaining Exposure = 5 USDT
```

A new order requesting 7 USDT must therefore be rejected or reduced to the permitted amount.

---

# 14. Pending Order Reservation

Capital committed to an unfilled order is not considered free capital.

The system must reserve capital for pending orders.

Conceptually:

```text
Available Capital
=
Account Equity
-
Current Position Capital
-
Pending Order Reservation
-
Safety Reserve
```

This prevents the following failure:

```text
Order A
    passes risk check

Order B
    passes risk check

Order C
    passes risk check

All orders execute
    ↓
Account becomes overexposed
```

Risk evaluation must consider the portfolio state including pending orders.

---

# 15. Safety Reserve

QuantOS shall maintain a configurable capital reserve.

The safety reserve exists to account for:

* fees;
* execution discrepancies;
* rounding;
* minimum balance requirements;
* unexpected account movements;
* operational recovery.

The system must not allocate 100% of available capital to new positions.

Conceptually:

```text
Deployable Capital
=
Account Equity
-
Safety Reserve
```

The reserve is not a trading position.

It is an operational safety buffer.

---

# 16. Kelly Criterion Constraint

Kelly sizing may be used as an upper-bound reference.

It must not be used as an unrestricted position-sizing mechanism.

The theoretical Kelly fraction is:

```text
f* = (bp - q) / b
```

where:

```text
p = probability of winning
q = probability of losing
b = win/loss payoff ratio
```

Theoretical Kelly sizing is highly sensitive to estimation error.

Therefore V1 shall use a capped Kelly approach.

Conceptually:

```text
Allowed Kelly
=
min(
    Estimated Kelly,
    Configured Kelly Cap
)
```

The Kelly result is then passed through all other risk constraints.

Therefore:

```text
Kelly
    ↓
Risk Cap
    ↓
Exposure Cap
    ↓
Drawdown Adjustment
    ↓
Portfolio Cap
    ↓
Final Position Size
```

Kelly can reduce or influence a position.

It can never override:

* account limits;
* drawdown limits;
* exposure limits;
* circuit breakers;
* exchange constraints.

If Kelly inputs are unavailable or unreliable:

```text
Kelly Adjustment = 1.0
```

or the configured conservative fallback must be applied.

The system must never manufacture a probability estimate solely to enable Kelly sizing.

---

# 17. Fractional Kelly

If Kelly sizing is enabled, V1 shall use fractional Kelly rather than full theoretical Kelly.

Conceptually:

```text
Fractional Kelly
=
Theoretical Kelly
×
Kelly Fraction
```

The resulting value is then capped.

Example:

```text
Theoretical Kelly = 18%
Kelly Fraction    = 25%

Fractional Kelly = 4.5%
```

If the configured maximum Kelly exposure is 2%:

```text
Final Kelly Exposure = 2%
```

This protects the system against estimation error.

The production configuration must define:

```text
kelly_enabled
kelly_fraction
kelly_cap
```

---

# 18. Volatility Adjustment

Position size should decrease as market uncertainty increases.

The Risk Engine may use the volatility information supplied by the Feature and Alpha layers.

Conceptually:

```text
Higher Volatility
        ↓
Smaller Position

Lower Volatility
        ↓
Normal Position
```

The volatility adjustment must be bounded.

The system must not increase position size without an explicit risk rule.

A volatility adjustment may therefore be expressed as:

```text
Volatility Multiplier
∈
[minimum_multiplier, maximum_multiplier]
```

with:

```text
maximum_multiplier <= 1.0
```

for the conservative V1 implementation.

This ensures volatility logic can reduce risk but cannot silently leverage the account.

---

# 19. Drawdown Adjustment

Position sizing must become more conservative as account drawdown increases.

Conceptually:

```text
Drawdown = 0%
    ↓
Normal Risk

Drawdown = Moderate
    ↓
Reduced Risk

Drawdown = High
    ↓
Minimal Risk

Drawdown = Limit
    ↓
Trading Halt
```

This creates a nonlinear protection mechanism.

The system must not respond to losses by increasing trade size to recover losses.

The following behavior is explicitly prohibited:

```text
Loss
 ↓
Increase position
 ↓
Attempt recovery
```

This is martingale behavior and is incompatible with V1 risk philosophy.

---

# 20. Maximum Drawdown

Maximum drawdown is an account-level safety boundary.

Drawdown shall be calculated against a defined equity reference.

Conceptually:

```text
Peak Equity
      ↓
Current Equity
      ↓
Drawdown
```

Formula:

```text
Drawdown
=
(Peak Equity - Current Equity)
/
Peak Equity
```

The Risk Engine must maintain the current peak equity reference.

Example:

```text
Peak Equity = 20.00 USDT
Current Equity = 18.50 USDT

Drawdown
=
(20.00 - 18.50) / 20.00

= 7.5%
```

The configured drawdown thresholds determine system behavior.

---

# 21. Drawdown Thresholds

V1 defines three conceptual drawdown zones.

```text
NORMAL
CAUTION
HALT
```

Example configuration:

```text
drawdown_caution_threshold
drawdown_halt_threshold
```

The exact production values belong in configuration rather than source code.

Behavior:

```text
Drawdown < Caution
    ↓
NORMAL

Caution <= Drawdown < Halt
    ↓
CAUTION

Drawdown >= Halt
    ↓
HALTED
```

Once HALTED, new positions are forbidden.

Existing positions may be reduced or closed.

---

# 22. Daily Loss Limit

The system must maintain a daily realized and unrealized PnL risk boundary.

Daily loss is measured from the configured daily starting equity reference.

Conceptually:

```text
Daily PnL
=
Current Equity
-
Daily Starting Equity
```

The Risk Engine must account for:

* realized trading PnL;
* unrealized position PnL;
* trading fees;
* execution costs where available.

A loss limit is not merely a reporting metric.

It is a trading control.

---

# 23. Daily Loss States

The daily loss system follows:

```text
NORMAL
    ↓
CAUTION
    ↓
HALTED
```

Example:

```text
Daily Loss < Warning Threshold
    → NORMAL

Daily Loss >= Warning Threshold
    → CAUTION

Daily Loss >= Maximum Daily Loss
    → HALTED
```

When the maximum daily loss is reached:

```text
No New Entries
```

The system must not continue trading simply because a new signal appears.

Existing positions remain subject to normal exit and protective logic.

---

# 24. Daily Loss Reset

The daily loss reference must reset according to a deterministic trading-day definition.

The reset boundary must be explicitly configured.

V1 should use a fixed UTC boundary.

The system must not infer the reset time from the local workstation clock.

This prevents behavior from changing due to:

* timezone;
* daylight-saving changes;
* workstation configuration;
* deployment location.

At the reset boundary:

```text
Daily Starting Equity
=
Current Account Equity
```

The historical peak equity used for drawdown is not reset by the daily reset.

These are separate risk concepts.

---

# 25. Consecutive Loss Protection

The Risk Engine may maintain a consecutive-loss counter.

The purpose is to detect short-term degradation in strategy behavior.

Conceptually:

```text
Trade Loss
    ↓
Loss Counter + 1

Trade Win
    ↓
Loss Counter Reset
```

A configured threshold may trigger:

```text
CAUTION
```

or:

```text
HALTED
```

This mechanism is not intended to predict the next trade.

It is an operational protection against a potentially degraded strategy or market regime.

The system must not increase risk following consecutive losses.

---

# 26. Cooldown Mechanism

A cooldown temporarily prevents new entries after defined events.

Possible triggers include:

* completed losing trade;
* consecutive losses;
* circuit-breaker activation;
* abnormal execution;
* rejected order burst;
* strategy state transition;
* exchange instability.

Cooldown state:

```text
COOLDOWN_ACTIVE
```

During cooldown:

```text
New Entries:        Forbidden
Position Reduction: Allowed
Position Exit:      Allowed
Emergency Exit:     Allowed
```

The cooldown duration must be deterministic and configuration-driven.

Example:

```text
cooldown_seconds = N
```

The system must use event timestamps rather than sleep-based logic.

---

# 27. Cooldown Scope

Cooldowns may operate at different scopes.

```text
Trade
Symbol
Strategy
Account
```

V1 should keep the implementation minimal.

The default production scope is:

```text
Account + Symbol
```

This means a problematic BTCUSDT trading sequence does not necessarily require ETHUSDT to be halted unless the trigger is account-wide.

Account-wide safety events override symbol-level cooldowns.

---

# 28. Circuit Breakers

Circuit breakers are hard safety mechanisms.

They exist to stop automated trading when the system detects conditions under which continued operation cannot be trusted.

Circuit breakers may be triggered by:

```text
Maximum Daily Loss
Maximum Drawdown
Exchange Connectivity Failure
Market Data Failure
Portfolio Reconciliation Failure
Unexpected Position
Repeated Execution Failure
Duplicate Order Detection
Critical Application Error
```

Once triggered:

```text
Risk State → HALTED or EMERGENCY
```

depending on severity.

---

# 29. Circuit Breaker Principle

A circuit breaker must fail closed.

That means:

```text
Unknown Safety State
        ↓
Do Not Trade
```

not:

```text
Unknown Safety State
        ↓
Assume Safe
        ↓
Trade
```

The system must never interpret missing risk information as permission to trade.

Examples:

```text
Unknown account balance
    → REJECT

Unknown position
    → REJECT

Unknown exchange status
    → REJECT

Unknown risk state
    → REJECT
```

This principle is mandatory.

---

# 30. Risk Decision Priority

When multiple risk rules apply, the most restrictive result wins.

Example:

```text
Alpha
    LONG

Position Sizing
    APPROVED

Daily Loss
    CAUTION

Drawdown
    HALTED

Final Decision
    REJECT
```

The decision process can be represented as:

```text
Trade Request
      ↓
Hard Safety Checks
      ↓
Account Limits
      ↓
Drawdown Limits
      ↓
Daily Loss Limits
      ↓
Exposure Limits
      ↓
Position Sizing
      ↓
Exchange Constraints
      ↓
Final Approval
```

No later stage can reverse a previous hard rejection.

---

# 31. Risk Rejection Reasons

Every rejected trade must have a machine-readable reason.

Examples:

```text
SYSTEM_HALTED
RISK_STATE_INVALID
DAILY_LOSS_LIMIT
MAX_DRAWDOWN
SYMBOL_EXPOSURE_LIMIT
ACCOUNT_EXPOSURE_LIMIT
INSUFFICIENT_BALANCE
PENDING_ORDER_LIMIT
INVALID_MARKET_DATA
INVALID_POSITION_STATE
INVALID_ALPHA
INVALID_PRICE
INVALID_QUANTITY
VOLATILITY_LIMIT
COOLDOWN_ACTIVE
CIRCUIT_BREAKER_ACTIVE
EXCHANGE_UNAVAILABLE
EXCHANGE_CONSTRAINT
```

A human-readable explanation may accompany the machine-readable code.

Example:

```text
Decision:
    REJECTED

Reason:
    MAX_DRAWDOWN

Explanation:
    Account drawdown exceeded configured trading threshold.
```

Risk rejection must be observable and auditable.

---

# 32. No Silent Risk Failures

The Risk Engine must never silently reject a trade.

Every decision must produce:

```text
timestamp
symbol
alpha_id
risk_state
decision
requested_size
approved_size
reason
configuration_version
account_state_reference
```

This creates an auditable chain:

```text
Market Data
    ↓
Features
    ↓
Alpha
    ↓
Risk Decision
    ↓
Order
    ↓
Execution
    ↓
Portfolio
```

A missing link in this chain is an operational defect.

---

# 33. Risk Invariants

The following invariants are mandatory.

### Invariant 1

No order may reach the Execution Engine without Risk Engine approval.

### Invariant 2

Risk Engine approval must contain an explicit maximum quantity.

### Invariant 3

Execution Engine may execute less than the approved quantity.

### Invariant 4

Execution Engine must never execute more than the approved quantity.

### Invariant 5

Pending orders count toward exposure.

### Invariant 6

Unknown portfolio state blocks new trading.

### Invariant 7

Unknown account balance blocks new trading.

### Invariant 8

HALTED state blocks new entries.

### Invariant 9

EMERGENCY state blocks new entries.

### Invariant 10

Risk parameters must be configuration-driven.

### Invariant 11

Risk decisions must be reproducible.

### Invariant 12

No recovery mechanism may increase risk after losses.

### Invariant 13

No alpha confidence value may override a hard risk limit.

### Invariant 14

No execution retry may create additional exposure.

### Invariant 15

A safety failure must fail closed.

---

# 34. Risk Engine Boundary

The Risk Engine owns:

```text
Risk State
Position Sizing
Exposure Limits
Drawdown Limits
Daily Loss Limits
Cooldowns
Circuit Breakers
Risk Rejection
Risk Approval
Risk Decision Logging
```

The Risk Engine does not own:

```text
Feature Engineering
Alpha Generation
Model Training
Exchange API Communication
Order Submission
Historical Data Storage
Strategy Research
```

This boundary is mandatory.

---

# 35. Execution Boundary

The Execution Engine receives only approved trade instructions.

Conceptually:

```text
Alpha Engine
      ↓
Trade Request
      ↓
Risk Engine
      ↓
Approved Order Intent
      ↓
Execution Engine
      ↓
Exchange Adapter
      ↓
Binance
```

The Execution Engine cannot transform:

```text
Approved Quantity = 0.001 BTC
```

into:

```text
0.002 BTC
```

because market conditions changed.

If additional exposure is required, a new Risk Engine decision is mandatory.

---

# 36. V1 Risk Objective

The objective of the V1 Risk Engine is not to maximize capital utilization.

It is to prevent the trading system from destroying its ability to continue operating.

The hierarchy is:

```text
1. Protect account
2. Prevent uncontrolled exposure
3. Maintain valid state
4. Execute approved trades correctly
5. Preserve statistical expectancy
6. Maximize capital efficiency
```

Capital efficiency is therefore subordinate to safety.

A trade that cannot be safely sized is rejected.

A trade that cannot be safely executed is rejected.

A system whose state cannot be trusted stops trading.

That behavior is considered correct.

---

# 37. Part 1 Completion Criteria

Part 1 of the Risk & Execution specification is complete when the implementation satisfies the following:

* Risk Engine is the authoritative risk boundary.
* Alpha cannot directly submit orders.
* Every trade request passes deterministic risk validation.
* Account exposure is explicitly limited.
* Symbol exposure is explicitly limited.
* Pending orders reserve capital.
* Position sizing is risk-budget driven.
* Kelly sizing is capped and optional.
* Volatility can reduce, but not increase, risk.
* Drawdown protection exists.
* Daily loss protection exists.
* Cooldowns exist.
* Circuit breakers exist.
* HALTED and EMERGENCY states exist.
* Unknown safety state fails closed.
* Every rejection has a machine-readable reason.
* Every risk decision is auditable.
* Execution cannot exceed Risk-approved quantity.

The next section of this specification will define the **execution model itself**, including order intent, order types, market/limit behavior, slippage, fees, partial fills, order state transitions, retries, idempotency, exchange synchronization, reconciliation, and emergency shutdown behavior.
# QuantOS Core

---

# 38. Execution Engine

The Execution Engine is the controlled transaction layer between the Risk Engine and the exchange.

Its responsibility is simple:

> **Execute exactly what Risk approved, no more and no less.**

The Execution Engine must not:

* generate alpha;
* decide whether a trade is attractive;
* independently resize a position;
* override risk limits;
* increase approved exposure;
* invent a new trade;
* retry an order in a way that creates duplicate exposure.

Its job is execution.

The architectural boundary is:

```text
Alpha
   ↓
Trade Request
   ↓
Risk Engine
   ↓
Approved Order Intent
   ↓
Execution Engine
   ↓
Exchange Adapter
   ↓
Exchange
```

The Execution Engine therefore acts as a controlled state machine rather than a strategy engine.

---

# 39. Approved Order Intent

The Risk Engine must produce an immutable **Approved Order Intent**.

Conceptually:

```text
ApprovedOrderIntent

intent_id
decision_id
symbol
side
quantity
maximum_quantity
order_type
limit_price
time_in_force
created_at
expires_at
risk_configuration_version
strategy_id
alpha_id
```

The Execution Engine consumes this object.

Once created, the execution layer must not modify the risk-approved quantity upward.

The following transformation is allowed:

```text
Approved:
1.000 ETH

Executed:
0.700 ETH
```

The following transformation is forbidden:

```text
Approved:
1.000 ETH

Executed:
1.200 ETH
```

Any additional quantity requires a new Risk Engine approval.

---

# 40. Execution Authority

Execution authority is deliberately narrow.

The Execution Engine may determine:

* whether an approved order can currently be submitted;
* the appropriate exchange order representation;
* exchange-compatible quantity rounding;
* exchange-compatible price rounding;
* whether an order has already been submitted;
* whether an order is still pending;
* whether an order has filled;
* whether an order requires cancellation;
* whether an execution failure requires escalation.

The Execution Engine may not determine:

* maximum account exposure;
* maximum trade risk;
* maximum position size;
* strategy direction;
* alpha confidence;
* whether a new position should exist.

Those decisions belong upstream.

---

# 41. Execution Lifecycle

Every approved order follows a deterministic lifecycle.

```text
APPROVED
    ↓
CREATED
    ↓
SUBMITTING
    ↓
SUBMITTED
    ↓
PARTIALLY_FILLED
    ↓
FILLED
```

Alternative paths include:

```text
SUBMITTED
    ↓
CANCEL_REQUESTED
    ↓
CANCELLED
```

or:

```text
SUBMITTED
    ↓
REJECTED
```

or:

```text
SUBMITTED
    ↓
EXPIRED
```

Operational failures may create:

```text
UNKNOWN
```

state.

UNKNOWN is a safety state and requires reconciliation before another order affecting the same exposure can be submitted.

---

# 42. Order State Machine

The canonical state machine is:

```text
                    ┌──────────────┐
                    │   APPROVED   │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │    CREATED   │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  SUBMITTING  │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
        ┌───────────┐             ┌──────────┐
        │ SUBMITTED │             │ REJECTED │
        └─────┬─────┘             └──────────┘
              │
       ┌──────┴───────┐
       │              │
       ▼              ▼
PARTIALLY_FILLED    FILLED
       │
       │
       ▼
CANCEL_REQUESTED
       │
       ▼
   CANCELLED
```

Every transition must be persisted.

The system must not rely exclusively on in-memory order state.

---

# 43. Order Identity

Every order requires multiple identifiers.

At minimum:

```text
intent_id
client_order_id
exchange_order_id
```

### intent_id

Identifies the internal trading intention.

### client_order_id

Identifies the order submitted by QuantOS.

### exchange_order_id

Identifies the order assigned by the exchange.

These identifiers must never be treated as interchangeable.

Example:

```text
intent_id:
INT-20260807-000123

client_order_id:
QOS-20260807-000123

exchange_order_id:
123456789
```

The mapping must be persisted.

---

# 44. Client Order ID

QuantOS shall generate deterministic, unique client order IDs.

The ID must allow the system to correlate:

```text
Trade Intent
      ↓
Risk Decision
      ↓
Order
      ↓
Exchange Execution
```

Client order IDs must not contain information that could exceed exchange limits.

The implementation must respect exchange-specific client-order-ID constraints.

---

# 45. Idempotency

Idempotency is mandatory.

A network timeout after order submission does not mean the order was not created.

Example:

```text
QuantOS
   ↓
Submit Order
   ↓
Exchange accepts
   ↓
Network timeout
   ↓
QuantOS receives no response
```

The dangerous response is:

```text
"Retry immediately"
```

because the original order may already exist.

Correct behavior:

```text
Submission uncertain
        ↓
Do NOT blindly retry
        ↓
Query/reconcile existing order
        ↓
Determine actual exchange state
        ↓
Continue
```

---

# 46. Duplicate Order Prevention

Before submitting an order, the Execution Engine must verify that the corresponding intent has not already been submitted.

Conceptually:

```text
if intent.status in
    SUBMITTED
    PARTIALLY_FILLED
    FILLED
    CANCEL_REQUESTED
    CANCELLED:
        do not submit again
```

If the intent is already associated with an exchange order:

```text
No second submission.
```

This is a hard invariant.

---

# 47. Retry Policy

Retries must be classified.

Not every error is retryable.

## Retryable Examples

Potentially retryable:

* temporary network timeout;
* temporary DNS failure;
* exchange 5xx response;
* transient connection reset;
* temporary rate-limit response after required delay.

## Non-Retryable Examples

Do not blindly retry:

* insufficient balance;
* invalid quantity;
* invalid price;
* minimum-notional failure;
* invalid symbol;
* invalid API credentials;
* rejected order;
* risk approval expired.

Execution errors must therefore be classified as:

```text
TRANSIENT
PERMANENT
UNKNOWN
```

---

# 48. Unknown Execution State

UNKNOWN is more dangerous than REJECTED.

A rejected order is known not to exist.

An unknown order may or may not exist.

Therefore:

```text
REJECTED
    → safe to evaluate new submission

UNKNOWN
    → reconcile first
```

If order status cannot be established:

```text
Trading on affected symbol
        ↓
PAUSED
```

until reconciliation succeeds.

---

# 49. Maximum Retry Count

V1 shall use a bounded retry mechanism.

No infinite retry loop is permitted.

Conceptually:

```text
attempt = 1
attempt <= configured_max_attempts
```

After the maximum number of attempts:

```text
Execution State
    ↓
FAILED / UNKNOWN
```

depending on whether exchange state is known.

Repeated failures may trigger the circuit breaker described in Part 1.

---

# 50. Order Expiration

Approved order intents should have an expiration timestamp.

Example:

```text
created_at:
12:00:00

expires_at:
12:00:05
```

If the order has not been submitted before expiration:

```text
APPROVED
    ↓
EXPIRED
```

No submission is permitted.

This prevents stale alpha signals from becoming new positions.

---

# 51. Approval Freshness

Risk approval is time-sensitive.

A valid approval from five minutes ago may no longer represent current:

* price;
* volatility;
* account exposure;
* market conditions;
* portfolio state.

Therefore each Approved Order Intent must contain an expiry.

Conceptually:

```text
current_time > expires_at
    ↓
INVALID
```

The order must return to the Risk Engine for re-evaluation.

---

# 52. Market Orders

Market orders prioritize execution certainty over price certainty.

Conceptually:

```text
Market Order

Goal:
Execute immediately at available market prices.
```

Market orders are permitted only where the strategy configuration explicitly allows them.

The Execution Engine must estimate expected execution cost before submission when sufficient market data exists.

Expected cost may include:

```text
spread
+
estimated slippage
+
fees
```

If the estimated cost violates configured execution constraints:

```text
REJECT / REPRICE / DEFER
```

depending on configuration.

---

# 53. Limit Orders

Limit orders prioritize price control over execution certainty.

A limit order specifies:

```text
price
quantity
side
time_in_force
```

The order may:

```text
Fill
Partially Fill
Remain Open
Expire
Cancel
```

The Execution Engine must track the actual state rather than assuming submission equals execution.

---

# 54. V1 Order Type Policy

V1 should minimize execution complexity.

The preferred initial order types are:

```text
LIMIT
MARKET
```

No advanced order types are required for V1.

Avoid unnecessary support for:

* trailing orders;
* iceberg orders;
* complex conditional orders;
* exchange-specific proprietary order types.

The objective is operational reliability rather than feature breadth.

---

# 55. Market Order Safety

A market order must never be treated as:

```text
Guaranteed fill at reference price
```

Instead:

```text
Reference Price
      ↓
Estimated Execution Range
      ↓
Potential Slippage
      ↓
Actual Fill Price
```

The actual fill price becomes part of the authoritative portfolio state.

Risk calculations must use actual fills after execution.

---

# 56. Slippage

Slippage is the difference between expected and actual execution price.

For a buy:

```text
Slippage
=
Actual Fill Price
-
Reference Price
```

For a sell:

```text
Slippage
=
Reference Price
-
Actual Fill Price
```

Normalized slippage:

```text
Slippage %
=
Absolute Slippage
/
Reference Price
```

The Execution Engine must record:

```text
reference_price
average_fill_price
slippage_absolute
slippage_percentage
```

---

# 57. Slippage Limits

The system may define a maximum acceptable execution deviation.

Example:

```text
Maximum Slippage = 0.20%
```

If estimated execution conditions exceed this threshold:

```text
Order
    ↓
Rejected / Deferred
```

For an already-submitted order, actual slippage may exceed the expected threshold due to market movement.

The system must distinguish:

```text
Pre-trade slippage protection
```

from:

```text
Post-trade slippage measurement
```

The latter is an observability metric and must not rewrite historical execution results.

---

# 58. Spread Protection

Before submitting a marketable order, the Execution Engine should evaluate the current spread.

Conceptually:

```text
Spread %
=
(Ask - Bid)
/
Mid Price
```

If spread exceeds the configured maximum:

```text
Do Not Trade
```

unless the order is an emergency position reduction.

Emergency exits have different priorities.

The system must not refuse to reduce a dangerous position merely because normal entry spread limits are exceeded.

---

# 59. Emergency Execution

Normal execution rules are subordinate to account safety.

Example:

```text
Position exists
Daily loss limit triggered
Market volatility extreme
```

New entries are forbidden.

However:

```text
Close Position
```

remains permitted.

Emergency execution may therefore accept:

* larger slippage;
* market orders;
* aggressive cancellation;
* immediate liquidation.

The purpose is to reduce risk rather than preserve execution quality.

---

# 60. Fees

Trading fees must be included in execution accounting.

The system must record:

```text
fee_amount
fee_currency
fee_rate
```

where available.

Actual fee information from the exchange is authoritative.

Estimated fees may be used before execution.

Actual fees must replace estimates after execution.

---

# 61. Effective Execution Price

For a filled order, the system should calculate an effective execution price.

For a buy:

```text
Effective Cost
=
Gross Notional
+
Fees
```

For a sell:

```text
Effective Proceeds
=
Gross Notional
-
Fees
```

The exact accounting model depends on fee currency.

The Portfolio layer must use exchange-reported fills and fees whenever available.

---

# 62. Partial Fills

Partial fills are first-class execution states.

Example:

```text
Requested:
1.000 BTC

Fill 1:
0.300 BTC

Remaining:
0.700 BTC
```

The system must not treat:

```text
0.300 BTC
```

as a completed 1 BTC trade.

The order remains:

```text
PARTIALLY_FILLED
```

until:

```text
FILLED
```

or:

```text
CANCELLED
```

---

# 63. Partial Fill Accounting

Each fill must be recorded individually where the exchange provides fill-level data.

Example:

```text
Fill #1
quantity = 0.300
price = 100000
fee = X

Fill #2
quantity = 0.200
price = 100050
fee = Y
```

The system then calculates:

```text
Total Filled Quantity
Average Fill Price
Total Fees
Remaining Quantity
```

Average fill price:

```text
Average Price
=
Σ(quantity × price)
/
Σ(quantity)
```

The order's portfolio impact must use actual fills.

---

# 64. Remaining Quantity

For every open order:

```text
Remaining Quantity
=
Approved Quantity
-
Filled Quantity
```

The value must never be negative.

If the exchange reports inconsistent state:

```text
Reconciliation Required
```

The system must not invent a correction.

---

# 65. Cancellation

An order may be cancelled when:

* its alpha signal expires;
* its execution timeout is reached;
* market conditions become unacceptable;
* risk state changes;
* the strategy exits;
* a circuit breaker activates.

Cancellation is itself an asynchronous exchange operation.

Therefore:

```text
CANCEL_REQUESTED
```

is a real state.

It must not immediately become:

```text
CANCELLED
```

until the exchange confirms cancellation.

---

# 66. Cancel-and-Replace

V1 should avoid unnecessary cancel-and-replace complexity.

If repricing is required:

```text
Existing Order
    ↓
Cancel
    ↓
Confirm Cancellation
    ↓
New Risk Evaluation
    ↓
New Approved Order
    ↓
Submit
```

The system must not repeatedly reprice an existing order without re-evaluating risk.

Every new order is a new exposure decision.

---

# 67. Order Timeout

Open orders require a maximum lifetime.

Conceptually:

```text
Order Submitted
       ↓
Timer
       ↓
Execution Timeout
       ↓
Cancel Request
```

The timeout is configuration-driven.

Example:

```text
entry_order_timeout_seconds
exit_order_timeout_seconds
```

Exit orders may have different requirements because reducing exposure has higher priority than opening exposure.

---

# 68. Entry vs Exit Priority

The system must distinguish:

```text
ENTRY
EXIT
```

orders.

### Entry

Creates or increases exposure.

Must satisfy full risk controls.

### Exit

Reduces or eliminates exposure.

Must remain available even when new trading is halted.

This distinction is critical.

For example:

```text
Risk State = HALTED

BUY BTC
    → REJECT

SELL existing BTC
    → ALLOWED
```

The exact side depends on the current position.

---

# 69. Reduce-Only Behavior

When reducing an existing position, the Execution Engine must ensure that the order cannot unintentionally reverse the position.

Conceptually:

```text
Current Position:
+0.01 BTC

Exit Request:
Sell 0.01 BTC

Allowed:
0.01 BTC

Not allowed:
0.02 BTC
```

If exchange functionality supports explicit reduce-only semantics for the selected market, it should be used where appropriate.

For Spot V1, the implementation must still enforce the equivalent invariant internally.

---

# 70. Position Reversal

V1 should not perform implicit position reversal.

For example:

```text
Current:
+0.01 BTC

Signal:
SHORT

```

must not automatically produce:

```text
Sell 0.02 BTC
```

Instead:

```text
1. Close existing position
2. Confirm execution
3. Re-evaluate new short/exit logic
```

For Spot, this distinction is especially important because short exposure is not represented in the same way as a futures position.

V1 therefore treats position reversal as a separate decision.

---

# 71. Quantity Precision

Exchange quantity constraints must be enforced.

The exchange may define:

```text
minimum quantity
maximum quantity
step size
minimum notional
```

The Execution Engine must normalize approved quantities to exchange-supported precision.

Example:

```text
Risk Approved:
0.001234567 BTC

Exchange Step:
0.000001 BTC

Submitted:
0.001234 BTC
```

Rounding must never increase risk.

Therefore the system should use conservative rounding.

---

# 72. Price Precision

Limit prices must similarly conform to exchange tick size.

Example:

```text
Risk Reference:
100000.123456

Tick Size:
0.01

Submitted:
100000.12
```

The rounding direction must be appropriate to the order side.

The system must not round in a way that unintentionally worsens the permitted execution price beyond configured constraints.

---

# 73. Minimum Notional

An exchange may reject orders below a minimum notional.

The system must validate this before submission where exchange metadata is available.

If:

```text
Approved Notional
<
Exchange Minimum
```

the order must be rejected unless the quantity can be adjusted within the already-approved risk budget.

The system must never increase notional merely to satisfy minimum order requirements.

Example:

```text
Risk Maximum:
4.00 USDT

Exchange Minimum:
5.00 USDT

Result:
REJECT
```

---

# 74. Maximum Notional

Exchange maximums must also be respected.

If:

```text
Risk Approved:
10 USDT

Exchange Maximum:
100 USDT
```

then:

```text
10 USDT
```

is valid.

If:

```text
Risk Approved:
200 USDT

Exchange Maximum:
100 USDT
```

the order must not be automatically split into two orders unless the Risk Engine explicitly approves that execution structure.

The Execution Engine cannot create additional exposure through order splitting.

---

# 75. Order Splitting

V1 should avoid discretionary order splitting.

One Risk-approved order should correspond to one logical execution intent.

If future execution optimization requires:

```text
Parent Order
    ↓
Child Orders
```

the parent quantity must remain the hard maximum.

For V1:

```text
1 Intent
    ↓
1 Exchange Order
```

is preferred.

This substantially simplifies reconciliation and failure handling.

---

# 76. Time in Force

V1 should support only the minimum required time-in-force modes.

Recommended initial modes:

```text
GTC
IOC
```

depending on exchange capabilities and strategy requirements.

The selected mode must be part of the Approved Order Intent.

The Execution Engine must not silently switch:

```text
GTC → IOC
```

because that changes execution behavior.

A materially different execution instruction requires new approval or an explicitly permitted execution transformation.

---

# 77. Execution Price Reference

Every order should retain the market reference used during risk evaluation.

Example:

```text
reference_price = 100000
```

This allows the system to calculate:

```text
Expected Slippage
Actual Slippage
Execution Drift
```

The reference price must not be overwritten by the final fill price.

Historical execution analysis depends on preserving both.

---

# 78. Execution Drift

Execution drift measures how market conditions changed between:

```text
Risk Decision
```

and:

```text
Actual Execution
```

Conceptually:

```text
Execution Drift
=
Actual Execution Price
-
Risk Reference Price
```

This metric is useful for determining whether:

* signals become stale too quickly;
* execution latency is excessive;
* market conditions are unsuitable;
* order timeout values need adjustment.

Execution drift does not retroactively modify the Risk decision.

---

# 79. Execution Latency

The Execution Engine must measure timestamps for:

```text
signal_timestamp
risk_decision_timestamp
order_created_timestamp
submit_timestamp
exchange_ack_timestamp
first_fill_timestamp
final_fill_timestamp
```

This allows calculation of:

```text
Risk Latency
Submission Latency
Exchange Latency
Time To First Fill
Time To Full Fill
Total Execution Time
```

Latency is a production metric.

It is not merely a debugging metric.

---

# 80. Execution Events

The Execution Engine should emit structured events.

Core events include:

```text
ORDER_INTENT_CREATED
ORDER_SUBMIT_STARTED
ORDER_SUBMITTED
ORDER_REJECTED
ORDER_PARTIALLY_FILLED
ORDER_FILLED
ORDER_CANCEL_REQUESTED
ORDER_CANCELLED
ORDER_EXPIRED
ORDER_UNKNOWN
EXECUTION_ERROR
```

Each event should contain enough information to reconstruct the order lifecycle.

---

# 81. Event Ordering

Execution events must preserve causal order.

Example:

```text
ORDER_SUBMIT_STARTED
        ↓
ORDER_SUBMITTED
        ↓
ORDER_PARTIALLY_FILLED
        ↓
ORDER_FILLED
```

The system must not report:

```text
ORDER_FILLED
```

before:

```text
ORDER_SUBMITTED
```

unless an exchange reconciliation process discovers a historical fill after a restart.

In that case the event should be marked as reconciliation-derived.

---

# 82. Exchange Adapter

The Execution Engine must communicate with the exchange through an adapter.

Architecture:

```text
Execution Engine
       ↓
Exchange Adapter Interface
       ↓
Binance Adapter
       ↓
Binance API
```

The Execution Engine should not contain Binance-specific API calls.

This separation enables:

```text
PaperExchange
BinanceExchange
FutureExchange
```

without changing execution business logic.

---

# 83. Exchange Adapter Contract

The adapter should expose a small interface.

Conceptually:

```text
get_account_state()

get_open_orders(symbol)

get_order(order_id)

submit_order(order)

cancel_order(order_id)

get_recent_fills(symbol)

get_exchange_metadata(symbol)

get_server_time()
```

The exact implementation belongs to the codebase.

The interface should remain exchange-neutral.

---

# 84. Exchange Metadata

The adapter must provide exchange constraints required for safe order construction.

At minimum:

```text
symbol
status
base_asset
quote_asset
price_tick_size
quantity_step_size
minimum_quantity
maximum_quantity
minimum_notional
maximum_notional
```

These values should be cached but periodically refreshed.

The Execution Engine must not assume exchange rules are static forever.

---

# 85. Server Time

Where supported, the adapter should expose exchange server time.

This helps prevent:

* timestamp drift;
* invalid request timestamps;
* incorrect expiration handling;
* synchronization errors.

The system should maintain a measured clock offset rather than blindly trusting local time.

---

# 86. Rate Limits

Exchange API rate limits must be respected.

The adapter should provide centralized rate-limit handling.

Execution code must not independently spam the exchange.

Rate-limit failures should be classified as:

```text
TRANSIENT
```

where appropriate.

Repeated rate-limit violations may trigger an operational warning or circuit breaker.

---

# 87. WebSocket vs REST

V1 may use both REST and WebSocket interfaces.

Recommended responsibilities:

```text
WebSocket
    ↓
Real-time order/account updates

REST
    ↓
Submission
Queries
Recovery
Reconciliation
```

WebSocket data must not be considered permanently authoritative merely because it is faster.

After reconnects, REST reconciliation should be used to establish a known state.

---

# 88. Exchange Acknowledgement

An order submission response is not necessarily equivalent to a fill.

Example:

```text
Exchange:
ORDER ACCEPTED
```

means:

```text
Order exists
```

not:

```text
Position changed by full quantity
```

The system must distinguish:

```text
Accepted
Open
Partially Filled
Filled
Cancelled
Rejected
```

This distinction is mandatory for correct portfolio accounting.

---

# 89. Fill Authority

For live trading, exchange-reported fills are authoritative.

The system must not infer fills solely from:

* market price;
* order submission;
* local assumptions;
* websocket silence;
* elapsed time.

A position changes because an execution occurred.

The exchange must provide evidence of that execution.

---

# 90. Local Execution Ledger

QuantOS should maintain an internal execution ledger.

Each fill should record:

```text
execution_id
order_id
intent_id
symbol
side
quantity
price
fee
fee_currency
timestamp
source
```

The ledger is append-oriented.

Historical fills must not be silently overwritten.

Corrections should be represented through reconciliation events.

---

# 91. Average Fill Price

For multiple fills:

```text
Average Fill Price
=
Σ(fill_quantity × fill_price)
/
Σ(fill_quantity)
```

Example:

```text
Fill A:
0.40 BTC @ 100000

Fill B:
0.60 BTC @ 100100

Average:
(0.40 × 100000 + 0.60 × 100100)
/
1.00

= 100060
```

The portfolio layer must use the actual average execution price.

---

# 92. Execution Cost Accounting

Execution cost should include:

```text
Trading Fees
+
Slippage
+
Spread Cost
+
Potential Funding/Borrow Costs
```

For Spot V1, the primary costs are:

```text
Trading Fees
Spread
Slippage
```

These costs must eventually feed performance analysis.

A strategy that appears profitable before execution costs may be unprofitable after costs.

---

# 93. Pre-Trade Cost Filter

Where enough market data exists, the Execution Engine may estimate:

```text
Expected Fee
Expected Spread Cost
Expected Slippage
```

and calculate:

```text
Expected Execution Cost
```

If:

```text
Expected Execution Cost
>
Configured Maximum
```

the trade may be rejected or deferred.

However, this is an execution constraint, not an alpha decision.

---

# 94. Execution Failure Escalation

Repeated execution failures should escalate.

Example:

```text
1 failure
    ↓
Retry / Reconcile

2 failures
    ↓
Operational warning

N failures
    ↓
Symbol execution paused

Severe failure
    ↓
Account circuit breaker
```

Exact thresholds belong in configuration.

The system should prefer a temporary halt over repeatedly sending potentially unsafe orders.

---

# 95. Order Submission Atomicity

Risk approval and order submission are separate operations.

Therefore a small race window exists:

```text
Risk Approval
      ↓
Market Changes
      ↓
Order Submission
```

The system cannot eliminate this completely.

Instead, it controls the risk through:

* short approval TTL;
* exposure reservation;
* price/slippage checks;
* execution limits;
* reconciliation.

The approval is therefore a bounded authorization, not a permanent permission.

---

# 96. Exposure Reservation During Submission

Once an Approved Order Intent is created, the approved exposure should be reserved internally.

Example:

```text
Account Equity = 20 USDT

Existing Exposure = 5 USDT

Approved Order = 4 USDT

Reserved Exposure = 4 USDT
```

Until the order resolves:

```text
Existing Exposure
+
Reserved Exposure
```

must be used for subsequent risk decisions.

This prevents concurrent signals from consuming the same capital.

---

# 97. Concurrent Order Requests

Multiple alpha signals may arrive before the first order completes.

The Risk Engine and Execution Engine must therefore support serialization or atomic reservation.

Example:

```text
Signal A → Risk approves 5 USDT
Signal B → arrives immediately
Signal C → arrives immediately
```

Signal B and C must see:

```text
5 USDT reserved
```

not:

```text
0 USDT reserved
```

Otherwise concurrent signals can collectively exceed account limits.

---

# 98. Symbol-Level Serialization

For V1, execution should serialize state-changing orders for the same symbol.

Conceptually:

```text
BTCUSDT
    Order A
       ↓
    Resolve
       ↓
    Order B
       ↓
    Resolve
```

This reduces race conditions involving:

* position size;
* pending orders;
* cancellation;
* reversal;
* reconciliation.

Parallel execution across independent symbols may be supported later.

---

# 99. Account-Level Safety Lock

Certain operations require an account-level lock.

Examples:

```text
Emergency liquidation
Full reconciliation
API credential failure
Unexpected position
Global circuit breaker
```

During the lock:

```text
New Entries
    → Forbidden
```

Existing positions may still be managed by the emergency path.

---

# 100. Execution and Risk Revalidation

Before submitting an order, the Execution Engine should confirm that the Approved Order Intent is still valid.

At minimum:

```text
current_time < expires_at
risk_state permits execution
intent has not already executed
```

For particularly sensitive orders, a final Risk Engine revalidation may be required.

If revalidation fails:

```text
Do Not Submit
```

---

# 101. Stale Orders

An order becomes stale when its original assumptions are no longer valid.

Examples:

```text
Approval expired
Risk state changed
Symbol halted
Market data invalid
Account state changed materially
```

A stale order must not be submitted merely because it was once approved.

The correct behavior is:

```text
Invalidate
    ↓
Return to Risk Engine
    ↓
Re-evaluate
```

---

# 102. Execution Journal

The Execution Engine must maintain a durable execution journal.

The journal should record:

```text
Intent Created
Risk Approved
Order Created
Submission Attempt
Exchange Response
Fill
Cancellation
Failure
Reconciliation
```

The journal should support reconstruction of:

```text
What did QuantOS intend?
What did Risk approve?
What did QuantOS submit?
What did the exchange accept?
What actually filled?
```

This is essential for production debugging.

---

# 103. No Hidden Execution

The Execution Engine must not create exchange orders outside the tracked lifecycle.

Forbidden:

```text
Direct API call
without
Order Intent
```

Every live order must have:

```text
intent_id
risk_decision
client_order_id
```

If an exchange order exists without an internal record:

```text
UNEXPECTED ORDER
```

and reconciliation is required.

---

# 104. Unexpected Exchange Orders

If the exchange reports an order that QuantOS does not recognize:

```text
Unknown Exchange Order
```

the system must not assume it belongs to QuantOS.

The account enters a protected state.

Possible actions:

```text
Stop new entries
Fetch order details
Reconcile account
Determine origin
Resolve state
Resume only after safety restored
```

---

# 105. Unexpected Fills

An unexpected fill is more severe than an unknown open order.

Example:

```text
Exchange:
BTC bought

QuantOS:
No corresponding order
```

The system must treat this as:

```text
CRITICAL STATE
```

because account exposure has changed without a known internal cause.

New trading must stop until reconciliation establishes the source and correct portfolio state.

---

# 106. Execution Recovery

After application restart, the Execution Engine must not assume:

```text
No in-memory orders
=
No open orders
```

Startup recovery must query the exchange.

Conceptually:

```text
Application Start
      ↓
Load Local State
      ↓
Query Exchange
      ↓
Compare
      ↓
Reconcile
      ↓
Establish Authoritative State
      ↓
Enable Trading
```

Trading should not resume before reconciliation completes.

---

# 107. Recovery Ordering

Startup recovery should follow this sequence:

```text
1. Connect to exchange
2. Validate server time
3. Retrieve account balances
4. Retrieve open orders
5. Retrieve recent fills
6. Retrieve current positions
7. Compare with local state
8. Resolve discrepancies
9. Rebuild reservations
10. Rebuild execution state
11. Evaluate risk state
12. Enable trading
```

If any critical step fails:

```text
Trading remains disabled.
```

---

# 108. Paper Trading Parity

Paper execution must implement the same logical interface as live execution.

```text
ExecutionEngine
      ↓
ExchangeAdapter
      ├── PaperAdapter
      └── BinanceAdapter
```

The strategy and risk code should not know which adapter is active.

This prevents paper trading from becoming a completely different system.

---

# 109. Paper Fill Model

The paper adapter should model at least:

```text
Fees
Spread
Slippage
Partial fills
Latency
Order rejection
```

It does not need to reproduce the exchange perfectly.

Its purpose is to expose execution logic bugs before capital is used.

The more realistic the execution model, the more useful the simulation.

---

# 110. Live/Paper Contract

The following behavior must remain identical:

```text
Risk Decision
Order Intent
Order State Machine
Position Accounting
Exposure Reservation
Circuit Breakers
Execution Events
```

Only the exchange interaction differs.

This creates:

```text
Paper
    ≈
Live
```

at the architectural level.

---

# 111. Execution Safety Invariants

The following invariants are mandatory.

### Invariant 1

No exchange order without a valid Risk approval.

### Invariant 2

No exchange order may exceed Risk-approved quantity.

### Invariant 3

Expired approvals cannot be submitted.

### Invariant 4

Unknown orders must be reconciled.

### Invariant 5

Unknown execution state cannot trigger blind retries.

### Invariant 6

Duplicate intents cannot create duplicate orders.

### Invariant 7

Partial fills must be accounted for.

### Invariant 8

Exchange fills are authoritative for live positions.

### Invariant 9

Cancellation is asynchronous until confirmed.

### Invariant 10

Execution retries must be bounded.

### Invariant 11

Order precision must not increase risk.

### Invariant 12

Emergency exits remain available during normal trading halts.

### Invariant 13

Unexpected exchange positions or fills halt new trading.

### Invariant 14

Startup recovery must reconcile exchange state before trading resumes.

### Invariant 15

All live orders must be traceable to an internal intent.

---

# 112. Execution Failure Philosophy

The Execution Engine must distinguish between:

```text
Unable to execute
```

and:

```text
Unsure whether execution occurred
```

The first is an execution failure.

The second is a state-integrity failure.

The second is more dangerous.

Therefore:

```text
Known Failure
    ↓
Controlled Recovery

Unknown State
    ↓
Trading Halt
    ↓
Reconciliation
```

This distinction is fundamental to safe automated trading.

---

# 113. Execution Sequence — Normal Entry

The normal entry flow is:

```text
Alpha Signal
    ↓
Trade Request
    ↓
Risk Evaluation
    ↓
Approved Order Intent
    ↓
Exposure Reservation
    ↓
Execution Precheck
    ↓
Exchange Submission
    ↓
Exchange Acknowledgement
    ↓
Order Open
    ↓
Fill Event
    ↓
Portfolio Update
    ↓
Risk State Update
```

Each transition must be observable.

---

# 114. Execution Sequence — Failed Submission

```text
Approved Intent
      ↓
Submission
      ↓
Network Timeout
      ↓
UNKNOWN
      ↓
Query Exchange
      │
      ├── Order Exists
      │       ↓
      │   Continue Tracking
      │
      └── Order Does Not Exist
              ↓
          Controlled Retry
```

The system must never assume timeout equals failure.

---

# 115. Execution Sequence — Partial Fill

```text
Approved:
1.0 BTC

        ↓

Fill:
0.4 BTC

        ↓

Remaining:
0.6 BTC

        ↓

Order remains OPEN

        ↓

Fill:
0.6 BTC

        ↓

FILLED
```

Portfolio state is updated after each confirmed fill.

---

# 116. Execution Sequence — Timeout

```text
Order Open
    ↓
Execution Timeout
    ↓
Cancel Request
    ↓
Cancellation Confirmed
    ↓
Remaining Quantity
    ↓
Order Closed
```

If cancellation is uncertain:

```text
CANCEL_REQUESTED
    ↓
UNKNOWN
    ↓
RECONCILIATION
```

The system must not create a replacement order until the original state is known.

---

# 117. Execution Sequence — Risk Halt

Suppose:

```text
Order A
    partially filled

Daily Loss Limit
    triggered
```

The system enters:

```text
HALTED
```

New entries are blocked.

However:

```text
Remaining Order A
```

must be evaluated.

The system may:

```text
Cancel remaining entry quantity
```

if the order would increase exposure.

Existing filled position remains manageable.

This prevents a risk halt from accidentally leaving an unwanted pending entry order active.

---

# 118. Execution Sequence — Emergency Exit

```text
Critical Risk Event
        ↓
EMERGENCY
        ↓
Stop New Entries
        ↓
Cancel Exposure-Increasing Orders
        ↓
Evaluate Existing Positions
        ↓
Submit Protective Exit
        ↓
Confirm Fill
        ↓
Reconcile
```

Emergency execution prioritizes risk reduction over normal execution optimization.

---

# 119. Execution Configuration

Execution behavior must be configuration-driven.

Conceptually:

```text
execution:
    order_type
    time_in_force
    approval_ttl_seconds
    entry_timeout_seconds
    exit_timeout_seconds
    max_slippage_pct
    max_spread_pct
    max_retries
    retry_backoff_seconds
    use_websocket
    reconciliation_interval
```

Risk configuration remains separate:

```text
risk:
    max_position_pct
    max_symbol_exposure_pct
    max_account_exposure_pct
    daily_loss_limit_pct
    max_drawdown_pct
    safety_reserve_pct
```

The separation prevents execution settings from becoming hidden risk parameters.

---

# 120. Execution Logging

Execution logs must be structured.

Example:

```text
{
    "event": "ORDER_SUBMITTED",
    "intent_id": "...",
    "client_order_id": "...",
    "exchange_order_id": "...",
    "symbol": "BTCUSDT",
    "side": "BUY",
    "quantity": 0.001,
    "timestamp": "..."
}
```

Logs must contain identifiers that allow correlation across:

```text
Alpha
Risk
Execution
Portfolio
Exchange
```

---

# 121. Execution Metrics

V1 should expose at least:

```text
orders_submitted
orders_filled
orders_cancelled
orders_rejected
orders_expired
orders_unknown
partial_fill_rate
fill_rate
average_slippage
average_execution_latency
execution_error_rate
retry_count
reconciliation_count
```

These metrics should be available separately for:

```text
symbol
side
order_type
strategy
time period
```

where practical.

---

# 122. Execution Quality Metrics

Execution quality should eventually include:

```text
Implementation Shortfall
Arrival Price
VWAP Comparison
Spread Paid
Slippage
Fee Cost
Time To Fill
Fill Probability
```

V1 does not require a sophisticated transaction-cost model.

It must, however, preserve the raw information needed to build one later.

---

# 123. Execution Auditability

For every completed trade, the system should be able to answer:

```text
Why did we trade?

What did Alpha propose?

What did Risk approve?

What quantity was approved?

What order was submitted?

When was it submitted?

What price did we receive?

How much filled?

What fees were charged?

Was there slippage?

What position resulted?
```

If these questions cannot be answered from persisted state and logs, the execution system is incomplete.

---

# 124. V1 Execution Philosophy

V1 deliberately avoids sophisticated execution optimization.

The first production objective is:

```text
Correctness
    >
Reliability
    >
Observability
    >
Execution Efficiency
```

A simple execution engine that:

* submits the correct order;
* avoids duplicates;
* tracks fills;
* reconciles state;
* respects risk;
* handles failures;

is more valuable than an advanced execution optimizer that cannot be trusted.

---

# 125. Part 2 Completion Criteria

Part 2 is complete when the implementation satisfies:

* Approved Order Intent exists.
* Order lifecycle is deterministic.
* Client and exchange order IDs are tracked.
* Idempotency is enforced.
* Duplicate orders are prevented.
* Retry behavior is bounded.
* Unknown execution state is handled safely.
* Orders expire.
* Market and limit orders are supported.
* Slippage is measured and constrained.
* Spread protection exists.
* Fees are recorded.
* Partial fills are supported.
* Cancellation is asynchronous.
* Entry and exit orders are differentiated.
* Position increases cannot occur without risk approval.
* Exchange precision constraints are respected.
* Minimum and maximum notional rules are enforced.
* Exchange metadata is consumed through an adapter.
* REST/WebSocket responsibilities are separated.
* Exchange fills are authoritative.
* Execution events are persisted.
* Startup recovery is defined.
* Paper and live execution share the same logical contract.
* Unexpected orders/fills trigger reconciliation.
* Emergency exits remain possible during trading halts.
* Every live order can be traced back to a Risk-approved intent.

---

# 126. Boundary to Part 3

At the end of Part 2, QuantOS has defined:

```text
Risk Approval
      ↓
Order Intent
      ↓
Execution
      ↓
Exchange
      ↓
Fill
```

However, execution alone is not sufficient.

QuantOS must also know whether its internal representation of the account agrees with reality.

That is the responsibility of Part 3:

```text
Portfolio State
Reconciliation
Recovery
Failure Handling
Emergency Shutdown
```

The core principle continues:

> **If QuantOS cannot prove what the account currently owns, it must stop opening new risk.**

# QuantOS Core

## 006_RISK_EXECUTION_SPECIFICATION.md

**Version:** 0.1.0-alpha
**Status:** Draft V1
**Document:** 006 — Risk & Execution Specification
**Part:** 3 of 4
**Scope:** Portfolio State, Reconciliation & Failure Handling

---

# 127. Portfolio State

The Portfolio State layer represents QuantOS's current understanding of the trading account.

It must answer, at any point in time:

```text
What capital exists?

What positions exist?

What orders are open?

What orders have filled?

How much exposure exists?

How much capital is reserved?

What is the current PnL?

Is the internal state consistent with the exchange?
```

Portfolio State is therefore not merely a reporting component.

It is a safety-critical state model.

---

# 128. Source of Truth

For live trading, the exchange is authoritative for actual account state.

QuantOS maintains an internal representation for speed and orchestration.

Therefore:

```text
Exchange
    ↓
Authoritative Reality

QuantOS State
    ↓
Operational Representation
```

The internal state must continuously converge toward exchange state.

If the two disagree materially:

```text
Internal State ≠ Exchange State
        ↓
RECONCILIATION REQUIRED
```

The system must not assume its internal state is correct.

---

# 129. Portfolio State Model

The portfolio state should contain at least:

```text
PortfolioState

account_equity
available_balance
reserved_balance
cash_balance
positions
open_orders
pending_orders
realized_pnl
unrealized_pnl
fees
peak_equity
daily_start_equity
daily_pnl
drawdown
risk_state
last_reconciliation
state_version
```

For each symbol:

```text
PositionState

symbol
quantity
average_entry_price
market_price
notional
unrealized_pnl
realized_pnl
last_update
source
```

---

# 130. Position State

For Spot V1, a position is fundamentally an asset balance.

For example:

```text
BTCUSDT

BTC Balance:
0.001 BTC

USDT Balance:
15 USDT
```

The system may represent the BTC balance as an open position for risk purposes.

The abstraction should nevertheless remain compatible with future derivatives support.

---

# 131. Position Direction

V1 Spot supports:

```text
LONG
FLAT
```

It does not support native short positions.

Therefore:

```text
BTC quantity > 0
    → LONG

BTC quantity = 0
    → FLAT
```

A negative Spot balance is invalid for normal operation.

If the system observes an impossible negative position:

```text
CRITICAL RECONCILIATION ERROR
```

---

# 132. Position Quantity

Position quantity must be derived from confirmed account and fill information.

The system must not infer position quantity from:

```text
Signal
Order Intent
Expected Fill
```

Instead:

```text
Confirmed Fill
      ↓
Position Update
```

or:

```text
Exchange Balance
      ↓
Reconciliation
      ↓
Position State
```

---

# 133. Position Cost Basis

The system must maintain a consistent average entry cost.

For multiple acquisitions:

```text
Average Entry Price
=
Σ(quantity × execution price)
/
Σ(quantity)
```

Fees must be incorporated according to the configured accounting model.

The accounting method must remain consistent throughout the system.

---

# 134. Position Valuation

Current position value is:

```text
Position Notional
=
Position Quantity
×
Current Market Price
```

The market price must come from a valid market-data source.

If market price is unavailable:

```text
Position Value
    → UNKNOWN
```

The system must not substitute an arbitrary stale value for risk-critical calculations.

---

# 135. Stale Market Price

A market price is considered stale when it exceeds the configured freshness threshold.

Conceptually:

```text
current_time - market_data_timestamp
>
maximum_market_data_age
```

Then:

```text
Market Data State
    → STALE
```

New entries must be blocked.

Existing positions remain subject to risk-management procedures.

---

# 136. Account Equity

Account equity should represent the current estimated value of the account.

Conceptually:

```text
Account Equity
=
Cash
+
Σ(Position Quantity × Current Price)
```

Actual exchange-reported account information should be preferred where available.

Unrealized PnL must be incorporated consistently.

The calculation must use a single valuation timestamp.

---

# 137. Available Balance

Available balance differs from total balance.

Conceptually:

```text
Available Balance
=
Total Balance
-
Reserved Balance
```

Reserved balance includes capital committed to open orders.

This distinction is mandatory for risk calculations.

---

# 138. Reserved Capital

Reserved capital includes pending orders that could consume account funds.

Example:

```text
USDT Balance:
20 USDT

Open BUY Order:
5 USDT

Available USDT:
15 USDT
```

QuantOS must not treat the full 20 USDT as available for another trade.

---

# 139. Exposure State

Portfolio exposure must include both:

```text
Current Position Exposure
+
Pending Order Exposure
```

Example:

```text
BTC Position:
5 USDT

Pending BTC Buy:
3 USDT

Total BTC Exposure:
8 USDT
```

Risk decisions must use the combined figure.

---

# 140. Portfolio PnL

Portfolio PnL should be separated into:

```text
Realized PnL
Unrealized PnL
Fees
```

Conceptually:

```text
Net PnL
=
Realized PnL
+
Unrealized PnL
-
Applicable Costs
```

The exact accounting model must be consistent with the backtesting and reporting layers.

---

# 141. Realized PnL

Realized PnL occurs when an asset position is reduced or closed.

Example:

```text
Bought:
0.001 BTC @ 100000

Sold:
0.001 BTC @ 101000

Gross PnL:
1 USDT
```

Trading fees must then be accounted for.

The final realized PnL should reflect actual execution costs.

---

# 142. Unrealized PnL

Unrealized PnL represents the current value of an open position relative to its cost basis.

Conceptually:

```text
Unrealized PnL
=
Current Position Value
-
Position Cost Basis
```

The current market price must satisfy freshness requirements.

---

# 143. Peak Equity

Peak equity is used for drawdown calculations.

The system must maintain:

```text
Peak Equity
```

as the highest valid account equity observed.

If:

```text
Current Equity > Peak Equity
```

then:

```text
Peak Equity = Current Equity
```

Otherwise:

```text
Peak Equity remains unchanged.
```

A daily reset must not reset Peak Equity.

---

# 144. State Versioning

Portfolio state should have a monotonically increasing version.

Example:

```text
State Version:
100
    ↓
Fill Event
    ↓
101
    ↓
Balance Update
    ↓
102
```

This helps detect:

* stale consumers;
* missed updates;
* race conditions;
* duplicate processing.

---

# 145. Event-Driven State Updates

Portfolio state should primarily be updated through events.

Examples:

```text
ORDER_FILLED
ORDER_CANCELLED
BALANCE_UPDATED
POSITION_RECONCILED
FEE_RECORDED
MARKET_PRICE_UPDATED
```

Each event produces a deterministic state transition.

---

# 146. Event Idempotency

Processing the same event twice must not double-count its effect.

Example:

```text
FILL_EVENT_123
```

processed once:

```text
Position +0.001 BTC
```

processed twice:

```text
Position must still be +0.001 BTC
```

The system must maintain processed-event identifiers.

---

# 147. Duplicate Fill Protection

Duplicate fill processing is a critical accounting failure.

If the exchange reports:

```text
Execution ID:
ABC123
```

QuantOS must record that execution ID.

If the same execution is received again:

```text
Ignore duplicate
```

rather than:

```text
Apply fill again
```

---

# 148. Reconciliation

Reconciliation compares:

```text
Internal QuantOS State
```

against:

```text
Exchange State
```

The objective is to identify discrepancies before they become trading risk.

Conceptually:

```text
Internal
    ↓
Compare
    ↑
Exchange
```

---

# 149. Reconciliation Frequency

Reconciliation should occur:

* at startup;
* after reconnect;
* after execution uncertainty;
* after unexpected events;
* periodically during normal operation.

The periodic interval is configuration-driven.

Example:

```text
reconciliation_interval_seconds
```

The interval should be short enough to detect operational drift but not so aggressive that it violates exchange rate limits.

---

# 150. Reconciliation Scope

A reconciliation cycle should compare at least:

```text
Account Balances
Open Orders
Recent Orders
Recent Fills
Position Quantities
Order Statuses
```

Where applicable:

```text
Fees
Asset balances
Exchange status
```

---

# 151. Balance Reconciliation

Example:

```text
Internal USDT:
15.00

Exchange USDT:
14.98
```

This is a discrepancy.

Possible causes include:

* fees;
* unrecorded execution;
* external account activity;
* stale internal state;
* rounding.

The system must investigate rather than silently overwrite the difference.

---

# 152. Position Reconciliation

Example:

```text
Internal BTC:
0.002 BTC

Exchange BTC:
0.001 BTC
```

This is a critical discrepancy.

The system must:

```text
1. Stop new entries
2. Mark state as inconsistent
3. Fetch recent orders/fills
4. Determine cause
5. Rebuild position state
6. Recalculate risk
7. Resume only after consistency
```

---

# 153. Open Order Reconciliation

Internal:

```text
Order QOS-123
Status:
OPEN
```

Exchange:

```text
Order:
FILLED
```

The internal state is stale.

The system must:

```text
Fetch fills
    ↓
Apply fills
    ↓
Update position
    ↓
Update order
    ↓
Release reservation
```

---

# 154. Missing Exchange Order

Internal:

```text
Order QOS-123
OPEN
```

Exchange:

```text
Order not found
```

The system must not immediately assume cancellation.

Possible explanations include:

* order filled;
* order cancelled;
* order expired;
* historical query window;
* API inconsistency.

The system must query sufficient history to establish the final state.

---

# 155. Unknown Position

If QuantOS cannot determine the current position:

```text
Position State
    = UNKNOWN
```

then:

```text
New Entries
    = FORBIDDEN
```

The system may continue with safe recovery operations.

Unknown is not equivalent to zero.

This distinction is critical.

---

# 156. Unknown Balance

If available quote balance cannot be established:

```text
Available Balance
    = UNKNOWN
```

then:

```text
New Entries
    = FORBIDDEN
```

The system must not interpret unknown as:

```text
0
```

or:

```text
Unlimited
```

---

# 157. Unknown Open Orders

If open orders cannot be reliably retrieved:

```text
Open Orders
    = UNKNOWN
```

then:

```text
New Entries
    = FORBIDDEN
```

because QuantOS cannot safely determine reserved exposure.

---

# 158. Reconciliation States

The system should represent reconciliation explicitly.

```text
RECONCILED
RECONCILIATION_PENDING
RECONCILIATION_FAILED
STATE_INCONSISTENT
```

Only:

```text
RECONCILED
```

permits normal entry trading.

---

# 159. Reconciliation Severity

Discrepancies should be classified.

### INFORMATIONAL

Minor expected differences such as rounding.

### WARNING

State drift that can be resolved automatically.

### CRITICAL

Unknown or materially inconsistent account state.

Example:

```text
0.00000001 BTC difference
    → INFORMATIONAL

Missing order metadata
    → WARNING

Unexpected 0.01 BTC position
    → CRITICAL
```

---

# 160. Automatic Reconciliation

Some discrepancies can be repaired automatically.

Example:

```text
Exchange:
Order FILLED

Internal:
Order OPEN
```

The system can:

```text
Fetch fills
Apply fill
Update order
Recalculate portfolio
```

and return to:

```text
RECONCILED
```

---

# 161. Manual-Recovery Conditions

Some discrepancies must stop automated trading until reviewed.

Examples:

```text
Unexpected external order
Unexpected external fill
Unexpected asset balance
Large unexplained balance discrepancy
Impossible position state
Corrupted local state
Repeated reconciliation failure
```

The system must not fabricate a resolution.

---

# 162. External Account Activity

V1 assumes QuantOS has exclusive control over the trading account.

External activity includes:

* manual trades;
* manual transfers;
* deposits;
* withdrawals;
* other bots;
* third-party applications.

Such activity breaks the assumption of exclusive control.

If detected:

```text
Account State
    → UNSAFE
```

New trading must stop.

---

# 163. Single-Writer Principle

For V1:

> **QuantOS should be the only system writing trading activity to the production account.**

This dramatically simplifies:

* position accounting;
* reconciliation;
* exposure calculation;
* debugging;
* recovery.

Multi-agent or multi-bot execution against the same account is prohibited unless explicitly designed later.

---

# 164. Startup Recovery

Application startup is treated as an untrusted state.

The system must not assume that the previous process ended cleanly.

Startup sequence:

```text
Process Start
      ↓
Load Configuration
      ↓
Validate Configuration
      ↓
Connect Exchange
      ↓
Synchronize Clock
      ↓
Fetch Account
      ↓
Fetch Orders
      ↓
Fetch Fills
      ↓
Reconcile
      ↓
Restore Risk State
      ↓
Restore Portfolio
      ↓
Enable Trading
```

---

# 165. Startup Safety Gate

Trading must remain disabled until:

```text
Configuration Valid
AND
Exchange Connected
AND
Account State Known
AND
Open Orders Known
AND
Positions Known
AND
Reconciliation Successful
AND
Risk State Safe
```

Only then:

```text
TRADING_ENABLED
```

---

# 166. Crash Recovery

If QuantOS crashes during execution:

```text
Order Submission
    ↓
Process Crash
```

the next startup must not assume:

```text
Order = Failed
```

Instead:

```text
Startup
    ↓
Exchange Query
    ↓
Recover Order State
```

This is mandatory.

---

# 167. Restart During Partial Fill

Example:

```text
Approved:
1 BTC

Exchange:
0.4 BTC filled

Process:
crashes
```

After restart:

```text
Query Exchange
    ↓
Find 0.4 BTC fill
    ↓
Restore PARTIALLY_FILLED
    ↓
Remaining = 0.6 BTC
```

The system must not submit another 1 BTC order.

---

# 168. Restart During Cancellation

Example:

```text
Cancel Requested
    ↓
Process Crash
```

After restart:

```text
Query Exchange
    ↓
Order Status
```

Possible outcomes:

```text
CANCELLED
FILLED
PARTIALLY_FILLED
OPEN
UNKNOWN
```

The resulting state determines the next action.

---

# 169. Rebuild Reserved Capital

After restart, reserved capital must be reconstructed from actual open orders.

Do not restore reservations solely from local memory.

Conceptually:

```text
Exchange Open Orders
        ↓
Calculate Pending Notional
        ↓
Reserved Capital
```

This ensures the post-restart risk state matches reality.

---

# 170. Risk State Recovery

Risk state should not simply reset to:

```text
NORMAL
```

on every restart.

The system must restore or recalculate:

```text
Peak Equity
Daily Starting Equity
Daily PnL
Drawdown
Consecutive Losses
Cooldowns
Circuit Breaker State
```

If the system previously halted because of a hard risk condition, restarting the process must not bypass the halt.

---

# 171. Persistent Circuit Breakers

Hard circuit breakers must survive application restarts.

Example:

```text
Maximum Drawdown Reached
    ↓
HALTED
    ↓
Application Restart
```

must remain:

```text
HALTED
```

until the configured recovery procedure occurs.

Otherwise a simple restart would become a way to bypass risk controls.

---

# 172. Recovery Conditions

A circuit breaker may be cleared only when its configured recovery condition is satisfied.

Possible requirements:

```text
Manual reset
Risk review
New trading day
Successful reconciliation
Exchange stability restored
Drawdown below threshold
```

The exact recovery mechanism must be explicit.

No implicit recovery is allowed for critical safety events.

---

# 173. Connectivity Failure

Exchange connectivity may fail at any time.

Possible states:

```text
CONNECTED
DEGRADED
DISCONNECTED
```

### CONNECTED

Normal operation.

### DEGRADED

Some services are unavailable or delayed.

### DISCONNECTED

Exchange state cannot be trusted.

---

# 174. Connectivity Failure Policy

During exchange disconnection:

```text
New Entries
    → Forbidden
```

Existing positions require special handling.

If live exchange interaction is unavailable:

```text
Cannot guarantee position state
```

therefore automated risk actions may be limited.

The system must avoid creating additional exposure when state is unknown.

---

# 175. Market Data Failure

Market data has its own health state:

```text
HEALTHY
STALE
DISCONNECTED
INVALID
```

New entries require:

```text
HEALTHY
```

market data.

Existing positions should continue to be monitored through any available source.

---

# 176. Risk Data Dependency

Risk evaluation depends on several data sources.

At minimum:

```text
Account State
Position State
Market Price
Volatility
Open Orders
Risk Configuration
```

If a required input is unavailable:

```text
Risk Decision
    → REJECT
```

The system must not use arbitrary defaults for critical risk inputs.

---

# 177. Configuration Failure

If risk configuration cannot be loaded or validated:

```text
Trading
    → DISABLED
```

Examples:

```text
Missing max_drawdown
Invalid negative exposure limit
Invalid percentage > 100%
Unknown symbol
Invalid execution timeout
```

Configuration errors are startup failures, not runtime warnings.

---

# 178. Configuration Version

Every risk and execution decision should reference the configuration version used.

Example:

```text
configuration_version:
v1.0.3
```

This allows post-trade analysis to answer:

```text
Which risk parameters were active when this trade occurred?
```

Configuration changes must be auditable.

---

# 179. Configuration Changes During Trading

V1 should not allow arbitrary live mutation of critical risk parameters.

Changes to:

```text
max_position
max_exposure
daily_loss_limit
drawdown_limit
kelly_cap
execution_timeout
```

should require:

```text
Controlled Configuration Update
    ↓
Validation
    ↓
Version Change
    ↓
Audit Event
```

The simplest V1 policy is:

> Critical configuration changes require a process restart and full startup reconciliation.

---

# 180. Emergency Shutdown

Emergency shutdown is a deliberate safety operation.

It can be triggered by:

```text
Operator
Risk Engine
Circuit Breaker
Critical Application Error
Reconciliation Failure
Exchange Anomaly
```

The sequence should be:

```text
EMERGENCY
    ↓
Stop New Entries
    ↓
Cancel Exposure-Increasing Orders
    ↓
Reconcile Account
    ↓
Evaluate Existing Positions
    ↓
Execute Protective Exits if required
    ↓
Persist State
    ↓
Disable Trading
```

---

# 181. Emergency Shutdown Priority

Emergency shutdown prioritizes:

```text
1. Prevent new exposure
2. Establish account state
3. Reduce dangerous exposure
4. Preserve evidence
5. Stop automated trading
```

It does not prioritize:

```text
Profit maximization
Execution quality
Signal completion
Strategy continuity
```

---

# 182. Graceful Shutdown

A normal operator shutdown is different from emergency shutdown.

Normal shutdown:

```text
Stop new signals
    ↓
Allow active exits
    ↓
Cancel appropriate pending entries
    ↓
Persist state
    ↓
Reconcile
    ↓
Stop
```

The system should not abruptly terminate while an order is in an unknown state.

---

# 183. Shutdown with Open Position

V1 should not automatically liquidate every position merely because the application process is stopping.

Instead, the operator must explicitly select the desired shutdown policy.

Possible modes:

```text
KEEP_POSITION
CLOSE_POSITION
EMERGENCY_LIQUIDATE
```

The default production behavior should be conservative and explicit.

No hidden liquidation should occur.

---

# 184. Shutdown with Open Orders

Exposure-increasing pending orders should generally be cancelled during controlled shutdown.

Example:

```text
Open BTC BUY
    ↓
Operator Shutdown
    ↓
Cancel BUY
    ↓
Confirm Cancellation
```

Existing positions remain under the configured shutdown policy.

---

# 185. Emergency Liquidation

Emergency liquidation is a separate operation.

It should only be used when:

```text
Position risk is unacceptable
```

and continued holding is more dangerous than execution cost.

The liquidation path may use:

```text
Market Order
```

if necessary.

However, the action must remain fully logged.

---

# 186. Emergency Liquidation Invariant

Emergency liquidation must not accidentally reverse a position.

If:

```text
Position:
+0.01 BTC
```

then the maximum exit quantity is:

```text
0.01 BTC
```

not:

```text
0.02 BTC
```

The liquidation mechanism must be position-aware.

---

# 187. Kill Switch

QuantOS should expose a single explicit kill-switch state.

```text
TRADING_ENABLED
TRADING_DISABLED
EMERGENCY_STOP
```

The kill switch must take precedence over normal alpha decisions.

If:

```text
Alpha = LONG
```

but:

```text
Kill Switch = TRADING_DISABLED
```

then:

```text
No Entry
```

---

# 188. Kill Switch Persistence

Emergency stop state should persist across restart.

Example:

```text
EMERGENCY_STOP
    ↓
Process Restart
    ↓
EMERGENCY_STOP
```

A restart must not automatically re-enable trading.

---

# 189. Recovery from Emergency Stop

Recovery requires:

```text
1. Identify cause
2. Reconcile exchange
3. Validate account state
4. Validate configuration
5. Confirm risk state
6. Clear emergency state
7. Re-enable trading explicitly
```

For critical incidents, manual operator confirmation should be required.

---

# 190. Failure Taxonomy

QuantOS should classify failures into:

```text
DATA_FAILURE
RISK_FAILURE
EXECUTION_FAILURE
EXCHANGE_FAILURE
STATE_FAILURE
CONFIGURATION_FAILURE
APPLICATION_FAILURE
OPERATOR_FAILURE
```

Examples:

```text
DATA_FAILURE
    stale BTC price

RISK_FAILURE
    invalid risk configuration

EXECUTION_FAILURE
    order submission rejected

EXCHANGE_FAILURE
    API unavailable

STATE_FAILURE
    position mismatch

CONFIGURATION_FAILURE
    invalid exposure limit

APPLICATION_FAILURE
    process crash

OPERATOR_FAILURE
    unauthorized manual trade
```

---

# 191. Failure Severity

Each failure receives a severity:

```text
INFO
WARNING
CRITICAL
FATAL
```

The severity determines whether trading continues.

Example:

```text
INFO
Minor rounding difference
    ↓
Continue

WARNING
Temporary websocket disconnect
    ↓
Degrade

CRITICAL
Unknown position
    ↓
Halt

FATAL
Account state cannot be reconciled
    ↓
Emergency stop
```

---

# 192. Fail-Closed Architecture

The general safety rule is:

```text
Known Safe
    → Continue

Known Unsafe
    → Halt / Recover

Unknown
    → Halt
```

Never:

```text
Unknown
    → Assume Safe
```

This principle applies across:

* risk;
* execution;
* portfolio;
* exchange connectivity;
* market data;
* configuration.

---

# 193. Data Integrity

Critical state must be persisted durably.

At minimum:

```text
Orders
Fills
Risk Decisions
Portfolio State
Circuit Breakers
Configuration Version
Reconciliation Results
```

The system should be recoverable after process failure.

---

# 194. Persistence Failure

If the system cannot persist critical execution state:

```text
New Entries
    → Forbidden
```

Because the system could otherwise create exchange activity without a reliable internal record.

Example:

```text
Order Submitted
    ↓
Database Write Failed
```

The system must treat this as an execution-state integrity problem.

It must reconcile before continuing.

---

# 195. Database as Execution Dependency

The database is not merely a reporting layer.

For live execution, durable state is part of the safety boundary.

Therefore:

```text
Exchange
+
Persistent Execution State
+
Risk State
```

must remain consistent enough to reconstruct exposure.

---

# 196. Transactional State Updates

Where practical, related state changes should be committed atomically.

Example:

```text
ORDER_FILLED
    ↓
Fill Record
+
Order State
+
Position Update
+
Reservation Release
```

These updates should not leave the database permanently in a half-applied state.

---

# 197. Recovery from Persistence Failure

If a database transaction fails:

```text
Do not assume state changed.
```

The system must determine:

```text
Committed
or
Not Committed
```

before retrying.

If uncertain:

```text
Reconcile with exchange.
```

The same unknown-state principle applies to persistence.

---

# 198. Clock Failure

Time is required for:

* order expiration;
* risk windows;
* cooldowns;
* daily loss reset;
* event ordering;
* exchange timestamps.

If local clock drift becomes unsafe:

```text
Trading
    → Disabled
```

Exchange server time should be used to detect significant clock mismatch.

---

# 199. Process Health

The production process should expose a health state:

```text
STARTING
HEALTHY
DEGRADED
HALTED
EMERGENCY
STOPPING
STOPPED
```

Health state is separate from:

```text
Risk State
```

but related.

For example:

```text
Process = HEALTHY
Risk = HALTED
```

is valid.

---

# 200. Liveness vs Safety

A system being alive does not mean it is safe to trade.

Example:

```text
Process:
Running

Exchange:
Disconnected

Portfolio:
Unknown

Risk:
HALTED
```

The system is operationally alive but trading is disabled.

Safety takes priority over liveness.

---

# 201. Watchdog

A lightweight watchdog should monitor critical components.

Possible checks:

```text
Market data heartbeat
Exchange heartbeat
Execution heartbeat
Portfolio heartbeat
Risk heartbeat
Persistence heartbeat
```

If a critical heartbeat stops:

```text
Risk State
    → CAUTION / HALTED
```

depending on the component.

---

# 202. Dead-Man Protection

The system should support a configurable dead-man condition.

Conceptually:

```text
No valid system heartbeat
for N seconds
    ↓
Trading Disabled
```

This protects against a process that appears alive but is no longer functioning correctly.

---

# 203. Operational Monitoring

Production monitoring should expose:

```text
Current Equity
Available Balance
Open Positions
Open Orders
Risk State
Drawdown
Daily PnL
Execution Health
Exchange Connectivity
Market Data Health
Reconciliation Status
```

An operator should be able to determine system safety without reading logs manually.

---

# 204. Alert Conditions

Alerts should be generated for:

```text
Daily Loss Warning
Drawdown Warning
Trading Halt
Emergency Stop
Unexpected Position
Unexpected Fill
Exchange Disconnect
Reconciliation Failure
Repeated Order Failure
Persistence Failure
Stale Market Data
Configuration Failure
```

Alerts are operational controls, not merely notifications.

---

# 205. Audit Trail

The system must preserve an immutable logical history of:

```text
Risk Decision
Order Intent
Order Submission
Exchange Response
Fill
Cancellation
Reconciliation
Circuit Breaker
Manual Intervention
```

The audit trail should answer:

```text
What happened?
When?
Why?
Which component caused it?
Which configuration was active?
What account state existed?
```

---

# 206. Manual Intervention

Manual intervention must be explicitly recorded.

Examples:

```text
MANUAL_HALT
MANUAL_RESUME
MANUAL_CANCEL
MANUAL_RECONCILIATION
MANUAL_LIQUIDATION
CONFIGURATION_CHANGE
```

The system must not treat operator actions as ordinary strategy events.

---

# 207. Manual Resume

After a critical halt, resuming trading should require an explicit action.

Conceptually:

```text
HALTED
    ↓
Root Cause Resolved
    ↓
Reconciliation Passed
    ↓
Manual Resume
    ↓
NORMAL
```

Automatic resume is prohibited for critical safety events unless explicitly configured for that particular condition.

---

# 208. Risk State and Portfolio State Interaction

The Risk Engine consumes Portfolio State.

Portfolio State consumes:

```text
Exchange
Execution
Market Data
```

Therefore:

```text
Exchange
   ↓
Execution / Reconciliation
   ↓
Portfolio
   ↓
Risk
   ↓
Trade Approval
```

A corrupted Portfolio State therefore automatically restricts Risk decisions.

---

# 209. Reconciliation Before New Risk

Before approving new exposure, the Risk Engine should verify:

```text
Portfolio State = RECONCILED
```

If:

```text
Portfolio State = UNKNOWN
```

then:

```text
Risk Decision = REJECT
```

This prevents risk calculations from operating on fictional capital.

---

# 210. State Consistency Matrix

The following matrix defines expected behavior:

| Portfolio State        | Risk State | New Entry  |
| ---------------------- | ---------- | ---------- |
| RECONCILED             | NORMAL     | Allowed    |
| RECONCILED             | CAUTION    | Restricted |
| RECONCILED             | HALTED     | Forbidden  |
| RECONCILED             | EMERGENCY  | Forbidden  |
| UNKNOWN                | Any        | Forbidden  |
| INCONSISTENT           | Any        | Forbidden  |
| RECONCILIATION_PENDING | Any        | Forbidden  |
| RECONCILIATION_FAILED  | Any        | Forbidden  |

Exit and emergency-reduction behavior remains separately controlled.

---

# 211. Failure Recovery Principle

Recovery must move through:

```text
Detect
  ↓
Contain
  ↓
Understand
  ↓
Reconcile
  ↓
Repair
  ↓
Validate
  ↓
Resume
```

The system must not jump directly from:

```text
Detect
```

to:

```text
Resume
```

---

# 212. Recovery Must Be Observable

Every recovery operation must emit an event.

Example:

```text
RECONCILIATION_STARTED
RECONCILIATION_DISCREPANCY
RECONCILIATION_REPAIRED
RECONCILIATION_FAILED
TRADING_HALTED
TRADING_RESUMED
```

This creates a complete operational history.

---

# 213. Recovery Must Be Idempotent

Running reconciliation twice should produce the same final state.

Example:

```text
Reconcile()
    ↓
State = Correct

Reconcile()
    ↓
State = Still Correct
```

It must not:

* duplicate fills;
* duplicate fees;
* duplicate orders;
* change PnL;
* create additional positions.

---

# 214. Recovery Must Prefer Exchange Evidence

When internal and external state conflict:

```text
Exchange Evidence
    >
Local Assumption
```

Example:

```text
Local:
Order OPEN

Exchange:
FILLED
```

The system must update based on the exchange evidence.

However, unexpected exchange activity must still be investigated.

---

# 215. Reconciliation Does Not Mean Blind Overwrite

The system must not simply execute:

```text
Internal State = Exchange State
```

without understanding the discrepancy.

Why?

Because doing so can hide:

* missed fills;
* external trades;
* accounting bugs;
* duplicate events;
* persistence failures.

Reconciliation must preserve the discrepancy as an auditable event.

---

# 216. Portfolio State Acceptance Criteria

Portfolio State is correct when:

* account balances are known;
* open orders are known;
* positions are known;
* reservations are known;
* fills are deduplicated;
* PnL is calculable;
* market prices are sufficiently fresh;
* state is persisted;
* reconciliation status is known.

---

# 217. Reconciliation Acceptance Criteria

Reconciliation is correct when:

* internal and exchange orders agree;
* internal and exchange positions agree;
* balances are explainable;
* fills are accounted for;
* fees are accounted for;
* reservations match open orders;
* unexplained discrepancies trigger protection;
* repeated reconciliation is idempotent.

---

# 218. Recovery Acceptance Criteria

Recovery is correct when:

* restart does not create duplicate orders;
* restart does not lose fills;
* restart does not reset circuit breakers incorrectly;
* restart rebuilds reserved capital;
* unknown orders are resolved;
* partial fills are recovered;
* open positions are recovered;
* trading remains disabled until state is known;
* emergency state survives restart where required.

---

# 219. Production Safety Rule

The most important rule of Part 3 is:

> **QuantOS must never increase exposure while uncertain about the state of the account.**

Therefore:

```text
Unknown State
    ↓
No New Exposure
```

The system may still:

```text
Reconcile
Cancel
Reduce
Exit
Recover
Alert
```

but it must not casually create additional risk.

---

# 220. Part 3 Completion Criteria

Part 3 is complete when the implementation provides:

* authoritative portfolio state;
* account balance tracking;
* position tracking;
* pending-order reservations;
* PnL tracking;
* peak-equity tracking;
* durable state versioning;
* idempotent event processing;
* exchange reconciliation;
* startup recovery;
* crash recovery;
* circuit-breaker persistence;
* connectivity failure handling;
* stale-data handling;
* persistence failure handling;
* clock health checks;
* watchdog monitoring;
* emergency shutdown;
* kill switch;
* manual recovery;
* external activity detection;
* unexpected-order handling;
* unexpected-fill handling;
* state-integrity enforcement.

The resulting safety model is:

```text
                ┌──────────────────┐
                │     Exchange     │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │  Reconciliation  │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ Portfolio State  │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │   Risk Engine    │
                └────────┬─────────┘
                         │
                  ┌──────┴──────┐
                  │             │
                  ▼             ▼
              APPROVE         REJECT
                  │
                  ▼
            Execution Engine
```

This creates the critical production invariant:

```text
NO VERIFIED STATE
        ↓
NO NEW RISK
```

---

# 221. Boundary to Part 4

At this point, 006 has defined:

```text
PART 1
Risk Architecture
    ↓
PART 2
Execution Engine
    ↓
PART 3
Portfolio + Reconciliation + Recovery
```

The remaining section converts the architecture into an implementation contract.

Part 4 will define:

* complete component interfaces;
* configuration schema;
* event contracts;
* persistence requirements;
* logging and audit requirements;
* metrics;
* testing requirements;
* paper/live parity;
* V1 acceptance criteria;
* production invariants;
* implementation boundaries;
* the final Codex-facing build contract.

The objective of Part 4 is to make 006 sufficiently precise that an engineer—or an AI coding agent—can implement the Risk and Execution subsystem without inventing missing architectural behavior.
# QuantOS Core

## 006_RISK_EXECUTION_SPECIFICATION.md

**Version:** 0.1.0-alpha
**Status:** V1 Engineering Specification
**Document:** 006 — Risk & Execution Specification
**Part:** 4 of 4 — Implementation Contract & Acceptance Criteria

---

# 222. Purpose of Part 4

Parts 1–3 defined the architecture, execution model, portfolio state, reconciliation, and failure-handling behavior.

Part 4 defines the implementation contract.

The objective is:

> An engineer or AI coding agent must be able to implement QuantOS Risk & Execution V1 from this document without inventing missing system behavior.

The implementation must prioritize:

```text
Correctness
Safety
Determinism
Observability
Recoverability
Simplicity
```

over:

```text
Performance optimization
Abstraction complexity
Feature breadth
Premature extensibility
```

---

# 223. V1 Implementation Boundary

The V1 implementation includes:

```text
Risk Engine
Execution Engine
Order Manager
Portfolio State
Reconciliation Engine
Circuit Breakers
Kill Switch
Failure Recovery
Execution Persistence
Audit Trail
Operational Metrics
```

V1 does not include:

```text
Multi-exchange smart routing
High-frequency execution
Options
Futures
Cross-margin
Portfolio optimization
Complex derivatives
Machine-learning execution
Reinforcement learning
Autonomous strategy generation
Multi-account orchestration
```

These are future systems.

---

# 224. V1 Trading Model

The first production implementation should remain intentionally narrow.

Supported trading model:

```text
Spot
Long
Single account
Single exchange
Market + limit orders
One primary execution process
One primary portfolio state
```

The architecture should not prevent future expansion.

However:

> Future extensibility must never increase V1 implementation complexity unless required by the current system.

---

# 225. Component Architecture

The implementation should be divided into clear components.

```text
quantos/
│
├── risk/
│   ├── engine
│   ├── limits
│   ├── sizing
│   └── breakers
│
├── execution/
│   ├── engine
│   ├── order_manager
│   ├── exchange_adapter
│   └── state_machine
│
├── portfolio/
│   ├── state
│   ├── positions
│   ├── balances
│   └── pnl
│
├── reconciliation/
│   ├── engine
│   ├── balances
│   ├── orders
│   └── positions
│
├── persistence/
│   ├── models
│   ├── repository
│   └── transactions
│
├── safety/
│   ├── kill_switch
│   ├── circuit_breaker
│   └── health
│
├── audit/
│   ├── events
│   └── logger
│
└── tests/
```

The exact directory names may differ if the existing repository establishes another convention.

The architectural boundaries must remain.

---

# 226. Dependency Direction

Dependencies should flow toward infrastructure.

Conceptually:

```text
Strategy
   ↓
Risk
   ↓
Execution
   ↓
Exchange Adapter
   ↓
Exchange
```

Portfolio state sits across execution and reconciliation:

```text
Exchange
   ↓
Execution / Reconciliation
   ↓
Portfolio
   ↓
Risk
```

Risk must not directly communicate with the exchange.

Execution must not independently override Risk.

---

# 227. Strategy-to-Risk Interface

The strategy should submit an intent rather than an exchange order.

Conceptual interface:

```text
TradeIntent

symbol
side
intent_type
confidence
requested_notional
signal_timestamp
strategy_id
strategy_version
metadata
```

Example:

```text
TradeIntent(
    symbol="BTCUSDT",
    side="BUY",
    requested_notional=10.0,
    confidence=0.72
)
```

The strategy does not decide final quantity.

Risk does.

---

# 228. Risk Decision Interface

Risk receives:

```text
TradeIntent
PortfolioState
MarketState
RiskConfiguration
SystemState
```

and returns:

```text
RiskDecision
```

Conceptually:

```text
RiskDecision

approved
reason
approved_quantity
approved_notional
risk_checks
risk_state
configuration_version
timestamp
```

Example:

```text
approved = false
reason = "DAILY_LOSS_LIMIT"
```

---

# 229. Risk Decision Invariant

Execution must never execute an order merely because a strategy requested it.

The path must be:

```text
Strategy
   ↓
Trade Intent
   ↓
Risk Engine
   ↓
Approved Risk Decision
   ↓
Execution
```

Any attempt to bypass Risk is a V1 architecture violation.

---

# 230. Execution Interface

Execution receives an approved order request.

Conceptually:

```text
ExecutionRequest

client_order_id
symbol
side
order_type
quantity
price
time_in_force
risk_decision_id
strategy_id
configuration_version
```

Execution returns:

```text
ExecutionResult

accepted
exchange_order_id
status
error_code
error_message
timestamp
```

Execution must preserve the relationship:

```text
Strategy
→ Risk Decision
→ Execution Request
→ Exchange Order
```

---

# 231. Client Order ID

Every submitted order must have a deterministic unique client order ID.

Example:

```text
QOS-20260807-000001
```

The ID must allow QuantOS to correlate:

```text
Intent
Risk Decision
Execution Request
Exchange Order
Fills
Audit Events
```

Client IDs must never be reused.

---

# 232. Order State Machine

Order state must use explicit transitions.

Recommended states:

```text
CREATED
RISK_APPROVED
SUBMITTING
OPEN
PARTIALLY_FILLED
FILLED
CANCEL_REQUESTED
CANCELLED
REJECTED
EXPIRED
UNKNOWN
```

Possible flow:

```text
CREATED
   ↓
RISK_APPROVED
   ↓
SUBMITTING
   ↓
OPEN
   ↓
PARTIALLY_FILLED
   ↓
FILLED
```

or:

```text
OPEN
   ↓
CANCEL_REQUESTED
   ↓
CANCELLED
```

Invalid transitions must be rejected.

---

# 233. Unknown Order State

`UNKNOWN` is a real state.

It means:

> QuantOS cannot currently establish the authoritative exchange state.

An UNKNOWN order must not be treated as:

```text
CANCELLED
```

or:

```text
FAILED
```

The system must reconcile it.

---

# 234. Order State Persistence

Every meaningful state transition must be persisted.

Example:

```text
ORDER_CREATED
ORDER_RISK_APPROVED
ORDER_SUBMITTING
ORDER_OPEN
ORDER_PARTIALLY_FILLED
ORDER_FILLED
```

This allows the system to reconstruct execution history after a crash.

---

# 235. Risk Check Pipeline

Risk checks should execute in a deterministic sequence.

Recommended order:

```text
1. System Safety
2. Market Data Validity
3. Portfolio State Validity
4. Account Availability
5. Symbol Validity
6. Position Limits
7. Exposure Limits
8. Daily Loss Limits
9. Drawdown Limits
10. Trade Frequency Limits
11. Strategy Constraints
12. Position Sizing
13. Final Approval
```

A failed check stops evaluation where appropriate.

---

# 236. System Safety Check

First check:

```text
Trading Enabled?
```

Required:

```text
system_state == HEALTHY
risk_state != HALTED
kill_switch == OFF
portfolio_state == RECONCILED
```

If any condition fails:

```text
REJECT
```

---

# 237. Market Data Check

Required:

```text
price exists
timestamp exists
price is positive
price is sufficiently fresh
```

Invalid:

```text
price = null
price <= 0
timestamp too old
symbol unavailable
```

Result:

```text
MARKET_DATA_INVALID
```

---

# 238. Portfolio Check

Risk requires:

```text
Known Balance
Known Position
Known Open Orders
Known Reservation
```

If any required state is unknown:

```text
PORTFOLIO_STATE_UNKNOWN
```

and the trade is rejected.

---

# 239. Account Availability Check

For Spot BUY:

```text
required_quote_balance
≤
available_quote_balance
```

For Spot SELL:

```text
requested_quantity
≤
available_asset_balance
```

The system must account for fees and exchange constraints.

---

# 240. Symbol Validation

Every order must validate:

```text
symbol exists
symbol is tradable
quantity precision valid
price precision valid
minimum quantity satisfied
minimum notional satisfied
```

Exchange metadata should be cached but refreshed when necessary.

---

# 241. Position Limit

Before approving:

```text
Current Position
+
Pending Position Increase
+
New Position Increase
```

must remain below the configured limit.

The calculation must include pending orders.

---

# 242. Exposure Limit

Exposure must include:

```text
Existing Exposure
+
Pending Exposure
+
Requested Exposure
```

Example:

```text
Current:
5 USDT

Pending:
2 USDT

Requested:
4 USDT

Total:
11 USDT
```

If maximum exposure is:

```text
10 USDT
```

the request is rejected.

---

# 243. Daily Loss Check

Daily PnL must be compared with:

```text
daily_loss_limit
```

If:

```text
daily_pnl <= -daily_loss_limit
```

then:

```text
RISK HALT
```

No additional entries are allowed.

---

# 244. Drawdown Check

Current equity is compared against peak equity.

Conceptually:

```text
drawdown
=
(peak_equity - current_equity)
/
peak_equity
```

If:

```text
drawdown >= max_drawdown
```

then:

```text
CIRCUIT BREAKER
```

The system must halt new entries.

---

# 245. Position Sizing

Risk should calculate final order size.

Conceptually:

```text
Risk-Adjusted Size
=
Base Strategy Size
×
Risk Multiplier
```

subject to:

```text
Maximum Position
Maximum Exposure
Available Capital
Exchange Minimums
Daily Risk
Drawdown State
```

The final quantity is:

```text
min(
    strategy_size,
    risk_limit_size,
    capital_limit_size,
    exposure_limit_size
)
```

followed by exchange precision normalization.

---

# 246. Zero-Quantity Rule

If risk normalization results in:

```text
quantity <= 0
```

the trade must be rejected.

Never submit a zero-sized order.

Reason:

```text
POSITION_SIZE_TOO_SMALL
```

---

# 247. Precision Normalization

Exchange precision must be applied before submission.

Conceptually:

```text
requested_quantity
    ↓
floor_to_step_size
    ↓
validated_quantity
```

Do not round upward when doing so could exceed a risk limit.

---

# 248. Fee Awareness

Risk sizing must account for trading fees where necessary.

The system must not assume:

```text
gross_notional == actual_cost
```

because fees may reduce available capital.

---

# 249. Execution Preparation

Once Risk approves:

```text
RiskDecision
    ↓
ExecutionRequest
```

The execution engine must validate the request again for mechanical correctness.

This is not a second Risk Engine.

It is a final execution-integrity check.

---

# 250. Mechanical Validation

Execution validates:

```text
symbol
side
order_type
quantity
price
precision
client_order_id
exchange connectivity
```

If invalid:

```text
EXECUTION_REJECTED
```

The system must not modify the risk decision to make an invalid order executable.

---

# 251. Exchange Submission

The exchange adapter must provide a stable abstraction.

Conceptual methods:

```text
get_account()
get_balances()
get_open_orders()
get_order()
get_recent_orders()
get_recent_fills()

submit_order()
cancel_order()
cancel_all_orders()

get_server_time()
get_exchange_metadata()
```

The exact method names may follow the existing codebase.

---

# 252. Exchange Adapter Isolation

Exchange-specific behavior must remain inside the adapter.

Examples:

```text
REST paths
Authentication
WebSocket protocol
Exchange error codes
Precision formats
Order parameter naming
Rate limits
```

must not leak into Risk.

---

# 253. Retry Policy

Retries must be classified.

Safe to retry:

```text
GET account
GET order
GET balances
GET market metadata
```

Potentially unsafe:

```text
SUBMIT order
CANCEL order
```

Submission retries must use idempotent client identifiers or reconciliation.

---

# 254. The Dangerous Retry

Never blindly perform:

```text
submit_order()
    ↓
timeout
    ↓
submit_order()
```

because the first request may have succeeded.

The exchange could contain:

```text
Order #1 = FILLED
```

while QuantOS believes:

```text
Order #1 = UNKNOWN
```

The correct process is:

```text
Timeout
    ↓
Query by client order ID
    ↓
Determine state
    ↓
Retry only if safe
```

---

# 255. Cancellation Policy

Cancellation must also be state-aware.

Before cancelling:

```text
Query current order state
```

Possible result:

```text
FILLED
```

Then:

```text
Do not cancel
```

Possible:

```text
OPEN
```

Then:

```text
Submit cancellation
```

---

# 256. Partial Fill Handling

After a partial fill:

```text
filled_quantity
remaining_quantity
```

must be tracked separately.

Example:

```text
Requested:
0.010 BTC

Filled:
0.004 BTC

Remaining:
0.006 BTC
```

Portfolio state updates immediately based on the confirmed fill.

---

# 257. Fill Processing

Each fill should contain:

```text
execution_id
order_id
symbol
side
quantity
price
fee
fee_asset
timestamp
```

Execution IDs must be deduplicated.

---

# 258. Fill-to-Portfolio Flow

The authoritative flow is:

```text
Exchange Fill
      ↓
Execution Event
      ↓
Deduplication
      ↓
Persistence
      ↓
Portfolio Update
      ↓
Risk Recalculation
      ↓
Audit Event
```

---

# 259. Post-Fill Risk Recalculation

After a fill:

```text
Portfolio
    ↓
Exposure changes
    ↓
Risk state recalculated
```

This matters because a fill may push the portfolio near:

```text
position limit
exposure limit
drawdown limit
daily loss limit
```

---

# 260. Order Timeout

V1 should support configurable order timeouts.

Example:

```text
order_timeout_seconds = 30
```

If:

```text
OPEN
+
age > timeout
```

then the configured action may be:

```text
CANCEL
```

or:

```text
REASSESS
```

The strategy must not silently assume an order filled.

---

# 261. Timeout Reconciliation

Before cancelling a timed-out order:

```text
Fetch latest order state
```

because it may have filled between:

```text
timeout detection
```

and:

```text
cancellation attempt
```

---

# 262. Execution Event Ordering

Events may arrive out of order.

Example:

```text
ORDER_FILLED
```

may arrive before:

```text
ORDER_OPEN
```

The system must not corrupt state.

Events must be processed using:

```text
exchange timestamp
execution identifiers
order state
local sequence
```

where available.

---

# 263. Eventual Consistency

QuantOS should tolerate temporary inconsistencies between:

```text
WebSocket
REST
Local Database
```

The reconciliation engine is the convergence mechanism.

The system should not require perfect real-time consistency at every instant.

It must require eventual authoritative consistency before increasing risk.

---

# 264. WebSocket and REST

V1 may use:

```text
WebSocket
```

for low-latency events and:

```text
REST
```

for authoritative queries.

Typical pattern:

```text
WebSocket
    ↓
Fast State Update

REST
    ↓
Authoritative Reconciliation
```

WebSocket events must not be treated as permanently authoritative if reconciliation is required.

---

# 265. Connection Recovery

After WebSocket disconnect:

```text
Stop relying on event stream
    ↓
Reconnect
    ↓
Determine missed event window
    ↓
Fetch REST state
    ↓
Reconcile
    ↓
Resume event processing
```

This prevents missed fills from silently corrupting state.

---

# 266. Rate Limit Protection

The execution system must respect exchange rate limits.

Rate-limit state should be observable.

If rate limits approach unsafe levels:

```text
Execution State
    → DEGRADED
```

New nonessential requests may be throttled.

Risk should not generate additional orders when execution cannot safely process them.

---

# 267. Request Correlation

Every exchange interaction should carry a correlation identifier where possible.

Example:

```text
correlation_id
client_order_id
request_timestamp
```

This enables tracing:

```text
Risk Decision
→ Execution
→ HTTP/WebSocket Request
→ Exchange Response
→ Fill
```

---

# 268. Error Codes

Errors must be machine-readable.

Example:

```text
RISK_DAILY_LOSS
RISK_MAX_EXPOSURE
RISK_PORTFOLIO_UNKNOWN
EXECUTION_TIMEOUT
EXECUTION_REJECTED
EXCHANGE_UNAVAILABLE
ORDER_UNKNOWN
RECONCILIATION_FAILED
PERSISTENCE_FAILURE
CONFIG_INVALID
```

Human-readable messages should accompany them.

---

# 269. Logging Levels

Recommended levels:

```text
DEBUG
INFO
WARNING
ERROR
CRITICAL
```

Production logs should avoid excessive DEBUG output.

Critical trading decisions must always produce INFO-level audit events even if debug logging is disabled.

---

# 270. Structured Logging

Logs should be structured rather than free-form whenever possible.

Example fields:

```text
timestamp
level
event_type
symbol
order_id
client_order_id
strategy_id
risk_decision_id
correlation_id
state
reason
error_code
```

---

# 271. Metrics

At minimum expose:

```text
orders_submitted_total
orders_filled_total
orders_cancelled_total
orders_rejected_total
fills_total
execution_latency
order_ack_latency
fill_latency
reconciliation_count
reconciliation_failures
portfolio_state_age
market_data_age
daily_pnl
drawdown
current_exposure
risk_rejections
```

---

# 272. Risk Metrics

Risk-specific metrics:

```text
risk_approved_total
risk_rejected_total
risk_rejection_by_reason
current_position_exposure
current_total_exposure
available_balance
reserved_balance
current_drawdown
daily_loss
circuit_breaker_state
```

These metrics should support operational diagnosis.

---

# 273. Execution Metrics

Execution-specific metrics:

```text
submission_latency
ack_latency
cancel_latency
fill_latency
partial_fill_rate
rejection_rate
timeout_rate
unknown_order_rate
exchange_error_rate
```

---

# 274. Reconciliation Metrics

Reconciliation-specific metrics:

```text
reconciliation_success_total
reconciliation_failure_total
balance_discrepancy_total
position_discrepancy_total
order_discrepancy_total
unexpected_fill_total
unexpected_order_total
recovery_success_total
```

---

# 275. Audit Event Schema

Audit events should minimally contain:

```text
event_id
event_type
timestamp
component
symbol
order_id
client_order_id
strategy_id
risk_decision_id
configuration_version
payload
```

Not every field is required for every event.

The schema should remain extensible.

---

# 276. Persistence Model

At minimum the database should represent:

```text
orders
fills
positions
balances
risk_decisions
audit_events
reconciliation_runs
circuit_breakers
system_state
configuration_versions
```

The exact storage technology should follow the existing repository architecture.

---

# 277. Database Constraints

The database should enforce uniqueness where appropriate.

Examples:

```text
client_order_id UNIQUE
execution_id UNIQUE
event_id UNIQUE
risk_decision_id UNIQUE
```

This provides an additional layer of duplicate protection.

---

# 278. Transaction Boundary

A fill processing transaction should conceptually perform:

```text
BEGIN

insert fill if not already present
update order
update position
update balance
release reservation
record audit event

COMMIT
```

If any critical operation fails:

```text
ROLLBACK
```

and reconcile if exchange state is uncertain.

---

# 279. State Snapshot

The system should periodically be able to generate a complete portfolio snapshot:

```text
PortfolioSnapshot

timestamp
equity
cash
available_balance
reserved_balance
positions
open_orders
realized_pnl
unrealized_pnl
fees
drawdown
risk_state
reconciliation_state
```

Snapshots aid:

* recovery;
* debugging;
* backtesting comparisons;
* operational review.

---

# 280. Recovery Journal

The system should preserve enough information to reconstruct:

```text
What happened before the crash?
```

This includes:

```text
orders
fills
state transitions
risk decisions
reconciliation events
circuit breaker events
```

The system must not depend exclusively on in-memory state.

---

# 281. Test Architecture

The Risk & Execution subsystem requires several test layers.

```text
Unit Tests
Integration Tests
Exchange Adapter Tests
State Machine Tests
Reconciliation Tests
Failure Injection Tests
Paper Trading Tests
End-to-End Tests
```

---

# 282. Risk Unit Tests

Every risk rule must have:

```text
Allowed Case
Boundary Case
Rejected Case
```

Example:

```text
max_exposure = 10

Exposure = 9
    → APPROVE

Exposure = 10
    → Boundary behavior explicitly defined

Exposure = 11
    → REJECT
```

Boundary behavior must be deterministic.

---

# 283. Execution Unit Tests

Test:

* valid order;
* invalid order;
* exchange rejection;
* timeout;
* duplicate submission;
* partial fill;
* full fill;
* cancellation;
* cancellation race;
* unknown order;
* reconnect.

---

# 284. State Machine Tests

Every legal transition must be tested.

Every illegal transition must also be tested.

Example:

```text
OPEN → FILLED
```

valid.

But:

```text
CANCELLED → FILLED
```

must be explicitly handled as either:

```text
Invalid
```

or:

```text
Exchange reconciliation exception
```

depending on the chosen implementation.

The system must not silently mutate states.

---

# 285. Reconciliation Tests

Required scenarios:

```text
Matching state
Missing local fill
Missing exchange fill
Unexpected position
Unexpected order
Cancelled vs OPEN mismatch
OPEN vs FILLED mismatch
Partial-fill mismatch
Balance mismatch
Duplicate fill
External trade
```

---

# 286. Crash Tests

The system should deliberately simulate crashes at:

```text
Before order submission
During order submission
After order submission
Before persistence
After persistence
During fill processing
During cancellation
During reconciliation
```

After restart:

```text
No duplicate exposure
No lost fills
No corrupted position
No bypassed risk halt
```

must be guaranteed.

---

# 287. Network Failure Tests

Simulate:

```text
REST timeout
REST disconnect
WebSocket disconnect
DNS failure
Connection reset
Rate limit
Exchange 5xx
Malformed response
Delayed response
```

The expected result must be defined for every case.

---

# 288. Exchange Failure Tests

The test suite must include:

```text
Order rejected
Order accepted but response lost
Order filled but response lost
Cancel accepted but response lost
Account query unavailable
Order query unavailable
```

These are especially important because they create ambiguity.

---

# 289. Ambiguous Submission Test

This scenario is mandatory.

```text
QuantOS submits BUY
       ↓
Exchange accepts BUY
       ↓
Network response lost
       ↓
QuantOS receives timeout
```

Expected:

```text
Order = UNKNOWN
```

Then:

```text
Query exchange
       ↓
Recover authoritative state
```

The system must not submit a duplicate BUY.

---

# 290. Paper Trading Parity

Paper trading should use the same:

```text
Risk Engine
Portfolio Engine
Execution State Machine
Order Manager
Reconciliation Logic
```

where possible.

Only the exchange adapter should differ materially.

Architecture:

```text
Risk
 ↓
Execution
 ↓
Exchange Interface
 ↙          ↘
Paper       Live
```

This prevents paper trading from becoming a completely different system.

---

# 291. Simulation Rules

Paper execution should simulate:

```text
fills
partial fills
fees
latency
order rejection
cancellation
slippage
```

It must not unrealistically assume:

```text
Every order fills immediately at mid-price.
```

---

# 292. Live Transition

The production path should be:

```text
Unit Tests
    ↓
Integration Tests
    ↓
Simulation
    ↓
Paper Trading
    ↓
Small Live Capital
    ↓
V1 Production
```

Never:

```text
Backtest
    ↓
Full Capital
```

---

# 293. Live Capital Constraint

V1 should deliberately operate with small capital.

The purpose is:

```text
Validate the system
```

not:

```text
Maximize capital deployment
```

Early production metrics should prioritize:

```text
Correctness
Execution reliability
Reconciliation accuracy
Risk enforcement
```

over PnL.

---

# 294. Production Deployment Checklist

Before enabling live trading:

```text
[ ] Exchange credentials valid
[ ] API permissions minimized
[ ] Withdrawal permission disabled
[ ] Symbol metadata verified
[ ] Risk configuration validated
[ ] Kill switch tested
[ ] Circuit breaker tested
[ ] Reconciliation tested
[ ] Crash recovery tested
[ ] Duplicate order protection tested
[ ] Duplicate fill protection tested
[ ] Persistence tested
[ ] Alerting tested
[ ] Logging tested
[ ] Paper trading passed
[ ] Small-capital test passed
[ ] Manual emergency procedure tested
```

---

# 295. API Credential Principle

The trading API should have the minimum permissions required.

For V1:

```text
Trading
Read
```

should be sufficient.

Withdrawal permissions should remain disabled.

Credentials must never be committed to source control.

---

# 296. Secrets Management

Secrets must be provided through:

```text
Environment Variables
Secret Manager
Secure Deployment Configuration
```

Never:

```text
Hardcoded API key
Hardcoded secret
Git repository
Log output
Exception message
```

---

# 297. Production Configuration

Configuration must distinguish:

```text
development
paper
live
```

The environment must be explicit.

A production process must not accidentally connect to a paper account.

A paper process must not accidentally connect to a production account.

---

# 298. Environment Safety

The application should display a clear startup identity:

```text
ENVIRONMENT: LIVE
EXCHANGE: <exchange>
ACCOUNT: <safe identifier>
TRADING: DISABLED
```

before enabling trading.

This reduces operator mistakes.

---

# 299. Live Enable Sequence

Trading should require:

```text
Application Started
        ↓
Environment Confirmed
        ↓
Exchange Connected
        ↓
Account Reconciled
        ↓
Risk Validated
        ↓
Operator Enables Trading
```

This is preferable to automatically enabling live trading on process startup.

---

# 300. V1 Acceptance Criteria

006 is considered implemented when the following statements are true.

### Risk

```text
A strategy cannot bypass Risk.
```

```text
Risk rejects trades when portfolio state is unknown.
```

```text
Risk enforces position and exposure limits.
```

```text
Risk enforces daily loss and drawdown limits.
```

```text
Risk accounts for pending orders.
```

```text
Risk decisions are deterministic and auditable.
```

---

# 301. Execution Acceptance Criteria

```text
Every order has a unique client ID.
```

```text
Every order follows an explicit state machine.
```

```text
Unknown exchange responses do not create duplicate orders.
```

```text
Partial fills update portfolio state correctly.
```

```text
Cancellation races are handled safely.
```

```text
Exchange-specific behavior is isolated behind an adapter.
```

---

# 302. Portfolio Acceptance Criteria

```text
Balances are tracked.
```

```text
Positions are tracked.
```

```text
Open-order reservations are tracked.
```

```text
Realized and unrealized PnL are tracked.
```

```text
Peak equity and drawdown are tracked.
```

```text
Portfolio state can be reconstructed after restart.
```

---

# 303. Reconciliation Acceptance Criteria

```text
QuantOS can compare internal and exchange state.
```

```text
Missing fills are recovered.
```

```text
Unexpected fills are detected.
```

```text
Unexpected positions are detected.
```

```text
Unknown orders are resolved.
```

```text
Reconciliation is idempotent.
```

```text
Trading cannot resume while reconciliation is unresolved.
```

---

# 304. Recovery Acceptance Criteria

```text
Process crashes do not create duplicate orders.
```

```text
Process crashes do not lose confirmed fills.
```

```text
Circuit breakers survive restart.
```

```text
Kill switch survives restart where required.
```

```text
Unknown state fails closed.
```

```text
Emergency stop prevents new exposure.
```

---

# 305. Observability Acceptance Criteria

An operator must be able to determine:

```text
Is the system running?

Is trading enabled?

What is the current account equity?

What positions exist?

What orders are open?

What is the current exposure?

What is today's PnL?

What is the drawdown?

Is the exchange connected?

Is market data healthy?

Is reconciliation healthy?

Why was the last trade approved or rejected?
```

without inspecting application source code.

---

# 306. Engineering Invariants

The following invariants are mandatory.

### Invariant 1

```text
No Risk Approval
    →
No Order
```

### Invariant 2

```text
Unknown Account State
    →
No New Exposure
```

### Invariant 3

```text
Duplicate Fill
    →
No Duplicate Accounting
```

### Invariant 4

```text
Ambiguous Order Submission
    →
Reconcile Before Retry
```

### Invariant 5

```text
Critical Risk Halt
    →
Restart Does Not Clear Halt
```

### Invariant 6

```text
External Account Activity
    →
Trading Halt
```

### Invariant 7

```text
Persistence Failure
    →
No Unsafe Continued Execution
```

### Invariant 8

```text
Stale Critical Market Data
    →
No New Entry
```

### Invariant 9

```text
Emergency Stop
    →
No New Exposure
```

### Invariant 10

```text
Reconciliation Failure
    →
No New Exposure
```

---

# 307. Non-Goals

The implementation must not introduce:

```text
Complex microservices
```

unless required.

```text
Distributed consensus
```

unless required.

```text
Message brokers
```

unless required.

```text
Multiple databases
```

unless required.

```text
Complex event-sourcing infrastructure
```

unless required.

```text
Machine-learning risk models
```

unless required.

```text
Autonomous parameter optimization
```

unless required.

The V1 system should remain understandable by one engineer.

---

# 308. Simplicity Requirement

The implementation should prefer:

```text
One process
One database
One exchange
One account
One portfolio
One execution engine
```

over distributed complexity.

The system is intended to become battle-tested before it becomes sophisticated.

---

# 309. Error Handling Principle

Do not hide errors.

Bad:

```text
except Exception:
    pass
```

Bad:

```text
if error:
    continue
```

Preferred:

```text
Detect
Classify
Log
Contain
Recover
or Halt
```

Every error must have a deliberate consequence.

---

# 310. AI Coding Agent Rules

If Codex or another AI coding agent implements this document, it must obey:

### Rule 1

Do not invent new trading behavior.

### Rule 2

Do not weaken a safety condition to make tests pass.

### Rule 3

Do not silently swallow exchange errors.

### Rule 4

Do not introduce automatic retries for ambiguous order submissions without reconciliation.

### Rule 5

Do not bypass the Risk Engine.

### Rule 6

Do not treat unknown state as zero.

### Rule 7

Do not automatically clear circuit breakers after restart.

### Rule 8

Do not introduce leverage into Spot V1.

### Rule 9

Do not introduce additional exchanges into V1.

### Rule 10

When requirements conflict, choose the safer behavior and document the conflict.

---

# 311. Implementation Order

The implementation should proceed in this order:

```text
1. Domain Models
        ↓
2. Persistence
        ↓
3. Portfolio State
        ↓
4. Risk Rules
        ↓
5. Risk Engine
        ↓
6. Order State Machine
        ↓
7. Exchange Adapter
        ↓
8. Execution Engine
        ↓
9. Reconciliation
        ↓
10. Circuit Breakers
        ↓
11. Recovery
        ↓
12. Observability
        ↓
13. Integration Tests
        ↓
14. Paper Trading
        ↓
15. Live Validation
```

Do not begin with the exchange integration.

Build the deterministic core first.

---

# 312. Definition of Done

006 is not complete merely because orders can be submitted.

It is complete when:

```text
QuantOS can safely decide
whether a trade is allowed,
calculate the permitted size,
submit it,
observe the result,
account for the fill,
reconcile against the exchange,
recover from failure,
and refuse additional risk
when state becomes uncertain.
```

---

# 313. Final Architecture

The completed V1 Risk & Execution architecture is:

```text
                         ┌───────────────────┐
                         │     Strategy      │
                         └─────────┬─────────┘
                                   │
                              Trade Intent
                                   │
                                   ▼
                         ┌───────────────────┐
                         │    Risk Engine    │
                         │                   │
                         │ Limits            │
                         │ Position          │
                         │ Exposure          │
                         │ Drawdown          │
                         │ Daily Loss        │
                         │ Sizing            │
                         └─────────┬─────────┘
                                   │
                              Risk Decision
                                   │
                                   ▼
                         ┌───────────────────┐
                         │ Execution Engine  │
                         │                   │
                         │ Order Manager     │
                         │ State Machine     │
                         │ Retry Policy      │
                         │ Timeout Handling  │
                         └─────────┬─────────┘
                                   │
                              Exchange API
                                   │
                                   ▼
                         ┌───────────────────┐
                         │     Exchange      │
                         └─────────┬─────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    ▼                             ▼
             Orders / Fills                  Balances
                    │                             │
                    └──────────────┬──────────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │  Reconciliation   │
                         │                   │
                         │ Orders            │
                         │ Fills             │
                         │ Positions         │
                         │ Balances          │
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │  Portfolio State  │
                         └─────────┬─────────┘
                                   │
                                   ▼
                         ┌───────────────────┐
                         │    Risk Engine    │
                         └───────────────────┘


        ┌────────────────────────────────────────────┐
        │              SAFETY LAYER                   │
        │                                             │
        │ Kill Switch                                │
        │ Circuit Breakers                           │
        │ Watchdog                                   │
        │ Persistence                                │
        │ Audit Trail                                │
        │ Health Monitoring                          │
        └────────────────────────────────────────────┘
```

---

# 314. The Core QuantOS Safety Loop

The complete operational loop is:

```text
MARKET DATA
     ↓
STRATEGY
     ↓
TRADE INTENT
     ↓
RISK
     ↓
APPROVE / REJECT
     ↓
EXECUTION
     ↓
EXCHANGE
     ↓
FILL
     ↓
PORTFOLIO
     ↓
RECONCILIATION
     ↓
RISK
     ↓
NEXT DECISION
```

At every stage:

```text
UNKNOWN
  ↓
STOP NEW RISK
```

---

# 315. The Core Execution Principle

The most important architectural rule in 006 is:

> **Alpha may request risk. Alpha may never directly create risk.**

The strategy proposes.

Risk decides.

Execution implements.

The exchange confirms.

Portfolio records.

Reconciliation verifies.

Risk evaluates again.

This separation must remain intact throughout V1.

---

# 316. The Core Recovery Principle

The second fundamental rule is:

> **QuantOS must be able to recover from uncertainty without creating additional exposure.**

Therefore:

```text
Timeout
  ↓
UNKNOWN
  ↓
RECONCILE
  ↓
AUTHORITATIVE STATE
  ↓
RECOVER
```

Never:

```text
Timeout
  ↓
Assume Failed
  ↓
Retry
```

---

# 317. The Core Production Principle

QuantOS V1 is not considered production-ready because it makes profitable trades.

It is production-ready when:

```text
Profitable Trade
    +
Correct Risk
    +
Correct Execution
    +
Correct Accounting
    +
Correct Recovery
    +
Correct Reconciliation
```

all work together.

A trading system that makes money but cannot explain its positions after a network failure is not a production trading system.

---

# 318. Final V1 Contract

The implementation of 006 must satisfy:

```text
SAFE
DETERMINISTIC
AUDITABLE
RECOVERABLE
RECONCILABLE
OBSERVABLE
```

while remaining:

```text
SMALL
SIMPLE
TESTABLE
UNDERSTANDABLE
```

The V1 philosophy remains:

```text
Simplicity
    >
Complexity

Correctness
    >
Speed

Risk Control
    >
Profit Maximization

Known State
    >
Assumption

Recovery
    >
Retry

Evidence
    >
Inference
```

---

# 319. Document Completion

**006 — Risk & Execution Specification is complete.**

The document now defines:

```text
PART 1
Risk Architecture
        +
PART 2
Execution Engine
        +
PART 3
Portfolio State,
Reconciliation &
Failure Recovery
        +
PART 4
Implementation Contract,
Testing &
Production Acceptance
```

The resulting subsystem provides the controlled bridge between:

```text
Alpha
  ↓
Risk
  ↓
Execution
  ↓
Real Market
```

and establishes the safety boundary required before QuantOS V1 can move from:

```text
Research
```

to:

```text
Paper Trading
```

and eventually:

```text
Small-Capital Live Trading
```

The next document in the QuantOS engineering sequence should build on this contract rather than redefining it.
