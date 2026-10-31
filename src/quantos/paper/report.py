"""Local accounting reports. Synthetic and natural journals cannot be combined."""
from collections import Counter
from datetime import datetime, timezone
import csv
import json
from pathlib import Path

from .journal import Journal
from .types import D, dumps


def atomic_json(path: Path, value):
    import os
    import tempfile
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(dumps(value) + "\n")
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def trade_result(journal: Journal, o):
    """Net PnL (fees+funding), filled entry quantity and planned risk of one closed order."""
    fills = journal.db.execute("SELECT * FROM fills WHERE order_id=?", (o["id"],)).fetchall()
    funding_trade = sum((D(r[0]) for r in journal.db.execute("SELECT amount FROM funding WHERE order_id=?", (o["id"],))), D(0))
    net = sum((D(r["pnl"]) - D(r["fee"]) for r in fills), D(0)) + funding_trade
    quantity = sum((D(r["quantity"]) for r in fills if r["kind"] == "ENTRY"), D(0))
    planned_risk = D(o["decision"]["achievable_risk"]) * quantity / D(o["quantity"])
    return net, quantity, planned_risk


def summarize(journal: Journal, start_ms=0, end_ms=2**63-1):
    events = journal.db.execute("SELECT time_ms,kind,payload FROM events WHERE time_ms>=? AND time_ms<? ORDER BY id",
                                (start_ms, end_ms)).fetchall()
    counts = Counter(r["kind"] for r in events)
    rejects, strategies, errors = Counter(), Counter(), []
    active_seconds, max_dd = D(0), D(0)
    seen_signals = set()
    for event in events:
        p = json.loads(event["payload"])
        if event["kind"] == "REJECTED_RISK": rejects[p["reason"]] += 1
        if event["kind"] in {"SIGNAL", "CANDIDATE"} and p["signal_id"] not in seen_signals:
            strategies[p["strategy_id"]] += 1
            seen_signals.add(p["signal_id"])
        if event["kind"] in {"ERROR", "RECONCILIATION_FAILED", "PROTECTION_REJECTED"}:
            errors.append({"time_ms": event["time_ms"], "kind": event["kind"], "details": p})
        if event["kind"] == "OBSERVATION":
            active_seconds += D(str(p["active_seconds"]))
            max_dd = max(max_dd, D(p["drawdown"]))
    rows = journal.db.execute("SELECT * FROM fills WHERE time_ms>=? AND time_ms<?", (start_ms, end_ms)).fetchall()
    fees = sum((D(r["fee"]) for r in rows), D(0))
    gross = sum((D(r["pnl"]) for r in rows), D(0))
    funding = sum((D(r[0]) for r in journal.db.execute("SELECT amount FROM funding WHERE time_ms>=? AND time_ms<?", (start_ms, end_ms))), D(0))
    trades = []
    for o in journal.orders():
        if o["state"] != "CLOSED" or not start_ms <= o["closed_ms"] < end_ms:
            continue
        net, quantity, planned_risk = trade_result(journal, o)
        trades.append({"order_id": o["id"], "strategy": o["signal"]["strategy_id"],
                       "net_pnl": str(net), "R": str(net / planned_risk) if planned_risk else None,
                       "reason": o["close_reason"], "filled_quantity": str(quantity),
                       "requested_risk": o["decision"]["requested_risk"],
                       "achievable_risk": str(planned_risk),
                       "binding_constraint": o["decision"]["binding_constraint"],
                       "notional": str(quantity * D(o["average"])),
                       "margin": str(quantity * D(o["average"]) / D(o["decision"]["leverage"]))})
    return {"identity": journal.get("identity"), "cash": str(journal.cash()),
            "equity_last_observed": journal.get("equity_last_observed"),
            "counts": dict(counts), "signals_by_strategy": dict(strategies),
            "rejects_by_reason": dict(rejects), "gross_pnl": str(gross), "fees": str(fees),
            "funding": str(funding), "net_cash_change": str(gross-fees+funding),
            "measured_active_seconds": str(active_seconds), "max_equity_drawdown": str(max_dd),
            "closed_trades": trades, "errors": errors,
            "open_orders": [o for o in journal.orders() if o["state"] not in {"CLOSED", "CANCELED", "REJECTED", "EXPIRED"}],
            "paused": journal.get("paused", False)}


def write_reports(state: Path, run: dict, day: int | None = None, final=False):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = run["started_ms"] + (day-1) * 86400000 if day else 0
    end = min(now+1, run["started_ms"] + day * 86400000) if day else now+1
    result = {"run": run, "generated_ms": now,
              "wall_seconds": max(0, (min(now, run.get("finished_ms", now))-run["started_ms"])/1000),
              "day": day, "final": final,
              "assumptions": ["Simulated, not exchange fills; reference rules have no verified edge.",
                  "LIMIT: strict trade-through, 1% participation after one order-size queue allowance.",
                  "MARKET/STOP: later quote, 500ms minimum latency, adverse size-dependent impact.",
                  "Funding uses published historical timestamps; offline periods can hide price excursions.",
                  "p5 is a 5% risk ceiling; 1x exposure can bind earlier.",
                  "pA sizes from p1's measured edge in R (quarter Kelly, capped 5%, probe 0.25% for the first 30 trades).",
                  "R divides net trade PnL by cost-inclusive approved risk of the actually filled size."],
              "profiles": {}}
    profiles = run.get("profiles", ["p1", "p5", "pA"])
    if profiles not in (["p1", "p5", "pA"], ["pR"], ["pL"]):
        raise ValueError("invalid report profile set")
    if profiles == ["pL"]:
        result["assumptions"] = [x for x in result["assumptions"] if not x.startswith(("p5 ", "pA "))]
        result["assumptions"].append("pL: $50 USDC LivePolicy, reserved event slots, min-notional bump <=3% risk.")
    if profiles == ["pR"]:
        result["assumptions"] = [x for x in result["assumptions"] if not x.startswith(("p5 ", "pA "))]
        result["assumptions"].append("pR is isolated USDT research; no candidate is promoted by replay success.")
    for profile in profiles:
        if profile == "pA" and not (state / "journal_pA.db").is_file():
            continue  # runs started before the adaptive profile existed
        if not (state / f"journal_{profile}.db").is_file():
            raise ValueError(f"missing {profile} journal; refusing to invent account state")
        j = Journal(state / f"journal_{profile}.db", profile, run["evidence"])
        try: result["profiles"][profile] = summarize(j, start, end)
        finally: j.close()
    folder = state / "reports"; folder.mkdir(exist_ok=True)
    name = "final" if final else f"day-{day}" if day else "latest"
    atomic_json(folder / f"{name}.json", result)
    lines = [f"# QuantOS paper report: {name}", "", f"Evidence: {run['evidence']}; status: {run['status']}",
             f"Wall observation: {result['wall_seconds']/3600:.2f} hours", "",
             "| Profile | Cash | Net cash change | Fees | Funding | Closed trades | Active hours | Max DD |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for profile, p in result["profiles"].items():
        lines.append(f"| {profile} | {p['cash']} | {p['net_cash_change']} | {p['fees']} | {p['funding']} | {len(p['closed_trades'])} | {D(p['measured_active_seconds'])/3600:.2f} | {D(p['max_equity_drawdown']):.2%} |")
        lines += ["", f"{profile} signals: {p['signals_by_strategy']}; decisions: {p['counts']}",
                  f"Rejections: {p['rejects_by_reason']}; errors: {len(p['errors'])}; paused: {p['paused']}"]
    lines += ["", *result["assumptions"], "", "One-year risk tails: NOT_ESTIMABLE from a 72-hour reference run."]
    (folder / f"{name}.md").write_text("\n".join(lines) + "\n")
    with (folder / f"{name}-trades.csv").open("w", newline="") as f:
        rows = [{"profile": profile, **trade} for profile, p in result["profiles"].items() for trade in p["closed_trades"]]
        fields = list(rows[0]) if rows else ["profile", "order_id", "net_pnl", "R"]
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    return result
