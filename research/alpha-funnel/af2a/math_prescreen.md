# AF2A Math and Economic Pre-Screen

Catalog revision: `8292efbe030f3dff7a542efc6d4e841c73ec93b7c2116b363de688cc483290b9`. This document describes exactly this canonical AF2A catalog revision.

No AF1 empirical screen, threshold optimization, model training, future-return inspection, or sealed-OOS access was performed.

PLAUSIBLE, BORDERLINE, LOW, and STRONG are qualitative research judgments. They are not calibrated probabilities, posterior probabilities, confidence levels, or expected returns. In this phase, PLAUSIBLE means the research lead judges there remains roughly a >=20% chance that deeper empirical work could reveal meaningful edge. This is a decision heuristic, not a statistical estimate. No retained hypothesis is currently STRONG.

## Cost and expectancy

`C = 0.002503128284573645913980997751940` is the exact mathematical gross break-even hurdle under the preserved multiplicative fee/slippage convention.

`2C = 0.005006256569147291827961995503880` is a deliberately conservative AF2A research margin. A gross effect between `C` and `2C` is above mathematical break-even before the multiplicative conversion; it is not automatically negative expectancy.

All quantities use simple-return fraction of entry notional, and `avg_loss` is a positive loss magnitude.

`E_gross = p_win * avg_win - (1 - p_win) * avg_loss`

`E_net = (E_gross - C) / (1 + C)`

`p_break_even = (avg_loss + C) / (avg_win + avg_loss)`

Illustrative exact-Decimal break-even hit rates, not forecasts:

- Average win `0.005`, average loss `0.005`: `p_break_even = 0.750312828457364591398099775194`
- Average win `0.010`, average loss `0.010`: `p_break_even = 0.625156414228682295699049887597`
- Average win `0.0075`, average loss `0.005`: `p_break_even = 0.6002502627658916731184798201552`

## Closed multiplicity

15 retained concepts expand to 44 AF1 definitions. Across 2 symbols and 7 horizons this is 616 closed-FDR tests. F6 is a derived contrast, while F6, B5, and every other deferred or killed entry create zero tests.

## Per-hypothesis pre-screen

| ID | Status | Viability | AF1 definitions | Frequency | Sample | Complexity | Economic assessment |
|---|---|---|---:|---|---|---|---|
| `af2a.a1.negative-return-shock-reversal-up` | KILL_PRE_SCREEN | LOW | 0 | low to medium; roughly 1-12 events/day/symbol before de-overlap | workable | low | R5 supports weak economics at tested horizons and R6 supplies a negative overshoot-reversal prior; neither proposition-identically tested this event. |
| `af2a.a2.positive-return-shock-reversal-down` | KILL_PRE_SCREEN | LOW | 0 | low to medium; roughly 1-12 events/day/symbol before de-overlap | workable | low | The preserved results are negative priors rather than proposition-identical tests, and no independent mechanism supports a twice-cost effect. |
| `af2a.a3.lower-wick-after-negative-move-reversal-up` | RETAIN_AF3 | PLAUSIBLE | 2 | low to medium; roughly 1-10 events/day/symbol | workable | medium | The interaction can select moves larger than the cost floor, unlike unconditional reversion. |
| `af2a.a4.upper-wick-after-positive-move-reversal-down` | RETAIN_AF3 | PLAUSIBLE | 2 | low to medium; roughly 1-10 events/day/symbol | workable | medium | The interaction can isolate gross moves materially above the preserved hurdle. |
| `af2a.a5.large-range-weak-close-failed-continuation` | RETAIN_AF3 | PLAUSIBLE | 4 | medium; roughly 3-20 events/day/symbol | strong | medium | Only ranges above about 0.5% have a credible path to twice-cost gross movement. |
| `af2a.a6.overextension-deteriorating-efficiency-reversal` | RETAIN_AF3 | BORDERLINE | 2 | low; roughly 0.5-6 events/day/symbol | workable | high | Displacement is chosen above three cost floors, but realized reversal must still clear twice cost. |
| `af2a.b1.high-efficiency-positive-return-continuation-up` | KILL_PRE_SCREEN | LOW | 0 | medium; roughly 3-20 events/day/symbol | strong | low | R4 and R5 are negative priors, not proposition-identical tests; the added efficiency transform does not provide a distinct economic mechanism likely to clear twice cost. |
| `af2a.b2.high-efficiency-negative-return-continuation-down` | KILL_PRE_SCREEN | LOW | 0 | medium; roughly 3-20 events/day/symbol | strong | low | R4 and R5 are negative priors, not proposition-identical tests; the added efficiency transform does not provide a distinct economic mechanism likely to clear twice cost. |
| `af2a.b3.range-expansion-close-high-continuation-up` | RETAIN_AF3 | PLAUSIBLE | 2 | low to medium; roughly 1-12 events/day/symbol | workable | medium | Expansion must support a gross continuation above 0.5006%. |
| `af2a.b4.range-expansion-close-low-continuation-down` | RETAIN_AF3 | PLAUSIBLE | 2 | low to medium; roughly 1-12 events/day/symbol | workable | medium | Expansion must support a gross continuation above 0.5006%. |
| `af2a.b5.directional-move-high-participation-continuation` | KILL_PRE_SCREEN | LOW | 0 | low to medium; roughly 1-10 events/day/symbol | workable | medium | Large moves plus participation have a plausible route to twice-cost continuation. |
| `af2a.b6.multi-horizon-return-alignment-continuation` | KILL_PRE_SCREEN | LOW | 0 | medium to high; roughly 8-40 events/day/symbol | strong | medium | The prior evidence is not proposition-identical, but it supports weak economics and redundancy for the same overlapping close-path information. |
| `af2a.c1.short-long-volatility-compression` | DEFER | BORDERLINE | 0 | medium; roughly 5-30 states/day/symbol | strong | low | State alone cannot specify the sign of a gross move. |
| `af2a.c2.short-long-volatility-expansion` | DEFER | BORDERLINE | 0 | medium; roughly 5-30 states/day/symbol | strong | low | Direction-free expansion cannot establish a positive directional expectancy. |
| `af2a.c3.compression-directional-range-expansion` | RETAIN_AF3 | PLAUSIBLE | 4 | low; roughly 0.5-6 events/day/symbol | workable | high | Tail expansion can plausibly exceed the 0.5006% preferred gross floor. |
| `af2a.c4.positive-shock-high-volatility-reversal-down` | RETAIN_AF3 | BORDERLINE | 1 | low; roughly 0.5-5 events/day/symbol | weak | medium | Joint tails can move enough, but prior overshoot reversal failed broadly. |
| `af2a.c5.negative-shock-high-volatility-reversal-up` | RETAIN_AF3 | BORDERLINE | 1 | low; roughly 0.5-5 events/day/symbol | weak | medium | Joint tails can clear costs, though unconditional R6 evidence was negative. |
| `af2a.c6.volatility-expansion-low-directional-efficiency` | DEFER | BORDERLINE | 0 | medium; roughly 3-20 states/day/symbol | workable | medium | The state may support mean reversion, but a sign trigger is absent. |
| `af2a.d1.base-volume-surprise` | DEFER | BORDERLINE | 0 | medium; roughly 5-30 states/day/symbol | strong | low | Activity alone has no credible signed gross effect. |
| `af2a.d2.quote-volume-surprise` | DEFER | PLAUSIBLE | 0 | medium; roughly 5-30 states/day/symbol | strong | low | Quote activity needs a signed price interaction to target twice-cost returns. |
| `af2a.d3.trade-count-surprise` | DEFER | BORDERLINE | 0 | medium; roughly 5-30 states/day/symbol | strong | low | Count alone cannot imply a signed effect above costs. |
| `af2a.d4.average-trade-notional-surprise` | DEFER | BORDERLINE | 0 | medium; roughly 3-20 states/day/symbol | workable | low | Average notional alone has no signed expectancy claim. |
| `af2a.d5.volume-per-range-activity-concentration` | DEFER | BORDERLINE | 0 | medium; roughly 3-20 states/day/symbol | workable | medium | The state requires a signed resolution trigger before costs can be assessed. |
| `af2a.d6.quote-volume-per-absolute-return-proxy` | DEFER | BORDERLINE | 0 | medium; denominator-dependent and undefined at zero return | workable | medium | The proxy has no direction and extreme ratios can be numerical artifacts. |
| `af2a.e1.high-base-volume-weak-return` | KILL_PRE_SCREEN | LOW | 0 | medium; roughly 3-20 states/day/symbol | strong | low | Any later resolution must exceed twice cost; the event itself is deliberately weak-return. |
| `af2a.e2.high-quote-volume-weak-return` | RETAIN_AF3 | PLAUSIBLE | 4 | medium; roughly 3-20 states/day/symbol | strong | medium | A subsequent directional resolution could exceed 0.5006%, but direction must be predeclared. |
| `af2a.e3.high-trade-count-narrow-range` | RETAIN_AF3 | BORDERLINE | 4 | medium; roughly 3-20 states/day/symbol | strong | medium | Only the later resolution, not the narrow event, can clear costs. |
| `af2a.e4.high-activity-declining-efficiency` | RETAIN_AF3 | PLAUSIBLE | 4 | low to medium; roughly 1-10 events/day/symbol | workable | high | Conditioned displacement is at least near twice cost and can plausibly reverse by 0.5006%. |
| `af2a.e5.large-return-low-participation` | RETAIN_AF3 | PLAUSIBLE | 4 | low; roughly 0.5-6 events/day/symbol | workable | medium | Large selected moves can support a reversal materially above cost. |
| `af2a.e6.large-return-high-participation` | RETAIN_AF3 | PLAUSIBLE | 4 | low; roughly 0.5-6 events/day/symbol | workable | medium | Large selected moves can support continuation materially above cost. |
| `af2a.f1.one-minute-opposed-fifteen-minute-state` | RETAIN_AF3 | BORDERLINE | 4 | medium; roughly 3-20 events/day/symbol | strong | medium | The larger state must permit a post-pullback move above twice cost. |
| `af2a.f2.one-minute-aligned-fifteen-minute-state` | KILL_PRE_SCREEN | LOW | 0 | medium to high; roughly 8-40 events/day/symbol | strong | low | Prior aligned momentum failed and R5 spreads were far below costs. |
| `af2a.f3.completed-five-minute-aligned-fifteen-minute-state` | KILL_PRE_SCREEN | LOW | 0 | medium; roughly 5-30 events/day/symbol | strong | low | R5 found stable negative short-return association but magnitudes far below costs. |
| `af2a.f4.high-versus-low-efficiency-conditioning` | KILL_PRE_SCREEN | LOW | 0 | high state occupancy | strong | low | No standalone signed gross effect exists. |
| `af2a.f5.high-versus-low-volatility-conditioning` | KILL_PRE_SCREEN | LOW | 0 | high state occupancy | strong | low | State alone has no signed expectancy. |
| `af2a.f6.upside-downside-shock-asymmetry` | DEFER | BORDERLINE | 0 | low to medium per side; roughly 1-10 events/day/symbol | workable | medium | At least one side must show a gross effect above 0.5006% without pooling signs. |

## AF3 materialization gate

Before any AF1 empirical screen runs, AF3 must:

1. construct every executable `ResearchHypothesis` from the frozen AF2A planned definitions;
2. prove exact one-to-one coverage of all 44 planned definitions;
3. prove that no extra executable hypothesis exists;
4. bind every executable hypothesis to its genuine AF1 `HypothesisMetadata.definition_sha256`;
5. construct the closed AF1 FDR universe;
6. verify exactly 44 definitions × 2 symbols × 7 horizons = 616 registered tests; and
7. fail closed if materialized AF1 semantics differ from the frozen AF2A plan.

`planned_definition_sha256` is only an AF2A research-planning identity. AF3 must create and verify the separate genuine AF1 identity; equality between the two hash types is not assumed.
