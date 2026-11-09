"""Owner-gated prepare/start/rollback for only the QuantOS demo and watchdog."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import sys
import time


DEMO = "com.quantos.v4.demo200"
WATCHDOG = "com.quantos.v4.demo200.watchdog"
LABELS = (DEMO, WATCHDOG)
ML1H_SIGNALS = Path("/Users/bailey12/Library/Application Support/ml1h/app/signals.json")
QOS = Path.home() / "Library" / "Application Support" / "QuantOS"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
STATE = QOS / "v4-demo200"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required plist missing or unsafe: {path}")
    with path.open("rb") as handle:
        value = plistlib.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"invalid plist: {path}")
    return value


def _write_new(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)


def _set_arg(args: list[str], name: str, value: str) -> list[str]:
    result = list(args)
    try:
        index = result.index(name)
    except ValueError:
        raise ValueError(f"existing plist is missing {name}") from None
    if index + 1 >= len(result):
        raise ValueError(f"existing plist has no value for {name}")
    result[index + 1] = value
    return result


def _state_from_args(plist: dict) -> Path:
    args = plist.get("ProgramArguments")
    if not isinstance(args, list) or not args or not all(isinstance(v, str) for v in args):
        raise ValueError("invalid ProgramArguments")
    try:
        state = Path(args[args.index("--state") + 1]).expanduser()
    except (ValueError, IndexError):
        raise ValueError("demo plist must identify --state") from None
    if state.resolve() != STATE.resolve():
        raise ValueError(f"demo plist points at unexpected state path: {state}")
    return state


def _validate_template(demo: dict, watchdog: dict, source: Path,
                       ml1h_signals: Path) -> tuple[dict, dict, str]:
    if demo.get("Label") != DEMO or watchdog.get("Label") != WATCHDOG:
        raise ValueError("installed plists do not match the two allowed service labels")
    demo_env = demo.get("EnvironmentVariables")
    watchdog_env = watchdog.get("EnvironmentVariables")
    if not isinstance(demo_env, dict) or not isinstance(watchdog_env, dict):
        raise ValueError("both installed plists must have environment mappings")
    for required in ("QUANTOS_KEY_FILE", "QUANTOS_TESTNET_HOST", "QUANTOS_DEMO_PROFILE"):
        if not demo_env.get(required):
            raise ValueError(f"existing demo plist lacks required environment key {required}")
    if demo_env["QUANTOS_TESTNET_HOST"] != "demo":
        raise ValueError("demo plist must target Binance Demo")
    if not demo.get("ProgramArguments") or not watchdog.get("ProgramArguments"):
        raise ValueError("both existing plists need ProgramArguments")
    state = _state_from_args(demo)
    if Path(watchdog["ProgramArguments"][watchdog["ProgramArguments"].index("--state") + 1]).resolve() != state.resolve():
        raise ValueError("demo and watchdog must share the existing state directory")
    if not Path(demo["ProgramArguments"][0]).is_file() or not os.access(demo["ProgramArguments"][0], os.X_OK):
        raise ValueError("existing demo Python executable is unavailable")
    old_pythonpath = demo_env.get("PYTHONPATH")
    if not isinstance(old_pythonpath, str) or not old_pythonpath:
        raise ValueError("existing demo PYTHONPATH is missing")
    old_source = Path(old_pythonpath)
    short_sha = old_source.parent.name if old_source.name == "src" else ""
    if not re.fullmatch(r"[0-9a-f]{7,40}", short_sha):
        raise ValueError("cannot identify previous snapshot SHA from installed PYTHONPATH")
    return {**demo, "ProgramArguments": _set_arg(demo["ProgramArguments"], "--hours", "0"),
            "EnvironmentVariables": {**demo_env, "PYTHONPATH": str(source / "src"),
                                     "QUANTOS_ML1H_SIGNALS": str(ml1h_signals)}}, \
           {**watchdog, "EnvironmentVariables": {**watchdog_env, "PYTHONPATH": str(source / "src")}}, old_pythonpath


def prepare(repo: str | Path, *, plan_dir: str | Path, snapshots: str | Path,
            launch_agents: str | Path = LAUNCH_AGENTS, ml1h_signals: str | Path = ML1H_SIGNALS,
            clean_snapshot_fn=None) -> dict:
    """Write candidate plists and a redacted plan outside LaunchAgents/state."""
    repo = Path(repo).expanduser().resolve()
    plan = Path(plan_dir).expanduser().resolve()
    snapshots = Path(snapshots).expanduser().resolve()
    launch_agents = Path(launch_agents).expanduser().resolve()
    if plan.exists():
        raise FileExistsError(f"plan output must be new: {plan}")
    if plan == launch_agents or launch_agents in plan.parents or plan in launch_agents.parents:
        raise ValueError("plan output must be outside LaunchAgents")
    if plan == STATE or STATE in plan.parents or plan in STATE.parents:
        raise ValueError("plan output must be separate from demo state")
    if Path(ml1h_signals).is_symlink() or not Path(ml1h_signals).is_file():
        raise FileNotFoundError(f"ML1h signals file must exist: {ml1h_signals}")
    dirty = _git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
    if dirty:
        raise ValueError(f"release checkout must be clean: {dirty.splitlines()[0]}")
    sha = _git(repo, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("release checkout did not return a full commit SHA")
    if clean_snapshot_fn is None:
        sys.path.insert(0, str(repo / "scripts"))
        from deploy_three import clean_snapshot as clean_snapshot_fn
    # PR51 returns the src directory, not the worktree root.
    source = Path(clean_snapshot_fn(repo, snapshots)).resolve().parent
    if not (source / "src").is_dir() or _git(source, "rev-parse", "HEAD") != sha:
        raise ValueError("candidate snapshot does not match the clean release SHA")

    old_demo_path = launch_agents / f"{DEMO}.plist"
    old_watchdog_path = launch_agents / f"{WATCHDOG}.plist"
    old_demo, old_watchdog = _load(old_demo_path), _load(old_watchdog_path)
    candidate_demo, candidate_watchdog, previous_pythonpath = _validate_template(
        old_demo, old_watchdog, source, Path(ml1h_signals).expanduser().resolve())
    # This validates the historical source identity against repository metadata; it never reads credentials.
    previous_short = Path(previous_pythonpath).parent.name
    previous_sha = _git(repo, "rev-parse", "--verify", f"{previous_short}^{{commit}}")
    if not previous_sha.startswith(previous_short):
        raise ValueError("previous snapshot prefix does not match repository metadata")

    plan.mkdir(parents=True, mode=0o700)
    plan.chmod(0o700)
    for name, plist in (("candidate-demo.plist", candidate_demo),
                        ("candidate-watchdog.plist", candidate_watchdog),
                        ("previous-demo.plist", old_demo),
                        ("previous-watchdog.plist", old_watchdog)):
        _write_new(plan / name, plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True))
    record = {"schema": 1, "release_sha": sha, "release_source": str(source),
              "previous_sha": previous_sha, "previous_pythonpath": previous_pythonpath,
              "previous_snapshot": str(Path(previous_pythonpath).parent),
              "state_dir": str(STATE), "allowed_labels": list(LABELS),
              "ml1h_signals": str(ml1h_signals), "hours": 0,
              "plist_files": ["candidate-demo.plist", "candidate-watchdog.plist",
                              "previous-demo.plist", "previous-watchdog.plist"]}
    _write_new(plan / "plan.json", (json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
    return record


def _state_status(state: Path) -> str:
    run = state / "run.json"
    if not run.is_file() or run.is_symlink():
        raise ValueError("demo state run.json is missing or unsafe")
    data = json.loads(run.read_text(encoding="utf-8"))
    status = data.get("status") if isinstance(data, dict) else None
    if not isinstance(status, str):
        raise ValueError("demo state has no valid status")
    return status


def apply_two_plists(plists: dict[str, dict], launch_agents: str | Path, *,
                     loaded_fn, bootout_fn, bootstrap_fn) -> None:
    """Replace/start only the demo pair. Injected hooks keep tests service-free."""
    if set(plists) != set(LABELS):
        raise ValueError("refusing any labels beyond demo and demo watchdog")
    launch_agents = Path(launch_agents)
    for label in (WATCHDOG, DEMO):
        if plists[label].get("Label") != label:
            raise ValueError(f"plist label mismatch for {label}")
        path = launch_agents / f"{label}.plist"
        if path.is_symlink():
            raise OSError(f"refusing symlink LaunchAgent plist: {path}")
        if loaded_fn(label):
            bootout_fn(label)
    for label in (DEMO, WATCHDOG):
        path = launch_agents / f"{label}.plist"
        temp = path.with_name(f".{path.name}.new")
        _write_new(temp, plistlib.dumps(plists[label], fmt=plistlib.FMT_XML, sort_keys=True))
        os.replace(temp, path)
    for label in (DEMO, WATCHDOG):
        path = launch_agents / f"{label}.plist"
        bootstrap_fn(label, path)


def _launchctl_hooks():
    domain = f"gui/{os.getuid()}"

    def loaded(label):
        result = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, text=True)
        if result.returncode == 0:
            return True
        if "Could not find service" in (result.stderr or ""):
            return False
        raise RuntimeError(f"cannot determine launchd state for {label}")

    def bootout(label):
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=True)
        deadline = time.monotonic() + 60
        while loaded(label):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"service did not stop: {label}")
            time.sleep(1)

    def bootstrap(label, path):
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)

    return loaded, bootout, bootstrap


def execute_start(plan_dir: str | Path, *, confirmed_flat: bool,
                  launch_agents: str | Path = LAUNCH_AGENTS, state: str | Path = STATE,
                  loaded_fn=None, bootout_fn=None, bootstrap_fn=None) -> Path:
    """Start release after explicit flat/open-orders attestation and completed state."""
    if not confirmed_flat:
        raise ValueError("owner must attest Binance Demo is flat with no open orders")
    state, plan_dir = Path(state).expanduser(), Path(plan_dir).expanduser()
    if _state_status(state) != "completed":
        raise ValueError("only a completed existing demo run may be archived for a fresh nonstop run")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived = state.with_name(f"{state.name}.completed-{stamp}")
    if archived.exists():
        raise FileExistsError(f"refusing to overwrite archived state: {archived}")
    plists = {DEMO: _load(plan_dir / "candidate-demo.plist"),
              WATCHDOG: _load(plan_dir / "candidate-watchdog.plist")}
    signals = Path(json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))["ml1h_signals"])
    if signals.is_symlink() or not signals.is_file():
        raise FileNotFoundError("configured ML1h signals file is no longer available")
    if plists[DEMO].get("EnvironmentVariables", {}).get("QUANTOS_ML1H_SIGNALS") != str(signals):
        raise ValueError("candidate plist ML1h path differs from reviewed plan")
    if loaded_fn is None or bootout_fn is None or bootstrap_fn is None:
        hooks = _launchctl_hooks()
        loaded_fn = loaded_fn or hooks[0]
        bootout_fn = bootout_fn or hooks[1]
        bootstrap_fn = bootstrap_fn or hooks[2]
    # Stop only these two labels before moving their completed state out of the way.
    for label in (WATCHDOG, DEMO):
        if loaded_fn(label):
            bootout_fn(label)
    _require_unlocked_state(state)
    if _state_status(state) != "completed":
        raise ValueError("demo status changed during shutdown; review state before proceeding")
    _require_local_flat(state)
    os.rename(state, archived)
    state.mkdir(mode=0o700)
    (state / "logs").mkdir(mode=0o700)
    apply_two_plists(plists, launch_agents, loaded_fn=lambda _label: False,
                     bootout_fn=bootout_fn, bootstrap_fn=bootstrap_fn)
    return archived


def execute_rollback(plan_dir: str | Path, *, confirmed_flat: bool,
                     launch_agents: str | Path = LAUNCH_AGENTS, state: str | Path = STATE,
                     loaded_fn=None, bootout_fn=None, bootstrap_fn=None) -> None:
    """Restore old snapshot plists and resume the same state without archiving it."""
    if not confirmed_flat:
        raise ValueError("owner must attest Binance Demo is flat with no open orders")
    state, plan_dir = Path(state).expanduser(), Path(plan_dir).expanduser()
    status = _state_status(state)
    if status not in {"running", "stopped"}:
        raise ValueError(f"rollback requires resumable state, found {status}")
    plan = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    signals = Path(plan.get("ml1h_signals", ML1H_SIGNALS)).expanduser()
    if signals.is_symlink() or not signals.is_file():
        raise FileNotFoundError("configured ML1h signals file is no longer available")
    old_source = Path(plan["previous_pythonpath"])
    if not re.fullmatch(r"[0-9a-f]{40}", str(plan.get("previous_sha", ""))):
        raise ValueError("prepared plan has no verified previous full SHA")
    if old_source.parent.name != plan["previous_sha"][:7]:
        raise ValueError("prepared plan previous path does not match its verified SHA")
    if not old_source.is_dir():
        raise FileNotFoundError(f"previous source is missing: {old_source}")
    demo = _load(plan_dir / "previous-demo.plist")
    watchdog = _load(plan_dir / "previous-watchdog.plist")
    demo["ProgramArguments"] = _set_arg(demo["ProgramArguments"], "--hours", "72")
    demo["EnvironmentVariables"] = {**demo.get("EnvironmentVariables", {}),
                                    "PYTHONPATH": str(old_source),
                                    "QUANTOS_ML1H_SIGNALS": str(signals)}
    watchdog["EnvironmentVariables"] = {**watchdog.get("EnvironmentVariables", {}),
                                        "PYTHONPATH": str(old_source)}
    if loaded_fn is None or bootout_fn is None or bootstrap_fn is None:
        hooks = _launchctl_hooks()
        loaded_fn = loaded_fn or hooks[0]
        bootout_fn = bootout_fn or hooks[1]
        bootstrap_fn = bootstrap_fn or hooks[2]
    # Unload only the demo pair before rewriting the old-runtime deadline.
    for label in (WATCHDOG, DEMO):
        if loaded_fn(label):
            bootout_fn(label)
    _require_unlocked_state(state)
    _require_local_flat(state)
    run_path = state / "run.json"
    original = run_path.read_bytes()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = plan_dir / f"rollback-run-before-{stamp}.json"
    _write_new(backup, original)
    run = json.loads(original)
    run["hours"] = 72
    run["deadline_ms"] = int(time.time() * 1000) + 72 * 60 * 60 * 1000
    _atomic_replace_json(run_path, run)
    apply_two_plists({DEMO: demo, WATCHDOG: watchdog}, launch_agents,
                     loaded_fn=lambda _label: False, bootout_fn=bootout_fn, bootstrap_fn=bootstrap_fn)


def _journal_payloads(path: Path) -> list:
    """Read order payloads without writing. A stopped WAL journal has no -shm file, and a
    mode=ro open cannot create one ("unable to open database file"); then nothing is
    writing, so an immutable read of the checkpointed file is exact."""
    uri = path.resolve().as_uri()
    try:
        with sqlite3.connect(uri + "?mode=ro", uri=True) as db:
            return db.execute("SELECT payload FROM orders").fetchall()
    except sqlite3.OperationalError as error:
        if "unable to open" not in str(error) or Path(str(path) + "-wal").exists():
            raise
        with sqlite3.connect(uri + "?immutable=1", uri=True) as db:
            return db.execute("SELECT payload FROM orders").fetchall()


def _require_local_flat(state: Path) -> None:
    """Owner attests exchange flatness; also reject unresolved local orders."""
    journals = list(state.glob("journal_*.db"))
    if not journals:
        raise ValueError("no local journal available to verify flat state")
    for path in journals:
        if path.is_symlink():
            raise ValueError("unsafe journal path")
        for (payload,) in _journal_payloads(path):
            order = json.loads(payload)
            if order.get("state") not in {"CLOSED", "CANCELED", "EXPIRED", "REJECTED"}:
                raise ValueError("local journal has unresolved orders; reconcile before deployment")
    mirror_path = state / "mirror.json"
    if mirror_path.exists():
        book = json.loads(mirror_path.read_text())
        if not isinstance(book, dict) or any(
            not isinstance(row, dict) or row.get("state") not in {"CLOSED", "VOID"}
            for row in book.values()
        ):
            raise ValueError("local exchange mirror is not flat; reconcile before deployment")


def _require_unlocked_state(state: Path) -> None:
    """Fail closed if the engine still owns its state lock; do not create one."""
    import fcntl

    lock_path = state / "engine.lock"
    if not lock_path.exists():
        return
    fd = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not os.path.isfile(lock_path):
            raise OSError("engine lock path is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("demo state is still owned by a running engine") from None
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def _atomic_replace_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.rollback-tmp")
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError(f"refusing preexisting temporary file: {temporary}")
    _write_new(temporary, (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode())
    os.replace(temporary, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--plan-dir", help="new review output for prepare; existing plan for rollback")
    parser.add_argument("--execute", action="store_true", help="owner-only service/state actions")
    parser.add_argument("--confirmed-flat", action="store_true",
                        help="owner attests Binance Demo is flat and has no open orders")
    parser.add_argument("--rollback", action="store_true", help="restore the previous snapshot plists")
    args = parser.parse_args(argv)
    qos = QOS
    snapshots = qos / "app-v4"
    plan_dir = Path(args.plan_dir).expanduser() if args.plan_dir else None
    if args.rollback:
        if not plan_dir:
            parser.error("--rollback requires --plan-dir from the matching release preparation")
        if not args.execute or not args.confirmed_flat:
            parser.error("--rollback requires both --execute and --confirmed-flat")
        execute_rollback(plan_dir, confirmed_flat=True)
        return 0
    if args.execute and not args.confirmed_flat:
        parser.error("--execute requires --confirmed-flat owner attestation")
    repo = Path(args.repo).expanduser().resolve()
    if plan_dir is None:
        sha = _git(repo, "rev-parse", "HEAD")
        plan_dir = qos / "release-plans" / f"demo-nonstop-{sha}"
    if args.execute:
        if not plan_dir.is_dir():
            parser.error("prepare and review the plan before using --execute")
        plan = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
        if plan.get("release_sha") != _git(repo, "rev-parse", "HEAD"):
            parser.error("prepared plan SHA does not match the release checkout")
        record = plan
    else:
        record = prepare(repo, plan_dir=plan_dir, snapshots=snapshots)
    print(json.dumps(record, indent=2, sort_keys=True))
    if args.execute:
        archived = execute_start(plan_dir, confirmed_flat=True)
        print(f"started nonstop demo; completed state archived at {archived}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
