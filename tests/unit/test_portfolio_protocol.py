import json
from pathlib import Path
import tempfile
import unittest

from quantos.paper.research.protocol import seal, read_seal, claim_evaluation, complete_cell


class ProtocolTests(unittest.TestCase):
    def test_freeze_and_evaluation_are_exclusive_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {"training_end_exclusive": "2025-01-01T00:00:00Z",
                    "evaluation_start": "2025-01-01T00:00:00Z"}
            kwargs = dict(code_sha="abc", training_data_sha="1" * 64)
            value = seal(root / "seal.json", plan, {"risk": ".01"}, **kwargs)
            self.assertEqual(read_seal(root / "seal.json"), value)
            with self.assertRaises(FileExistsError):
                seal(root / "seal.json", plan, {"risk": ".02"}, **kwargs)
            claim_evaluation(root / "out", value, "2" * 64)
            with self.assertRaises(FileExistsError): claim_evaluation(root / "out", value, "2" * 64)
            complete_cell(root / "out", {"risk": ".01"}, {"net_return": -.2})
            with self.assertRaises(FileExistsError):
                complete_cell(root / "out", {"risk": ".01"}, {"net_return": .2})
            value["payload"]["selection"]["risk"] = ".02"
            (root / "seal.json").write_text(json.dumps(value))
            with self.assertRaises(ValueError): read_seal(root / "seal.json")

    def test_no_results_before_frozen_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): complete_cell(Path(tmp), {}, {})


if __name__ == "__main__": unittest.main()
