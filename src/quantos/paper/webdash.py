"""Local live dashboard: `python -m quantos.paper.webdash --run NAME=STATE ... [--sentibot APP_DIR] [--port 8787]`.

Serves http://127.0.0.1:PORT/ and /api/state (localhost only). Reads each run's status.json, heartbeat, alerts and its journal
(SQLite read-only) plus sentibot's latest perp line. No network calls and no writes.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import time

DECISIONS = ("SIGNAL", "APPROVED", "REJECTED_RISK", "FILLED", "PARTIAL", "CLOSED", "CANCELED",
             "SENTIMENT_SHADOW", "KILL_SWITCH", "LOSS_BREAKER", "EXCHANGE_RECONCILE_FAILED")


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _tail(path: Path, n: int) -> list:
    try:
        lines = path.read_text().splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try: out.append(json.loads(line))
        except ValueError: pass
    return out


def _price(p: dict) -> str:
    """Short human text for fill/close events: price to 6 significant digits, plus gross PnL."""
    try:
        text = f"price {float(p['price']):.6g}" if p.get("price") else ""
        if p.get("pnl_gross"): text += f", pnl {float(p['pnl_gross']):+.4f}"
        return text
    except (TypeError, ValueError):
        return ""


def run_view(name: str, state: Path, now_ms: int) -> dict:
    view = {"name": name, "state": str(state), "run": _json(state / "run.json", {}),
            "status": _json(state / "dashboard" / "status.json", {}),
            "heartbeat": _json(state / "heartbeat.json", {}), "alerts": _tail(state / "alerts.jsonl", 50),
            "mirror": _json(state / "mirror.json")}
    hb = view["heartbeat"].get("updated_ms")
    view["heartbeat_age_s"] = None if hb is None else round((now_ms - hb) / 1000, 1)
    db_path = state / "journal_pL.db"
    if not db_path.exists():
        return view
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        since = now_ms - 3600_000
        view["scans_last_hour"] = db.execute(
            "SELECT COUNT(DISTINCT time_ms) FROM events WHERE kind='SCAN' AND time_ms>=?", (since,)).fetchone()[0]
        view["errors_last_hour"] = db.execute(
            "SELECT COUNT(*) FROM events WHERE kind='ERROR' AND time_ms>=?", (since,)).fetchone()[0]
        # Fail-closed stale-quote rejections (quiet USDC books) are expected and reported apart from real errors.
        view["errors_last_15m"], view["stale_quotes_last_15m"] = db.execute(
            "SELECT COALESCE(SUM(payload NOT LIKE '%invalid or stale quote%'),0), "
            "COALESCE(SUM(payload LIKE '%invalid or stale quote%'),0) FROM events WHERE kind='ERROR' AND time_ms>=?",
            (now_ms - 900_000,)).fetchone()
        row = db.execute("SELECT time_ms,payload FROM events WHERE kind='SELF_CHECK' ORDER BY id DESC LIMIT 1").fetchone()
        view["self_check"] = None if row is None else {"time_ms": row[0], "checks": json.loads(row[1])}
        view["counts"] = dict(db.execute(
            "SELECT kind, COUNT(*) FROM events WHERE kind IN ('SIGNAL','APPROVED','REJECTED_RISK','FILLED','CLOSED') "
            "GROUP BY kind").fetchall())
        reasons = {}
        for (payload,) in db.execute("SELECT payload FROM events WHERE kind='REJECTED_RISK'"):
            r = json.loads(payload).get("reason", "?"); reasons[r] = reasons.get(r, 0) + 1
        view["reject_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1]))
        signals = {}
        for (payload,) in db.execute("SELECT payload FROM events WHERE kind='SIGNAL' ORDER BY id DESC LIMIT 300"):
            p = json.loads(payload); signals[p.get("signal_id")] = p
        # Fill/close events carry the order id; map it back to the order's signal.
        by_order = {}
        for (payload,) in db.execute("SELECT payload FROM orders"):
            o = json.loads(payload); by_order[o["id"]] = o["signal"]
        feed = []
        marks = ",".join("?" * len(DECISIONS))
        for t, kind, payload in db.execute(
                f"SELECT time_ms,kind,payload FROM events WHERE kind IN ({marks}) ORDER BY id DESC LIMIT 40", DECISIONS):
            p = json.loads(payload)
            s = signals.get(p.get("signal_id")) or by_order.get(p.get("id")) or (p if kind == "SIGNAL" else {})
            feed.append({"time_ms": t, "kind": kind, "strategy": s.get("strategy_id"), "symbol": s.get("symbol"),
                         "side": s.get("side"), "reason": p.get("reason") or p.get("verdict") or "",
                         "detail": p.get("detail") or p.get("binding_constraint") or _price(p) or ""})
        view["feed"] = feed
        closed = db.execute("SELECT COALESCE(SUM(CAST(pnl AS REAL)),0), COALESCE(SUM(CAST(fee AS REAL)),0) FROM fills").fetchone()
        view["realized"] = {"pnl": closed[0], "fees": closed[1]}
        from .risk import LivePolicy, strategy_slot
        used = {}
        for pos in view["status"].get("engines", [{}])[0].get("open_positions", []):
            slot = strategy_slot(pos.get("strategy_id") or ""); used[slot] = used.get(slot, 0) + 1
        view["slots"] = {slot: {"used": used.get(slot, 0), "max": n} for slot, n in LivePolicy.slots}
        view["fills"] = [{"time_ms": t, "kind": k, "order_id": o, "quantity": q, "price": pr, "fee": f, "pnl": pn}
                         for t, k, o, q, pr, f, pn in db.execute(
                             "SELECT time_ms,kind,order_id,quantity,price,fee,pnl FROM fills ORDER BY time_ms DESC LIMIT 30")]
    finally:
        db.close()
    return view


def sentibot_view(path: Path | None, now_ms: int) -> dict | None:
    if path is None:
        return None
    rows = _tail(path, 1)
    if not rows:
        return {"available": False}
    r = rows[-1]
    return {"available": True, "time": r.get("time"), "equity": r.get("equity"),
            "composite": {k: v.get("composite") for k, v in (r.get("signals") or {}).items() if isinstance(v, dict)},
            "news": {k: v for k, v in ((r.get("sentiment_raw") or {}).get("news") or {}).items() if not k.startswith("_n")},
            "sources": (r.get("sentiment") or {}).get("status"), "counts": (r.get("sentiment") or {}).get("counts")}


def snapshot(runs, sentibot) -> dict:
    """The /api/state contract: {time_ms, runs: [run_view...], sentibot: {perp, spot}}."""
    now = int(time.time() * 1000)
    bots = None if sentibot is None else {
        "perp": sentibot_view(sentibot / "paper_perp_signals.jsonl", now),
        "spot": sentibot_view(sentibot / "paper_signals.jsonl", now)}
    return {"time_ms": now, "runs": [run_view(n, s, now) for n, s in runs], "sentibot": bots}


PAGE = (Path(__file__).with_name("webdash.html"))


def serve(runs, sentibot, port=8787):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/state"):
                body, kind = json.dumps(snapshot(runs, sentibot), default=str).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, kind = PAGE.read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404); return
            self.send_response(200)
            self.send_header("Content-Type", kind); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main(argv=None):
    p = argparse.ArgumentParser(prog="quantos-webdash")
    p.add_argument("--run", action="append", required=True, help="NAME=STATE_DIR")
    p.add_argument("--sentibot", type=Path, help="sentibot app folder")
    p.add_argument("--port", type=int, default=8787)
    a = p.parse_args(argv)
    runs = [(r.split("=", 1)[0], Path(r.split("=", 1)[1]).expanduser()) for r in a.run]
    serve(runs, a.sentibot.expanduser() if a.sentibot else None, a.port)


if __name__ == "__main__":
    main()
