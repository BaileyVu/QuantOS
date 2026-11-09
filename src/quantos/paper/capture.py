"""Passive, append-only demo evidence; failures never change execution results."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import os
from pathlib import Path
import sqlite3
import threading

from .types import dumps


class CaptureWriter:
    def __init__(self, state: Path):
        self.root = Path(state) / "capture"
        self.lock = threading.RLock()
        self.errors = self.events = 0
        self.last_error = None
        self.closed = False
        self.pending_fills = []

    def _error(self, exc):
        self.errors += 1
        self.last_error = type(exc).__name__

    def status(self):
        with self.lock:
            return {"errors": self.errors, "last_error": self.last_error,
                    "events": self.events, "closed": self.closed}

    def record(self, kind, payload, now_ms):
        with self.lock:
            if self.closed:
                return
            try:
                day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).strftime("%Y-%m-%d")
                encoded = (dumps({"schema": 1, "kind": kind, "received_ms": now_ms,
                                  "payload": payload}) + "\n").encode("utf-8")
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                if self.root.is_symlink():
                    raise ValueError("capture directory cannot be a symlink")
                fd = os.open(self.root / f"{day}.jsonl.gz",
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "ab") as raw:
                    fcntl.flock(raw.fileno(), fcntl.LOCK_EX)
                    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                        stream.write(encoded)
                    raw.flush()
                    os.fsync(raw.fileno())
                    fcntl.flock(raw.fileno(), fcntl.LOCK_UN)
                self.events += 1
            except Exception as exc:
                self._error(exc)

    def input_bytes(self, source, raw, now_ms):
        with self.lock:
            try:
                self.record("input_bytes", {"source": source, "encoding": "base64",
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "bytes": base64.b64encode(raw).decode("ascii")}, now_ms)
            except Exception as exc:
                self._error(exc)

    def buffer_fill(self, observation):
        # Engine calls this inside its transaction; no disk write until commit.
        with self.lock:
            if not self.closed:
                self.pending_fills.append(observation)

    def flush_fills(self, engines):
        with self.lock:
            pending, self.pending_fills = self.pending_fills, []
            by_profile = {e.journal.profile: e for e in engines}
            for observation in pending:
                try:
                    engine = by_profile[observation["profile"]]
                    db = engine.journal.db
                    if db.in_transaction:
                        self.pending_fills.append(observation)
                        continue
                    # A rolled-back/voided fill is not committed evidence.
                    exists = db.execute("SELECT 1 FROM fills WHERE id=?", (observation["fill_id"],)).fetchone()
                    if exists:
                        self.record("model_fill", observation, observation["time_ms"])
                except Exception as exc:
                    self._error(exc)

    def snapshot(self, journal, phase, now_ms):
        with self.lock:
            if self.closed:
                return
            temp, created = None, False
            try:
                if journal.db.in_transaction:
                    raise ValueError("snapshot requires a committed journal")
                folder = self.root / "snapshots"
                if self.root.is_symlink() or folder.is_symlink():
                    raise ValueError("snapshot directory cannot be a symlink")
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                name = f"{phase}-{now_ms}-{os.getpid()}-{journal.profile}.db"
                target, temp = folder / name, folder / (name + ".tmp")
                # Refuse overwrite, including an interrupted earlier snapshot.
                if target.exists():
                    raise FileExistsError(name)
                fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                created = True
                destination = sqlite3.connect(temp)
                try:
                    journal.db.backup(destination)
                finally:
                    destination.close()
                digest = hashlib.sha256(temp.read_bytes()).hexdigest()
                os.rename(temp, target)
                self.record("journal_snapshot", {"phase": phase, "profile": journal.profile,
                            "file": f"snapshots/{name}", "sha256": digest}, now_ms)
            except Exception as exc:
                self._error(exc)
                if created:
                    try: temp.unlink(missing_ok=True)
                    except OSError: pass

    def close(self):
        with self.lock:
            self.closed = True
