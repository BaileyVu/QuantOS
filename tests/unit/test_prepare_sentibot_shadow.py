from pathlib import Path
import plistlib
import os
from unittest.mock import patch
import subprocess
import sys
import tempfile
import unittest

from scripts.prepare_sentibot_shadow import prepare


class PrepareSentibotShadowTests(unittest.TestCase):
    def make_repo(self, root):
        repo = root / "repo"
        (repo / "sentibot").mkdir(parents=True)
        (repo / "sentibot" / "sentibot_reach_shadow.py").write_text("# fixture\n")
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "fixture"], check=True, capture_output=True)
        return repo

    def test_prepares_full_sha_snapshot_new_state_and_shadow_only_plist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_repo(root)
            snapshot_root, state, plist_path = root / "snapshots", root / "external-state", root / "out" / "shadow.plist"
            result = prepare(repo, snapshot_root, state, plist_path, sys.executable)
            self.assertEqual(len(result["commit"]), 40)
            self.assertEqual(Path(result["snapshot"]), (snapshot_root / result["commit"] / "source").resolve())
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            with plist_path.open("rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(plist["Label"], "com.quantos.sentibot.reach-shadow")
            self.assertEqual(plist["ProgramArguments"], [
                os.path.abspath(sys.executable), "-m", "sentibot.sentibot_reach_shadow",
                "--state-dir", str(state.resolve()), "--every", "900", "--cycle-timeout", "600"])
            self.assertEqual(plist["WorkingDirectory"], result["snapshot"])
            self.assertEqual(plist["EnvironmentVariables"]["PYTHONPATH"], result["snapshot"])
            self.assertTrue(Path(plist["StandardOutPath"]).is_relative_to(state.resolve()))

    def test_preserves_virtualenv_executable_and_refuses_launchagents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_repo(root)
            python = root / "venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            with patch("scripts.prepare_sentibot_shadow.Path.home", return_value=root):
                with self.assertRaisesRegex(ValueError, "LaunchAgents"):
                    prepare(repo, root / "snapshots", root / "state",
                            root / "Library/LaunchAgents/shadow.plist", python)
            result = prepare(repo, root / "snapshots", root / "state", root / "shadow.plist", python)
            with Path(result["plist"]).open("rb") as handle:
                self.assertEqual(plistlib.load(handle)["ProgramArguments"][0], str(python))

    def test_rejects_dirty_source_and_does_not_create_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_repo(root)
            (repo / "untracked").write_text("dirty\n")
            state, plist_path = root / "state", root / "shadow.plist"
            with self.assertRaisesRegex(ValueError, "dirty source"):
                prepare(repo, root / "snapshots", state, plist_path, sys.executable)
            self.assertFalse(state.exists())
            self.assertFalse(plist_path.exists())

    def test_refuses_existing_state_or_plist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self.make_repo(root)
            state = root / "state"
            state.mkdir()
            with self.assertRaises(FileExistsError):
                prepare(repo, root / "snapshots", state, root / "shadow.plist", sys.executable)


if __name__ == "__main__":
    unittest.main()
