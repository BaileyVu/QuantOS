# AF2A Human-Directed Hypothesis Ledger

Catalog ID: `8292efbe030f3dff7a542efc6d4e841c73ec93b7c2116b363de688cc483290b9`

Status: research metadata only. AF1 broad empirical screening has not run. No future return, AF3 result, or sealed OOS data was inspected.

PLAUSIBLE, BORDERLINE, LOW, and STRONG are qualitative research judgments. They are not calibrated probabilities, posterior probabilities, confidence levels, or expected returns. In this phase, PLAUSIBLE means the research lead judges there remains roughly a >=20% chance that deeper empirical work could reveal meaningful edge. This is a decision heuristic, not a statistical estimate. No retained hypothesis is currently STRONG.

## Cost and multiplicity contract

`C = 0.002503128284573645913980997751940` is the mathematical gross break-even hurdle in simple-return fraction of entry notional. `2C = 0.005006256569147291827961995503880` is a deliberately conservative AF2A research margin, not mathematical break-even.

The retained universe contains **15 concepts**, **44 AF1 definitions**, symbols `BTCUSDT, ETHUSDT`, horizons `1, 3, 5, 10, 15, 30, 60` minutes, and **616 closed-FDR tests**. Killed and deferred entries contribute zero tests.

## Preserved research prior

R4-R6 inform AF2A as negative priors. They are not described as proposition-identical falsifications of new conditioned events. A1, A2, B1, B2, and B6 remain killed on independent redundancy, mechanism, complexity, and cost-plausibility grounds recorded below.

## A. Reversal / Exhaustion

### `af2a.a1.negative-return-shock-reversal-up` — Negative return shock to upward reversal

Forced selling can temporarily overshoot fair short-horizon value, but prior unconditional overshoot work failed after costs.

- **Direction:** `reversal_up`
- **Causal inputs:** `close`
- **Formula:** `r_5=close[t]/close[t-5]-1; event=r_5<=-shock`
- **Lookback:** 6 completed 1-minute candles
- **Predeclared settings:** large {"shock": "0.0075"}; extreme {"shock": "0.0125"}
- **Frequency / sample:** low to medium; roughly 1-12 events/day/symbol before de-overlap; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** R5 supports weak economics at tested horizons and R6 supplies a negative overshoot-reversal prior; neither proposition-identically tested this event.
- **Primary failure:** The shock may reflect information rather than temporary pressure.
- **Overlap:** Simple unconditional reversion restates the rejected R6 lane.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** low / 4 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** R6 supplies a negative prior for overshoot reversal but did not test this exact 5-minute event and horizon definition; the bare shock has no new conditioner, duplicates the rejected economic mechanism, and has weak cost plausibility, so it remains killed.

### `af2a.a2.positive-return-shock-reversal-down` — Positive return shock to downward reversal

A sharp rise may exhaust short-term buyers, but a bare symmetric reversal repeats the rejected overshoot premise.

- **Direction:** `reversal_down`
- **Causal inputs:** `close`
- **Formula:** `r_5=close[t]/close[t-5]-1; event=r_5>=shock`
- **Lookback:** 6 completed 1-minute candles
- **Predeclared settings:** large {"shock": "0.0075"}; extreme {"shock": "0.0125"}
- **Frequency / sample:** low to medium; roughly 1-12 events/day/symbol before de-overlap; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The preserved results are negative priors rather than proposition-identical tests, and no independent mechanism supports a twice-cost effect.
- **Primary failure:** Positive shocks can continue when information-driven.
- **Overlap:** Symmetric counterpart to A1 and the rejected R6 lane.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** low / 4 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** R6 supplies a negative prior for overshoot reversal but did not test this exact positive 5-minute event and horizon definition; without a new conditioner, redundancy and weak cost plausibility independently justify the kill.

### `af2a.a3.lower-wick-after-negative-move-reversal-up` — Lower-wick rejection after negative movement

A long lower wick after a decline records intrabar rejection of lower prices and may isolate failed selling rather than generic overshoot.

- **Direction:** `reversal_up`
- **Causal inputs:** `open`, `high`, `low`, `close`
- **Formula:** `r_5=close[t]/close[t-5]-1; r_5<=return_max and lower_wick=(min(open[t],close[t])-low[t])/(high[t]-low[t])>=wick_min and close_location=(close[t]-low[t])/(high[t]-low[t])>=close_min`
- **Lookback:** 6 completed 1-minute candles
- **Predeclared settings:** clear_rejection {"close_min": "0.60", "return_max": "-0.004", "wick_min": "0.50"}; extreme_rejection {"close_min": "0.70", "return_max": "-0.0075", "wick_min": "0.65"}
- **Frequency / sample:** low to medium; roughly 1-10 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The interaction can select moves larger than the cost floor, unlike unconditional reversion.
- **Primary failure:** Wicks may be micro-noise and entries occur after the rejection is visible.
- **Overlap:** Materially differs from A1 through intrabar rejection conditioning.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** A causal rejection pattern is economically distinct from rejected unconditional overshoot and could select a tail large enough to survive costs.

### `af2a.a4.upper-wick-after-positive-move-reversal-down` — Upper-wick rejection after positive movement

A long upper wick after a rise records intrabar rejection of higher prices and may identify failed buying.

- **Direction:** `reversal_down`
- **Causal inputs:** `open`, `high`, `low`, `close`
- **Formula:** `r_5=close[t]/close[t-5]-1; r_5>=return_min and upper_wick=(high[t]-max(open[t],close[t]))/(high[t]-low[t])>=wick_min and close_location=(close[t]-low[t])/(high[t]-low[t])<=close_max`
- **Lookback:** 6 completed 1-minute candles
- **Predeclared settings:** clear_rejection {"close_max": "0.40", "return_min": "0.004", "wick_min": "0.50"}; extreme_rejection {"close_max": "0.30", "return_min": "0.0075", "wick_min": "0.65"}
- **Frequency / sample:** low to medium; roughly 1-10 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The interaction can isolate gross moves materially above the preserved hurdle.
- **Primary failure:** Strong trends can absorb the wick and resume upward.
- **Overlap:** Materially differs from A2 through intrabar rejection conditioning.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** The wick and close-location conjunction supplies a materially new failure signal absent from prior unconditional reversal tests.

### `af2a.a5.large-range-weak-close-failed-continuation` — Large directional range with weak close location

A large move that cannot hold its directional extreme may indicate failed continuation and short-horizon reversal.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `open`, `high`, `low`, `close`
- **Formula:** `range_pct=(high[t]-low[t])/close[t-1]; range_pct>=range_pct_min and ((close[t]>open[t] and close_location<=1-failure_tail) or (close[t]<open[t] and close_location>=failure_tail)); reverse sign(close[t]-open[t])`
- **Lookback:** 2 completed 1-minute candles
- **Predeclared settings:** large {"failure_tail": "0.40", "range_pct_min": "0.006"}; extreme {"failure_tail": "0.30", "range_pct_min": "0.010"}
- **Frequency / sample:** medium; roughly 3-20 events/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Only ranges above about 0.5% have a credible path to twice-cost gross movement.
- **Primary failure:** The observed range may be volatility without directional reversal.
- **Overlap:** Pairs the upward and downward versions; overlaps E4 but uses one-candle failure.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** It conditions on an explicit failed auction and has workable frequency while remaining distinct from bare momentum or reversal.

### `af2a.a6.overextension-deteriorating-efficiency-reversal` — Overextension with deteriorating path efficiency

A multi-minute displacement whose path becomes less efficient may signal exhaustion before reversal.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`
- **Formula:** `extension=abs(close[t]/close[t-extension_lookback_returns]-1); path_efficiency(a..b)=abs(close[b]-close[a])/sum(abs(close[j]-close[j-1]),j=a+1..b), undefined when path sum is zero; prior_efficiency=path_efficiency(t-short_efficiency_returns-prior_efficiency_returns..t-short_efficiency_returns); short_efficiency=path_efficiency(t-short_efficiency_returns..t); efficiency_drop_value=prior_efficiency-short_efficiency; windows_overlap=false means return intervals are disjoint and boundary_rule=shared_endpoint_disjoint_return_intervals assigns the change ending at t-short_efficiency_returns to the prior window and the next change to the short window; event=extension>=extension_min and efficiency_drop_value>=efficiency_drop; reverse sign(close[t]-close[t-extension_lookback_returns])`
- **Lookback:** 16 completed 1-minute candles
- **Predeclared settings:** clear_decay {"boundary_rule": "shared_endpoint_disjoint_return_intervals", "efficiency_drop": "0.20", "extension_lookback_returns": 15, "extension_min": "0.0075", "prior_efficiency_returns": 10, "short_efficiency_returns": 5, "windows_overlap": false}
- **Frequency / sample:** low; roughly 0.5-6 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Displacement is chosen above three cost floors, but realized reversal must still clear twice cost.
- **Primary failure:** Two-window conjunction can become sparse and decay need not imply reversal.
- **Overlap:** Uses path deterioration absent from A1/A2; related to E4 but requires overextension.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** high / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **BORDERLINE**
- **Reason:** A single fully specified, non-overlapping path-deterioration definition remains economically distinct, while the second sparse tail setting was removed before outcomes to control multiplicity.

## B. Momentum / Path Persistence

### `af2a.b1.high-efficiency-positive-return-continuation-up` — High-efficiency positive move continuation

A smooth positive path can indicate persistent buying, but this is close to previously rejected momentum.

- **Direction:** `continuation_up`
- **Causal inputs:** `close`
- **Formula:** `r_15>=return_min and efficiency_15>=efficiency_min`
- **Lookback:** 16 completed 1-minute candles
- **Predeclared settings:** moderate {"efficiency_min": "0.70", "return_min": "0.004"}; extreme {"efficiency_min": "0.85", "return_min": "0.0075"}
- **Frequency / sample:** medium; roughly 3-20 events/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** R4 and R5 are negative priors, not proposition-identical tests; the added efficiency transform does not provide a distinct economic mechanism likely to clear twice cost.
- **Primary failure:** Smooth moves may already be exhausted at next-open entry.
- **Overlap:** Closely overlaps prior momentum, return_15m, and efficiency_ratio_20m.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** low / 4 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** R4 supplies a negative prior for momentum but did not test this efficiency-conditioned proposition; R5 supports weak economics and redundancy at tested horizons without proposition identity. The condition is still only a close-path transformation, so AF2A independently kills it for redundancy and poor cost plausibility.

### `af2a.b2.high-efficiency-negative-return-continuation-down` — High-efficiency negative move continuation

A smooth negative path can indicate persistent selling, but it is the downside form of rejected momentum.

- **Direction:** `continuation_down`
- **Causal inputs:** `close`
- **Formula:** `r_15<=-return_min and efficiency_15>=efficiency_min`
- **Lookback:** 16 completed 1-minute candles
- **Predeclared settings:** moderate {"efficiency_min": "0.70", "return_min": "0.004"}; extreme {"efficiency_min": "0.85", "return_min": "0.0075"}
- **Frequency / sample:** medium; roughly 3-20 events/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** R4 and R5 are negative priors, not proposition-identical tests; the added efficiency transform does not provide a distinct economic mechanism likely to clear twice cost.
- **Primary failure:** Short-horizon declines often mean-revert and spot implementation cannot short in production.
- **Overlap:** Downside counterpart to B1 and close to prior momentum tests.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** low / 4 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** R4 supplies a negative prior for momentum but did not test this efficiency-conditioned proposition; R5 supports weak economics and redundancy at tested horizons without proposition identity. The condition is still only a close-path transformation, so AF2A independently kills it for redundancy and poor cost plausibility.

### `af2a.b3.range-expansion-close-high-continuation-up` — Range expansion closing near high

A range expansion that retains a high close may represent accepted higher prices rather than a transient spike.

- **Direction:** `continuation_up`
- **Causal inputs:** `open`, `high`, `low`, `close`
- **Formula:** `range_ratio_20=(high[t]-low[t])/mean(high[j]-low[j],j=t-20..t-1); range_ratio_20>=range_vs_20m_mean and (close[t]-low[t])/(high[t]-low[t])>=close_min and close[t]>open[t]`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** clear {"close_min": "0.80", "range_vs_20m_mean": "1.75"}; extreme {"close_min": "0.90", "range_vs_20m_mean": "2.50"}
- **Frequency / sample:** low to medium; roughly 1-12 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Expansion must support a gross continuation above 0.5006%.
- **Primary failure:** The expansion may exhaust immediately after the close.
- **Overlap:** Differs from B1 through range acceptance; symmetric with B4.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** Range acceptance is a materially different interaction from smooth-path momentum and can isolate moves large enough to test the cost hurdle.

### `af2a.b4.range-expansion-close-low-continuation-down` — Range expansion closing near low

A range expansion that retains a low close may represent accepted lower prices and persistent selling.

- **Direction:** `continuation_down`
- **Causal inputs:** `open`, `high`, `low`, `close`
- **Formula:** `range_ratio_20=(high[t]-low[t])/mean(high[j]-low[j],j=t-20..t-1); range_ratio_20>=range_vs_20m_mean and (close[t]-low[t])/(high[t]-low[t])<=close_max and close[t]<open[t]`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** clear {"close_max": "0.20", "range_vs_20m_mean": "1.75"}; extreme {"close_max": "0.10", "range_vs_20m_mean": "2.50"}
- **Frequency / sample:** low to medium; roughly 1-12 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Expansion must support a gross continuation above 0.5006%.
- **Primary failure:** Down moves can snap back and spot production cannot short.
- **Overlap:** Symmetric with B3; differs from generic path momentum.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** The close-at-extreme condition is a new acceptance signal and deserves symmetric research treatment.

### `af2a.b5.directional-move-high-participation-continuation` — Directional move with high participation

A directional move confirmed by unusually high quote activity and trade count may have broader participation and persist.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`, `quote_volume`, `trade_count`
- **Formula:** `abs(r_5)>=return_min and quote_volume_ratio_20>=quote_volume_ratio_min and trade_count_ratio_20>=trade_count_ratio_min; continue sign(r_5)`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** confirmed {"quote_volume_ratio_min": "1.75", "return_min": "0.004", "trade_count_ratio_min": "1.50"}; extreme {"quote_volume_ratio_min": "2.50", "return_min": "0.0075", "trade_count_ratio_min": "2.00"}
- **Frequency / sample:** low to medium; roughly 1-10 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Large moves plus participation have a plausible route to twice-cost continuation.
- **Primary failure:** High activity can mark climax rather than confirmation.
- **Overlap:** Formula-level duplicate of E6; E6 is the sole canonical retained form.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** KILL_PRE_SCREEN as redundant with E6, the retained canonical price-by-activity continuation concept; B5 parameters are catalog history only and are not merged into E6 or expanded into AF1 definitions.

### `af2a.b6.multi-horizon-return-alignment-continuation` — Multi-horizon return alignment

Aligned 1m, completed 5m, and 15m returns may indicate trend persistence across scales.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `feature:return_1m`, `feature:completed_5m_return`, `feature:return_15m`
- **Formula:** `sign(return_1m)=sign(completed_5m_return)=sign(return_15m) and abs(return_15m)>=return_15m_min`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** aligned {"return_15m_min": "0.004"}; strong_aligned {"return_15m_min": "0.0075"}
- **Frequency / sample:** medium to high; roughly 8-40 events/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The prior evidence is not proposition-identical, but it supports weak economics and redundancy for the same overlapping close-path information.
- **Primary failure:** The signals are overlapping transformations of the same close path.
- **Overlap:** Directly overlaps F2/F3; R5 found return_15m and SMA spread highly redundant.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 3 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** R4 supplies a negative momentum prior and R5 supports weak economics and redundancy at tested horizons, but neither directly tested this conjunction. AF2A still kills it because all gates are overlapping close-path transforms and the added multiplicity lacks a distinct cost-plausible mechanism.

## C. Volatility Transitions

### `af2a.c1.short-long-volatility-compression` — Short/long realized-volatility compression

Compression identifies a quiet state that may precede movement but supplies no directional edge by itself.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`
- **Formula:** `rv_5/rv_30<=ratio_max`
- **Lookback:** 31 completed 1-minute candles
- **Predeclared settings:** compressed {"ratio_max": "0.60"}; extreme {"ratio_max": "0.35"}
- **Frequency / sample:** medium; roughly 5-30 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** State alone cannot specify the sign of a gross move.
- **Primary failure:** Compression can persist without breakout.
- **Overlap:** C3 supplies the missing directional expansion event.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** Direction-neutral compression is a conditioning variable, not a standalone AF1 directional hypothesis; retain only through C3.

### `af2a.c2.short-long-volatility-expansion` — Short/long realized-volatility expansion

Expansion marks a regime transition but can precede either continuation or reversal.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`
- **Formula:** `rv_5/rv_30>=ratio_min`
- **Lookback:** 31 completed 1-minute candles
- **Predeclared settings:** expanded {"ratio_min": "1.75"}; extreme {"ratio_min": "2.50"}
- **Frequency / sample:** medium; roughly 5-30 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Direction-free expansion cannot establish a positive directional expectancy.
- **Primary failure:** Expansion has no intrinsic sign and may already be priced.
- **Overlap:** Directional forms are covered by C3-C5 and B3-B5.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** It is useful context but cannot enter AF1 without a predeclared directional interaction.

### `af2a.c3.compression-directional-range-expansion` — Compression followed by directional range expansion

A quiet regime followed by an accepted directional expansion may concentrate new information and escape prior noise.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `open`, `high`, `low`, `close`
- **Formula:** `simple_return_j=close[j]/close[j-1]-1; prior_rv_5=sqrt(mean(simple_return_j^2,j=t-5..t-1)); prior_rv_30=sqrt(mean(simple_return_j^2,j=t-30..t-1)); compression_ratio=prior_rv_5/prior_rv_30; mean_prior_range_20=mean(high[j]-low[j],j=t-20..t-1); range_ratio=(high[t]-low[t])/mean_prior_range_20; close_location=(close[t]-low[t])/(high[t]-low[t]); require prior_rv_30>0, mean_prior_range_20>0, and high[t]-low[t]>0; event=compression_ratio<=compression_max and range_ratio>=range_expansion_min and ((close[t]>open[t] and close_location>=close_tail => continuation_up) or (close[t]<open[t] and close_location<=1-close_tail => continuation_down))`
- **Lookback:** 32 completed 1-minute candles
- **Predeclared settings:** breakout {"close_tail": "0.80", "compression_max": "0.60", "range_expansion_min": "2.00"}; extreme_breakout {"close_tail": "0.90", "compression_max": "0.40", "range_expansion_min": "3.00"}
- **Frequency / sample:** low; roughly 0.5-6 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Tail expansion can plausibly exceed the 0.5006% preferred gross floor.
- **Primary failure:** False breakouts and sparse de-overlapped events.
- **Overlap:** Adds prior compression to B3/B4 and requires paired directions.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** high / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** The prior-only RMS compression, nonzero denominator rules, expansion gate, and corrected near-extreme UP/DOWN tails are fully frozen and economically distinct from unconditional momentum.

### `af2a.c4.positive-shock-high-volatility-reversal-down` — Positive shock in high volatility

A positive shock during an already volatile regime may be more likely to exhaust than a shock in normal conditions.

- **Direction:** `reversal_down`
- **Causal inputs:** `close`, `feature:realized_volatility_20m`
- **Formula:** `simple_return_j=close[j]/close[j-1]-1; rv_20=sqrt(mean(simple_return_j^2 over rv_lookback_returns)); rv_estimator=rms_simple_returns; calibration_scope=per_symbol; calibration_mode=rolling_prior_only uses the preceding calibration_lookback_minutes valid rv_20 observations for the same symbol; exclude_current_observation=true; require at least minimum_calibration_observations; update every calibration_update_minutes; threshold=sorted(prior_rv)[ceil(rv_state_quantile*N)-1] under quantile_rule=nearest_rank_ceil; zero_volatility_behavior=valid_zero_current_undefined_zero_baseline means current zero rv is valid but a zero/absent calibration baseline cannot create an event; r_5=close[t]/close[t-5]-1; event=r_5>=shock and rv_20>=threshold`
- **Lookback:** 43221 completed 1-minute candles
- **Predeclared settings:** high_vol {"calibration_lookback_minutes": 43200, "calibration_mode": "rolling_prior_only", "calibration_scope": "per_symbol", "calibration_update_minutes": 1, "exclude_current_observation": true, "minimum_calibration_observations": 2000, "quantile_rule": "nearest_rank_ceil", "rv_estimator": "rms_simple_returns", "rv_lookback_returns": 20, "rv_state_quantile": "0.80", "shock": "0.0075", "zero_volatility_behavior": "valid_zero_current_undefined_zero_baseline"}
- **Frequency / sample:** low; roughly 0.5-5 events/day/symbol; weak
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Joint tails can move enough, but prior overshoot reversal failed broadly.
- **Primary failure:** High volatility may strengthen continuation and de-overlapped samples may be sparse.
- **Overlap:** Materially conditions A2; symmetric with C5.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **BORDERLINE**
- **Reason:** A single predeclared per-symbol rolling prior-only high-volatility interaction remains for a bounded test; the second sparse setting was removed before outcomes.

### `af2a.c5.negative-shock-high-volatility-reversal-up` — Negative shock in high volatility

A negative shock in an already volatile regime may exhaust forced selling and reverse upward.

- **Direction:** `reversal_up`
- **Causal inputs:** `close`, `feature:realized_volatility_20m`
- **Formula:** `simple_return_j=close[j]/close[j-1]-1; rv_20=sqrt(mean(simple_return_j^2 over rv_lookback_returns)); rv_estimator=rms_simple_returns; calibration_scope=per_symbol; calibration_mode=rolling_prior_only uses the preceding calibration_lookback_minutes valid rv_20 observations for the same symbol; exclude_current_observation=true; require at least minimum_calibration_observations; update every calibration_update_minutes; threshold=sorted(prior_rv)[ceil(rv_state_quantile*N)-1] under quantile_rule=nearest_rank_ceil; zero_volatility_behavior=valid_zero_current_undefined_zero_baseline means current zero rv is valid but a zero/absent calibration baseline cannot create an event; r_5=close[t]/close[t-5]-1; event=r_5<=-shock and rv_20>=threshold`
- **Lookback:** 43221 completed 1-minute candles
- **Predeclared settings:** high_vol {"calibration_lookback_minutes": 43200, "calibration_mode": "rolling_prior_only", "calibration_scope": "per_symbol", "calibration_update_minutes": 1, "exclude_current_observation": true, "minimum_calibration_observations": 2000, "quantile_rule": "nearest_rank_ceil", "rv_estimator": "rms_simple_returns", "rv_lookback_returns": 20, "rv_state_quantile": "0.80", "shock": "0.0075", "zero_volatility_behavior": "valid_zero_current_undefined_zero_baseline"}
- **Frequency / sample:** low; roughly 0.5-5 events/day/symbol; weak
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Joint tails can clear costs, though unconditional R6 evidence was negative.
- **Primary failure:** Volatile selloffs can continue and sample counts may be weak.
- **Overlap:** Materially conditions A1; symmetric with C4.
- **AF1 feasibility:** `direct_single_direction`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **BORDERLINE**
- **Reason:** A single predeclared per-symbol rolling prior-only high-volatility interaction remains for a bounded test; the second sparse setting was removed before outcomes.

### `af2a.c6.volatility-expansion-low-directional-efficiency` — Volatility expansion with low directional efficiency

High volatility with low efficiency describes choppy two-sided trading, but does not imply an entry direction.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`
- **Formula:** `rv_5/rv_30>=volatility_ratio_min and efficiency_15<=efficiency_max`
- **Lookback:** 31 completed 1-minute candles
- **Predeclared settings:** choppy {"efficiency_max": "0.30", "volatility_ratio_min": "1.75"}; extreme_chop {"efficiency_max": "0.20", "volatility_ratio_min": "2.50"}
- **Frequency / sample:** medium; roughly 3-20 states/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The state may support mean reversion, but a sign trigger is absent.
- **Primary failure:** Low efficiency can persist and transaction costs dominate frequent reversals.
- **Overlap:** Overlaps E4 and needs a signed failure trigger.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** medium / 3 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** The current definition is direction-neutral and redundant with the stronger signed E4 interaction.

## D. Activity / Liquidity Proxies

### `af2a.d1.base-volume-surprise` — Base-volume surprise

Unusual base volume marks activity but its units vary with asset price and it has no directional sign.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `volume`
- **Formula:** `volume[t]/mean(volume[t-19:t])>=surprise_min`
- **Lookback:** 20 completed 1-minute candles
- **Predeclared settings:** high {"surprise_min": "2.0"}; extreme {"surprise_min": "3.5"}
- **Frequency / sample:** medium; roughly 5-30 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Activity alone has no credible signed gross effect.
- **Primary failure:** Volume can be high at continuation, reversal, or churn.
- **Overlap:** Quote-volume D2 is more comparable; signed uses appear in B5/E1/E4.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** Defer as a conditioner because a standalone AF1 direction would be invented rather than economically specified.

### `af2a.d2.quote-volume-surprise` — Quote-volume surprise

Unusual quote notional is a cross-price activity measure but remains direction-neutral.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `quote_volume`
- **Formula:** `quote_volume[t]/mean(quote_volume[t-19:t])>=surprise_min`
- **Lookback:** 20 completed 1-minute candles
- **Predeclared settings:** high {"surprise_min": "2.0"}; extreme {"surprise_min": "3.5"}
- **Frequency / sample:** medium; roughly 5-30 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Quote activity needs a signed price interaction to target twice-cost returns.
- **Primary failure:** Notional activity can accompany either informed flow or noise.
- **Overlap:** Preferred activity base for E2/E5/E6; standalone duplicates those conditions.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **DEFER** / **PLAUSIBLE**
- **Reason:** Defer the standalone state and test it only within explicit price/activity disagreement hypotheses.

### `af2a.d3.trade-count-surprise` — Trade-count surprise

A burst in recorded trades signals participation intensity but not buyer/seller direction or trade size.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `trade_count`
- **Formula:** `trade_count[t]/mean(trade_count[t-19:t])>=surprise_min`
- **Lookback:** 20 completed 1-minute candles
- **Predeclared settings:** high {"surprise_min": "2.0"}; extreme {"surprise_min": "3.5"}
- **Frequency / sample:** medium; roughly 5-30 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Count alone cannot imply a signed effect above costs.
- **Primary failure:** Many small trades may be noise and Binance trade count is not aggressor imbalance.
- **Overlap:** Useful only in B5/E3 interactions.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 4 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** Defer because the current Candle cannot turn trade-count surprise into directional order-flow information.

### `af2a.d4.average-trade-notional-surprise` — Average trade-notional surprise

Quote volume per recorded trade approximates average notional size, without identifying aggressor or information content.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `quote_volume`, `trade_count`
- **Formula:** `(quote_volume[t]/trade_count[t])/mean(valid_average_notional[t-19:t])>=surprise_min`
- **Lookback:** 20 completed 1-minute candles
- **Predeclared settings:** high {"surprise_min": "1.75"}; extreme {"surprise_min": "3.0"}
- **Frequency / sample:** medium; roughly 3-20 states/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Average notional alone has no signed expectancy claim.
- **Primary failure:** Zero trade counts are undefined; averages can hide heterogeneous trade sizes.
- **Overlap:** Distinct from volume and count but only a proxy, not imbalance.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 4 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** Defer until paired with a signed price response; standalone direction would exceed the data's information content.

### `af2a.d5.volume-per-range-activity-concentration` — Volume-per-range activity concentration

High normalized volume per unit of normalized range can mark absorption or two-sided liquidity concentration.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `volume`, `high`, `low`, `close`
- **Formula:** `normalized_volume=volume[t]/mean(volume[j],j=t-volume_normalization_window..t-1); normalized_range_t=(high[t]-low[t])/close[t-1]; prior_normalized_range_j=(high[j]-low[j])/close[j-1]; normalized_range=normalized_range_t/mean(prior_normalized_range_j,j=t-range_normalization_window..t-1); activity_concentration=normalized_volume/normalized_range; zero_denominator_behavior=undefined_no_event for zero prior-volume mean, zero prior-range mean, zero previous close, or zero current normalized range; event=activity_concentration>=concentration_min`
- **Lookback:** 22 completed 1-minute candles
- **Predeclared settings:** concentrated {"concentration_min": "2.0", "range_normalization_window": 20, "volume_normalization_window": 20, "zero_denominator_behavior": "undefined_no_event"}; extreme {"concentration_min": "3.5", "range_normalization_window": 20, "volume_normalization_window": 20, "zero_denominator_behavior": "undefined_no_event"}
- **Frequency / sample:** medium; roughly 3-20 states/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The state requires a signed resolution trigger before costs can be assessed.
- **Primary failure:** Small but nonzero normalized ranges can still create unstable concentration; every zero denominator is undefined.
- **Overlap:** Economic mechanism is expressed more directly by E1-E3.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** medium / 3 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** The helper and catalog now share one dimensionless causal quantity, but the unsigned state remains deferred pending a distinct signed trigger.

### `af2a.d6.quote-volume-per-absolute-return-proxy` — Quote-volume per absolute-return price-impact proxy

Normalized quote activity divided by absolute return is an inverse price-impact proxy; it measures neither order flow nor latent liquidity.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `quote_volume`, `close`
- **Formula:** `r_1=close[t]/close[t-return_lookback]-1; normalized_quote_volume=quote_volume[t]/mean(quote_volume[j],j=t-quote_volume_normalization_window..t-1); quote_volume_per_absolute_return=normalized_quote_volume/abs(r_1); require mean prior quote volume>0 and abs(r_1)>0, otherwise undefined/no event; high_liquidity if quote_volume_per_absolute_return>=inverse_impact_min; low_liquidity if quote_volume_per_absolute_return<=inverse_impact_max`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** high_liquidity {"inverse_impact_min": "200", "quote_volume_normalization_window": 20, "return_lookback": 1}; low_liquidity {"inverse_impact_max": "25", "quote_volume_normalization_window": 20, "return_lookback": 1}
- **Frequency / sample:** medium; denominator-dependent and undefined at zero return; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The proxy has no direction and extreme ratios can be numerical artifacts.
- **Primary failure:** Near-zero returns create unstable ratios and quoted volume does not measure depth.
- **Overlap:** Low inverse impact relates to E5; high inverse impact relates to E2.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** medium / 3 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** Defer the single canonical normalized-quote-volume-per-absolute-return proxy until a signed event exists; zero return and zero mean prior quote volume are undefined.

## E. Price / Activity Disagreement

### `af2a.e1.high-base-volume-weak-return` — High base volume with weak absolute return

High activity without price progress may indicate absorption, but base volume is less comparable than quote volume.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `volume`, `close`
- **Formula:** `volume_ratio_20>=activity_min and abs(r_1)<=return_max`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** disagreement {"activity_min": "2.0", "return_max": "0.001"}; extreme {"activity_min": "3.5", "return_max": "0.0005"}
- **Frequency / sample:** medium; roughly 3-20 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Any later resolution must exceed twice cost; the event itself is deliberately weak-return.
- **Primary failure:** Absorption and exhaustion imply competing directions.
- **Overlap:** E2 is the more comparable quote-notional definition.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** Mathematically redundant with the stronger quote-volume E2 definition and less comparable between BTC and ETH.

### `af2a.e2.high-quote-volume-weak-return` — High quote volume with weak absolute return

High notional activity with little current price response after signed pressure can indicate absorption or exhaustion; the frozen interpretation is reversal of that pressure.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `quote_volume`, `close`
- **Formula:** `prior_pressure=close[t-1]/close[t-1-prior_pressure_lookback]-1; r_1=close[t]/close[t-1]-1; quote_volume_ratio=quote_volume[t]/mean(quote_volume[j],j=t-quote_volume_normalization_window..t-1); require mean prior quote volume>0; event=quote_volume_ratio>=activity_min and abs(r_1)<=return_max and ((prior_pressure<=-pressure_min => reversal_up) or (prior_pressure>=pressure_min => reversal_down))`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** disagreement {"activity_min": "2.0", "pressure_min": "0.004", "prior_pressure_lookback": 5, "quote_volume_normalization_window": 20, "return_max": "0.001"}; extreme {"activity_min": "3.5", "pressure_min": "0.0075", "prior_pressure_lookback": 5, "quote_volume_normalization_window": 20, "return_max": "0.0005"}
- **Frequency / sample:** medium; roughly 3-20 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** A subsequent directional resolution could exceed 0.5006%, but direction must be predeclared.
- **Primary failure:** High notional with weak movement can persist rather than reverse.
- **Overlap:** Preferred replacement for E1 and unsigned D2.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** The five-minute prior-pressure source, activity normalization, weak-response gate, and UP/DOWN reversal mapping are predeclared before AF3.

### `af2a.e3.high-trade-count-narrow-range` — High trade count with narrow range

High trade intensity with narrow displacement after signed pressure can indicate absorption; the frozen interpretation is reversal of that pressure.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `trade_count`, `high`, `low`, `close`
- **Formula:** `prior_pressure=close[t-1]/close[t-1-prior_pressure_lookback]-1; trade_count_ratio=trade_count[t]/mean(trade_count[j],j=t-trade_count_normalization_window..t-1); normalized_range=(high[t]-low[t])/close[t-1]; require mean prior trade count>0 and close[t-1]>0; event=trade_count_ratio>=count_min and normalized_range<=range_max and ((prior_pressure<=-pressure_min => reversal_up) or (prior_pressure>=pressure_min => reversal_down))`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** concentrated {"count_min": "2.0", "pressure_min": "0.004", "prior_pressure_lookback": 5, "range_max": "0.0015", "trade_count_normalization_window": 20}; extreme {"count_min": "3.5", "pressure_min": "0.0075", "prior_pressure_lookback": 5, "range_max": "0.0010", "trade_count_normalization_window": 20}
- **Frequency / sample:** medium; roughly 3-20 states/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Only the later resolution, not the narrow event, can clear costs.
- **Primary failure:** Trade count is not aggressor imbalance and a tight range can simply continue.
- **Overlap:** Distinct from E2 because count can rise while notional does not.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **BORDERLINE**
- **Reason:** The five-minute signed-pressure source, count normalization, range gate, and UP/DOWN reversal mapping are fully predeclared; viability remains borderline because count is not aggressor flow.

### `af2a.e4.high-activity-declining-efficiency` — High activity with declining path efficiency

Rising activity while directional efficiency deteriorates can mark a contested move losing incremental price impact.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`, `quote_volume`
- **Formula:** `quote_volume_ratio_20>=activity_min and efficiency_5<=efficiency_10-efficiency_drop and abs(r_10)>=move_min; reverse sign(r_10)`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** decay {"activity_min": "1.75", "efficiency_drop": "0.20", "move_min": "0.005"}; extreme_decay {"activity_min": "2.5", "efficiency_drop": "0.30", "move_min": "0.0075"}
- **Frequency / sample:** low to medium; roughly 1-10 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Conditioned displacement is at least near twice cost and can plausibly reverse by 0.5006%.
- **Primary failure:** Efficiency changes can be noisy and the conjunction may overfit.
- **Overlap:** Related to A6, but activity is the distinct exhaustion evidence.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** high / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** The price-impact deterioration interaction is materially different from R6's bare overshoot reversal.

### `af2a.e5.large-return-low-participation` — Large return with unusually low participation

A large move on unusually low quote activity may be fragile and prone to reversal.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`, `quote_volume`, `trade_count`
- **Formula:** `abs(r_5)>=return_min and quote_volume_ratio_20<=quote_volume_ratio_max and trade_count_ratio_20<=trade_count_ratio_max; reverse sign(r_5)`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** fragile {"quote_volume_ratio_max": "0.70", "return_min": "0.0075", "trade_count_ratio_max": "0.75"}; extreme_fragile {"quote_volume_ratio_max": "0.50", "return_min": "0.0125", "trade_count_ratio_max": "0.55"}
- **Frequency / sample:** low; roughly 0.5-6 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Large selected moves can support a reversal materially above cost.
- **Primary failure:** Low activity may reflect a genuine liquidity gap that continues rather than reverses.
- **Overlap:** Competing interpretation to E6 is explicitly preserved.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** It tests the instructed fragile-move interpretation with explicit activity conditioning absent from prior reversion work.

### `af2a.e6.large-return-high-participation` — Large return with unusually high participation

A large move confirmed by high quote activity and trade count may reflect broad information incorporation and continue.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`, `quote_volume`, `trade_count`
- **Formula:** `abs(r_5)>=return_min and quote_volume_ratio_20>=quote_volume_ratio_min and trade_count_ratio_20>=trade_count_ratio_min; continue sign(r_5)`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** confirmed {"quote_volume_ratio_min": "1.75", "return_min": "0.0075", "trade_count_ratio_min": "1.50"}; extreme_confirmed {"quote_volume_ratio_min": "2.50", "return_min": "0.0125", "trade_count_ratio_min": "2.00"}
- **Frequency / sample:** low; roughly 0.5-6 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Large selected moves can support continuation materially above cost.
- **Primary failure:** High participation may be a terminal climax rather than confirmation.
- **Overlap:** Canonical price-by-activity continuation concept; B5 is killed as redundant and its settings are not merged here.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **PLAUSIBLE**
- **Reason:** Retained as the sole canonical price-by-activity continuation concept with exactly two meaningfully separated predeclared settings.

## F. Regime / Cross-Horizon / Asymmetry

### `af2a.f1.one-minute-opposed-fifteen-minute-state` — One-minute return opposed to 15-minute state

A short counter-move inside a larger state may be a temporary pullback whose resolution follows the 15-minute direction.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `feature:return_1m`, `feature:return_15m`
- **Formula:** `sign(return_1m)=-sign(return_15m) and abs(return_15m)>=state_min and abs(return_1m)>=pullback_min; continue sign(return_15m)`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** pullback {"pullback_min": "0.0015", "state_min": "0.005"}; deep_pullback {"pullback_min": "0.003", "state_min": "0.0075"}
- **Frequency / sample:** medium; roughly 3-20 events/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** The larger state must permit a post-pullback move above twice cost.
- **Primary failure:** The counter-move may signal trend failure rather than a pullback.
- **Overlap:** Materially different from aligned F2 and generic B6.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **RETAIN_AF3** / **BORDERLINE**
- **Reason:** Opposition is an explicit interaction not isolated by prior aligned momentum or unconditional reversal tests.

### `af2a.f2.one-minute-aligned-fifteen-minute-state` — One-minute return aligned with 15-minute state

Alignment across one and fifteen minutes is a simple momentum confirmation.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `feature:return_1m`, `feature:return_15m`
- **Formula:** `sign(return_1m)=sign(return_15m) and abs(return_1m)>=return_1m_min and abs(return_15m)>=state_min`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** aligned {"return_1m_min": "0.001", "state_min": "0.004"}; strong_aligned {"return_1m_min": "0.002", "state_min": "0.0075"}
- **Frequency / sample:** medium to high; roughly 8-40 events/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** Prior aligned momentum failed and R5 spreads were far below costs.
- **Primary failure:** Signals share the same close path and can be redundant.
- **Overlap:** Subsumed by B6 and overlaps F3.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** It is a redundant restatement of aligned momentum, a lane rejected in R4 and unsupported at cost scale in R5.

### `af2a.f3.completed-five-minute-aligned-fifteen-minute-state` — Completed 5-minute return aligned with 15-minute state

Completed 5m and 15m alignment is another multi-scale momentum confirmation.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `feature:completed_5m_return`, `feature:return_15m`
- **Formula:** `sign(completed_5m_return)=sign(return_15m) and abs(completed_5m_return)>=return_5m_min and abs(return_15m)>=state_min`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** aligned {"return_5m_min": "0.002", "state_min": "0.004"}; strong_aligned {"return_5m_min": "0.0035", "state_min": "0.0075"}
- **Frequency / sample:** medium; roughly 5-30 events/day/symbol; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** R5 found stable negative short-return association but magnitudes far below costs.
- **Primary failure:** Overlapping horizons restate the same price path.
- **Overlap:** Subsumed by B6/F2 and uses highly related returns.
- **AF1 feasibility:** `requires_predeclared_paired_direction_definitions`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** Redundancy and rejected momentum economics do not justify another aligned-return definition.

### `af2a.f4.high-versus-low-efficiency-conditioning` — High- versus low-efficiency conditioning

Path efficiency can separate directional and noisy regimes, but comparison alone supplies no entry direction.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `feature:efficiency_ratio_20m`
- **Formula:** `efficiency_ratio_20m>=high_cut or efficiency_ratio_20m<=low_cut`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** broad {"high_cut": "0.70", "low_cut": "0.30"}; extreme {"high_cut": "0.85", "low_cut": "0.15"}
- **Frequency / sample:** high state occupancy; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** No standalone signed gross effect exists.
- **Primary failure:** A conditioning split can encourage post-hoc direction selection.
- **Overlap:** Already embedded in A6, B1/B2, and E4.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** It is mathematically redundant with stronger signed definitions and cannot enter AF1 alone.

### `af2a.f5.high-versus-low-volatility-conditioning` — High- versus low-volatility conditioning

Volatility state can alter behavior, but a state split alone does not predict direction.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `feature:realized_volatility_20m`
- **Formula:** `rv_20>=prior_only_quantile(high_state_quantile) or rv_20<=prior_only_quantile(low_state_quantile)`
- **Lookback:** 21 completed 1-minute candles
- **Predeclared settings:** broad {"high_state_quantile": "0.80", "low_state_quantile": "0.20"}; extreme {"high_state_quantile": "0.90", "low_state_quantile": "0.10"}
- **Frequency / sample:** high state occupancy; strong
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** State alone has no signed expectancy.
- **Primary failure:** Regime buckets can invite outcome-driven strategy selection.
- **Overlap:** C4/C5 provide explicit signed high-volatility interactions.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** low / 3 of 4
- **Status / viability:** **KILL_PRE_SCREEN** / **LOW**
- **Reason:** It is a redundant conditioner already represented in C4/C5 and lacks a standalone AF1 direction.

### `af2a.f6.upside-downside-shock-asymmetry` — Upside versus downside shock asymmetry

Equivalent signed shocks may have different reversal or continuation behavior because spot liquidity and participant constraints are asymmetric.

- **Direction:** `direction_neutral_state_conditioning`
- **Causal inputs:** `close`
- **Formula:** `derived comparison only: source_pairs=c4_vs_c5,e5_signed_sides,e6_signed_sides; comparison_metric=signed_effect_difference; consume matched already-produced canonical AF1 results without defining an event, direction, hypothesis, or FDR test`
- **Lookback:** 1 completed 1-minute candles
- **Predeclared settings:** derived_contrast {"comparison_metric": "signed_effect_difference", "source_pairs": "c4_vs_c5|e5_signed_sides|e6_signed_sides"}
- **Frequency / sample:** low to medium per side; roughly 1-10 events/day/symbol; workable
- **Gross research margin:** 2C = 0.005006256569147291827961995503880
- **Economic assessment:** At least one side must show a gross effect above 0.5006% without pooling signs.
- **Primary failure:** A derived sign difference can be sampling noise and is not an independent hypothesis.
- **Overlap:** Consumes existing signed results only; no shock event is duplicated.
- **AF1 feasibility:** `condition_only_not_standalone_af1_hypothesis`
- **Complexity / explainability:** medium / 4 of 4
- **Status / viability:** **DEFER** / **BORDERLINE**
- **Reason:** DEFER as a derived comparison: it may compare matched signed results already produced by C4/C5, E5, and E6, but creates no AF1 definition and no FDR test.

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
