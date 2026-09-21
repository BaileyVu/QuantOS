"""AF4A: immutable, offline diagnostics of the pinned AF3 WATCH population.

This research entrypoint reads AF3 result metadata, never candles or evaluators.
Run with --source-root <existing AF1 store> --output-root <new AF4A store>.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from quantos.domain.evaluation.alpha_funnel import (
    af1_decimal_context, canonical_json_bytes, ScoreBands,
)
from quantos.infrastructure.storage.alpha_funnel import AlphaFunnelArtifactStore

BASE_HEAD = "49fa4b50713fe4e1fd5bbf079142da054dbc8466"
SOURCE_RUN = "78acd6bffa78189741a25ec5d3e1f1f1829297becf927f402bf024436ada8e67"
SOURCE_HASH = "10c48c9cf936624285b25e9495897675b67935a7935d48920a95291e2c8bd86f"
CONFIG_HASH = "4cfa0501c6a004e6ed75f3992333260040509718b951c68ca886146397abe6f1"
REFERENCE_HASH = "43ca2cf8a91fd7bf79ad592e0751e64d9154115df0100f175a63de3d1ea0d9a9"
MF1_HASH = "66b086acbf189de30981999dce2388ef079a1c1ff165f009e60f54b0ecb4ffbd"
UNIT = "simple_return_fraction_of_entry_notional"
CATEGORIES = ("COST", "SAMPLE_SUFFICIENCY", "STATISTICAL_STRENGTH",
              "MULTIPLE_TESTING", "TEMPORAL_ROBUSTNESS", "RVS_COMPOSITE_EVIDENCE")
REPO = Path(__file__).resolve().parents[1]


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def pinned_json(path: Path, expected: str) -> dict:
    value = json.loads(path.read_bytes())
    require(digest(canonical_json_bytes(value)) == expected, f"identity mismatch: {path.name}")
    return value


def check_config(config: dict) -> None:
    require(digest(canonical_json_bytes(config)) == CONFIG_HASH,
            "only the frozen AF3 configuration is permitted")


def frozen_config(repo: Path = REPO) -> dict:
    return pinned_json(repo / "research/alpha-funnel/af3c/screening_config.json", CONFIG_HASH)


def watch_key(row: dict) -> tuple:
    return row["hypothesis_id"], row["symbol"], row["horizon_minutes"]


def validate_watch(rows: list[dict]) -> list[dict]:
    require(len(rows) == 17, "AF4A requires exactly 17 WATCH rows")
    require(all(r["provisional_classification"] == "WATCH" for r in rows),
            "non-WATCH input rejected")
    require(len({watch_key(r) for r in rows}) == 17, "duplicate WATCH identity")
    return sorted(rows, key=watch_key)


def verify_source(source_root: Path, repo: Path = REPO) -> tuple:
    reference = pinned_json(repo / "research/alpha-funnel/af3c/run.json", REFERENCE_HASH)
    config = frozen_config(repo)
    artifact = AlphaFunnelArtifactStore(source_root).load_verified(SOURCE_RUN)
    require(artifact.run_id == SOURCE_RUN and artifact.results_sha256 == SOURCE_HASH
            and digest(artifact.results_bytes()) == SOURCE_HASH, "AF3 identity/hash mismatch")
    manifest = json.loads(artifact.manifest_bytes())
    document = json.loads(artifact.results_bytes())
    require(manifest["production_approved"] is False, "production approval mismatch")
    require(all(d["role"] == "development"
                and d["start_time"].startswith("2024-01-01")
                and d["end_time"].startswith("2025-09-30") for d in manifest["datasets"]),
            "only the pinned development metadata may be read")
    stored = manifest["configuration"]
    require(stored["allow_sealed_oos"] is False, "sealed OOS permission rejected")
    for key, value in config.items():
        if key in ("schema_version", "fdr_universe_id"):
            continue
        actual = stored[key]
        if key == "classification":
            actual = {k: actual[k] for k in value}
        if key == "costs":
            actual = [{k: a[k] for k in b} for a, b in zip(actual, value, strict=True)]
        require(actual == value, f"stored configuration mismatch: {key}")
    universe = stored["fdr_universe"]
    require(universe["universe_id"] == reference["af3b_universe_id"],
            "FDR universe mismatch")
    rows = document["results"]
    require(len(rows) == 616 and Counter(r["provisional_classification"] for r in rows)
            == {"KILL": 599, "WATCH": 17}, "AF3 population mismatch")
    hypotheses = {h["stable_id"]: h for h in manifest["hypotheses"]}
    require(all(h["parameters"]["af2a_catalog_id"] == reference["af2a_catalog_id"]
                for h in hypotheses.values()), "catalog mismatch")
    watches = validate_watch([r for r in rows if r["provisional_classification"] == "WATCH"])
    return reference, config, manifest, watches, hypotheses, digest(artifact.manifest_bytes())


def _gate(observed, threshold, comparison: str) -> dict:
    passed = observed is not None and (
        Decimal(str(observed)) >= Decimal(str(threshold)) if comparison == ">="
        else Decimal(str(observed)) <= Decimal(str(threshold)))
    return {"observed": observed, "threshold": threshold,
            "comparison": comparison, "pass": passed}


def remaining_failures(gates: dict) -> dict:
    """Remove one mandatory predicate logically, without changing any predicate."""
    failed = sorted(k for k, v in gates.items() if not v["pass"])
    return {removed: [k for k in failed if k != removed] for removed in failed}


def diagnose(row: dict, config: dict, hypothesis: dict) -> dict:
    """Exact bookkeeping only; reject any replacement screening configuration."""
    check_config(config)
    require(row["provisional_classification"] == "WATCH", "non-WATCH input rejected")
    require(row["horizon_minutes"] in config["horizons_minutes"], "unsupported horizon")
    require(row["cost_adjusted_expectancy_unit"] == UNIT, "cost unit mismatch")
    with af1_decimal_context():
        return _diagnose(row, config, hypothesis)


def _diagnose(row: dict, config: dict, hypothesis: dict) -> dict:
    rules = config["classification"]
    cost = row["cost_adjusted_expectancy"]
    scores = row["research_viability_score"]
    temporal = row["temporal_stability"]
    multiple = row["multiple_testing"]
    base = Decimal(cost["base"])
    require(base >= Decimal(rules["kill_below_base_cost_expectancy"])
            and row["deoverlapped_event_count"] > 0, "WATCH contradicts KILL rule")
    for scenario in config["costs"]:
        require(Decimal(row["gross_expectancy"]) - Decimal(scenario["round_trip_rate"])
                == Decimal(cost[scenario["name"]]), "cost arithmetic mismatch")
    gates = {
        "COST": _gate(cost["base"], rules["promote_min_base_cost_expectancy"], ">="),
        "SAMPLE_SUFFICIENCY": _gate(row["deoverlapped_event_count"],
                                  rules["promote_min_deoverlapped_events"], ">="),
        "MULTIPLE_TESTING": _gate(multiple["q_value"], config["fdr_threshold"], "<="),
        "RVS_COMPOSITE_EVIDENCE": _gate(scores["RVS"], rules["promote_min_rvs"], ">="),
    }
    require(gates["MULTIPLE_TESTING"]["pass"] == multiple["passes_fdr_threshold"],
            "stored FDR result mismatch")
    require(not all(g["pass"] for g in gates.values()), "WATCH contradicts PROMOTE gates")
    values = {
        "M": cost["base"],
        "S": (str(max(Decimal(0), Decimal(row["descriptive_t_statistic"])))
              if row["descriptive_t_statistic"] is not None else None),
        "F": row["deoverlapped_event_frequency_per_day"],
        "R": temporal["robustness_evidence_ratio"],
    }
    components = {}
    for label, bands in config["scoring"].items():
        component = label[0]
        value = values[component]
        calculated = ScoreBands(tuple(Decimal(b) for b in bands)).score(
            Decimal(value) if value is not None else None)
        if component == "R" and calculated == 4:
            if temporal["positive_qualified_block_count"] < config[
                    "minimum_positive_blocks_for_max_robustness"]:
                calculated = 3
        require(scores[component] == calculated, f"stored {component} score mismatch")
        components[component] = {
            "input": value, "score": calculated, "bands": bands,
            "below_maximum_component_score": calculated < 4,
            "standalone_promotion_gate": False,
        }
    require(scores["X"] == hypothesis["human_explainability_score"]
            and sum(scores[k] for k in "MSFRX") == scores["RVS"], "RVS mismatch")
    checks = {
        "coverage": _gate(temporal["qualified_block_coverage"],
                          config["minimum_stability_block_coverage"], ">="),
        "positive_blocks_for_maximum_R": _gate(
            temporal["positive_qualified_block_count"],
            config["minimum_positive_blocks_for_max_robustness"], ">="),
    }
    secondary = []
    if Decimal(cost["stress_2c"]) <= 0:
        secondary.append({"category": "COST", "reason": "stress_2c_expectancy_nonpositive"})
    for component, category in (("M", "COST"), ("S", "STATISTICAL_STRENGTH"),
                                ("F", "SAMPLE_SUFFICIENCY"), ("R", "TEMPORAL_ROBUSTNESS")):
        if components[component]["below_maximum_component_score"]:
            secondary.append({"category": category,
                              "reason": f"{component}_below_frozen_maximum_score_band"})
    for name, gate in checks.items():
        if not gate["pass"]:
            secondary.append({"category": "TEMPORAL_ROBUSTNESS", "reason": name})
    binding = sorted(k for k, v in gates.items() if not v["pass"])
    return {
        "key": list(watch_key(row)),
        "source_row": row,
        "source_row_sha256": digest(canonical_json_bytes(row)),
        "parameter_identity": hypothesis["parameters"],
        "hypothesis_metadata_sha256": digest(canonical_json_bytes(hypothesis)),
        "promotion_gates": gates,
        "binding_failures": binding,
        "secondary_weaknesses": secondary,
        "components": components,
        "temporal_component_checks": checks,
        "cost_gap_from_promotion": str(base - Decimal(rules["promote_min_base_cost_expectancy"])),
        "counterfactual_remaining_binding_failures": remaining_failures(gates),
        "nominal_p_le_fdr_threshold_but_BH_fails": (
            row["raw_p_value"] is not None
            and Decimal(row["raw_p_value"]) <= Decimal(config["fdr_threshold"])
            and not gates["MULTIPLE_TESTING"]["pass"]),
    }


def counts(rows: list[dict]) -> dict:
    binding = {c: sum(c in r["binding_failures"] for r in rows) for c in CATEGORIES}
    secondary = {c: sum(any(w["category"] == c for w in r["secondary_weaknesses"])
                       for r in rows) for c in CATEGORIES}
    return {"WATCH": len(rows), "binding": binding, "secondary": secondary,
            "binding_combinations": dict(sorted(Counter(
                " + ".join(r["binding_failures"]) for r in rows).items()))}


def aggregate(rows: list[dict], config: dict) -> dict:
    result = counts(rows)
    for name, field, labels in (
        ("symbol", "symbol", ("BTCUSDT", "ETHUSDT")),
        ("horizon", "horizon_minutes", config["horizons_minutes"]),
        ("family", "hypothesis_family", sorted({r["source_row"]["hypothesis_family"] for r in rows})),
    ):
        result[name] = {str(v): counts([r for r in rows if r["source_row"][field] == v])
                        for v in labels}
    result["nominal_p_le_005_but_BH_fails"] = sum(
        r["nominal_p_le_fdr_threshold_but_BH_fails"] for r in rows)
    result["base_net_nonpositive"] = sum(
        Decimal(r["source_row"]["cost_adjusted_expectancy"]["base"]) <= 0 for r in rows)
    result["stress_net_nonpositive"] = sum(
        Decimal(r["source_row"]["cost_adjusted_expectancy"]["stress_2c"]) <= 0 for r in rows)
    result["semantics"] = (
        "Counts overlap. Secondary counts use the existing maximum component score bands, "
        "not new mandatory gates. Horizon/symbol/family concentrations are descriptive only; "
        "no returns, p-values, or event counts are pooled into a composite candidate."
    )
    return result


INTERPRETATION = {
    "facts": [
        "All 17 fail count, BH FDR and total RVS; 13 additionally fail the base-net promotion minimum.",
        "The four cost-passing rows each have three deoverlapped events, on ETH at 10/15/30/60 minutes.",
        "All 17 have R=0 because block coverage is below 0.75; sparse coverage does not prove regime instability.",
        "Six nominal p-values are <=0.05 but all 17 BH q-values exceed 0.05 across the closed 616-test universe.",
        "13 rows have negative stress expectancy, while all 17 have positive base-net expectancy.",
    ],
    "interpretation": [
        "The WATCH subset has a joint sample, coverage and multiplicity deficit; magnitude is also binding in 13.",
        "This does not support a pure magnitude-only diagnosis with sufficient observations.",
        "Sparse extreme states may produce noisy effect estimates. Deoverlap does not establish independence.",
        "Insufficient temporal coverage leaves temporal repeatability unresolved; it cannot identify a missing regime variable.",
        "Concentration in price/activity disagreement and reversal/exhaustion suggests investigating distinct information, not more candle transformations. It proves neither redundancy nor impossibility of candle alpha.",
        "No failed gate is removed for classification, and no case is promoted or approved for trading.",
    ],
    "MF1_separate_evidence": (
        "CASE 1 — NO INCREMENTAL PARTICIPATION INFORMATION FOUND is independent supporting "
        "evidence about the tested activity extension. AF3 and MF1 statistics are not pooled."
    ),
    "DE1": (
        "The existing trade-flow feasibility priority remains justified, not empirically validated. "
        "Aggressor direction, flow persistence, price/flow divergence and impact/absorption proxies "
        "are relevant mechanism categories because WATCH cases cluster in disagreement/reversal "
        "families. MF1 tested a coarse size proxy, not trade-size distributions; AF4A cannot prioritize that category. The sample/coverage "
        "deficit must remain visible; these results do not show that any flow mechanism will solve it. "
        "No mechanism can be selected as superior from AF3 alone. No DE1 implementation or AF4B hypotheses."
    ),
}


def analyze(source_root: Path, repo: Path = REPO) -> dict:
    ref, config, manifest, watches, hypotheses, manifest_hash = verify_source(source_root, repo)
    mf1 = pinned_json(repo / "research/phase-4d-mf1/run.json", MF1_HASH)
    rows = [diagnose(r, config, hypotheses[r["hypothesis_id"]]) for r in watches]
    code_paths = ["research/alpha_funnel_af4a.py",
                  "src/quantos/domain/evaluation/alpha_funnel.py",
                  "src/quantos/infrastructure/storage/alpha_funnel.py"]
    code = {p: digest((repo / p).read_bytes()) for p in code_paths}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return {
        "schema_version": "af4a-watch-forensics-v1",
        "source": {"AF3_reference": ref, "manifest_sha256": manifest_hash,
                   "results_sha256": SOURCE_HASH, "run_id": SOURCE_RUN,
                   "catalog_id": ref["af2a_catalog_id"],
                   "fdr_universe_id": ref["af3b_universe_id"],
                   "MF1_reference_sha256": MF1_HASH, "MF1_run_id": mf1["external_run_id"]},
        "code_identity": {"starting_main_HEAD": BASE_HEAD, "repository_HEAD": head, "files_sha256": code},
        "configuration": config,
        "cost_unit": UNIT,
        "WATCH_set_sha256": digest(canonical_json_bytes(watches)),
        "WATCH_keys": [list(watch_key(r)) for r in watches],
        "inventory": rows,
        "aggregate": aggregate(rows, config),
        "interpretation": INTERPRETATION,
        "scope": {"production_approved": False, "research_only": True,
                  "source_dataset_role": "development", "market_data_queries": 0,
                  "network_requests": 0, "screening_validation_accessed": False,
                  "sealed_oos_accessed": False, "data_2026_accessed": False,
                  "hypotheses_created": 0, "thresholds_changed": False},
    }


def report(result: dict) -> bytes:
    lines = ["# AF4A — AF3 WATCH Forensics", "",
             "All numeric values below retain the source Decimal strings. Return units: " + UNIT,
             "Only the four promotion predicates are binding. Component checks are secondary.",
             "No horizon pooling. RVS is an evidence score, not a probability.",
             "Temporal positivity describes gross returns; raw p tests gross mean against zero.", ""]
    for name, text in result["interpretation"].items():
        lines += ["## " + name, "", *(text if isinstance(text, list) else [text]), ""]
    lines += ["## Aggregates", "", "~~~json",
              json.dumps(result["aggregate"], indent=2), "~~~", ""]
    for index, row in enumerate(result["inventory"], 1):
        lines += [f"## WATCH {index}: " + " / ".join(map(str, row["key"])), "",
                  "Full source fields, exact parameters, mandatory gates and secondary checks:",
                  "", "~~~json", json.dumps(row, indent=2), "~~~", ""]
    return ("\n".join(lines) + "\n").encode("utf-8")


def publish(result: dict, output_root: Path, source_root: Path) -> tuple[str, dict]:
    output_root, source_root = output_root.resolve(), source_root.resolve()
    require(not output_root.is_relative_to(source_root)
            and not source_root.is_relative_to(output_root), "source/output stores must be disjoint")
    analysis = canonical_json_bytes(result)
    run_id = digest(analysis)
    files = {"analysis.json": analysis, "report.md": report(result),
             "analysis.py": Path(__file__).read_bytes()}
    expected_code = result["code_identity"]["files_sha256"]["research/alpha_funnel_af4a.py"]
    require(digest(files["analysis.py"]) == expected_code, "code changed after analysis")
    manifest = {"schema_version": "af4a-artifact-manifest-v1", "run_id": run_id,
                "files_sha256": {k: digest(v) for k, v in sorted(files.items())}}
    files["manifest.json"] = canonical_json_bytes(manifest)
    runs = output_root / "runs"
    require(runs.resolve().is_relative_to(output_root), "output runs directory escapes store")
    runs.mkdir(parents=True, exist_ok=True)
    target = runs / run_id
    if target.exists():
        require(target.is_dir() and not target.is_symlink()
                and {p.name for p in target.iterdir()} == set(files), "existing artifact shape mismatch")
        require(all(not (target / k).is_symlink() and (target / k).read_bytes() == v
                    for k, v in files.items()), "immutable artifact conflict")
    else:
        with TemporaryDirectory(prefix=".af4a-", dir=runs) as directory:
            stage = Path(directory) / "complete"
            stage.mkdir()
            for name, payload in files.items():
                with (stage / name).open("xb") as stream:
                    stream.write(payload)
            stage.rename(target)
    require(all((target / k).read_bytes() == v for k, v in files.items()), "publication verification failed")
    return run_id, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.source_root)
    run_id, manifest = publish(result, args.output_root, args.source_root)
    print(json.dumps({"run_id": run_id, "manifest": manifest,
                      "aggregate": result["aggregate"]}, sort_keys=True))


if __name__ == "__main__":
    main()
