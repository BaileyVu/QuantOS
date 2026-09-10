"""Causal, hand-checkable and deterministic candidate feature acceptance tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import (
    Context, Decimal, DefaultContext, FloatOperation, Inexact, ROUND_DOWN,
    ROUND_HALF_EVEN, getcontext, localcontext,
)
from pathlib import Path
import socket
import time
import unittest
from unittest.mock import patch

from quantos.domain.features import (
    FEATURE_NAMES, FEATURE_VERSION, MIN_HISTORY, FeatureEngineError,
    FeatureVector, compute_feature_vector,
)
from quantos.domain.market_data import Candle, MarketEvent

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
MICROSECOND = timedelta(microseconds=1)
EXPECTED_NAMES = (
    "return_1m", "return_15m", "realized_volatility_20m", "efficiency_ratio_20m",
    "sma_spread_5_20", "momentum_balance_14", "volume_ratio_20", "true_range_pct",
    "range_position_20", "completed_5m_return",
)


def candles(
    count: int = 21, *, symbol: str = "BTCUSDT", start: datetime = START,
) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            symbol=symbol, interval="1m", open_time=start + index * MINUTE,
            close_time=start + (index + 1) * MINUTE - MICROSECOND,
            open=Decimal(99 + index), high=Decimal(122 + index),
            low=Decimal(80 + index), close=Decimal(100 + index),
            volume=Decimal(1 + index), quote_volume=Decimal(100), trade_count=1,
        )
        for index in range(count)
    )


def flat(candle: Candle, price: str) -> Candle:
    value = Decimal(price)
    return replace(candle, open=value, high=value, low=value, close=value)


def compute(sequence: tuple[Candle, ...]) -> FeatureVector | None:
    return compute_feature_vector(sequence, decision_time=sequence[-1].close_time)


def signature(vector: FeatureVector) -> tuple:
    """Compare Decimal representations too, including trailing digits/signs."""
    return tuple((name, value.as_tuple()) for name, value in vector.values.items())


class FeatureContractTests(unittest.TestCase):
    def test_exact_candidate_version_names_and_count(self) -> None:
        self.assertEqual(FEATURE_VERSION, "candidate-v1")
        self.assertIs(type(FEATURE_NAMES), tuple)
        self.assertEqual(FEATURE_NAMES, EXPECTED_NAMES)
        self.assertEqual(len(FEATURE_NAMES), 10)
        result = compute(candles())
        self.assertEqual(result.feature_version, "candidate-v1")
        self.assertEqual(tuple(result.values), EXPECTED_NAMES)

    def test_both_v1_symbols_share_the_same_calculations(self) -> None:
        btc = compute(candles())
        eth = compute(candles(symbol="ETHUSDT"))
        self.assertEqual(btc.symbol, "BTCUSDT")
        self.assertEqual(eth.symbol, "ETHUSDT")
        self.assertEqual(signature(btc), signature(eth))

    def test_output_remains_an_immutable_feature_vector(self) -> None:
        result = compute(candles())
        self.assertIs(type(result), FeatureVector)
        with self.assertRaises(TypeError):
            result.values["return_1m"] = Decimal(5)
        with self.assertRaises(FrozenInstanceError):
            result.feature_version = "changed"

    def test_only_finite_builtin_decimal_values_are_emitted(self) -> None:
        for value in compute(candles()).values.values():
            self.assertIs(type(value), Decimal)
            self.assertTrue(value.is_finite())

    def test_caller_inputs_remain_unchanged(self) -> None:
        source = candles()
        before = tuple(replace(candle) for candle in source)
        compute(source)
        self.assertEqual(source, before)


class FeatureFormulaTests(unittest.TestCase):
    def test_all_ten_formulas_against_hand_calculated_values(self) -> None:
        # 100,200 alternating for 20 candles, then 400. Returns: eleven +1,
        # nine -0.5; squared sum=13.25, RMS=sqrt(0.6625). Total movement=2100,
        # net movement=300; last 14 movement=1500, net=300. SMA5=200, SMA20=165.
        # Volumes 2..21 average 11.5. Last true range=220. Low=80, high=420.
        # Latest complete bucket is 00:15..00:19: open=190 and close=200.
        source = tuple(
            replace(candle, open=Decimal(price - 10), high=Decimal(price + 20),
                    low=Decimal(price - 20), close=Decimal(price))
            for candle, price in zip(candles(), [100, 200] * 10 + [400])
        )
        expected = (
            "1", "1", "0.8139410298049853193676507954991917",
            "0.1428571428571428571428571428571429",
            "0.212121212121212121212121212121212", "0.2",
            "0.826086956521739130434782608695652", "1.1",
            "0.9411764705882352941176470588235294",
            "0.052631578947368421052631578947368",
        )
        result = compute(source)
        self.assertEqual(tuple(result.values), EXPECTED_NAMES)
        for name, value in zip(EXPECTED_NAMES, expected):
            with self.subTest(feature=name):
                self.assertEqual(result.values[name], Decimal(value))

    def test_monotonic_rise_has_positive_returns_and_unit_trend_momentum(self) -> None:
        values = compute(candles()).values
        for name in ("return_1m", "return_15m", "sma_spread_5_20"):
            self.assertGreater(values[name], Decimal(0))
        self.assertEqual(values["efficiency_ratio_20m"], Decimal(1))
        self.assertEqual(values["momentum_balance_14"], Decimal(1))

    def test_monotonic_fall_has_negative_returns_and_negative_unit_momentum(self) -> None:
        source = tuple(flat(candle, str(200 - index)) for index, candle in enumerate(candles()))
        values = compute(source).values
        for name in ("return_1m", "return_15m", "sma_spread_5_20"):
            self.assertLess(values[name], Decimal(0))
        self.assertEqual(values["efficiency_ratio_20m"], Decimal(1))
        self.assertEqual(values["momentum_balance_14"], Decimal(-1))

    def test_constant_prices_produce_unavailable_not_neutral_imputation(self) -> None:
        self.assertIsNone(compute(tuple(flat(candle, "100") for candle in candles())))

    def test_reversals_have_zero_net_momentum_with_positive_movement(self) -> None:
        source = tuple(flat(candle, str(100 + index % 2)) for index, candle in enumerate(candles()))
        values = compute(source).values
        self.assertEqual(values["efficiency_ratio_20m"], Decimal(0))
        self.assertEqual(values["momentum_balance_14"], Decimal(0))
        self.assertGreater(values["realized_volatility_20m"], Decimal(0))

    def test_constant_positive_volume_has_zero_volume_ratio(self) -> None:
        source = tuple(replace(candle, volume=Decimal(2)) for candle in candles())
        self.assertEqual(compute(source).values["volume_ratio_20"], Decimal(0))

    def test_zero_current_volume_is_valid_when_mean_volume_is_positive(self) -> None:
        source = candles()
        source = source[:-1] + (replace(source[-1], volume=Decimal(0)),)
        self.assertEqual(compute(source).values["volume_ratio_20"], Decimal(-1))

    def test_true_range_uses_high_low_and_both_gap_directions(self) -> None:
        source = candles()
        # Previous close is 119. Respectively: intrabar range, gap up, gap down.
        for opened, high, low, close, expected in (
            ("119", "121", "117", "120", "4"),
            ("130", "132", "129", "131", "13"),
            ("108", "110", "106", "109", "13"),
        ):
            with self.subTest(high=high, low=low):
                final = replace(source[-1], open=Decimal(opened), high=Decimal(high),
                                low=Decimal(low), close=Decimal(close))
                with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
                    ratio = Decimal(expected) / Decimal(119)
                self.assertEqual(compute(source[:-1] + (final,)).values["true_range_pct"], ratio)

    def test_range_position_endpoints_are_zero_and_one(self) -> None:
        source = candles()
        for price, expected in (("81", "0"), ("142", "1")):
            with self.subTest(price=price):
                final = replace(source[-1], open=Decimal(price), close=Decimal(price),
                                low=Decimal(81), high=Decimal(142))
                self.assertEqual(compute(source[:-1] + (final,)).values["range_position_20"],
                                 Decimal(expected))


class FeatureAvailabilityTests(unittest.TestCase):
    def test_exact_first_ready_candle_is_the_twenty_first(self) -> None:
        source = candles()
        self.assertEqual(MIN_HISTORY, 21)
        for count in range(1, 21):
            with self.subTest(count=count):
                self.assertIsNone(compute(source[:count]))
        self.assertEqual(compute(source).timestamp, START + 21 * MINUTE - MICROSECOND)

    def test_first_ready_has_no_hidden_padding_for_any_five_minute_phase(self) -> None:
        for phase in range(5):
            with self.subTest(phase=phase):
                source = candles(start=START + phase * MINUTE)
                self.assertIsNone(compute(source[:-1]))
                self.assertIsNotNone(compute(source))

    def test_large_older_prefix_does_not_change_same_asof_result(self) -> None:
        source = candles(1000)
        self.assertEqual(signature(compute(source)), signature(compute(source[-21:])))

    def test_zero_previous_close_blocks_return_1m_and_true_range(self) -> None:
        source = candles()
        self.assertIsNone(compute(source[:19] + (flat(source[19], "0"),) + source[20:]))

    def test_zero_fifteen_minute_reference_close_is_unavailable(self) -> None:
        source = candles()
        self.assertIsNone(compute(source[:5] + (flat(source[5], "0"),) + source[6:]))

    def test_zero_oldest_return_denominator_blocks_twenty_return_volatility(self) -> None:
        source = candles()
        self.assertIsNone(compute((flat(source[0], "0"),) + source[1:]))

    def test_zero_twenty_transition_movement_is_unavailable(self) -> None:
        source = tuple(replace(candle, open=Decimal(100), close=Decimal(100),
                               low=Decimal(99)) for candle in candles())
        self.assertIsNone(compute(source))

    def test_zero_fourteen_transition_movement_with_earlier_trend_is_unavailable(self) -> None:
        source = candles()
        source = source[:6] + tuple(flat(candle, "106") for candle in source[6:])
        self.assertIsNone(compute(source))

    def test_zero_sma_denominator_is_unavailable(self) -> None:
        source = candles()
        # With non-negative prices, zero SMA necessarily overlaps zero returns.
        self.assertIsNone(compute(source[:1] + tuple(flat(candle, "0") for candle in source[1:])))

    def test_zero_mean_volume_is_unavailable(self) -> None:
        self.assertIsNone(compute(tuple(replace(candle, volume=Decimal(0)) for candle in candles())))

    def test_zero_range_denominator_is_unavailable(self) -> None:
        source = candles()
        # Zero valid OHLC range necessarily also implies constant recent closes.
        self.assertIsNone(compute(source[:1] + tuple(flat(candle, "101") for candle in source[1:])))

    def test_zero_bucket_open_is_unavailable(self) -> None:
        source = candles()
        first = replace(source[15], open=Decimal(0), low=Decimal(0))
        self.assertIsNone(compute(source[:15] + (first,) + source[16:]))

    def test_zero_latest_close_is_not_blanket_rejected(self) -> None:
        source = candles()
        source = source[:-1] + (flat(source[-1], "0"),)
        self.assertEqual(compute(source).values["return_1m"], Decimal(-1))

    def test_no_exact_utc_bucket_is_unavailable_without_rounding_input_times(self) -> None:
        # The existing Candle contract permits shifted opens. They cannot form
        # the required fixed UTC bucket, even when 21 valid candles are present.
        self.assertIsNone(compute(candles(start=START + timedelta(seconds=1))))


class FeatureInputTests(unittest.TestCase):
    def test_non_tuple_and_empty_inputs_are_errors(self) -> None:
        for source in ((), [], list(candles()), iter(candles()), None, "candles"):
            with self.subTest(input_type=type(source)):
                with self.assertRaises(FeatureEngineError):
                    compute_feature_vector(source, decision_time=START)

    def test_non_candle_is_an_error(self) -> None:
        with self.assertRaises(FeatureEngineError):
            compute_feature_vector((object(),), decision_time=START)

    def test_invalid_symbol_and_interval_are_errors_even_if_constructor_bypassed(self) -> None:
        for field, value in (("symbol", "SOLUSDT"), ("interval", "5m")):
            with self.subTest(field=field):
                source = candles()
                object.__setattr__(source[0], field, value)
                with self.assertRaises(FeatureEngineError):
                    compute(source)

    def test_mixed_supported_symbols_are_an_error(self) -> None:
        source = candles()
        source = source[:-1] + (replace(source[-1], symbol="ETHUSDT"),)
        with self.assertRaisesRegex(FeatureEngineError, "one symbol"):
            compute(source)

    def test_duplicate_gap_reverse_and_subminute_spacing_are_errors(self) -> None:
        source = candles()
        shifted = replace(source[1], open_time=source[1].open_time + MICROSECOND)
        for invalid in (source[:1] + source, source[:5] + source[6:],
                        tuple(reversed(source)), source[:1] + (shifted,) + source[2:]):
            with self.subTest(opens=tuple(candle.open_time for candle in invalid[:3])):
                with self.assertRaises(FeatureEngineError):
                    compute_feature_vector(invalid, decision_time=source[-1].close_time)

    def test_all_candle_contract_fields_are_revalidated(self) -> None:
        invalid_fields = (
            ("open", Decimal(-1)), ("high", Decimal(0)), ("low", Decimal(999)),
            ("close", Decimal("NaN")), ("volume", Decimal(-1)),
            ("quote_volume", Decimal("Infinity")), ("trade_count", True),
            ("trade_count", -1), ("volume", 1),
            ("open_time", START.replace(tzinfo=None)), ("close_time", START),
        )
        for field, value in invalid_fields:
            with self.subTest(field=field, value=value):
                source = candles()
                object.__setattr__(source[0], field, value)
                with self.assertRaises(FeatureEngineError):
                    compute(source)

    def test_invalid_old_prefix_is_not_discarded(self) -> None:
        source = candles(30)
        object.__setattr__(source[0], "volume", Decimal(-1))
        with self.assertRaises(FeatureEngineError):
            compute(source)

    def test_malformed_input_is_not_masked_by_insufficient_history(self) -> None:
        source = candles(1)
        object.__setattr__(source[0], "trade_count", "1")
        with self.assertRaises(FeatureEngineError):
            compute(source)

    def test_decision_requires_builtin_utc_datetime(self) -> None:
        class ExtendedDatetime(datetime):
            pass

        invalid_times = (
            None, "2026-01-01", START.replace(tzinfo=None),
            START.astimezone(timezone(timedelta(hours=7))),
            ExtendedDatetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for decision_time in invalid_times:
            with self.subTest(decision_time=decision_time):
                with self.assertRaises(FeatureEngineError):
                    compute_feature_vector(candles(), decision_time=decision_time)


class FeatureCausalityTests(unittest.TestCase):
    def test_final_candle_one_microsecond_before_completion_is_an_error(self) -> None:
        source = candles()
        with self.assertRaisesRegex(FeatureEngineError, "complete"):
            compute_feature_vector(source, decision_time=source[-1].close_time - MICROSECOND)

    def test_completion_equality_and_explicit_later_timestamp_are_preserved(self) -> None:
        source = candles()
        completed = compute(source)
        later = source[-1].close_time + timedelta(seconds=2)
        delayed = compute_feature_vector(source, decision_time=later)
        self.assertEqual(completed.timestamp, source[-1].close_time)
        self.assertEqual(delayed.timestamp, later)
        self.assertEqual(signature(completed), signature(delayed))

    def test_every_input_close_is_checked_not_only_the_final_candle(self) -> None:
        source = candles()
        first = replace(source[0], close_time=source[-1].close_time + MICROSECOND)
        with self.assertRaisesRegex(FeatureEngineError, "complete"):
            compute((first,) + source[1:])

    def test_future_input_cannot_be_silently_dropped_for_an_earlier_asof(self) -> None:
        source = candles(30)
        with self.assertRaises(FeatureEngineError):
            compute_feature_vector(source, decision_time=source[20].close_time)

    def test_later_calculation_cannot_change_an_earlier_vector(self) -> None:
        source = candles(30)
        earlier = compute(source[:21])
        before = signature(earlier)
        compute(source)
        self.assertEqual(signature(earlier), before)
        self.assertEqual(signature(compute(source[:21])), before)

    def test_fixed_utc_buckets_at_each_required_rollover_boundary(self) -> None:
        source = tuple(
            replace(candle, open=Decimal(100), low=Decimal(90),
                    close=Decimal(170 + index), high=Decimal(172 + index))
            for index, candle in enumerate(candles(41, start=START - 30 * MINUTE))
        )
        for minute, expected in ((3, "0.99"), (4, "1.04"), (5, "1.04"),
                                 (8, "1.04"), (9, "1.09"), (10, "1.09")):
            with self.subTest(minute=minute):
                prefix = source[:31 + minute]
                result = compute(prefix)
                self.assertEqual(result.values["completed_5m_return"], Decimal(expected))
                self.assertEqual(result.timestamp, prefix[-1].close_time)

        # At 00:04's closure minus one microsecond, the bucket cannot be used.
        with self.assertRaises(FeatureEngineError):
            compute_feature_vector(source[:35], decision_time=source[34].close_time - MICROSECOND)
        before = compute_feature_vector(source[:34], decision_time=source[34].close_time - MICROSECOND)
        self.assertEqual(before.values["completed_5m_return"], Decimal("0.99"))

    def test_context_uses_actual_close_time_instead_of_inferred_minute_end(self) -> None:
        source = candles(25)
        final = replace(source[-1], close_time=source[-1].close_time + timedelta(seconds=5))
        source = source[:-1] + (final,)
        with self.assertRaises(FeatureEngineError):
            compute_feature_vector(source, decision_time=START + 25 * MINUTE)
        self.assertIsNotNone(compute(source))

    def test_historical_and_live_canonical_inputs_have_identical_semantics(self) -> None:
        historical = candles()
        live = tuple(MarketEvent(candle.close_time, replace(candle)) for candle in historical)
        result = compute_feature_vector(tuple(event.candle for event in live),
                                        decision_time=live[-1].timestamp)
        self.assertEqual(signature(compute(historical)), signature(result))


class FeatureDeterminismTests(unittest.TestCase):
    def test_repeated_calculations_are_exact_including_decimal_representation(self) -> None:
        source = candles()
        expected = signature(compute(source))
        for _ in range(10):
            self.assertEqual(signature(compute(source)), expected)

    def test_hostile_ambient_context_does_not_change_values_or_global_context(self) -> None:
        source = candles()
        expected = signature(compute(source))
        with localcontext() as hostile:
            hostile.prec = 3
            hostile.rounding = ROUND_DOWN
            hostile.Emin = -2
            hostile.Emax = 2
            hostile.clamp = 1
            hostile.capitals = 0
            for signal in hostile.traps:
                hostile.traps[signal] = True
            hostile.flags[Inexact] = True
            before = repr(hostile)
            self.assertEqual(signature(compute(source)), expected)
            self.assertIs(getcontext(), hostile)
            self.assertEqual(repr(hostile), before)

    def test_mutated_default_context_does_not_change_calculations(self) -> None:
        source = candles()
        expected = signature(compute(source))
        original = DefaultContext.copy()
        try:
            DefaultContext.prec = 2
            DefaultContext.rounding = ROUND_DOWN
            DefaultContext.Emin = -2
            DefaultContext.Emax = 2
            DefaultContext.clamp = 1
            DefaultContext.capitals = 0
            for signal in DefaultContext.traps:
                DefaultContext.traps[signal] = True
            DefaultContext.flags[Inexact] = True
            self.assertEqual(signature(compute(source)), expected)
        finally:
            for field in ("prec", "rounding", "Emin", "Emax", "clamp", "capitals"):
                setattr(DefaultContext, field, getattr(original, field))
            DefaultContext.traps = original.traps
            DefaultContext.flags = original.flags

    def test_float_operation_trap_remains_enabled_without_any_float_conversion(self) -> None:
        source = candles()
        with localcontext() as context:
            context.traps[FloatOperation] = True
            self.assertIsNotNone(compute(source))
            self.assertFalse(context.flags[FloatOperation])

    def test_overflow_and_underflow_fail_closed_as_domain_errors(self) -> None:
        source = candles()
        # Dividing the smallest normal value by 3 requires inexact subnormal
        # rounding; dividing by 1 would be exact and must remain permitted.
        for huge, tiny in (("1E+999999", "1"), ("3", "1E-999999")):
            with self.subTest(huge=huge, tiny=tiny):
                values = tuple(flat(candle, huge) for candle in source[:-1]) + (flat(source[-1], tiny),)
                with self.assertRaises(FeatureEngineError):
                    compute(values)

    def test_computation_has_no_clock_network_or_filesystem_side_effects(self) -> None:
        source = candles()
        with (
            patch.object(time, "time", side_effect=AssertionError("wall clock")),
            patch.object(time, "monotonic", side_effect=AssertionError("clock")),
            patch.object(socket, "socket", side_effect=AssertionError("network")),
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch("builtins.open", side_effect=AssertionError("filesystem")),
            patch.object(Path, "open", side_effect=AssertionError("filesystem")),
        ):
            self.assertIsNotNone(compute(source))
