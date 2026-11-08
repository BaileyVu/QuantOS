"""1h candle/trend-location ML signals (edge_vault/patml_v1): an external scorer (LightGBM, frozen 2020-2024 models)
writes signals.json just after each 1h close; this strategy turns a fresh one for the snapshot's symbol into a
MARKET signal with a +-1.5 ATR target/stop and a 48h hold, so it runs through the normal risk layer and mirror.
Exploratory evidence only (failed the strict BUILD test; held-out +22..+33 bp); watched separately in the demo run."""
import json, os
from decimal import Decimal

from ..types import Snapshot
from .indicators import make_signal

D = Decimal
PATH = os.environ.get("QUANTOS_ML1H_SIGNALS", "")
FRESH_MS = 600_000  # act only within 10 minutes of the bar close the scorer used
K_ATR, HOLD_S = D("1.5"), 48 * 3600


class Ml1hPatternStrategy:
    strategy_id = "ml1h_pattern"
    version = "0.1.0"

    def __init__(self, path: str = PATH, input_observer=None):
        self.path, self._mtime, self._data = path, None, {}
        self.input_observer, self.observation_error = input_observer, None

    def _signals(self):
        try:
            m = os.stat(self.path).st_mtime
        except OSError:
            return {}
        if m != self._mtime:
            try:
                # Preserve the exact bytes consumed; an independent polling
                # read could observe a different producer version.
                with open(self.path, "rb") as source: raw = source.read()
                if self.input_observer is not None:
                    try: self.input_observer("ml1h", raw, None)
                    except Exception as exc: self.observation_error = type(exc).__name__
                self._data, self._mtime = json.loads(raw), m
            except (OSError, ValueError):
                return {}
        return self._data

    def evaluate(self, snapshot: Snapshot):
        if not self.path or snapshot.instrument.margin_asset != "USDC":
            return None
        d = self._signals()
        bar = d.get("bar_close_ms")
        if not isinstance(bar, int) or not 0 <= snapshot.at_ms - bar <= FRESH_MS:
            return None
        hit = next((s for s in d.get("signals", ()) if s.get("symbol") == snapshot.instrument.symbol), None)
        if hit is None or hit.get("side") not in ("LONG", "SHORT"):
            return None
        side, atr = hit["side"], D(str(hit["atr"]))
        q = snapshot.quote
        entry = q.ask if side == "LONG" else q.bid
        if not atr.is_finite() or atr <= 0 or not entry.is_finite() or entry <= 0:
            return None
        sign = 1 if side == "LONG" else -1
        stop, target = entry - sign * K_ATR * atr, entry + sign * K_ATR * atr
        return make_signal(snapshot, self.strategy_id, self.version, str(bar), side, "MARKET", entry, stop, target,
                           HOLD_S, bar + FRESH_MS, "1h pattern-ML score above training top 2%",
                           (("score", D(str(round(hit["score"], 6)))), ("atr_1h", atr)))
