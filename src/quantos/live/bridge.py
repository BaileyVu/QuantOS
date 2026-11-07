"""Mirror the paper engine's positions onto Binance (dry-run / testnet / gated mainnet).

The paper engine stays the single decision maker. Each cycle the mirror:
1. opens an exchange position (market entry + exchange-native reduce-only
   STOP_MARKET) for every newly filled paper position;
2. replaces the exchange stop when the paper stop tightens;
3. closes (reduce-only) and cancels protection when the paper position closes;
4. reconciles exchange positions/protection with what it placed. Two failed
   reconciliations in a row, or any protection failure, engage the kill switch
   and flatten. A transport failure (network, timeout, 5xx, rate limit) is not a
   failed reconciliation: it holds new entries, retries with backoff, and kills
   only once it has lasted TRANSIENT_KILL_MS over TRANSIENT_KILL_CHECKS checks. Auth failures (e.g. a dynamic home IP falling off the API key
   whitelist) raise a critical alert.
State lives in `<state>/mirror.json`, so a restart neither re-enters nor forgets.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..paper.types import D, Instrument
from .client import BinanceError, RateLimited
from .kill import KillSwitch, flatten
from .orders import (ProtectionFailed, client_order_id, place_with_protection, reduce_only_close,
                     stop_market_protect, submit)
from .reconcile import reconcile

AUTH_CODES = {-2015, -2014, -1022, -2008}
# A home connection blips; two failed 30s checks used to flatten everything (2026-11-03, -$1.73).
TRANSIENT_KILL_MS, TRANSIENT_KILL_CHECKS = 120_000, 4
TRANSIENT_RETRY_MS = (5_000, 10_000, 20_000, 30_000)
STOP_RETRY_MS = (30_000, 60_000, 120_000, 300_000)
# Books take the real fill prices and fees (userTrades) instead of simulated ones; demo P&L drifted
# from Binance (+$3.05 booked vs +$4.04 real, 11-05). Retried with backoff while fills are not visible yet.
SETTLE_RETRY_MS = (5_000, 15_000, 60_000, 300_000)
SETTLE_GIVE_UP = 12
# A kill's one flatten failed on the network on 11-03 and NEAR/ADA stayed open 3.5h; retry until verified flat.
FLATTEN_RETRY_MS = 30_000


def is_auth_error(error: Exception) -> bool:
    return isinstance(error, BinanceError) and (
        getattr(error, "code", None) in AUTH_CODES or getattr(error, "status", None) in (401, 403))


def is_transient_error(error: Exception) -> bool:
    """The exchange could not be reached or asked us to back off; nothing was compared."""
    if isinstance(error, RateLimited): return True
    if isinstance(error, BinanceError): return getattr(error, "status", 0) >= 500
    return isinstance(error, (ConnectionError, TimeoutError, OSError))


def _instrument(fields: dict) -> Instrument:
    return Instrument(**{k: v if k in {"symbol", "margin_asset", "status", "base_asset"} else D(v)
                         for k, v in fields.items()})


def _vwap(trades, qty, asset):
    """(average price, commission) over the first `qty` of these trades (all when None). Commission is None
    when any part was paid in another asset (e.g. BNB), so the engine keeps its fee rate."""
    left, value, fee, filled = qty, D(0), D(0), D(0)
    other = False
    for t in trades:
        q = D(str(t["qty"]))
        take = q if left is None else min(q, left - filled)
        if take <= 0: break
        value += take * D(str(t["price"]))
        fee += D(str(t["commission"])) * take / q
        other = other or t.get("commissionAsset") != asset
        filled += take
    return value / filled, None if other else fee


class ExchangeMirror:
    def __init__(self, client, run_id: str, state_dir: Path, alert=None, margin_type="ISOLATED"):
        self.client, self.run_id, self.state = client, run_id, Path(state_dir)
        self.alert = alert or (lambda level, key, message: None)
        self.kill = KillSwitch(self.state)
        self.margin_type, self.failures = margin_type, 0
        # Soft pause: no new entries while the exchange is unreachable; open positions stay managed.
        self.hold, self.transient, self.transient_since = False, 0, None
        self.flat_confirmed, self.next_flatten_ms = False, 0
        self.settle_retry = {}  # order id -> (next attempt ms, failures)
        self.stop_retry = {}  # order id -> (next attempt ms, failures); in memory, a restart retries at once
        # Reconciliation costs ~3 signed calls; every 2s it stalled the runtime's main loop until
        # quotes went stale. Check every 30s, and right after any exchange action.
        self.check_every_ms, self.last_check_ms = 30_000, 0
        self.path = self.state / "mirror.json"
        self.book = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.book, sort_keys=True))
        os.replace(tmp, self.path)

    def _guard(self, key, fn):
        try:
            result = fn()
            if key == "reconcile": self.alert("recovered", "auth", "")
            return result, None
        except Exception as error:
            if is_auth_error(error):
                self.alert("critical", "auth", f"Binance rejected the API key ({error}); check the IP whitelist")
            return None, error

    def sync(self, engine, now_ms: int, events=()) -> dict:
        """`events`: account events from the user-data stream since the last sync (may be empty)."""
        if self.kill.active:
            if not self.flat_confirmed and now_ms >= self.next_flatten_ms:
                self.next_flatten_ms = now_ms + FLATTEN_RETRY_MS
                self._flatten()
            return {"skipped": "KILL"} if self.flat_confirmed else {"skipped": "KILL", "flat": False}
        self.flat_confirmed = False
        self._on_events(engine, events, now_ms)
        before = json.dumps(self.book, sort_keys=True)
        live = {o["id"]: o for o in engine.active() if D(o["filled"]) > 0 and o["state"] != "CLOSING"}
        for oid, o in live.items():
            if oid in self.book:
                continue
            s, ins = o["signal"], _instrument(o["instrument"])
            side = "BUY" if s["side"] == "LONG" else "SELL"
            self._guard("setup", lambda: self.client.set_margin_type(ins.symbol, self.margin_type))
            self._guard("setup", lambda: self.client.set_leverage(ins.symbol, D(o["decision"]["leverage"])))
            placed, error = self._guard("entry", lambda: place_with_protection(
                self.client, ins, side, D(o["filled"]), D(o["stop_price"]), self.run_id, oid[:20]))
            if error is not None:
                self.book[oid] = {"symbol": ins.symbol, "state": "FAILED", "error": type(error).__name__}
                self._save()
                if isinstance(error, ProtectionFailed):
                    self._halt(f"protection failed on {ins.symbol}: {error}")
                else:
                    self.alert("critical", f"entry:{oid}", f"exchange entry failed on {ins.symbol}: {error}")
                continue
            qty = D(str(placed.entry.get("executedQty", o["filled"])))
            stop_cid = placed.sl.get("clientAlgoId") or placed.sl.get("clientOrderId")
            self.book[oid] = {"symbol": ins.symbol, "side": side, "qty": str(qty), "stop": o["stop_price"],
                              "stop_cid": stop_cid, "stop_n": 0, "state": "OPEN", "instrument": o["instrument"],
                              "opened_ms": now_ms, "entry_order": placed.entry.get("orderId"), "booked": ""}
            self._save()
        for oid, m in list(self.book.items()):
            if m["state"] != "OPEN":
                continue
            ins, opp = _instrument(m["instrument"]), "SELL" if m["side"] == "BUY" else "BUY"
            o = live.get(oid)
            if o is None:  # paper closed or closing: flatten this leg and drop protection
                _, error = self._guard("close", lambda: submit(self.client, reduce_only_close(
                    m["symbol"], opp, D(m["qty"]), client_order_id(self.run_id, oid[:20], "close"), ins)))
                self._cancel_stop(m)
                if error is not None and self._flat_on_exchange(m["symbol"]):
                    error = None  # the exchange stop filled first; the reduce-only close had nothing to close
                m["state"] = "CLOSED" if error is None else "CLOSE_FAILED"
                if error is not None:
                    self._halt(f"reduce-only close failed on {m['symbol']}: {error}")
            elif D(o["stop_price"]) != D(m["stop"]) and now_ms >= self.stop_retry.get(oid, (0, 0))[0]:
                # The replacement is reduce-only with the position's quantity: Binance allows only one
                # closePosition stop per side (-4130), so a second closePosition stop was rejected on every
                # trailing move (11-05, NEAR). Place first, then cancel, so the position is never unprotected.
                fails = self.stop_retry.get(oid, (0, 0))[1]
                n = m["stop_n"] + 1
                cid = client_order_id(self.run_id, f"{oid[:16]}-{n}" + (f"-r{fails}" if fails else ""), "sl")
                placed, error = self._guard("stop", lambda: submit(self.client, stop_market_protect(
                    m["symbol"], opp, D(o["stop_price"]), cid, ins, qty=D(m["qty"]))))
                if error is None:
                    self._cancel_stop(m)
                    m.update(stop=o["stop_price"], stop_n=n,
                             stop_cid=placed.get("clientAlgoId") or placed.get("clientOrderId"))
                    if self.stop_retry.pop(oid, None): self.alert("recovered", f"stop:{oid}", "")
                else:
                    # Back off instead of retrying on every 2s sync: each failed signed call blocked the
                    # main loop and the feed queue fell up to 31 minutes behind (11-05).
                    delay = STOP_RETRY_MS[min(fails, len(STOP_RETRY_MS) - 1)]
                    self.stop_retry[oid] = (now_ms + delay, fails + 1)
                    if fails in (0, 2):
                        self.alert("warn" if fails == 0 else "critical", f"stop:{oid}",
                                   f"stop update failed {fails + 1}x on {m['symbol']}; old stop kept, "
                                   f"retrying with backoff: {error}")
            self._save()
        if hasattr(engine, "book_real_fills") and getattr(self.client, "mode", "") != "dry-run":
            for oid, m in list(self.book.items()):
                due = m.get("booked") == "" if m["state"] == "OPEN" else (m["state"] == "CLOSED" and m.get("booked") != "all")
                if (m.get("opened_ms") and due
                        and now_ms >= self.settle_retry.get(oid, (0, 0))[0]):
                    self._settle(engine, oid, m, now_ms)
        every = (TRANSIENT_RETRY_MS[min(self.transient, len(TRANSIENT_RETRY_MS)) - 1]
                 if self.transient else self.check_every_ms)
        if json.dumps(self.book, sort_keys=True) == before and now_ms - self.last_check_ms < every:
            engine.exchange_hold = self.hold
            return {"ok": True, "skipped": "INTERVAL"}
        self.last_check_ms = now_ms
        result = self.check(now_ms)
        engine.exchange_hold = self.hold
        return result

    def _on_events(self, engine, events, now_ms):
        """React to the account stream instead of waiting for the 30s REST check: a position that went flat
        on the exchange (its stop filled) closes the paper side now, and a fill on a mirrored symbol
        reconciles and books its real price at this sync."""
        for ev in events:
            if ev.get("e") == "ORDER_TRADE_UPDATE":
                o = ev.get("o", {})
                if o.get("X") in ("FILLED", "PARTIALLY_FILLED"):
                    for oid, m in self.book.items():
                        if m["symbol"] == o.get("s") and m["state"] in ("OPEN", "CLOSED"):
                            self.settle_retry.pop(oid, None)
                            self.last_check_ms = 0
            elif ev.get("e") == "ACCOUNT_UPDATE":
                flat = {p["s"] for p in ev.get("a", {}).get("P", []) if D(str(p.get("pa", "0"))) == 0}
                for oid, m in self.book.items():
                    if m["state"] == "OPEN" and m["symbol"] in flat:
                        self.last_check_ms = 0
                        if hasattr(engine, "close_external") and engine.close_external(oid, now_ms, "EXCHANGE_CLOSED"):
                            self.alert("warn", f"exchange_closed:{oid}", f"{m['symbol']} closed on the exchange "
                                       "(stop filled first); paper position closed to match")

    def _flat_on_exchange(self, symbol) -> bool:
        rows, error = self._guard("close", lambda: self.client.position_risk(symbol))
        return error is None and all(D(str(r["positionAmt"])) == 0 for r in rows if r.get("symbol") == symbol)

    def _settle(self, engine, oid, m, now_ms):
        """Book the leg's real entry (once open) and real exit (once closed) from Binance's trade list."""
        fails = self.settle_retry.get(oid, (0, 0))[1]
        trades, error = self._guard("fills", lambda: self.client.user_trades(m["symbol"], m["opened_ms"] - 60_000))
        done = False
        try:
            if error is not None: raise error
            asset = m["instrument"].get("margin_asset")
            entry = [t for t in trades if str(t.get("orderId")) == str(m.get("entry_order"))]
            if m["booked"] == "" and entry:
                engine.book_real_fills(oid, now_ms, entry=_vwap(entry, None, asset))
                m["booked"] = "entry"
            if m["state"] == "CLOSED" and m["booked"] == "entry":
                since = min((int(t["time"]) for t in entry), default=m["opened_ms"] - 60_000)
                exits = sorted((t for t in trades if t.get("side") != m["side"] and int(t["time"]) >= since
                                and str(t.get("orderId")) != str(m.get("entry_order"))), key=lambda t: int(t["time"]))
                if sum(D(str(t["qty"])) for t in exits) >= D(m["qty"]):
                    booked = engine.book_real_fills(oid, now_ms, exit=_vwap(exits, D(m["qty"]), asset))
                    if booked and booked["has_exit"]:  # else the paper exit is not written yet: retry
                        m["booked"] = "all"
            done = m["booked"] == "all" or (m["state"] == "OPEN" and m["booked"] == "entry")
        except Exception as e:  # never let bookkeeping break order management; retry with backoff
            error = e
        self._save()
        if done:
            self.settle_retry.pop(oid, None)
        elif fails + 1 >= SETTLE_GIVE_UP:
            m["booked"] = "all"  # stop asking; the simulated values stay
            self._save()
            self.settle_retry.pop(oid, None)
            self.alert("warn", f"fills:{oid}", f"real fills not found for {m['symbol']}; books keep simulated "
                                               f"prices for this trade ({error or 'no matching trades'})")
        else:
            self.settle_retry[oid] = (now_ms + SETTLE_RETRY_MS[min(fails, len(SETTLE_RETRY_MS) - 1)], fails + 1)

    def _cancel_stop(self, m):
        cancel = (self.client.cancel_algo_order if getattr(self.client, "conditional_api", "") == "algo"
                  else lambda cid: self.client.cancel_order(m["symbol"], cid))
        self._guard("cancel", lambda: cancel(m["stop_cid"]))

    def check(self, now_ms: int | None = None) -> dict:
        if getattr(self.client, "mode", "") == "dry-run":  # nothing real to compare against
            return {"ok": True, "report": "DRY_RUN_UNVERIFIED", "error": ""}
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        expected = {m["symbol"]: D(m["qty"]) * (1 if m["side"] == "BUY" else -1)
                    for m in self.book.values() if m["state"] == "OPEN"}
        orders = {m["stop_cid"] for m in self.book.values() if m["state"] == "OPEN"}
        report, error = self._guard("reconcile", lambda: reconcile(self.client, expected, orders))
        if error is not None and is_transient_error(error):
            return self._transient(error, now_ms)
        ok = error is None and report.ok
        if self.hold or self.transient:
            self.hold, self.transient, self.transient_since = False, 0, None
            self.alert("recovered", "exchange_hold", "")
        self.failures = 0 if ok else self.failures + 1
        if not ok:
            self.alert("warn" if self.failures < 2 else "critical", "reconcile",
                       f"exchange reconciliation failed: {error or report}")
            if self.failures >= 2:
                self._halt("two consecutive reconciliation failures")
        return {"ok": ok, "report": None if report is None else report.__dict__, "error": str(error or "")}

    def _transient(self, error, now_ms: int) -> dict:
        self.transient += 1
        self.transient_since = now_ms if self.transient_since is None else self.transient_since
        lasted = now_ms - self.transient_since
        if not self.hold:
            self.hold = True
            self.alert("warn", "exchange_hold", f"exchange unreachable; new entries held: {error}")
        if self.transient >= TRANSIENT_KILL_CHECKS and lasted >= TRANSIENT_KILL_MS:
            self.alert("critical", "reconcile", f"exchange unreachable for {lasted // 1000}s over "
                                                f"{self.transient} checks: {error}")
            self._halt(f"exchange unreachable for {lasted // 1000}s ({error})")
        return {"ok": False, "report": None, "error": str(error), "transient": True, "hold": True}

    def _halt(self, reason: str):
        self.kill.engage(reason)
        self.alert("critical", "kill_switch", f"exchange kill switch engaged: {reason}")
        self._flatten()

    def _flatten(self):
        """Close everything on the exchange, then verify it. Unverified stays critical and is retried."""
        if getattr(self.client, "mode", "") == "dry-run":
            self.flat_confirmed = True
            return
        instruments = {m["symbol"]: _instrument(m["instrument"]) for m in self.book.values() if "instrument" in m}
        result, error = self._guard("flatten", lambda: flatten(self.client, None, instruments, tag=self.run_id))
        left, check_error = self._guard("flatten", self._exchange_exposure)
        if error is None and check_error is None and not result.errors and not left:
            self.flat_confirmed = True
            for m in self.book.values():
                if m["state"] == "OPEN": m["state"] = "CLOSED"
            self._save()
            self.alert("recovered", "kill_flatten", "")
        else:
            self.alert("critical", "kill_flatten", "kill switch on but exchange NOT verified flat, retrying: "
                       f"{left if left else ''} {error or check_error or result.errors}")

    def _exchange_exposure(self) -> list:
        left = [p["symbol"] for p in self.client.position_risk() if D(str(p["positionAmt"])) != 0]
        left += [o["symbol"] for o in self.client.open_orders()]
        if getattr(self.client, "conditional_api", "legacy") == "algo":
            left += [o.get("symbol") for o in self.client.open_algo_orders()]
        return sorted(set(left))
