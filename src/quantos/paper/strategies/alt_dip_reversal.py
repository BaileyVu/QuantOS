"""Quarterly-calibrated, cross-sectional alt dip-reversal candidate.

The research-data layer supplies the fixed return threshold for a particular
quarter and quantile. This strategy only validates the calibration boundary
and compares the current coin's closed 24h return with that threshold.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from ..types import Signal, Snapshot
from .candidate_features import HOUR_MS, rolling_24h_return
from .indicators import make_signal, validated_bars

D = Decimal
HOLDING_HOURS = frozenset({12, 24, 72})
CUTOFF_QUANTILES = frozenset({D("0.005"), D("0.01")})
CALIBRATION_HOURS = 90 * 24
EMBARGO_HOURS = 3 * 24
STOP_FRACTION = D("0.25")


def _quarter_start_ms(time_ms: int) -> int:
    at = datetime.fromtimestamp(time_ms / 1000, timezone.utc)
    month = ((at.month - 1) // 3) * 3 + 1
    start = datetime(at.year, month, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000)


class AltDipReversalStrategy:
    strategy_id = "alt_dip_reversal"
    version = "research-1.0.0"

    def __init__(self, cutoff_quantile: Decimal, holding_hours: int):
        if cutoff_quantile not in CUTOFF_QUANTILES or holding_hours not in HOLDING_HOURS:
            raise ValueError("ALT candidate parameters must use the preregistered cutoff and hold grid")
        self.cutoff_quantile = cutoff_quantile
        self.holding_hours = holding_hours

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        if (snapshot.instrument.status != "TRADING"
                or snapshot.instrument.margin_asset != "USDT"
                or snapshot.quote.symbol != snapshot.instrument.symbol):
            return None
        bars = validated_bars(snapshot, "1h", HOUR_MS, 25)
        cutoff = snapshot.quarterly_return_cutoff
        if (bars is None or cutoff is None or not cutoff.is_finite()
                or snapshot.calibration_end_ms is None):
            return None
        current = bars[-1]
        quarter_start = _quarter_start_ms(current.close_ms)
        # The original baseline's calibration interval is [S-90d, S-3d).
        # calibration_end_ms is the exclusive upper boundary, not last row time.
        expected_calibration_end = quarter_start - EMBARGO_HOURS * HOUR_MS
        if snapshot.calibration_end_ms != expected_calibration_end:
            return None
        if not (quarter_start <= current.close_ms < quarter_start + 92 * 24 * HOUR_MS):
            return None
        returns = rolling_24h_return(bars)
        if not returns or returns[-1].time_ms != current.close_ms:
            return None
        ret_24h = returns[-1].value
        if ret_24h > cutoff:
            return None

        entry = current.close
        hold_seconds = self.holding_hours * 60 * 60
        return make_signal(
            snapshot, self.strategy_id, self.version,
            f"{current.close_ms}:q{self.cutoff_quantile}:h{self.holding_hours}",
            "LONG", "MARKET", entry, entry * (1 - STOP_FRACTION), None,
            hold_seconds, current.close_ms + 90_000,
            "closed 24h return is at or below the fixed pre-quarter cross-sectional cutoff",
            (("return_24h", ret_24h),
             ("quarterly_return_cutoff", cutoff),
             ("cutoff_quantile", self.cutoff_quantile),
             ("holding_hours", D(self.holding_hours)),
             ("catastrophe_stop_fraction", STOP_FRACTION)),
        )
