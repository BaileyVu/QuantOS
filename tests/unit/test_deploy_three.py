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
                    raise OSError("simulated cleanup failure")
                self.fail(f"unexpected command: {args}")

            with patch.object(deploy_three.subprocess, "run", side_effect=run) as mocked:
                with self.assertRaisesRegex(RuntimeError, "cleanliness"):
                    deploy_three.clean_snapshot(repo, snapshots)
            self.assertFalse(target.exists())
            self.assertTrue(any(c.args[0][3:5] == ["worktree", "remove"]
                                for c in mocked.call_args_list))


class GitWorktreeIntegrationTests(unittest.TestCase):
    def make_repo(self, root):
        repo = root / "repo"
        repo.mkdir()
        self.git(repo, "init", "--quiet")
        self.git(repo, "config", "user.name", "QuantOS test")
        self.git(repo, "config", "user.email", "quantos-test@example.invalid")
        (repo / "src").mkdir()
        (repo / "src" / "sample.py").write_text("VALUE = 'committed'\n")
        self.git(repo, "add", "src/sample.py")
        self.git(repo, "commit", "--quiet", "-m", "test snapshot")
        return repo, self.git(repo, "rev-parse", "HEAD")

    @staticmethod
    def git(repo, *args):
        result = subprocess.run(["git", "-C", str(repo), *args], check=True,
                                capture_output=True, text=True)
        return result.stdout.strip()

    def remove_snapshot(self, repo, snapshots, commit):
        worktree = snapshots / commit / "source"
        if worktree.exists():
            self.git(repo, "worktree", "remove", "--force", str(worktree))

    def test_real_worktree_is_at_full_commit_and_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, commit = self.make_repo(root)
            snapshots = root / "snapshots"
            try:
                source = deploy_three.clean_snapshot(repo, snapshots)
                self.assertEqual(source, (snapshots / commit / "source" / "src").resolve())
                self.assertEqual((source / "sample.py").read_text(), "VALUE = 'committed'\n")
                self.assertEqual(self.git(source.parent, "rev-parse", "HEAD"), commit)
                self.assertIn(str(source.parent), self.git(repo, "worktree", "list", "--porcelain"))
                self.assertEqual(deploy_three.clean_snapshot(repo, snapshots), source)
            finally:
                self.remove_snapshot(repo, snapshots, commit)
            self.assertNotIn(str(snapshots / commit / "source"),
                             self.git(repo, "worktree", "list", "--porcelain"))

    def test_real_git_refuses_dirty_tracked_and_untracked_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, commit = self.make_repo(root)
            snapshots = root / "snapshots"
            tracked = repo / "src" / "sample.py"
            try:
                tracked.write_text("VALUE = 'modified'\n")
                with self.assertRaisesRegex(SystemExit, "dirty source tree"):
                    deploy_three.clean_snapshot(repo, snapshots)
                tracked.write_text("VALUE = 'committed'\n")
                (repo / "untracked.txt").write_text("not in commit\n")
                with self.assertRaisesRegex(SystemExit, "dirty source tree"):
                    deploy_three.clean_snapshot(repo, snapshots)
                self.assertFalse((snapshots / commit / "source").exists())
            finally:
                self.remove_snapshot(repo, snapshots, commit)
            self.assertNotIn(str(snapshots / commit / "source"),
                             self.git(repo, "worktree", "list", "--porcelain"))

    def test_real_git_refuses_dirty_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, commit = self.make_repo(root)
            snapshots = root / "snapshots"
            worktree = snapshots / commit / "source"
            try:
                source = deploy_three.clean_snapshot(repo, snapshots)
                (source / "intruder.txt").write_text("dirty snapshot\n")
                with self.assertRaisesRegex(SystemExit, "existing deployment snapshot is invalid or dirty"):
                    deploy_three.clean_snapshot(repo, snapshots)
            finally:
                self.remove_snapshot(repo, snapshots, commit)
            self.assertNotIn(str(worktree), self.git(repo, "worktree", "list", "--porcelain"))


if __name__ == "__main__":
    unittest.main()
