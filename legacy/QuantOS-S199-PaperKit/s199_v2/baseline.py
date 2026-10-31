from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
PAIR = re.compile(r"(\w+)=([^\s]+)")


def parse_evaluations(log_path: Path | str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_no, raw in enumerate(Path(log_path).read_text(errors="replace").splitlines(), 1):
        line = ANSI.sub("", raw)
        match = re.search(r"SANDBOX_EVAL_(SIGNAL|SKIP)\s+(.*)", line)
        if not match:
            continue
        row: dict[str, object] = {"kind": match.group(1).lower(), "line": line_no}
        iso = re.search(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)", line)
        if iso:
            row["observed_utc"] = iso.group(1)
        for key, value in PAIR.findall(match.group(2)):
            if key in {"seconds_left"}:
                row[key] = int(value)
            elif key not in {"slug", "reason", "selected"}:
                try:
                    row[key] = float(value)
                except ValueError:
                    row[key] = value
            else:
                row[key] = value
        rows.append(row)
    return rows


def reproduce(session_dir: Path | str) -> dict[str, object]:
    root = Path(session_dir)
    rows = parse_evaluations(root / "s199_session.log")
    final: dict[str, dict[str, object]] = {}
    for row in rows:
        final[str(row["slug"])] = row
    decisions = Counter(
        "signal" if row["kind"] == "signal" else str(row.get("reason"))
        for row in final.values()
    )
    settlements = json.loads((root / "s199_settlements.json").read_text())
    positions = settlements["positions"]
    start = datetime.fromisoformat(json.loads((root / "session.json").read_text())["session_id"].split("-")[0].replace("Z", "+00:00"))
    end = datetime.fromisoformat(json.loads((root / "end.json").read_text())["ended_utc"].replace("Z", "+00:00"))
    wins = sum(bool(p["winner"]) for p in positions)
    log = ANSI.sub("", (root / "s199_session.log").read_text(errors="replace"))
    return {
        "duration_seconds": int((end - start).total_seconds()),
        "initial_cash": "200",
        "ending_equity": settlements["summary"]["synthetic_equity"],
        "net_pnl": str(Decimal(settlements["summary"]["synthetic_equity"]) - Decimal("200")),
        "fills": len(positions), "wins": wins, "losses": len(positions) - wins,
        "evaluation_records": len(rows), "distinct_markets": len(final),
        "final_decisions": dict(sorted(decisions.items())),
        "websocket_reconnections": log.count("PolymarketWebSocketClient: ws-client 0: Reconnected"),
        "exit_code": json.loads((root / "end.json").read_text())["session_exit_code"],
        "final_diagnostics_present": (root / "s199_diagnostics.json").exists(),
    }
