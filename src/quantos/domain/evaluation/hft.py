"""Latency, markout, and trading metrics for HFT paper sessions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from quantos.domain.execution.hft_paper import HftFill, HftPaperAccount


MARKOUT_HORIZONS = (1, 5, 10, 30, 60)


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = percentile * Decimal(len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass(frozen=True, slots=True)
class Markout:
    fill_id: str
    horizon_seconds: int
    markout_bps: Decimal
    alpha_bucket: str
    obi_bucket: str
    volatility_regime: str


@dataclass(slots=True)
class _PendingMarkout:
    fill: HftFill
    alpha_bucket: str
    obi_bucket: str
    volatility_regime: str
    completed: set[int]


class HftEvaluation:
    def __init__(self, starting_equity: Decimal) -> None:
        self.starting_equity = starting_equity
        self.latencies: dict[str, list[Decimal]] = {
            "feed_ms": [], "processing_ms": [], "ack_ms": []
        }
        self.markouts: list[Markout] = []
        self._pending: list[_PendingMarkout] = []
        self.equity_curve: list[Decimal] = [starting_equity]
        self.equity_points: list[tuple[datetime, Decimal]] = []
        self.kill_switch_events = 0
        self.directional_alpha_pnl = Decimal("0")
        self.gross_spread_capture = Decimal("0")

    def record_latency(
        self, feed_ms: Decimal, processing_ms: Decimal, acknowledgement_ms: Decimal
    ) -> None:
        for name, value in (
            ("feed_ms", feed_ms),
            ("processing_ms", processing_ms),
            ("ack_ms", acknowledgement_ms),
        ):
            if value < 0:
                raise ValueError("latency cannot be negative")
            self.latencies[name].append(value)

    def register_passive_fill(
        self,
        fill: HftFill,
        *,
        alpha_bps: Decimal,
        obi_z: Decimal,
        volatility_bps: Decimal,
    ) -> None:
        if not fill.maker:
            return
        self._pending.append(_PendingMarkout(
            fill=fill,
            alpha_bucket=self._bucket(abs(alpha_bps), (Decimal("1"), Decimal("3"))),
            obi_bucket=self._bucket(abs(obi_z), (Decimal("1"), Decimal("2"))),
            volatility_regime=self._bucket(
                volatility_bps, (Decimal("2"), Decimal("8"))
            ),
            completed=set(),
        ))

    def on_mid(self, timestamp: datetime, mid_price: Decimal) -> tuple[Markout, ...]:
        added: list[Markout] = []
        remaining: list[_PendingMarkout] = []
        for pending in self._pending:
            for horizon in MARKOUT_HORIZONS:
                if horizon in pending.completed:
                    continue
                if timestamp >= pending.fill.timestamp + timedelta(seconds=horizon):
                    direction = Decimal("1") if pending.fill.side.value == "BUY" else Decimal("-1")
                    bps = (
                        (mid_price - pending.fill.price)
                        / pending.fill.price * Decimal("10000") * direction
                    )
                    observation = Markout(
                        pending.fill.fill_id, horizon, bps,
                        pending.alpha_bucket, pending.obi_bucket,
                        pending.volatility_regime,
                    )
                    self.markouts.append(observation)
                    added.append(observation)
                    pending.completed.add(horizon)
            if len(pending.completed) < len(MARKOUT_HORIZONS):
                remaining.append(pending)
        self._pending = remaining
        return tuple(added)

    def record_equity(
        self, value: Decimal, timestamp: datetime | None = None
    ) -> None:
        self.equity_curve.append(value)
        if timestamp is not None:
            self.equity_points.append((timestamp, value))

    def report(self, account: HftPaperAccount, elapsed_seconds: Decimal) -> dict:
        maker_fills = sum(1 for fill in account.fills if fill.maker)
        round_trips = sum(
            1 for fill in account.fills if fill.role in {"EXIT", "SAFETY_EXIT"}
        )
        hours = elapsed_seconds / Decimal("3600") if elapsed_seconds > 0 else Decimal("0")
        fees = account.maker_fees + account.taker_fees
        net = account.balance - account.starting_equity
        queue_waits = [fill.queue_wait_ms for fill in account.fills if fill.maker]
        queue_ahead = [
            fill.queue_ahead_at_placement for fill in account.fills if fill.maker
        ]
        return {
            "quotes_submitted": account.quotes_submitted,
            "quotes_cancelled": account.quotes_cancelled,
            "cancelled_before_fill": account.cancelled_before_fill,
            "maker_fills": maker_fills,
            "taker_exits": account.taker_exits,
            "fill_rate": (
                Decimal(maker_fills) / Decimal(account.quotes_submitted)
                if account.quotes_submitted else Decimal("0")
            ),
            "trades_per_hour": (
                Decimal(len(account.fills)) / hours if hours else Decimal("0")
            ),
            "round_trips_per_hour": (
                Decimal(round_trips) / hours if hours else Decimal("0")
            ),
            "gross_spread_capture": self.gross_spread_capture,
            "directional_alpha_pnl": self.directional_alpha_pnl,
            "gross_pnl": account.realized_pnl,
            "maker_fees": account.maker_fees,
            "taker_fees": account.taker_fees,
            "funding": account.funding,
            "net_pnl": net,
            "net_pnl_per_fill": net / Decimal(len(account.fills)) if account.fills else Decimal("0"),
            "net_bps_per_round_trip": (
                net / account.starting_equity * Decimal("10000") / Decimal(round_trips)
                if round_trips else Decimal("0")
            ),
            "inventory_turnover": sum(
                (fill.price * fill.quantity for fill in account.fills), Decimal("0")
            ) / account.starting_equity,
            "average_holding_seconds": self._average_holding(account.fills),
            "queue_wait_ms": self._summary(queue_waits),
            "queue_ahead_at_placement": self._summary(queue_ahead),
            "fill_probability_diagnostics": {
                "model": "TRADE_ONLY_CONSERVATIVE",
                "empirical_fill_rate": (
                    Decimal(maker_fills) / Decimal(account.quotes_submitted)
                    if account.quotes_submitted else Decimal("0")
                ),
                "unexplained_depth_reduction_advances_queue": False,
                "touch_equals_fill": False,
            },
            "latency": {
                name: self._summary(values) for name, values in self.latencies.items()
            },
            "markouts": {
                str(horizon): self._markout_summary(horizon)
                for horizon in MARKOUT_HORIZONS
            },
            "markout_by_alpha_bucket": self._grouped_markouts("alpha_bucket"),
            "markout_by_obi_bucket": self._grouped_markouts("obi_bucket"),
            "markout_by_volatility_regime": self._grouped_markouts("volatility_regime"),
            "maximum_drawdown": self._maximum_drawdown(),
            "sharpe": self._minute_sharpe(),
            "sharpe_aggregation": "one_minute_last_equity",
            "kill_switch_events": self.kill_switch_events,
        }

    @staticmethod
    def _bucket(value: Decimal, cuts: tuple[Decimal, Decimal]) -> str:
        if value < cuts[0]:
            return "LOW"
        if value < cuts[1]:
            return "MEDIUM"
        return "HIGH"

    @staticmethod
    def _summary(values: list[Decimal]) -> dict:
        return {
            "median": _percentile(values, Decimal(".5")),
            "p90": _percentile(values, Decimal(".9")),
            "p99": _percentile(values, Decimal(".99")),
        }

    def _markout_summary(self, horizon: int) -> dict:
        values = [item.markout_bps for item in self.markouts if item.horizon_seconds == horizon]
        if not values:
            return {"count": 0, "average": None, "median": None, "positive_fraction": None, "negative_fraction": None}
        count = Decimal(len(values))
        return {
            "count": len(values),
            "average": sum(values, Decimal("0")) / count,
            "median": _percentile(values, Decimal(".5")),
            "positive_fraction": Decimal(sum(value > 0 for value in values)) / count,
            "negative_fraction": Decimal(sum(value < 0 for value in values)) / count,
        }

    def _grouped_markouts(self, field: str) -> dict:
        result = {}
        for bucket in sorted({getattr(item, field) for item in self.markouts}):
            values = [item.markout_bps for item in self.markouts if getattr(item, field) == bucket]
            result[bucket] = {
                "count": len(values),
                "average": sum(values, Decimal("0")) / Decimal(len(values)),
                "median": _percentile(values, Decimal(".5")),
            }
        return result

    @staticmethod
    def _average_holding(fills: list[HftFill]) -> Decimal:
        entries: dict[str, datetime] = {}
        durations: list[Decimal] = []
        last_entry: datetime | None = None
        for fill in fills:
            if fill.role == "ENTRY":
                last_entry = fill.timestamp
            elif fill.role in {"EXIT", "SAFETY_EXIT"} and last_entry is not None:
                durations.append(Decimal(str((fill.timestamp - last_entry).total_seconds())))
                last_entry = None
        return sum(durations, Decimal("0")) / Decimal(len(durations)) if durations else Decimal("0")

    def _maximum_drawdown(self) -> Decimal:
        peak = self.equity_curve[0]
        result = Decimal("0")
        for value in self.equity_curve:
            peak = max(peak, value)
            if peak > 0:
                result = max(result, (peak - value) / peak)
        return result

    def _minute_sharpe(self) -> Decimal | None:
        by_minute: dict[datetime, Decimal] = {}
        for timestamp, value in self.equity_points:
            by_minute[timestamp.replace(second=0, microsecond=0)] = value
        values = [by_minute[key] for key in sorted(by_minute)]
        if len(values) < 3:
            return None
        returns = [
            current / previous - Decimal("1")
            for previous, current in zip(values, values[1:])
            if previous > 0
        ]
        if len(returns) < 2:
            return None
        mean = sum(returns, Decimal("0")) / Decimal(len(returns))
        variance = sum(
            ((value - mean) ** 2 for value in returns), Decimal("0")
        ) / Decimal(len(returns))
        if variance == 0:
            return None
        return mean / variance.sqrt() * Decimal("525600").sqrt()
