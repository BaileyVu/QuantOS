"""File-based kill switch and emergency flatten."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..paper.types import D
from .orders import client_order_id, reduce_only_close, submit


class KillSwitch:
    def __init__(self, state_dir):
        self.path = Path(state_dir) / "KILL"

    @property
    def active(self) -> bool: return self.path.exists()

    def engage(self, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(" ".join(str(reason).split())[:500] + "\n")
        os.replace(tmp, self.path)

    def reason(self) -> str: return self.path.read_text().strip() if self.active else ""


@dataclass
class FlattenResult:
    closed: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)
    order_ids: dict = field(default_factory=dict)


def flatten(client, symbols=None, instruments: dict | None = None, tag: str = "kill", scope=None,
            close_ids=None) -> FlattenResult:
    """Reduce-only close, verify flat, then cancel protection (only symbols `scope` accepts,
    when given). Per-symbol errors are collected."""
    res = FlattenResult()
    positions = {p["symbol"]: D(str(p["positionAmt"])) for p in client.position_risk()}
    if symbols is None:
        symbols = sorted({s for s, q in positions.items() if q != 0} | {o["symbol"] for o in client.open_orders()})
        if getattr(client, "conditional_api", "legacy") == "algo":
            symbols = sorted(set(symbols) | {o["symbol"] for o in client.open_algo_orders()})
        if scope is not None: symbols = [s for s in symbols if scope(s)]
    for sym in symbols:
        try:
            q = positions.get(sym, D("0"))
            if q != 0:
                cid = (close_ids or {}).get(sym) or client_order_id(tag, f"{sym}-{abs(q)}", "close")
                closed = submit(client, reduce_only_close(sym, "SELL" if q > 0 else "BUY", abs(q), cid, (instruments or {}).get(sym)))
                if closed.get("orderId") is not None: res.order_ids[sym] = closed["orderId"]
                confirmed = [p for p in client.position_risk(sym) if p.get("symbol") == sym]
                if (len(confirmed) != 1 or confirmed[0].get("positionSide", "BOTH") != "BOTH"
                        or D(str(confirmed[0]["positionAmt"])) != 0):
                    raise ValueError("close not confirmed flat; protection retained")
                res.closed.append(sym)
            client.cancel_all(sym)
            if getattr(client, "conditional_api", "legacy") == "algo": client.cancel_all_algo(sym)
        except Exception as e:
            res.errors[sym] = type(e).__name__
    return res
