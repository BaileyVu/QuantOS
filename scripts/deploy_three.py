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
        w = build_watchdog_plist(py, state, state / "logs", pythonpath=app)
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
    for label in ("com.quantos.v4.paper", "com.quantos.v4.demo"):  # staggered: no simultaneous cold start
        for name in (label, label + ".watchdog"):
            subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{name}"], check=False)
            while loaded(name): time.sleep(1)
            path = AGENTS / f"{name}.plist"
            with path.open("wb") as f:
                plistlib.dump(plan[name], f, sort_keys=True)
            subprocess.run(["launchctl", "bootstrap", DOMAIN, str(path)], check=True)
            print("restarted", name)
        if label.endswith("paper"): time.sleep(90)


def uninstall():
    for label in ("com.quantos.v4.paper", "com.quantos.v4.demo", "com.quantos.v4.paper.watchdog",
                  "com.quantos.v4.demo.watchdog", "com.sentibot.spot", "com.sentibot.perp"):
        if loaded(label):
            subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{label}"], check=False)
            print("stopped", label)


if __name__ == "__main__":
    {"install": install, "uninstall": uninstall, "restart-quantos": restart_quantos}[sys.argv[1]]()
