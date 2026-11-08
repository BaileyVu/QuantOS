import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from quantos.paper.research.registry import ResearchRegistry, create_dataset_manifest
from quantos.paper.research.timesfm_experiment import HOUR_MS, Forecast
from quantos.paper.research import timesfm_volatility as protocol
from quantos.paper.research.timesfm_model import MODEL_ID, MODEL_REVISION, WEIGHTS_SHA256


SERIES = {
    "market": protocol.MARKET,
    "timeframe": protocol.TIMEFRAME,
    "timestamp_semantics": protocol.TIMESTAMP_SEMANTICS,
}
DEPS_DIRECT = {name: f"{name}-version" for name in
               ("timesfm", "torch", "numpy", "huggingface-hub", "safetensors")}
DEPS = {"direct": DEPS_DIRECT, "resolved": {**DEPS_DIRECT, "requests": "requests-version"},
        "python": "3.11.17", "platform": "synthetic-platform"}


def fixture_series(root: Path, symbol: str, *, omit_first=False, bad_symbol=False):
    start_ms = protocol._utc_ms(protocol.START_DATE)
    end_ms = protocol._utc_ms(protocol.date(2023, 1, 1))
    rows = []
    for i, ts in enumerate(range(start_ms, end_ms, HOUR_MS)):
        if omit_first and i == 0:
            continue
        close = 100 * math.exp(.0001 * i)
        rows.append({"time_ms": ts, "symbol": "WRONG" if bad_symbol and i == 10 else symbol,
                     "high": close * 1.001, "low": close * .999, "close": close})
    data = root / f"{symbol}.jsonl"
    manifest = root / f"{symbol}.manifest.json"
    data.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = create_dataset_manifest(
        data, manifest, source="synthetic fixture", start="2022-01-01", end="2022-12-31",
        series={"symbol": symbol, **SERIES},
    )
    return manifest, result["manifest_sha256"]


def fake_prediction(prefix):
    ts, close = prefix[-1]
    return Forecast(ts, close, {1: close, 4: close, 12: close, 24: close},
                    {24: close * math.exp(-.01)}, {24: close * math.exp(.01)})


def progress_payload(plan_hash, origin=100):
    return {
        "plan_sha256": plan_hash, "symbol": "BTCUSDT", "origin_ms": origin,
        "target_start_ms": origin + HOUR_MS,
        "target_end_ms": origin + protocol.HORIZON * HOUR_MS,
        "observation": {
            "realized": .1,
            "predicted": {name: .2 for name in protocol.PREDICTIONS},
            "features": {"origin_ms": origin, "drift_4h": .01, "surprise": None},
        },
    }


class TimesfmProtocolTest(unittest.TestCase):
    def test_manifest_requires_series_contract_and_complete_2022_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path, manifest_hash = fixture_series(root, "BTCUSDT")
            symbol, rows, _ = protocol.load_build_bars(manifest_path, manifest_hash)
            self.assertEqual(symbol, "BTCUSDT")
            self.assertEqual(len(rows), 8760)
            self.assertEqual(rows[0][0], protocol._utc_ms(protocol.START_DATE))
            self.assertEqual(rows[-1][0], protocol._utc_ms(protocol.date(2023, 1, 1)) - HOUR_MS)

            partial, partial_hash = fixture_series(root, "ETHUSDT", omit_first=True)
            with self.assertRaisesRegex(ValueError, "cover every 2022"):
                protocol.load_build_bars(partial, partial_hash)
            wrong, wrong_hash = fixture_series(root, "SOLUSDT", bad_symbol=True)
            with self.assertRaisesRegex(ValueError, "row 11 symbol"):
                protocol.load_build_bars(wrong, wrong_hash)

            bad_contract = root / "bad-contract.json"
            bad_contract.write_text(json.dumps({
                "range": {"start": "2022-01-01", "end": "2022-12-31"},
                "series": {"symbol": "BTCUSDT", **{**SERIES, "market": "wrong market"}},
            }))
            with patch("quantos.paper.research.timesfm_volatility.verify_dataset_manifest") as verify:
                with self.assertRaisesRegex(ValueError, "series does not match"):
                    protocol.load_build_bars(bad_contract, "a" * 64)
                verify.assert_not_called()

    def test_log_quantile_width_is_primary_forecast_error_unit(self):
        rows = []
        start = protocol._utc_ms(protocol.START_DATE)
        for i in range(560):
            close = 100 * math.exp(.001 * i)
            rows.append((start + i * HOUR_MS, close * 1.01, close * .99, close))
        result = protocol.compare(rows, fake_prediction)
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["observations"][0]["predicted"]["timesfm"],
                               .02 / protocol.NORMAL_80_WIDTH)
        obs = result["observations"][0]
        self.assertEqual(obs["target_start_ms"], obs["origin_ms"] + HOUR_MS)
        self.assertEqual(obs["target_end_ms"], obs["origin_ms"] + 24 * HOUR_MS)

    def test_compare_resumes_completed_origins_without_predictor_calls(self):
        rows = []
        start = protocol._utc_ms(protocol.START_DATE)
        for i in range(560):
            close = 100 * math.exp(.0001 * i)
            rows.append((start + i * HOUR_MS, close * 1.01, close * .99, close))
        original = protocol.compare(rows, fake_prediction)
        cached = {row["origin_ms"]: {k: row[k] for k in ("realized", "predicted", "features")}
                  for row in original["observations"]}
        never_call = Mock(side_effect=AssertionError("completed forecast repeated"))
        resumed = protocol.compare(rows, never_call, completed=cached)
        never_call.assert_not_called()
        self.assertEqual(original, resumed)

    def test_progress_chain_rejects_tampering_duplicate_unknown_and_other_plan(self):
        expected = {"BTCUSDT": {100: (100 + HOUR_MS, 100 + 24 * HOUR_MS)}}
        plan_hash = "a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress = root / "progress.jsonl"
            tail = protocol.append_progress(progress, payload=progress_payload(plan_hash),
                                            previous_hash=protocol.PROGRESS_ZERO)
            completed, found_tail = protocol.read_progress(progress, plan_hash=plan_hash,
                                                            expected=expected)
            self.assertEqual(found_tail, tail)
            self.assertIn(100, completed["BTCUSDT"])

            duplicate = root / "duplicate.jsonl"
            first_tail = protocol.append_progress(duplicate, payload=progress_payload(plan_hash),
                                                  previous_hash=protocol.PROGRESS_ZERO)
            protocol.append_progress(duplicate, payload=progress_payload(plan_hash),
                                     previous_hash=first_tail)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                protocol.read_progress(duplicate, plan_hash=plan_hash, expected=expected)

            unknown = root / "unknown.jsonl"
            protocol.append_progress(unknown, payload=progress_payload(plan_hash, 999),
                                     previous_hash=protocol.PROGRESS_ZERO)
            with self.assertRaisesRegex(ValueError, "unknown origin"):
                protocol.read_progress(unknown, plan_hash=plan_hash, expected=expected)

            mismatch = root / "mismatch.jsonl"
            protocol.append_progress(mismatch, payload=progress_payload("b" * 64),
                                     previous_hash=protocol.PROGRESS_ZERO)
            with self.assertRaisesRegex(ValueError, "different frozen plan"):
                protocol.read_progress(mismatch, plan_hash=plan_hash, expected=expected)

            text = progress.read_text().replace('"realized":0.1', '"realized":0.2')
            progress.write_text(text)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                protocol.read_progress(progress, plan_hash=plan_hash, expected=expected)

    def test_checkpoint_only_commit_preserves_plan_identity_and_original_audit_head(self):
        first = {"source": {"commit": "commit-a", "source_sha256": "x"}, "data": 1}
        second = {"source": {"commit": "commit-b", "source_sha256": "x"}, "data": 1}
        first_doc, first_hash = protocol._plan_document(first)
        second_doc, second_hash = protocol._plan_document(second)
        self.assertEqual(first_hash, second_hash)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            saved = protocol._persist_plan(output, first_doc)
            resumed = protocol._persist_plan(output, second_doc)
            self.assertEqual(saved["plan"]["source"]["commit"], "commit-a")
            self.assertEqual(resumed["plan"]["source"]["commit"], "commit-a")
            altered = json.loads((output / "preregistered_plan.json").read_text())
            altered["plan"]["source"]["commit"] = "tampered"
            (output / "preregistered_plan.json").write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, "different frozen plan"):
                protocol._persist_plan(output, second_doc)

    def test_bootstrap_minimum_and_seed_are_deterministic(self):
        insufficient = {symbol: {"n": 13, "observations": []} for symbol in protocol.SYMBOLS}
        self.assertEqual(protocol.paired_week_block_bootstrap(insufficient)["status"],
                         "INSUFFICIENT_N")
        ready = {}
        for symbol in protocol.SYMBOLS:
            observations = []
            for i in range(14):
                observations.append({"origin_ms": i * 24 * HOUR_MS, "realized": .1,
                                     "predicted": {name: .2 + i / 10000
                                                   for name in protocol.PREDICTIONS}})
            ready[symbol] = {"n": 14, "observations": observations}
        first = protocol.paired_week_block_bootstrap(ready)
        self.assertEqual(first, protocol.paired_week_block_bootstrap(ready))
        self.assertEqual(first["status"], "COMPLETE")

    def test_run_protocol_registers_records_and_exact_resume_skips_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_inputs = [fixture_series(root, symbol) for symbol in protocol.SYMBOLS]
            inputs = [(path, digest) for path, digest in manifest_inputs]
            registry_path = root / "registry.sqlite"
            output = root / "run"
            calls = []

            def predictor(prefix):
                calls.append(prefix[-1][0])
                return fake_prediction(prefix)

            model_meta = {
                "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                "weights_sha256": WEIGHTS_SHA256, "device": "cpu",
                "torch_num_threads": 2, "dependencies": DEPS_DIRECT,
            }
            fake_model = object()
            with patch.object(protocol, "_source_provenance", return_value={
                    "commit": "commit-a", "source_sha256": "source-hash", "files": []}), \
                 patch.object(protocol, "_dependency_versions", return_value=DEPS), \
                 patch("quantos.paper.research.timesfm_model.load_model",
                       return_value=(fake_model, model_meta)) as load_model, \
                 patch("quantos.paper.research.timesfm_experiment.timesfm25_predictor",
                       return_value=predictor), \
                 patch.object(protocol, "paired_week_block_bootstrap",
                              return_value={"status": "SYNTHETIC_TEST"}):
                with self.assertRaisesRegex(ValueError, "exactly one manifest"):
                    protocol.run_protocol(inputs + [inputs[0]], registry_path,
                                          root / "cache", output)
                first = protocol.run_protocol(inputs, registry_path, root / "cache", output)
                self.assertEqual(len(calls), 3 * 343)
                self.assertEqual(load_model.call_count, 1)
                calls_before_resume = len(calls)
                # A checkpoint-only HEAD change is audit-only; code/data plan
                # bytes remain the same and every saved origin must be reused.
                with patch.object(protocol, "_source_provenance", return_value={
                        "commit": "commit-b", "source_sha256": "source-hash", "files": []}):
                    second = protocol.run_protocol(inputs, registry_path, root / "cache", output)
                self.assertEqual(len(calls), calls_before_resume)
                self.assertEqual(load_model.call_count, 1)
                self.assertEqual(first, second)

            with ResearchRegistry(registry_path) as registry:
                rows = registry.result_rows(family="timesfm25")
                self.assertEqual(len(rows), 3)
                self.assertTrue(all("trades" not in row["stats"] for row in rows))


if __name__ == "__main__":
    unittest.main()
