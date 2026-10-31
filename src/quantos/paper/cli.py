"""Public-data-only paper CLI; deliberately has no live/testnet order command."""
import argparse
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import sys
import time
import tomllib

from .adaptive import AdaptiveSizer
from .demo import run_demo
from .journal import Journal
from .market import PublicMarket
from .report import write_reports
from .runtime import Runtime
from .strategies import library
from .strategies.manifest import manifest_for
from .types import D, dumps


def parser():
    p = argparse.ArgumentParser(prog="quantos", description="Unauthenticated Binance Futures paper engine")
    sub = p.add_subparsers(dest="command", required=True)
    default = Path.home() / "Library" / "Application Support" / "QuantOS" / "v4-paper"
    for name in ("start", "status", "journal", "report", "stop", "install-service", "exits"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--state", type=Path, default=default)
        if name in {"start", "install-service"}: cmd.add_argument("--hours", type=float, default=72)
        if name in {"start", "install-service"}:
            cmd.add_argument("--min-quote-volume", type=_quote_volume, default=D("20000000"))
        if name == "start": cmd.add_argument("--mode", choices=["paper"], default="paper")
        if name in {"start", "install-service"}:
            cmd.add_argument("--portfolio", choices=["legacy", "live50"], default="legacy")
            cmd.add_argument("--sentiment-file", type=Path, help="sentibot paper_perp_signals.jsonl (shadow only)")
        if name == "install-service":
            cmd.add_argument("--pythonpath", type=Path, help="copied source dir outside ~/Desktop")
            cmd.add_argument("--watchdog", action="store_true")
        if name == "report": cmd.add_argument("--day", type=int, choices=[1, 2, 3])
        if name == "exits": cmd.add_argument("--profile", choices=["p1", "p5", "pA", "pL"], default="p1")
        if name == "journal":
            cmd.add_argument("--profile", choices=["p1", "p5", "pA", "pL"], default="p1")
            cmd.add_argument("--limit", type=int, default=50)
    sub.add_parser("doctor")
    cmd = sub.add_parser("strategies"); cmd.add_argument("action", choices=["list"])
    cmd.add_argument("--state", type=Path, default=default)
    cmd.add_argument("--include-candidates", action="store_true")
    cmd = sub.add_parser("demo"); cmd.add_argument("--state", required=True, type=Path)
    cmd = sub.add_parser("backtest")
    cmd.add_argument("--research", action="store_true", help="Use isolated USDT pR candidate replay")
    cmd.add_argument("--input", nargs="+", required=True, type=Path)
    cmd.add_argument("--state", required=True, type=Path)
    selection = cmd.add_mutually_exclusive_group()
    selection.add_argument("--strategy", default="all")
    selection.add_argument("--config", type=Path)
    return p


def _quote_volume(value: str) -> Decimal:
    try:
        parsed = D(value)
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError("quote volume must be a decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise argparse.ArgumentTypeError("quote volume must be finite and non-negative")
    return parsed


class _ReadOnlyMeta:
    def __init__(self, db=None):
        self.db = db

    def get(self, key, default=None):
        if self.db is None:
            return default
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])


def _read_pa_profile(state: Path, strategy_id: str) -> dict:
    """Read pA sizing state with SQLite read-only mode; never initializes or writes a journal."""
    path = state / "journal_pA.db"
    db = None
    try:
        if path.exists():
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
            db.row_factory = sqlite3.Row
            journal = _ReadOnlyMeta(db)
            identity = journal.get("identity")
            if not isinstance(identity, dict) or identity.get("profile") != "pA":
                raise ValueError("pA journal identity mismatch")
            equity = D("200")
            for row in db.execute("SELECT fee,pnl FROM fills"):
                equity += D(row["pnl"]) - D(row["fee"])
            for row in db.execute("SELECT amount FROM funding"):
                equity += D(row[0])
            for (payload,) in db.execute("SELECT payload FROM orders ORDER BY rowid"):
                order = json.loads(payload)
                if order.get("state") not in {"CLOSED", "CANCELED", "EXPIRED", "REJECTED"}:
                    filled = D(order.get("filled", "0"))
                    if filled > 0:
                        equity += filled * (D(order["last_mark"]) - D(order["average"])) * D(order["direction"])
            high_water = max(D(journal.get("high_water", "200")), equity)
            sizer = AdaptiveSizer(journal, journal)
            risk, detail = sizer.risk_fraction(strategy_id, equity, high_water)
            if journal.get("paused", False):
                risk = D(0)
            observed = detail["n"] > 0
            source = "persisted" if observed else "persisted_no_observations"
        else:
            journal = _ReadOnlyMeta()
            sizer = AdaptiveSizer(journal, journal)
            risk, detail = sizer.risk_fraction(strategy_id, D("200"), D("200"))
            observed = False
            source = "default_probe_no_observations"
        percent = risk * D(100)
        return {
            "state": detail["state"], "n": detail["n"], "mu_R": detail["mu_R"],
            "risk_fraction": risk, "risk_percent": percent,
            "observations": "available" if observed else "none",
            "source": source, "sizing_mode": detail["mode"],
        }
    except sqlite3.Error:
        raise ValueError("unable to read pA journal safely") from None
    finally:
        if db is not None:
            db.close()


def _strategy_listing(state: Path, include_candidates=False) -> list[dict]:
    rows = []
    for strategy in library(include_candidates):
        manifest = manifest_for(strategy.strategy_id, strategy.version)
        if manifest is None:
            raise ValueError(f"strategy has no preregistered manifest: {strategy.strategy_id}")
        rows.append({
            "manifest": asdict(manifest),
            "evidence_status": manifest.evidence_status,
            "pA": ({"state": "RESEARCH_ONLY", "risk_fraction": D(0), "risk_percent": D(0)}
                   if manifest.status == "candidate" else _read_pa_profile(state, strategy.strategy_id)),
        })
    return rows


def _strategy_from_config(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        raise ValueError("invalid backtest config") from None
    if set(config) != {"strategy", "parameters"}:
        raise ValueError("backtest config must contain only [strategy] and [parameters]")
    strategy = config["strategy"]
    if not isinstance(strategy, dict) or set(strategy) != {"id", "version"}:
        raise ValueError("invalid strategy declaration in backtest config")
    if not isinstance(strategy["id"], str) or not isinstance(strategy["version"], str):
        raise ValueError("strategy id and version must be strings")
    manifest = manifest_for(strategy["id"], strategy["version"])
    if manifest is None:
        raise ValueError("unknown strategy or version in backtest config")
    parameters = config["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {key for key, _ in manifest.parameters}:
        raise ValueError("backtest parameters must exactly match the preregistered manifest")
    for key, expected in manifest.parameters:
        value = parameters[key]
        if _parameter_string(value) != expected:
            raise ValueError(f"parameter {key} differs from the preregistered manifest")
    return manifest.strategy_id


def _parameter_string(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float, Decimal)):
        return str(value)
    return ""


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
        elif args.command == "strategies": result = _strategy_listing(args.state, args.include_candidates)
        elif args.command == "demo":
            report = run_demo(args.state)
            result = {"evidence": "synthetic", "report": str(args.state / "reports" / "final.json"),
                      "scenarios": report["run"]["scenarios"], "library": report["run"]["library"]}
        elif args.command == "backtest":
            from .replay import replay
            selected_strategy = _strategy_from_config(args.config) if args.config else args.strategy
            result = replay(args.input, args.state, selected_strategy,
                            **({"research": True} if args.research else {}))
        elif args.command == "start": return Runtime(
            args.state, args.hours, min_quote_volume=args.min_quote_volume,
            portfolio=args.portfolio, sentiment_path=args.sentiment_file).run()
        elif args.command == "install-service":
            from .service import install_service, install_watchdog
            extra = ["--portfolio", args.portfolio] + (
                ["--sentiment-file", str(args.sentiment_file)] if args.sentiment_file else [])
            result = {"plist": str(install_service(
                Path(sys.executable), args.state, args.hours,
                min_quote_volume=args.min_quote_volume, extra_args=extra, pythonpath=args.pythonpath))}
            if args.watchdog:
                result["watchdog"] = str(install_watchdog(Path(sys.executable), args.state, args.pythonpath))
        elif args.command == "stop":
            if not (args.state / "run.json").exists(): raise ValueError("no paper run in this state directory")
            (args.state / "stop.request").touch()
            result = {"status": "graceful stop requested", "state": str(args.state)}
        else:
            run = json.loads((args.state / "run.json").read_text())
            if args.command == "exits":
                from .exits import choose_exit, trades_from_orders
                j = Journal(args.state / f"journal_{args.profile}.db", args.profile, run["evidence"])
                try: policy, result = choose_exit(trades_from_orders(j.orders()))
                finally: j.close()
                result = {**result, "recommended": asdict(policy)}
            elif args.command == "report":
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
