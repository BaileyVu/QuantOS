"""Turn the replay journals into per-signal CSVs and summary JSON (read-only on the journals)."""
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path.home() / "Desktop/PythonProject/QuantOS/src"))
from quantos.paper.journal import Journal  # noqa: E402
from quantos.paper.report import trade_result  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
f = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%m-%d %H:%M:%S") if t else ""  # noqa: E731
STRATS = ["ema_atr_limit_pullback", "donchian_atr_stop_breakout", "tsmom_4h", "turtle_55",
          "vol_squeeze_breakout", "delist_short_e17", "crash_flush_e2", "alt_dip_reversal", "ema_level_2h"]


def journal_rows(out, group, profile, path):
    marks = json.loads((out / "end_marks.json").read_text())["marks"]
    j = Journal(path, "pR" if group != "L" else profile, "replay")
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    decisions, cancels, signals = {}, defaultdict(list), []
    for r in db.execute("SELECT time_ms,kind,payload FROM events WHERE kind IN "
                        "('SIGNAL','APPROVED','REJECTED_RISK','CANCELED','EXPIRED','ADAPTIVE_SIZING','LOSS_BREAKER') ORDER BY id"):
        p = json.loads(r["payload"])
        if r["kind"] == "SIGNAL":
            signals.append((r["time_ms"], p))
        elif r["kind"] in ("APPROVED", "REJECTED_RISK"):
            decisions[p["signal_id"]] = p
        elif r["kind"] == "ADAPTIVE_SIZING":
            decisions.setdefault("_adaptive", {})[p["signal_id"]] = p
        elif r["kind"] in ("CANCELED", "EXPIRED"):
            cancels[p["id"]].append(p["reason"])
    orders = {o["signal"]["signal_id"]: o for o in j.orders()}
    rows = []
    for t, s in signals:
        d = decisions.get(s["signal_id"], {})
        o = orders.get(s["signal_id"])
        row = {"group": group, "profile": profile, "time": f(t), "strategy": s["strategy_id"], "symbol": s["symbol"],
               "side": s["side"], "type": s["entry_type"], "entry": s["entry_price"], "stop": s["stop_loss"],
               "target": s["take_profit"], "features": ";".join(f"{k}={D(v):.6g}" for k, v in s["features"]),
               "approved": d.get("approved"), "decision": d.get("reason"), "qty": d.get("quantity"),
               "risk_usd": d.get("achievable_risk"), "leverage": d.get("leverage"),
               "adaptive_risk": decisions.get("_adaptive", {}).get(s["signal_id"], {}).get("risk"),
               "fill": "", "fill_time": "", "fill_price": "", "exit_reason": "", "exit_time": "",
               "exit_price": "", "net_pnl": "", "R": "", "unrealized_at_end": ""}
        if o:
            fills = db.execute("SELECT * FROM fills WHERE order_id=? ORDER BY time_ms", (o["id"],)).fetchall()
            entry = [x for x in fills if x["kind"] == "ENTRY"]
            exit_ = [x for x in fills if x["kind"] == "EXIT"]
            row["fill"] = "FILLED" if entry else "NO_FILL:" + ",".join(dict.fromkeys(cancels.get(o["id"], [o["state"]])))
            if entry:
                row["fill_time"], row["fill_price"] = f(entry[0]["time_ms"]), o["average"]
                net, qty, planned = trade_result(j, o)
                if exit_:
                    row.update(exit_reason=o.get("close_reason"), exit_time=f(exit_[0]["time_ms"]),
                               exit_price=exit_[0]["price"], net_pnl=f"{net:.4f}",
                               R=f"{net / planned:.3f}" if planned else "")
                else:
                    mark = D(marks[f"{group}:{j.profile}:{o['id']}"])
                    unreal = D(o["filled"]) * (mark - D(o["average"])) * D(o["direction"])
                    row.update(exit_reason="OPEN_AT_WINDOW_END", exit_price=f"mark {mark}",
                               unrealized_at_end=f"{unreal:.4f}", net_pnl=f"{net + unreal:.4f}",
                               R=f"{(net + unreal) / planned:.3f}" if planned else "")
        rows.append(row)
    cash = j.cash()
    j.close()
    return rows, cash, db


def main():
    summary = {}
    all_rows = []
    sets = [("out", "L", p, f"L/journal_{p}.db") for p in ("p1", "p5", "pA")]
    sets += [("out", "R", "pR", "R/journal_pR.db")]
    sets += [("out", "CF", p, f"CF_{p}/journal_pR.db") for p in ("p1", "p5", "pA")]
    sets += [("out_oirepair", "R", "pR", "R/journal_pR.db")]
    sets += [("out_oirepair", "CF", p, f"CF_{p}/journal_pR.db") for p in ("p1", "p5", "pA")]
    for outname, group, profile, rel in sets:
        out = ROOT / outname
        if not (out / rel).exists():
            continue
        rows, cash, db = journal_rows(out, group, profile, out / rel)
        for r in rows:
            r["run"] = outname
        all_rows += rows
        key = f"{outname}:{group}:{profile}"
        per = {}
        for s in STRATS:
            rs = [r for r in rows if r["strategy"] == s]
            if not rs:
                continue
            per[s] = {"signals": len(rs), "approved": sum(1 for r in rs if r["approved"]),
                      "filled": sum(1 for r in rs if r["fill"] == "FILLED"),
                      "closed": sum(1 for r in rs if r["exit_reason"] and r["exit_reason"] != "OPEN_AT_WINDOW_END"),
                      "open": sum(1 for r in rs if r["exit_reason"] == "OPEN_AT_WINDOW_END"),
                      "net_pnl_incl_unrealized": f'{sum(D(r["net_pnl"]) for r in rs if r["net_pnl"]):.4f}',
                      "R_sum": f'{sum(D(r["R"]) for r in rs if r["R"]):.3f}',
                      "rejects": dict(Counter(r["decision"] for r in rs if not r["approved"]).most_common()),
                      "no_fill": dict(Counter(r["fill"] for r in rs if r["fill"].startswith("NO_FILL")))}
        unreal = sum(D(r["unrealized_at_end"]) for r in rows if r["unrealized_at_end"])
        kinds = dict(db.execute("SELECT kind,count(*) FROM events WHERE kind IN ('DISCONNECTED','LOSS_BREAKER','CONFLICT','DUPLICATE','EDGE_STATE','FUNDING') GROUP BY kind").fetchall())
        disc = Counter(json.loads(p)["reason"] for (p,) in db.execute("SELECT payload FROM events WHERE kind='DISCONNECTED'"))
        summary[key] = {"cash_realized": f"{cash:.4f}", "unrealized": f"{unreal:.4f}",
                        "equity_marked": f"{cash + unreal:.4f}", "pnl_marked": f"{cash + unreal - 200:.4f}",
                        "per_strategy": per, "events": kinds, "disconnect_reasons": dict(disc),
                        "paused": json.loads(db.execute("SELECT value FROM meta WHERE key='paused'").fetchone()[0])
                        if db.execute("SELECT 1 FROM meta WHERE key='paused'").fetchone() else False}
        db.close()
    with (ROOT / "signals_all.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run", *all_rows[0].keys()] if all_rows else ["run"], extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    traded = [r for r in all_rows if r["approved"]]
    with (ROOT / "signals_approved.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run", *all_rows[0].keys()], extrasaction="ignore")
        w.writeheader()
        w.writerows(traded)
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=1))
    for k, v in summary.items():
        print(k, v["equity_marked"], v["pnl_marked"], {s: (x["signals"], x["approved"], x["filled"], x["net_pnl_incl_unrealized"]) for s, x in v["per_strategy"].items()}, v["disconnect_reasons"], v["paused"])


if __name__ == "__main__":
    main()
