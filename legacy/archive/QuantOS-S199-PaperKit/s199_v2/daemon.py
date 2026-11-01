from __future__ import annotations

import os
import sys
from pathlib import Path


def spawn(root: Path, runtime: Path, log: Path) -> None:
    """Double-fork from the invoking terminal and exec the paper engine."""
    first = os.fork()
    if first:
        return
    os.setsid()
    second = os.fork()
    if second:
        os._exit(0)

    runtime.mkdir(parents=True, exist_ok=True)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(null)
    os.close(fd)
    os.chdir(root)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), str(root / "vendor" / "prediction-market-backtesting")]
    )
    argv = [
        sys.executable,
        "-m",
        "s199_v2.cli",
        "engine",
        str(root),
        str(runtime),
    ]
    os.execve(sys.executable, argv, env)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: python -m s199_v2.daemon ROOT RUNTIME LOG")
    spawn(*(Path(value).resolve() for value in sys.argv[1:]))


if __name__ == "__main__":
    main()
