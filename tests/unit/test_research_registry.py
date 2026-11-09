import json
import math
from pathlib import Path
import tempfile
import unittest

from quantos.paper.research.registry import (
    ResearchRegistry,
    calculate_deflated_sharpe,
    create_dataset_manifest,
    hash_params,
    verify_dataset_manifest,
)


def ledger_row(**overrides):
    row = {
        "family": "demo_family",
        "variant_id": "v1",
        "params": {"stop": 2, "entry": "market"},
        "period": "BUILD 2020-01..2023-06",
        "trades": 25,
        "mean_net_bp": 1.25,
        "ci90": [-0.5, 3.0],
        "verdict": "diagnostic",
    }
    return {**row, **overrides}


class ResearchRegistryTest(unittest.TestCase):
    def test_import_is_idempotent_and_n_counts_variants_not_period_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            rows = [ledger_row(), ledger_row(period="HELD 2023-07..2024-12", trades=9)]
            ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with ResearchRegistry(root / "registry.sqlite") as registry:
                first = registry.import_jsonl(ledger)
                second = registry.import_jsonl(ledger)
                self.assertEqual((first.variants_added, first.results_added), (1, 2))
                self.assertEqual((second.variants_added, second.results_added), (0, 0))
                self.assertEqual(second.duplicates_skipped, 2)
                self.assertEqual(registry.trial_counts("demo_family")["family_n"], 1)
                self.assertEqual(registry.trial_counts()["registry_n"], 1)
                self.assertEqual(registry.trial_counts()["legacy_cci_n"], 81)
                self.assertEqual([x["period_class"] for x in registry.result_rows()], ["BUILD", "HELD"])

    def test_registered_variants_are_bound_to_manifest_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ResearchRegistry(Path(tmp) / "registry.sqlite") as registry:
                h1, h2 = "a" * 64, "b" * 64
                one = registry.register_variant("f", "v", {"x": 1}, manifest_hash=h1)
                again = registry.register_variant("f", "v-alias", {"x": 1}, manifest_hash=h1)
                other_data = registry.register_variant("f", "v", {"x": 1}, manifest_hash=h2)
                self.assertEqual(one, again)
                self.assertNotEqual(one, other_data)
                self.assertEqual(registry.trial_counts("f")["family_n"], 2)

    def test_malformed_jsonl_rejects_whole_import_without_partial_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "bad.jsonl"
            ledger.write_text(json.dumps(ledger_row()) + "\n{bad json}\n", encoding="utf-8")
            with ResearchRegistry(root / "registry.sqlite") as registry:
                with self.assertRaisesRegex(ValueError, "line 2"):
                    registry.import_jsonl(ledger)
                self.assertEqual(registry.trial_counts()["registry_n"], 0)
                ledger.write_text(json.dumps(ledger_row(trades=-1)) + "\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "trades"):
                    registry.import_jsonl(ledger)
                self.assertEqual(registry.trial_counts()["registry_n"], 0)

    def test_param_hash_is_canonical_and_changed_result_is_retained(self):
        self.assertEqual(hash_params({"a": 1, "b": 2}), hash_params({"b": 2, "a": 1}))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            with ResearchRegistry(root / "registry.sqlite") as registry:
                ledger.write_text(json.dumps(ledger_row()) + "\n", encoding="utf-8")
                registry.import_jsonl(ledger)
                ledger.write_text(json.dumps(ledger_row(mean_net_bp=1.5)) + "\n", encoding="utf-8")
                registry.import_jsonl(ledger)
                self.assertEqual(registry.trial_counts()["registry_n"], 1)
                self.assertEqual(len(registry.result_rows()), 2)

    def test_non_evaluable_legacy_result_keeps_null_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / "ledger.jsonl"
            ledger.write_text(json.dumps(ledger_row(mean_net_bp=None, ci90=None, trades=0)) + "\n",
                              encoding="utf-8")
            with ResearchRegistry(root / "registry.sqlite") as registry:
                registry.import_jsonl(ledger)
                stats = registry.result_rows()[0]["stats"]
                self.assertIsNone(stats["mean_net_bp"])
                self.assertIsNone(stats["ci90"])

    def test_manifest_binds_source_range_gaps_and_dataset_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "bars.csv"
            manifest_path = root / "bars.manifest.json"
            data.write_bytes(b"timestamp,close\n2020-01-01,1\n2020-01-03,2\n")
            created = create_dataset_manifest(data, manifest_path, source="local fixture",
                start="2020-01-01", end="2020-01-03",
                missing_ranges=[{"start": "2020-01-02", "end": "2020-01-02"}])
            verified = verify_dataset_manifest(manifest_path)
            self.assertEqual(created["manifest_sha256"], verified["manifest_sha256"])
            self.assertEqual(verified["missing_ranges"], [{"start": "2020-01-02", "end": "2020-01-02"}])
            data.write_bytes(data.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_dataset_manifest(manifest_path)

    def test_single_trial_reduces_to_probabilistic_sharpe(self):
        result = calculate_deflated_sharpe(n_trials=1, trial_sharpe_mean=0,
            trial_sharpe_std=0.4, sharpe_ratio=0.7, sample_size=100,
            skewness=0.2, kurtosis=3.4)
        variance = 1 - 0.2 * 0.7 + ((3.4 - 1) / 4) * 0.7 ** 2
        expected = 0.5 * (1 + math.erf(
            (0.7 * math.sqrt(99) / math.sqrt(variance)) / math.sqrt(2)))
        self.assertAlmostEqual(result.probability, expected, places=14)
        self.assertEqual(result.benchmark_sharpe, 0)

    def test_fixed_dsr_vector_and_more_trials_raise_threshold(self):
        fixed = calculate_deflated_sharpe(n_trials=20, trial_sharpe_mean=0,
            trial_sharpe_std=0.2, sharpe_ratio=0.75, sample_size=250,
            skewness=0.25, kurtosis=3.5)
        self.assertAlmostEqual(fixed.benchmark_sharpe, 0.3801415902362397, places=14)
        variance = 1 - .25 * .75 + ((3.5 - 1) / 4) * .75 ** 2
        self.assertAlmostEqual(fixed.z_score, (.75 - .3801415902362397) * math.sqrt(249 / variance), places=12)
        single = calculate_deflated_sharpe(n_trials=1, trial_sharpe_mean=0,
            trial_sharpe_std=0.2, sharpe_ratio=0.75, sample_size=250,
            skewness=0.25, kurtosis=3.5)
        self.assertGreater(single.probability, fixed.probability)

    def test_returns_input_and_invalid_or_insufficient_inputs(self):
        result = calculate_deflated_sharpe(n_trials=1, trial_sharpe_mean=0,
            trial_sharpe_std=0, returns=[-0.02, 0.01, 0.03, 0.02])
        self.assertEqual(result.sample_size, 4)
        with self.assertRaisesRegex(ValueError, "at least three returns"):
            calculate_deflated_sharpe(n_trials=1, trial_sharpe_mean=0,
                trial_sharpe_std=0, returns=[-0.01, 0.01])
        with self.assertRaisesRegex(ValueError, "both"):
            calculate_deflated_sharpe(n_trials=1, trial_sharpe_mean=0,
                trial_sharpe_std=0, returns=[-0.01, 0.01, 0.02], sharpe_ratio=1)
        with self.assertRaisesRegex(ValueError, "sample_size"):
            calculate_deflated_sharpe(n_trials=1, trial_sharpe_mean=0,
                trial_sharpe_std=0, sharpe_ratio=0.3, sample_size=1,
                skewness=0, kurtosis=3)
        with self.assertRaisesRegex(ValueError, "kurtosis"):
            calculate_deflated_sharpe(n_trials=1, trial_sharpe_mean=0,
                trial_sharpe_std=0, sharpe_ratio=0.3, sample_size=10,
                skewness=0, kurtosis=0.5)

    def test_registry_dsr_uses_family_or_global_n_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with ResearchRegistry(Path(tmp) / "registry.sqlite") as registry:
                h = "a" * 64
                registry.register_variant("f", "a", {"x": 1}, manifest_hash=h)
                registry.register_variant("f", "b", {"x": 2}, manifest_hash=h)
                registry.register_variant("other", "c", {"x": 3}, manifest_hash=h)
                common = dict(trial_sharpe_mean=0, trial_sharpe_std=0.3,
                    sharpe_ratio=0.6, sample_size=100, skewness=0, kurtosis=3)
                family = registry.deflated_sharpe(scope="family", family="f", **common)
                global_result = registry.deflated_sharpe(scope="global", **common)
                self.assertEqual(family.n_trials, 2)
                self.assertEqual(global_result.n_trials, 84)  # 3 registry + 81 external CCI
                self.assertGreater(family.probability, global_result.probability)

    def test_dsr_rejects_shifted_null_and_impossible_moments(self):
        args = dict(n_trials=20, trial_sharpe_mean=0, trial_sharpe_std=.2,
                    sharpe_ratio=.5, sample_size=100, skewness=0, kurtosis=3)
        for changes in ({"trial_sharpe_mean": -.1}, {"skewness": 3}):
            with self.assertRaises(ValueError): calculate_deflated_sharpe(**{**args, **changes})

    def test_manifest_metadata_tamper_rejected_against_registered_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data.csv").write_text("data")
            path = root / "manifest.json"
            created = create_dataset_manifest(root / "data.csv", path, source="fixture",
                start="2020-01-01", end="2020-01-02")
            verify_dataset_manifest(path, expected_hash=created["manifest_sha256"])
            data = json.loads(path.read_text()); data["source"] = "changed"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "manifest checksum"):
                verify_dataset_manifest(path, expected_hash=created["manifest_sha256"])


if __name__ == "__main__":
    unittest.main()
