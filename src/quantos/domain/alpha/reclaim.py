"""Q05-LR-v1: frozen liquidity reclaim hypothesis, never a profitability claim."""
from dataclasses import dataclass, replace
from decimal import Decimal as D

from quantos.domain.alpha.hft import HftSide
from quantos.domain.alpha.retail import RetailSignal
from quantos.domain.alpha.retail_edge import CandidateDecision
from quantos.domain.features.reclaim import ReclaimFeatures


@dataclass(frozen=True, slots=True)
class ReclaimSignal(RetailSignal):
    target_price: D = D("0")
    invalidation_price: D = D("0")
    source_window: tuple[int, ...] = ()


class LiquidityReclaimAlpha:
    strategy = "Q05-LR-v1"

    def __init__(self):
        self.features = ReclaimFeatures()
        self.setup = None
        self.active = None
        self.cooldown_ns = 0

    def observe(self, f, now_ns):
        def no(reason, raw=False):
            return CandidateDecision(self.strategy, reason, raw)
        numeric = (f.mid_price, f.spread_bps, f.realized_volatility_bps,
                   f.static_obi, f.signed_trade_flow)
        if any(not x.is_finite() for x in numeric) or f.mid_price <= 0:
            raise ValueError("invalid reclaim features")
        window = self.features.observe(f, now_ns)
        if window is None:
            self.setup = self.active = None
            return no(self.features.reason)
        if not 0 < f.spread_bps <= 10 or not 0 <= f.realized_volatility_bps <= 20:
            self.setup = self.active = None
            return no("dislocated_or_wide_spread_regime")
        if self.active is not None:
            active = self.active
            direction = 1 if active.side is HftSide.BUY else -1
            if now_ns >= active.expires_ns:
                self.active = None
                return no("intent_expired_cooldown")
            if (f.mid_price - active.invalidation_price) * direction < active.invalidation_price * D(".0002"):
                self.active = None
                return no("reclaim_lost_before_fill")
            if f.static_obi * direction < D(".15") or f.signed_trade_flow * direction <= 0:
                self.active = None
                return no("flow_invalidated_before_fill")
            expected = (active.target_price / f.mid_price - 1) * 10000 * direction
            if expected <= 0:
                self.active = None
                return no("target_already_reached")
            return CandidateDecision(self.strategy, "reconfirmed", False,
                replace(active, book_update_id=f.book_update_id, expected_move_bps=expected,
                        uncertainty_bps=max(D("5"), expected / 2)))
        if now_ns < self.cooldown_ns:
            return no("setup_cooldown")
        if self.setup is None:
            if (window.high / window.low - 1) * 10000 < 60:
                return no("prior_range_below_60bps")
            displacement = max(D("10"), 2 * f.realized_volatility_bps)
            direction = 1 if (window.low / f.mid_price - 1) * 10000 >= displacement else -1 if (f.mid_price / window.high - 1) * 10000 >= displacement else 0
            if not direction:
                return no("no_liquidity_displacement")
            self.setup = {"direction": direction, "boundary": window.low if direction > 0 else window.high,
                "target": (window.low + window.high) / 2, "extreme": f.mid_price,
                "started": now_ns, "last_extreme": now_ns,
                "identity": f"{self.strategy}:{f.timestamp.isoformat()}:{f.book_update_id}",
                "sources": (window.first_ns, window.last_ns, window.first_book_id, window.last_book_id, f.book_update_id)}
            return no("displacement_observed", True)
        s = self.setup
        direction = s["direction"]
        if now_ns - s["started"] >= 120_000_000_000:
            self.setup = None
            self.cooldown_ns = now_ns + 300_000_000_000
            return no("continued_trend_or_setup_expired")
        if (f.mid_price - s["extreme"]) * direction < 0:
            s["extreme"], s["last_extreme"] = f.mid_price, now_ns
        if now_ns - s["started"] < 5_000_000_000 or now_ns - s["last_extreme"] < 3_000_000_000:
            return no("continuation_not_yet_failed")
        if (f.mid_price - s["boundary"]) * direction < s["boundary"] * D(".0002"):
            return no("no_observed_reclaim")
        expected = (s["target"] / f.mid_price - 1) * 10000 * direction
        if expected <= 0:
            self.setup = None
            self.cooldown_ns = now_ns + 300_000_000_000
            return no("target_already_reached")
        if f.static_obi * direction < D(".15") or f.signed_trade_flow * direction <= 0:
            return no("flow_or_imbalance_not_confirmed")
        signal = ReclaimSignal(s["identity"], HftSide.BUY if direction > 0 else HftSide.SELL,
            expected, (f.mid_price / s["extreme"] - 1) * 10000,
            f.book_update_id, now_ns, now_ns + 2_000_000_000,
            self.strategy, max(D("5"), expected / 2), 900,
            s["target"], s["boundary"], s["sources"])
        self.active, self.setup = signal, None
        self.cooldown_ns = now_ns + 300_000_000_000
        return CandidateDecision(self.strategy, "confirmed", True, signal)
