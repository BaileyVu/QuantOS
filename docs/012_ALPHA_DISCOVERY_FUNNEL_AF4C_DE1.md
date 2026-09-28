# AF4C DE1 Price-Impact & Flow-Divergence Shadow Preregistration

**Version:** 1.0.0
**Date:** 2026-09-28
**Status:** SHADOW; preregistration only, ready for human audit
**Starting A1 HEAD:** 3722019f461ea28eb9e49c2de27e6cd259dfb609
**Schema:** af4c-de1-preregistration-v1
**Future evaluator ID:** af4c-impact-screen-tplus2-v1 (not implemented)
**Catalog ID:** 71b244b9843bc6c105f9b761f23437984e4ee87ee6c5156f300e72c8bb785350
**FDR family ID:** f0d87b0935d56a8c53f3939304c3c071cc4d6ec33d59ef9029a775322432bd42

## 1. Authority, purpose, and independence

AF4C is additive and subordinate to docs 000–011 and the frozen scientific
lifecycle. Document 000 remains highest authority; 001–007 remain coequal.
No preceding specification or AF4B preregistration is amended. The A1 starting
engine changes computation/storage performance only, not scientific semantics.

AF4C asks whether completed-minute DE1 transaction-price impact, VWAP
displacement, and cross-symbol price/flow divergence contain robust economically
meaningful information beyond previously tested candle and simple signed-flow
mechanisms. The human-directed universe is closed before inspecting any AF4C
predictive observation, signal count, future return, expectancy, p-value, q-value,
classification, or performance result.

AF4B's preregistration is known. AF4B performance/outcome results were **not
inspected**. AF4C is frozen while AF4B remains pending, as specified by the human
request; this is not a runtime status inspection. Mechanisms were selected from
the pre-existing DE1 roadmap concepts of VWAP displacement, price/flow divergence,
realized price-impact proxies, and cross-market disagreement/catch-up. This is a
separate DEVELOPMENT generation, not an outcome-dependent AF4B modification,
threshold relaxation, parameter rescue, or duplicate of its simple BTC-flow rule.

This remains DE1: only validated completed-minute Binance Spot AggregateTrade
state plus approved completed Candle fields, for BTCUSDT and ETHUSDT. It does not
authorize DE2, derivatives, order book, alternative data, leverage, shorts, model
training, feature search, production deployment, or live trading. The six
production modules and all runtime boundaries remain unchanged.

## 2. Data role and availability

The sole permitted future role is DEVELOPMENT:

`[2024-01-01T00:00:00Z, 2025-10-01T00:00:00Z)`.

Both symbols require Candle and DE1 coverage within that exact interval and DE1
daily partitions from **2024-01-01 through 2025-09-30 inclusive**. No
pre-DEVELOPMENT lookback or post-DEVELOPMENT data is permitted.
SCREENING_VALIDATION, SEALED_OOS, RESERVE, and 2026_RESEARCH are prohibited.
No acquisition, network use, or opening of market observation values is
authorized in this phase. A separately authorized execution phase must validate
and bind exact immutable Candle/DE1 dataset identities and content hashes before
opening observation values, signal counts, or outcomes; none are invented here.

DE1 family `aggregate_trade_minute_state` uses schema
`aggregate-trade-minute-state-v1` and aggregation
`aggregate-trade-minute-aggregation-v1`. Require validated source completeness,
DE1G finalization, healthy coverage, and all required source watermarks through
minute end. State minutes are UTC half-open intervals `[t,t+1m)`.

Join exact symbol and exact UTC minute. H5 requires both symbols at the exact
same UTC minute. Missing or invalid required support makes the observation
ineligible. No lag search, nearest-neighbor alignment, forward-fill, interpolation,
or retrospective repair of unavailable information is allowed.

## 3. Inputs and frozen derived definitions

The complete allowed input set is:

- `de1.total_base_quantity`, `de1.total_quote_notional`;
- `de1.aggressive_buy_base_quantity`, `de1.aggressive_sell_base_quantity`;
- `de1.aggressive_buy_quote_notional`, `de1.aggressive_sell_quote_notional`;
- `candle.open`, `candle.close`.

Required-input lists include fields needed by each fixed comparator. No raw
AggregateTrade signal trigger, individual-execution inference, trade-ID-range
inferred execution count, raw-event timing feature, open/partial state, future
information, or external data is allowed.

For one symbol/minute t, B and Q denote total base quantity and quote notional;
B_buy, B_sell, Q_buy, Q_sell denote their aggressive-side counterparts.
All future feature arithmetic uses exact Decimal-compatible semantics and the
inherited fixed AF1 Decimal context (`af1-decimal-50-half-even-tcdf-v1`).

| Definition | Formula | Defined only when |
| --- | --- | --- |
| Total VWAP V_t | Q_t / B_t | B_t > 0 and Q_t > 0 |
| Signed quote imbalance I_t | (Q_buy_t - Q_sell_t) / Q_t | Q_t > 0 |
| Displacement D_t | (close_t - V_t) / V_t | V_t > 0 |
| Buy VWAP V_buy_t | Q_buy_t / B_buy_t | B_buy_t > 0 and Q_buy_t > 0 |
| Sell VWAP V_sell_t | Q_sell_t / B_sell_t | B_sell_t > 0 and Q_sell_t > 0 |
| Candle return R_t | close_t / open_t - 1 | Valid completed candle, open_t > 0 |

There is no epsilon, zero substitution, or imputation. Any undefined required
value makes the observation ineligible.

For exactly t-4 through t inclusive:

```text
Q_5t = sum(Q_j)
B_5t = sum(B_j)
NetQ_5t = sum(Q_buy_j - Q_sell_j)
V_5t = Q_5t / B_5t
I_5t = NetQ_5t / Q_5t
```

All five completed minutes and required candles must exist inside DEVELOPMENT;
require Q_5t > 0 and B_5t > 0. No alternative window is registered.

## 4. Exactly five hypotheses and nine evaluations

Every hypothesis has one parameter set, one horizon, and UP direction only.
UP is an unlevered long Binance Spot research return, never short selling.
There are no numerical trigger thresholds beyond the stated sign/order
comparisons, threshold/quantile sweeps, horizon sweeps, or alternative variants.

### H1 — Flow-confirmed positive impact

**ID:** af4c.h1.flow-confirmed-positive-impact
**Parameter:** flow-impact-sign-v1
**Formula:** `I_t > 0 AND D_t > 0`.

Aggressive net buying with a close above transaction VWAP suggests buyer-driven
impact persisted through the minute rather than immediately reverting. Require
Q_t > 0, B_t > 0, V_t > 0, and defined I_t/D_t; the displacement condition is
equivalent to close_t > V_t.

BTCUSDT → BTCUSDT and ETHUSDT → ETHUSDT; H=1, lookback=1 completed minute.
First eligible state minute: 2024-01-01T00:00:00Z.
Comparator: unconditional eligible long outcome on identical target, horizon,
and common support.

### H2 — Buy/sell VWAP separation

**ID:** af4c.h2.buy-sell-vwap-separation
**Parameter:** side-vwap-order-v1
**Formula:** `V_buy_t > V_sell_t AND D_t > 0`.

Higher aggressive BUY-side quantity-weighted price than SELL-side price, together
with a close above total VWAP, is consistent with upward price progression or
adverse selection. Require both side VWAPs and total VWAP/displacement defined.
BUY/SELL quantities are Binance AggregateTrade aggressor-side aggregates; this
makes no claim about the distribution of individual executions.

BTCUSDT → BTCUSDT and ETHUSDT → ETHUSDT; H=5, lookback=1 completed minute.
First eligible state minute: 2024-01-01T00:00:00Z.
Comparator: completed candle `close_t > open_t`, identical target/horizon/support.

### H3 — Positive-impact acceleration

**ID:** af4c.h3.positive-impact-acceleration
**Parameter:** impact-change-one-minute-v1
**Formula:** `D_t > 0 AND D_t > D_{t-1}`.

Increasing positive close-to-VWAP displacement may identify strengthening realized
impact beyond positive raw signed flow. Require V_t and V_{t-1} and their
displacements defined, with both completed minutes inside DEVELOPMENT.

BTCUSDT → BTCUSDT and ETHUSDT → ETHUSDT; H=1, lookback=2 completed minutes.
First eligible state minute: 2024-01-01T00:01:00Z.
Comparator: `R_t > 0 AND R_t > R_{t-1}`, with R defined above, using identical
target, horizon, eligibility chronology, and common support.

### H4 — Five-minute flow-impact persistence

**ID:** af4c.h4.five-minute-flow-impact-persistence
**Parameter:** five-minute-flow-impact-sign-v1
**Formula:** `I_5t > 0 AND close_t > V_5t`.

Positive aggressive quote flow over the complete five-minute window, with the
latest close above its quantity-weighted transaction price, may identify durable
demand with realized price impact. Require the exact five-minute definition in
§3, including all completed DE1 minutes and required candles inside DEVELOPMENT.

BTCUSDT → BTCUSDT and ETHUSDT → ETHUSDT; H=5, lookback=5 completed minutes.
First eligible state minute: 2024-01-01T00:04:00Z.
Comparator: `close_t > open_{t-4}`, identical target/horizon/common support.

### H5 — BTC-impact-led ETH catch-up

**ID:** af4c.h5.btc-impact-led-eth-catchup
**Parameter:** btc-impact-eth-lag-v1
**Formula:** `I_BTC,t > 0 AND D_BTC,t > 0 AND D_ETH,t <= 0`.

BTC may lead short-horizon Spot price discovery. Positive BTC aggressive flow
and realized impact, while ETH remains at or below its own transaction VWAP,
may identify a lagging market capable of catch-up. All required BTC and ETH
VWAP/flow denominators must be defined at the exact shared UTC state minute.

Source information: BTCUSDT **and** ETHUSDT; target: ETHUSDT only. H=5,
lookback=1 completed minute. First eligible state minute: 2024-01-01T00:00:00Z.
Comparator: `BTC close_t > BTC open_t AND ETH close_t <= ETH open_t`, identical
ETH target, H=5 outcome chronology, and common support.

No symmetric ETH-to-BTC duplicate or lag search exists. Unlike AF4B's BTC-flow
hypothesis, H5 requires both realized BTC impact and simultaneous ETH
VWAP-relative lag. No additional BTC-flow threshold may be tuned.

| Hypothesis | Source state → target return | Horizon | Evaluations |
| --- | --- | --- | --- |
| H1 | BTC → BTC; ETH → ETH | 1 minute | 2 |
| H2 | BTC → BTC; ETH → ETH | 5 minutes | 2 |
| H3 | BTC → BTC; ETH → ETH | 1 minute | 2 |
| H4 | BTC → BTC; ETH → ETH | 5 minutes | 2 |
| H5 | BTC + ETH → ETH | 5 minutes | 1 |
| Total | Five hypothesis/parameter combinations | | **9** |

Any other count fails closed. All nine deterministic evaluation IDs are frozen
in the catalog before observations are opened.

## 5. Causal t+2 outcome contract

State minute t covers `[t,t+1m)`. Earliest knowability is **t+1m+5s**, conditional
on validated DE1 finalization and healthy source coverage. Entry is the open of
the completed-candle interval beginning at **t+2m**, offset exactly 2. The t+1
open is prohibited because it preceded knowability.

Hold H consecutive one-minute candles starting at t+2m. Exit at the close of
the candle beginning t+(H+1)m, observed at t+(H+2)m:

`gross return = close[t+(H+1)m] / open[t+2m] - 1`.

Sort eligible state minutes ascending, retain the earliest, then retain only a
state minute at least H minutes after the prior retained minute. Apply this
same chronological de-overlap separately to each primary and fixed comparator
trigger, on identical eligible support. This follows AF4B's event-screen rule;
it does not assert IID samples.

The common last eligible state minute is **2025-09-30T23:53:00Z**. Its H=5
outcome ends with the last DEVELOPMENT candle; completion at the exclusive
boundary does not require a post-boundary candle. No outcome-dependent chronology
changes are permitted. AF1's t+1 convention cannot be silently reused.

## 6. Exact costs, statistics, and advancement

Costs are simple-return fractions of entry notional:

```text
BASE_COST = 0.002503128284573645913980997751940
STRESS_2C = 0.005006256569147291827961995503880
STRESS_2C == 2 * BASE_COST  (exact Decimal equality)
```

Subtract each declared round-trip rate exactly once from mean directional gross
return. No cost optimization or post-result lowering is allowed.

The catalog preserves AF4B's complete statistical contract:

- Minimum 100 de-overlapped events; null: mean directional gross return <= 0;
  descriptive t-statistic and inherited event-screen test methodology.
- Deterministic bootstrap: 200 samples, confidence 0.95, seed 20260914.
- Eight fixed equal chronological DEVELOPMENT blocks; at least 10 events per
  qualifying block, qualified coverage at least 0.75, and at least six positive
  qualified blocks for maximum robustness. No result-dependent block boundaries.
- Promotion requires base-cost-adjusted expectancy >= BASE_COST, de-overlapped
  count >= 100, BH q-value <= 0.05, and research viability score >= 13.
- M/S/F/R bands are identical to AF4B; human explainability X=4. RVS remains
  M+S+F+R+X, maximum 20, not a probability. Missing or insufficient coverage
  cannot be rescued by effect magnitude.

| Score | Frozen bands |
| --- | --- |
| M | 0; 0.001251564142286822956990498875970; BASE_COST; STRESS_2C |
| S | 0.5; 1.5; 2.0; 3.0 |
| F | 0.1; 0.5; 2; 5 |
| R | 0.25; 0.50; 0.75; 1.00 |

Reuse AF4B/AF1 score-band semantics exactly. No gate is weakened.

Each primary must pass **every** unchanged promotion gate and have a **strictly
positive aggregate gross-expectancy difference** versus its fixed comparator
before advancing as incremental DE1 information. A gate-passing primary with a
non-positive difference is candle restatement/inconclusive and cannot advance;
an undefined required comparison cannot establish a positive difference.
Comparator statistics and the same eight-block differences are descriptive,
not extra FDR tests. No comparator may be chosen after outcomes, and no block
may be selected, pooled, or redefined to rescue a result.

## 7. Closed family and repeated DEVELOPMENT interpretation

AF4C has its own closed **Benjamini-Hochberg family of exactly nine evaluations**
at alpha 0.05. Every registered evaluation remains included. Undefined or failed
tests receive conservative **p=1** for multiplicity; unfavorable rows cannot be
deleted. Do not combine AF4B and AF4C p-values into a result-dependent family.

AF4B and AF4C are separate preregistered generations using the same DEVELOPMENT
role. Repeated DEVELOPMENT generations are not fresh independent confirmation.
Even an AF4C PROMOTE is only candidate-generation evidence. Untouched validation,
Backtest, Walk-Forward, Monte Carlo, Paper Trading, and Explicit Live Approval
remain mandatory; no automated result enables production or live trading.

## 8. Frozen shadow sealing/unsealing policy

AF4C execution requires **separate human authorization**. If execution occurs
while AF4B remains pending, predictive results must be written as **sealed shadow
evidence**. Normal stdout and final reports must not reveal signal counts,
returns, expectancies, p-values, q-values, rankings, classifications, or candidate
identities. Only non-predictive operational completion/integrity evidence may be
surfaced. Unsealing is never automatic.

- **Case A:** AF4B's authoritative terminal outcome has zero PROMOTE evaluations.
  AF4C shadow evidence becomes eligible for explicit human-approved unsealing
  and review. Eligibility alone does not unseal it.
- **Case B:** AF4B's authoritative terminal outcome has one or more PROMOTE
  evaluations. AF4C remains sealed for the current candidate-selection decision
  and must not be used to choose among or modify AF4B candidates. A later,
  separately authorized research phase may decide its disposition.

Unknown or nonterminal AF4B status leaves AF4C sealed. This policy is frozen now
and cannot change after AF4C outcomes exist. No AF4B results are inspected here.

## 9. Machine artifacts, lineage, and stopping boundary

`research/alpha_funnel_af4c_preregistration.py` builds the exact declaration,
canonical JSON, hypothesis definition hashes, evaluation IDs, FDR family ID, and
overall catalog ID. All identities use SHA-256. Canonical JSON uses sorted keys,
compact separators, UTF-8/ASCII escapes, finite values, and one final LF. Loading
rejects duplicate keys, noncanonical bytes, unknown/missing/altered fields and
types, and any deviation from the frozen declaration, even after rehashing.
The writer creates once or verifies identical bytes; conflicting bytes fail.

`research/alpha-funnel/af4c/catalog.json` binds the starting A1 commit, exact
repository-byte hashes for docs 000–011, and only the frozen AF4B preregistration
catalog's identity/hash and family identity. No AF4B run/result identity is bound.
Docs/012 records the resulting catalog and FDR identities above, avoiding
circular self-hashing. The preregistration test suite verifies these pins.

Stop after preregistration tests, catalog review, and diff checks. No market
observations or outcomes were accessed, no network acquisition occurred, and no
hypothesis was executed in this phase. No run.json, performance artifact,
evaluator, commit, push, merge, or automatic next phase is authorized. A1's speed
does not change the research permission boundary. This preregistration is not
evidence of alpha, economic viability, paper approval, or live approval.
