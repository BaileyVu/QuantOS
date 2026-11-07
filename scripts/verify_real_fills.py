"""Binance DEMO check that the mirror books real fills: one tiny long through ExchangeMirror, closed,
then the booked entry/exit/fees compared with Binance's own trade list and realizedPnl.

Usage: QUANTOS_KEY_FILE=/path/to/demo-keys PYTHONPATH=src python scripts/verify_real_fills.py [SYMBOL]
Demo only (hard-coded). Refuses if the account has any open position. Prints prices, never keys.
"""
from dataclasses import asdict
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ["QUANTOS_TESTNET_HOST"] = "demo"

from quantos.live.bridge import ExchangeMirror  # noqa: E402
from quantos.live.client import SignedClient  # noqa: E402
from quantos.live.keys import load_keys  # noqa: E402
from quantos.paper.market import PublicMarket, parse_exchange_info  # noqa: E402
from quantos.paper.types import D, floor_step  # noqa: E402


class Engine:
    def __init__(self): self.orders, self.booked = [], []
    def active(self): return self.orders
    def book_real_fills(self, oid, now_ms, entry=None, exit=None):
        self.booked.append(("entry", entry) if entry else ("exit", exit))
        return {"has_exit": exit is not None}


def run(m, e, now, oid, alerts):
    m.sync(e, now())
    for _ in range(15):
        if e.booked: break
        time.sleep(2); m.sync(e, now())
    print("open:", m.book[oid]["state"], "qty", m.book[oid]["qty"], "booked", e.booked)
    time.sleep(3)
    e.orders = []
    m.sync(e, now())
    for _ in range(40):
        if len(e.booked) >= 2: break
        time.sleep(2); m.sync(e, now())
    print("closed:", m.book[oid]["state"], "booked", e.booked, "alerts", alerts)


def main(symbol="NEARUSDC"):
    client = SignedClient("testnet", load_keys(os.environ["QUANTOS_KEY_FILE"]))
    if any(D(str(p["positionAmt"])) != 0 for p in client.position_risk()):
        raise SystemExit("REFUSED: Demo account has an open position")
    ins = parse_exchange_info(client.exchange_info(), "USDC").get(symbol)
    mark = PublicMarket().quote(symbol).mark
    step = max(ins.step, ins.market_step)
    qty = max(ins.min_qty, -floor_step(-(ins.min_notional * D("1.2") / mark), step))
    fields = {k: str(v) for k, v in asdict(ins).items()}
    oid = uuid.uuid4().hex
    order = {"id": oid, "state": "PROTECTED", "filled": str(qty), "stop_price": str(mark * D("0.9")),
             "instrument": fields, "signal": {"side": "LONG", "symbol": symbol}, "decision": {"leverage": "1"}}
    alerts = []
    with tempfile.TemporaryDirectory() as state:
        m = ExchangeMirror(client, f"vf{int(time.time())}", Path(state), lambda *a: alerts.append(a))
        e = Engine()
        e.orders = [order]
        now = lambda: int(time.time() * 1000)
        try:
            run(m, e, now, oid, alerts)
        except BaseException:
            m._flatten()  # close the test position and its stop, then verify flat
            print("ERROR: test position flattened, flat verified:", m.flat_confirmed)
            raise
        if any(D(str(p["positionAmt"])) != 0 for p in client.position_risk(symbol)):
            m._flatten()
            print("WARNING: position was left open; flattened, verified:", m.flat_confirmed)
        leg = m.book[oid]
        trades = client.user_trades(symbol, leg["opened_ms"] - 60_000)
    entry = [t for t in trades if str(t["orderId"]) == str(leg["entry_order"])]
    exits = [t for t in trades if t["side"] == "SELL" and str(t["orderId"]) != str(leg["entry_order"])]
    real_pnl = sum(D(str(t["realizedPnl"])) for t in exits)
    real_fee = sum(D(str(t["commission"])) for t in entry + exits)
    ok = len(e.booked) == 2
    if ok:
        (_, (ep, ef)), (_, (xp, xf)) = e.booked
        booked_pnl = D(leg["qty"]) * (xp - ep)
        booked_fee = (ef or 0) + (xf or 0)
        print(f"entry {ep} exit {xp} pnl booked {booked_pnl} vs Binance {real_pnl}; fees {booked_fee} vs {real_fee}")
        ok = abs(booked_pnl - real_pnl) < D("0.0001") and abs(booked_fee - real_fee) < D("0.000001")
    flat = all(D(str(p["positionAmt"])) == 0 for p in client.position_risk(symbol))
    print("flat:", flat)
    print("RESULT:", "PASS" if ok and flat else "FAIL")


if __name__ == "__main__":
    main(*sys.argv[1:])
