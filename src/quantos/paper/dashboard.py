"""Machine-readable status snapshot for dashboards (atomic JSON file)."""
from __future__ import annotations

import json
from pathlib import Path

from .health import _atomic_write
from .types import dumps

DEFAULT_KINDS = ("SIGNAL", "APPROVED", "REJECTED_RISK", "FILLED", "CLOSED")


def recent_events(journal, kinds=DEFAULT_KINDS, limit: int = 20) -> list[dict]:
    kinds = tuple(kinds)
    if not kinds:
        return []
    marks = ",".join("?" * len(kinds))
    rows = journal.db.execute(
        f"SELECT id,time_ms,kind,payload FROM events WHERE kind IN ({marks}) "
        "ORDER BY id DESC LIMIT ?", (*kinds, int(limit))).fetchall()
    out = []
    for r in rows:
        try: payload = json.loads(r["payload"])
        except ValueError: payload = r["payload"]
        out.append({"id": r["id"], "time_ms": r["time_ms"], "kind": r["kind"], "payload": payload})
    return out


def _engine_view(e, now_ms):
    acct = e.account(now_ms)
    positions = []
    for o in e.active():
        s = o["signal"]
        positions.append({"symbol": s["symbol"], "side": s["side"],
                          "strategy_id": s.get("strategy_id"), "stop_loss": s.get("stop_loss"),
                          "take_profit": s.get("take_profit"), "filled": o.get("filled"),
                          "average": o.get("average"), "state": o.get("state"),
                          "stop_price": o.get("stop_price"), "last_mark": o.get("last_mark")})
    return {"profile": e.journal.profile, "cash": e.journal.cash(), "equity": acct.equity,
            "day_start_equity": acct.day_start_equity, "high_water": acct.high_water,
            "day_pnl": acct.equity - acct.day_start_equity,
            "drawdown": acct.high_water - acct.equity,
            "paused": acct.paused, "reconciled": acct.reconciled,
            "open_positions": positions}


def build_status(mode, run, engines, feed_health, kill_switch_active, now_ms,
                 recent_signals=(), recent_fills=()) -> dict:
    status = {"schema": 1, "time_ms": int(now_ms), "mode": mode,
              "run": dict(run or {}), "kill_switch_active": bool(kill_switch_active),
              "feed_health": feed_health,
              "engines": [_engine_view(e, now_ms) for e in engines],
              "recent_signals": list(recent_signals), "recent_fills": list(recent_fills)}
    # Normalise Decimals to strings (dumps str()s them) into plain JSON types.
    return json.loads(dumps(status))


def write_status(state_dir, status) -> Path:
    path = Path(state_dir) / "dashboard" / "status.json"
    _atomic_write(path, dumps(status) + "\n")
    return path
