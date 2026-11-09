import hashlib
import json
from pathlib import Path
import plistlib
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import ml5m_cutover as cutover


class Ml5mCutoverTests(unittest.TestCase):
    def test_flat_check_rejects_positions_orders_and_invalid_payloads(self):
        for positions, orders, algos in (
            ([{"positionAmt": "-1"}], [], []), ([], [{}], []), ([], [], [{}]), ({}, [], []),
        ):
            with self.subTest(positions=positions, orders=orders, algos=algos):
                client = SimpleNamespace(position_risk=lambda: positions, open_orders=lambda: orders,
                                         open_algo_orders=lambda: algos)
                with self.assertRaises(ValueError):
                    cutover.require_flat(client)
        cutover.require_flat(SimpleNamespace(position_risk=lambda: [{"positionAmt": "0.00"}],
                                              open_orders=list, open_algo_orders=list))

    def test_install_and_rollback_preserve_the_book_and_retire_only_legacy_executor(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            plan, agents, old_app, scorer_state = [root / name for name in ("plan", "agents", "legacy", "scorer")]
            for directory in (plan, agents, old_app):
                directory.mkdir()
            model = old_app / "model.pkl"
            model.write_bytes(b"model")
            (old_app / "exec_state_testnet.json").write_text("[]")
            (old_app / "score_hist.json").write_text('{"some": "history"}')
            before, after = {}, {}
            metadata = {"model_sha256": hashlib.sha256(b"model").hexdigest()}
            for prefix, labels, values, sha in (("previous", cutover.BEFORE, before, "1" * 40),
                                                 ("release", cutover.AFTER, after, "2" * 40)):
                source = str(root / prefix / "src")
                metadata.update({f"{prefix}_source": source, f"{prefix}_sha": sha})
                for label in labels:
                    value = {"Label": label, "EnvironmentVariables": {"PYTHONPATH": source}}
                    values[label] = value
                    name = "previous" if prefix == "previous" else "candidate"
                    (plan / f"{name}-{label}.plist").write_bytes(plistlib.dumps(value))
                    if prefix == "previous":
                        (agents / f"{label}.plist").write_bytes(plistlib.dumps(value))
            (plan / "plan.json").write_text(json.dumps(metadata))
            active = set(cutover.BEFORE)
            launches = []
            hooks = (active.__contains__, active.remove, lambda label, _path: (launches.append(label), active.add(label)))

            def git(path, action, *_):
                return "" if action == "status" else metadata[f"{path.name}_sha"]

            with patch.multiple(cutover, LAUNCH_AGENTS=agents, OLD_APP=old_app, MODEL=model, SCORER_STATE=scorer_state,
                                _git=git, _state_status=lambda _: "running", _require_local_flat=lambda _: None,
                                _require_unlocked_state=lambda _: None, prime_scorer=lambda _: None):
                def nonflat_after_stop(_plists):
                    if not active:
                        raise cutover.DemoNotFlat("new exposure during shutdown")

                with self.assertRaises(cutover.DemoNotFlat):
                    cutover.switch(plan, False, hooks=hooks, exchange_check=nonflat_after_stop)
                self.assertEqual(active, set(cutover.BEFORE))
                self.assertTrue((agents / f"{cutover.LEGACY}.plist").is_file())

                launches.clear()
                cutover.switch(plan, False, hooks=hooks, exchange_check=lambda _: None)
                self.assertEqual(launches, list(cutover.AFTER))
                self.assertEqual(active, set(cutover.AFTER))
                self.assertFalse((agents / f"{cutover.LEGACY}.plist").exists())
                self.assertEqual((scorer_state / "score_hist.json").read_text(), '{"some": "history"}')

                launches.clear()
                cutover.switch(plan, True, hooks=hooks, exchange_check=lambda _: None)
                self.assertEqual(launches, list(cutover.BEFORE))
                self.assertEqual(active, set(cutover.BEFORE))
                self.assertFalse((agents / f"{cutover.SCORER}.plist").exists())
