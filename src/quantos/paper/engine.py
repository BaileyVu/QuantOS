"""Single-owner paper execution state machine; no network order interface exists."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json

from .journal import Journal
from .risk import Account, Policy, evaluate, valid_quote, quantity_step
from .types import D, Funding, Instrument, Quote, Signal, Trade, floor_step

TERMINAL = {"CLOSED", "CANCELED", "EXPIRED", "REJECTED"}


@dataclass
class Faults:
    """Deterministic fake exchange failure switches, never used by natural runtime."""
    timeout_after_accept: bool = False
    crash_after_fill: bool = False
    reject_stop: bool = False
    reject_target: bool = False
    manual_position: bool = False


class PaperEngine:
    latency_ms = 500
    participation = D(".01")

    def __init__(self, journal: Journal, policy: Policy, faults: Faults | None = None):
        if faults is not None and journal.get("identity")["evidence"] != "synthetic":
            raise ValueError("fault injection is synthetic-only")
        self.journal, self.policy = journal, policy
        self.faults = faults or Faults()
        self.quotes: dict[str, Quote] = {}
        self.connected = False

    def active(self):
        return [o for o in self.journal.orders() if o["state"] not in TERMINAL]

    def entry_allowed(self):
        return (self.connected and self.journal.get("reconciled", False)
                and not self.journal.get("paused", False))

    def query_order(self, order_id):
        row = self.journal.db.execute("SELECT payload FROM orders WHERE id=?", (order_id,)).fetchone()
        if row is None: raise ValueError("unknown paper order")
        return json.loads(row[0])

    def enforce_breaker(self, now_ms):
        account = self.account(now_ms)
        if (account.equity > account.day_start_equity*(1-self.policy.daily_loss)
                and account.equity > account.high_water*(1-self.policy.drawdown)):
            return
        first = self.journal.get("paused") != "LOSS_BREAKER"
        self.journal.set("paused", "LOSS_BREAKER")
        for o in self.active():
            if D(o["filled"]) > 0 and o["state"] != "CLOSING":
                self._closing(o, now_ms, "LOSS_BREAKER")
            else:
                self._cancel(o, now_ms, "LOSS_BREAKER")
            self.journal.save(o)
        if first: self.journal.event(now_ms, "LOSS_BREAKER", {"equity": str(account.equity)})

    def account(self, now_ms):
        equity = self.journal.cash()
        for o in self.active():
            q = self.quotes.get(o["signal"]["symbol"])
            if D(o["filled"]) > 0:
                mark = q.mark if q else D(o["last_mark"])
                equity += D(o["filled"]) * (mark - D(o["average"])) * D(o["direction"])
        day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date().isoformat()
        high = max(D(self.journal.get("high_water", "200")), equity)
        self.journal.set("high_water", str(high))
        if self.journal.get("day") != day:
            self.journal.set("day", day)
            self.journal.set("day_equity", str(equity))
        return Account(equity, D(self.journal.get("day_equity")), high,
                       len(self.active()), self.connected and self.journal.get("reconciled", False),
                       bool(self.journal.get("paused", False)))

    def recover(self, now_ms):
        """Verify durable fill totals and restore known protection before new entries."""
        with self.journal.transaction():
            errors = []
            if self.faults.manual_position:
                errors.append("UNOWNED_POSITION")
            if self.journal.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                errors.append("DATABASE_INTEGRITY")
            for o in self.journal.orders():
                rows = self.journal.db.execute("SELECT kind,quantity,price,fee,pnl FROM fills WHERE order_id=?", (o["id"],)).fetchall()
                entered = sum((D(r["quantity"]) for r in rows if r["kind"] == "ENTRY"), D(0))
                exited = sum((D(r["quantity"]) for r in rows if r["kind"] == "EXIT"), D(0))
                if entered - exited != D(o["filled"]) or entered > D(o["quantity"]) or exited > entered:
                    errors.append("FILL_STATE_MISMATCH")
                    continue
                if entered:
                    average = sum((D(r["quantity"])*D(r["price"]) for r in rows if r["kind"] == "ENTRY"), D(0))/entered
                    if average != D(o["average"]):
                        errors.append("AVERAGE_PRICE_MISMATCH")
                        continue
                if D(o["filled"]) > 0:
                    o["remaining"] = "0"
                    if o["state"] in TERMINAL:
                        errors.append("TERMINAL_WITH_EXPOSURE")
                    elif o["state"] != "CLOSING":
                        self._protect(o, now_ms)
                        self.journal.save(o)
                elif o["state"] not in TERMINAL:
                    # Pending intents never resume into stale market evidence after a restart.
                    o["state"] = "CANCELED"
                    o["remaining"] = "0"
                    self.journal.save(o)
                    self.journal.event(now_ms, "CANCELED", {"id": o["id"], "reason": "RESTART"})
            if errors:
                self.journal.set("paused", errors)
            self.journal.set("reconciled", not errors)
            self.journal.event(now_ms, "RECONCILED" if not errors else "RECONCILIATION_FAILED", errors)
        return not errors

    def connection(self, connected, now_ms, reason=""):
        self.connected = connected
        with self.journal.transaction():
            self.journal.event(now_ms, "CONNECTED" if connected else "DISCONNECTED", {"reason": reason})
            if not connected:
                for o in self.active():
                    self._cancel(o, now_ms, "FEED_DISCONNECTED")

    def propose(self, signal: Signal, instrument: Instrument, quote: Quote, now_ms):
        with self.journal.transaction():
            if self.journal.db.execute("SELECT 1 FROM signals WHERE id=?", (signal.signal_id,)).fetchone():
                self.journal.event(now_ms, "DUPLICATE", {"signal_id": signal.signal_id})
                return None
            self.journal.db.execute("INSERT INTO signals VALUES(?,?)", (signal.signal_id, now_ms))
            self.journal.event(now_ms, "SIGNAL", asdict(signal))
            self.quotes[quote.symbol] = quote
            decision = evaluate(signal, instrument, quote, self.account(now_ms), self.policy, now_ms)
            self.journal.event(now_ms, "APPROVED" if decision.approved else "REJECTED_RISK",
                               {"signal_id": signal.signal_id, **asdict(decision)})
            if not decision.approved:
                return None
            order_id = hashlib.sha256(f"{self.journal.profile}:{signal.signal_id}".encode()).hexdigest()[:32]
            order = {"id": order_id, "signal": asdict(signal), "instrument": asdict(instrument),
                     "decision": asdict(decision), "quantity": str(decision.quantity),
                     "remaining": str(decision.quantity), "filled": "0", "average": "0",
                     "direction": "1" if signal.side == "LONG" else "-1",
                     "state": "CREATED", "created_ms": now_ms,
                     "eligible_ms": now_ms + self.latency_ms, "first_fill_ms": None,
                     "last_trade_id": -1, "queue": str(decision.quantity),
                     "stop_active": False, "target_active": False,
                     "last_mark": str(quote.mark), "trigger_ms": None}
            self.journal.save(order)
            self.journal.event(now_ms, "CREATED", {"id": order_id})
            for state in ("SUBMITTING", "ACKNOWLEDGED", "OPEN"):
                order["state"] = state
                self.journal.event(now_ms, state, {"id": order_id})
            self.journal.save(order)
        if self.faults.timeout_after_accept:
            self.faults.timeout_after_accept = False
            # Accepted state is authoritative. Caller must query, never resubmit.
            with self.journal.transaction():
                self.journal.event(now_ms, "TRANSPORT_TIMEOUT", {"id": order_id})
                self.journal.event(now_ms, "RECONCILING", {"id": order_id})
                authoritative = self.query_order(order_id)
                self.journal.event(now_ms, "ORDER_QUERY", {"id": order_id, "resolved": authoritative["state"]})
        return order_id

    def _protect(self, o, now_ms):
        if self.faults.reject_stop or self.faults.reject_target:
            o["stop_active"] = o["target_active"] = False
            self.journal.set("paused", "PROTECTION_REJECTED")
            self.journal.event(now_ms, "PROTECTION_REJECTED", {"id": o["id"]})
            self._closing(o, now_ms, "PROTECTION_FAILURE")
        else:
            o["stop_active"] = o["target_active"] = True
            o["state"] = "PROTECTED"
            self.journal.event(now_ms, "PROTECTED", {"id": o["id"], "quantity": o["filled"],
                                                     "stop_id": o["id"] + "-sl", "target_id": o["id"] + "-tp"})

    def _entry_fill(self, o, quantity, price, now_ms, source, maker):
        quantity = min(quantity, D(o["remaining"]))
        if quantity <= 0:
            return
        fill_id = f"{o['id']}:ENTRY:{source}"
        if self.journal.db.execute("SELECT 1 FROM fills WHERE id=?", (fill_id,)).fetchone():
            return
        old = D(o["filled"])
        o["average"] = str((D(o["average"]) * old + price * quantity) / (old + quantity))
        o["filled"] = str(old + quantity)
        o["remaining"] = str(D(o["remaining"]) - quantity)
        o["first_fill_ms"] = o["first_fill_ms"] or now_ms
        fee = price * quantity * (self.policy.maker_fee if maker else self.policy.taker_fee)
        self.journal.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?)",
                               (fill_id, o["id"], now_ms, "ENTRY", str(quantity), str(price), str(fee), "0"))
        o["state"] = "FILLED" if D(o["remaining"]) == 0 else "PARTIAL"
        self.journal.event(now_ms, o["state"], {"id": o["id"], "fill_id": fill_id,
                                               "quantity": str(quantity), "price": str(price), "fee": str(fee)})
        if not self.faults.crash_after_fill:
            self._protect(o, now_ms)
        self.journal.save(o)

    def trade(self, trade: Trade, now_ms):
        if (not self.entry_allowed() or not 0 <= now_ms - trade.time_ms <= 5000
                or not trade.price.is_finite() or trade.price <= 0):
            return
        with self.journal.transaction():
            for o in self.active():
                s = o["signal"]
                if (s["symbol"] != trade.symbol or s["entry_type"] != "LIMIT" or
                    o["state"] == "CLOSING" or D(o["remaining"]) <= 0 or
                    trade.time_ms < o["eligible_ms"] or now_ms >= s["expires_ms"] or
                    trade.trade_id <= o["last_trade_id"]):
                    continue
                o["last_trade_id"] = trade.trade_id
                through = (trade.buyer_maker and trade.price < D(s["entry_price"])) if s["side"] == "LONG" else (not trade.buyer_maker and trade.price > D(s["entry_price"]))
                if through and trade.quantity.is_finite() and trade.quantity > 0:
                    available = trade.quantity * self.participation
                    queue = D(o["queue"])
                    o["queue"] = str(max(D(0), queue - available))
                    available = max(D(0), available - queue)
                    filters = o["instrument"]
                    ins = Instrument(**{k: v if k in {"symbol", "margin_asset", "status"} else D(v)
                                        for k, v in filters.items()})
                    quantity = floor_step(available, quantity_step(ins))
                    self._entry_fill(o, quantity, D(s["entry_price"]), now_ms, f"trade-{trade.trade_id}", True)
                self.journal.save(o)
        if self.faults.crash_after_fill and any(D(o["filled"]) > 0 and not o["stop_active"] for o in self.active()):
            raise RuntimeError("SYNTHETIC_CRASH_AFTER_DURABLE_FILL")

    def _cancel(self, o, now_ms, reason):
        if D(o["remaining"]) > 0:
            o["remaining"] = "0"
            self.journal.event(now_ms, "CANCELED", {"id": o["id"], "reason": reason})
        if D(o["filled"]) == 0:
            o["state"] = "EXPIRED" if reason == "ENTRY_TTL" else "CANCELED"
            self.journal.event(now_ms, o["state"], {"id": o["id"], "reason": reason})
        self.journal.save(o)

    def cancel(self, order_id, now_ms, raced_trade: Trade | None = None):
        if raced_trade is not None:
            self.trade(raced_trade, now_ms)
        with self.journal.transaction():
            for o in self.active():
                if o["id"] == order_id:
                    self._cancel(o, now_ms, "REQUESTED")

    def _closing(self, o, now_ms, reason):
        o["remaining"] = "0"
        o["state"] = "CLOSING"
        o["close_reason"] = reason
        o["exit_after_ms"] = now_ms + self.latency_ms
        self.journal.event(now_ms, "CLOSING", {"id": o["id"], "reason": reason})

    def _exit(self, o, q, now_ms):
        quantity = D(o["filled"])
        side = D(o["direction"])
        depth = q.bid_qty if side > 0 else q.ask_qty
        impact = self.policy.slippage + D(".01") * quantity / depth
        price = (q.bid if side > 0 else q.ask) * max(D(".01"), (1 - side * impact))
        fee = price * quantity * self.policy.taker_fee
        pnl = quantity * (price - D(o["average"])) * side
        self.journal.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?)",
                               (o["id"] + ":EXIT", o["id"], now_ms, "EXIT", str(quantity), str(price), str(fee), str(pnl)))
        o["filled"] = o["remaining"] = "0"
        o["stop_active"] = o["target_active"] = False
        o["state"] = "CLOSED"
        o["closed_ms"] = now_ms
        self.journal.event(now_ms, "CLOSED", {"id": o["id"], "reason": o["close_reason"],
                                             "price": str(price), "quantity": str(quantity), "pnl_gross": str(pnl)})

    def quote(self, q: Quote, now_ms):
        if not self.journal.get("reconciled", False):
            return
        if not valid_quote(q, now_ms):
            self.connection(False, now_ms, "INVALID_DATA")
            return
        previous = self.quotes.get(q.symbol)
        if previous and (q.time_ms < previous.time_ms or
                         (q.time_ms == previous.time_ms and q.mark_time_ms == previous.mark_time_ms)):
            return
        self.quotes[q.symbol] = q
        with self.journal.transaction():
            self.enforce_breaker(now_ms)
            for o in self.active():
                s = o["signal"]
                if s["symbol"] != q.symbol:
                    continue
                o["last_mark"] = str(q.mark)
                if now_ms >= s["expires_ms"] and D(o["remaining"]) > 0:
                    self._cancel(o, now_ms, "ENTRY_TTL")
                if o["state"] == "CLOSING" and q.time_ms >= o["exit_after_ms"]:
                    self._exit(o, q, now_ms)
                elif D(o["filled"]) > 0 and o["state"] != "CLOSING":
                    long = s["side"] == "LONG"
                    stop = q.mark <= D(s["stop_loss"]) if long else q.mark >= D(s["stop_loss"])
                    target = q.bid >= D(s["take_profit"]) if long else q.ask <= D(s["take_profit"])
                    if stop or target or now_ms >= o["first_fill_ms"] + s["maximum_hold_seconds"] * 1000:
                        self._closing(o, now_ms, "SL" if stop else "TP" if target else "TIME_EXIT")
                elif (self.entry_allowed() and o["state"] not in TERMINAL and D(o["remaining"]) > 0
                      and s["entry_type"] in {"MARKET", "STOP"} and q.time_ms >= o["eligible_ms"]):
                    long = s["side"] == "LONG"
                    if s["entry_type"] == "STOP" and o["trigger_ms"] is None:
                        if (q.ask >= D(s["entry_price"]) if long else q.bid <= D(s["entry_price"])):
                            o["trigger_ms"] = now_ms
                    elif s["entry_type"] == "MARKET" or q.time_ms >= o["trigger_ms"] + self.latency_ms:
                        price = (q.ask if long else q.bid) * (1 + D(o["direction"]) * self.policy.slippage)
                        # Reject gaps beyond risk allowance instead of inventing fills at trigger.
                        depth = q.ask_qty if long else q.bid_qty
                        quantity = D(o["remaining"])
                        if quantity > depth * D(".1"):
                            self._cancel(o, now_ms, "INSUFFICIENT_EXECUTABLE_DEPTH")
                        elif abs(price - D(s["entry_price"])) > D(s["entry_price"]) * self.policy.slippage * 2:
                            self._cancel(o, now_ms, "ENTRY_GAP_EXCEEDS_ALLOWANCE")
                        elif quantity * price * (1 + self.policy.taker_fee) > self.account(now_ms).equity * self.policy.gross_fraction:
                            self._cancel(o, now_ms, "FILL_EXCEEDS_GROSS_CAP")
                        else:
                            # Size-dependent additional impact, bounded by risk allowance.
                            impact = (D(".0005") + D(".01") * quantity / depth) * self.policy.slippage / D(".0015")
                            price = (q.ask if long else q.bid) * (1 + D(o["direction"]) * impact)
                            exit_depth = q.bid_qty if long else q.ask_qty
                            exit_impact = self.policy.slippage + D(".01") * quantity / exit_depth
                            stop = D(s["stop_loss"])
                            fill_risk = quantity * (abs(price-stop) + price*self.policy.taker_fee
                                                    + stop*(self.policy.taker_fee+exit_impact))
                            if fill_risk > D(o["decision"]["achievable_risk"]):
                                self._cancel(o, now_ms, "FILL_EXCEEDS_APPROVED_RISK")
                            else:
                                self._entry_fill(o, quantity, price, now_ms, f"quote-{q.time_ms}", False)
                self.journal.save(o)
            self.enforce_breaker(now_ms)

    def funding(self, observation: Funding, now_ms):
        if (observation.time_ms > now_ms or not observation.rate.is_finite()
                or not observation.mark.is_finite() or observation.mark <= 0):
            raise ValueError("future funding")
        with self.journal.transaction():
            # Closed orders also need late-arriving actual funding history.
            for o in self.journal.orders():
                if o["signal"]["symbol"] != observation.symbol:
                    continue
                rows = self.journal.db.execute("SELECT kind,quantity FROM fills WHERE order_id=? AND time_ms<=?",
                                               (o["id"], observation.time_ms)).fetchall()
                quantity = sum((D(r["quantity"]) * (1 if r["kind"] == "ENTRY" else -1) for r in rows), D(0))
                if quantity <= 0:
                    continue
                amount = -D(o["direction"]) * quantity * observation.mark * observation.rate
                identity = f"{o['id']}:{observation.time_ms}"
                result = self.journal.db.execute("INSERT OR IGNORE INTO funding VALUES(?,?,?,?)",
                                                (identity, o["id"], observation.time_ms, str(amount)))
                if result.rowcount:
                    self.journal.event(now_ms, "FUNDING", {"id": identity, "amount": str(amount), "funding_ms": observation.time_ms})
            self.enforce_breaker(now_ms)

    def tick(self, now_ms, finish=False):
        """Deadlines remain supervised even while public I/O is blocked/offline."""
        if not self.journal.get("reconciled", False):
            return
        with self.journal.transaction():
            for o in self.active():
                if now_ms >= o["signal"]["expires_ms"] or finish:
                    self._cancel(o, now_ms, "RUN_END" if finish else "ENTRY_TTL")
                if D(o["filled"]) > 0 and o["state"] != "CLOSING" and (
                        finish or now_ms >= o["first_fill_ms"] + o["signal"]["maximum_hold_seconds"] * 1000):
                    self._closing(o, now_ms, "RUN_END" if finish else "TIME_EXIT")
                    self.journal.save(o)
