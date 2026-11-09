"""Serialize runtime and independent watchdog exchange mutations on one state directory."""
from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def mirror_lock(state):
    with (Path(state) / "mirror.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
