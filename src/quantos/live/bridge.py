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
from .client import BinanceError, RateLimited, UnknownOrder
from .kill import KillSwitch, flatten
from .orders import (ProtectionFailed, client_order_id, place_with_protection, reduce_only_close,
                     stop_market_protect, submit)
from .reconcile import reconcile

AUTH_CODES = {-2015, -2014, -1022, -2008}
# A home connection blips; two failed 30s checks used to flatten everything (2026-11-03, -$1.73).
TRANSIENT_KILL_MS, TRANSIENT_KILL_CHECKS = 120_000, 4
TRANSIENT_RETRY_MS = (5_000, 10_000, 20_000, 30_000)
# A kill's one flatten failed on the network on 11-03 and NEAR/ADA stayed open 3.5h; retry until verified flat.
FLATTEN_RETRY_MS = 30_000
# Exchange stop moves: each costs signed calls on the main loop. A rejected move retried every 2s starved the
# loop and left quotes 31 min old (11-05), so moves are rate-limited and failures back off.
STOP_MOVE_MIN_MS, STOP_RETRY_MS = 10_000, 30_000


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


class ExchangeMirror:
    def __init__(self, client, run_id: str, state_dir: Path, alert=None, margin_type="ISOLATED"):
        self.client, self.run_id, self.state = client, run_id, Path(state_dir)
        self.alert = alert or (lambda level, key, message: None)
        self.kill = KillSwitch(self.state)
        self.margin_type, self.failures = margin_type, 0
        # Soft pause: no new entries while the exchange is unreachable; open positions stay managed.
        self.hold, self.transient, self.transient_since = False, 0, None
        self.flat_confirmed, self.next_flatten_ms = False, 0
        self.next_stop_ms = {}
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

    def sync(self, engine, now_ms: int) -> dict:
        if self.kill.active:
            if not self.flat_confirmed and now_ms >= self.next_flatten_ms:
                self.next_flatten_ms = now_ms + FLATTEN_RETRY_MS
                self._flatten()
            return {"skipped": "KILL"} if self.flat_confirmed else {"skipped": "KILL", "flat": False}
        self.flat_confirmed = False
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
                              "stop_cid": stop_cid, "stop_n": 0, "state": "OPEN", "instrument": o["instrument"]}
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
                m["state"] = "CLOSED" if error is None else "CLOSE_FAILED"
                if error is not None:
                    self._halt(f"reduce-only close failed on {m['symbol']}: {error}")
            elif D(o["stop_price"]) != D(m["stop"]) and now_ms >= self.next_stop_ms.get(oid, 0):
                self._move_stop(oid, m, o["stop_price"], ins, opp, now_ms)
            self._save()
        every = (TRANSIENT_RETRY_MS[min(self.transient, len(TRANSIENT_RETRY_MS)) - 1]
                 if self.transient else self.check_every_ms)
        if json.dumps(self.book, sort_keys=True) == before and now_ms - self.last_check_ms < every:
            engine.exchange_hold = self.hold
            return {"ok": True, "skipped": "INTERVAL"}
        self.last_check_ms = now_ms
        result = self.check(now_ms)
        engine.exchange_hold = self.hold
        return result

    def _cancel_stop(self, m):
        cancel = (self.client.cancel_algo_order if getattr(self.client, "conditional_api", "") == "algo"
                  else lambda cid: self.client.cancel_order(m["symbol"], cid))
        return self._guard("cancel", lambda: cancel(m["stop_cid"]))

    def _place_stop(self, oid, m, price, ins, opp):
        m["stop_n"] += 1
        cid = client_order_id(self.run_id, f"{oid[:16]}-{m['stop_n']}", "sl")
        placed, error = self._guard("stop", lambda: submit(self.client, stop_market_protect(
            m["symbol"], opp, D(price), cid, ins)))
        if error is None:
            m["stop_cid"] = placed.get("clientAlgoId") or placed.get("clientOrderId")
        return error

    def _move_stop(self, oid, m, price, ins, opp, now_ms):
        """Binance allows one closePosition stop per side (-4130), so the old stop must go first. If the new one
        fails the old one goes back; if that fails too the position is unprotected, so halt."""
        self.next_stop_ms[oid] = now_ms + STOP_MOVE_MIN_MS
        _, error = self._cancel_stop(m)
        if error is not None and not isinstance(error, UnknownOrder):
            self.next_stop_ms[oid] = now_ms + STOP_RETRY_MS
            self.alert("warn", f"stop:{oid}", f"stop move failed on {m['symbol']}; old stop kept: {error}")
            return
        error = self._place_stop(oid, m, price, ins, opp)
        if error is None:
            m["stop"] = price
            self.alert("recovered", f"stop:{oid}", "")
            return
        self.next_stop_ms[oid] = now_ms + STOP_RETRY_MS
        restore = self._place_stop(oid, m, m["stop"], ins, opp)
        if restore is not None:
            self._halt(f"{m['symbol']} unprotected: stop move failed ({error}), restore failed ({restore})")
        else:
            self.alert("warn", f"stop:{oid}", f"stop move failed on {m['symbol']}; old stop restored: {error}")

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
