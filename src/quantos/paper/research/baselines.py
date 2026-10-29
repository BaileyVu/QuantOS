"""Causal crash-price controls. Signal generation only; execution stays in the engine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import random
from typing import Mapping

from ..types import Signal, Snapshot
from ..strategies.candidate_features import HOUR_MS, closed_hourly_bars
from ..strategies.crash_flush_e2 import MAJORS, STOP_FRACTION, CrashFlushE2Strategy, crash_price_gate
from ..strategies.indicators import make_signal

D = Decimal
SEED = 28102026
BASELINE_ID = "crash_price_only_v1"
RANDOM_ID = "crash_price_matched_random_v1"


@dataclass(frozen=True)
class PriceOpportunity:
    snapshot: Snapshot
    close_ms: int
    entry: Decimal
    price_change_4h: Decimal
    high_24h: Decimal

    @property
    def symbol_month(self) -> tuple[str, str]:
        month = datetime.fromtimestamp((self.close_ms + 1) / 1000, timezone.utc)
        return self.snapshot.instrument.symbol, month.strftime("%Y-%m")


def eligible_crash_price(snapshot: Snapshot) -> PriceOpportunity | None:
    """Apply the strategy's same instrument, bar-validity, and price gate."""
    if (snapshot.instrument.symbol not in MAJORS or snapshot.instrument.margin_asset != "USDT"
            or snapshot.instrument.status != "TRADING" or snapshot.quote.symbol != snapshot.instrument.symbol):
        return None
    bars = closed_hourly_bars(snapshot.candles_1h, snapshot.at_ms, 24)
    if bars is None:
        return None
    result = crash_price_gate(bars)
    if result is None:
        return None
    current, change, high = result
    return PriceOpportunity(snapshot, current.close_ms, current.close, change, high)


def _signal(opportunity: PriceOpportunity, holding_hours: int, baseline_id: str) -> Signal | None:
    if holding_hours not in (24, 48, 72):
        raise ValueError("baseline hold must be 24, 48, or 72 hours")
    snap, entry = opportunity.snapshot, opportunity.entry
    return make_signal(
        snap, CrashFlushE2Strategy.strategy_id, CrashFlushE2Strategy.version,
        f"{baseline_id}:{opportunity.close_ms}:h{holding_hours}",
        "LONG", "MARKET", entry, entry * (1 - STOP_FRACTION), None,
        holding_hours * 3600, snap.at_ms + 90_000,
        f"{baseline_id}: 4h price decline and 8% drawdown from trailing 24h high; OI not required",
        (("baseline_flag", D(1)), ("price_change_4h", opportunity.price_change_4h),
         ("drawdown_from_24h_high", entry / opportunity.high_24h - 1),
         ("catastrophe_stop_fraction", STOP_FRACTION), ("holding_hours", D(holding_hours))),
    )


def price_only_signals(snapshots: tuple[Snapshot, ...], holding_hours: int = 24) -> tuple[Signal, ...]:
    """Emit every causal crash-price opportunity without consulting OI or outcomes."""
    out = []
    for snapshot in snapshots:
        item = eligible_crash_price(snapshot)
        if item is not None:
            signal = _signal(item, holding_hours, BASELINE_ID)
            if signal is not None:
                out.append(signal)
    return tuple(out)


def matched_random_signals(
    snapshots: tuple[Snapshot, ...], event_counts: Mapping[tuple[str, str], int],
    holding_hours: int = 24, seed: int = SEED,
) -> tuple[Signal, ...]:
    """Sample eligible crash-price bars by symbol/month, matching supplied OI counts.

    The interface accepts only counts and as-of snapshots, never later prices
    or returns. Sampling is without replacement and fails if a stratum lacks bars.
    """
    if holding_hours not in (24, 48, 72):
        raise ValueError("baseline hold must be 24, 48, or 72 hours")
    groups: dict[tuple[str, str], list[PriceOpportunity]] = {}
    seen = set()
    for snapshot in snapshots:
        identity = (snapshot.instrument.symbol, snapshot.at_ms)
        if identity in seen:
            raise ValueError(f"duplicate symbol/time snapshot: {identity}")
        seen.add(identity)
        item = eligible_crash_price(snapshot)
        if item is not None:
            groups.setdefault(item.symbol_month, []).append(item)
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in event_counts.values()):
        raise ValueError("event counts must be nonnegative integers")
    rng, selected = random.Random(seed), []
    for key in sorted(event_counts):
        count = event_counts[key]
        candidates = sorted(groups.get(key, ()), key=lambda x: x.close_ms)
        if count > len(candidates):
            raise ValueError(f"insufficient crash-price bars for {key}: {count} requested, {len(candidates)} available")
        selected.extend(rng.sample(candidates, count))
    selected.sort(key=lambda x: (x.snapshot.instrument.symbol, x.close_ms))
    out = []
    for item in selected:
        signal = _signal(item, holding_hours, RANDOM_ID)
        if signal is not None:
            out.append(signal)
    if len(out) != len(selected):
        raise ValueError("a sampled opportunity could not be represented as a signal")
    return tuple(out)


@dataclass(frozen=True)
class DelayedIntent:
    original_decision: Signal
    delayed_decision: Signal


def delay_market_intent_one_hour(original: Signal, next_snapshot: Snapshot) -> DelayedIntent | None:
    """Create a distinct next-hour intent; leave original decision and expiry intact."""
    if (original.entry_type != "MARKET" or next_snapshot.at_ms != original.observed_ms + HOUR_MS
            or next_snapshot.instrument.symbol != original.symbol):
        return None
    bars = closed_hourly_bars(next_snapshot.candles_1h, next_snapshot.at_ms, 1)
    if bars is None:
        return None
    entry = bars[-1].close
    if original.strategy_id in ("crash_flush_e2", "alt_dip_reversal"):
        stop = entry * (1 - STOP_FRACTION)
        target = None
    elif original.strategy_id == "ema_level_2h":
        stop = original.stop_loss
        risk = entry - stop if original.side == "LONG" else stop - entry
        if risk <= 0:
            return None
        target = entry + (D("1.5") * risk if original.side == "LONG" else -D("1.5") * risk)
    else:
        return None
    signature = hashlib.sha256(original.signal_id.encode()).hexdigest()
    delayed = make_signal(
        next_snapshot, original.strategy_id, original.strategy_version,
        f"delay_1h:{signature}:{next_snapshot.at_ms}", original.side, "MARKET", entry,
        stop, target, original.maximum_hold_seconds, next_snapshot.at_ms + 90_000,
        f"one-hour delayed intent from decision {original.observed_ms}; original decision retained",
        tuple(original.features) + (("delay_1h_flag", D(1)), ("original_decision_ms", D(original.observed_ms))),
    )
    return None if delayed is None else DelayedIntent(original, delayed)
