"""Prepare a clean Sentibot shadow snapshot and LaunchAgent plist; never install it."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _overlaps(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def clean_snapshot(repo: str | Path, snapshots: str | Path) -> tuple[str, Path]:
    """Return (full commit SHA, immutable-by-convention detached clean worktree)."""
    repo = Path(repo).expanduser().resolve()
    snapshots = Path(snapshots).expanduser().resolve()
    if _overlaps(repo, snapshots):
        raise ValueError("snapshot root must be external to source tree")
    dirty = _git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
    if dirty:
        raise ValueError(f"refusing dirty source tree: {dirty.splitlines()[0]}")
    commit = _git(repo, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("git returned an invalid full commit SHA")
    worktree = Path(snapshots).expanduser().resolve() / commit / "source"
    if worktree.exists():
        if (_git(worktree, "rev-parse", "HEAD") != commit or
                _git(worktree, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none") or
                not (worktree / "sentibot" / "sentibot_reach_shadow.py").is_file()):
            raise ValueError(f"existing snapshot is invalid or dirty: {worktree}")
        return commit, worktree
    worktree.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), commit],
                       check=True, capture_output=True, text=True)
        if (_git(worktree, "rev-parse", "HEAD") != commit or
                _git(worktree, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none") or
                not (worktree / "sentibot" / "sentibot_reach_shadow.py").is_file()):
            raise ValueError(f"new snapshot failed verification: {worktree}")
    except Exception:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                       capture_output=True, check=False)
        raise
    return commit, worktree


def prepare(repo: str | Path, snapshots: str | Path, state_dir: str | Path,
            plist_path: str | Path, python_path: str | Path, *, every: int = 900,
            cycle_timeout: int = 600) -> dict:
    """Create a detached source snapshot, new private state directory, and plist only."""
    repo = Path(repo).expanduser().resolve()
    snapshots = Path(snapshots).expanduser().resolve()
    state = Path(state_dir).expanduser().resolve()
    plist_file = Path(plist_path).expanduser().resolve()
    python = Path(python_path).expanduser().resolve()
    if every < 60 or cycle_timeout < 1 or cycle_timeout >= every:
        raise ValueError("require every >= 60 and 1 <= cycle_timeout < every")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("pinned Python executable must exist and be executable")
    if state.exists() or plist_file.exists():
        raise FileExistsError("state directory and plist output must be new paths")
    if _overlaps(repo, state) or _overlaps(snapshots, state):
        raise ValueError("state directory must be external to source and snapshots")
    if _overlaps(repo, plist_file) or _overlaps(snapshots, plist_file):
        raise ValueError("plist output must be external to source and snapshots")
    if state == plist_file or state in plist_file.parents or plist_file in state.parents:
        raise ValueError("state and plist paths must be separate")

    commit, source = clean_snapshot(repo, snapshots)
    state.mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    logs = state / "logs"
    logs.mkdir(mode=0o700)
    label = "com.quantos.sentibot.reach-shadow"
    plist = {
        "Label": label,
        "ProgramArguments": [str(python), "-m", "sentibot.sentibot_reach_shadow",
                             "--state-dir", str(state), "--every", str(every),
                             "--cycle-timeout", str(cycle_timeout)],
        "WorkingDirectory": str(source),
        "EnvironmentVariables": {"PYTHONPATH": str(source)},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,
        "StandardOutPath": str(logs / "out.log"),
        "StandardErrorPath": str(logs / "err.log"),
    }
    plist_file.parent.mkdir(parents=True, exist_ok=True)
    with plist_file.open("xb") as handle:
        plistlib.dump(plist, handle, sort_keys=True)
    return {"commit": commit, "snapshot": str(source), "state_dir": str(state),
            "plist": str(plist_file), "label": label}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--snapshots", required=True, help="new snapshot root; full SHA subdirectory is used")
    parser.add_argument("--state-dir", required=True, help="new dedicated external state directory")
    parser.add_argument("--plist", required=True, help="new plist output path; not installed")
    parser.add_argument("--python", required=True, help="absolute pinned executable path")
    parser.add_argument("--every", type=int, default=900)
    parser.add_argument("--cycle-timeout", type=int, default=600)
    args = parser.parse_args(argv)
    result = prepare(args.repo, args.snapshots, args.state_dir, args.plist,
                     args.python, every=args.every, cycle_timeout=args.cycle_timeout)
    print("prepared", result["commit"], result["snapshot"], result["state_dir"], result["plist"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
