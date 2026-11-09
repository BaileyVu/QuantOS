"""Consume completed-bar alt-ML scores in the USDC demo portfolio."""
import json
import os
from decimal import Decimal

from ..types import Snapshot
from .indicators import closed_bars, make_signal

BAR_MS = 300_000
HOLD_SECONDS = 24 * 3600
TARGET_FRACTION = Decimal("0.02")
STOP_FRACTION = Decimal("0.04")
SOFT_STOP_FRACTION = Decimal("0.02")
USDC_TWINS = frozenset({
    "ADAUSDC", "AVAXUSDC", "BCHUSDC", "BNBUSDC", "DOGEUSDC", "ETHUSDC",
    "FILUSDC", "LINKUSDC", "LTCUSDC", "SOLUSDC", "XRPUSDC",
})


class Ml5mAltsStrategy:
    strategy_id = "ml5m_alts"
    version = "0.1.0"

    def __init__(self, path, input_observer=None):
        self.path = path
        self.input_observer = input_observer
        self.observation_error = None
        self._version = None
        self._data = {}

    def _signals(self):
        try:
            with open(self.path, "rb") as source:
                stat = os.fstat(source.fileno())
                version = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                if version == self._version:
                    return self._data
                raw = source.read()
            data = json.loads(raw)
        except (OSError, ValueError):
            return {}
        if self.input_observer is not None:
            try:
                self.input_observer("ml5m_alts", raw, None)
            except Exception as error:
                self.observation_error = type(error).__name__
        self._version = version
        self._data = data if isinstance(data, dict) else {}
        return self._data

    def evaluate(self, snapshot: Snapshot):
        if snapshot.instrument.margin_asset != "USDC" or snapshot.instrument.symbol not in USDC_TWINS:
            return None
        bars = closed_bars(snapshot, "5m", 1)
        if not bars:
            return None
        data = self._signals()
        bar = data.get("bar_close_ms")
        if (type(bar) is not int or bar != bars[-1].close_ms
                or (bar + 1) % (3 * BAR_MS) != BAR_MS
                or not 0 <= snapshot.at_ms - bar < BAR_MS):
            return None
        threshold = data.get("thr")
        rows = data.get("signals")
        if type(threshold) not in (int, float) or not 0 <= threshold <= 1 or not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict) or row.get("symbol") != snapshot.instrument.symbol:
                continue
            score = row.get("score")
            if row.get("side") != "LONG" or type(score) not in (int, float) or not threshold <= score <= 1:
                return None
            entry = snapshot.quote.ask
            if not entry.is_finite() or entry <= 0:
                return None
            return make_signal(snapshot, self.strategy_id, self.version, str(bar), "LONG", "MARKET", entry,
                               entry * (1 - STOP_FRACTION), entry * (1 + TARGET_FRACTION),
                               HOLD_SECONDS, bar + BAR_MS, "5m alt-ML score at or above rolling p99",
                               (("score", Decimal(str(score))),))
        return None
