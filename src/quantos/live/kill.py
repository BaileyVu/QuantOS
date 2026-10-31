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


def flatten(client, symbols=None, instruments: dict | None = None, tag: str = "kill") -> FlattenResult:
    """Cancel all open orders, then reduce-only close every nonzero position. Per-symbol errors are collected."""
    res = FlattenResult()
    positions = {p["symbol"]: D(str(p["positionAmt"])) for p in client.position_risk()}
    if symbols is None:
        symbols = sorted({s for s, q in positions.items() if q != 0} | {o["symbol"] for o in client.open_orders()})
    for sym in symbols:
        try:
            client.cancel_all(sym)
            if getattr(client, "conditional_api", "legacy") == "algo": client.cancel_all_algo(sym)
            q = positions.get(sym, D("0"))
            if q != 0:
                cid = client_order_id(tag, f"{sym}-{abs(q)}", "close")
                submit(client, reduce_only_close(sym, "SELL" if q > 0 else "BUY", abs(q), cid, (instruments or {}).get(sym)))
                res.closed.append(sym)
        except Exception as e:
            res.errors[sym] = type(e).__name__
    return res
