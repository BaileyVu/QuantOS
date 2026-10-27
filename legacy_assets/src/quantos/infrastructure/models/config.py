"""Explicit caller configuration and fixed CPU model-family invariants."""

from dataclasses import dataclass
from decimal import Decimal
import math

MODEL_FAMILY_VERSION = "lightgbm-gross-return-regressor-v1"
MODEL_ARTIFACT_SCHEMA_VERSION = "lightgbm-artifact-v1"


class ModelError(ValueError):
    """Training or prediction cannot be performed safely."""


class ModelArtifactError(ModelError):
    """A selected model artifact is missing, incompatible, or corrupt."""


def finite_float(value: Decimal) -> float:
    if type(value) is not Decimal or not value.is_finite():
        raise ModelError("model values must be finite built-in Decimals")
    result = float(value)
    if not math.isfinite(result):
        raise ModelError("Decimal cannot be represented as finite float64")
    return result


@dataclass(frozen=True, slots=True)
class LightGBMConfig:
    learning_rate: Decimal
    num_leaves: int
    max_depth: int
    min_data_in_leaf: int
    lambda_l2: Decimal
    num_boost_round: int
    early_stopping_rounds: int
    random_seed: int

    def __post_init__(self) -> None:
        rate, penalty = finite_float(self.learning_rate), finite_float(self.lambda_l2)
        if not 0 < rate <= 1 or self.lambda_l2 < 0 or penalty < 0:
            raise ModelError("require 0 < learning_rate <= 1 and lambda_l2 >= 0")
        for name, low, high in (
            ("num_leaves", 2, 131072), ("max_depth", 1, 2147483647),
            ("min_data_in_leaf", 1, 2147483647), ("num_boost_round", 1, 2147483647),
            ("early_stopping_rounds", 1, 2147483647), ("random_seed", 0, 2147483647),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ModelError(f"invalid {name}")
        if self.early_stopping_rounds > self.num_boost_round:
            raise ModelError("early_stopping_rounds must not exceed num_boost_round")

    def parameters(self) -> dict[str, object]:
        LightGBMConfig.__post_init__(self)
        return {
            "objective": "regression", "metric": "rmse", "boosting_type": "gbdt",
            "device_type": "cpu", "deterministic": True, "force_col_wise": True,
            "force_row_wise": False, "num_threads": 1, "feature_fraction": 1.0,
            "bagging_fraction": 1.0, "bagging_freq": 0, "lambda_l1": 0,
            "max_bin": 255, "verbosity": -1,
            "seed": self.random_seed, "feature_fraction_seed": self.random_seed,
            "bagging_seed": self.random_seed, "data_random_seed": self.random_seed,
            "learning_rate": finite_float(self.learning_rate), "num_leaves": self.num_leaves,
            "max_depth": self.max_depth, "min_data_in_leaf": self.min_data_in_leaf,
            "lambda_l2": finite_float(self.lambda_l2), "num_iterations": self.num_boost_round,
        }
