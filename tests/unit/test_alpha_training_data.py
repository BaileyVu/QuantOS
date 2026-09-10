"""Exact target semantics, chronological accounting, and adversarial leakage tests."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DefaultContext, FloatOperation, Inexact, ROUND_DOWN, localcontext
from pathlib import Path
import socket
import time
from types import MappingProxyType
import unittest
from unittest.mock import patch

from quantos.domain.alpha import (
    TARGET_HORIZON_MINUTES, TARGET_VERSION, TargetLabel, TrainingDataError,
    TrainingDataset, TrainingExample, build_training_dataset,
)
from quantos.domain.features import FEATURE_NAMES, FEATURE_VERSION, compute_feature_vector
from quantos.domain.market_data import Candle, DatasetIdentity, DatasetValidationStatus, ValidatedCandleSequence, validate_candle_sequence

START = datetime(2025, 1, 1, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
US = timedelta(microseconds=1)
BUILDER_FEATURE = "quantos.domain.alpha.training_data.compute_feature_vector"


def source(count=26, *, symbol="BTCUSDT", close_offset=MINUTE-US):
    candles = tuple(Candle(
        symbol=symbol, interval="1m", open_time=START+i*MINUTE,
        close_time=START+i*MINUTE+close_offset, open=Decimal(90+i),
        high=Decimal(200+i), low=Decimal(50+i), close=Decimal(100+i),
        volume=Decimal(1+i), quote_volume=Decimal(1000+i), trade_count=i,
    ) for i in range(count))
    return validated(candles)


def validated(candles):
    identity = DatasetIdentity(
        symbol=candles[0].symbol, timeframe="1m", start_time=candles[0].open_time,
        end_time=candles[-1].open_time, source="synthetic-research",
        schema_version="candle-v1", ingestion_version="fixture-v1",
    )
    return validate_candle_sequence(identity, candles)


def changed(sequence, index, **fields):
    candle = sequence.candles[index]
    opened = fields.get("open", candle.open)
    closed = fields.get("close", candle.close)
    fields.setdefault("high", max(candle.high, opened, closed))
    fields.setdefault("low", min(candle.low, opened, closed))
    candles = sequence.candles[:index] + (replace(candle, **fields),) + sequence.candles[index+1:]
    return validated(candles)


def feature_signature(feature):
    return (feature.timestamp, feature.symbol, feature.feature_version,
            tuple((name, value.as_tuple()) for name, value in feature.values.items()))


def dataset_signature(dataset):
    return (dataset.source_identity, dataset.feature_version, dataset.target_version,
            dataset.horizon_minutes, dataset.unavailable_feature_timestamps,
            tuple((feature_signature(e.feature), e.label.decision_time, e.label.symbol,
                   e.label.target_version, e.label.horizon_minutes, e.label.entry_reference_time,
                   e.label.exit_reference_time, e.label.value.as_tuple()) for e in dataset.examples))


class AlphaTrainingContractTests(unittest.TestCase):
    def setUp(self):
        self.source = source()
        self.dataset = build_training_dataset(self.source)
        self.example = self.dataset.examples[0]

    def test_exact_target_constants_and_provenance(self):
        self.assertEqual(TARGET_VERSION, "gross-next-open-to-close-5m-v1")
        self.assertEqual(TARGET_HORIZON_MINUTES, 5)
        self.assertIs(type(TARGET_HORIZON_MINUTES), int)
        self.assertIs(self.dataset.source_identity, self.source.identity)
        self.assertIs(self.dataset.source_identity.validation_status, DatasetValidationStatus.VALIDATED)
        self.assertEqual(self.dataset.feature_version, "candidate-v1")
        self.assertEqual(self.dataset.target_version, TARGET_VERSION)
        self.assertEqual(self.dataset.horizon_minutes, 5)
        self.assertEqual(self.dataset.source_identity, self.source.identity)

    def test_label_example_and_dataset_are_immutable(self):
        for obj, field, value in ((self.example.label, "value", Decimal(9)),
                                  (self.example, "label", None), (self.dataset, "examples", ())):
            with self.subTest(contract=type(obj).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(obj, field, value)
        self.assertIs(type(self.dataset.examples), tuple)
        self.assertIs(type(self.dataset.unavailable_feature_timestamps), tuple)
        with self.assertRaises(TypeError):
            self.example.feature.values["target"] = self.example.label.value

    def test_label_rejects_wrong_version_horizon_and_symbol(self):
        for field, value in (("target_version", "other"), ("horizon_minutes", 4),
                             ("horizon_minutes", True), ("symbol", "SOLUSDT")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(TrainingDataError):
                    replace(self.example.label, **{field: value})

    def test_label_rejects_non_decimal_nonfinite_and_impossible_returns(self):
        for value in (1, 1.0, Decimal("NaN"), Decimal("Infinity"), Decimal("-1.01")):
            with self.subTest(value=value):
                with self.assertRaises(TrainingDataError):
                    replace(self.example.label, value=value)

    def test_label_requires_builtin_utc_timestamps(self):
        class ExtendedDatetime(datetime):
            pass

        values = (None, START.replace(tzinfo=None),
                  START.astimezone(timezone(timedelta(hours=7))),
                  ExtendedDatetime(2025, 1, 1, tzinfo=timezone.utc))
        for field in ("decision_time", "entry_reference_time", "exit_reference_time"):
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(TrainingDataError):
                        replace(self.example.label, **{field: value})

    def test_label_requires_strict_future_references_and_full_horizon(self):
        label = self.example.label
        for fields in ({"entry_reference_time": label.decision_time},
                       {"entry_reference_time": label.decision_time-US},
                       {"exit_reference_time": label.entry_reference_time},
                       {"exit_reference_time": label.entry_reference_time+4*MINUTE}):
            with self.subTest(fields=fields):
                with self.assertRaises(TrainingDataError):
                    replace(label, **fields)

    def test_example_rejects_symbol_and_timestamp_mismatch(self):
        for fields in ({"symbol": "ETHUSDT"}, {"timestamp": self.example.feature.timestamp-US}):
            with self.subTest(fields=fields):
                with self.assertRaises(TrainingDataError):
                    TrainingExample(replace(self.example.feature, **fields), self.example.label)

    def test_example_rejects_feature_version_mismatch(self):
        with self.assertRaisesRegex(TrainingDataError, "feature_version"):
            TrainingExample(replace(self.example.feature, feature_version="wrong-v1"), self.example.label)

    def test_example_rejects_missing_extra_or_reordered_feature_names(self):
        values = dict(self.example.feature.values)
        cases = (dict(list(values.items())[1:]), {**values, "target": Decimal(1)},
                 dict(reversed(tuple(values.items()))))
        for candidate in cases:
            with self.subTest(names=tuple(candidate)):
                with self.assertRaises(TrainingDataError):
                    TrainingExample(replace(self.example.feature, values=candidate), self.example.label)

    def test_example_rejects_forged_label_and_mutable_feature_mapping(self):
        feature = replace(self.example.feature)
        object.__setattr__(feature, "values", dict(feature.values))
        with self.assertRaises(TrainingDataError):
            TrainingExample(feature, self.example.label)
        label = replace(self.example.label)
        object.__setattr__(label, "horizon_minutes", 3)
        with self.assertRaises(TrainingDataError):
            TrainingExample(self.example.feature, label)

    def test_example_snapshots_a_mapping_proxy_backed_by_mutable_caller_data(self):
        feature = replace(self.example.feature)
        caller_values = dict(feature.values)
        object.__setattr__(feature, "values", MappingProxyType(caller_values))
        example = TrainingExample(feature, self.example.label)
        before = feature_signature(example.feature)
        caller_values[FEATURE_NAMES[0]] = Decimal(999)
        self.assertEqual(feature_signature(example.feature), before)

    def test_dataset_rejects_mutable_containers(self):
        for fields in ({"examples": list(self.dataset.examples)},
                       {"unavailable_feature_timestamps": []}):
            with self.subTest(fields=fields):
                with self.assertRaises(TrainingDataError):
                    replace(self.dataset, **fields)

    def test_dataset_rejects_version_horizon_and_source_mismatch(self):
        for fields in ({"feature_version": "other"}, {"target_version": "other"},
                       {"horizon_minutes": 4}, {"source_identity": source(symbol="ETHUSDT").identity}):
            with self.subTest(fields=fields):
                with self.assertRaises(TrainingDataError):
                    replace(self.dataset, **fields)

    def test_dataset_revalidates_forged_example_feature_version(self):
        object.__setattr__(self.example.feature, "feature_version", "wrong")
        with self.assertRaises(TrainingDataError):
            replace(self.dataset)

    def test_dataset_rejects_missing_candidate_accounting(self):
        with self.assertRaisesRegex(TrainingDataError, "accounting"):
            replace(self.dataset, examples=())

    def test_dataset_snapshots_forged_caller_backed_feature_mapping(self):
        caller_values = dict(self.example.feature.values)
        object.__setattr__(self.example.feature, "values", MappingProxyType(caller_values))
        snapshot = replace(self.dataset)
        before = dataset_signature(snapshot)
        caller_values[FEATURE_NAMES[0]] = Decimal(999)
        self.assertEqual(dataset_signature(snapshot), before)

    def test_dataset_rejects_duplicate_reversed_and_out_of_range_decisions(self):
        dataset = build_training_dataset(source(28))
        for examples in ((dataset.examples[0],)*3, tuple(reversed(dataset.examples))):
            with self.subTest(timestamps=tuple(e.feature.timestamp for e in examples)):
                with self.assertRaises(TrainingDataError):
                    replace(dataset, examples=examples)
        with self.assertRaises(TrainingDataError):
            replace(self.dataset, examples=(), unavailable_feature_timestamps=(START+19*MINUTE+timedelta(seconds=30),))

    def test_dataset_rejects_wrong_entry_open_even_with_ordered_label(self):
        label = replace(self.example.label, entry_reference_time=self.example.label.entry_reference_time+US)
        example = TrainingExample(self.example.feature, label)
        with self.assertRaisesRegex(TrainingDataError, "next source candle"):
            replace(self.dataset, examples=(example,))


class AlphaTargetFormulaTests(unittest.TestCase):
    def test_hand_calculated_positive_negative_and_zero_returns(self):
        for exit_price, expected in (("105", "0.05"), ("95", "-0.05"), ("100", "0")):
            with self.subTest(exit_price=exit_price):
                sequence = changed(source(), 21, open=Decimal(100))
                sequence = changed(sequence, 25, close=Decimal(exit_price))
                label = build_training_dataset(sequence).examples[0].label
                self.assertEqual(label.value, Decimal(expected))
                self.assertIs(type(label.value), Decimal)
                self.assertTrue(label.value.is_finite())
                self.assertEqual(label.value.as_tuple(), Decimal(expected).as_tuple())

    def test_entry_is_next_open_not_current_close_or_next_close(self):
        sequence = changed(source(), 21, open=Decimal(100))
        sequence = changed(sequence, 25, close=Decimal(105))
        self.assertNotEqual(sequence.candles[20].close, Decimal(100))
        self.assertNotEqual(sequence.candles[21].close, Decimal(100))
        self.assertEqual(build_training_dataset(sequence).examples[0].label.value, Decimal("0.05"))

    def test_exit_is_t_plus_five_not_four_or_six(self):
        sequence = changed(source(27), 21, open=Decimal(100))
        sequence = changed(sequence, 24, close=Decimal(900))
        sequence = changed(sequence, 25, close=Decimal(105))
        sequence = changed(sequence, 26, close=Decimal(700))
        self.assertEqual(build_training_dataset(sequence).examples[0].label.value, Decimal("0.05"))

    def test_high_precision_inputs_use_precision_34_round_half_even(self):
        sequence = changed(source(), 21, open=Decimal("3.000000000000000000000000000000000001"))
        sequence = changed(sequence, 25, close=Decimal("10.000000000000000000000000000000000007"))
        value = build_training_dataset(sequence).examples[0].label.value
        self.assertEqual(value.as_tuple(), Decimal("2.333333333333333333333333333333333").as_tuple())

    def test_zero_entry_denominator_fails_closed(self):
        with self.assertRaisesRegex(TrainingDataError, "zero target entry open"):
            build_training_dataset(changed(source(), 21, open=Decimal(0)))

    def test_zero_exit_is_valid_minus_one_return(self):
        value = build_training_dataset(changed(source(), 25, close=Decimal(0))).examples[0].label.value
        self.assertEqual(value, Decimal(-1))

    def test_target_overflow_and_inexact_underflow_fail_closed(self):
        for entry, exit_price in (("1E-999999", "1E+999999"), ("3", "1E-999999")):
            with self.subTest(entry=entry, exit_price=exit_price):
                sequence = changed(source(), 21, open=Decimal(entry))
                sequence = changed(sequence, 25, close=Decimal(exit_price))
                with self.assertRaises(TrainingDataError):
                    build_training_dataset(sequence)


class AlphaTrainingBoundaryTests(unittest.TestCase):
    def test_all_short_valid_sources_through_25_have_no_candidates(self):
        for count in range(1, 26):
            with self.subTest(count=count):
                sequence = source(count)
                dataset = build_training_dataset(sequence)
                self.assertEqual(dataset.candidate_decision_count, 0)
                self.assertEqual(dataset.examples, ())
                self.assertEqual(dataset.unavailable_feature_timestamps, ())
                self.assertIs(dataset.source_identity, sequence.identity)

    def test_26_candles_have_exactly_one_candidate_at_index_20(self):
        sequence = source()
        dataset = build_training_dataset(sequence)
        self.assertEqual(dataset.candidate_decision_count, 1)
        self.assertEqual(len(dataset.examples), 1)
        example = dataset.examples[0]
        self.assertEqual(example.feature.timestamp, sequence.candles[20].close_time)
        self.assertEqual(example.label.entry_reference_time, sequence.candles[21].open_time)
        self.assertEqual(example.label.exit_reference_time, sequence.candles[25].close_time)

    def test_full_day_has_1415_chronological_examples_and_no_truncated_labels(self):
        sequence = source(1440)
        dataset = build_training_dataset(sequence)
        self.assertEqual(dataset.candidate_decision_count, 1415)
        self.assertEqual(len(dataset.examples), 1415)
        self.assertEqual(dataset.unavailable_feature_timestamps, ())
        for i, example in enumerate(dataset.examples, start=20):
            self.assertEqual(example.feature.timestamp, sequence.candles[i].close_time)
            self.assertEqual(example.label.entry_reference_time, sequence.candles[i+1].open_time)
            self.assertEqual(example.label.exit_reference_time, sequence.candles[i+5].close_time)
            self.assertLess(example.feature.timestamp, example.label.entry_reference_time)
            self.assertLess(example.label.entry_reference_time, example.label.exit_reference_time)
        self.assertEqual(dataset.examples[-1].feature.timestamp, START+1435*MINUTE-US)
        self.assertEqual(dataset.examples[-1].label.entry_reference_time, START+1435*MINUTE)
        self.assertEqual(dataset.examples[-1].label.exit_reference_time, START+1440*MINUTE-US)

    def test_last_candidate_is_n_minus_six_and_neighbors_are_not_skipped(self):
        sequence = source(31)
        timestamps = tuple(e.feature.timestamp for e in build_training_dataset(sequence).examples)
        self.assertEqual(timestamps, tuple(c.close_time for c in sequence.candles[20:26]))

    def test_both_v1_symbols_are_supported_independently(self):
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                dataset = build_training_dataset(source(30, symbol=symbol))
                self.assertEqual(len(dataset.examples), 5)
                self.assertTrue(all(e.feature.symbol == e.label.symbol == symbol for e in dataset.examples))

    def test_actual_close_times_are_preserved_without_provider_precision_assumptions(self):
        for offset in (timedelta(seconds=30), MINUTE-timedelta(milliseconds=1), MINUTE-US):
            with self.subTest(offset=offset):
                sequence = source(close_offset=offset)
                example = build_training_dataset(sequence).examples[0]
                self.assertEqual(example.feature.timestamp, START+20*MINUTE+offset)
                self.assertEqual(example.label.exit_reference_time, START+25*MINUTE+offset)

    def test_entry_at_or_before_decision_time_is_rejected_without_timestamp_repair(self):
        for offset in (MINUTE, MINUTE+US):
            with self.subTest(offset=offset):
                with self.assertRaises(TrainingDataError):
                    build_training_dataset(source(close_offset=offset))


class AlphaMissingFeatureTests(unittest.TestCase):
    def constant_source(self, count=30):
        return validated(tuple(replace(c, close=Decimal(100)) for c in source(count).candles))

    def test_all_unavailable_rows_are_explicitly_accounted(self):
        sequence = self.constant_source()
        dataset = build_training_dataset(sequence)
        self.assertEqual(dataset.examples, ())
        self.assertEqual(dataset.unavailable_feature_timestamps, tuple(c.close_time for c in sequence.candles[20:25]))
        self.assertEqual(dataset.candidate_decision_count, 5)

    def test_one_missing_feature_row_does_not_discard_later_available_rows(self):
        sequence = source(30)
        candles = tuple(replace(c, close=Decimal(100)) if i <= 20 else c for i, c in enumerate(sequence.candles))
        sequence = validated(candles)
        dataset = build_training_dataset(sequence)
        self.assertEqual(dataset.unavailable_feature_timestamps, (sequence.candles[20].close_time,))
        self.assertEqual(tuple(e.feature.timestamp for e in dataset.examples), tuple(c.close_time for c in sequence.candles[21:25]))
        self.assertEqual(len(dataset.examples)+len(dataset.unavailable_feature_timestamps), 5)

    def test_zero_target_entry_remains_error_when_features_are_unavailable(self):
        sequence = changed(self.constant_source(26), 21, open=Decimal(0))
        with self.assertRaisesRegex(TrainingDataError, "zero target entry open"):
            build_training_dataset(sequence)

    def test_unavailable_accounting_rejects_duplicate_reversed_and_overlapping_rows(self):
        dataset = build_training_dataset(self.constant_source(27))
        times = dataset.unavailable_feature_timestamps
        for candidate in ((times[0], times[0]), tuple(reversed(times))):
            with self.subTest(candidate=candidate):
                with self.assertRaises(TrainingDataError):
                    replace(dataset, unavailable_feature_timestamps=candidate)
        available = build_training_dataset(source(27))
        with self.assertRaises(TrainingDataError):
            replace(available, examples=available.examples[:1], unavailable_feature_timestamps=(available.examples[0].feature.timestamp,))


class AlphaCausalityTests(unittest.TestCase):
    def assert_feature_unchanged(self, before, after):
        self.assertEqual(before.feature, after.feature)
        self.assertEqual(feature_signature(before.feature), feature_signature(after.feature))

    def test_future_entry_open_changes_target_but_not_feature(self):
        sequence = source(40)
        before = build_training_dataset(sequence).examples[0]
        after = build_training_dataset(changed(sequence, 21, open=Decimal(222))).examples[0]
        self.assert_feature_unchanged(before, after)
        self.assertNotEqual(before.label.value, after.label.value)

    def test_future_exit_close_changes_target_but_not_feature(self):
        sequence = source(40)
        before = build_training_dataset(sequence).examples[0]
        after = build_training_dataset(changed(sequence, 25, close=Decimal(300))).examples[0]
        self.assert_feature_unchanged(before, after)
        self.assertNotEqual(before.label.value, after.label.value)

    def test_beyond_horizon_changes_neither_earlier_feature_nor_label(self):
        sequence = source(40)
        before = build_training_dataset(sequence).examples[0]
        after = build_training_dataset(changed(sequence, 26, open=Decimal(800), close=Decimal(900))).examples[0]
        self.assert_feature_unchanged(before, after)
        self.assertEqual(before.label, after.label)
        self.assertEqual(before.label.value.as_tuple(), after.label.value.as_tuple())

    def test_intermediate_future_candle_does_not_leak_into_features(self):
        sequence = source(40)
        before = build_training_dataset(sequence).examples[0]
        after = build_training_dataset(changed(sequence, 23, close=Decimal(700), volume=Decimal(99999))).examples[0]
        self.assert_feature_unchanged(before, after)
        self.assertEqual(before.label, after.label)

    def test_every_example_matches_independently_computed_causal_features(self):
        sequence = source(50)
        for i, example in enumerate(build_training_dataset(sequence).examples, start=20):
            expected = compute_feature_vector(sequence.candles[:i+1], decision_time=sequence.candles[i].close_time)
            self.assertEqual(feature_signature(example.feature), feature_signature(expected))
            self.assertEqual(tuple(example.feature.values), FEATURE_NAMES)

    def test_feature_engine_receives_only_final_21_available_candles(self):
        sequence = source(35)
        with patch(BUILDER_FEATURE, wraps=compute_feature_vector) as compute:
            build_training_dataset(sequence)
        self.assertEqual(compute.call_count, 10)
        for i, call in enumerate(compute.call_args_list, start=20):
            self.assertEqual(call.args, (sequence.candles[i-20:i+1],))
            self.assertEqual(call.kwargs, {"decision_time": sequence.candles[i].close_time})
            self.assertTrue(all(c.close_time <= call.kwargs["decision_time"] for c in call.args[0]))

    def test_forged_feature_version_from_engine_is_not_silently_rewritten(self):
        sequence = source()
        feature = compute_feature_vector(sequence.candles[:21], decision_time=sequence.candles[20].close_time)
        feature = replace(feature, feature_version="wrong-version")
        with patch(BUILDER_FEATURE, return_value=feature):
            with self.assertRaises(TrainingDataError):
                build_training_dataset(sequence)
        self.assertEqual(feature.feature_version, "wrong-version")


class AlphaSequenceSafetyTests(unittest.TestCase):
    def test_requires_actual_validated_sequence(self):
        for candidate in (None, (), source().candles, source().identity):
            with self.subTest(candidate_type=type(candidate)):
                with self.assertRaises(TrainingDataError):
                    build_training_dataset(candidate)

    def test_rejects_unvalidated_or_forged_identity(self):
        for field, value in (("symbol", "SOLUSDT"), ("timeframe", "5m"),
                             ("source", ""), ("schema_version", ""), ("ingestion_version", None),
                             ("start_time", START-MINUTE), ("end_time", START+30*MINUTE),
                             ("validation_status", DatasetValidationStatus.UNVALIDATED),
                             ("validation_status", "validated")):
            with self.subTest(field=field):
                sequence = source()
                object.__setattr__(sequence.identity, field, value)
                with self.assertRaises(TrainingDataError):
                    build_training_dataset(sequence)

    def test_rejects_wrong_candle_symbol_interval_and_identity_mismatch(self):
        for field, value in (("symbol", "SOLUSDT"), ("symbol", "ETHUSDT"), ("interval", "5m")):
            with self.subTest(field=field, value=value):
                sequence = source()
                object.__setattr__(sequence.candles[0], field, value)
                with self.assertRaises(TrainingDataError):
                    build_training_dataset(sequence)

    def test_rejects_duplicates_gaps_reversal_mutable_or_empty_candles(self):
        for transform in (lambda x: x[:1]+x, lambda x: x[:2]+x[3:],
                          lambda x: tuple(reversed(x)), lambda x: list(x), lambda x: ()):
            sequence = source()
            object.__setattr__(sequence, "candles", transform(sequence.candles))
            with self.assertRaises(TrainingDataError):
                build_training_dataset(sequence)

    def test_revalidates_invalid_candle_fields_even_in_unused_tail(self):
        for field, value in (("open", Decimal(-1)), ("high", Decimal(0)),
                             ("low", Decimal(9999)), ("close", Decimal("NaN")),
                             ("volume", Decimal(-1)), ("quote_volume", Decimal("Infinity")),
                             ("trade_count", True), ("volume", 1.0),
                             ("open_time", START.replace(tzinfo=None)), ("close_time", START)):
            with self.subTest(field=field):
                sequence = source()
                object.__setattr__(sequence.candles[-1], field, value)
                with self.assertRaises(TrainingDataError):
                    build_training_dataset(sequence)

    def test_short_malformed_source_is_not_accepted_as_empty_dataset(self):
        sequence = source(3)
        object.__setattr__(sequence.candles[1], "trade_count", -1)
        with self.assertRaises(TrainingDataError):
            build_training_dataset(sequence)

    def test_future_unavailable_history_is_an_error_not_a_missing_feature(self):
        sequence = source()
        first = replace(sequence.candles[0], close_time=sequence.candles[20].close_time+US)
        sequence = validated((first,)+sequence.candles[1:])
        with self.assertRaises(TrainingDataError):
            build_training_dataset(sequence)


class AlphaTrainingDeterminismTests(unittest.TestCase):
    def test_repeated_build_is_exact_and_does_not_mutate_source(self):
        sequence = source(60)
        before = tuple(replace(c) for c in sequence.candles)
        first = build_training_dataset(sequence)
        for _ in range(3):
            again = build_training_dataset(sequence)
            self.assertEqual(first, again)
            self.assertEqual(dataset_signature(first), dataset_signature(again))
        self.assertEqual(sequence.candles, before)

    def test_hostile_ambient_context_is_neither_inherited_nor_mutated(self):
        sequence = source(40)
        expected = dataset_signature(build_training_dataset(sequence))
        with localcontext() as hostile:
            hostile.prec = 2
            hostile.rounding = ROUND_DOWN
            hostile.Emin, hostile.Emax, hostile.capitals, hostile.clamp = -2, 2, 0, 1
            for signal in hostile.traps:
                hostile.traps[signal] = True
            hostile.flags[Inexact] = True
            before = repr(hostile)
            self.assertEqual(dataset_signature(build_training_dataset(sequence)), expected)
            self.assertEqual(repr(hostile), before)

    def test_mutated_default_context_does_not_change_any_contract(self):
        sequence = source(40)
        expected = dataset_signature(build_training_dataset(sequence))
        original = DefaultContext.copy()
        try:
            DefaultContext.prec, DefaultContext.rounding = 2, ROUND_DOWN
            DefaultContext.Emin, DefaultContext.Emax = -2, 2
            DefaultContext.capitals, DefaultContext.clamp = 0, 1
            for signal in DefaultContext.traps:
                DefaultContext.traps[signal] = True
            DefaultContext.flags[Inexact] = True
            before = repr(DefaultContext)
            self.assertEqual(dataset_signature(build_training_dataset(sequence)), expected)
            self.assertEqual(repr(DefaultContext), before)
        finally:
            for field in ("prec", "rounding", "Emin", "Emax", "capitals", "clamp"):
                setattr(DefaultContext, field, getattr(original, field))
            DefaultContext.traps, DefaultContext.flags = original.traps, original.flags

    def test_float_operation_trap_allows_decimal_only_construction(self):
        sequence = source()
        with localcontext() as context:
            context.traps[FloatOperation] = True
            self.assertEqual(len(build_training_dataset(sequence).examples), 1)
            self.assertFalse(context.flags[FloatOperation])

    def test_error_path_restores_ambient_context(self):
        sequence = changed(source(), 21, open=Decimal(0))
        with localcontext() as context:
            before = repr(context)
            with self.assertRaises(TrainingDataError):
                build_training_dataset(sequence)
            self.assertEqual(repr(context), before)

    def test_builder_has_no_clock_network_or_filesystem_side_effects(self):
        sequence = source(40)
        with (patch.object(time, "time", side_effect=AssertionError("wall clock")),
              patch.object(time, "monotonic", side_effect=AssertionError("clock")),
              patch.object(socket, "socket", side_effect=AssertionError("network")),
              patch.object(socket, "create_connection", side_effect=AssertionError("network")),
              patch("builtins.open", side_effect=AssertionError("filesystem")),
              patch.object(Path, "open", side_effect=AssertionError("filesystem"))):
            self.assertEqual(len(build_training_dataset(sequence).examples), 15)
