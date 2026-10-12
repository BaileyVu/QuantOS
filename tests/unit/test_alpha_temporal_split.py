"""Chronological split contracts, exact purge boundaries, and future isolation."""

from copy import copy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from quantos.domain.alpha import TemporalSplitError, ExcludedDecision
from tests.model_fixtures import datasets, split, change_example, unavailable, START, MINUTE, US


class TemporalSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = datasets()

    def test_counts_and_both_symbols(self):
        result = split(self.inputs)
        self.assertEqual(len(result.train), 150)
        self.assertEqual(len(result.validation), 90)
        self.assertEqual(len(result.purged_training_boundary), 10)
        self.assertEqual(len(result.purged_validation_tail), 10)
        for rows in (result.train, result.validation):
            self.assertEqual({row.feature.symbol for row in rows}, {"BTCUSDT", "ETHUSDT"})

    def test_exit_equal_to_validation_start_is_purged(self):
        result = split(self.inputs)
        row = self.inputs[0].examples[95-20]
        self.assertEqual(row.label.exit_reference_time, result.validation_start)
        self.assertIn(ExcludedDecision(row.feature.timestamp, "BTCUSDT"), result.purged_training_boundary)

    def test_exit_after_validation_start_is_purged(self):
        result = split(self.inputs)
        for index in range(96, 100):
            row = self.inputs[0].examples[index-20]
            self.assertGreater(row.label.exit_reference_time, result.validation_start)
            self.assertIn(ExcludedDecision(row.feature.timestamp, "BTCUSDT"), result.purged_training_boundary)

    def test_exit_before_boundary_may_train(self):
        result = split(self.inputs)
        row = self.inputs[0].examples[94-20]
        self.assertLess(row.label.exit_reference_time, result.validation_start)
        self.assertIn(row, result.train)

    def test_first_validation_is_never_training(self):
        result = split(self.inputs)
        self.assertEqual(result.validation[0].feature.timestamp, result.validation_start)
        self.assertNotIn(result.validation[0], result.train)

    def test_validation_tail_equal_and_beyond_are_excluded(self):
        result = split(self.inputs)
        for index in range(145, 150):
            row = self.inputs[0].examples[index-20]
            self.assertGreaterEqual(row.label.exit_reference_time, result.validation_end_exclusive)
            self.assertNotIn(row, result.validation)
            self.assertIn(ExcludedDecision(row.feature.timestamp, "BTCUSDT"), result.purged_validation_tail)

    def test_last_validation_exit_before_end_is_kept(self):
        result = split(self.inputs)
        self.assertIn(self.inputs[0].examples[144-20], result.validation)

    def test_future_rows_and_exact_end_are_excluded(self):
        result = split(self.inputs)
        for row in result.train + result.validation:
            self.assertLess(row.feature.timestamp, result.validation_end_exclusive)
        changed = (change_example(self.inputs[0], 150, value=Decimal("1e999")), self.inputs[1])
        other = split(changed)
        self.assertEqual(result.train, other.train)
        self.assertEqual(result.validation, other.validation)

    def test_purged_target_changes_do_not_change_training_rows(self):
        result = split(self.inputs)
        other = split((change_example(self.inputs[0], 95, value=Decimal("123")), self.inputs[1]))
        self.assertEqual(result.train, other.train)
        self.assertEqual(result.validation, other.validation)

    def test_global_timestamp_symbol_order(self):
        result = split(self.inputs)
        for rows in (result.train, result.validation):
            keys = [(row.feature.timestamp, row.feature.symbol) for row in rows]
            self.assertEqual(keys, sorted(keys))
            self.assertEqual([symbol for _, symbol in keys[:2]], ["BTCUSDT", "ETHUSDT"])

    def test_input_reversal_produces_identical_split(self):
        self.assertEqual(split(self.inputs), split(tuple(reversed(self.inputs))))

    def test_missing_features_recorded_in_correct_windows(self):
        result = split((unavailable(self.inputs[0], 20, 100, 160), self.inputs[1]))
        self.assertEqual(result.unavailable_train, (ExcludedDecision(START+21*MINUTE-US, "BTCUSDT"),))
        self.assertEqual(result.unavailable_validation, (ExcludedDecision(START+101*MINUTE-US, "BTCUSDT"),))
        self.assertEqual(len(result.train), 149)
        self.assertEqual(len(result.validation), 89)

    def test_training_start_is_inclusive_and_earlier_decisions_excluded(self):
        result = split(self.inputs, train_start=START+31*MINUTE-US)
        self.assertEqual(result.train[0].feature.timestamp, START+31*MINUTE-US)
        self.assertEqual(len(result.train), 130)

    def test_historical_lookback_is_permitted_for_first_validation_feature(self):
        result = split(self.inputs)
        self.assertEqual(result.validation[0], self.inputs[0].examples[100-20])

    def test_real_acceptance_noon_boundary_has_exact_five_rows_per_symbol(self):
        result = split(datasets(1440), validation_start=START+721*MINUTE-US,
                       validation_end_exclusive=START+1001*MINUTE-US)
        for symbol in ("BTCUSDT", "ETHUSDT"):
            self.assertEqual([row.timestamp for row in result.purged_training_boundary if row.symbol == symbol],
                             [START+(index+1)*MINUTE-US for index in range(715, 720)])

    def test_missing_duplicate_or_wrong_container_datasets_fail(self):
        for inputs in ((), self.inputs[:1], self.inputs[1:], (self.inputs[0], self.inputs[0]), list(self.inputs), (None, self.inputs[1])):
            with self.subTest(inputs=type(inputs).__name__), self.assertRaises(TemporalSplitError):
                split(inputs)

    def test_incompatible_dataset_metadata_and_forged_content_fail(self):
        for name, value in (("feature_version", "bad"), ("target_version", "bad"),
                            ("horizon_minutes", 6), ("examples", ()), ("examples", [])):
            altered = copy(self.inputs[0])
            object.__setattr__(altered, name, value)
            with self.subTest(name=name), self.assertRaises(TemporalSplitError):
                split((altered, self.inputs[1]))

    def test_invalid_boundary_types_timezones_and_order_fail(self):
        class ExtendedDatetime(datetime):
            pass
        for fields in (
            {"train_start": START.replace(tzinfo=None)},
            {"validation_start": START.astimezone(timezone(timedelta(hours=1)))},
            {"train_start": ExtendedDatetime(2025, 1, 1, tzinfo=timezone.utc)},
            {"validation_start": START}, {"validation_end_exclusive": START},
            {"validation_start": "2025-01-01"},
        ):
            with self.subTest(fields=fields), self.assertRaises(TemporalSplitError):
                split(self.inputs, **fields)

    def test_zero_training_or_validation_rows_fail(self):
        for fields in ({"validation_start": START+22*MINUTE},
                       {"validation_end_exclusive": START+102*MINUTE},
                       {"validation_start": START+300*MINUTE, "validation_end_exclusive": START+400*MINUTE}):
            with self.subTest(fields=fields), self.assertRaises(TemporalSplitError):
                split(self.inputs, **fields)

    def test_one_symbol_missing_actual_rows_fails(self):
        for indices in (range(20, 95), range(100, 145)):
            with self.assertRaises(TemporalSplitError):
                split((unavailable(self.inputs[0], *indices), self.inputs[1]))

    def test_output_is_immutable(self):
        result = split(self.inputs)
        with self.assertRaises(FrozenInstanceError):
            result.train = ()
        with self.assertRaises(ValueError):
            replace(result, train=())

    def test_excluded_contract_rejects_invalid_symbol_and_time(self):
        for timestamp, symbol in ((START, "SOLUSDT"), (START.replace(tzinfo=None), "BTCUSDT")):
            with self.assertRaises(TemporalSplitError):
                ExcludedDecision(timestamp, symbol)
