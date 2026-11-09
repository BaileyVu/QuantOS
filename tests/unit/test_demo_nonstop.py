import json
import tempfile
import unittest
import sqlite3
import plistlib
from unittest.mock import patch
from pathlib import Path

from scripts import demo_nonstop
from scripts.demo_nonstop import (
    DEMO, LABELS, WATCHDOG, STATE, _validate_template, _require_local_flat,
    apply_two_plists, execute_rollback, execute_start, prepare,
)

def journal(state, order=None):
    with sqlite3.connect(state / 'journal_pD.db') as db:
        db.execute('CREATE TABLE orders(payload TEXT)')
        if order: db.execute('INSERT INTO orders VALUES(?)', (json.dumps(order),))


def _plist(label, state, python, pythonpath, *, demo=False):
    args = [python]
    if demo:
        args += ["-m", "quantos.paper.cli", "start", "--mode", "paper", "--state", str(state),
                 "--hours", "72", "--portfolio", "live50", "--execution", "testnet"]
        env = {"QUANTOS_KEY_FILE": "/private/key.env", "QUANTOS_TESTNET_HOST": "demo",
               "QUANTOS_DEMO_PROFILE": "pD", "PYTHONPATH": pythonpath}
    else:
        args += ["-m", "quantos.live.watchdog", "--execution", "testnet", "--state", str(state)]
        env = {"QUANTOS_KEY_FILE": "/private/key.env", "QUANTOS_TESTNET_HOST": "demo",
               "QUANTOS_DEMO_PROFILE": "pD", "PYTHONPATH": pythonpath}
    return {"Label": label, "ProgramArguments": args, "EnvironmentVariables": env}


class DemoNonstopTests(unittest.TestCase):
    def test_prepare_uses_src_returned_by_clean_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); agents=root/'agents'; agents.mkdir()
            source=root/'snapshots'/'source'; (source/'src').mkdir(parents=True)
            signals=root/'signals.json'; signals.write_text('{}')
            for label in LABELS:
                value=_plist(label, STATE, '/usr/bin/python3', str(root/'5d8342d'/'src'), demo=label==DEMO)
                (agents/f'{label}.plist').write_bytes(plistlib.dumps(value))
            sha='a'*40
            def git(repo,*args):
                if args[0]=='status': return ''
                return '5d8342d'+'b'*33 if '--verify' in args else sha
            with patch('scripts.demo_nonstop._git', side_effect=git):
                result=prepare(root, plan_dir=root/'plan', snapshots=root/'snapshots',
                    launch_agents=agents, ml1h_signals=signals,
                    clean_snapshot_fn=lambda *_: source/'src')
            self.assertEqual(result['release_source'], str(source.resolve()))
            candidate=plistlib.loads((root/'plan'/'candidate-demo.plist').read_bytes())
            self.assertEqual(candidate['EnvironmentVariables']['PYTHONPATH'], str(source.resolve()/'src'))

    def test_local_unresolved_order_blocks_start_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp); journal(state, {'state':'PROTECTED'})
            with self.assertRaisesRegex(ValueError, 'unresolved orders'):
                _require_local_flat(state)

    def test_candidate_changes_only_snapshot_hours_and_ml1h_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            python = "/usr/bin/python3"
            old_src = Path(tmp) / "app-v4" / "5d8342d" / "src"
            old_src.mkdir(parents=True)
            demo = _plist(DEMO, Path.home() / "Library/Application Support/QuantOS/v4-demo200",
                          python, str(old_src), demo=True)
            watchdog = _plist(WATCHDOG, Path.home() / "Library/Application Support/QuantOS/v4-demo200",
                              python, str(old_src))
            source = Path(tmp) / "new" / "source"
            source.mkdir(parents=True)
            signals = Path(tmp) / "signals.json"
            signals.write_text("synthetic")
            candidate, candidate_watchdog, old_path = _validate_template(demo, watchdog, source, signals)
            self.assertEqual(candidate["ProgramArguments"], [*demo["ProgramArguments"][:demo["ProgramArguments"].index("--hours") + 1],
                                                             "0", *demo["ProgramArguments"][demo["ProgramArguments"].index("--hours") + 2:]])
            self.assertEqual(candidate["EnvironmentVariables"]["QUANTOS_KEY_FILE"], "/private/key.env")
            self.assertEqual(candidate["EnvironmentVariables"]["PYTHONPATH"], str(source / "src"))
            self.assertEqual(candidate["EnvironmentVariables"]["QUANTOS_ML1H_SIGNALS"], str(signals))
            self.assertEqual(candidate_watchdog["EnvironmentVariables"]["PYTHONPATH"], str(source / "src"))
            self.assertEqual(old_path, str(old_src))

    def test_apply_replaces_only_demo_and_watchdog_with_fake_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            python = "/usr/bin/python3"
            plists = {DEMO: _plist(DEMO, state, python, "/new/src", demo=True),
                      WATCHDOG: _plist(WATCHDOG, state, python, "/new/src")}
            loaded, booted, started, events = [], [], [], []

            def loaded_fn(label):
                loaded.append(label)
                return True

            apply_two_plists(plists, root / "LaunchAgents", loaded_fn=loaded_fn,
                             bootout_fn=lambda label: (booted.append(label), events.append(('stop',label))),
                             bootstrap_fn=lambda label, path: (started.append((label, path)),events.append(('start',label))))
            self.assertEqual(events, [('stop',WATCHDOG),('stop',DEMO),('start',DEMO),('start',WATCHDOG)])
            self.assertEqual(set(loaded), set(LABELS))
            self.assertEqual(set(booted), set(LABELS))
            self.assertEqual({label for label, _ in started}, set(LABELS))
            self.assertEqual({p.name for p in (root / "LaunchAgents").iterdir()},
                             {f"{DEMO}.plist", f"{WATCHDOG}.plist"})

    def test_rollback_sets_old_runtime_72h_deadline_and_preserves_journal_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir()
            run = {"status": "running", "hours": 0, "deadline_ms": None, "portfolio": "live50"}
            (state / "run.json").write_text(json.dumps(run))
            journal(state)
            (state / "capture").mkdir()
            (state / "capture" / "day.jsonl.gz").write_bytes(b"capture-fixture")
            original_journal = (state / "journal_pD.db").read_bytes()
            original_capture = (state / "capture" / "day.jsonl.gz").read_bytes()

            plan = root / "plan"
            plan.mkdir()
            old_source = root / "app-v4" / "5d8342d" / "src"
            old_source.mkdir(parents=True)
            old_sha = "5d8342d4e90613783a93a08a58bbc662906236b4"
            demo = _plist(DEMO, state, "/usr/bin/python3", str(old_source), demo=True)
            watchdog = _plist(WATCHDOG, state, "/usr/bin/python3", str(old_source))
            for name, data in (("previous-demo.plist", demo), ("previous-watchdog.plist", watchdog)):
                (plan / name).write_bytes(__import__("plistlib").dumps(data))
            signals = root / "signals.json"
            signals.write_text("fixture")
            (plan / "plan.json").write_text(json.dumps({"previous_sha": old_sha,
                                                         "previous_pythonpath": str(old_source),
                                                         "ml1h_signals": str(signals)}))

            started = []
            execute_rollback(plan, confirmed_flat=True, launch_agents=root / "LaunchAgents", state=state,
                             loaded_fn=lambda _label: False, bootout_fn=lambda _label: self.fail("unexpected bootout"),
                             bootstrap_fn=lambda label, path: started.append(label))
            after = json.loads((state / "run.json").read_text())
            self.assertEqual(after["hours"], 72)
            self.assertGreater(after["deadline_ms"], 0)
            self.assertEqual((state / "journal_pD.db").read_bytes(), original_journal)
            restored=plistlib.loads((root/'LaunchAgents'/f'{DEMO}.plist').read_bytes())['ProgramArguments']
            self.assertEqual(restored[restored.index('--hours')+1], '72')
            self.assertEqual((state / "capture" / "day.jsonl.gz").read_bytes(), original_capture)
            self.assertEqual(set(started), set(LABELS))
            self.assertEqual(len(list(plan.glob("rollback-run-before-*.json"))), 1)

    def test_execute_start_archives_only_completed_state_and_touches_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, plan, agents = root / "v4-demo200", root / "plan", root / "LaunchAgents"
            state.mkdir()
            plan.mkdir()
            (state / "run.json").write_text(json.dumps({"status": "completed", "hours": 72}))
            (state / "journal.db").write_bytes(b"complete-journal")
            journal(state)
            signals = root / "signals.json"
            signals.write_text("fixture")
            (plan / "plan.json").write_text(json.dumps({"ml1h_signals": str(signals)}))
            python = "/usr/bin/python3"
            candidate_demo = _plist(DEMO, state, python, "/new/src", demo=True)
            candidate_demo["EnvironmentVariables"]["QUANTOS_ML1H_SIGNALS"] = str(signals)
            for name, plist in (("candidate-demo.plist", candidate_demo),
                                ("candidate-watchdog.plist", _plist(WATCHDOG, state, python, "/new/src"))):
                (plan / name).write_bytes(__import__("plistlib").dumps(plist))
            booted, started = [], []
            archived = execute_start(plan, confirmed_flat=True, launch_agents=agents, state=state,
                                     loaded_fn=lambda _label: True, bootout_fn=booted.append,
                                     bootstrap_fn=lambda label, path: started.append(label))
            self.assertEqual(json.loads((archived / "run.json").read_text())["status"], "completed")
            self.assertEqual((archived / "journal.db").read_bytes(), b"complete-journal")
            self.assertTrue((state / "logs").is_dir())
            self.assertEqual(set(booted), set(LABELS))
            self.assertEqual(set(started), set(LABELS))


if __name__ == "__main__":
    unittest.main()


class StoppedWalJournalTest(unittest.TestCase):
    def test_flat_check_reads_a_stopped_journal_when_read_only_open_fails(self):
        import sqlite3 as real
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            db = real.connect(state / "journal_pD.db")
            db.execute("create table orders(payload)")
            db.execute("""insert into orders values ('{"state": "CLOSED"}')""")
            db.commit(); db.close()
            connect = real.connect

            def macos_like(target, **kw):  # older SQLite cannot open a stopped WAL db with mode=ro
                if str(target).endswith("?mode=ro"):
                    raise real.OperationalError("unable to open database file")
                return connect(target, **kw)

            with mock.patch.object(demo_nonstop.sqlite3, "connect", macos_like):
                demo_nonstop._require_local_flat(state)
                db = connect(state / "journal_pD.db")
                db.execute("""insert into orders values ('{"state": "OPEN"}')"""); db.commit(); db.close()
                with self.assertRaises(ValueError):
                    demo_nonstop._require_local_flat(state)
