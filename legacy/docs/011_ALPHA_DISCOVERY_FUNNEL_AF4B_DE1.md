# QuantOS V1 — AF4B DE1 Flow Hypothesis Preregistration

**Version:** 1.0.0
**Status:** Frozen research preregistration
**Date:** 2026-09-22
**Catalog ID:** 7bf403626ef205c2b4026d2b9ceecb7ff6c7ab6bd19dbbfcfc726625d5034346
**FDR family ID:** 2c36097242d3e61f884fae8c61c5998cc237f84c376fe5a3b60a2de3d9e55fc0

## 1. Authority and scope

This document freezes AF4B, the first hypothesis screen using the approved DE1 completed-minute AggregateTrade state. It is additive and subordinate to docs/000_READ_FIRST.md through docs/010_DATA_EDGE_RESEARCH_PROGRAM.md. It does not amend production requirements, authorize a production feature, approve a strategy, authorize short selling, or permit live trading.

AF4B is preregistration only. No hypothesis was executed, no market data was acquired, and no predictive observation, correlation, return, signal count, expectancy, p-value, or candidate performance was inspected while choosing this universe. The machine-readable catalog is authoritative for every frozen degree of freedom.

AF3 produced 599 KILL, 17 WATCH, and 0 PROMOTE evaluations. AF4A found all 17 WATCH rows blocked by sample sufficiency, BH/FDR, and composite evidence; 13 also failed the economic-cost gate. MF1 found no incremental information from trade_count_ratio_20, quote_volume_ratio_20, or avg_quote_trade_size_ratio_20 added to CONTROL10. AF4B therefore tests a small set of exchange-native directional-flow mechanisms with broad conditions. It does not rename total activity or volume.

## 2. Scientific question

AF4B asks whether validated completed-minute aggressive trade flow contains robust, economically meaningful short-horizon information unavailable to the exhausted completed-candle search. It tests mechanism-based event conditions without machine learning, automatic feature generation, parameter optimization, percentile fitting, pilot testing, or result-dependent thresholds.

The universe contains exactly six primary hypotheses and six hypothesis-parameter combinations before symbol and horizon expansion. Five hypotheses apply independently to BTCUSDT and ETHUSDT. One applies only as BTCUSDT flow leading an ETHUSDT outcome. Each hypothesis has one horizon and one parameterization. The closed multiplicity family therefore contains:

(5 × 2) + (1 × 1) = 11

registered evaluations.

All hypotheses are UP: an unlevered long Binance Spot research return. No DOWN evaluation or short-sale assumption is registered.

## 3. Approved information and alignment

A signal may use only:

1. validated completed one-minute Candle fields under existing QuantOS semantics; and
2. validated completed-minute DE1 state finalized under the DE1G research availability policy.

The DE1 family is aggregate_trade_minute_state, state schema aggregate-trade-minute-state-v1, aggregation version aggregate-trade-minute-aggregation-v1, with UTC half-open minute semantics PT1M:[start,end).

Candle and state join on exact symbol and exact UTC minute. The sole cross-symbol exception is H6, which joins the BTCUSDT source state and ETHUSDT target candle at the exact same UTC minute. Nearest-neighbor matching, forward-fill, interpolation, and lag search are forbidden. A missing required side makes the observation ineligible.

Raw AggregateTrade events cannot trigger a research trade. Partial or open state, L2, order book, derivatives, individual executions, and future information are forbidden. AggregateTrade record counts and side averages remain record-level quantities; trade-ID ranges cannot be interpreted as individual trades.

## 4. Frozen hypotheses

Notation:

- Q_buy(t): aggressive BUY quote notional in state minute t;
- Q_sell(t): aggressive SELL quote notional;
- Q_total(t): total quote notional;
- N_buy(t), N_sell(t): BUY- and SELL-side AggregateTrade record counts;
- open(t), close(t): completed Candle open and close;
- I(t) = [Q_buy(t)-Q_sell(t)] / Q_total(t).

### H1 — Immediate positive quote flow

**ID:** af4b.h1.immediate-positive-quote-flow
**Mechanism:** net aggressive buying may express short-lived informed demand or urgency.
**Formula:** I(t) > 0.
**Symbols:** BTCUSDT and ETHUSDT independently.
**Direction:** UP.
**Parameter set:** sign-zero-v1; threshold exactly zero.
**Eligibility:** exact joined support and Q_total(t) > 0. If the denominator is zero or undefined, I(t) is undefined and the observation is ineligible. No epsilon or zero substitution.
**Horizon:** 1 minute.

### H2 — Five-minute positive quote-flow persistence

**ID:** af4b.h2.five-minute-positive-quote-flow-persistence
**Mechanism:** large orders may be split across a short sequence of completed minutes.
**Formula:** sum j=0..4 of [Q_buy(t-j)-Q_sell(t-j)] divided by sum j=0..4 of Q_total(t-j), greater than zero.
**Symbols:** BTCUSDT and ETHUSDT independently.
**Direction:** UP.
**Parameter set:** five-minute-sign-zero-v1; lookback exactly five completed minutes and threshold exactly zero.
**Eligibility:** five consecutive exact joined completed states wholly inside DEVELOPMENT and a strictly positive five-minute total-notional denominator. Otherwise the feature is undefined and the observation is ineligible.
**Horizon:** 5 minutes.

### H3 — Positive quote-flow acceleration

**ID:** af4b.h3.positive-quote-flow-acceleration
**Mechanism:** an increase in signed aggressive pressure may add information beyond its current level.
**Formula:** I(t) > I(t-1).
**Symbols:** BTCUSDT and ETHUSDT independently.
**Direction:** UP.
**Parameter set:** one-minute-change-v1; comparison lag exactly one minute.
**Eligibility:** exact completed states at t-1 and t, each with Q_total > 0, wholly inside DEVELOPMENT. Either invalid denominator makes the observation ineligible.
**Horizon:** 1 minute.

### H4 — Sell-flow absorption reversal up

**ID:** af4b.h4.sell-flow-absorption-reversal-up
**Mechanism:** heavy aggressive selling that does not depress the completed-minute candle may indicate passive bid absorption.
**Formula:** Q_sell(t) >= 2 × Q_buy(t) and close(t) >= open(t).
**Symbols:** BTCUSDT and ETHUSDT independently.
**Direction:** UP.
**Parameter set:** sell-two-to-one-v1; fixed economically interpretable 2:1 minimum.
**Eligibility:** exact state/Candle join, Q_total(t) > 0, and both clauses true. There is no division; the positive-total rule prevents zero-activity minutes from satisfying the comparison.
**Horizon:** 5 minutes.

### H5 — BUY-side aggregate-record size asymmetry

**ID:** af4b.h5.buy-side-aggregate-record-size-asymmetry
**Mechanism:** larger average quote notional per BUY-side AggregateTrade record may reveal directional urgency or record-size composition.
**Formula:** A_buy(t)=Q_buy(t)/N_buy(t), A_sell(t)=Q_sell(t)/N_sell(t), trigger A_buy(t)>A_sell(t).
**Symbols:** BTCUSDT and ETHUSDT independently.
**Direction:** UP.
**Parameter set:** side-average-order-v1; strict comparison only.
**Eligibility:** exact joined state and both side event counts strictly positive. A missing or zero count makes its average undefined and the observation is ineligible. No epsilon or zero substitution.
**Horizon:** 5 minutes.
**Semantic limit:** this is an average per Binance AggregateTrade record, not an individual execution-size statistic.

### H6 — BTC positive flow leads ETH

**ID:** af4b.h6.btc-positive-flow-leads-eth
**Mechanism:** completed-minute BTC aggressive buying pressure may lead ETH short-horizon price response.
**Formula:** I_BTC(t)>0; evaluate ETHUSDT.
**Symbols:** source BTCUSDT, target ETHUSDT only. No symmetric ETH-to-BTC duplicate.
**Direction:** UP.
**Parameter set:** btc-to-eth-sign-zero-v1; threshold exactly zero.
**Eligibility:** exact same UTC minute for BTC state and ETH target support, both validated streams present, and BTC Q_total(t)>0. An invalid denominator makes the observation ineligible.
**Horizon:** 5 minutes.

## 5. Causal execution and outcome

For a state minute t=[t,t+1m), the state is knowable no earlier than t+1m+5s, and only after DE1G finalization with healthy source coverage and all required source watermarks through the minute end.

The opening price at t+1m occurred before the state was knowable and is forbidden. The earliest conservative candle execution proxy is the open of the candle beginning at t+2m.

For horizon H, hold the H consecutive one-minute candles beginning at t+2m. Exit at the close of the candle beginning at t+(H+1)m, observed at t+(H+2)m. The UP gross return is:

r(t,H) = close[t+(H+1)m] / open[t+2m] - 1

For H=1, entry is the t+2m open and exit is observed at t+3m. For H=5, entry is the t+2m open and exit is observed at t+7m.

Eligible state minutes are sorted ascending. Retain the earliest, then retain another only when its state minute is at least H minutes after the previous retained state minute. This preserves non-overlapping holding intervals.

Frozen AF1 machinery uses a different next-open convention and cannot be silently reused. The future evaluator ID is af4b-event-screen-tplus2-v1; it must implement this contract without changing AF1.

## 6. Cost and promotion contract

Return units are simple-return fractions of entry notional. Subtract a declared round-trip cost exactly once from the mean directional gross return:

- base C = 0.002503128284573645913980997751940;
- stress 2C = 0.005006256569147291827961995503880.

No cost or threshold optimization is permitted.

AF4B reuses the AF1/AF3 event-screen methodology except for the required t+2 outcome convention. It freezes:

- at least 100 de-overlapped events;
- descriptive t-statistic and the existing gross-mean test;
- deterministic bootstrap: 200 samples, 95% confidence, seed 20260914;
- eight fixed equal chronological DEVELOPMENT blocks;
- at least 10 events for a block to qualify;
- qualified-block coverage at least 0.75;
- at least six positive qualified blocks for maximum robustness;
- unchanged M/S/F/R bands and human explainability score 4;
- base cost-adjusted expectancy at least C;
- BH q-value at most 0.05;
- research viability score at least 13.

All mandatory promotion gates remain unchanged. Effect magnitude cannot rescue inadequate sample coverage, multiplicity failure, or economic failure.

## 7. Closed multiplicity family

Benjamini-Hochberg at alpha 0.05 operates over exactly the 11 registered evaluation IDs in catalog FDR family 2c36097242d3e61f884fae8c61c5998cc237f84c376fe5a3b60a2de3d9e55fc0.

Every symbol, hypothesis, parameter, and horizon row remains in the family. An undefined or non-evaluable test receives conservative p=1 and is never dropped. Comparator diagnostics are descriptive and do not add hypotheses or p-values to the closed family. No winner-selected horizon, parameter, direction, symbol, or subgroup may replace this family.

## 8. Incremental-information interpretation

Each primary has a comparator fixed before execution:

- H1, H2, H3, H5: unconditional eligible long outcome for the same target, horizon, and common support;
- H4: the same completed green-candle condition with the flow clause removed;
- H6: unconditional eligible ETH long outcome for the same horizon and common support.

A primary can be described as incremental only if it passes every unchanged promotion gate and its aggregate gross-expectancy difference against the declared comparator is strictly positive. A primary that passes promotion gates but has a non-positive difference is classified as candle restatement/inconclusive and cannot advance. Comparator results cannot rescue a failed primary.

Failure of count or temporal coverage is reported as a sample/coverage artifact. Candidate and comparator differences are reported across the same eight fixed blocks to reveal concentration, but blocks cannot be selected, pooled, or redefined. A statistically interesting result below the unchanged economic gate is economically irrelevant for advancement.

## 9. Data role and acquisition boundary

The only permitted role is DEVELOPMENT:

[2024-01-01T00:00:00Z, 2025-10-01T00:00:00Z)

Required BTCUSDT and ETHUSDT Candle and DE1 state coverage is exactly that interval. Required DE1 daily partitions are 2024-01-01 through 2025-09-30 inclusive. No pre-DEVELOPMENT warm-up is allowed:

- H1, H4, H5, H6 first state minute: 2024-01-01T00:00:00Z;
- H3 first state minute: 2024-01-01T00:01:00Z;
- H2 first state minute: 2024-01-01T00:04:00Z.

All evaluations use common support ending with state minute 2025-09-30T23:53:00Z. Its H=5 exit is observed at 2025-10-01T00:00:00Z, so no post-DEVELOPMENT candle is required.

SCREENING_VALIDATION, SEALED_OOS, RESERVE, and all 2026 research data are forbidden. The evaluator cannot pull extra dates.

The next phase must acquire and materialize only this declared DEVELOPMENT range. It must validate the sources and freeze exact immutable Candle and DE1 dataset IDs and content hashes before any observation values, signal counts, or outcomes are opened. Those future identities cannot be invented during preregistration.

## 10. Artifact and next phase

The canonical compact JSON artifact is research/alpha-funnel/af4b/catalog.json.

Its SHA-256-derived catalog identity is 7bf403626ef205c2b4026d2b9ceecb7ff6c7ab6bd19dbbfcfc726625d5034346.

The catalog binds frozen specifications, prior research identities, DE1 versions, formulas, symbol mappings, parameter sets, denominators, eligibility, horizons, t+2 outcomes, costs, statistics, temporal rules, data boundaries, prohibited roles, all evaluation IDs, and the closed FDR family.

The smallest next phase is data acquisition/materialization plus exact frozen-screen execution. It may acquire only the declared DEVELOPMENT partitions, bind immutable source identities before opening values, implement the registered evaluator, and execute exactly the catalog. It may not change the catalog after data opening. AF4B itself stops before that phase.
