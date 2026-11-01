# QuantOS Core — 004_FEATURE_ENGINE_SPECIFICATION.md

Version: 1.1.0-V1
Status: Frozen V1
Last Updated: 2026-09-21

Amendment: 2026-09-21 — Human-approved DE1A completed-minute research inputs and deterministic dependencies; production semantics unchanged.

## 1. Objective

The Feature Engine transforms canonical market data into a small, deterministic feature vector for the single V1 strategy.

The target is 10–15 production features.
The hard maximum is 20.

## 2. Feature Principles

Every feature must:

1. have a clear purpose;
2. be computable using information available at the decision time;
3. be deterministic;
4. be testable;
5. be reproducible from stored data;
6. provide measurable incremental value.

Features must be removed when they are redundant, unstable, or unsupported by validation.

## 3. Approved Feature Families

The initial feature set should remain small and cover distinct information:

- short-term returns;
- medium-term returns;
- volatility;
- trend strength;
- moving-average relationship;
- momentum;
- volume behavior;
- range/price structure;
- relative position within recent range;
- regime/context state.

A feature family may contribute more than one feature only when validation demonstrates incremental value.

## 4. Avoiding Feature Overlap

Do not create large collections of correlated indicators merely because they are available.

For candidate features, evaluate:

- correlation/redundancy;
- stability across time;
- incremental out-of-sample value;
- sensitivity to parameter changes;
- importance stability.

The objective is a compact information set, not the highest feature count.

## 5. Causality

A feature at timestamp `t` may use:

- current completed candle information;
- prior candles;
- approved higher-timeframe information whose close was already available;
- for separately authorized research only, versioned completed-minute state
  derived from the market-event scope permitted by 003 §2.1, after the applicable
  minute is complete and the required completeness and point-in-time availability
  conditions are satisfied.

This research input permission does not approve any feature formula, change the
existing production feature set, or permit raw-event-driven decisions. Production
eligibility requires a separate approved feature specification, demonstrated
historical/live parity and the existing validation lifecycle. Unknown availability,
incomplete or missing state, or missing source coverage must not be silently
treated as valid information.

It may not use:

- future candles;
- future labels;
- future aggregate values;
- test-period statistics;
- information created after the decision timestamp.

## 6. Higher Timeframes

Higher-timeframe context is permitted only when it is temporally aligned without leakage.

For example, a 5-minute or higher context value may be used only after the relevant higher-timeframe candle has actually closed.

## 7. Missing Data

A feature vector with required missing values must not be passed to the production model.

The correct response is a controlled `NO_TRADE`/`HOLD` outcome, not silent imputation that changes the strategy's meaning.

## 8. Feature Versioning

Every feature specification has a version.

A trained model records the feature version it expects.

A model must not run against an incompatible feature schema.

## 9. Determinism

Given identical:

- canonical candle inputs;
- explicitly authorized completed-minute research state;
- input dataset versions and content identities;
- decision timestamps;
- applicable completion and availability policies;
- feature configuration;
- feature version;

the Feature Engine must produce identical outputs. Research features must declare
all data dependencies required for deterministic reproduction. Event-derived input
must not be introduced through undeclared callback state or provider-specific
runtime objects.

## 10. Testing

Tests must cover:

- known-value calculations;
- rolling-window boundaries;
- insufficient-history behavior;
- timestamp alignment;
- missing-data behavior;
- deterministic replay;
- higher-timeframe alignment;
- look-ahead prevention.

## 11. Production Promotion

A candidate feature enters production only when it demonstrates useful out-of-sample value without creating unacceptable instability or redundancy.

The feature budget is a complexity control, not a target to fill.
