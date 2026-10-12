"""Explicit clock-domain contracts for HFT live-paper diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
import math

from quantos.domain.common import require_utc


def _wall_delta_ms(later: datetime, earlier: datetime) -> Decimal:
    delta = later - earlier
    microseconds = (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )
    return Decimal(microseconds) / Decimal("1000")


class HftClockHealth(str, Enum):
    CALIBRATING = "CALIBRATING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNSAFE = "UNSAFE"


@dataclass(frozen=True, slots=True)
class HftClockSample:
    local_send_wall: datetime
    local_receive_wall: datetime
    server_wall: datetime
    local_send_monotonic: float
    local_receive_monotonic: float

    def __post_init__(self) -> None:
        require_utc(self.local_send_wall, "local_send_wall")
        require_utc(self.local_receive_wall, "local_receive_wall")
        require_utc(self.server_wall, "server_wall")
        for name in ("local_send_monotonic", "local_receive_monotonic"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.local_receive_monotonic < self.local_send_monotonic:
            raise ValueError("monotonic clock moved backwards during calibration")
        if self.local_receive_wall < self.local_send_wall:
            raise ValueError("wall clock moved backwards during calibration")

    @property
    def rtt_ms(self) -> Decimal:
        return Decimal(str(
            (self.local_receive_monotonic - self.local_send_monotonic) * 1000
        ))

    @property
    def local_midpoint(self) -> datetime:
        return self.local_send_wall + (
            self.local_receive_wall - self.local_send_wall
        ) / 2

    @property
    def offset_ms(self) -> Decimal:
        return _wall_delta_ms(self.server_wall, self.local_midpoint)

    def payload(self) -> dict:
        return {
            "local_send_wall": self.local_send_wall,
            "local_receive_wall": self.local_receive_wall,
            "server_wall": self.server_wall,
            "rtt_ms": self.rtt_ms,
            "offset_ms": self.offset_ms,
        }


@dataclass(frozen=True, slots=True)
class HftClockCalibration:
    offset_ms: Decimal
    selected_rtt_ms: Decimal
    samples: tuple[HftClockSample, ...]
    selected_samples: tuple[HftClockSample, ...]
    calibrated_at_wall: datetime
    calibrated_at_monotonic: float

    def payload(self) -> dict:
        return {
            "exchange_clock_offset_ms": self.offset_ms,
            "calibration_rtt_ms": self.selected_rtt_ms,
            "calibration_timestamp": self.calibrated_at_wall,
            "calibration_rtt_samples_ms": [
                sample.rtt_ms for sample in self.samples
            ],
            "calibration_offset_samples_ms": [
                sample.offset_ms for sample in self.samples
            ],
            "selected_calibration_samples": [
                sample.payload() for sample in self.selected_samples
            ],
        }


def robust_clock_calibration(
    samples: tuple[HftClockSample, ...],
    lowest_rtt_sample_count: int,
) -> HftClockCalibration:
    if not samples:
        raise ValueError("clock calibration requires samples")
    if not 1 <= lowest_rtt_sample_count <= len(samples):
        raise ValueError("invalid lowest-RTT sample count")
    selected = tuple(sorted(
        samples, key=lambda sample: (sample.rtt_ms, sample.offset_ms)
    )[:lowest_rtt_sample_count])

    def median(values: list[Decimal]) -> Decimal:
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / Decimal("2")

    return HftClockCalibration(
        offset_ms=median([sample.offset_ms for sample in selected]),
        selected_rtt_ms=median([sample.rtt_ms for sample in selected]),
        samples=samples,
        selected_samples=selected,
        calibrated_at_wall=max(
            sample.local_receive_wall for sample in samples
        ),
        calibrated_at_monotonic=max(
            sample.local_receive_monotonic for sample in samples
        ),
    )


@dataclass(frozen=True, slots=True)
class HftFeedLatency:
    raw_uncalibrated_latency_ms: Decimal
    corrected_signed_latency_ms: Decimal
    observed_feed_latency_ms: Decimal


class HftClockMonitor:
    """Tracks calibrated exchange/local clock health without mutating clocks."""

    def __init__(
        self,
        negative_tolerance_ms: Decimal,
        excessive_negative_limit: int,
        recalibration_interval: timedelta,
    ) -> None:
        if negative_tolerance_ms < 0:
            raise ValueError("negative-latency tolerance must be non-negative")
        if excessive_negative_limit < 1:
            raise ValueError("excessive-negative limit must be positive")
        if recalibration_interval <= timedelta(0):
            raise ValueError("recalibration interval must be positive")
        self.negative_tolerance_ms = negative_tolerance_ms
        self.excessive_negative_limit = excessive_negative_limit
        self.recalibration_interval_seconds = (
            recalibration_interval.total_seconds()
        )
        self.health = HftClockHealth.CALIBRATING
        self.calibration: HftClockCalibration | None = None
        self.latest: HftFeedLatency | None = None
        self.negative_latency_observation_count = 0
        self.excessive_negative_latency_count = 0
        self._consecutive_excessive = 0
        self._verification_after_recalibration = False
        self.recalibration_requested = False

    @property
    def quoting_allowed(self) -> bool:
        return self.health is HftClockHealth.HEALTHY

    def apply_calibration(self, calibration: HftClockCalibration) -> None:
        self._verification_after_recalibration = self.calibration is not None
        self.calibration = calibration
        self.health = (
            HftClockHealth.DEGRADED
            if self._verification_after_recalibration
            else HftClockHealth.HEALTHY
        )
        self._consecutive_excessive = 0
        self.recalibration_requested = False

    def mark_calibration_failed(self) -> None:
        self.health = HftClockHealth.UNSAFE
        self.recalibration_requested = False

    def calibration_due(self, monotonic_now: float) -> bool:
        if self.health is HftClockHealth.UNSAFE:
            return False
        if self.recalibration_requested:
            return True
        if self.calibration is None:
            return True
        return (
            monotonic_now - self.calibration.calibrated_at_monotonic
            >= self.recalibration_interval_seconds
        )

    def seconds_until_calibration(self, monotonic_now: float) -> float:
        if self.recalibration_requested or self.calibration is None:
            return 0.0
        due = (
            self.calibration.calibrated_at_monotonic
            + self.recalibration_interval_seconds
        )
        return max(due - monotonic_now, 0.0)

    def observe(
        self,
        exchange_event_time: datetime,
        local_receive_wall: datetime,
    ) -> HftFeedLatency:
        require_utc(exchange_event_time, "exchange_event_time")
        require_utc(local_receive_wall, "local_receive_wall")
        if self.calibration is None:
            raise RuntimeError("exchange clock is not calibrated")
        raw = _wall_delta_ms(local_receive_wall, exchange_event_time)
        corrected_receive = local_receive_wall + timedelta(
            microseconds=int(self.calibration.offset_ms * Decimal("1000"))
        )
        corrected = _wall_delta_ms(corrected_receive, exchange_event_time)
        observation = HftFeedLatency(
            raw_uncalibrated_latency_ms=raw,
            corrected_signed_latency_ms=corrected,
            observed_feed_latency_ms=max(corrected, Decimal("0")),
        )
        self.latest = observation
        if corrected < 0:
            self.negative_latency_observation_count += 1
        if corrected < -self.negative_tolerance_ms:
            self.excessive_negative_latency_count += 1
            self._consecutive_excessive += 1
            if self._consecutive_excessive >= self.excessive_negative_limit:
                if self._verification_after_recalibration:
                    self.health = HftClockHealth.UNSAFE
                    self.recalibration_requested = False
                else:
                    self.health = HftClockHealth.DEGRADED
                    self.recalibration_requested = True
        else:
            self._consecutive_excessive = 0
            self._verification_after_recalibration = False
            if self.health is not HftClockHealth.UNSAFE:
                self.health = HftClockHealth.HEALTHY
        return observation

    def payload(self, monotonic_now: float) -> dict:
        calibration = self.calibration
        latest = self.latest
        age = None
        if calibration is not None:
            age = max(
                monotonic_now - calibration.calibrated_at_monotonic, 0.0
            )
        return {
            "exchange_clock_offset_ms": (
                calibration.offset_ms if calibration is not None else None
            ),
            "calibration_rtt_ms": (
                calibration.selected_rtt_ms
                if calibration is not None else None
            ),
            "raw_feed_latency_ms": (
                latest.raw_uncalibrated_latency_ms
                if latest is not None else None
            ),
            "corrected_feed_latency_ms": (
                latest.corrected_signed_latency_ms
                if latest is not None else None
            ),
            "observed_feed_latency_ms": (
                latest.observed_feed_latency_ms
                if latest is not None else None
            ),
            "negative_latency_observation_count": (
                self.negative_latency_observation_count
            ),
            "excessive_negative_latency_count": (
                self.excessive_negative_latency_count
            ),
            "clock_health": self.health.value,
            "last_clock_calibration_age_seconds": age,
        }
