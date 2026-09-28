"""Frozen AF4C shadow preregistration; no data reader or hypothesis evaluator.

The builder is a closed declaration, not a configurable search space. Validation
compares canonical bytes to this declaration, including types and every field,
even when a caller recomputes all supplied content identities.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "af4c-de1-preregistration-v1"
EVALUATOR_ID = "af4c-impact-screen-tplus2-v1"
STARTING_HEAD = "3722019f461ea28eb9e49c2de27e6cd259dfb609"
CATALOG_PATH = Path(__file__).resolve().parent / "alpha-funnel" / "af4c" / "catalog.json"
BASE_COST = "0.002503128284573645913980997751940"
STRESS_COST = "0.005006256569147291827961995503880"
DEVELOPMENT_START = "2024-01-01T00:00:00Z"
DEVELOPMENT_END = "2025-10-01T00:00:00Z"
COMMON_LAST_STATE_MINUTE = "2025-09-30T23:53:00Z"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
ALLOWED_FORMULA_INPUTS = frozenset({
    "de1.total_base_quantity", "de1.total_quote_notional",
    "de1.aggressive_buy_base_quantity", "de1.aggressive_sell_base_quantity",
    "de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional",
    "candle.open", "candle.close",
})

# Literal pins populated from repository specifications and preregistration only.
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
    "docs/009_ALPHA_DISCOVERY_FUNNEL_AF1.md": "ad7803207d6939fd1c60e509fa7fcd6d7edc705a6280add43ec752e5b5c58644",
    "docs/010_DATA_EDGE_RESEARCH_PROGRAM.md": "2dc1930702397618c81a3bd4bdb29da008a7f21ad9482af8088e466909dfda5f",
    "docs/011_ALPHA_DISCOVERY_FUNNEL_AF4B_DE1.md": "6bdeb64b9a73e6e4e1b23165598228efd6c5b403955e9942f15f545834388d8e",
}
AF4B_LINEAGE = {
    "name": "AF4B frozen PREREGISTRATION only",
    "path": "research/alpha-funnel/af4b/catalog.json",
    "sha256": "576f5a41de6701855a6c6f68283643f94bfeda3590342f1cce44fc76fa6cc273",
    "catalog_id": "7bf403626ef205c2b4026d2b9ceecb7ff6c7ab6bd19dbbfcfc726625d5034346",
    "fdr_family_id": "2c36097242d3e61f884fae8c61c5998cc237f84c376fe5a3b60a2de3d9e55fc0",
}
INHERITED_CONTRACTS = {
    "execution_outcome_contract": {
        "common_support_rule": "all evaluations use common state-minute support ending with 2025-09-30T23:53:00Z, set by the five-minute horizon and t+2 entry",
        "deoverlap_rule": "sort eligible state minutes ascending, retain the earliest, then retain only a state minute at least H minutes after the prior retained state minute",
        "directional_treatment": "UP means an unlevered long Spot research return; DOWN is absent; no short sale is authorized",
        "earliest_knowable_time": "t+1m+5s, conditional on DE1G finalization and healthy source conditions",
        "entry_minute_offset": 2,
        "entry_proxy": "open of the completed-candle interval beginning at t+2m",
        "exit_proxy": "close of the candle beginning at t+(H+1)m, observed at t+(H+2)m",
        "gross_return_formula": "close[t+(H+1)m]/open[t+2m]-1",
        "holding_period_definition": "H consecutive one-minute candles beginning with the t+2m entry candle",
        "state_interval": "minute t is [t,t+1m)",
        "t_plus_1_open_allowed": False,
    },
    "cost_contract": {
        "application": "subtract each declared round-trip rate exactly once from mean directional gross return",
        "optimization_allowed": False,
        "scenarios": [
            {
                "name": "base",
                "round_trip_rate": "0.002503128284573645913980997751940",
            },
            {
                "name": "stress_2c",
                "round_trip_rate": "0.005006256569147291827961995503880",
            },
        ],
        "unit": "simple_return_fraction_of_entry_notional",
    },
    "statistical_contract": {
        "descriptive_t_statistic": True,
        "deterministic_bootstrap": {
            "confidence": "0.95",
            "random_seed": 20260914,
            "samples": 200,
        },
        "gross_mean_test_null": "mean directional gross return <= 0",
        "methodology": "reuse AF1/AF3 event screen with only the registered t+2 outcome extension",
        "minimum_deoverlapped_events": 100,
        "no_gate_weakening": True,
        "promotion_gates": {
            "BH_q_value_at_most": "0.05",
            "base_cost_adjusted_expectancy_at_least": "0.002503128284573645913980997751940",
            "deoverlapped_event_count_at_least": 100,
            "research_viability_score_at_least": 13,
        },
        "score_bands": {
            "F": ["0.1", "0.5", "2", "5"],
            "M": ["0", "0.001251564142286822956990498875970", "0.002503128284573645913980997751940", "0.005006256569147291827961995503880"],
            "R": ["0.25", "0.50", "0.75", "1.00"],
            "S": ["0.5", "1.5", "2.0", "3.0"],
        },
        "temporal_robustness": {
            "block_count": 8,
            "block_definition": "eight fixed equal chronological DEVELOPMENT blocks inherited from AF3; no result-dependent boundaries",
            "minimum_events_per_block": 10,
            "minimum_positive_blocks_for_maximum_robustness": 6,
            "minimum_qualified_block_coverage": "0.75",
        },
    },
    "incremental_information_contract": {
        "candle_restatement_rule": "a primary that passes promotion gates but has non-positive comparator difference is candle restatement/inconclusive and cannot advance",
        "comparators": "each hypothesis declares one pre-specified candle-only or unconditional same-support comparator; comparator results are descriptive and are not extra FDR tests",
        "economic_irrelevance_rule": "statistical evidence below the unchanged base-cost promotion gate cannot advance",
        "new_information_rule": "a primary must pass every unchanged promotion gate and have strictly positive aggregate gross-expectancy difference versus its declared comparator before it can be described as incremental",
        "question": "Does completed-minute DE1 transaction-price impact, VWAP displacement, and cross-symbol price/flow divergence contain robust economically meaningful information beyond previously tested candle and simple signed-flow mechanisms?",
        "regime_rule": "fixed eight-block candidate and comparator differences are reported; no block may be selected, pooled, or redefined after results",
        "sample_artifact_rule": "failure of count or block coverage is reported as sample/coverage weakness and cannot be rescued by effect magnitude",
    },
    "data_contract": {
        "allowed_role": "DEVELOPMENT",
        "common_last_eligible_state_minute": "2025-09-30T23:53:00Z",
        "data_after_development_allowed": False,
        "development_interval": {
            "end_exclusive": "2025-10-01T00:00:00Z",
            "start_inclusive": "2024-01-01T00:00:00Z",
        },
        "lookback_before_development_allowed": False,
        "network_acquisition_in_this_phase": False,
        "prohibited_roles": ["SCREENING_VALIDATION", "SEALED_OOS", "RESERVE", "2026_RESEARCH"],
        "required_candle_interval": {
            "end_exclusive": "2025-10-01T00:00:00Z",
            "start_inclusive": "2024-01-01T00:00:00Z",
        },
        "required_de1_daily_partition_dates": {
            "first_inclusive": "2024-01-01",
            "last_inclusive": "2025-09-30",
        },
        "required_de1_state_interval": {
            "end_exclusive": "2025-10-01T00:00:00Z",
            "start_inclusive": "2024-01-01T00:00:00Z",
        },
        "required_symbols": ["BTCUSDT", "ETHUSDT"],
    },
}


def canonical_json_bytes(value: Any) -> bytes:
    """Sorted compact UTF-8 JSON with finite values and one final LF."""
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
        "human_explainability_score": 4,
        "state_status": "validated completed-minute state finalized under DE1G policy",
        "state_minute_semantics": "UTC half-open [t,t+1m), completed only",
        "missing_join_policy": "ineligible; no forward-fill, interpolation, or nearest-neighbor match",
        "individual_trade_inference": False,
        "denominator_policy": "all required derived values must be defined under derived_definitions; undefined means ineligible; no epsilon, zero substitution, or imputation",
        "comparator_support": "identical target, horizon, eligibility chronology and common support; apply the same chronological de-overlap independently to each trigger",
    }
    vwap_inputs = ["de1.total_base_quantity", "de1.total_quote_notional", "candle.close"]
    flow_inputs = ["de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional"]
    own_bindings = [{"source_symbols": [s], "target_symbol": s} for s in SYMBOLS]
    rows = [
        {
            "hypothesis_id": "af4c.h1.flow-confirmed-positive-impact",
            "mechanism": "Aggressive net buying accompanied by a close above the minute's aggregate-trade VWAP indicates buyer-driven impact persisted through the completed minute rather than immediately reverting.",
            "formula": "I_t > 0 AND D_t > 0",
            "required_inputs": vwap_inputs + flow_inputs,
            "eligibility_rule": "exact completed state/candle join at t; Q_t > 0 AND B_t > 0 AND V_t > 0; I_t and D_t defined",
            "lookback_minutes": 1,
            "first_eligible_state_minute": DEVELOPMENT_START,
            "symbol_bindings": own_bindings,
            "horizons_minutes": [1],
            "parameter_sets": [{"parameter_set_id": "flow-impact-sign-v1"}],
            "comparator_formula": "unconditional eligible long outcome",
        },
        {
            "hypothesis_id": "af4c.h2.buy-sell-vwap-separation",
            "mechanism": "Higher aggressive BUY-side quantity-weighted price than SELL-side price, with a close above total transaction VWAP, is consistent with upward price progression/adverse selection.",
            "formula": "V_buy_t > V_sell_t AND D_t > 0",
            "required_inputs": vwap_inputs + flow_inputs + [
                "de1.aggressive_buy_base_quantity", "de1.aggressive_sell_base_quantity", "candle.open"],
            "eligibility_rule": "exact completed state/candle join at t; V_t, V_buy_t, V_sell_t and D_t defined",
            "lookback_minutes": 1,
            "first_eligible_state_minute": DEVELOPMENT_START,
            "symbol_bindings": own_bindings,
            "horizons_minutes": [5],
            "parameter_sets": [{"parameter_set_id": "side-vwap-order-v1"}],
            "comparator_formula": "close_t > open_t",
            "semantic_warning": "BUY/SELL quantities are Binance AggregateTrade aggressor-side aggregates; no claim about the distribution of individual executions",
        },
        {
            "hypothesis_id": "af4c.h3.positive-impact-acceleration",
            "mechanism": "Increasing positive close-to-transaction-VWAP displacement may indicate strengthening realized impact rather than merely positive raw signed flow.",
            "formula": "D_t > 0 AND D_t > D_{t-1}",
            "required_inputs": vwap_inputs + ["candle.open"],
            "eligibility_rule": "exact completed states and candles at t-1 and t inside DEVELOPMENT; V_t, V_{t-1}, D_t and D_{t-1} defined",
            "lookback_minutes": 2,
            "first_eligible_state_minute": "2024-01-01T00:01:00Z",
            "symbol_bindings": own_bindings,
            "horizons_minutes": [1],
            "parameter_sets": [{"parameter_set_id": "impact-change-one-minute-v1"}],
            "comparator_formula": "R_t > 0 AND R_t > R_{t-1}",
        },
        {
            "hypothesis_id": "af4c.h4.five-minute-flow-impact-persistence",
            "mechanism": "Positive aggressive quote flow over a complete five-minute window leaving the latest close above its quantity-weighted transaction price may indicate durable demand with realized price impact.",
            "formula": "I_5t > 0 AND close_t > V_5t",
            "required_inputs": vwap_inputs + flow_inputs + ["candle.open"],
            "eligibility_rule": "all five consecutive completed DE1 minutes t-4 through t and required candles inside DEVELOPMENT; Q_5t > 0 AND B_5t > 0; I_5t and V_5t defined",
            "lookback_minutes": 5,
            "first_eligible_state_minute": "2024-01-01T00:04:00Z",
            "symbol_bindings": own_bindings,
            "horizons_minutes": [5],
            "parameter_sets": [{"parameter_set_id": "five-minute-flow-impact-sign-v1"}],
            "comparator_formula": "close_t > open_{t-4}",
        },
        {
            "hypothesis_id": "af4c.h5.btc-impact-led-eth-catchup",
            "mechanism": "BTC may lead short-horizon Spot price discovery: simultaneous positive BTC aggressive flow and realized impact while ETH is at or below its own transaction VWAP may identify ETH catch-up.",
            "formula": "I_BTC,t > 0 AND D_BTC,t > 0 AND D_ETH,t <= 0",
            "required_inputs": vwap_inputs + flow_inputs + ["candle.open"],
            "eligibility_rule": "BTCUSDT and ETHUSDT validated completed states and candles at the exact same UTC minute t; all required BTC and ETH VWAP/flow denominators defined",
            "lookback_minutes": 1,
            "first_eligible_state_minute": DEVELOPMENT_START,
            "symbol_bindings": [{"source_symbols": list(SYMBOLS), "target_symbol": "ETHUSDT"}],
            "horizons_minutes": [5],
            "parameter_sets": [{"parameter_set_id": "btc-impact-eth-lag-v1"}],
            "comparator_formula": "BTC close_t > BTC open_t AND ETH close_t <= ETH open_t",
            "alignment": "exact same UTC state minute only; no lag search, nearest-neighbor alignment, forward-fill, or interpolation",
            "symmetric_eth_to_btc_allowed": False,
            "af4b_nonidentity": "requires simultaneous BTC realized impact and ETH VWAP-relative lag; not merely BTC positive flow predicts ETH; no tuned BTC-flow threshold",
        },
    ]
    return [_identified({**common, **row}, "hypothesis_definition_sha256") for row in rows]


def _evaluations(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for hypothesis in hypotheses:
        for binding in hypothesis["symbol_bindings"]:
            rows.append(_identified({
                "hypothesis_id": hypothesis["hypothesis_id"],
                "hypothesis_definition_sha256": hypothesis["hypothesis_definition_sha256"],
                "parameter_set_id": hypothesis["parameter_sets"][0]["parameter_set_id"],
                **binding,
                "direction": "UP",
                "horizon_minutes": hypothesis["horizons_minutes"][0],
                "dataset_role": "DEVELOPMENT",
                "evaluator_id": EVALUATOR_ID,
            }, "evaluation_id"))
    return rows


def _catalog_without_id() -> dict[str, Any]:
    hypotheses = _hypotheses()
    evaluations = _evaluations(hypotheses)
    contracts = deepcopy(INHERITED_CONTRACTS)
    contracts["execution_outcome_contract"]["evaluator_id"] = EVALUATOR_ID
    contracts["execution_outcome_contract"]["af1_compatibility"] = (
        "AF1 t+1 entry is causally invalid for DE1G; the future AF4C evaluator must implement t+2 without changing AF1"
    )
    contracts["data_contract"]["first_eligible_state_minute_by_hypothesis"] = {
        h["hypothesis_id"]: h["first_eligible_state_minute"] for h in hypotheses
    }
    contracts["data_contract"]["identity_binding_rule"] = (
        "a separately human-authorized execution phase must validate and freeze exact immutable Candle and DE1 dataset IDs and content hashes before opening observation values, signal counts, or outcomes; no acquisition authorized here"
    )
    return {
        **contracts,
        "schema_version": SCHEMA_VERSION,
        "phase": "AF4C",
        "title": "DE1 Price-Impact and Flow-Divergence Shadow Preregistration",
        "scope": {
            "research_only": True, "preregistration_only": True,
            "experiment_executed": False, "production_authorized": False,
            "market_data_acquired": False, "predictive_observations_inspected": False,
            "model_training_authorized": False, "evaluator_implemented": False,
        },
        "lineage": {
            "starting_head": STARTING_HEAD,
            "starting_engine": "frozen A1 acceleration engine; computation/storage performance only; scientific semantics unchanged",
            "specification_hash_policy": "SHA-256 of exact repository file bytes; docs/012 binds catalog and FDR IDs without circular self-hashing",
            "specifications": [{"path": p, "sha256": h} for p, h in SPEC_HASHES.items()],
            "parent_research": [deepcopy(AF4B_LINEAGE)],
        },
        "information_family": {
            "provider": "Binance", "market": "Spot",
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
                "individual-execution inference", "first_trade_id/last_trade_id inferred execution counts",
                "raw-event timing features", "order book", "derivatives", "future information", "external data",
            ],
        },
        "alignment_contract": {
            "join_keys": ["symbol", "exact_UTC_minute"],
            "cross_symbol_exception": "af4c.h5 requires both BTCUSDT and ETHUSDT state and candles at exact shared UTC minute; ETHUSDT target only",
            "missing_side_policy": "observation ineligible",
            "nearest_neighbor": False, "forward_fill": False,
            "interpolation": False, "lag_search": False,
        },
        "derived_definitions": {
            "arithmetic": "exact Decimal-compatible semantics; no binary-float feature arithmetic; inherit AF1 fixed Decimal context af1-decimal-50-half-even-tcdf-v1",
            "aliases": {
                "B_t": "de1.total_base_quantity_t", "Q_t": "de1.total_quote_notional_t",
                "B_buy_t": "de1.aggressive_buy_base_quantity_t",
                "B_sell_t": "de1.aggressive_sell_base_quantity_t",
                "Q_buy_t": "de1.aggressive_buy_quote_notional_t",
                "Q_sell_t": "de1.aggressive_sell_quote_notional_t",
            },
            "V_t": {"formula": "Q_t / B_t", "defined_when": "B_t > 0 AND Q_t > 0"},
            "I_t": {"formula": "(Q_buy_t - Q_sell_t) / Q_t", "defined_when": "Q_t > 0"},
            "D_t": {"formula": "(close_t - V_t) / V_t", "defined_when": "V_t > 0"},
            "V_buy_t": {"formula": "Q_buy_t / B_buy_t", "defined_when": "B_buy_t > 0 AND Q_buy_t > 0"},
            "V_sell_t": {"formula": "Q_sell_t / B_sell_t", "defined_when": "B_sell_t > 0 AND Q_sell_t > 0"},
            "R_t": {"formula": "close_t / open_t - 1", "defined_when": "validated completed candle with open_t > 0"},
            "five_minute": {
                "window": "t-4 through t inclusive",
                "Q_5t": "sum(Q_j)", "B_5t": "sum(B_j)",
                "NetQ_5t": "sum(Q_buy_j - Q_sell_j)",
                "V_5t": "Q_5t / B_5t", "I_5t": "NetQ_5t / Q_5t",
                "defined_when": "all five completed minutes and required candles exist inside DEVELOPMENT; Q_5t > 0 AND B_5t > 0",
                "alternative_window_allowed": False,
            },
            "undefined_required_value_policy": "observation ineligible; no epsilon, zero substitution, or imputation",
        },
        "af4b_independence": {
            "af4b_preregistration_known": True,
            "af4b_performance_or_outcomes_inspected": False,
            "frozen_while_af4b_pending": True,
            "outcome_dependent_af4b_modification": False,
            "preexisting_roadmap_concepts": ["VWAP displacement", "price/flow divergence", "realized price-impact proxies", "cross-market disagreement/catch-up"],
            "generation_interpretation": "AF4B and AF4C are separate preregistered DEVELOPMENT generations on reused DEVELOPMENT; repeated generations are not fresh independent confirmation; never combine their p-values into a result-dependent family",
        },
        "shadow_policy": {
            "status": "SHADOW while AF4B unresolved",
            "execution_requires_separate_human_authorization": True,
            "if_executed_while_af4b_pending": "write predictive results as sealed shadow evidence",
            "normal_stdout_and_final_reports_prohibit": ["signal counts", "returns", "expectancies", "p-values", "q-values", "rankings", "classifications", "candidate identities"],
            "permitted_public_evidence": "only non-predictive operational completion/integrity evidence",
            "automatic_unsealing": False,
            "case_a": "AF4B authoritative terminal outcome has zero PROMOTE evaluations: eligible for explicit human-approved unsealing/review, never automatic",
            "case_b": "AF4B authoritative terminal outcome has one or more PROMOTE evaluations: remain sealed for current candidate-selection decision; must not choose among or modify AF4B candidates; a later separately authorized research phase may decide disposition",
            "unknown_or_nonterminal_af4b": "remain sealed; no inferred terminal outcome",
            "policy_changes_after_af4c_outcomes_allowed": False,
        },
        "universe_limits": {
            "registered_primary_hypotheses": 5,
            "registered_hypothesis_parameter_combinations_before_expansion": 5,
            "registered_evaluations_after_symbol_horizon_expansion": 9,
            "threshold_sweep": False, "horizon_sweep": False,
            "alternative_variants": False, "parameter_rescue": False,
        },
        "hypotheses": hypotheses,
        "evaluations": evaluations,
        "fdr_family": _identified({
            "generation": "AF4C", "method": "Benjamini-Hochberg", "alpha": "0.05",
            "closed": True, "registered_evaluation_count": 9,
            "evaluation_ids": [e["evaluation_id"] for e in evaluations],
            "undefined_test_policy": "retain every registered evaluation and assign conservative p=1; never drop a failed or undefined row",
            "comparator_tests_in_family": False,
        }, "fdr_family_id"),
        "promotion_interpretation": "PROMOTE is DEVELOPMENT candidate-generation evidence only, not independent confirmation; cannot bypass untouched validation, Backtest, Walk-Forward, Monte Carlo, Paper Trading, or Explicit Live Approval",
        "stopping_boundary": "preregistration only; no observations, network acquisition, hypothesis execution, evaluator implementation, AF4B outcome inspection, commit, push, or merge; execution and unsealing require separate human authorization; no DE2 or production admission",
    }


def build_catalog() -> dict[str, Any]:
    """Construct the single registered universe without filesystem/data access."""
    document = _catalog_without_id()
    require(len(document["hypotheses"]) == 5, "exactly five hypotheses required")
    require(sum(len(h["parameter_sets"]) for h in document["hypotheses"]) == 5,
            "exactly five hypothesis/parameter combinations required")
    require(len(document["evaluations"]) == 9, "exactly nine evaluations required")
    with localcontext() as context:
        context.prec = 80
        require(Decimal(STRESS_COST) == 2 * Decimal(BASE_COST), "stress cost must equal 2C")
    return _identified(document, "catalog_id")


def validate_catalog(document: dict[str, Any]) -> dict[str, Any]:
    """Validate every frozen degree of freedom, unknown fields, types and IDs."""
    require(isinstance(document, dict), "catalog must be an object")
    require(canonical_json_bytes(document) == canonical_json_bytes(build_catalog()),
            "catalog differs from frozen AF4C preregistration (fields, semantics, or IDs)")
    return deepcopy(document)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_catalog(payload: bytes) -> dict[str, Any]:
    """Parse canonical JSON, rejecting duplicate keys at every nesting level."""
    document = json.loads(payload, object_pairs_hook=_unique_object)
    require(payload == canonical_json_bytes(document), "catalog is not canonical JSON")
    return validate_catalog(document)


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    return parse_catalog(path.read_bytes())


def write_catalog(path: Path = CATALOG_PATH) -> str:
    """Create once, or verify identical existing bytes; never overwrite a conflict."""
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
