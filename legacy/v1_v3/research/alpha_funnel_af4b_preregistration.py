"""AF4B deterministic preregistration for DE1 completed-minute flow research.

This module only constructs and verifies a closed research catalog. It has no
market-data reader, network client, feature evaluator, or screening entrypoint.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "af4b-de1-preregistration-v1"
CATALOG_PATH = Path(__file__).resolve().parent / "alpha-funnel" / "af4b" / "catalog.json"
STARTING_HEAD = "61a652d89739b26a68cb8a86ab8b7d6061ebf7a4"
BASE_COST = "0.002503128284573645913980997751940"
STRESS_COST = "0.005006256569147291827961995503880"
DEVELOPMENT_START = "2024-01-01T00:00:00Z"
DEVELOPMENT_END = "2025-10-01T00:00:00Z"
COMMON_LAST_STATE_MINUTE = "2025-09-30T23:53:00Z"
SYMBOLS = ("BTCUSDT", "ETHUSDT")

SPEC_HASHES = {
    "docs/000_READ_FIRST.md": "b1847c8dfe68a91311a8699f35f673f615bda4361351c45d6d1dd792221ea502",
    "docs/001_PRODUCT_REQUIREMENTS.md": "15163ea527073610c8bbe6f25d236ffc8d78c49a2cf729a03b5625db061657e3",
    "docs/002_SYSTEM_ARCHITECTURE.md": "610d3c5c73fd145e2d5c2d92569d1a9aa9fbb5de9e1bab5a5d7bec7485308d57",
    "docs/003_DATA_ARCHITECTURE.md": "07278a2fff8b5221abbb7f1d3d3078ba1d71f933f22213cfe82319c718984535",
    "docs/004_FEATURE_ENGINE_SPECIFICATION.md": "3b7006dfd346a775aef49efc957269dcf8828d65a7fe885616f55e8f162cbbf0",
    "docs/005_ALPHA_ENGINE.md": "9e86e2ee999ec9022f6cd54485e94861b0f2feb11c539b9d154d4da6ad62f8cd",
    "docs/006_RISK_EXECUTION_SPECIFICATION.md": "072074721d5e6fa76aec65911deb8f568287d427a9633ac021339d530412e77a",
    "docs/007_VALIDATION_BACKTESTING.md": "f62a92d5f23f8e804ffed5aa2104887d481c6e03093d5bb9d1ccb66569e2fd2c",
    "docs/008_IMPLEMENTATION_GUIDE.md": "c290853ea1da8a82426bec775fdd27577d7be3d2f6702efeab8d63f612324e3f",
    "docs/009_ALPHA_DISCOVERY_FUNNEL_AF1.md": "655152845bee3daa0d90804339375054573568e02a24bf566bcb3dec200ffd3c",
    "docs/010_DATA_EDGE_RESEARCH_PROGRAM.md": "2dc1930702397618c81a3bd4bdb29da008a7f21ad9482af8088e466909dfda5f",
}

PARENT_RESEARCH = (
    {
        "name": "AF2A registered catalog",
        "path": "research/alpha-funnel/af2a/catalog.json",
        "sha256": "15978cd7e023a20f153271201370960725540eeb704e581e14b34d793cdc7be0",
        "identity": "060147848ab0d3aa3deb4a77fe0cf5af6bdd10a374587c24e5ff7a640a0a5d74",
    },
    {
        "name": "AF2A FDR plan",
        "path": "research/alpha-funnel/af2a/fdr_plan.json",
        "sha256": "b811ae84fc8702dc6f4d416356d4a7d158124a35ec752d2543981d6227cfa341",
        "identity": "32a6a1e1e916a0b012bd5ac22d747b3b57afa53a99e5153d0fb1a0685511a6ae",
    },
    {
        "name": "AF3 screening configuration",
        "path": "research/alpha-funnel/af3c/screening_config.json",
        "sha256": "4cfa0501c6a004e6ed75f3992333260040509718b951c68ca886146397abe6f1",
        "identity": "4cfa0501c6a004e6ed75f3992333260040509718b951c68ca886146397abe6f1",
    },
    {
        "name": "AF3 development split",
        "path": "research/alpha-funnel/af3b/split.json",
        "sha256": "d72445295f45c7cf17eae69d0b605f824a40fafa1f39b51a9eef2cd9df4898ed",
        "identity": "6c2e037cc844a73bff8ee80b51920b93bdfb05a3fe05730de7abdc8e09ff4a86",
    },
    {
        "name": "AF3 screen result reference",
        "path": "research/alpha-funnel/af3c/run.json",
        "sha256": "43ca2cf8a91fd7bf79ad592e0751e64d9154115df0100f175a63de3d1ea0d9a9",
        "identity": "78acd6bffa78189741a25ec5d3e1f1f1829297becf927f402bf024436ada8e67",
    },
    {
        "name": "AF4A WATCH forensics",
        "path": "research/alpha-funnel/af4a/run.json",
        "sha256": "223e488221c25b8de6487a96e4f50d2dd8caf67455624f62325cad5ef2295b86",
        "identity": "79d5de90098805f206e3cfc458fe9b204732c96dc65e9486e5ba509202099084",
    },
    {
        "name": "MF1 participation-information feasibility",
        "path": "research/phase-4d-mf1/run.json",
        "sha256": "66b086acbf189de30981999dce2388ef079a1c1ff165f009e60f54b0ecb4ffbd",
        "identity": "dd84b1245c05207a133d03cadf713583000f8c7c4e6e59b0fa17992b8cec958c",
    },
)

ALLOWED_FORMULA_INPUTS = frozenset({
    "de1.aggressive_buy_event_count",
    "de1.aggressive_sell_event_count",
    "de1.aggressive_buy_quote_notional",
    "de1.aggressive_sell_quote_notional",
    "de1.total_quote_notional",
    "candle.open",
    "candle.close",
})


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one permitted AF4B JSON representation."""
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _identified(value: dict[str, Any], field: str) -> dict[str, Any]:
    result = deepcopy(value)
    result[field] = digest(canonical_json_bytes(value))
    return result


def _hypotheses() -> list[dict[str, Any]]:
    common = {
        "direction": "UP",
        "production_short_authorized": False,
        "parameter_sets": [{"parameter_set_id": "fixed-v1"}],
        "state_minute_semantics": "UTC half-open [t,t+1m), completed only",
        "state_status": "validated completed-minute state finalized under DE1G policy",
        "missing_join_policy": "ineligible; no forward-fill, interpolation, or nearest-neighbor match",
        "human_explainability_score": 4,
        "individual_trade_inference": False,
    }
    rows = [
        {
            "hypothesis_id": "af4b.h1.immediate-positive-quote-flow",
            "mechanism": "Net aggressive buying in the latest completed minute may express short-lived informed demand or urgency.",
            "formula": "I_t=(Q_buy_t-Q_sell_t)/Q_total_t; trigger I_t>0",
            "required_inputs": ["de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional", "de1.total_quote_notional"],
            "denominator_policy": "Q_total_t must be strictly positive; otherwise I_t is undefined and the observation is ineligible; no epsilon or zero substitution",
            "eligibility_rule": "exact joined state and candle support; Q_total_t>0; I_t>0",
            "lookback_minutes": 1,
            "first_eligible_state_minute": DEVELOPMENT_START,
            "symbol_bindings": [{"source_symbol": s, "target_symbol": s} for s in SYMBOLS],
            "horizons_minutes": [1],
            "parameter_sets": [{"parameter_set_id": "sign-zero-v1", "imbalance_threshold": "0"}],
            "candle_only_comparator": "unconditional eligible long outcome on identical target, horizon, and common support",
        },
        {
            "hypothesis_id": "af4b.h2.five-minute-positive-quote-flow-persistence",
            "mechanism": "Aggressive demand may persist because large orders are split over a short sequence of completed minutes.",
            "formula": "I_5t=sum_{j=0..4}(Q_buy_{t-j}-Q_sell_{t-j})/sum_{j=0..4}Q_total_{t-j}; trigger I_5t>0",
            "required_inputs": ["de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional", "de1.total_quote_notional"],
            "denominator_policy": "the five-minute Q_total sum must be strictly positive; otherwise I_5t is undefined and the observation is ineligible; no epsilon or zero substitution",
            "eligibility_rule": "five consecutive exact joined completed states wholly inside DEVELOPMENT; positive five-minute denominator; I_5t>0",
            "lookback_minutes": 5,
            "first_eligible_state_minute": "2024-01-01T00:04:00Z",
            "symbol_bindings": [{"source_symbol": s, "target_symbol": s} for s in SYMBOLS],
            "horizons_minutes": [5],
            "parameter_sets": [{"parameter_set_id": "five-minute-sign-zero-v1", "lookback_minutes": 5, "imbalance_threshold": "0"}],
            "candle_only_comparator": "unconditional eligible long outcome on identical target, horizon, and common support",
        },
        {
            "hypothesis_id": "af4b.h3.positive-quote-flow-acceleration",
            "mechanism": "A rise in signed aggressive pressure may add information beyond its current level.",
            "formula": "I_t=(Q_buy_t-Q_sell_t)/Q_total_t; trigger I_t>I_{t-1}",
            "required_inputs": ["de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional", "de1.total_quote_notional"],
            "denominator_policy": "Q_total_t and Q_total_{t-1} must each be strictly positive; otherwise the corresponding imbalance is undefined and the observation is ineligible; no epsilon or zero substitution",
            "eligibility_rule": "exact joined completed states at t-1 and t wholly inside DEVELOPMENT; both denominators positive; I_t>I_{t-1}",
            "lookback_minutes": 2,
            "first_eligible_state_minute": "2024-01-01T00:01:00Z",
            "symbol_bindings": [{"source_symbol": s, "target_symbol": s} for s in SYMBOLS],
            "horizons_minutes": [1],
            "parameter_sets": [{"parameter_set_id": "one-minute-change-v1", "comparison_lag_minutes": 1}],
            "candle_only_comparator": "unconditional eligible long outcome on identical target, horizon, and common support",
        },
        {
            "hypothesis_id": "af4b.h4.sell-flow-absorption-reversal-up",
            "mechanism": "Heavy aggressive selling that fails to depress the completed-minute candle may indicate passive bid absorption and an upward reversal.",
            "formula": "Q_sell_t>=2*Q_buy_t AND candle.close_t>=candle.open_t",
            "required_inputs": ["de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional", "de1.total_quote_notional", "candle.open", "candle.close"],
            "denominator_policy": "no division; Q_total_t must be strictly positive so a zero-activity minute cannot satisfy the comparison",
            "eligibility_rule": "exact state/candle UTC-minute join; Q_total_t>0; Q_sell_t>=2*Q_buy_t; completed candle close_t>=open_t",
            "lookback_minutes": 1,
            "first_eligible_state_minute": DEVELOPMENT_START,
            "symbol_bindings": [{"source_symbol": s, "target_symbol": s} for s in SYMBOLS],
            "horizons_minutes": [5],
            "parameter_sets": [{"parameter_set_id": "sell-two-to-one-v1", "minimum_sell_to_buy_multiple": "2"}],
            "candle_only_comparator": "completed candle close_t>=open_t on identical target, horizon, eligibility, and common support, with the flow clause removed",
        },
        {
            "hypothesis_id": "af4b.h5.buy-side-aggregate-record-size-asymmetry",
            "mechanism": "Larger average quote notional per BUY-side AggregateTrade record than per SELL-side record may reveal directional urgency or size composition.",
            "formula": "A_buy_t=Q_buy_t/N_buy_t; A_sell_t=Q_sell_t/N_sell_t; trigger A_buy_t>A_sell_t",
            "required_inputs": ["de1.aggressive_buy_event_count", "de1.aggressive_sell_event_count", "de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional"],
            "denominator_policy": "N_buy_t and N_sell_t must each be strictly positive; otherwise its side average is undefined and the observation is ineligible; no epsilon or zero substitution",
            "eligibility_rule": "exact joined completed state; both side event counts strictly positive; A_buy_t>A_sell_t",
            "lookback_minutes": 1,
            "first_eligible_state_minute": DEVELOPMENT_START,
            "symbol_bindings": [{"source_symbol": s, "target_symbol": s} for s in SYMBOLS],
            "horizons_minutes": [5],
            "parameter_sets": [{"parameter_set_id": "side-average-order-v1", "comparison": "strictly_greater"}],
            "candle_only_comparator": "unconditional eligible long outcome on identical target, horizon, and common support",
            "semantic_warning": "A side average is per Binance AggregateTrade record, never per individual execution or trade-ID range.",
        },
        {
            "hypothesis_id": "af4b.h6.btc-positive-flow-leads-eth",
            "mechanism": "Completed-minute BTC aggressive buying pressure may lead ETH short-horizon price response.",
            "formula": "I_BTC,t=(Q_buy_BTC,t-Q_sell_BTC,t)/Q_total_BTC,t; trigger I_BTC,t>0; evaluate ETH",
            "required_inputs": ["de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional", "de1.total_quote_notional"],
            "denominator_policy": "BTC Q_total_t must be strictly positive; otherwise I_BTC,t is undefined and the observation is ineligible; no epsilon or zero substitution",
            "eligibility_rule": "BTC state and ETH target candle share the exact UTC minute t; both required validated streams present; BTC denominator positive; I_BTC,t>0",
            "lookback_minutes": 1,
            "first_eligible_state_minute": DEVELOPMENT_START,
            "symbol_bindings": [{"source_symbol": "BTCUSDT", "target_symbol": "ETHUSDT"}],
            "horizons_minutes": [5],
            "parameter_sets": [{"parameter_set_id": "btc-to-eth-sign-zero-v1", "imbalance_threshold": "0"}],
            "candle_only_comparator": "unconditional eligible ETH long outcome on identical horizon and common support",
            "cross_symbol_alignment": "exact same UTC state minute only; no lag search, nearest-neighbor match, forward-fill, interpolation, or symmetric ETH-to-BTC duplicate",
        },
    ]
    result = []
    for row in rows:
        value = {**common, **row}
        result.append(_identified(value, "hypothesis_definition_sha256"))
    return result


def _evaluations(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for hypothesis in hypotheses:
        for parameter_set in hypothesis["parameter_sets"]:
            for binding in hypothesis["symbol_bindings"]:
                for horizon in hypothesis["horizons_minutes"]:
                    value = {
                        "hypothesis_id": hypothesis["hypothesis_id"],
                        "hypothesis_definition_sha256": hypothesis["hypothesis_definition_sha256"],
                        "parameter_set_id": parameter_set["parameter_set_id"],
                        "source_symbol": binding["source_symbol"],
                        "target_symbol": binding["target_symbol"],
                        "direction": hypothesis["direction"],
                        "horizon_minutes": horizon,
                        "dataset_role": "DEVELOPMENT",
                        "evaluator_id": "af4b-event-screen-tplus2-v1",
                    }
                    rows.append(_identified(value, "evaluation_id"))
    return sorted(rows, key=lambda x: (x["hypothesis_id"], x["source_symbol"],
                                      x["target_symbol"], x["horizon_minutes"],
                                      x["parameter_set_id"]))


def _catalog_without_id() -> dict[str, Any]:
    hypotheses = _hypotheses()
    evaluations = _evaluations(hypotheses)
    fdr = _identified({
        "method": "Benjamini-Hochberg",
        "alpha": "0.05",
        "authority": "AF1/AF3 closed-family rule",
        "closed": True,
        "registered_evaluation_count": len(evaluations),
        "evaluation_ids": [row["evaluation_id"] for row in evaluations],
        "undefined_test_policy": "retain every registered evaluation in the family and assign conservative p=1; never drop a failed or undefined row",
    }, "fdr_family_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": "AF4B",
        "title": "DE1 Binance Spot Aggregate-Trade Flow Hypothesis Preregistration",
        "scope": {
            "research_only": True,
            "preregistration_only": True,
            "experiment_executed": False,
            "production_authorized": False,
            "market_data_acquired": False,
            "predictive_observations_inspected": False,
            "model_training_authorized": False,
        },
        "lineage": {
            "starting_main_head": STARTING_HEAD,
            "specifications": [{"path": path, "sha256": value}
                               for path, value in SPEC_HASHES.items()],
            "parent_research": list(PARENT_RESEARCH),
        },
        "information_family": {
            "provider": "Binance",
            "market": "Spot",
            "de1_family": "aggregate_trade_minute_state",
            "state_schema_version": "aggregate-trade-minute-state-v1",
            "aggregation_version": "aggregate-trade-minute-aggregation-v1",
            "minute_semantics": "PT1M:[start,end)",
            "historical_source_requirement": "validated_source_complete",
            "research_availability_requirement": "finalized_under_policy with healthy source coverage and all required source watermarks through minute end",
            "lateness_allowance_seconds": 5,
            "allowed_formula_inputs": sorted(ALLOWED_FORMULA_INPUTS),
            "prohibited_inputs": [
                "raw AggregateTrade events as signal triggers", "partial/open minute state",
                "individual execution counts or sizes", "trade-ID-range inferred trades",
                "order book or L2", "derivatives", "future information",
            ],
        },
        "alignment_contract": {
            "join_keys": ["symbol", "exact_UTC_minute"],
            "cross_symbol_exception": "af4b.h6 uses exact UTC minute with BTCUSDT source and ETHUSDT target",
            "missing_side_policy": "observation ineligible",
            "nearest_neighbor": False,
            "forward_fill": False,
            "interpolation": False,
        },
        "execution_outcome_contract": {
            "state_interval": "minute t is [t,t+1m)",
            "earliest_knowable_time": "t+1m+5s, conditional on DE1G finalization and healthy source conditions",
            "entry_proxy": "open of the completed-candle interval beginning at t+2m",
            "entry_minute_offset": 2,
            "t_plus_1_open_allowed": False,
            "holding_period_definition": "H consecutive one-minute candles beginning with the t+2m entry candle",
            "exit_proxy": "close of the candle beginning at t+(H+1)m, observed at t+(H+2)m",
            "gross_return_formula": "close[t+(H+1)m]/open[t+2m]-1",
            "directional_treatment": "UP means an unlevered long Spot research return; DOWN is absent; no short sale is authorized",
            "deoverlap_rule": "sort eligible state minutes ascending, retain the earliest, then retain only a state minute at least H minutes after the prior retained state minute",
            "common_support_rule": "all evaluations use common state-minute support ending with 2025-09-30T23:53:00Z, set by the five-minute horizon and t+2 entry",
            "af1_compatibility": "AF1 next-open t+1 execution is causally invalid for DE1G state; the future AF4B evaluator must implement this t+2 contract without changing AF1",
            "evaluator_id": "af4b-event-screen-tplus2-v1",
        },
        "cost_contract": {
            "unit": "simple_return_fraction_of_entry_notional",
            "application": "subtract each declared round-trip rate exactly once from mean directional gross return",
            "scenarios": [
                {"name": "base", "round_trip_rate": BASE_COST},
                {"name": "stress_2c", "round_trip_rate": STRESS_COST},
            ],
            "optimization_allowed": False,
        },
        "statistical_contract": {
            "methodology": "reuse AF1/AF3 event screen with only the registered t+2 outcome extension",
            "minimum_deoverlapped_events": 100,
            "gross_mean_test_null": "mean directional gross return <= 0",
            "descriptive_t_statistic": True,
            "deterministic_bootstrap": {"samples": 200, "confidence": "0.95", "random_seed": 20260914},
            "temporal_robustness": {
                "block_count": 8,
                "block_definition": "eight fixed equal chronological DEVELOPMENT blocks inherited from AF3; no result-dependent boundaries",
                "minimum_events_per_block": 10,
                "minimum_qualified_block_coverage": "0.75",
                "minimum_positive_blocks_for_maximum_robustness": 6,
            },
            "score_bands": {
                "M": ["0", "0.001251564142286822956990498875970", BASE_COST, STRESS_COST],
                "S": ["0.5", "1.5", "2.0", "3.0"],
                "F": ["0.1", "0.5", "2", "5"],
                "R": ["0.25", "0.50", "0.75", "1.00"],
            },
            "promotion_gates": {
                "base_cost_adjusted_expectancy_at_least": BASE_COST,
                "deoverlapped_event_count_at_least": 100,
                "BH_q_value_at_most": "0.05",
                "research_viability_score_at_least": 13,
            },
            "no_gate_weakening": True,
        },
        "incremental_information_contract": {
            "question": "Does completed-minute DE1 flow add robust economically useful information beyond the exhausted completed-candle search?",
            "comparators": "each hypothesis declares one pre-specified candle-only or unconditional same-support comparator; comparator results are descriptive and are not extra FDR tests",
            "new_information_rule": "a primary must pass every unchanged promotion gate and have strictly positive aggregate gross-expectancy difference versus its declared comparator before it can be described as incremental",
            "candle_restatement_rule": "a primary that passes promotion gates but has non-positive comparator difference is candle restatement/inconclusive and cannot advance",
            "sample_artifact_rule": "failure of count or block coverage is reported as sample/coverage weakness and cannot be rescued by effect magnitude",
            "regime_rule": "fixed eight-block candidate and comparator differences are reported; no block may be selected, pooled, or redefined after results",
            "economic_irrelevance_rule": "statistical evidence below the unchanged base-cost promotion gate cannot advance",
        },
        "data_contract": {
            "allowed_role": "DEVELOPMENT",
            "development_interval": {"start_inclusive": DEVELOPMENT_START, "end_exclusive": DEVELOPMENT_END},
            "required_symbols": list(SYMBOLS),
            "required_candle_interval": {"start_inclusive": DEVELOPMENT_START, "end_exclusive": DEVELOPMENT_END},
            "required_de1_state_interval": {"start_inclusive": DEVELOPMENT_START, "end_exclusive": DEVELOPMENT_END},
            "required_de1_daily_partition_dates": {"first_inclusive": "2024-01-01", "last_inclusive": "2025-09-30"},
            "first_eligible_state_minute_by_hypothesis": {h["hypothesis_id"]: h["first_eligible_state_minute"] for h in hypotheses},
            "common_last_eligible_state_minute": COMMON_LAST_STATE_MINUTE,
            "lookback_before_development_allowed": False,
            "data_after_development_allowed": False,
            "identity_binding_rule": "the next phase must acquire only the declared interval, validate it, and freeze exact immutable Candle and DE1 dataset IDs and content hashes before any observation values, signal counts, or outcomes are opened",
            "prohibited_roles": ["SCREENING_VALIDATION", "SEALED_OOS", "RESERVE", "2026_RESEARCH"],
            "network_acquisition_in_this_phase": False,
        },
        "universe_limits": {
            "target_primary_hypotheses": 6,
            "hard_max_primary_hypotheses": 8,
            "hard_max_hypothesis_parameter_combinations_before_expansion": 12,
            "registered_primary_hypotheses": len(hypotheses),
            "registered_hypothesis_parameter_combinations_before_expansion": sum(len(h["parameter_sets"]) for h in hypotheses),
            "registered_evaluations_after_symbol_horizon_expansion": len(evaluations),
        },
        "hypotheses": hypotheses,
        "evaluations": evaluations,
        "fdr_family": fdr,
        "next_phase_constraint": "acquire and materialize only the frozen DEVELOPMENT Candle and DE1 ranges, bind immutable IDs before opening observations, then execute exactly this catalog with af4b-event-screen-tplus2-v1; no catalog edits after data opening",
    }


def build_catalog() -> dict[str, Any]:
    """Build the frozen catalog and its content-derived identity."""
    return _identified(_catalog_without_id(), "catalog_id")


def _semantic_validate(document: dict[str, Any]) -> None:
    require(document["schema_version"] == SCHEMA_VERSION, "schema version mismatch")
    scope = document["scope"]
    require(scope == {
        "research_only": True, "preregistration_only": True,
        "experiment_executed": False, "production_authorized": False,
        "market_data_acquired": False, "predictive_observations_inspected": False,
        "model_training_authorized": False,
    }, "scope must remain preregistration-only")
    hypotheses = document["hypotheses"]
    limits = document["universe_limits"]
    require(len(hypotheses) <= limits["hard_max_primary_hypotheses"], "primary hypothesis hard maximum exceeded")
    combinations = sum(len(h["parameter_sets"]) for h in hypotheses)
    require(combinations <= limits["hard_max_hypothesis_parameter_combinations_before_expansion"], "hypothesis-parameter hard maximum exceeded")
    require(len(hypotheses) == 6 and combinations == 6, "frozen universe must contain exactly six one-parameter hypotheses")
    require(len({h["hypothesis_id"] for h in hypotheses}) == len(hypotheses), "duplicate hypothesis ID")
    for hypothesis in hypotheses:
        inputs = set(hypothesis["required_inputs"])
        require(inputs <= ALLOWED_FORMULA_INPUTS, "unknown or prohibited formula input")
        require(not any("raw" in value.lower() for value in inputs), "raw AggregateTrade input prohibited")
        require(hypothesis["state_status"] == "validated completed-minute state finalized under DE1G policy", "completed finalized state required")
        require(hypothesis["direction"] == "UP" and not hypothesis["production_short_authorized"], "only long Spot research outcomes registered")
        require("undefined" in hypothesis["denominator_policy"] or hypothesis["hypothesis_id"].startswith("af4b.h4."), "denominator behavior must be explicit")
        require(not hypothesis["individual_trade_inference"], "individual-trade inference prohibited")
        require(hypothesis["horizons_minutes"] in ([1], [5]), "unregistered horizon")
        require(len(hypothesis["parameter_sets"]) == 1, "undeclared parameter set")
    execution = document["execution_outcome_contract"]
    require(execution["entry_minute_offset"] == 2 and not execution["t_plus_1_open_allowed"], "entry must occur after DE1G knowability")
    require(document["information_family"]["lateness_allowance_seconds"] == 5, "DE1G lateness policy mismatch")
    data = document["data_contract"]
    require(data["allowed_role"] == "DEVELOPMENT", "only DEVELOPMENT role permitted")
    require(data["development_interval"] == {"start_inclusive": DEVELOPMENT_START, "end_exclusive": DEVELOPMENT_END}, "development interval mismatch")
    require("SCREENING_VALIDATION" in data["prohibited_roles"] and "SEALED_OOS" in data["prohibited_roles"] and "2026_RESEARCH" in data["prohibited_roles"], "protected roles must be rejected")
    require(not data["network_acquisition_in_this_phase"], "network acquisition prohibited")
    require(data["required_de1_daily_partition_dates"] == {"first_inclusive": "2024-01-01", "last_inclusive": "2025-09-30"}, "data boundary mismatch")
    costs = document["cost_contract"]["scenarios"]
    require(costs == [{"name": "base", "round_trip_rate": BASE_COST}, {"name": "stress_2c", "round_trip_rate": STRESS_COST}], "cost scenarios changed")
    with localcontext() as context:
        context.prec = 80
        require(Decimal(STRESS_COST) == Decimal(2) * Decimal(BASE_COST),
                "stress cost must be exactly 2C")
    temporal = document["statistical_contract"]["temporal_robustness"]
    require(temporal["block_count"] == 8 and temporal["minimum_events_per_block"] == 10 and temporal["minimum_qualified_block_coverage"] == "0.75" and temporal["minimum_positive_blocks_for_maximum_robustness"] == 6, "temporal robustness rules changed")
    evaluations = document["evaluations"]
    require(len(evaluations) == 11, "frozen expansion must contain exactly eleven evaluations")
    require({(r["source_symbol"], r["target_symbol"]) for r in evaluations} <= {("BTCUSDT", "BTCUSDT"), ("ETHUSDT", "ETHUSDT"), ("BTCUSDT", "ETHUSDT")}, "symbol scope changed")
    family = document["fdr_family"]
    require(family["closed"] and family["registered_evaluation_count"] == len(evaluations), "FDR family must remain closed")
    require(family["evaluation_ids"] == [r["evaluation_id"] for r in evaluations], "every evaluation must remain in FDR family")
    require("p=1" in family["undefined_test_policy"], "undefined tests must receive conservative p=1")


def validate_catalog(document: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless a document is exactly the frozen AF4B catalog."""
    require(isinstance(document, dict), "catalog must be an object")
    supplied = document.get("catalog_id")
    without_id = {key: value for key, value in document.items() if key != "catalog_id"}
    require(supplied == digest(canonical_json_bytes(without_id)), "catalog ID mismatch")
    _semantic_validate(document)
    require(canonical_json_bytes(document) == canonical_json_bytes(build_catalog()), "catalog differs from frozen preregistration")
    return deepcopy(document)


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """Load only canonical bytes and then verify all frozen semantics and IDs."""
    payload = path.read_bytes()
    document = json.loads(payload)
    require(payload == canonical_json_bytes(document), "catalog is not canonical JSON")
    return validate_catalog(document)


def write_catalog(path: Path = CATALOG_PATH) -> str:
    """Write the deterministic preregistration once, or verify identical bytes."""
    document = build_catalog()
    payload = canonical_json_bytes(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == payload, "immutable catalog conflict")
    else:
        with path.open("xb") as stream:
            stream.write(payload)
    load_catalog(path)
    return document["catalog_id"]


if __name__ == "__main__":
    print(write_catalog())
