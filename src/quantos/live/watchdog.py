"""Independent demo/testnet protection watchdog. Never enables mainnet.

Run as a separate process. Native SL/TP survive an asleep Mac; a local watchdog
cannot enforce elapsed-time exits until the host is awake and the exchange reachable.
"""
import argparse
import json
import os
from pathlib import Path
import time

from .bridge import ExchangeMirror, _instrument
from .orders import client_order_id, reduce_only_close, submit
from .state_lock import mirror_lock
from ..paper.types import D


def protect_once(state, client, now_ms=None, max_age_s=120, alert=lambda *a: None):
    if client.mode not in ("dry-run", "testnet"):
        raise ValueError("protection watchdog is demo/testnet only")
    state = Path(state)
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    with mirror_lock(state):
        path = state / "mirror.json"
        if not path.exists(): return {"closed": [], "errors": {}}
        book = json.loads(path.read_text())
        active = {oid: m for oid, m in book.items() if "instrument" in m
                  and m["state"] in ("OPEN", "PENDING", "CLOSE_FAILED", "FAILED")}
        if not active: return {"closed": [], "errors": {}}
        try:
            beat = int(json.loads((state / "heartbeat.json").read_text())["updated_ms"])
        except (OSError, ValueError, KeyError, TypeError):
            beat = min(m.get("opened_ms", now_ms) for m in active.values())
        stale = now_ms - beat > max_age_s * 1000 or beat > now_ms + 2000
        result = {"closed": [], "errors": {}}
        for oid, m in active.items():
            if not stale and now_ms < m.get("deadline_ms", now_ms + 1): continue
            run_id = m.get("run_id")
            if not run_id:
                result["errors"][oid] = "legacy record lacks run_id; manual reconciliation required"
                continue
            mirror = ExchangeMirror(client, run_id, state, alert)
            m = mirror.book[oid]
            if not mirror.scope(m["symbol"]):
                result["errors"][oid] = "symbol outside watchdog scope"
                continue
            if stale: mirror.kill.engage("runtime heartbeat stale or clock moved backward")
            try:
                if m["state"] in ("PENDING", "FAILED"):
                    # Require a definite terminal entry outcome before calling a leg flat.
                    entry = client.get_order(m["symbol"], client_order_id(run_id, oid[:20], "entry"))
                    if entry.get("status") not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                        raise ValueError("entry outcome not terminal")
                    m["entry_order"] = entry.get("orderId")
                    m["qty"] = str(D(str(entry.get("executedQty", "0"))))
                    if D(m["qty"]) == 0:
                        # Persist for the engine to void on recovery; no synthetic exit/P&L.
                        m.update(state="VOID", entry_void=True)
                        mirror._save()
                        continue
                    # The process may have died after placing brackets but before
                    # saving their replies. Deterministic ids recover those legs.
                    m.setdefault("stop_cid", client_order_id(run_id, oid[:20], "sl"))
                    if m.get("target") is not None:
                        m.setdefault("tp_cid", client_order_id(run_id, oid[:20], "tp"))
                rows = client.position_risk(m["symbol"])
                amounts = [D(str(r["positionAmt"])) for r in rows if r.get("symbol") == m["symbol"]]
                if len(amounts) != 1 or any(r.get("positionSide", "BOTH") != "BOTH" for r in rows
                                          if r.get("symbol") == m["symbol"]):
                    raise ValueError("one-way position unavailable")
                qty = amounts[0]
                if qty:
                    close = submit(client, reduce_only_close(m["symbol"], "SELL" if qty > 0 else "BUY", abs(qty),
                                   client_order_id(run_id, oid[:20], "close"), _instrument(m["instrument"])))
                    mirror._remember_exit(m, close.get("orderId"))
                    mirror._save()
                if not mirror._flat_on_exchange(m["symbol"]):
                    raise ValueError("watchdog close not confirmed flat; protection retained")
                mirror._discover_exits(m)
                if m.get("stop_cid"): mirror._cancel_stop(m)
                mirror._cancel_tp(m)
                m.update(state="CLOSED", watchdog_closed=True)
                mirror._save()
                result["closed"].append(oid)
            except Exception as error:
                result["errors"][oid] = str(error)
                alert("critical", f"watchdog:{oid}", str(error))
        return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--execution", choices=("testnet", "dry-run"), default="dry-run")
    a = p.parse_args(argv)
    from .client import SignedClient
    from .keys import load_keys
    from ..paper.health import Alerter
    keys = load_keys(os.environ["QUANTOS_KEY_FILE"]) if a.execution == "testnet" else None
    alerter = Alerter(a.state)
    now = int(time.time() * 1000)
    result = protect_once(a.state, SignedClient(a.execution, keys), now,
                          alert=lambda level, key, message: alerter.alert(level, key, message, now))
    print(json.dumps(result, sort_keys=True))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
