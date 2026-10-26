"""macOS launchd supervision for the local paper CLI.

This helper only installs/inspects/stops its fixed per-user launchd label; it
never signals arbitrary PIDs. Python is the directly supervised process so
launchd delivers SIGTERM to the runtime for graceful checkpoint/close. Runtime
may manage its own caffeinate child; a closed lid, power loss, or OS policy can
still stop observation.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile

LABEL = "com.quantos.v3.paper"


def build_plist(python: Path, state: Path, logdir: Path,
                duration_hours: int = 72) -> dict:
    # Keep the venv executable's lexical path; resolving its symlink can select
    # the base interpreter and lose the venv's installed package environment.
    python = Path(os.path.abspath(os.path.expanduser(os.fspath(python))))
    state, logdir = (Path(p).expanduser().resolve() for p in (state, logdir))
    if not 0 < duration_hours <= 72:
        raise ValueError("duration_hours must be between 1 and 72")
    logs = logdir / "quantos-v3-paper.log"
    errors = logdir / "quantos-v3-paper.err.log"
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "quantos.paper.cli", "start", "--mode", "paper",
                             "--state", str(state), "--hours", str(duration_hours)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        # The runtime uses up to 25 seconds for graceful checkpoint/close.
        "ExitTimeOut": 45,
        "ThrottleInterval": 30,
        "WorkingDirectory": str(state.parent),
        "StandardOutPath": str(logs),
        "StandardErrorPath": str(errors),
    }


def _launchctl() -> str:
    if os.name != "posix" or not Path("/System/Library/LaunchDaemons").exists():
        raise RuntimeError("launchd service management requires macOS")
    path = shutil.which("launchctl")
    if not path:
        raise RuntimeError("launchctl is unavailable")
    return path


def _domain() -> str:
    if not hasattr(os, "getuid"):
        raise RuntimeError("per-user launchd domain unavailable")
    return f"gui/{os.getuid()}"


def install_service(python: Path, state: Path, duration_hours: int = 72) -> Path:
    launchctl = _launchctl()
    python = Path(os.path.abspath(os.path.expanduser(os.fspath(python))))
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("python must be an existing executable file")
    existing = subprocess.run([launchctl, "print", f"{_domain()}/{LABEL}"],
                              check=False, capture_output=True, text=True)
    if existing.returncode == 0:
        raise RuntimeError("paper service is already loaded; stop it before replacing its plist")
    if "Could not find service" not in (existing.stderr or ""):
        raise RuntimeError("could not confirm paper service is stopped")
    path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    logdir = Path(state).expanduser().resolve() / "logs"
    plist = build_plist(python, state, logdir, duration_hours)
    path.parent.mkdir(parents=True, exist_ok=True)
    logdir.mkdir(parents=True, exist_ok=True)
    logdir.chmod(0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{LABEL}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(plist, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    subprocess.run([launchctl, "bootstrap", _domain(), str(path)], check=True,
                   capture_output=True, text=True)
    return path


def status_service() -> bool:
    launchctl = _launchctl()
    result = subprocess.run([launchctl, "print", f"{_domain()}/{LABEL}"],
                            check=False, capture_output=True, text=True)
    return result.returncode == 0


def stop_service() -> bool:
    """Boot out this exact launchd service; launchd delivers its normal SIGTERM."""
    launchctl = _launchctl()
    result = subprocess.run([launchctl, "bootout", f"{_domain()}/{LABEL}"],
                            check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    # An already absent service is a successful idempotent stop.
    if "Could not find service" in (result.stderr or ""):
        return False
    raise subprocess.CalledProcessError(result.returncode, result.args,
                                        output=result.stdout, stderr=result.stderr)
