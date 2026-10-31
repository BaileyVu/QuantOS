"""Mirror the paper engine's positions onto Binance (dry-run / testnet / gated mainnet).

The paper engine stays the single decision maker. Each cycle the mirror:
1. opens an exchange position (market entry + exchange-native reduce-only
   STOP_MARKET) for every newly filled paper position;
2. replaces the exchange stop when the paper stop tightens;
3. closes (reduce-only) and cancels protection when the paper position closes;
4. reconciles exchange positions/protection with what it placed. Two failed
   reconciliations in a row, or any protection failure, engage the kill switch
   and flatten. Auth failures (e.g. a dynamic home IP falling off the API key
   whitelist) raise a critical alert.
State lives in `<state>/mirror.json`, so a restart neither re-enters nor forgets.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..paper.types import D, Instrument
from .client import BinanceError
from .kill import KillSwitch, flatten
from .orders import (ProtectionFailed, client_order_id, place_with_protection, reduce_only_close,
                     stop_market_protect, submit)
from .reconcile import reconcile

AUTH_CODES = {-2015, -2014, -1022, -2008}


def is_auth_error(error: Exception) -> bool:
    return isinstance(error, BinanceError) and (
        getattr(error, "code", None) in AUTH_CODES or getattr(error, "status", None) in (401, 403))


def _instrument(fields: dict) -> Instrument:
    return Instrument(**{k: v if k in {"symbol", "margin_asset", "status", "base_asset"} else D(v)
                         for k, v in fields.items()})


class ExchangeMirror:
    def __init__(self, client, run_id: str, state_dir: Path, alert=None, margin_type="ISOLATED"):
        self.client, self.run_id, self.state = client, run_id, Path(state_dir)
        self.alert = alert or (lambda level, key, message: None)
        self.kill = KillSwitch(self.state)
        self.margin_type, self.failures = margin_type, 0
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
            return {"skipped": "KILL"}
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
            elif D(o["stop_price"]) != D(m["stop"]):
                m["stop_n"] += 1
                cid = client_order_id(self.run_id, f"{oid[:16]}-{m['stop_n']}", "sl")
                placed, error = self._guard("stop", lambda: submit(self.client, stop_market_protect(
                    m["symbol"], opp, D(o["stop_price"]), cid, ins)))
                if error is None:
                    self._cancel_stop(m)
                    m.update(stop=o["stop_price"], stop_cid=placed.get("clientAlgoId") or placed.get("clientOrderId"))
                else:
                    self.alert("warn", f"stop:{oid}", f"stop update failed on {m['symbol']}; old stop kept: {error}")
            self._save()
        return self.check()

    def _cancel_stop(self, m):
        cancel = (self.client.cancel_algo_order if getattr(self.client, "conditional_api", "") == "algo"
                  else lambda cid: self.client.cancel_order(m["symbol"], cid))
        self._guard("cancel", lambda: cancel(m["stop_cid"]))

    def check(self) -> dict:
        if getattr(self.client, "mode", "") == "dry-run":  # nothing real to compare against
            return {"ok": True, "report": "DRY_RUN_UNVERIFIED", "error": ""}
        expected = {m["symbol"]: D(m["qty"]) * (1 if m["side"] == "BUY" else -1)
                    for m in self.book.values() if m["state"] == "OPEN"}
        orders = {m["stop_cid"] for m in self.book.values() if m["state"] == "OPEN"}
        report, error = self._guard("reconcile", lambda: reconcile(self.client, expected, orders))
        ok = error is None and report.ok
        self.failures = 0 if ok else self.failures + 1
        if not ok:
            self.alert("warn" if self.failures < 2 else "critical", "reconcile",
                       f"exchange reconciliation failed: {error or report}")
            if self.failures >= 2:
                self._halt("two consecutive reconciliation failures")
        return {"ok": ok, "report": None if report is None else report.__dict__, "error": str(error or "")}

    def _halt(self, reason: str):
        self.kill.engage(reason)
        self.alert("critical", "kill_switch", f"exchange kill switch engaged: {reason}")
        instruments = {m["symbol"]: _instrument(m["instrument"]) for m in self.book.values() if "instrument" in m}
        self._guard("flatten", lambda: flatten(self.client, None, instruments, tag=self.run_id))
