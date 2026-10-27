"""Public-data-only paper CLI; deliberately has no live/testnet order command."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

from .demo import run_demo
from .journal import Journal
from .market import PublicMarket
from .report import write_reports
from .runtime import Runtime
from .strategies import strategy_registry
from .types import dumps


def parser():
    p = argparse.ArgumentParser(prog="quantos", description="Unauthenticated Binance Futures paper engine")
    sub = p.add_subparsers(dest="command", required=True)
    default = Path.home() / "Library" / "Application Support" / "QuantOS" / "v3-paper"
    for name in ("start", "status", "journal", "report", "stop", "install-service"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--state", type=Path, default=default)
        if name in {"start", "install-service"}: cmd.add_argument("--hours", type=float, default=72)
        if name == "start": cmd.add_argument("--mode", choices=["paper"], default="paper")
        if name == "report": cmd.add_argument("--day", type=int, choices=[1, 2, 3])
        if name == "journal":
            cmd.add_argument("--profile", choices=["p1", "p5", "pA"], default="p1")
            cmd.add_argument("--limit", type=int, default=50)
    sub.add_parser("doctor")
    cmd = sub.add_parser("strategies"); cmd.add_argument("action", choices=["list"])
    cmd = sub.add_parser("demo"); cmd.add_argument("--state", required=True, type=Path)
    cmd = sub.add_parser("backtest")
    cmd.add_argument("--input", nargs="+", required=True, type=Path)
    cmd.add_argument("--state", required=True, type=Path)
    cmd.add_argument("--strategy", default="all")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            market = PublicMarket()
            offset = market.server_time()-int(time.time()*1000)
            instruments = market.exchange_info()
            eligible = [s for s in ("BTCUSDC", "ETHUSDC") if s in instruments and instruments[s].status == "TRADING"]
            if abs(offset) > 2000 or not eligible: raise RuntimeError("clock or margin compatibility check failed")
            result = {"mode": "paper", "keys_required": False, "clock_offset_ms": offset,
                      "eligible_symbols": eligible, "python": sys.version.split()[0]}
        elif args.command == "strategies": result = [asdict(s) for s in strategy_registry()]
        elif args.command == "demo":
            report = run_demo(args.state)
            result = {"evidence": "synthetic", "report": str(args.state / "reports" / "final.json"),
                      "scenarios": report["run"]["scenarios"]}
        elif args.command == "backtest":
            from .replay import replay
            result = replay(args.input, args.state, args.strategy)
        elif args.command == "start": return Runtime(args.state, args.hours).run()
        elif args.command == "install-service":
            from .service import install_service
            result = {"plist": str(install_service(Path(sys.executable), args.state, args.hours))}
        elif args.command == "stop":
            if not (args.state / "run.json").exists(): raise ValueError("no paper run in this state directory")
            (args.state / "stop.request").touch()
            result = {"status": "graceful stop requested", "state": str(args.state)}
        else:
            run = json.loads((args.state / "run.json").read_text())
            if args.command == "report":
                result = write_reports(args.state, run, day=args.day)
            elif args.command == "status":
                result = json.loads((args.state / "status.json").read_text())
                result["run"] = run
                result["status_age_seconds"] = (int(time.time()*1000)-result["updated_ms"])/1000
            else:
                if not 1 <= args.limit <= 10000: raise ValueError("limit must be 1..10000")
                j = Journal(args.state / f"journal_{args.profile}.db", args.profile, run["evidence"])
                try:
                    result = [dict(row) for row in j.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (args.limit,))]
                finally: j.close()
        print(dumps(result))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"QuantOS: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
