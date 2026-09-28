"""Temporary synthetic sealed publication; no operational result discovery."""
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.domain.evaluation import af4c as a
from quantos.infrastructure.storage.af4c_shadow import public_receipt, publish_synthetic_shadow
from tests.unit.test_af4c_evaluator import CATALOG, ROOT, fixture


class ShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = a.evaluate_synthetic(CATALOG, fixture())

    def test_receipt_exact_allowlist_and_no_predictive_leak(self):
        receipt = public_receipt(self.result)
        document = json.loads(receipt.payload)
        self.assertEqual(set(document), {"schema", "completed_successfully", "data_kind", "catalog_id",
            "fdr_family_id", "evaluator_version", "registered_evaluation_count", "sealed_payload_sha256",
            "input_identity_sha256", "integrity_status", "shadow", "sealed"})
        self.assertEqual(document["registered_evaluation_count"], 9)
        for forbidden in ("KILL", "WATCH", "PROMOTE", "q_value", "p_value", "expectancy", "return", "signal", "candidate"):
            self.assertNotIn(forbidden, receipt.payload.decode())
        for e in CATALOG.evaluations:
            self.assertNotIn(e.evaluation_id, receipt.payload.decode())
            self.assertNotIn(e.row["hypothesis_id"], receipt.payload.decode())
        self.assertNotIn("results", document)

    def test_temporary_publication_verified_silent_and_separate(self):
        with TemporaryDirectory(dir=ROOT) as temporary, redirect_stdout(StringIO()) as output:
            directory = Path(temporary) / "publication"
            receipt = publish_synthetic_shadow(self.result, directory)
            self.assertEqual((directory / "receipt.json").read_bytes(), receipt.payload)
            sealed = (directory / "sealed/payload.json").read_bytes()
            self.assertEqual(sealed, self.result.payload)
            self.assertEqual(sha256(sealed).hexdigest(), json.loads(receipt.payload)["sealed_payload_sha256"])
            self.assertEqual(set(p.name for p in directory.iterdir()), {"sealed", "receipt.json"})
            self.assertEqual(output.getvalue(), "")

    def test_never_overwrite_existing_directory(self):
        with TemporaryDirectory(dir=ROOT) as temporary:
            directory = Path(temporary) / "publication"
            publish_synthetic_shadow(self.result, directory)
            with self.assertRaises(FileExistsError):
                publish_synthetic_shadow(self.result, directory)
            self.assertEqual((directory / "sealed/payload.json").read_bytes(), self.result.payload)

    def test_corrupt_result_rejected_before_publication(self):
        corrupt = object.__new__(a.SyntheticResult)
        object.__setattr__(corrupt, "payload", self.result.payload + b" ")
        object.__setattr__(corrupt, "payload_sha256", self.result.payload_sha256)
        with TemporaryDirectory(dir=ROOT) as temporary:
            directory = Path(temporary) / "publication"
            with self.assertRaises(ValueError):
                publish_synthetic_shadow(corrupt, directory)
            self.assertFalse(directory.exists())

    def test_rehashed_semantically_corrupt_evidence_rejected(self):
        mutations = [lambda d: d.update(p_value_method="one_sided"),
                     lambda d: d.update(shadow=False),
                     lambda d: d.update(preregistration_commit="0" * 40),
                     lambda d: d.update(unexpected=True),
                     lambda d: d["results"][0].update(gross_expectancy="999"),
                     lambda d: d["results"][0].update(p_value="0"),
                     lambda d: d["results"][0].update(q_value="0"),
                     lambda d: d["results"][0].update(bootstrap_interval=["0", "0"]),
                     lambda d: d["results"][0].update(comparator_gross_expectancy="999"),
                     lambda d: d["results"][0]["temporal_robustness"].update(qualified_block_count=8),
                     lambda d: d["results"][0].update(eligible_minutes=True),
                     lambda d: d["results"][0]["events"][0].__setitem__(0, -1),
                     lambda d: d["results"][0]["events"][0].__setitem__(1, "NaN"),
                     lambda d: d["results"].reverse(),
                     lambda d: d["results"].pop(),
                     lambda d: d["results"].append(d["results"][0]),
                     lambda d: d["input_identity"].update(data_kind="DEVELOPMENT")]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                doc = self.result.validate()
                mutate(doc)
                payload = a.canonical_json_bytes(doc)
                corrupt = object.__new__(a.SyntheticResult)
                object.__setattr__(corrupt, "payload", payload)
                object.__setattr__(corrupt, "payload_sha256", sha256(payload).hexdigest())
                with self.assertRaises(ValueError):
                    public_receipt(corrupt)

    def test_receipt_immutable_and_no_unseal_entrypoint(self):
        receipt = public_receipt(self.result)
        with self.assertRaises(FrozenInstanceError):
            receipt.payload = b"changed"
        from quantos.infrastructure.storage import af4c_shadow
        self.assertFalse(any(name.startswith("unseal") for name in dir(af4c_shadow)))


if __name__ == "__main__":
    unittest.main()
