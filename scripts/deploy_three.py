"""Install the three launchd runs: QuantOS paper, QuantOS demo-money mirror, Sentibot (spot + perp).

Usage: ../QuantOS/.venv/bin/python scripts/deploy_three.py install|uninstall
Everything runs from ~/Library/Application Support (launchd jobs hit macOS privacy blocks under ~/Desktop).
Mainnet is never configured here. The demo run uses Binance Demo futures (fake funds).
"""
import os
from pathlib import Path
import plistlib
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from quantos.paper.service import build_plist, build_watchdog_plist  # noqa: E402

BASE = Path.home() / "Library" / "Application Support"
QOS, SB = BASE / "QuantOS", BASE / "sentibot"
AGENTS = Path.home() / "Library" / "LaunchAgents"
SENTIMENT = SB / "app" / "paper_perp_signals.jsonl"
KEY = QOS / "keys" / "binance-futures-testnet.env"
DOMAIN = f"gui/{os.getuid()}"


def python():
    out = subprocess.run([str(REPO.parent / "QuantOS" / ".venv" / "bin" / "python"), "-c",
                          "import os,sys;print(os.path.realpath(sys.executable))"], capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def jobs(resume=False):
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    app = QOS / "app-v4" / commit / "src"
    app.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", "--exclude", "__pycache__", f"{REPO / 'src'}/", f"{app}/"], check=True)
    py, out = python(), {}
    for name, execution, env in (("paper", "paper", {}),
                                 ("demo", "testnet", {"QUANTOS_KEY_FILE": str(KEY), "QUANTOS_TESTNET_HOST": "demo"})):
        state = QOS / ("v4-live50" if name == "paper" else "v4-live50-demo")
        if (state / "run.json").exists() != resume:
            raise SystemExit(f"{state}: {'no run to resume' if resume else 'already has a run; use a fresh folder'}")
        (state / "logs").mkdir(parents=True, exist_ok=True)
        state.chmod(0o700)
        p = build_plist(py, state, state / "logs", 72, min_quote_volume="5000000", pythonpath=app,
                        extra_args=["--portfolio", "live50", "--execution", execution, "--sentiment-file", str(SENTIMENT)])
        p["Label"] = f"com.quantos.v4.{name}"
        p["EnvironmentVariables"].update(env)
        out[p["Label"]] = p
        w = build_watchdog_plist(py, state, state / "logs", pythonpath=app, execution=execution)
        w["EnvironmentVariables"].update(env)
        w["Label"] = f"com.quantos.v4.{name}.watchdog"
        out[w["Label"]] = w
    (SB / "logs").mkdir(parents=True, exist_ok=True)
    for kind, args in (("spot", []), ("perp", ["--perp"])):
        out[f"com.sentibot.{kind}"] = {
            "Label": f"com.sentibot.{kind}",
            "ProgramArguments": ["/usr/bin/python3", "paper.py", "--loop", *args],
            "WorkingDirectory": str(SB / "app"), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 60,
            "StandardOutPath": str(SB / "logs" / f"{kind}.log"), "StandardErrorPath": str(SB / "logs" / f"{kind}.err.log")}
    return out


def loaded(label):
    return subprocess.run(["launchctl", "print", f"{DOMAIN}/{label}"], capture_output=True).returncode == 0


def install():
    if not KEY.is_file() or KEY.stat().st_mode & 0o077:
        raise SystemExit("demo key copy missing or not mode 600")
    plan = jobs()
    busy = [label for label in plan if loaded(label)]
    if busy:
        raise SystemExit(f"already loaded: {busy}")
    for label, plist in plan.items():
        path = AGENTS / f"{label}.plist"
        with path.open("wb") as f:
            plistlib.dump(plist, f, sort_keys=True)
        subprocess.run(["launchctl", "bootstrap", DOMAIN, str(path)], check=True)
        print("started", label)


def restart_quantos():
    """Deploy the current commit to both QuantOS runs; each resumes its run.json (same deadline)."""
    import time
    plan = {k: v for k, v in jobs(resume=True).items() if k.startswith("com.quantos.v4.")}
    only = sys.argv[2:] or ["paper", "demo"]
    for label in [f"com.quantos.v4.{n}" for n in only]:  # staggered: no simultaneous cold start
        for name in (label, label + ".watchdog"):
            subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{name}"], check=False)
            while loaded(name): time.sleep(1)
            path = AGENTS / f"{name}.plist"
            with path.open("wb") as f:
                plistlib.dump(plan[name], f, sort_keys=True)
            subprocess.run(["launchctl", "bootstrap", DOMAIN, str(path)], check=True)
            print("restarted", name)
        if label.endswith("paper") and len(only) > 1: time.sleep(90)


def install_dashboard(runs=None, exchange=None):
    """Local read-only dashboard on http://127.0.0.1:8787 (launchd com.quantos.dashboard)."""
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    app = QOS / "app-v4" / commit / "src"
    app.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", "--exclude", "__pycache__", f"{REPO / 'src'}/", f"{app}/"], check=True)
    label = "com.quantos.dashboard"
    if loaded(label):
        subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{label}"], check=False)
        while loaded(label): __import__("time").sleep(1)
    (QOS / "dashboard-logs").mkdir(exist_ok=True)
    plist = {"Label": label, "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
             "ProgramArguments": [str(python()), "-m", "quantos.paper.webdash",
                                  *(runs or ["--run", f"paper 2%={QOS / 'v4-live50'}#pL", "--run", f"paper 5%={QOS / 'v4-live50'}#pL5",
                                             "--run", f"paper $200={QOS / 'v4-live50'}#pM10",
                                             "--run", f"paper $200 m/5={QOS / 'v4-live50'}#pM5",
                                             "--run", f"demo 2%={QOS / 'v4-live50-demo'}#pL"]),
                                  "--sentibot", str(SB / "app"), "--port", "8787",
                                  *(["--exchange", exchange] if exchange else [])],
             "EnvironmentVariables": {"PYTHONPATH": str(app)}, "WorkingDirectory": str(QOS),
             "StandardOutPath": str(QOS / "dashboard-logs" / "out.log"),
             "StandardErrorPath": str(QOS / "dashboard-logs" / "err.log")}
    path = AGENTS / f"{label}.plist"
    with path.open("wb") as f:
        plistlib.dump(plist, f, sort_keys=True)
    subprocess.run(["launchctl", "bootstrap", DOMAIN, str(path)], check=True)
    print("started", label, "http://localhost:8787")


def uninstall():
    for label in ("com.quantos.v4.paper", "com.quantos.v4.demo", "com.quantos.v4.paper.watchdog",
                  "com.quantos.v4.demo.watchdog", "com.sentibot.spot", "com.sentibot.perp", "com.quantos.dashboard",
                  "com.quantos.hlcopy", "com.quantos.v4.demo200", "com.quantos.v4.demo200.watchdog",
                  "com.quantos.hlcopy.reverse", "com.quantos.mlalts"):
        if loaded(label):
            subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{label}"], check=False)
            print("stopped", label)


def install_demo200(hours=0):
    """Owner's combined $200 demo: QuantOS pD on Binance Demo + alt-ML executor (shares the 3-position cap via
    status.json) + sentibot (sentiment gate input). hours=0 (default since 2026-11-07) runs until
    `quantos stop`; `quantos pause` / `resume` hold new entries. Fresh state folder; no mainnet. HL copy was
    dropped (2026-11-03) and is no longer started."""
    import time
    if not KEY.is_file() or KEY.stat().st_mode & 0o077:
        raise SystemExit("demo key copy missing or not mode 600")
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    app = QOS / "app-v4" / commit / "src"
    app.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", "--exclude", "__pycache__", f"{REPO / 'src'}/", f"{app}/"], check=True)
    state, py, plan = QOS / "v4-demo200", python(), {}
    if (state / "run.json").exists(): raise SystemExit(f"{state} already has a run; use a fresh folder")
    (state / "logs").mkdir(parents=True, exist_ok=True); state.chmod(0o700)
    env = {"QUANTOS_KEY_FILE": str(KEY), "QUANTOS_TESTNET_HOST": "demo", "QUANTOS_DEMO_PROFILE": "pD"}
    p = build_plist(py, state, state / "logs", hours, min_quote_volume="5000000", pythonpath=app,
                    extra_args=["--portfolio", "live50", "--execution", "testnet", "--sentiment-file", str(SENTIMENT)])
    p["Label"] = "com.quantos.v4.demo200"; p["EnvironmentVariables"].update(env); plan[p["Label"]] = p
    w = build_watchdog_plist(py, state, state / "logs", pythonpath=app, execution="testnet")
    w["EnvironmentVariables"].update(env)
    w["Label"] = "com.quantos.v4.demo200.watchdog"; plan[w["Label"]] = w
    for kind, args in (("spot", []), ("perp", ["--perp"])):
        (SB / "logs").mkdir(parents=True, exist_ok=True)
        plan[f"com.sentibot.{kind}"] = {
            "Label": f"com.sentibot.{kind}", "ProgramArguments": ["/usr/bin/python3", "paper.py", "--loop", *args],
            "WorkingDirectory": str(SB / "app"), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 60,
            "StandardOutPath": str(SB / "logs" / f"{kind}.log"), "StandardErrorPath": str(SB / "logs" / f"{kind}.err.log")}
    ml = BASE / "mlalts"
    plan["com.quantos.mlalts"] = {
        "Label": "com.quantos.mlalts", "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 30,
        "ProgramArguments": [str(ml / "venv" / "bin" / "python"), str(ml / "app" / "demo_exec.py"), "testnet"],
        "WorkingDirectory": str(ml / "app"),
        "EnvironmentVariables": {**env, "PYTHONPATH": str(app), "QOS_STATUS": str(state / "status.json"),
                                 "ML_VIRTUAL_BALANCE": "200", "ML_LEV": "5",
                                 **({"ML_DEADLINE_MS": str(int(time.time() * 1000) + hours * 3600000)} if hours else {})},
        "StandardOutPath": str(ml / "logs" / "out.log"), "StandardErrorPath": str(ml / "logs" / "err.log")}
    busy = [label for label in plan if loaded(label)]
    if busy: raise SystemExit(f"already loaded: {busy}")
    for label, plist in plan.items():
        path = AGENTS / f"{label}.plist"
        with path.open("wb") as f: plistlib.dump(plist, f, sort_keys=True)
        subprocess.run(["launchctl", "bootstrap", DOMAIN, str(path)], check=True); print("started", label)
    install_dashboard(runs=["--run", f"demo $200={state}#pD"], exchange=f"demo={KEY}")


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall, "restart-quantos": restart_quantos,
     "install-dashboard": install_dashboard, "install-demo200": install_demo200}[sys.argv[1]]()
