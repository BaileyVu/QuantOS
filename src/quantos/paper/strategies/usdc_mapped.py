"""Run a USDT-researched event rule on a USDC perpetual.

Mapping assumption (stated, not verified as an edge): the rule's price inputs
come from the USDC contract's own candles (USDC/USDT basis is treated as
negligible for 4h/24h percentage thresholds) and its open-interest inputs come
from the USDT twin (`XYZUSDT`), the series the rule was researched on. The
strategy sees a USDT-labelled view; the resulting signal is re-anchored to
the USDC quote and rounded to the USDC tick, with the same stop fraction.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from ..types import Signal, Snapshot

D = Decimal
PAPER_VERSION = "paper-usdc-1.1.0"


def usdt_twin(symbol: str) -> str | None:
    return symbol[:-4] + "USDT" if symbol.endswith("USDC") and len(symbol) > 4 else None


def _tick(value: Decimal, tick: Decimal, rounding) -> Decimal:
    return (value / tick).to_integral_value(rounding=rounding) * tick


class UsdcMapped:
    def __init__(self, inner, symbols: frozenset[str] | None = None):
        self.inner = inner
        self.strategy_id = inner.strategy_id
        self.version = PAPER_VERSION
        self.symbols = symbols  # USDT twins allowed; None means any

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        ins, q = snapshot.instrument, snapshot.quote
        twin = usdt_twin(ins.symbol)
        if ins.margin_asset != "USDC" or twin is None or (self.symbols is not None and twin not in self.symbols):
            return None
        view = replace(snapshot, instrument=replace(ins, symbol=twin, margin_asset="USDT"),
                       quote=replace(q, symbol=twin))
        signal = self.inner.evaluate(view)
        if signal is None or signal.side != "LONG":
            return None
        stop_fraction = dict(signal.features).get("catastrophe_stop_fraction")
        if stop_fraction is None or not 0 < stop_fraction < 1:
            return None
        entry = _tick(q.ask, ins.tick, ROUND_CEILING)
        stop = _tick(entry * (1 - stop_fraction), ins.tick, ROUND_FLOOR)
        return replace(signal, strategy_version=self.version, symbol=ins.symbol,
                       signal_id=f"{signal.signal_id}:{ins.symbol}", entry_price=entry, stop_loss=stop,
                       features=signal.features + (("source_entry", signal.entry_price),))
