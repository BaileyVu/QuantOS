"""Prepare, deploy, or roll back the owner-operated, flat-only Binance Demo cutover."""
import argparse
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from tempfile import NamedTemporaryFile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.demo_nonstop import (
    DEMO, LAUNCH_AGENTS, QOS, STATE, WATCHDOG, _git, _launchctl_hooks, _load,
    _require_local_flat, _require_unlocked_state, _state_from_args, _state_status, _write_new,
)
from scripts.deploy_three import clean_snapshot

LEGACY = "com.quantos.mlalts"
SCORER = "com.quantos.ml5m.scorer"
DASHBOARD = "com.quantos.dashboard"
OLD_APP = Path.home() / "Library/Application Support/mlalts/app"
SCORER_STATE = QOS / "ml5m-scoring"
MODEL = OLD_APP / "model_long20.pkl"
BEFORE = (DASHBOARD, WATCHDOG, DEMO, LEGACY)
AFTER = (SCORER, DASHBOARD, WATCHDOG, DEMO)


class DemoNotFlat(ValueError):
    """A read-only account check found exchange exposure."""


def plist_path(label):
    return LAUNCH_AGENTS / f"{label}.plist"


def demo_env(plist):
    env = plist["EnvironmentVariables"]
    if env.get("QUANTOS_TESTNET_HOST") != "demo" or not env.get("QUANTOS_KEY_FILE"):
        raise ValueError("both executors must use explicitly configured Binance Demo credentials")
    if any(key != "QUANTOS_KEY_FILE" and any(word in key.lower() for word in ("key", "token", "secret"))
           for key in env):
        raise ValueError("refusing executor plist with inline credentials")
    return env


def with_source(plist, source, **env):
    return {**plist, "EnvironmentVariables": {**plist.get("EnvironmentVariables", {}),
                                               "PYTHONPATH": str(source), **env}}


def prepare(plan, repo, snapshots):
    if plan.exists() or LAUNCH_AGENTS.resolve() in (plan, *plan.parents) or STATE.resolve() in (plan, *plan.parents):
        raise ValueError("prepare requires a new plan directory outside LaunchAgents and demo state")
    previous = {label: _load(plist_path(label)) for label in BEFORE}
    if any(plist.get("Label") != label for label, plist in previous.items()):
        raise ValueError("installed LaunchAgent labels differ from their file names")
    if plist_path(SCORER).exists() or plist_path(SCORER).is_symlink():
        raise ValueError("scorer plist is already installed")
    env = demo_env(previous[DEMO])
    demo_env(previous[LEGACY])
    _state_from_args(previous[DEMO])
    args = previous[DEMO]["ProgramArguments"]
    if (args[args.index("--execution") + 1] != "testnet" or env.get("QUANTOS_DEMO_PROFILE") != "pD"
            or args[args.index("--hours") + 1] != "0"):
        raise ValueError("expected a nonstop pD Binance Demo service")
    old_source = Path(env["PYTHONPATH"])
    previous_sha = _git(old_source.parent, "rev-parse", "HEAD")
    scorer_python = previous[LEGACY]["ProgramArguments"][0]
    if not MODEL.is_file() or not os.access(scorer_python, os.X_OK):
        raise ValueError("trusted alt-ML model and legacy Python runtime are required")
    source = clean_snapshot(repo, snapshots)
    release_sha = _git(source.parent, "rev-parse", "HEAD")
    candidate = {label: with_source(previous[label], source) for label in (DEMO, WATCHDOG, DASHBOARD)}
    candidate[DEMO] = with_source(previous[DEMO], source, QUANTOS_ML5M_SIGNALS=str(SCORER_STATE / "signals.json"))
    candidate[SCORER] = {
        "Label": SCORER, "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProgramArguments": [scorer_python, "-m", "quantos.paper.ml5m_scoring",
                             "--model", str(MODEL), "--state", str(SCORER_STATE)],
        "WorkingDirectory": str(SCORER_STATE), "EnvironmentVariables": {"PYTHONPATH": str(source)},
        "StandardOutPath": str(SCORER_STATE / "out.log"), "StandardErrorPath": str(SCORER_STATE / "err.log"),
    }
    plan.mkdir(parents=True, mode=0o700)
    for prefix, plists in (("previous", previous), ("candidate", candidate)):
        for label, plist in plists.items():
            _write_new(plan / f"{prefix}-{label}.plist", plistlib.dumps(plist))
    metadata = {"release_sha": release_sha, "previous_sha": previous_sha,
                "release_source": str(source), "previous_source": str(old_source),
                "model_sha256": hashlib.sha256(MODEL.read_bytes()).hexdigest()}
    _write_new(plan / "plan.json", json.dumps(metadata, indent=2).encode())
    return metadata


def require_flat(client):
    positions = client.position_risk()
    orders = client.open_orders()
    algo_orders = client.open_algo_orders()
    if not all(isinstance(data, list) for data in (positions, orders, algo_orders)):
        raise ValueError("unexpected Binance Demo account response")
    if orders or algo_orders or any(Decimal(position["positionAmt"]) != 0 for position in positions):
        raise DemoNotFlat("Binance Demo has positions or open orders")


def check_exchange(plists):
    from quantos.live.client import SignedClient, TESTNET_HOSTS
    from quantos.live.keys import load_keys

    os.environ["QUANTOS_TESTNET_HOST"] = "demo"
    paths = {demo_env(plists[label])["QUANTOS_KEY_FILE"] for label in (DEMO, LEGACY)}
    for path in sorted(paths):
        try:
            client = SignedClient("testnet", load_keys(path))
            if client.base != TESTNET_HOSTS["demo"]:
                raise ValueError("unexpected Demo host")
            require_flat(client)
        except DemoNotFlat:
            raise
        except Exception:
            raise RuntimeError("could not verify Binance Demo flatness") from None


def install(label, plist):
    path = plist_path(label)
    if path.is_symlink():
        raise ValueError(f"unsafe LaunchAgent path for {label}")
    if plist is None:
        path.unlink(missing_ok=True)
        return
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            plistlib.dump(plist, file)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def seed_scorer():
    SCORER_STATE.mkdir(mode=0o700, exist_ok=True)
    if SCORER_STATE.is_symlink():
        raise ValueError("unsafe scorer state directory")
    history = SCORER_STATE / "score_hist.json"
    if not history.exists():
        data = (OLD_APP / "score_hist.json").read_bytes()
        if not isinstance(json.loads(data), dict):
            raise ValueError("invalid legacy score history")
        _write_new(history, data)


def prime_scorer(plist):
    # Supply only the candidate source environment. The scorer needs no credentials.
    command = [*plist["ProgramArguments"], "--once"]
    subprocess.run(command, check=True, env=plist["EnvironmentVariables"],
                   cwd=SCORER_STATE, timeout=300, capture_output=True)
    if not (SCORER_STATE / "signals.json").is_file():
        raise ValueError("scorer did not publish an initial signal file")


def switch(plan, rollback, *, hooks=None, exchange_check=check_exchange):
    before = {label: _load(plan / f"previous-{label}.plist") for label in BEFORE}
    after = {label: _load(plan / f"candidate-{label}.plist") for label in AFTER}
    expected, target = (after, before) if rollback else (before, after)
    restore_order, start_order = (AFTER, BEFORE) if rollback else (BEFORE, AFTER)
    labels = (*BEFORE, SCORER)
    for label in labels:
        path = plist_path(label)
        actual = _load(path) if path.exists() or path.is_symlink() else None
        if actual != expected.get(label) or (label in target and target[label].get("Label") != label):
            raise ValueError(f"installed or prepared LaunchAgent differs from the plan for {label}")
    metadata = json.loads((plan / "plan.json").read_bytes())
    prefix = "previous" if rollback else "release"
    source = Path(metadata[f"{prefix}_source"])
    if (target[DEMO]["EnvironmentVariables"]["PYTHONPATH"] != str(source)
            or _git(source.parent, "rev-parse", "HEAD") != metadata[f"{prefix}_sha"]
            or _git(source.parent, "status", "--porcelain", "--untracked-files=all")):
        raise ValueError("target release snapshot differs from the prepared plan")
    if not rollback and hashlib.sha256(MODEL.read_bytes()).hexdigest() != metadata["model_sha256"]:
        raise ValueError("trusted alt-ML model changed after preparation")
    if _state_status(STATE) not in {"running", "suspended", "stopped"}:
        raise ValueError("pD state must be resumable without resetting the book")
    _require_local_flat(STATE)
    exchange_check(before)

    loaded, bootout, bootstrap = hooks or _launchctl_hooks()
    was_loaded = {label for label in labels if loaded(label)}
    if was_loaded - set(expected):
        raise ValueError("unexpected parallel scorer or executor is loaded")
    stop_order = (LEGACY, SCORER, DEMO, WATCHDOG, DASHBOARD)
    changed = False
    try:
        for label in stop_order:
            if label in was_loaded:
                bootout(label)
        _require_unlocked_state(STATE)
        _require_local_flat(STATE)
        exchange_check(before)
        legacy_positions = json.loads((OLD_APP / "exec_state_testnet.json").read_bytes())
        if legacy_positions != []:
            raise ValueError("legacy executor still has unresolved local positions")
        if not rollback:
            seed_scorer()
            prime_scorer(target[SCORER])
        changed = True
        for label in labels:
            install(label, target.get(label))
        # No order-capable service has started. Recheck right before starting the new set.
        exchange_check(before)
    except Exception:
        if changed:
            for label in labels:
                install(label, expected.get(label))
        for label in restore_order:
            if label in was_loaded and not loaded(label):
                bootstrap(label, plist_path(label))
        raise

    # Once activation starts, a new order may appear. Leave any started
    # services supervising their orders and require a fresh flat-only switch.
    for label in start_order:
        try:
            bootstrap(label, plist_path(label))
        except Exception:
            raise RuntimeError(f"prepared configuration installed but {label} failed to start") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "deploy", "rollback"))
    parser.add_argument("--plan-dir", required=True, type=Path)
    parser.add_argument("--snapshots", type=Path, default=QOS / "app-v4")
    args = parser.parse_args()
    plan = args.plan_dir.expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if args.action == "prepare":
        print(json.dumps(prepare(plan, repo, args.snapshots), indent=2))
    else:
        sys.path.insert(0, str(repo / "src"))
        with (QOS / "ml5m-cutover.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            switch(plan, args.action == "rollback")
        print(f"{args.action} completed")


if __name__ == "__main__":
    main()
