"""Compare exchange state with expected state."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..paper.types import D


@dataclass(frozen=True)
class Report:
    ok: bool
    unknown_positions: dict = field(default_factory=dict)
    missing_positions: dict = field(default_factory=dict)
    qty_mismatch: dict = field(default_factory=dict)
    orphan_orders: list = field(default_factory=list)
    missing_protection: list = field(default_factory=list)


def _protects(order: dict, qty) -> bool:
    return ((order.get("type") or order.get("orderType")) == "STOP_MARKET"
            and (order.get("reduceOnly") in (True, "true") or order.get("closePosition") in (True, "true"))
            and order.get("side") == ("SELL" if qty > 0 else "BUY"))


def compare(actual: dict, open_orders: list, expected_positions: dict, expected_orders) -> Report:
    expected = {s: D(str(q)) for s, q in expected_positions.items() if D(str(q)) != 0}
    actual = {s: D(str(q)) for s, q in actual.items() if D(str(q)) != 0}
    unknown = {s: q for s, q in actual.items() if s not in expected}
    missing = {s: q for s, q in expected.items() if s not in actual}
    mism = {s: (expected[s], actual[s]) for s in expected if s in actual and expected[s] != actual[s]}
    cid = lambda o: o.get("clientOrderId") or o.get("clientAlgoId") or ""
    orphans = sorted(cid(o) for o in open_orders if cid(o) not in set(expected_orders))
    unprot = sorted(s for s, q in actual.items()
                    if not any(o.get("symbol") == s and _protects(o, q) for o in open_orders))
    ok = not (unknown or missing or mism or orphans or unprot)
    return Report(ok, unknown, missing, mism, orphans, unprot)


def fetch_positions(client) -> dict:
    out = {}
    for p in client.position_risk():
        q = D(str(p["positionAmt"]))
        if q != 0: out[p["symbol"]] = q
    return out


def reconcile(client, expected_positions: dict, expected_orders) -> Report:
    orders = list(client.open_orders())
    if getattr(client, "conditional_api", "legacy") == "algo":
        orders += list(client.open_algo_orders())
    return compare(fetch_positions(client), orders, expected_positions, expected_orders)
