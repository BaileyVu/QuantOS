from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class FrozenLogisticModel:
    columns: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    bias: float

    @classmethod
    def load(cls, path: Path | str) -> "FrozenLogisticModel":
        model = json.loads(Path(path).read_text())["model"]
        result = cls(
            tuple(map(str, model["columns"])), tuple(map(float, model["means"])),
            tuple(map(float, model["scales"])), tuple(map(float, model["weights"])),
            float(model["bias"]),
        )
        if not (len(result.columns) == len(result.means) == len(result.scales) == len(result.weights)):
            raise ValueError("invalid frozen model dimensions")
        return result

    def predict(self, row: dict[str, float]) -> float:
        total = self.bias
        for name, mean, scale, weight in zip(self.columns, self.means, self.scales, self.weights, strict=True):
            value = float(row[name])
            if not math.isfinite(value):
                raise ValueError(f"non-finite feature: {name}")
            total += ((value - mean) / (scale if scale else 1.0)) * weight
        if total >= 0:
            z = math.exp(-total)
            return 1.0 / (1.0 + z)
        z = math.exp(total)
        return z / (1.0 + z)


def calibrate(probability: float, calibration: dict[str, object]) -> float:
    p = min(1.0 - 1e-9, max(1e-9, probability))
    kind = str(calibration.get("kind", "raw"))
    if kind == "raw":
        return p
    if kind == "shrink":
        factor = float(calibration["factor"])
        return 0.5 + factor * (p - 0.5)
    if kind == "platt":
        logit = math.log(p / (1.0 - p))
        value = float(calibration["slope"]) * logit + float(calibration["intercept"])
        return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, value))))
    raise ValueError(f"unknown calibration: {kind}")


@dataclass
class PublicBook:
    token_id: str
    bids: dict[Decimal, Decimal]
    asks: dict[Decimal, Decimal]
    min_order_size: Decimal = Decimal("0")
    tick_size: Decimal = Decimal("0.01")
    source_ts_ms: int = 0

    @classmethod
    def empty(cls, token_id: str) -> "PublicBook":
        return cls(token_id, {}, {})

    @staticmethod
    def _levels(values: object) -> dict[Decimal, Decimal]:
        result: dict[Decimal, Decimal] = {}
        if not isinstance(values, list):
            return result
        for item in values:
            if not isinstance(item, dict):
                continue
            try:
                price, size = Decimal(str(item["price"])), Decimal(str(item["size"]))
            except (KeyError, ValueError):
                continue
            if price > 0 and size > 0:
                result[price] = size
        return result

    def snapshot(self, payload: dict[str, object]) -> None:
        self.bids = self._levels(payload.get("bids"))
        self.asks = self._levels(payload.get("asks"))
        self.min_order_size = Decimal(str(payload.get("min_order_size") or self.min_order_size))
        self.tick_size = Decimal(str(payload.get("tick_size") or self.tick_size))
        self.source_ts_ms = int(payload.get("timestamp") or 0)

    def change(self, *, side: str, price: object, size: object, source_ts_ms: int) -> None:
        level, quantity = Decimal(str(price)), Decimal(str(size))
        target = self.bids if side.upper() == "BUY" else self.asks
        if quantity <= 0:
            target.pop(level, None)
        else:
            target[level] = quantity
        self.source_ts_ms = max(self.source_ts_ms, source_ts_ms)

    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self.bids: return None
        price=max(self.bids); return price,self.bids[price]

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self.asks: return None
        price=min(self.asks); return price,self.asks[price]

    def ask_levels(self) -> list[tuple[Decimal, Decimal]]:
        return [(p,self.asks[p]) for p in sorted(self.asks)]

    def features(self, levels: int = 5) -> dict[str, float]:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None or bid[0] >= ask[0]:
            raise ValueError("book is empty or crossed")
        bids=sorted(self.bids,reverse=True)[:levels]; asks=sorted(self.asks)[:levels]
        bid_depth=sum(self.bids[p] for p in bids); ask_depth=sum(self.asks[p] for p in asks)
        depth=bid_depth+ask_depth; size=bid[1]+ask[1]
        if depth <= 0 or size <= 0: raise ValueError("book has no positive depth")
        return {"bid":float(bid[0]),"ask":float(ask[0]),"mid":float((bid[0]+ask[0])/2),
            "spread":float(ask[0]-bid[0]),"bid_size":float(bid[1]),"ask_size":float(ask[1]),
            "bid_depth":float(bid_depth),"ask_depth":float(ask_depth),
            "book_imbalance":float((bid_depth-ask_depth)/depth),
            "microprice":float((ask[0]*bid[1]+bid[0]*ask[1])/size)}
