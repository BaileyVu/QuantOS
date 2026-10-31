"""Idempotent client order ids, filter-rounded builders, and protected entry placement."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from ..paper.types import D, Instrument, floor_step
from .client import AmbiguousOrderState, SignedClient, UnknownOrder

LEGS = ("entry", "sl", "tp", "close")
ID_RE = re.compile(r"^[\.A-Z\:/a-z0-9_-]{1,36}$")


class ProtectionFailed(RuntimeError): pass
class EntryNotFilled(RuntimeError): pass
class OrderRejected(ValueError): pass


def client_order_id(run_id: str, signal_id: str, leg: str) -> str:
    if leg not in LEGS or not run_id or not signal_id: raise ValueError("bad id parts")
    h = hashlib.sha256(f"{run_id}\x1f{signal_id}\x1f{leg}".encode()).hexdigest()[:24]
    cid = f"qos-{leg}-{h}"
    assert ID_RE.match(cid)
    return cid


def _opp(side: str) -> str:
    if side not in ("BUY", "SELL"): raise ValueError("side")
    return "SELL" if side == "BUY" else "BUY"


def round_qty(inst: Instrument | None, qty) -> Decimal:
    q = D(str(qty))
    if inst is None: return q
    q = floor_step(q, inst.market_step)
    if q < inst.market_min_qty or q <= 0 or q > inst.market_max_qty: raise OrderRejected("quantity outside lot filter")
    return q


def round_price(inst: Instrument, price, rounding) -> Decimal:
    p = (D(str(price)) / inst.tick).to_integral_value(rounding=rounding) * inst.tick
    if p < inst.min_price or p > inst.max_price or p <= 0: raise OrderRejected("price outside price filter")
    return p


def market_entry(symbol, side, qty, cid, inst: Instrument) -> dict:
    return {"symbol": symbol, "side": side, "type": "MARKET", "quantity": round_qty(inst, qty), "newClientOrderId": cid}


def stop_market_protect(symbol, side, stop_price, cid, inst: Instrument, qty=None) -> dict:
    """`side` is the protective order's side. qty=None -> closePosition=true, else reduceOnly+quantity."""
    rnd = ROUND_CEILING if side == "SELL" else ROUND_FLOOR  # toward the market: never looser than requested
    p = {"symbol": symbol, "side": side, "type": "STOP_MARKET", "stopPrice": round_price(inst, stop_price, rnd),
         "workingType": "MARK_PRICE", "priceProtect": "TRUE", "newClientOrderId": cid}
    if qty is None: p["closePosition"] = True
    else: p.update(reduceOnly=True, quantity=round_qty(inst, qty))
    return p


def take_profit_market(symbol, side, stop_price, qty, cid, inst: Instrument) -> dict:
    rnd = ROUND_FLOOR if side == "SELL" else ROUND_CEILING
    return {"symbol": symbol, "side": side, "type": "TAKE_PROFIT_MARKET", "stopPrice": round_price(inst, stop_price, rnd),
            "workingType": "MARK_PRICE", "priceProtect": "TRUE", "reduceOnly": True,
            "quantity": round_qty(inst, qty), "newClientOrderId": cid}


def reduce_only_close(symbol, side, qty, cid, inst: Instrument | None = None) -> dict:
    return {"symbol": symbol, "side": side, "type": "MARKET", "reduceOnly": True,
            "quantity": round_qty(inst, qty), "newClientOrderId": cid}


CONDITIONAL = {"STOP_MARKET", "TAKE_PROFIT_MARKET"}


def to_algo(params: dict) -> dict:
    """Legacy conditional-order params -> Algo Order API params (stopPrice -> triggerPrice)."""
    p = {k: v for k, v in params.items() if k not in ("stopPrice", "newClientOrderId")}
    return {**p, "algoType": "CONDITIONAL", "triggerPrice": params["stopPrice"],
            "clientAlgoId": params["newClientOrderId"]}


def submit(client: SignedClient, params: dict) -> dict:
    """Send an order; on ambiguity query by client id, resubmit once only if provably absent."""
    sym, cid = params["symbol"], params["newClientOrderId"]
    algo = params.get("type") in CONDITIONAL and getattr(client, "conditional_api", "legacy") == "algo"
    send = (lambda: client.new_algo_order(to_algo(params))) if algo else (lambda: client.new_order(params))
    query = (lambda: client.get_algo_order(cid)) if algo else (lambda: client.get_order(sym, cid))
    for attempt in (0, 1):
        try:
            return send()
        except AmbiguousOrderState:
            try:
                return query()
            except UnknownOrder:
                if attempt: raise
    raise AmbiguousOrderState("unreachable")


@dataclass(frozen=True)
class Placement:
    entry: dict
    sl: dict
    tp: dict | None = None
    tp_error: str | None = None


def place_with_protection(client, inst: Instrument, side, qty, stop_price, run_id, signal_id,
                          tp_price=None, close_position: bool = True) -> Placement:
    sym, opp = inst.symbol, _opp(side)
    cid = lambda leg: client_order_id(run_id, signal_id, leg)
    entry = submit(client, market_entry(sym, side, qty, cid("entry"), inst))
    if entry.get("status") != "FILLED":
        entry = client.get_order(sym, cid("entry"))
    filled = D(str(entry.get("executedQty", "0")))
    if entry.get("status") not in ("FILLED", "PARTIALLY_FILLED") or filled <= 0:
        raise EntryNotFilled(f"entry status {entry.get('status')}")
    try:
        sl = submit(client, stop_market_protect(sym, opp, stop_price, cid("sl"), inst, None if close_position else filled))
    except Exception as e:
        try:
            submit(client, reduce_only_close(sym, opp, filled, cid("close"), inst))
        except Exception as e2:
            raise ProtectionFailed(f"SL failed ({type(e).__name__}) and emergency close failed ({type(e2).__name__})") from e
        raise ProtectionFailed(f"SL failed ({type(e).__name__}); position closed") from e
    tp = tp_err = None
    if tp_price is not None:
        try: tp = submit(client, take_profit_market(sym, opp, tp_price, filled, cid("tp"), inst))
        except Exception as e: tp_err = type(e).__name__
    return Placement(entry, sl, tp, tp_err)
