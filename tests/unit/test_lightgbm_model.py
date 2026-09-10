"""Native CPU model training, input safety, and exact replay contracts."""

from copy import copy
from dataclasses import replace
from decimal import Decimal, FloatOperation, localcontext
from types import MappingProxyType
import unittest
from unittest.mock import patch

import numpy as np

from quantos.domain.features import FEATURE_NAMES
from quantos.infrastructure.models import ModelError, LightGBMConfig, build_model_matrix, train_model
from tests.model_fixtures import datasets, split, config, change_example, unavailable


class LightGBMModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = datasets()
        cls.split = split(cls.inputs)
        cls.config = config()
        cls.artifact = train_model(cls.split, cls.config, code_version="synthetic-v1")

    def test_candidate_config_contract(self):
        candidate = config(num_leaves=15, max_depth=5, min_data_in_leaf=50,
                           num_boost_round=200, early_stopping_rounds=20)
        self.assertEqual(candidate.learning_rate, Decimal("0.05"))
        self.assertEqual(candidate.lambda_l2, Decimal("1"))
        self.assertEqual(candidate.random_seed, 20260911)
        trained = train_model(self.split, candidate, code_version="candidate-config-test")
        self.assertGreaterEqual(trained.metadata["best_iteration"], 1)
        self.assertLessEqual(trained.metadata["best_iteration"], 200)

    def test_invalid_configs_reject_exact_types_and_ranges(self):
        for name, values in {
            "learning_rate": [0, 0.05, Decimal(0), Decimal(-1), Decimal(2), Decimal("NaN"), Decimal("Infinity"), Decimal("1e-999")],
            "lambda_l2": [1, Decimal(-1), Decimal("NaN"), Decimal("1e999")],
            "num_leaves": [True, 1, 131073, 3.0], "max_depth": [0, -1, True],
            "min_data_in_leaf": [0, -1, True], "num_boost_round": [0, -1, True],
            "early_stopping_rounds": [0, -1, 13, True], "random_seed": [-1, True, 2147483648],
        }.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ModelError):
                    config(**{name: value})

    def test_fixed_parameters_and_all_seed_paths(self):
        params = self.config.parameters()
        expected = {"objective": "regression", "metric": "rmse", "boosting_type": "gbdt",
                    "device_type": "cpu", "deterministic": True, "force_col_wise": True,
                    "force_row_wise": False, "num_threads": 1, "feature_fraction": 1.0,
                    "bagging_fraction": 1.0, "bagging_freq": 0, "lambda_l1": 0, "max_bin": 255, "verbosity": -1}
        for key, value in expected.items():
            self.assertEqual(params[key], value)
        for key in ("seed", "feature_fraction_seed", "bagging_seed", "data_random_seed"):
            self.assertEqual(params[key], self.config.random_seed)
        self.assertEqual(params, self.artifact.metadata["resolved_parameters"])

    def test_float64_matrix_has_ten_explicit_columns_and_separate_target(self):
        x, y = build_model_matrix(self.split.train)
        self.assertEqual(x.dtype, np.float64)
        self.assertEqual(y.dtype, np.float64)
        self.assertEqual(x.shape, (150, 10))
        self.assertEqual(y.shape, (150,))
        for index, row in enumerate(self.split.train):
            self.assertEqual(list(x[index]), [float(row.feature.values[name]) for name in FEATURE_NAMES])
            self.assertEqual(y[index], float(row.label.value))

    def test_changing_only_label_never_changes_features(self):
        row = self.split.train[0]
        other = replace(row, label=replace(row.label, value=Decimal("0.9")))
        x, y = build_model_matrix((row,))
        changed_x, changed_y = build_model_matrix((other,))
        np.testing.assert_array_equal(x, changed_x)
        self.assertNotEqual(y[0], changed_y[0])

    def test_nonfinite_float_conversion_fails_before_training(self):
        row = self.split.train[0]
        for example in (replace(row, label=replace(row.label, value=Decimal("1e999"))),
                        replace(row, feature=replace(row.feature, values={**row.feature.values, FEATURE_NAMES[0]: Decimal("1e999")}))):
            with self.assertRaises(ModelError):
                build_model_matrix((example,))

    def test_matrix_requires_actual_nonempty_immutable_rows(self):
        for rows in ((), [], (None,)):
            with self.assertRaises(ModelError):
                build_model_matrix(rows)

    def test_training_requires_actual_contracts_and_code_version(self):
        for version in ("", " ", None, 1):
            with self.assertRaises(ModelError):
                train_model(self.split, self.config, code_version=version)
        with self.assertRaises(ModelError):
            train_model(None, self.config, code_version="test")
        bad = copy(self.config)
        object.__setattr__(bad, "random_seed", True)
        with self.assertRaises(ModelError):
            train_model(self.split, bad, code_version="test")

    def test_repeated_training_exact_bytes_metadata_and_predictions(self):
        repeated = train_model(self.split, self.config, code_version="synthetic-v1")
        self.assertEqual(self.artifact, repeated)
        self.assertEqual(self.artifact.model_bytes, repeated.model_bytes)
        self.assertEqual(self.artifact.metadata_bytes, repeated.metadata_bytes)
        self.assertEqual(self.artifact.artifact_id, repeated.artifact_id)
        self.assertEqual(self.artifact.model_version, repeated.model_version)
        before, after = self.artifact.load(), repeated.load()
        for row in self.split.validation[:4]:
            self.assertEqual(before.predict(row.feature).as_tuple(), after.predict(row.feature).as_tuple())

    def test_reversed_dataset_input_order_same_artifact(self):
        reversed_split = split(tuple(reversed(self.inputs)))
        repeated = train_model(reversed_split, self.config, code_version="synthetic-v1")
        self.assertEqual(self.artifact, repeated)

    def test_purged_training_label_does_not_change_matrix_or_artifact(self):
        altered = split((change_example(self.inputs[0], 95, value=Decimal("3")), self.inputs[1]))
        np.testing.assert_array_equal(build_model_matrix(self.split.train)[1], build_model_matrix(altered.train)[1])
        self.assertEqual(self.artifact, train_model(altered, self.config, code_version="synthetic-v1"))

    def test_validation_tail_label_is_not_evaluated(self):
        altered = split((change_example(self.inputs[0], 145, value=Decimal("1e999")), self.inputs[1]))
        self.assertEqual(self.artifact, train_model(altered, self.config, code_version="synthetic-v1"))

    def test_future_sentinel_features_and_labels_never_enter_model(self):
        row = self.inputs[0].examples[160-20]
        future_feature = replace(row.feature, values={name: Decimal("1e999") for name in FEATURE_NAMES})
        altered = split((change_example(self.inputs[0], 160, value=Decimal("1e999"), feature=future_feature), self.inputs[1]))
        for name in ("train", "validation"):
            a, b = build_model_matrix(getattr(self.split, name)), build_model_matrix(getattr(altered, name))
            np.testing.assert_array_equal(a[0], b[0])
            np.testing.assert_array_equal(a[1], b[1])
        self.assertEqual(self.artifact, train_model(altered, self.config, code_version="synthetic-v1"))

    def test_best_iteration_and_metric_are_finite_and_recorded(self):
        metadata = self.artifact.metadata
        self.assertGreaterEqual(metadata["best_iteration"], 1)
        self.assertLessEqual(metadata["best_iteration"], self.config.num_boost_round)
        self.assertTrue(Decimal(metadata["validation_rmse"]).is_finite())
        booster = self.artifact.load()._booster
        self.assertEqual(booster.num_trees(), metadata["best_iteration"])
        self.assertEqual(booster.feature_name(), list(FEATURE_NAMES))

    def test_native_training_receives_only_training_and_validation_rows(self):
        import quantos.infrastructure.models.model as module
        actual = module.lgb.train
        def inspect(params, training, **kwargs):
            self.assertEqual(training.data.shape, (150, 10))
            self.assertEqual(len(kwargs["valid_sets"]), 1)
            self.assertEqual(kwargs["valid_sets"][0].data.shape, (90, 10))
            self.assertEqual(training.feature_name, list(FEATURE_NAMES))
            return actual(params, training, **kwargs)
        with patch.object(module.lgb, "train", side_effect=inspect):
            train_model(self.split, self.config, code_version="inspection")

    def test_prediction_is_round_trip_decimal_and_repeats_exactly(self):
        model = self.artifact.load()
        for row in self.split.validation[:4]:
            prediction = model.predict(row.feature)
            self.assertIs(type(prediction), Decimal)
            self.assertTrue(prediction.is_finite())
            self.assertEqual(prediction.as_tuple(), model.predict(row.feature).as_tuple())
        with patch.object(model._booster, "predict", return_value=np.array([0.1], dtype=np.float64)):
            self.assertEqual(model.predict(self.split.validation[0].feature).as_tuple(), Decimal("0.1").as_tuple())

    def test_invalid_prediction_shape_and_nonfinite_fail(self):
        model = self.artifact.load()
        for output in (np.array([np.nan]), np.array([np.inf]), np.array([-np.inf]), np.array([]), np.array([[1.0]]), np.array([1.0, 2.0])):
            with self.subTest(output=output), patch.object(model._booster, "predict", return_value=output), self.assertRaises(ModelError):
                model.predict(self.split.validation[0].feature)

    def test_inference_rejects_forged_feature_contracts(self):
        model = self.artifact.load()
        original = self.split.validation[0].feature
        for field, value in (("symbol", "SOLUSDT"), ("feature_version", "other"),
                             ("values", MappingProxyType(dict(list(original.values.items())[1:]))),
                             ("values", MappingProxyType({**original.values, "extra": Decimal(1)})),
                             ("values", MappingProxyType(dict(reversed(list(original.values.items())))))):
            altered = copy(original)
            object.__setattr__(altered, field, value)
            with self.subTest(field=field), self.assertRaises(ModelError):
                model.predict(altered)
        for value in (1.0, 1, Decimal("NaN"), Decimal("Infinity"), Decimal("1e999")):
            altered = copy(original)
            object.__setattr__(altered, "values", MappingProxyType({**original.values, FEATURE_NAMES[0]: value}))
            with self.subTest(value=value), self.assertRaises(ModelError):
                model.predict(altered)
        with self.assertRaises(ModelError):
            model.predict(None)

    def test_inference_independent_of_decimal_context(self):
        model = self.artifact.load()
        feature = self.split.validation[0].feature
        expected = model.predict(feature)
        with localcontext() as context:
            context.prec = 2
            context.traps[FloatOperation] = True
            self.assertEqual(model.predict(feature).as_tuple(), expected.as_tuple())

    def test_metadata_carries_provenance_and_no_machine_or_clock_state(self):
        metadata = self.artifact.metadata
        self.assertEqual([item["symbol"] for item in metadata["source_identities"]], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(metadata["train_counts"], {"BTCUSDT": 75, "ETHUSDT": 75})
        self.assertEqual(metadata["validation_counts"], {"BTCUSDT": 45, "ETHUSDT": 45})
        self.assertEqual(metadata["exclusions"]["purged_training_boundary"]["counts"], {"BTCUSDT": 5, "ETHUSDT": 5})
        self.assertEqual(set(metadata["software_versions"]), {"python", "lightgbm", "numpy", "scipy"})
        for forbidden in (b"created_at", b"hostname", b"username", b"QuantOS", b"final_test"):
            self.assertNotIn(forbidden, self.artifact.metadata_bytes)

    def test_metadata_access_does_not_mutate_result(self):
        metadata = self.artifact.metadata
        metadata["feature_names"].reverse()
        self.assertEqual(self.artifact.metadata["feature_names"], list(FEATURE_NAMES))

    def test_missing_feature_exclusions_are_preserved_in_metadata(self):
        inputs = (unavailable(self.inputs[0], 20, 100, 160), self.inputs[1])
        result = train_model(split(inputs), self.config, code_version="missing-fixture")
        metadata = result.metadata
        self.assertEqual(metadata["train_counts"], {"BTCUSDT": 74, "ETHUSDT": 75})
        self.assertEqual(metadata["validation_counts"], {"BTCUSDT": 44, "ETHUSDT": 45})
        for name in ("unavailable_train", "unavailable_validation"):
            self.assertEqual(metadata["exclusions"][name]["counts"], {"BTCUSDT": 1, "ETHUSDT": 0})
            self.assertEqual(len(metadata["exclusions"][name]["decisions"]), 1)

    def test_serialization_preserves_trained_booster_best_state_predictions(self):
        import quantos.infrastructure.models.model as module
        original_train = module.lgb.train
        captured = []
        def capture(*args, **kwargs):
            booster = original_train(*args, **kwargs)
            x, _ = build_model_matrix(self.split.validation[:4])
            captured.extend(booster.predict(x, num_iteration=booster.best_iteration,
                                            raw_score=True, num_threads=1))
            return booster
        with patch.object(module.lgb, "train", side_effect=capture):
            result = train_model(self.split, self.config, code_version="native-roundtrip")
        loaded = result.load()
        for value, row in zip(captured, self.split.validation[:4], strict=True):
            self.assertEqual(Decimal(repr(float(value))).as_tuple(), loaded.predict(row.feature).as_tuple())

    def test_code_and_configuration_changes_have_distinct_identity(self):
        other = train_model(self.split, self.config, code_version="synthetic-v2")
        self.assertEqual(self.artifact.model_bytes, other.model_bytes)
        self.assertNotEqual(self.artifact.artifact_id, other.artifact_id)
        changed_config = train_model(self.split, config(random_seed=22), code_version="synthetic-v1")
        self.assertNotEqual(self.artifact.artifact_id, changed_config.artifact_id)
