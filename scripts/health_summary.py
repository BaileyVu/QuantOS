"""Read-only ≤10-line health summary of every live service (no network, no writes except its own cursor).

Usage: python scripts/health_summary.py
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import time

HOME = Path.home()
Q = HOME / "Library/Application Support/QuantOS"
CURSOR = Q / "health_cursor.json"
SERVICES = ("com.quantos.v4.paper", "com.quantos.v4.demo", "com.sentibot.spot", "com.sentibot.perp",
            "com.quantos.dashboard", "com.quantos.hlcopy")
OPEN = "json_extract(payload,'$.state') NOT IN ('CLOSED','CANCELED','EXPIRED','REJECTED')"


def running(label):
    out = subprocess.run(["launchctl", "print", f"gui/{subprocess.getoutput('id -u')}/{label}"],
                         capture_output=True, text=True).stdout
    return "state = running" in out


def age(path, key="updated_ms"):
    try:
        h = json.loads(path.read_text())
        ts = h.get(key) / 1000 if h.get(key) else h.get("ts")
        return round(time.time() - ts)
    except Exception:
        return None


def main():
    cur = json.loads(CURSOR.read_text()) if CURSOR.exists() else {}
    down = [s.split(".")[-1] for s in SERVICES if not running(s)]
    lines = [f"services: {'ALL UP' if not down else 'DOWN: ' + ','.join(down)}"]
    for run, profile in (("v4-live50", "pL"), ("v4-live50", "pL5"), ("v4-live50-demo", "pL")):
        db = sqlite3.connect((Q / run / f"journal_{profile}.db").as_uri() + "?mode=ro", uri=True)
        cash = 50.0 + sum(float(p) - float(f) for f, p in db.execute("SELECT fee,pnl FROM fills"))
        cash += sum(float(a) for (a,) in db.execute("SELECT amount FROM funding"))
        pos = [json.loads(p) for (p,) in db.execute(f"SELECT payload FROM orders WHERE {OPEN}")]
        upnl = sum(float(o["filled"]) * (float(o["last_mark"]) - float(o["average"])) * float(o["direction"])
                   for o in pos if float(o["filled"]) > 0)
        key = f"{run}:{profile}"
        last = cur.get(key, 0)
        new = db.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM events WHERE id>? AND kind IN "
                         "('FILLED','CLOSED','KILL_SWITCH','LOSS_BREAKER','EXCHANGE_RECONCILE_FAILED')",
                         (last,)).fetchone()
        cur[key] = max(last, new[1])
        paused = db.execute("SELECT value FROM meta WHERE key='paused'").fetchone()
        db.close()
        syms = ",".join(f"{o['signal']['symbol']}{'+' if o['direction'] == '1' else '-'}" for o in pos) or "-"
        lines.append(f"{run[3:]}/{profile}: eq {cash + upnl:.2f} pos {syms} new_trade_events {new[0]}"
                     f" paused {json.loads(paused[0]) if paused else False}")
    for run in ("v4-live50", "v4-live50-demo"):
        alerts = [json.loads(line) for line in (Q / run / "alerts.jsonl").read_text().splitlines() if line.strip()]
        seen = cur.get(f"{run}:alerts", len(alerts))
        crit = [a["key"] for a in alerts[seen:] if a.get("level") == "critical"]
        cur[f"{run}:alerts"] = len(alerts)
        lines.append(f"{run[3:]}: hb {age(Q / run / 'heartbeat.json')}s new_critical {crit or '-'}")
    lines.append(f"hlcopy: hb {age(HOME / 'Library/Application Support/hl_copy/state/heartbeat.json', 'ts')}s")
    CURSOR.write_text(json.dumps(cur))
    print("\n".join(lines[:10]))


if __name__ == "__main__":
    main()
