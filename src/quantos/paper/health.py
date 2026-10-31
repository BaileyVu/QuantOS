"""Heartbeat, feed staleness, alerting and external watchdog (stdlib only)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone


def _atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


class Heartbeat:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / "heartbeat.json"

    def beat(self, now_ms: int, detail: dict | None = None):
        doc = {"pid": os.getpid(), "updated_ms": int(now_ms), "iso": _iso(now_ms),
               "detail": detail or {}}
        _atomic_write(self.path, json.dumps(doc, sort_keys=True, default=str) + "\n")


class FeedMonitor:
    def __init__(self, stale_seconds: float = 60, startup_grace_seconds: float = 60):
        self.stale_seconds = stale_seconds
        self.grace = startup_grace_seconds
        self.last: dict[str, int] = {}
        self.expected: dict[str, int] = {}

    def expect(self, feed_name: str, now_ms: int):
        """Register a feed; if never seen it is stale once the grace has elapsed."""
        self.expected.setdefault(feed_name, int(now_ms))

    def note(self, feed_name: str, now_ms: int):
        self.last[feed_name] = int(now_ms)

    def stale(self, now_ms: int) -> list[tuple[str, float]]:
        out = []
        for name, t in self.last.items():
            age = (now_ms - t) / 1000
            if age > self.stale_seconds:
                out.append((name, age))
        for name, t in self.expected.items():
            if name in self.last:
                continue
            age = (now_ms - t) / 1000
            if age > self.stale_seconds + self.grace:
                out.append((name, age))
        return sorted(out)

    def ages(self, now_ms: int) -> dict[str, float]:
        return {n: (now_ms - t) / 1000 for n, t in self.last.items()}


def applescript_escape(text: str) -> str:
    text = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return " ".join(text.split())[:240]


class Alerter:
    def __init__(self, state_dir, notify: bool = True, cooldown_s: float = 900, runner=None,
                 platform: str | None = None, stream=None):
        self.dir = Path(state_dir)
        self.path = self.dir / "alerts.jsonl"
        self.notify = notify
        self.cooldown_ms = int(cooldown_s * 1000)
        self.runner = runner or subprocess.run
        self.platform = platform or sys.platform
        self.stream = stream
        self.active: dict[str, int] = {}   # key -> last alert ms (while not cleared)

    def _emit(self, level, key, message, now_ms, extra=None):
        rec = {"time_ms": int(now_ms), "iso": _iso(now_ms), "level": level, "key": key,
               "message": message}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError as e:
            print(f"ALERT-WRITE-FAILED {e}", file=self.stream or sys.stderr)
        print(f"[ALERT {level}] {key}: {message}", file=self.stream or sys.stderr, flush=True)
        if self.notify and self.platform == "darwin":
            script = f'display notification "{applescript_escape(f"{level} {key}: {message}")}" with title "QuantOS"'
            try:
                self.runner(["osascript", "-e", script], timeout=5, capture_output=True,
                            check=False)
            except Exception:
                pass
        return rec

    def alert(self, level: str, key: str, message: str, now_ms: int):
        last = self.active.get(key)
        if last is not None and now_ms - last < self.cooldown_ms:
            return None
        self.active[key] = int(now_ms)
        return self._emit(level, key, message, now_ms)

    def clear(self, key: str, now_ms: int):
        if key not in self.active:
            return None
        del self.active[key]
        return self._emit("recovered", key, f"{key} recovered", now_ms)


def watchdog_check(state_dir, now_ms: int, max_age_s: float = 120):
    path = Path(state_dir) / "heartbeat.json"
    try:
        doc = json.loads(path.read_text())
        updated = int(doc["updated_ms"])
    except FileNotFoundError:
        return False, "heartbeat missing"
    except (ValueError, KeyError, TypeError, OSError) as e:
        return False, f"heartbeat unreadable: {e}"
    age = (now_ms - updated) / 1000
    if age > max_age_s:
        return False, f"heartbeat stale {age:.0f}s (pid {doc.get('pid')})"
    return True, f"ok age {age:.0f}s"


def _run_status(state_dir):
    try:
        return json.loads((Path(state_dir) / "run.json").read_text()).get("status")
    except (OSError, ValueError, AttributeError):
        return None


def watchdog_main(state_dir, now_ms=None, max_age_s=120, alerter=None) -> int:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    ok, reason = watchdog_check(state_dir, now_ms, max_age_s)
    if ok:
        return 0
    if _run_status(state_dir) != "running":
        return 0   # run finished/stopped on purpose: nothing to watch
    # Separate process each minute: cooldown persists via the alerts file.
    alerter = alerter or Alerter(state_dir)
    _restore_cooldown(alerter, "watchdog", now_ms)
    alerter.alert("critical", "watchdog", f"QuantOS paper run unhealthy: {reason}", now_ms)
    return 1


def _restore_cooldown(alerter: Alerter, key: str, now_ms: int):
    try:
        for line in alerter.path.read_text().splitlines()[-200:]:
            r = json.loads(line)
            if r.get("key") == key:
                if r.get("level") == "recovered":
                    alerter.active.pop(key, None)
                else:
                    alerter.active[key] = int(r["time_ms"])
    except (OSError, ValueError, KeyError):
        pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quantos.paper.health")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watchdog")
    w.add_argument("--state", required=True)
    w.add_argument("--max-age", type=float, default=120)
    a = p.parse_args(argv)
    return watchdog_main(Path(a.state).expanduser(), max_age_s=a.max_age)


if __name__ == "__main__":
    raise SystemExit(main())
