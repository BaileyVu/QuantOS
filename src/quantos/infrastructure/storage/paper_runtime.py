"""Atomic bounded paper checkpoint plus append-only minute evaluation evidence."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from quantos.application.paper_runtime import PaperRuntimeError
from quantos.domain.runtime_contracts import arithmetic, canonical, identity, primitive


def _replace_durable(source: Path, target: Path) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
        move.restype = wintypes.BOOL
        if not move(str(source.resolve()), str(target.resolve()), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(source, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


class LocalPaperRuntimeStore:
    """One session writer. Stale locks and cross-file uncertainty fail closed.

    The checkpoint binds the evidence chain through its count/tail digest.
    Evidence contains evaluations, not a second execution/account ledger.
    """
    def __init__(self, state_path: str | Path, evidence_path: str | Path, ledger_path: str | Path):
        self.path, self.evidence_path = Path(state_path), Path(evidence_path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.temp_path = self.path.with_name(self.path.name + ".tmp")
        ledger = Path(ledger_path)
        self.ledger_lock_path = ledger.with_name(ledger.name + ".runtime.lock")
        paths = (self.path, self.evidence_path, self.lock_path, self.temp_path, ledger,
                 self.ledger_lock_path, *(ledger.with_name(ledger.name+s) for s in (".head", ".head.tmp", ".lock")))
        if len({p.resolve() for p in paths}) != len(paths):
            raise ValueError("state, evidence and companion paths must differ")
        self._descriptor = None
        self._ledger_descriptor = None
        self._latest = None
        self._expected = None
        self._count, self._tail, self._size = 0, None, 0

    def acquire(self):
        if self._descriptor is not None:
            raise PaperRuntimeError("runtime store already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._descriptor = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            self.ledger_lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._ledger_descriptor = os.open(self.ledger_lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except BaseException:
            self.release()
            raise
        if self.temp_path.exists():
            self.release()
            raise PaperRuntimeError("orphan runtime checkpoint temporary file; inspect before recovery")

    def release(self):
        if self._ledger_descriptor is not None:
            os.close(self._ledger_descriptor)
            self._ledger_descriptor = None
            self.ledger_lock_path.unlink()
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None
            self.lock_path.unlink()

    def _locked(self):
        if self._descriptor is None:
            raise PaperRuntimeError("runtime persistence requires the session lock")

    def load(self):
        self._locked()
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            self._expected = None
            return None
        value = json.loads(raw)
        if set(value) != {"state", "checksum"} or value["checksum"] != identity(value["state"]):
            raise PaperRuntimeError("corrupt runtime checkpoint checksum")
        if (canonical(value)+"\n").encode("ascii") != raw:
            raise PaperRuntimeError("noncanonical runtime checkpoint")
        self._expected = raw
        return value["state"]

    def save(self, state):
        self._locked()
        try:
            current = self.path.read_bytes()
        except FileNotFoundError:
            current = None
        if current != self._expected:
            raise PaperRuntimeError("runtime checkpoint changed during session")
        payload = (canonical({"state": state, "checksum": identity(state)})+"\n").encode("ascii")
        with self.temp_path.open("xb") as stream:
            if stream.write(payload) != len(payload):
                raise PaperRuntimeError("incomplete runtime checkpoint write")
            stream.flush()
            os.fsync(stream.fileno())
        _replace_durable(self.temp_path, self.path)
        self._expected = payload

    def evidence_head(self):
        self._locked()
        count, tail, size = 0, None, 0
        self._latest = None
        totals = {"fees": Decimal("0"), "slippage": Decimal("0"), "realized_pnl": Decimal("0"), "trade_count": 0}
        previous_time = None
        try:
            stream = self.evidence_path.open("rb")
        except FileNotFoundError:
            self._count, self._tail, self._size = 0, None, 0
            return 0, None
        with stream:
            for raw in stream:
                item = json.loads(raw)
                if (canonical(item)+"\n").encode("ascii") != raw:
                    raise PaperRuntimeError("corrupt minute evidence framing")
                if set(item) != {"sequence", "previous", "evidence"} or item["sequence"] != count or item["previous"] != tail:
                    raise PaperRuntimeError("corrupt minute evidence chain")
                evidence = item["evidence"]
                timestamp = datetime.fromisoformat(evidence["equity"]["timestamp"])
                if previous_time is not None and timestamp-previous_time != timedelta(minutes=1):
                    raise PaperRuntimeError("nonchronological minute evidence")
                with localcontext(arithmetic()):
                    for decision in evidence["decisions"]:
                        result = decision["execution"]
                        if result is not None:
                            totals["fees"] += Decimal(result["fee"])
                            totals["slippage"] += Decimal(result["slippage_cost"])
                    totals["realized_pnl"] += sum((Decimal(t["net_pnl"]) for t in evidence["completed_trades"]), Decimal("0"))
                totals["trade_count"] += len(evidence["completed_trades"])
                if primitive(totals) != evidence["totals"]:
                    raise PaperRuntimeError("minute evidence cumulative totals mismatch")
                previous_time, self._latest = timestamp, evidence
                count, tail, size = count+1, identity(item), size+len(raw)
        if size == 0:
            raise PaperRuntimeError("empty existing minute evidence file")
        self._count, self._tail, self._size = count, tail, size
        return count, tail

    def latest_evidence(self):
        self._locked()
        return self._latest

    def append(self, evidence):
        self._locked()
        try:
            size = self.evidence_path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size != self._size:
            raise PaperRuntimeError("minute evidence changed during session")
        item = {"sequence": self._count, "previous": self._tail, "evidence": evidence}
        payload = (canonical(item)+"\n").encode("ascii")
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        with self.evidence_path.open("ab" if self._count else "xb") as stream:
            if stream.write(payload) != len(payload):
                raise PaperRuntimeError("incomplete minute evidence append")
            stream.flush()
            os.fsync(stream.fileno())
        self._count += 1
        self._size += len(payload)
        self._tail = identity(item)
        self._latest = evidence
        return self._count, self._tail
