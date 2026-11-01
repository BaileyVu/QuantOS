"""Native LightGBM training, canonical metadata, and verified scalar inference."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from hashlib import sha256
import json
import logging
import math
import platform
from types import MappingProxyType

import lightgbm as lgb
import numpy as np
import scipy

from quantos.domain.alpha import PurgedTemporalSplit, TrainingExample, TARGET_VERSION, TARGET_HORIZON_MINUTES
from quantos.domain.features import FeatureVector, FEATURE_NAMES, FEATURE_VERSION
from quantos.domain.market_data import DatasetIdentity
from quantos.infrastructure.models.config import (
    LightGBMConfig, MODEL_ARTIFACT_SCHEMA_VERSION, MODEL_FAMILY_VERSION,
    ModelError, ModelArtifactError, finite_float,
)

_LOG = logging.getLogger("quantos")
_EXCLUSIONS = ("purged_training_boundary", "purged_validation_tail", "unavailable_train", "unavailable_validation")
_METADATA_KEYS = {
    "artifact_schema_version", "model_family_version", "artifact_id", "model_version",
    "model_sha256", "code_version", "feature_version", "feature_names", "target_version",
    "target_horizon_minutes", "source_identities", "train_start", "validation_start",
    "validation_end_exclusive", "train_counts", "validation_counts", "exclusions",
    "training_config", "resolved_parameters", "random_seed", "best_iteration",
    "validation_rmse", "software_versions",
}


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _parse_timestamp(value: str) -> datetime:
    if type(value) is not str:
        raise ModelArtifactError("timestamp metadata must be text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0) or _timestamp(parsed) != value:
        raise ModelArtifactError("metadata timestamps must be canonical UTC")
    return parsed


def _feature_row(feature: FeatureVector) -> list[float]:
    if type(feature) is not FeatureVector:
        raise ModelError("prediction requires an actual FeatureVector")
    if feature.feature_version != FEATURE_VERSION or tuple(feature.values) != FEATURE_NAMES:
        raise ModelError("incompatible feature version or ordered schema")
    if type(feature.values) is not MappingProxyType:
        raise ModelError("feature values must be immutable")
    try:
        FeatureVector.__post_init__(replace(feature))
    except (ValueError, TypeError, AttributeError) as error:
        raise ModelError(f"invalid feature contract: {error}") from error
    return [finite_float(feature.values[name]) for name in FEATURE_NAMES]


def build_model_matrix(rows: tuple[TrainingExample, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Separate ten ordered feature columns from the scalar regression labels."""
    if type(rows) is not tuple or not rows:
        raise ModelError("model matrix requires non-empty immutable rows")
    features, targets = [], []
    for row in rows:
        if type(row) is not TrainingExample:
            raise ModelError("model rows must be TrainingExample contracts")
        try:
            checked = replace(row)
        except (ValueError, TypeError, AttributeError) as error:
            raise ModelError(f"invalid training row: {error}") from error
        features.append(_feature_row(checked.feature))
        targets.append(finite_float(checked.label.value))
    x = np.array(features, dtype=np.float64)
    y = np.array(targets, dtype=np.float64)
    if x.shape != (len(rows), 10) or y.shape != (len(rows),) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ModelError("invalid float64 training matrix")
    return x, y


def _identity_metadata(identity: DatasetIdentity) -> dict[str, object]:
    fields = asdict(identity)
    fields["start_time"] = _timestamp(identity.start_time)
    fields["end_time"] = _timestamp(identity.end_time)
    fields["validation_status"] = identity.validation_status.value
    return fields


def _config_metadata(config: LightGBMConfig) -> dict[str, object]:
    fields = asdict(config)
    for name in ("learning_rate", "lambda_l2"):
        fields[name] = str(fields[name])
    return fields


def _identify(core: dict[str, object]) -> dict[str, object]:
    # Both identifiers are derived fields. Exclude both to avoid circularity.
    artifact_id = sha256(canonical_json(core)).hexdigest()
    return {**core, "artifact_id": artifact_id, "model_version": f"{MODEL_FAMILY_VERSION}:{artifact_id}"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelArtifactError(message)


def _verify_metadata(payload: bytes, model_bytes: bytes) -> dict[str, object]:
    try:
        _require(type(payload) is bytes and type(model_bytes) is bytes, "artifact payloads must be bytes")
        metadata = json.loads(payload)
        _require(type(metadata) is dict and set(metadata) == _METADATA_KEYS, "unexpected metadata fields")
        _require(canonical_json(metadata) == payload, "metadata is not canonical JSON")
        core = {key: value for key, value in metadata.items() if key not in ("artifact_id", "model_version")}
        _require(_identify(core) == metadata, "artifact identity mismatch")
        _require(metadata["model_sha256"] == sha256(model_bytes).hexdigest(), "model SHA-256 mismatch")
        for key, expected in (
            ("artifact_schema_version", MODEL_ARTIFACT_SCHEMA_VERSION),
            ("model_family_version", MODEL_FAMILY_VERSION), ("feature_version", FEATURE_VERSION),
            ("feature_names", list(FEATURE_NAMES)), ("target_version", TARGET_VERSION),
        ):
            _require(metadata[key] == expected, f"incompatible {key}")
        _require(type(metadata["target_horizon_minutes"]) is int and metadata["target_horizon_minutes"] == TARGET_HORIZON_MINUTES, "incompatible horizon")
        _require(type(metadata["code_version"]) is str and bool(metadata["code_version"].strip()), "missing code_version")
        config_data = dict(metadata["training_config"])
        for key in ("learning_rate", "lambda_l2"):
            _require(type(config_data[key]) is str, "config Decimal metadata must be text")
            config_data[key] = Decimal(config_data[key])
        config = LightGBMConfig(**config_data)
        _require(_config_metadata(config) == metadata["training_config"], "invalid training configuration metadata")
        _require(canonical_json(config.parameters()) == canonical_json(metadata["resolved_parameters"]), "resolved parameter mismatch")
        _require(type(metadata["random_seed"]) is int and metadata["random_seed"] == config.random_seed, "seed mismatch")
        best = metadata["best_iteration"]
        _require(type(best) is int and 1 <= best <= config.num_boost_round, "invalid best iteration")
        metric = metadata["validation_rmse"]
        _require(type(metric) is str, "RMSE must be canonical float text")
        metric_float = float(metric)
        _require(math.isfinite(metric_float) and metric_float >= 0 and repr(metric_float) == metric, "invalid validation RMSE")
        versions = metadata["software_versions"]
        _require(type(versions) is dict and set(versions) == {"python", "lightgbm", "numpy", "scipy"}, "missing software versions")
        _require(all(type(value) is str and bool(value.strip()) for value in versions.values()), "invalid software versions")
        start, validation, end = (_parse_timestamp(metadata[key]) for key in ("train_start", "validation_start", "validation_end_exclusive"))
        _require(start < validation < end, "invalid temporal boundaries")
        identities = metadata["source_identities"]
        _require(type(identities) is list and len(identities) == 2, "two source identities required")
        for identity, symbol in zip(identities, ("BTCUSDT", "ETHUSDT"), strict=True):
            fields = dict(identity)
            _require(fields.pop("validation_status") == "validated", "source must be validated")
            fields["start_time"] = _parse_timestamp(fields["start_time"])
            fields["end_time"] = _parse_timestamp(fields["end_time"])
            value = DatasetIdentity(**fields)
            _require(value.symbol == symbol, "source identities must be in canonical symbol order")
            _require(_identity_metadata(value._validated_copy()) == identity, "invalid source identity fields")
        for name in ("train_counts", "validation_counts"):
            counts = metadata[name]
            _require(type(counts) is dict and set(counts) == {"BTCUSDT", "ETHUSDT"}, "missing per-symbol row counts")
            _require(all(type(count) is int and count > 0 for count in counts.values()), "both symbols need actual model rows")
        exclusions = metadata["exclusions"]
        _require(type(exclusions) is dict and set(exclusions) == set(_EXCLUSIONS), "missing exclusion accounting")
        seen = set()
        for name in _EXCLUSIONS:
            group = exclusions[name]
            _require(type(group) is dict and set(group) == {"counts", "decisions"}, "invalid exclusion fields")
            _require(type(group["decisions"]) is list, "exclusions must be lists")
            decisions = []
            counts = {"BTCUSDT": 0, "ETHUSDT": 0}
            for row in group["decisions"]:
                _require(type(row) is dict and set(row) == {"timestamp", "symbol"}, "invalid excluded decision")
                timestamp, symbol = _parse_timestamp(row["timestamp"]), row["symbol"]
                _require(symbol in counts, "invalid excluded symbol")
                lower, upper = (start, validation) if name in ("purged_training_boundary", "unavailable_train") else (validation, end)
                _require(lower <= timestamp < upper, "excluded timestamp outside its window")
                key = (timestamp, symbol)
                _require(key not in seen, "duplicate excluded decision")
                seen.add(key)
                decisions.append(key)
                counts[symbol] += 1
            _require(decisions == sorted(decisions), "exclusions must be chronological")
            _require(canonical_json(counts) == canonical_json(group["counts"]), "exclusion count mismatch")
        return metadata
    except ModelArtifactError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, DecimalException) as error:
        raise ModelArtifactError(f"invalid artifact metadata: {error}") from error


def _verify_booster(model_bytes: bytes, metadata: dict[str, object]) -> lgb.Booster:
    try:
        text = model_bytes.decode("utf-8")
        _require(text.startswith("tree\nversion="), "malformed LightGBM text header")
        booster = lgb.Booster(model_str=text)
        _require(booster.feature_name() == list(FEATURE_NAMES), "Booster feature order mismatch")
        _require(booster.num_feature() == 10, "Booster feature count mismatch")
        _require(booster.current_iteration() == metadata["best_iteration"] and booster.num_trees() == metadata["best_iteration"], "Booster best-iteration tree state mismatch")
        _require(booster.dump_model()["objective"] == "regression", "Booster objective mismatch")
        return booster
    except ModelArtifactError:
        raise
    except (lgb.basic.LightGBMError, ValueError, TypeError, KeyError) as error:
        raise ModelArtifactError(f"cannot load native LightGBM model: {error}") from error


class VerifiedModel:
    """One selected, verified Booster; no registry or global model state."""

    def __init__(self, artifact: "ModelArtifact") -> None:
        metadata = _verify_metadata(artifact.metadata_bytes, artifact.model_bytes)
        self._booster = _verify_booster(artifact.model_bytes, metadata)
        self._best_iteration = metadata["best_iteration"]
        self.model_version = metadata["model_version"]

    def predict(self, feature: FeatureVector) -> Decimal:
        try:
            x = np.array([_feature_row(feature)], dtype=np.float64)
            prediction = self._booster.predict(x, num_iteration=self._best_iteration,
                                               raw_score=True, num_threads=1)
            if not isinstance(prediction, np.ndarray) or prediction.shape != (1,):
                raise ModelError("model must return exactly one scalar regression prediction")
            score = float(prediction[0])
            if not math.isfinite(score):
                raise ModelError("nonfinite model prediction")
            result = Decimal(repr(score))
            _LOG.debug("model_prediction", extra={"event": "model_prediction", "context": {
                "model_version": self.model_version, "symbol": feature.symbol,
                "decision_time": _timestamp(feature.timestamp), "gross_return": str(result),
            }})
            return result
        except ModelError:
            raise
        except (ValueError, TypeError, AttributeError, lgb.basic.LightGBMError) as error:
            raise ModelError(f"prediction failed: {error}") from error


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Immutable serialized training result; metadata access returns a copy."""

    model_bytes: bytes
    metadata_bytes: bytes

    def __post_init__(self) -> None:
        _verify_metadata(self.metadata_bytes, self.model_bytes)

    @property
    def metadata(self) -> dict[str, object]:
        return _verify_metadata(self.metadata_bytes, self.model_bytes)

    @property
    def artifact_id(self) -> str:
        return self.metadata["artifact_id"]

    @property
    def model_version(self) -> str:
        return self.metadata["model_version"]

    def load(self) -> VerifiedModel:
        return VerifiedModel(self)


def train_model(split: PurgedTemporalSplit, config: LightGBMConfig, *, code_version: str) -> ModelArtifact:
    """Train exactly one shared candidate using only the purged model rows."""
    if type(split) is not PurgedTemporalSplit or type(config) is not LightGBMConfig:
        raise ModelError("actual split and LightGBMConfig contracts are required")
    if type(code_version) is not str or not code_version.strip():
        raise ModelError("caller must provide code_version")
    try:
        split, config = replace(split), replace(config)
        x_train, y_train = build_model_matrix(split.train)
        x_validation, y_validation = build_model_matrix(split.validation)
        parameters = config.parameters()
        training = lgb.Dataset(x_train, label=y_train, feature_name=list(FEATURE_NAMES), params=parameters)
        validation = lgb.Dataset(x_validation, label=y_validation, reference=training,
                                 feature_name=list(FEATURE_NAMES), params=parameters)
        booster = lgb.train(parameters, training, num_boost_round=config.num_boost_round,
                            valid_sets=[validation], valid_names=["validation"], callbacks=[
                                lgb.early_stopping(config.early_stopping_rounds, first_metric_only=True, verbose=False),
                            ])
        best = booster.best_iteration
        if type(best) is not int or not 1 <= best <= config.num_boost_round:
            raise ModelError("invalid best iteration returned by training")
        metric = float(booster.best_score["validation"]["rmse"])
        if not math.isfinite(metric) or metric < 0:
            raise ModelError("invalid validation RMSE")
        model_bytes = booster.model_to_string(num_iteration=best).encode("utf-8")
        core = {
            "artifact_schema_version": MODEL_ARTIFACT_SCHEMA_VERSION,
            "model_family_version": MODEL_FAMILY_VERSION, "model_sha256": sha256(model_bytes).hexdigest(),
            "code_version": code_version, "feature_version": FEATURE_VERSION,
            "feature_names": list(FEATURE_NAMES), "target_version": TARGET_VERSION,
            "target_horizon_minutes": TARGET_HORIZON_MINUTES,
            "source_identities": [_identity_metadata(dataset.source_identity) for dataset in split.datasets],
            **{name: _timestamp(getattr(split, name)) for name in ("train_start", "validation_start", "validation_end_exclusive")},
            **{name + "_counts": {symbol: sum(row.feature.symbol == symbol for row in getattr(split, name))
                                    for symbol in ("BTCUSDT", "ETHUSDT")} for name in ("train", "validation")},
            "exclusions": {name: {
                "counts": {symbol: sum(row.symbol == symbol for row in getattr(split, name)) for symbol in ("BTCUSDT", "ETHUSDT")},
                "decisions": [{"timestamp": _timestamp(row.timestamp), "symbol": row.symbol} for row in getattr(split, name)],
            } for name in _EXCLUSIONS},
            "training_config": _config_metadata(config), "resolved_parameters": parameters,
            "random_seed": config.random_seed, "best_iteration": best, "validation_rmse": repr(metric),
            "software_versions": {"python": platform.python_version(), "lightgbm": lgb.__version__,
                                  "numpy": np.__version__, "scipy": scipy.__version__},
        }
        metadata = _identify(core)
        _verify_booster(model_bytes, metadata)
        artifact = ModelArtifact(model_bytes, canonical_json(metadata))
        _LOG.info("model_trained", extra={"event": "model_trained", "context": {
            "model_version": artifact.model_version, "best_iteration": best, "validation_rmse": repr(metric),
        }})
        return artifact
    except ModelError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, lgb.basic.LightGBMError) as error:
        raise ModelError(f"model training failed: {error}") from error
