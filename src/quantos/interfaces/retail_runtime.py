"""Operator-owned foreground QOS07 launcher; no service installation or daemon."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import sys
import uuid


def durable(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def now():
    return datetime.now(timezone.utc).isoformat()


class BoundedLog:
    def __init__(self, path, maximum=64 * 1024 ** 2):
        self.stream = path.open("x", buffering=1)
        self.maximum, self.written = maximum, 0

    def write(self, value):
        size = len(value.encode())
        if self.written + size > self.maximum:
            raise RuntimeError("runtime log capacity reached")
        self.stream.write(value)
        self.written += size
        return len(value)

    def flush(self):
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.flush()
        self.stream.close()


def preflight(args):
    if sys.version_info < (3, 11):
        raise ValueError("Python >=3.11 required")
    for module in ("quantos", "websockets", "duckdb", "pyarrow", "numpy", "scipy", "lightgbm"):
        importlib.import_module(module)
    from quantos.infrastructure.configuration.hft import load_hft_config
    from quantos.infrastructure.configuration.retail import load_retail_profile
    from quantos.infrastructure.storage.retail_paper import exclusive_account
    from quantos.interfaces.retail_edge import read_state
    config, policy = load_hft_config(args.market_config), load_retail_profile(args.capital_profile)
    if config.starting_equity != 200 or config.research_only:
        raise ValueError("operational $200 market configuration required")
    active = args.state_store.with_suffix(args.state_store.suffix + ".session.json")
    if active.exists():
        raise ValueError("unreconciled prior managed session; inspect account session marker and preserve capture")
    marker = args.state_store.with_suffix(args.state_store.suffix + ".kill")
    if marker.exists():
        raise ValueError("operator kill marker present; explicit operator recovery required")
    with exclusive_account(args.state_store):
        account, runtime = read_state(args.state_store)
        if account.policy != policy:
            raise ValueError("account/profile mismatch; no automatic migration")
    if shutil.disk_usage(args.state_store.parent).free < 9 * 1024 ** 3:
        raise ValueError("need 9 GiB free for bounded 8 GiB journal and reserves")
    return {"python": platform.python_version(), "architecture": platform.machine(),
            "profile": policy.profile, "balance": str(account.balance),
            "account_sha256": sha256(args.state_store.read_bytes()).hexdigest(),
            "public_access": "validated during managed startup; offline preflight does not contact exchange"}


def session_status(path):
    startup = json.loads((path / "startup.json").read_text())
    terminal = path / "exit.json"
    if terminal.exists():
        return {"startup": startup, "terminal": json.loads(terminal.read_text())}
    with (path / "session.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            status = "active_process; inspect runtime log for feed health"
        else:
            status = "terminated_without_exit; preserve incomplete capture and reconcile account"
            fcntl.flock(stream, fcntl.LOCK_UN)
    return {"startup": startup, "status": status}


def managed_run(args, operation=None):
    if not 1 <= args.duration_seconds <= 43200:
        raise ValueError("duration must be 1..43200 seconds")
    args.session.mkdir(parents=True, exist_ok=False)
    with (args.session / "session.lock").open("x") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        startup = {"session_id": str(uuid.uuid4()), "pid": os.getpid(), "started_at": now(),
                   "duration_seconds": args.duration_seconds, "strategy": "Q05-LR-v1",
                   "profile": str(args.capital_profile.resolve()), "state": str(args.state_store.resolve()),
                   "mode": "PUBLIC PAPER ONLY", "authenticated_requests": 0, "economic_orders": 0}
        durable(args.session / "startup.json", startup)
        code, reason, error = 1, "startup_failure", None
        handlers = {}
        log = None
        requested = []
        active = args.state_store.with_suffix(args.state_store.suffix + ".session.json")
        claimed = False
        def stop(signum, frame):
            requested.append(signum)
            args.state_store.with_suffix(args.state_store.suffix + ".kill").touch()
        try:
            log = BoundedLog(args.session / "runtime.jsonl")
            with redirect_stdout(log), redirect_stderr(log):
                for sig in (signal.SIGINT, signal.SIGTERM):
                    handlers[sig] = signal.signal(sig, stop)
                checks = preflight(args)
                durable(args.session / "preflight.json", checks)
                with active.open("x") as claim:
                    json.dump({"session": str(args.session.resolve()), "pid": os.getpid()}, claim)
                    claim.flush()
                    os.fsync(claim.fileno())
                claimed = True
                from quantos.interfaces.retail_edge import edge_command
                namespace = argparse.Namespace(futures_operation="retail-edge-paper", strategy="qos07",
                    state_store=args.state_store, market_config=args.market_config, capital_profile=args.capital_profile,
                    capture=args.session / "events.jsonl", duration_seconds=args.duration_seconds, managed_session=str(args.session.resolve()))
                code = (operation or edge_command)(namespace)
                if code == 0:
                    from quantos.interfaces.retail_edge import replay_capture, read_state
                    from quantos.application.retail_replay import stable_json
                    replay = replay_capture(args.session / "events.jsonl")
                    account, runtime = read_state(args.state_store)
                    ledger = {"balance": account.balance, "position": account.position,
                              "pending": account.pending, "fills": account.fills,
                              "lifecycle": account.lifecycle, "funding": account.funding_ledger}
                    if sha256(stable_json(ledger).encode()).hexdigest() != replay["metrics"]["ledger_digest"]:
                        raise ValueError("durable account ledger differs from terminal capture")
                    if stable_json(runtime.get("metrics")) != stable_json(replay["metrics"]):
                        raise ValueError("terminal capture and durable account checkpoint differ")
                reason = ("operator_signal" if requested else "bounded_session_finished") if code == 0 else "runtime_nonzero_exit"
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            code = 130 if isinstance(exc, KeyboardInterrupt) else 1
            reason = "startup_or_runtime_failure"
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            try:
                if log:
                    log.close()
            except OSError as exc:
                code, reason, error = 1, "log_flush_failure", {"type": type(exc).__name__, "message": str(exc)}
            durable(args.session / "exit.json", {"ended_at": now(), "exit_code": code,
                "reason": reason, "error": error, "signals": requested,
                "capture_complete_required": True, "account_reconciliation_required_before_restart": True})
        if code == 0 and claimed:
            active.unlink()
        return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight", "run", "status"))
    parser.add_argument("--session", type=Path)
    parser.add_argument("--state-store", type=Path)
    parser.add_argument("--market-config", type=Path)
    parser.add_argument("--capital-profile", type=Path)
    parser.add_argument("--duration-seconds", type=int)
    args = parser.parse_args(argv)
    if args.operation == "status":
        if args.session is None:
            parser.error("--session required")
        print(json.dumps(session_status(args.session), sort_keys=True))
        return 0
    if any(getattr(args, key) is None for key in ("state_store", "market_config", "capital_profile")):
        parser.error("explicit state-store, market-config and capital-profile required")
    if args.operation == "preflight":
        print(json.dumps(preflight(args), sort_keys=True))
        return 0
    if args.session is None or args.duration_seconds is None:
        parser.error("run requires unique --session and bounded --duration-seconds")
    return managed_run(args)


if __name__ == "__main__":
    sys.exit(main())
