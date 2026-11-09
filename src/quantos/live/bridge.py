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
from .client import AmbiguousOrderState, BinanceError, RateLimited, UnknownOrder
from .kill import KillSwitch, flatten
from .orders import (ProtectionFailed, client_order_id, place_with_protection, reduce_only_close,
                     stop_market_protect, submit, verify_position_leverage, take_profit_market)
from .reconcile import reconcile
from .state_lock import mirror_lock

AUTH_CODES = {-2015, -2014, -1022, -2008}
# A home connection blips; two failed 30s checks used to flatten everything (2026-11-03, -$1.73).
TRANSIENT_KILL_MS, TRANSIENT_KILL_CHECKS = 120_000, 4
TRANSIENT_RETRY_MS = (5_000, 10_000, 20_000, 30_000)
STOP_RETRY_MS = (30_000, 60_000, 120_000, 300_000)
# Books take the real fill prices and fees (userTrades) instead of simulated ones; demo P&L drifted
# from Binance (+$3.05 booked vs +$4.04 real, 11-05). Retried with backoff while fills are not visible yet.
SETTLE_RETRY_MS = (5_000, 15_000, 60_000, 300_000)
SETTLE_GIVE_UP = 12
# An entry whose outcome is unknown is resolved by client id each sync; past this it is treated as unsafe.
PENDING_KILL_MS = 90_000
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
    def __init__(self, client, run_id: str, state_dir: Path, alert=None, margin_type="ISOLATED", quote_asset="USDC"):
        self.client, self.run_id, self.state = client, run_id, Path(state_dir)
        # The mirror owns only its quote asset's symbols: the alt-ML executor trades USDT pairs on the same
        # account, and its positions must neither fail reconciliation nor be flattened by this kill switch.
        self.scope = lambda symbol: str(symbol).endswith(quote_asset)
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
        with mirror_lock(self.state):
            self.book = json.loads(self.path.read_text()) if self.path.exists() else {}
            for oid, m in self.book.items():
                if m.get("entry_void") and hasattr(engine, "void_entry"):
                    engine.void_entry(oid, now_ms, "exchange entry confirmed unfilled")
                elif m.get("watchdog_closed") and hasattr(engine, "close_external"):
                    engine.close_external(oid, now_ms, "WATCHDOG_EXIT")
            return self._sync(engine, now_ms, events)

    def _sync(self, engine, now_ms, events):
        if self.kill.active:
            if not self.flat_confirmed and now_ms >= self.next_flatten_ms:
                self.next_flatten_ms = now_ms + FLATTEN_RETRY_MS
                self._flatten()
            if self.flat_confirmed and hasattr(engine, "book_real_fills"):
                for oid, m in self.book.items():
                    if m["state"] == "CLOSED" and m.get("booked") != "all" and m.get("entry_order"):
                        if hasattr(engine, "close_external"): engine.close_external(oid, now_ms, "KILL_EXIT")
                        if now_ms >= self.settle_retry.get(oid, (0, 0))[0]: self._settle(engine, oid, m, now_ms)
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
            _, setup = self._guard("setup", lambda: self.client.set_margin_type(ins.symbol, self.margin_type))
            if setup is None:
                _, setup = self._guard("setup", lambda: self.client.set_leverage(ins.symbol, D(o["decision"]["leverage"])))
            if setup is not None:
                # The size was approved for this leverage and margin type; never enter at the account's old ones.
                if not is_transient_error(setup):
                    self._void(engine, oid, ins.symbol, now_ms, f"leverage/margin setup rejected: {setup}")
                continue  # transient: retry at the next sync
            # Write intent BEFORE the side effect: a process killed between entry
            # and stop must leave a deterministic id the resolver/watchdog owns.
            self.book[oid] = {"symbol": ins.symbol, "side": side, "qty": o["filled"], "stop": o["stop_price"],
                              "state": "PENDING", "instrument": o["instrument"], "opened_ms": now_ms,
                              "leverage": o["decision"]["leverage"], "run_id": self.run_id,
                              "signal_id": oid[:20],
                              "pending_since": now_ms, "stop_n": 0, "booked": "",
                              "target": s.get("take_profit"),
                              "deadline_ms": int(o.get("first_fill_ms") or now_ms) + int(s.get("maximum_hold_seconds", 14400)) * 1000}
            self._save()
            placed, error = self._guard("entry", lambda: place_with_protection(
                self.client, ins, side, D(o["filled"]), D(o["stop_price"]), self.run_id, oid[:20],
                expected_leverage=D(o["decision"]["leverage"]), tp_price=s.get("take_profit")))
            if error is not None:
                if isinstance(error, ProtectionFailed):
                    self.book[oid].update(state="FAILED", error=type(error).__name__)
                    self._save()
                    self._halt(f"protection failed on {ins.symbol}: {error}")
                    return {"skipped": "KILL"}
                elif isinstance(error, AmbiguousOrderState) or is_transient_error(error):
                    # Outcome unknown: it may have filled. Resolve by client id before anything else.
                    self._save()
                    self.alert("warn", f"entry:{oid}", f"entry outcome unknown on {ins.symbol}; resolving: {error}")
                else:  # definite rejection (filters, margin, not filled): nothing exists on the exchange
                    self._void(engine, oid, ins.symbol, now_ms, f"exchange entry rejected: {error}")
                continue
            qty = D(str(placed.entry.get("executedQty", o["filled"])))
            stop_cid = placed.sl.get("clientAlgoId") or placed.sl.get("clientOrderId")
            self.book[oid].update(qty=str(qty), stop_cid=stop_cid, state="OPEN",
                                 entry_order=placed.entry.get("orderId"),
                                 tp_cid=(placed.tp or {}).get("clientAlgoId") or (placed.tp or {}).get("clientOrderId"),
                                 entry_average=str(placed.entry.get("avgPrice", "0")))
            self.book[oid].pop("pending_since", None)
            self._save()
            if placed.tp_error:
                # The leg is protected by its stop and the engine still exits at target, so one rejected
                # take-profit (e.g. -2021 after a fast fill, or a timeout) must not flatten every position.
                self.alert("warn", f"tp:{oid}", f"take-profit not placed on {ins.symbol}: {placed.tp_error}; "
                           "stop protects the leg, engine manages the target")
        for oid, m in list(self.book.items()):
            if m["state"] == "PENDING":
                self._resolve(engine, oid, m, now_ms)
                if self.kill.active: return {"skipped": "KILL"}
            if m["state"] != "OPEN":
                continue
            ins, opp = _instrument(m["instrument"]), "SELL" if m["side"] == "BUY" else "BUY"
            o = live.get(oid)
            if m.get("risk_close"):
                if hasattr(engine, "close_external"):
                    engine.close_external(oid, now_ms, "REALISED_RISK_EXCEEDED")
                o = None  # durable even if the journal transaction rolled back on a crash
            if o is not None and hasattr(engine, "check_realised_risk") and not m.get("risk_verified"):
                if self._check_entry_risk(engine, oid, m, now_ms) is False:
                    o = None  # close this leg immediately in the ordinary reduce-only path
                if self.kill.active:
                    return {"skipped": "KILL"}
            if o is None:  # paper closed or closing: flatten this leg and drop protection
                closed, error = self._guard("close", lambda: submit(self.client, reduce_only_close(
                    m["symbol"], opp, D(m["qty"]), client_order_id(self.run_id, oid[:20], "close"), ins)))
                if closed is not None:
                    self._remember_exit(m, closed.get("orderId"))
                    if getattr(self.client, "mode", "") != "dry-run" and not self._flat_on_exchange(m["symbol"]):
                        error = ValueError("close not confirmed flat; protection retained")
                if error is not None and self._flat_on_exchange(m["symbol"]):
                    error = None  # the exchange stop filled first; the reduce-only close had nothing to close
                if error is None:
                    self._discover_exits(m)
                    self._cancel_stop(m)  # keep protection until the close is confirmed
                    self._cancel_tp(m)
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
                    m.setdefault("stop_history", []).append(m["stop_cid"])
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
            engine.exchange_hold = self.hold or any("risk_pending" in m or m.get("settlement_pending") for m in self.book.values())
            return {"ok": True, "skipped": "INTERVAL"}
        self.last_check_ms = now_ms
        result = self.check(now_ms)
        engine.exchange_hold = self.hold or any("risk_pending" in m or m.get("settlement_pending") for m in self.book.values())
        return result

    def _check_entry_risk(self, engine, oid, m, now_ms):
        """Keep protection while resolving a missing average; hold entries, then kill at 90s."""
        if getattr(self.client, "mode", "") == "dry-run":
            return True  # synthetic dry-run acknowledgements have no exchange price
        try:
            price = D(m.get("entry_average", "0"))
            if not price.is_finite() or price <= 0:
                entry = self.client.get_order(m["symbol"], client_order_id(self.run_id, oid[:20], "entry"))
                price = D(str(entry.get("avgPrice", "0")))
                if not price.is_finite() or price <= 0:
                    raise ValueError("exchange entry average unavailable")
            result = engine.check_realised_risk(oid, now_ms, price, D(m["qty"]))
            m.update(entry_average=str(price), risk_verified=True, risk_close=not result)
            m.pop("risk_pending", None)
            self._save()
            return result
        except Exception as error:
            m.setdefault("risk_pending", now_ms)
            self._save()
            self.alert("warn", f"risk:{oid}", f"real fill risk unverified on {m['symbol']}: {error}")
            if now_ms - m["risk_pending"] >= PENDING_KILL_MS:
                self._halt(f"real fill risk unverified on {m['symbol']} for 90s")
            return None

    def _void(self, engine, oid, symbol, now_ms, why):
        """Nothing was opened on the exchange: remove the paper entry too, so the books match Binance."""
        self.book[oid] = {"symbol": symbol, "state": "FAILED", "error": why[:200]}
        self._save()
        if hasattr(engine, "void_entry"): engine.void_entry(oid, now_ms, why[:200])
        self.alert("critical", f"entry:{oid}", f"{symbol}: {why}; paper entry voided")

    def _resolve(self, engine, oid, m, now_ms):
        """A PENDING entry: ask Binance by client id. Filled -> protect and adopt; absent -> void; still
        unknown after PENDING_KILL_MS -> kill (flatten), never leave a possibly unprotected position."""
        ins, opp = _instrument(m["instrument"]), "SELL" if m["side"] == "BUY" else "BUY"
        entry, error = self._guard("resolve", lambda: self.client.get_order(m["symbol"], client_order_id(
            self.run_id, oid[:20], "entry")))
        if isinstance(error, UnknownOrder) or (error is None and entry.get("status") in ("CANCELED", "EXPIRED", "REJECTED")
                                                and D(str(entry.get("executedQty", "0"))) == 0):
            return self._void(engine, oid, m["symbol"], now_ms, "entry never filled on the exchange")
        filled = D(str(entry.get("executedQty", "0"))) if error is None else D(0)
        if error is None and filled > 0 and entry.get("status") in ("FILLED", "PARTIALLY_FILLED", "CANCELED", "EXPIRED"):
            # Older persisted PENDING records can recover the original approval
            # from the engine; never infer leverage from the exchange itself.
            expected = m.get("leverage")
            if expected is None:
                o = next((o for o in engine.active() if o["id"] == oid), None)
                expected = o["decision"]["leverage"] if o else None
            _, lev_error = self._guard("leverage", lambda: verify_position_leverage(self.client, m["symbol"], expected))
            if lev_error is not None:
                return self._halt(f"leverage unverified on {m['symbol']} after resolved entry: {lev_error}")
            sl, sl_error = self._guard("stop", lambda: submit(self.client, stop_market_protect(
                m["symbol"], opp, D(m["stop"]), client_order_id(self.run_id, oid[:20], "sl"), ins)))
            if sl_error is not None:
                return self._halt(f"protection failed on {m['symbol']} after a resolved entry: {sl_error}")
            m.update(state="OPEN", qty=str(filled), entry_order=entry.get("orderId"),
                     entry_average=str(entry.get("avgPrice", "0")),
                     stop_cid=sl.get("clientAlgoId") or sl.get("clientOrderId"))
            m.pop("pending_since", None)
            self._save()
            if m.get("target") is not None:
                tp, tp_error = self._guard("tp", lambda: submit(self.client, take_profit_market(
                    m["symbol"], opp, D(m["target"]), filled, client_order_id(self.run_id, oid[:20], "tp"), ins)))
                if tp_error is not None:  # stop already protects the leg; see the entry path above
                    self.alert("warn", f"tp:{oid}", f"take-profit not placed on {m['symbol']} after resolved "
                               f"entry: {tp_error}; stop protects the leg, engine manages the target")
                else:
                    m["tp_cid"] = tp.get("clientAlgoId") or tp.get("clientOrderId")
                    self._save()
            self.alert("recovered", f"entry:{oid}", "")
            return None
        if now_ms - m["pending_since"] > PENDING_KILL_MS:
            self._halt(f"entry on {m['symbol']} unresolved for {PENDING_KILL_MS // 1000}s: {error or entry.get('status')}")

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
                            known = {m.get("stop_cid"), m.get("tp_cid"),
                                     client_order_id(self.run_id, oid[:20], "close")}
                            if o.get("c") and o.get("c") in known:
                                self._remember_exit(m, o.get("i"))
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
        if error is not None: return False
        matches = [r for r in rows if r.get("symbol") == symbol]
        return (len(matches) == 1 and matches[0].get("positionSide", "BOTH") == "BOTH"
                and D(str(matches[0]["positionAmt"])) == 0)

    @staticmethod
    def _remember_exit(m, order_id):
        if order_id is not None and str(order_id) not in ("", "0", "None"):
            m["exit_orders"] = sorted(set(m.get("exit_orders", ())) | {str(order_id)})

    def _discover_exits(self, m):
        """Resolve conditional order ids to the actual matching-engine order ids."""
        if m.get("run_id") and m.get("signal_id"):
            cid = client_order_id(m["run_id"], m["signal_id"], "close")
            row, error = self._guard("exit_id", lambda: self.client.get_order(m["symbol"], cid))
            if error is None and D(str(row.get("executedQty", "0"))) > 0:
                self._remember_exit(m, row.get("orderId"))
        cids = set(m.get("stop_history", ())) | {m.get("stop_cid"), m.get("tp_cid")}
        for cid in sorted(c for c in cids if c):
            algo = getattr(self.client, "conditional_api", "") == "algo"
            row, error = self._guard("exit_id", lambda: self.client.get_algo_order(cid) if algo
                                     else self.client.get_order(m["symbol"], cid))
            if error is None:
                actual = row.get("actualOrderId") if algo else (row.get("orderId") if D(str(row.get("executedQty", "0"))) > 0 else None)
                self._remember_exit(m, actual)

    def _settle(self, engine, oid, m, now_ms):
        """Only order-linked, complete paginated fills can replace simulated books."""
        fails = self.settle_retry.get(oid, (0, 0))[1]
        done, error = False, None
        try:
            asset = m["instrument"].get("margin_asset")
            if m["booked"] == "":
                entry = self.client.order_trades(m["symbol"], m.get("entry_order"), m["opened_ms"] - 60_000)
                if not entry or sum(D(str(t["qty"])) for t in entry) != D(m["qty"]):
                    raise ValueError("entry fills incomplete")
                booked = engine.book_real_fills(oid, now_ms, entry=_vwap(entry, None, asset))
                if booked is None: raise ValueError("paper entry not available")
                if "risk_safe" in booked:
                    m.update(risk_verified=True, risk_close=not booked["risk_safe"])
                    m.pop("risk_pending", None)
                m["booked"] = "entry"
            if m["state"] == "CLOSED" and m["booked"] == "entry":
                self._discover_exits(m)
                exits = []
                for order_id in m.get("exit_orders", ()):
                    exits.extend(self.client.order_trades(m["symbol"], order_id, m["opened_ms"] - 60_000))
                if not exits or sum(D(str(t["qty"])) for t in exits) != D(m["qty"]):
                    raise ValueError("linked exit fills incomplete")
                booked = engine.book_real_fills(oid, now_ms, exit=_vwap(exits, None, asset))
                if booked and booked["has_exit"]:
                    m["booked"] = "all"
            done = m["booked"] == "all" or (m["state"] == "OPEN" and m["booked"] == "entry")
        except Exception as exc:
            error = exc
        m["settlement_pending"] = not done
        self._save()
        if done:
            self.settle_retry.pop(oid, None)
        else:
            self.settle_retry[oid] = (now_ms + SETTLE_RETRY_MS[min(fails, len(SETTLE_RETRY_MS) - 1)], fails + 1)
            if fails == 0 or fails + 1 == SETTLE_GIVE_UP:
                self.alert("critical" if fails + 1 >= SETTLE_GIVE_UP else "warn", f"fills:{oid}",
                           f"settlement UNVERIFIED for {m['symbol']}; entries held, retrying: {error or 'paper exit pending'}")

    def _cancel_tp(self, m):
        if m.get("tp_cid"):
            cancel = (self.client.cancel_algo_order if getattr(self.client, "conditional_api", "") == "algo"
                      else lambda cid: self.client.cancel_order(m["symbol"], cid))
            self._guard("cancel", lambda: cancel(m["tp_cid"]))

    def _cancel_stop(self, m):
        cancel = (self.client.cancel_algo_order if getattr(self.client, "conditional_api", "") == "algo"
                  else lambda cid: self.client.cancel_order(m["symbol"], cid))
        self._guard("cancel", lambda: cancel(m["stop_cid"]))

    def check(self, now_ms: int | None = None) -> dict:
        if getattr(self.client, "mode", "") == "dry-run":  # nothing real to compare against
            return {"ok": True, "report": "DRY_RUN_UNVERIFIED", "error": ""}
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        if any(m["state"] == "PENDING" for m in self.book.values()):
            return {"ok": True, "report": "PENDING_ENTRY", "error": ""}  # bounded by PENDING_KILL_MS
        expected = {m["symbol"]: D(m["qty"]) * (1 if m["side"] == "BUY" else -1)
                    for m in self.book.values() if m["state"] == "OPEN"}
        orders = {m[key] for m in self.book.values() if m["state"] == "OPEN"
                  for key in ("stop_cid", "tp_cid") if m.get(key)}
        report, error = self._guard("reconcile", lambda: reconcile(self.client, expected, orders, self.scope))
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
        close_ids = {m["symbol"]: client_order_id(self.run_id, oid[:20], "close")
                     for oid, m in self.book.items()
                     if m["state"] in ("OPEN", "PENDING", "CLOSE_FAILED", "FAILED") and "instrument" in m}
        result, error = self._guard("flatten", lambda: flatten(self.client, None, instruments, tag=self.run_id,
                                                             scope=self.scope, close_ids=close_ids))
        unresolved = False
        for oid, m in self.book.items():
            if result is not None: self._remember_exit(m, result.order_ids.get(m["symbol"]))
            if m["state"] in ("PENDING", "FAILED") and "instrument" in m:
                entry, query_error = self._guard("resolve", lambda: self.client.get_order(m["symbol"], client_order_id(self.run_id, oid[:20], "entry")))
                if query_error is None and entry.get("status") in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                    m["entry_order"] = entry.get("orderId")
                    m["qty"] = str(D(str(entry.get("executedQty", "0"))))
                    if D(m["qty"]) == 0:
                        m.update(state="VOID", entry_void=True)
                elif isinstance(query_error, UnknownOrder):
                    m.update(state="VOID", entry_void=True)
                elif not isinstance(query_error, UnknownOrder): unresolved = True
        self._save()  # preserve any successful close ids even when another leg fails
        left, check_error = self._guard("flatten", self._exchange_exposure)
        if error is None and check_error is None and not result.errors and not left and not unresolved:
            self.flat_confirmed = True
            for m in self.book.values():
                if m["state"] in ("OPEN", "PENDING", "CLOSE_FAILED", "FAILED") and "instrument" in m:
                    m["state"] = "CLOSED"
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
        return sorted(s for s in set(left) if self.scope(s))
