from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import deploy_three


class CleanSnapshotTests(unittest.TestCase):
    commit = "a" * 40

    def test_uses_full_sha_detached_worktree_and_reuses_clean_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, snapshots = Path(temp) / "repo", Path(temp) / "snapshots"
            repo.mkdir()
            calls = []

            def run(args, **kwargs):
                calls.append(args)
                if args[3] == "status":
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[3] == "rev-parse":
                    return subprocess.CompletedProcess(args, 0, self.commit, "")
                if args[3:5] == ["worktree", "add"]:
                    target = Path(args[-2])
                    (target / "src").mkdir(parents=True)
                    return subprocess.CompletedProcess(args, 0, "", "")
                self.fail(f"unexpected command: {args}")

            with patch.object(deploy_three.subprocess, "run", side_effect=run):
                source = deploy_three.clean_snapshot(repo, snapshots)
                self.assertEqual(source, (snapshots / self.commit / "source" / "src").resolve())
                self.assertEqual(deploy_three.clean_snapshot(repo, snapshots), source)

            add = next(c for c in calls if c[3:5] == ["worktree", "add"])
            self.assertEqual(add[-1], self.commit)
            self.assertEqual(sum(c[3:5] == ["worktree", "add"] for c in calls), 1)

    def test_refuses_tracked_or_untracked_source_changes_before_worktree_add(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, snapshots = Path(temp) / "repo", Path(temp) / "snapshots"
            repo.mkdir()
            with patch.object(deploy_three.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, "?? untracked.py\n", "")) as run:
                with self.assertRaisesRegex(SystemExit, "dirty source tree"):
                    deploy_three.clean_snapshot(repo, snapshots)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(snapshots.exists())

    def test_removes_new_worktree_if_post_add_validation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, snapshots = Path(temp) / "repo", Path(temp) / "snapshots"
            repo.mkdir()
            target = snapshots / self.commit / "source"

            def run(args, **kwargs):
                if args[3] == "status":
                    output = "" if Path(args[2]) == repo.resolve() else "?? unexpected.py\n"
                    return subprocess.CompletedProcess(args, 0, output, "")
                if args[3] == "rev-parse":
                    return subprocess.CompletedProcess(args, 0, self.commit, "")
                if args[3:5] == ["worktree", "add"]:
                    (Path(args[-2]) / "src").mkdir(parents=True)
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[3:5] == ["worktree", "remove"]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                self.fail(f"unexpected command: {args}")

            with patch.object(deploy_three.subprocess, "run", side_effect=run) as mocked:
                with self.assertRaisesRegex(RuntimeError, "cleanliness"):
                    deploy_three.clean_snapshot(repo, snapshots)
            self.assertFalse(target.exists())
            self.assertTrue(any(c.args[0][3:5] == ["worktree", "remove"]
                                for c in mocked.call_args_list))


if __name__ == "__main__":
    unittest.main()
