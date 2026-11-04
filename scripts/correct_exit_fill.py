"""Correct one closed position's recorded exit to the real fill. Run only with the service stopped.

Rewrites the fills (exit, and optionally entry), the order's average, the FILLED and CLOSED events,
recomputes that strategy's loss streak, and appends a BOOKS_CORRECTION event with before/after values.
Cash and equity are derived from fills, so they follow. Backs up the journal first; re-running with the
same values is a no-op.

Usage: python scripts/correct_exit_fill.py --order 6589638c --price 125.58 [--entry-price P] [--entry-fee F]
       [--exit-fee F] [--state DIR] [--profile pD] [--dry-run]
"""
import argparse
import fcntl
import json
from decimal import Decimal as D, getcontext
from pathlib import Path
import sqlite3
import time

getcontext().prec = 28
STATE = Path.home() / "Library/Application Support/QuantOS/v4-demo200"


def equity(db) -> D:
    identity = json.loads(db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()[0])
    cash = D(identity["initial_balance"])
    for fee, pnl in db.execute("SELECT fee,pnl FROM fills"):
        cash += D(pnl) - D(fee)
    for (amount,) in db.execute("SELECT amount FROM funding"):
        cash += D(amount)
    return cash


def correct(db, order_prefix: str, price: D, now_ms: int, entry_price=None, entry_fee=None, exit_fee=None) -> dict:
    rows = db.execute("SELECT id,payload FROM orders WHERE id LIKE ?", (order_prefix + "%",)).fetchall()
    if len(rows) != 1: raise SystemExit(f"expected one order for {order_prefix}, found {len(rows)}")
    oid, order = rows[0][0], json.loads(rows[0][1])
    if order["state"] != "CLOSED": raise SystemExit(f"order is {order['state']}, not CLOSED")
    entries = db.execute("SELECT id,price,fee FROM fills WHERE order_id=? AND kind='ENTRY'", (oid,)).fetchall()
    fill = db.execute("SELECT quantity,price,fee,pnl FROM fills WHERE id=?", (oid + ":EXIT",)).fetchone()
    if fill is None or len(entries) != 1: raise SystemExit("expected one ENTRY and one EXIT fill")
    quantity, old_price, old_fee, old_pnl = D(fill[0]), D(fill[1]), D(fill[2]), D(fill[3])
    old_entry_price, old_entry_fee = D(entries[0][1]), D(entries[0][2])
    average = old_entry_price if entry_price is None else entry_price
    new_entry_fee = old_entry_fee if entry_fee is None else entry_fee
    fee = old_fee / old_price * price if exit_fee is None else exit_fee  # default: the engine's taker rate
    pnl = quantity * (price - average) * D(order["direction"])
    if (old_price, old_fee, old_pnl, old_entry_price, old_entry_fee) == (price, fee, pnl, average, new_entry_fee):
        return {"skipped": "already corrected", "equity": str(equity(db))}
    before = equity(db)
    with db:
        db.execute("UPDATE fills SET price=?,fee=? WHERE id=?", (str(average), str(new_entry_fee), entries[0][0]))
        db.execute("UPDATE fills SET price=?,fee=?,pnl=? WHERE id=?", (str(price), str(fee), str(pnl), oid + ":EXIT"))
        db.execute("UPDATE orders SET payload=? WHERE id=?", (json.dumps({**order, "average": str(average)}), oid))
        closed = db.execute("SELECT id,payload FROM events WHERE kind='CLOSED' AND json_extract(payload,'$.id')=?", (oid,)).fetchall()
        if len(closed) != 1: raise SystemExit("expected exactly one CLOSED event")
        payload = {**json.loads(closed[0][1]), "price": str(price), "pnl_gross": str(pnl)}
        db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload), closed[0][0]))
        for event_id, raw in db.execute("SELECT id,payload FROM events WHERE kind='FILLED' AND json_extract(payload,'$.id')=?", (oid,)).fetchall():
            db.execute("UPDATE events SET payload=? WHERE id=?",
                       (json.dumps({**json.loads(raw), "price": str(average), "fee": str(new_entry_fee)}), event_id))
        strategy = order["signal"]["strategy_id"]
        streaks = json.loads(db.execute("SELECT value FROM meta WHERE key='loss_streaks'").fetchone()[0])
        was_loss, is_loss = old_pnl - old_fee < 0, pnl - fee < 0
        if was_loss and not is_loss:
            streaks[strategy] = 0
        elif is_loss and not was_loss:
            streaks[strategy] = streaks.get(strategy, 0) + 1
        db.execute("UPDATE meta SET value=? WHERE key='loss_streaks'", (json.dumps(streaks),))
        after = equity(db)
        db.execute("UPDATE meta SET value=? WHERE key='equity_last_observed'", (json.dumps(str(after)),))
        db.execute("INSERT INTO events(time_ms,kind,payload) VALUES(?,?,?)", (now_ms, "BOOKS_CORRECTION", json.dumps({
            "id": oid, "symbol": order["signal"]["symbol"], "reason": "manual exchange close; recorded the real fills",
            "old_entry_price": str(old_entry_price), "new_entry_price": str(average),
            "old_entry_fee": str(old_entry_fee), "new_entry_fee": str(new_entry_fee),
            "old_price": str(old_price), "new_price": str(price), "old_fee": str(old_fee), "new_fee": str(fee),
            "old_pnl": str(old_pnl), "new_pnl": str(pnl), "old_equity": str(before), "new_equity": str(after)})))
    return {"symbol": order["signal"]["symbol"], "entry_price": str(average), "exit_price": str(price),
            "pnl_gross": str(pnl), "entry_fee": str(new_entry_fee), "exit_fee": str(fee),
            "equity_before": str(before), "equity_after": str(after), "loss_streaks": streaks}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--order", required=True, help="order id or unique prefix")
    ap.add_argument("--price", required=True, type=D, help="real exit price")
    ap.add_argument("--entry-price", type=D, help="real entry price (default: keep the recorded one)")
    ap.add_argument("--entry-fee", type=D, help="real entry fee (default: keep the recorded one)")
    ap.add_argument("--exit-fee", type=D, help="real exit fee (default: the engine's taker rate)")
    ap.add_argument("--state", type=Path, default=STATE)
    ap.add_argument("--profile", default="pD")
    ap.add_argument("--dry-run", action="store_true", help="only check the service lock and report the target")
    a = ap.parse_args()
    if not a.price.is_finite() or a.price <= 0: raise SystemExit("price must be positive")
    with (a.state / "engine.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("REFUSED: the service is running (engine.lock held); stop it first") from None
        path = a.state / f"journal_{a.profile}.db"
        db = sqlite3.connect(path, timeout=10)
        try:
            if a.dry_run:
                print("lock free; would correct", a.order, "to", a.price, "equity now", equity(db)); return
            backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%dT%H%M%S')}")
            copy = sqlite3.connect(backup)
            try:
                db.backup(copy)
            finally:
                copy.close()
            print("backup", backup)
            print(json.dumps(correct(db, a.order, a.price, int(time.time() * 1000), a.entry_price, a.entry_fee, a.exit_fee), indent=1))
        finally:
            db.close()


if __name__ == "__main__":
    main()
