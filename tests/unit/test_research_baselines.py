from dataclasses import replace
from decimal import Decimal as D
import unittest

from quantos.paper.types import Signal
from quantos.paper.strategies.candidate_features import HOUR_MS
from quantos.paper.strategies.crash_flush_e2 import CrashFlushE2Strategy
from quantos.paper.research.baselines import (
    BASELINE_ID, RANDOM_ID, delay_market_intent_one_hour, matched_random_signals,
    price_only_signals,
)
from tests.unit.test_portfolio_crash_flush import crash_snapshot


def shifted(snapshot, hours):
    delta = hours * HOUR_MS
    bars = tuple(replace(b, open_ms=b.open_ms + delta, close_ms=b.close_ms + delta)
                 for b in snapshot.candles_1h)
    oi = tuple(replace(v, time_ms=v.time_ms + delta, available_ms=v.available_ms + delta)
               for v in snapshot.oi_values)
    at = snapshot.at_ms + delta
    return replace(snapshot, at_ms=at, candles_1h=bars, oi_values=oi,
                   quote=replace(snapshot.quote, time_ms=at, received_ms=at))


class ResearchBaselineTests(unittest.TestCase):
    def test_price_only_matches_shared_price_gate_and_does_not_need_oi(self):
        snapshot = crash_snapshot()
        original = CrashFlushE2Strategy().evaluate(snapshot)
        no_oi = replace(snapshot, oi_values=())
        signal, = price_only_signals((no_oi,))
        self.assertEqual((signal.strategy_id, signal.strategy_version),
                         (original.strategy_id, original.strategy_version))
        self.assertEqual(signal.entry_price, original.entry_price)
        self.assertEqual(signal.stop_loss, original.stop_loss)
        self.assertEqual(signal.maximum_hold_seconds, original.maximum_hold_seconds)
        self.assertEqual(dict(signal.features)["baseline_flag"], D(1))
        self.assertNotEqual(signal.signal_id, original.signal_id)
        self.assertIn(BASELINE_ID, signal.reason)

    def test_future_rows_do_not_change_price_only_signal(self):
        snapshot = crash_snapshot()
        base, = price_only_signals((snapshot,))
        future_bar = replace(snapshot.candles_1h[-1],
                             open_ms=snapshot.at_ms, close_ms=snapshot.at_ms + HOUR_MS - 1)
        changed, = price_only_signals((replace(snapshot, candles_1h=snapshot.candles_1h + (future_bar,)),))
        self.assertEqual(base, changed)

    def test_matched_random_is_seeded_stratified_and_fails_closed(self):
        snapshot = crash_snapshot()
        snapshots = tuple(shifted(snapshot, i) for i in range(8))
        key = ("BTCUSDT", "1970-01")
        a = matched_random_signals(snapshots, {key: 4})
        b = matched_random_signals(snapshots, {key: 4})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 4)
        self.assertEqual(len({s.observed_ms for s in a}), 4)
        self.assertTrue(all(RANDOM_ID in s.reason and s.strategy_id == "crash_flush_e2" for s in a))
        with self.assertRaises(ValueError):
            matched_random_signals(snapshots[:1], {key: 2})
        with self.assertRaises(ValueError):
            matched_random_signals((snapshots[0], snapshots[0]), {key: 0})

    def test_delayed_crash_intent_is_new_and_keeps_original_record(self):
        snapshot = crash_snapshot()
        original = CrashFlushE2Strategy().evaluate(snapshot)
        next_snapshot = shifted(snapshot, 1)
        delayed = delay_market_intent_one_hour(original, next_snapshot)
        self.assertIsNotNone(delayed)
        self.assertEqual(delayed.original_decision, original)
        self.assertEqual(original.observed_ms, snapshot.at_ms)
        self.assertEqual(original.expires_ms, snapshot.at_ms + 90_000)
        self.assertEqual(delayed.delayed_decision.observed_ms, snapshot.at_ms + HOUR_MS)
        self.assertEqual(delayed.delayed_decision.stop_loss,
                         delayed.delayed_decision.entry_price * D("0.75"))
        self.assertEqual(delayed.delayed_decision.expires_ms,
                         next_snapshot.at_ms + 90_000)
        self.assertEqual(delayed.delayed_decision.maximum_hold_seconds,
                         original.maximum_hold_seconds)
        self.assertNotEqual(delayed.delayed_decision.signal_id, original.signal_id)
        self.assertIsNone(delay_market_intent_one_hour(original, shifted(snapshot, 2)))

    def test_delayed_ema_recomputes_r_from_new_entry_and_alt_resets_wide_stop(self):
        snapshot = crash_snapshot()
        next_snapshot = shifted(snapshot, 1)
        bars = list(next_snapshot.candles_1h)
        last = bars[-1]
        bars[-1] = replace(last, open=D(110), high=D(112), low=D(109), close=D(110))
        next_snapshot = replace(next_snapshot, candles_1h=tuple(bars))
        ema = Signal("ema_level_2h", "research-1.0.0", "ema-id", "BTCUSDT", "LONG",
                     snapshot.at_ms, snapshot.at_ms + 90_000, "MARKET", D(100), D(80),
                     D(130), 72 * 3600, "ema")
        delayed_ema = delay_market_intent_one_hour(ema, next_snapshot).delayed_decision
        self.assertEqual(delayed_ema.stop_loss, D(80))
        self.assertEqual(delayed_ema.take_profit, D(155))  # 110 + 1.5 * (110 - 80)
        alt = replace(ema, strategy_id="alt_dip_reversal", signal_id="alt-id")
        delayed_alt = delay_market_intent_one_hour(alt, next_snapshot).delayed_decision
        self.assertEqual(delayed_alt.stop_loss, D(82.50))
        self.assertIsNone(delayed_alt.take_profit)


if __name__ == "__main__":
    unittest.main()
