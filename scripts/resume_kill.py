"""Clear an engaged kill switch on one run and restart its LaunchAgent.

A kill leaves two marks: the <state>/KILL file and the journal's meta.paused = "KILL_SWITCH";
the running process also remembers it, so a restart is needed. This undoes all three.

Usage: python scripts/resume_kill.py [--state DIR] [--profile pD] [--label com.quantos.v4.demo200] [--no-restart]
"""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

STATE = Path.home() / "Library/Application Support/QuantOS/v4-demo200"


def resume(state: Path, profile: str) -> list[str]:
    done, kill = [], state / "KILL"
    if kill.exists():
        backup = state.parent / f"KILL.{state.name}.{time.strftime('%Y%m%dT%H%M%S')}.bak"
        shutil.copy2(kill, backup)
        reason = kill.read_text().strip()
        kill.unlink()
        done.append(f"removed KILL ({reason}); backup {backup}")
    db = sqlite3.connect(state / f"journal_{profile}.db")
    try:
        row = db.execute("SELECT value FROM meta WHERE key='paused'").fetchone()
        paused = json.loads(row[0]) if row else False
        if paused == "KILL_SWITCH":
            with db: db.execute("UPDATE meta SET value=? WHERE key='paused'", (json.dumps(False),))
            done.append("journal paused KILL_SWITCH -> false")
        elif paused:
            done.append(f"journal paused={paused!r} left alone (not a kill switch pause)")
    finally:
        db.close()
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", type=Path, default=STATE)
    ap.add_argument("--profile", default="pD")
    ap.add_argument("--label", default="com.quantos.v4.demo200")
    ap.add_argument("--no-restart", action="store_true")
    a = ap.parse_args()
    for line in resume(a.state, a.profile) or ["nothing to clear"]: print(line)
    if not a.no_restart:
        target = f"gui/{subprocess.getoutput('id -u')}/{a.label}"
        subprocess.run(["launchctl", "kickstart", "-k", target], check=True)
        print(f"restarted {a.label}")


if __name__ == "__main__":
    main()
