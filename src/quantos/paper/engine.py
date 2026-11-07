"""Single-owner paper execution state machine; no network order interface exists."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from decimal import InvalidOperation

from .journal import Journal
from .risk import portfolio_risk_caps, Account, Policy, evaluate, usable_quote, valid_quote, quantity_step
from .types import D, Funding, Instrument, Quote, Signal, Trade, floor_step
from .strategies.manifest import manifest_for
from .stop_cost import price_stop

TERMINAL = {"CLOSED", "CANCELED", "EXPIRED", "REJECTED"}


def record_path(o, q: Quote, limit=480):
    """Keep a 1-minute mark-price OHLC path while a position is open, for offline exit (TP/SL) studies."""
    minute, price = q.received_ms // 60000, str(q.mark)
    path = o.setdefault("path", [])
    if path and path[-1][0] == minute:
        bar = path[-1]
        bar[1], bar[2], bar[3] = str(max(D(bar[1]), q.mark)), str(min(D(bar[2]), q.mark)), price
    elif len(path) < limit:
        path.append([minute, price, price, price])


@dataclass
class Faults:
    """Deterministic fake exchange failure switches, never used by natural runtime."""
    timeout_after_accept: bool = False
    crash_after_fill: bool = False
    reject_stop: bool = False
    reject_target: bool = False
    manual_position: bool = False


EXIT_IMPACT_CAP = D(".01")


class PaperEngine:
    latency_ms = 500
    participation = D(".01")

    def __init__(self, journal: Journal, policy: Policy, faults: Faults | None = None, adaptive=None):
        if faults is not None and journal.get("identity")["evidence"] != "synthetic":
            raise ValueError("fault injection is synthetic-only")
        self.journal, self.policy = journal, policy
        self.adaptive = adaptive  # AdaptiveSizer for profile pA; None keeps fixed-fraction sizing
        self.faults = faults or Faults()
        self.quotes: dict[str, Quote] = {}
        self.connected = False
        if (journal.profile == "pR") != policy.candidates_enabled:
            raise ValueError("research policy requires a separate pR journal")
        if policy.candidates_enabled:
            frozen = json.loads(json.dumps(asdict(policy), default=str, sort_keys=True))
            with journal.transaction():
                previous = journal.get("research_policy")
                if previous is not None and previous != frozen:
                    raise ValueError("research policy changed; use a new state directory")
                journal.set("research_policy", frozen)

    def active(self):
        return [o for o in self.journal.orders() if o["state"] not in TERMINAL]

    def entry_allowed(self):
        # exchange_hold: the live mirror's soft pause while the exchange is unreachable (in memory only).
        return (self.connected and self.journal.get("reconciled", False)
                and not self.journal.get("paused", False) and not getattr(self, "exchange_hold", False)
                and not getattr(self, "manual_pause", False))  # `quantos pause` (PAUSE file in the state dir)

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
        if first: self.journal.set("paused_ms", now_ms)
        for o in self.active():
            if D(o["filled"]) > 0 and o["state"] != "CLOSING":
                self._closing(o, now_ms, "LOSS_BREAKER")
            else:
                self._cancel(o, now_ms, "LOSS_BREAKER")
            self.journal.save(o)
        if first: self.journal.event(now_ms, "LOSS_BREAKER", {"equity": str(account.equity)})

    def kill(self, now_ms, reason="KILL_SWITCH"):
        """Pause entries, cancel pending intents and close every filled position."""
        with self.journal.transaction():
            first = self.journal.get("paused") != reason
            self.journal.set("paused", reason)
            for o in self.active():
                if D(o["filled"]) > 0 and o["state"] != "CLOSING":
                    self._closing(o, now_ms, reason)
                elif o["state"] != "CLOSING":
                    self._cancel(o, now_ms, reason)
                self.journal.save(o)
            if first: self.journal.event(now_ms, reason, {})

    def account(self, now_ms):
        equity = self.journal.cash()
        gross = margin = long_risk = short_risk = D(0)
        symbols, strategies = [], []
        for o in self.active():
            q = self.quotes.get(o["signal"]["symbol"])
            mark = q.mark if q else D(o["last_mark"])
            if D(o["filled"]) > 0:
                equity += D(o["filled"]) * (mark - D(o["average"])) * D(o["direction"])
            # Pending entries reserve capital too. Initial risk is retained
            # until quantity leaves the account, even after a stop tightens.
            reserved_qty = D(o["filled"]) + D(o["remaining"])
            notional = reserved_qty * max(mark, D(o["signal"]["entry_price"]))
            gross += notional
            margin += notional / D(o["decision"]["leverage"])
            reserved_risk = D(o["decision"]["achievable_risk"]) * reserved_qty / D(o["quantity"])
            if o["signal"]["side"] == "LONG": long_risk += reserved_risk
            else: short_risk += reserved_risk
            symbols.append(o["signal"]["symbol"])
            strategies.append(o["signal"]["strategy_id"])
        day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date().isoformat()
        high = max(D(self.journal.get("high_water", self.journal.get("identity")["initial_balance"])), equity)
        self.journal.set("high_water", str(high))
        if self.journal.get("day") != day:
            self.journal.set("day", day)
            self.journal.set("day_equity", str(equity))
        return Account(equity, D(self.journal.get("day_equity")), high,
                       len(self.active()), self.connected and self.journal.get("reconciled", False),
                       bool(self.journal.get("paused", False)),
                       gross, margin, long_risk, short_risk, tuple(symbols), tuple(strategies),
                       self.journal.cash())

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
                    # A persisted stop must agree with its durable update history.
                    latest = self.journal.db.execute(
                        "SELECT payload FROM events WHERE kind='STOP_UPDATED' "
                        "AND json_extract(payload,'$.id')=? ORDER BY id DESC LIMIT 1", (o["id"],)).fetchone()
                    expected = json.loads(latest[0])["new_stop"] if latest else o["signal"]["stop_loss"]
                    try:
                        current = D(o.get("stop_price", o["signal"]["stop_loss"]))
                        valid = (current.is_finite() and current == D(expected)
                                 and (current-D(o["signal"]["stop_loss"]))*D(o["direction"]) >= 0)
                    except (InvalidOperation, ValueError, TypeError):
                        valid = False
                    if not valid:
                        errors.append("STOP_STATE_MISMATCH")
                        continue
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
            limit = dict(getattr(self.policy, "loss_streak_pause", ())).get(signal.strategy_id)
            if limit and self.journal.get("loss_streaks", {}).get(signal.strategy_id, 0) >= limit:
                self.journal.event(now_ms, "REJECTED_RISK", {
                    "signal_id": signal.signal_id, "approved": False, "reason": "LOSS_STREAK_PAUSE"})
                return None
            cooldowns = self.journal.get("entry_cooldowns", {})
            if now_ms < cooldowns.get(f"{signal.strategy_id}:{signal.symbol}", 0):
                self.journal.event(now_ms, "REJECTED_RISK", {
                    "signal_id": signal.signal_id, "approved": False, "reason": "ENTRY_COOLDOWN"})
                return None
            self.quotes[quote.symbol] = quote
            policy = self.policy
            if self.adaptive is not None:
                a = self.account(now_ms)
                fraction, detail = self.adaptive.risk_fraction(signal.strategy_id, a.equity, a.high_water)
                self.journal.event(now_ms, "ADAPTIVE_SIZING", {"signal_id": signal.signal_id, **detail})
                if fraction <= 0:
                    self.journal.event(now_ms, "REJECTED_RISK", {"signal_id": signal.signal_id,
                                                                 "approved": False, "reason": detail["reason"]})
                    return None
                policy = replace(self.policy, risk_fraction=fraction)
            decision = evaluate(signal, instrument, quote, self.account(now_ms), policy, now_ms)
            self.journal.event(now_ms, "APPROVED" if decision.approved else "REJECTED_RISK",
                               {"signal_id": signal.signal_id, **asdict(decision)})
            if not decision.approved:
                return None
            if decision.reason == "NET_RR_NOT_APPLICABLE":
                self.journal.event(now_ms, decision.reason, {"signal_id": signal.signal_id})
            order_id = hashlib.sha256(f"{self.journal.profile}:{signal.signal_id}".encode()).hexdigest()[:32]
            order = {"id": order_id, "signal": asdict(signal), "instrument": asdict(instrument),
                     "decision": asdict(decision), "quantity": str(decision.quantity),
                     "remaining": str(decision.quantity), "filled": "0", "average": "0",
                     "direction": "1" if signal.side == "LONG" else "-1",
                     "state": "CREATED", "created_ms": now_ms,
                     "eligible_ms": now_ms + self.latency_ms, "first_fill_ms": None,
                     "last_trade_id": -1, "queue": str(decision.quantity),
                     "stop_active": False, "target_active": False,
                     "stop_price": str(signal.stop_loss),
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
        has_target = o["signal"]["take_profit"] is not None
        if self.faults.reject_stop or (has_target and self.faults.reject_target):
            o["stop_active"] = o["target_active"] = False
            self.journal.set("paused", "PROTECTION_REJECTED")
            self.journal.event(now_ms, "PROTECTION_REJECTED", {"id": o["id"]})
            self._closing(o, now_ms, "PROTECTION_FAILURE")
        else:
            o["stop_active"] = True
            o["target_active"] = has_target
            o["state"] = "PROTECTED"
            self.journal.event(now_ms, "PROTECTED", {"id": o["id"], "quantity": o["filled"],
                                                     "stop_id": o["id"] + "-sl",
                                                     "target_id": o["id"] + "-tp" if has_target else None})

    def _tighten_stop(self, o, candidate, now_ms, reason, bar_ms=None):
        tick = D(o["instrument"]["tick"])
        long = o["direction"] == "1"
        candidate = floor_step(candidate, tick) if long else -floor_step(-candidate, tick)
        old = D(o.get("stop_price", o["signal"]["stop_loss"]))
        if (not candidate.is_finite()
                or not D(o["instrument"]["min_price"]) <= candidate <= D(o["instrument"]["max_price"])
                or (candidate-old)*D(o["direction"]) <= 0):
            return
        o["stop_price"] = str(candidate)
        self.journal.event(now_ms, "STOP_UPDATED", {"id": o["id"], "old_stop": str(old),
            "new_stop": str(candidate), "reason": reason, "closed_bar_ms": bar_ms})

    def channel_stops(self, snapshot, now_ms):
        """Only completed, contiguous 4h bars can tighten a channel protection."""
        from .strategies.indicators import closed_bars
        bars = closed_bars(snapshot, "4h", 20)
        if bars is None or snapshot.at_ms > now_ms or not self.journal.get("reconciled", False):
            return
        with self.journal.transaction():
            for o in self.active():
                s = o["signal"]
                m = manifest_for(s["strategy_id"], s["strategy_version"])
                if (s["symbol"] != snapshot.instrument.symbol or not m or m.exit_model != "channel"
                        or D(o["filled"]) <= 0 or o["state"] == "CLOSING"
                        or bars[-1].close_ms <= o.get("channel_bar_ms", o["first_fill_ms"])):
                    continue
                price = min(c.low for c in bars[-20:]) if s["side"] == "LONG" else max(c.high for c in bars[-20:])
                self._tighten_stop(o, price, now_ms, "CHANNEL", bars[-1].close_ms)
                o["channel_bar_ms"] = bars[-1].close_ms
                self.journal.save(o)

    def _trailing_stop(self, o, q, now_ms):
        s = o["signal"]
        m = manifest_for(s["strategy_id"], s["strategy_version"])
        if not m or m.exit_model != "trailing":
            return
        direction, entry = D(o["direction"]), D(o["average"])
        risk = abs(entry-D(s["stop_loss"]))
        price = q.bid if direction > 0 else q.ask
        best = D(o.get("best_exit_price", o["average"]))
        best = max(best, price) if direction > 0 else min(best, price)
        o["best_exit_price"] = str(best)
        if risk > 0 and (best-entry)*direction >= risk:
            self._tighten_stop(o, best-direction*D(2)*risk, now_ms, "TRAILING")

    def _entry_fill(self, o, quantity, price, now_ms, source, maker):
        quantity = min(quantity, D(o["remaining"]))
        if quantity <= 0:
            return
        fill_id = f"{o['id']}:ENTRY:{source}"
        if self.journal.db.execute("SELECT 1 FROM fills WHERE id=?", (fill_id,)).fetchone():
            return
        fee = price * quantity * (self.policy.maker_fee if maker else self.policy.taker_fee)
        if self.policy.candidates_enabled or self.policy.max_positions > 1:
            # Recheck the *whole* reserved portfolio at execution, including
            # pending intents and immediate entry fee/mark-to-market loss.
            account = self.account(now_ms)
            q = self.quotes.get(o["signal"]["symbol"])
            mark = q.mark if q else D(o["last_mark"])
            reserve_price = max(mark, D(o["signal"]["entry_price"]))
            extra = quantity * max(D(0), price-reserve_price)
            equity_after = account.equity-fee + quantity*(mark-price)*D(o["direction"])
            gross_after = account.gross_notional + extra
            margin_after = account.margin_used + extra/D(o["decision"]["leverage"])
            if (equity_after <= 0 or gross_after > equity_after*self.policy.gross_fraction
                    or margin_after > equity_after):
                self._cancel(o, now_ms, "FILL_EXCEEDS_PORTFOLIO_CAP")
                return
            direction_cap, total_cap = portfolio_risk_caps(self.policy)
            used_direction = account.long_risk if D(o["direction"]) > 0 else account.short_risk
            if (used_direction > equity_after * direction_cap
                    or account.long_risk + account.short_risk > equity_after * total_cap):
                self._cancel(o, now_ms, "FILL_EXCEEDS_PORTFOLIO_RISK_CAP")
                return
        old = D(o["filled"])
        o["average"] = str((D(o["average"]) * old + price * quantity) / (old + quantity))
        o["filled"] = str(old + quantity)
        o["remaining"] = str(D(o["remaining"]) - quantity)
        o["first_fill_ms"] = o["first_fill_ms"] or now_ms
        if old == 0 and o["signal"]["strategy_id"] in {"crash_flush_e2", "alt_dip_reversal"}:
            cooldowns = self.journal.get("entry_cooldowns", {})
            seconds = (86400 if o["signal"]["strategy_id"] == "crash_flush_e2"
                       else o["signal"]["maximum_hold_seconds"])
            cooldowns[f'{o["signal"]["strategy_id"]}:{o["signal"]["symbol"]}'] = now_ms + seconds*1000
            self.journal.set("entry_cooldowns", cooldowns)
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
                    ins = Instrument(**{k: v if k in {"symbol", "margin_asset", "status", "base_asset"} else D(v)
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
        # Capped: 0.1 lot showing on a thin ask once simulated a 7% exit that never happened (11-04).
        impact = min(self.policy.slippage + D(".01") * quantity / depth, EXIT_IMPACT_CAP)
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
        # Consecutive losing closes per strategy (net of this exit's fee), for policy.loss_streak_pause.
        streaks = self.journal.get("loss_streaks", {})
        sid = o["signal"]["strategy_id"]
        streaks[sid] = streaks.get(sid, 0) + 1 if pnl - fee < 0 else 0
        self.journal.set("loss_streaks", streaks)

    def void_entry(self, order_id, now_ms, reason):
        """The exchange never opened this position: drop its paper fills so cash and P&L match Binance."""
        with self.journal.transaction():
            row = self.journal.db.execute("SELECT payload FROM orders WHERE id=?", (order_id,)).fetchone()
            if row is None: return False
            o = json.loads(row[0])
            self.journal.db.execute("DELETE FROM fills WHERE order_id=?", (order_id,))
            o.update(state="CANCELED", filled="0", remaining="0", stop_active=False, target_active=False)
            self.journal.save(o)
            self.journal.event(now_ms, "VOIDED", {"id": order_id, "reason": reason})
            return True

    def close_external(self, order_id, now_ms, reason="EXCHANGE_CLOSED"):
        """The exchange position is already gone (its stop filled first): close the paper side too, so the
        mirror and the books stop disagreeing. The exit fill is then corrected to the real one."""
        with self.journal.transaction():
            for o in self.active():
                if o["id"] == order_id and D(o["filled"]) > 0 and o["state"] != "CLOSING":
                    self._closing(o, now_ms, reason)
                    self.journal.save(o)
                    return True
        return False

    def check_realised_risk(self, order_id, now_ms, price, quantity, entry_fee=None):
        """Real exchange fills must remain within 1.2x the approved risk budget.

        Returns False after requesting a close. The mirror executes that close;
        a closing/closed order is never reopened by a late fee correction.
        """
        with self.journal.transaction():
            o = self.query_order(order_id)
            if o["state"] in TERMINAL or o["state"] == "CLOSING":
                return False
            if not quantity.is_finite() or quantity <= 0:
                raise ValueError("invalid real fill quantity")
            approved = D(o["decision"]["requested_risk"])
            if not approved.is_finite() or approved <= 0:
                raise ValueError("missing approved risk")
            fee_rate = self.policy.taker_fee
            if entry_fee is not None:
                if not entry_fee.is_finite() or entry_fee < 0:
                    raise ValueError("invalid real entry fee")
                fee_rate = max(fee_rate, entry_fee / (price * quantity))
            q = self.quotes.get(o["signal"]["symbol"])
            slip = 2 * price * self.policy.slippage
            if q is not None and usable_quote(q, now_ms):
                depth = q.bid_qty if o["signal"]["side"] == "LONG" else q.ask_qty
                slip = max(slip, q.ask - q.bid,
                           price * (self.policy.slippage + D(".01") * quantity / depth))
            cost = price_stop(price, D(o["stop_price"]), o["signal"]["side"],
                              slip, fee_rate, max(self.policy.taker_fee, fee_rate))
            realised = quantity * cost.loss_per_unit
            detail = {"id": order_id, "approved": str(approved), "realised": str(realised),
                      "quantity": str(quantity), "entry_price": str(price),
                      "limit": str(approved * D("1.2"))}
            # Persist measured exposure for subsequent portfolio approvals.
            o["decision"]["achievable_risk"] = str(max(D(o["decision"]["achievable_risk"]), realised))
            if realised > approved * D("1.2"):
                self._closing(o, now_ms, "REALISED_RISK_EXCEEDED")
                self.journal.event(now_ms, "REALISED_RISK_EXCEEDED", detail)
                self.journal.save(o)
                return False
            self.journal.event(now_ms, "REALISED_RISK_CHECKED", detail)
            self.journal.save(o)
            return True

    def book_real_fills(self, order_id, now_ms, entry=None, exit=None):
        """Replace simulated fill prices/fees with the exchange's. entry/exit = (price, fee or None);
        a None fee keeps the engine's rate at the real price. Cash and equity derive from fills, so they
        follow. Re-applying the same values is a no-op. Returns {"changed", "has_exit", ...} (the
        BOOKS_CORRECTION payload when changed), or None when the order has no entry fill."""
        db = self.journal.db
        with self.journal.transaction():
            row = db.execute("SELECT payload FROM orders WHERE id=?", (order_id,)).fetchone()
            entries = db.execute("SELECT id,quantity,price,fee FROM fills WHERE order_id=? AND kind='ENTRY'",
                                 (order_id,)).fetchall()
            if row is None or not entries:
                return None
            o = json.loads(row[0])
            total = sum(D(r["quantity"]) for r in entries)
            old_avg = sum(D(r["quantity"]) * D(r["price"]) for r in entries) / total
            old_entry_fee = sum(D(r["fee"]) for r in entries)
            avg, entry_fee = old_avg, old_entry_fee
            if entry is not None:
                avg = D(str(entry[0]))
                entry_fee = old_entry_fee / old_avg * avg if entry[1] is None else D(str(entry[1]))
            x = db.execute("SELECT quantity,price,fee,pnl FROM fills WHERE id=?", (order_id + ":EXIT",)).fetchone()
            out = {"id": order_id, "symbol": o["signal"]["symbol"], "source": "exchange fills",
                   "old_entry_price": str(old_avg), "new_entry_price": str(avg),
                   "old_entry_fee": str(old_entry_fee), "new_entry_fee": str(entry_fee)}
            changed = (avg, entry_fee) != (old_avg, old_entry_fee)
            if x is not None:
                qty, old_px, old_fee, old_pnl = D(x["quantity"]), D(x["price"]), D(x["fee"]), D(x["pnl"])
                px, fee = old_px, old_fee
                if exit is not None:
                    px = D(str(exit[0]))
                    fee = old_fee / old_px * px if exit[1] is None else D(str(exit[1]))
                pnl = qty * (px - avg) * D(o["direction"])
                out.update(old_price=str(old_px), new_price=str(px), old_fee=str(old_fee), new_fee=str(fee),
                           old_pnl=str(old_pnl), new_pnl=str(pnl))
                changed = changed or (px, fee, pnl) != (old_px, old_fee, old_pnl)
            out["has_exit"] = x is not None
            if entry is not None and x is None:
                out["risk_safe"] = self.check_realised_risk(order_id, now_ms, avg, total, entry_fee)
                # Risk may have moved this order to CLOSING. Preserve that state
                # when the accounting correction saves the new average below.
                o = self.query_order(order_id)
            if not changed:
                return {**out, "changed": False}
            out["changed"] = True
            before = self.journal.cash()
            for r in entries:  # fee split by quantity across entry fills
                db.execute("UPDATE fills SET price=?,fee=? WHERE id=?",
                           (str(avg), str(entry_fee * D(r["quantity"]) / total), r["id"]))
            o["average"] = str(avg)
            self.journal.save(o)
            for eid, raw in db.execute("SELECT id,payload FROM events WHERE kind IN ('FILLED','PARTIAL') "
                                       "AND json_extract(payload,'$.id')=?", (order_id,)).fetchall():
                p = json.loads(raw)
                p.update(price=str(avg), fee=str(entry_fee * D(p.get("quantity", total)) / total))
                db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(p), eid))
            if x is not None:
                db.execute("UPDATE fills SET price=?,fee=?,pnl=? WHERE id=?", (str(px), str(fee), str(pnl), order_id + ":EXIT"))
                for eid, raw in db.execute("SELECT id,payload FROM events WHERE kind='CLOSED' "
                                           "AND json_extract(payload,'$.id')=?", (order_id,)).fetchall():
                    db.execute("UPDATE events SET payload=? WHERE id=?",
                               (json.dumps({**json.loads(raw), "price": str(px), "pnl_gross": str(pnl)}), eid))
                was_loss, is_loss = old_pnl - old_fee < 0, pnl - fee < 0
                if was_loss != is_loss:  # only the latest close can flip the streak it set
                    streaks, sid = self.journal.get("loss_streaks", {}), o["signal"]["strategy_id"]
                    streaks[sid] = streaks.get(sid, 0) + 1 if is_loss else 0
                    self.journal.set("loss_streaks", streaks)
            out.update(old_cash=str(before), new_cash=str(self.journal.cash()))
            self.journal.event(now_ms, "BOOKS_CORRECTION", out)
            return out

    def quote(self, q: Quote, now_ms):
        if not self.journal.get("reconciled", False):
            return
        if not usable_quote(q, now_ms):
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
                if D(o["filled"]) > 0:
                    record_path(o, q)
                if now_ms >= s["expires_ms"] and D(o["remaining"]) > 0:
                    self._cancel(o, now_ms, "ENTRY_TTL")
                if o["state"] == "CLOSING" and q.received_ms >= o["exit_after_ms"]:  # a quiet book is current as of receipt
                    self._exit(o, q, now_ms)
                elif D(o["filled"]) > 0 and o["state"] != "CLOSING":
                    long = s["side"] == "LONG"
                    self._trailing_stop(o, q, now_ms)
                    level = D(o.get("stop_price", s["stop_loss"]))
                    stop = q.mark <= level if long else q.mark >= level
                    target = s["take_profit"] is not None and (q.bid >= D(s["take_profit"]) if long else q.ask <= D(s["take_profit"]))
                    if stop or target or now_ms >= o["first_fill_ms"] + s["maximum_hold_seconds"] * 1000:
                        self._closing(o, now_ms, "SL" if stop else "TP" if target else "TIME_EXIT")
                elif (self.entry_allowed() and o["state"] not in TERMINAL and D(o["remaining"]) > 0
                      and s["entry_type"] in {"MARKET", "STOP"} and q.time_ms >= o["eligible_ms"]
                      and valid_quote(q, now_ms)):
                    long = s["side"] == "LONG"
                    if s["entry_type"] == "STOP" and o["trigger_ms"] is None:
                        if (q.ask >= D(s["entry_price"]) if long else q.bid <= D(s["entry_price"])):
                            o["trigger_ms"] = now_ms
                    elif s["entry_type"] == "MARKET" or q.time_ms >= o["trigger_ms"] + self.latency_ms:
                        price = (q.ask if long else q.bid) * (1 + D(o["direction"]) * self.policy.slippage)
                        # Reject gaps beyond risk allowance instead of inventing fills at trigger.
                        depth = q.ask_qty if long else q.bid_qty
                        quantity = D(o["remaining"])
                        # Default: <=10% of top-of-book. Profiles may set depth_fraction (e.g. 1.0) because thin
                        # USDC books made every $60 pM10 entry cancel in the 10-31 run (top level only is observed).
                        if quantity > depth * getattr(self.policy, "depth_fraction", D(".1")):
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
                            direction = D(o["direction"])
                            distance = (price-stop)*direction
                            costs = price*(2*self.policy.taker_fee + 2*max(self.policy.slippage, impact, exit_impact))
                            fill_risk = quantity * (abs(price-stop) + price*self.policy.taker_fee
                                                    + stop*(self.policy.taker_fee+exit_impact))
                            if distance <= 0:
                                self._cancel(o, now_ms, "INVALID_BRACKET")
                            elif s["take_profit"] is None and costs > D(".25")*distance:
                                self._cancel(o, now_ms, "COSTS_TOO_HIGH_FOR_STOP")
                            elif fill_risk > (max(D(o["decision"]["achievable_risk"]), D(o["decision"]["requested_risk"]))
                                              if hasattr(self.policy, "risk_headroom") else D(o["decision"]["achievable_risk"])):
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

    def _referee(self, now_ms):
        """pA only: let the adaptive referee lift a loss-breaker pause when the evidence says bad luck has passed."""
        if self.journal.get("paused") != "LOSS_BREAKER" or self.active():
            return
        since = self.journal.get("paused_ms", now_ms)
        errors = self.journal.db.execute("SELECT COUNT(*) FROM events WHERE kind='ERROR' AND time_ms>=?",
                                         (since,)).fetchone()[0]
        verdict, why = self.adaptive.referee(since, errors)
        if verdict != self.journal.get("referee_verdict"):
            self.journal.set("referee_verdict", verdict)
            self.journal.event(now_ms, "REFEREE", {"verdict": verdict, "reasons": why})
        if verdict == "AUTO_RESUME":
            equity = self.account(now_ms).equity
            for key, value in (("high_water", str(equity)), ("day_equity", str(equity)),
                               ("auto_resumed_ms", now_ms), ("referee_verdict", None)):
                self.journal.set(key, value)
            self.journal.set("paused", False)
            self.journal.event(now_ms, "AUTO_RESUMED", {"equity": str(equity)})

    def tick(self, now_ms, finish=False):
        """Deadlines remain supervised even while public I/O is blocked/offline."""
        if not self.journal.get("reconciled", False):
            return
        with self.journal.transaction():
            if self.adaptive is not None:
                self.adaptive.learn(now_ms)
                self._referee(now_ms)
            for o in self.active():
                if now_ms >= o["signal"]["expires_ms"] or finish:
                    self._cancel(o, now_ms, "RUN_END" if finish else "ENTRY_TTL")
                if D(o["filled"]) > 0 and o["state"] != "CLOSING" and (
                        finish or now_ms >= o["first_fill_ms"] + o["signal"]["maximum_hold_seconds"] * 1000):
                    self._closing(o, now_ms, "RUN_END" if finish else "TIME_EXIT")
                    self.journal.save(o)
