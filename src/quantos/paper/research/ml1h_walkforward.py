"""Frozen-fold LightGBM scoring helper for BUILD-only ml1h experiments.

This module accepts caller-provided feature matrices and labels. It does not
load data, build labels, choose positions, calculate P&L/costs, or inspect HELD.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from datetime import date
import math
import numbers
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence

ARMS = ("baseline", "augmented")
DIRECTIONS = ("LONG", "SHORT")
DEFAULT_WARMUP_QUARTERS = 4
CALIBRATION_QUANTILE = 0.98
LIGHTGBM_PARAMS = MappingProxyType({
    "n_estimators": 200,
    "num_leaves": 31,
    "learning_rate": 0.05,
    "min_child_samples": 500,
    "subsample": 0.7,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "n_jobs": 2,
    "random_state": 17,
    "verbosity": -1,
})


class Classifier(Protocol):
    def fit(self, features, labels): ...
    def predict_proba(self, features): ...


@dataclass(frozen=True)
class Fold:
    quarter: str
    calibration_quarter: str
    fit_indices: tuple[int, ...]
    calibration_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    test_start_ms: int
    test_end_ms: int


@dataclass(frozen=True)
class FoldScores:
    fold: Fold
    # Keys use "arm:DIRECTION"; score tuples align with fold.test_indices.
    scores: Mapping[str, tuple[float, ...]]
    thresholds: Mapping[str, float]


def lightgbm_classifier():
    """Construct the preregistered classifier; import the optional dep lazily."""
    try:
        from lightgbm import LGBMClassifier
    except ImportError as exc:
        raise RuntimeError("LightGBM is required only when fitting ml1h folds") from exc
    return LGBMClassifier(**LIGHTGBM_PARAMS)


def _quarter_number(timestamp_ms: int) -> int:
    day = date.fromordinal(date(1970, 1, 1).toordinal() + timestamp_ms // 86_400_000)
    return day.year * 4 + (day.month - 1) // 3


def _quarter_start_ms(qnumber: int) -> int:
    year, quarter0 = divmod(qnumber, 4)
    month = quarter0 * 3 + 1
    start = date(year, month, 1)
    return (start.toordinal() - date(1970, 1, 1).toordinal()) * 86_400_000


def _quarter_label(qnumber: int) -> str:
    year, quarter0 = divmod(qnumber, 4)
    return f"{year}Q{quarter0 + 1}"


def quarterly_folds(timestamp_ms: Sequence[int], label_end_ms: Sequence[int | None], *,
                    dataset_start_ms: int,
                    build_end_exclusive_ms: int,
                    warmup_quarters: int = DEFAULT_WARMUP_QUARTERS) -> tuple[Fold, ...]:
    """Create expanding quarterly folds with a reserved prior-quarter calibration set.

    The first test quarter follows `warmup_quarters` elapsed calendar quarters.
    The final prior quarter is reserved for score calibration, and earlier
    labels must end strictly before calibration starts. Calibration labels must
    end strictly before their test quarter. This label-end rule is the purge;
    rows at/after BUILD end are excluded before fold construction.
    """
    n = len(timestamp_ms)
    if len(label_end_ms) != n:
        raise ValueError("timestamp and label_end arrays must have equal lengths")
    if (dataset_start_ms < 0 or build_end_exclusive_ms <= dataset_start_ms or
            warmup_quarters < 1):
        raise ValueError("invalid BUILD fold parameters")
    for i, (timestamp, label_end) in enumerate(zip(timestamp_ms, label_end_ms)):
        if isinstance(timestamp, bool) or not isinstance(timestamp, numbers.Integral) or timestamp < 0:
            raise ValueError(f"invalid timestamp at row {i}")
        if timestamp < build_end_exclusive_ms and label_end is not None:
            if (isinstance(label_end, bool) or not isinstance(label_end, numbers.Integral) or
                    label_end < timestamp):
                raise ValueError(f"invalid label_end_ms at row {i}")
    active = [i for i, timestamp in enumerate(timestamp_ms) if timestamp < build_end_exclusive_ms]
    if not active:
        return ()
    if min(timestamp_ms[i] for i in active) < dataset_start_ms:
        raise ValueError("rows precede dataset_start_ms")
    first_q = _quarter_number(dataset_start_ms)
    last_q = max(_quarter_number(timestamp_ms[i]) for i in active)
    first_test_q = first_q + warmup_quarters
    folds = []
    for test_q in range(first_test_q, last_q + 1):
        test_start = _quarter_start_ms(test_q)
        test_end = min(_quarter_start_ms(test_q + 1), build_end_exclusive_ms)
        calibration_start = _quarter_start_ms(test_q - 1)
        fit = tuple(i for i in active
                    if timestamp_ms[i] < calibration_start
                    and label_end_ms[i] is not None
                    and label_end_ms[i] < calibration_start)
        calibration = tuple(i for i in active
                            if calibration_start <= timestamp_ms[i] < test_start
                            and label_end_ms[i] is not None
                            and label_end_ms[i] < test_start)
        test = tuple(i for i in active if test_start <= timestamp_ms[i] < test_end)
        if fit and calibration and test:
            folds.append(Fold(_quarter_label(test_q), _quarter_label(test_q - 1),
                              fit, calibration, test, test_start, test_end))
    return tuple(folds)


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("calibration returned no scores")
    pos = (len(ordered) - 1) * q
    low, high = math.floor(pos), math.ceil(pos)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _rows(matrix: Sequence[Sequence[float]], indices: Sequence[int]):
    return [matrix[i] for i in indices]


def _positive_scores(model: Classifier, matrix: Sequence[Sequence[float]],
                     indices: Sequence[int]) -> tuple[float, ...]:
    predictions = model.predict_proba(_rows(matrix, indices))
    scores = []
    for row in predictions:
        try:
            score = float(row[1])
        except (IndexError, TypeError, ValueError, OverflowError):
            raise ValueError("classifier must return two-column positive-class probabilities") from None
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("classifier returned an invalid probability")
        scores.append(score)
    if len(scores) != len(indices):
        raise ValueError("classifier returned the wrong number of scores")
    return tuple(scores)


def walk_forward_scores(feature_matrices: Mapping[str, Sequence[Sequence[float]]],
                        timestamp_ms: Sequence[int],
                        targets: Mapping[str, Sequence[int | None]],
                        label_end_ms: Sequence[int | None], *,
                        dataset_start_ms: int,
                        build_end_exclusive_ms: int,
                        model_factory: Callable[[], Classifier] | None = None,
                        warmup_quarters: int = DEFAULT_WARMUP_QUARTERS,
                        quarters: set[str] | None = None,
                        fold_callback: Callable[[FoldScores], None] | None = None) -> tuple[FoldScores, ...]:
    """Fit baseline/augmented LONG/SHORT classifiers and score frozen BUILD folds.

    Thresholds are the linear-interpolated 98th percentile of score probabilities
    on the preceding calibration quarter. Calibration labels do not fit the
    model and do not set the score threshold. Test labels are not read. Returned
    scores/thresholds are ingredients for the caller's one-position-per-symbol
    and unchanged-cost L2 accounting; this helper does not choose trades.
    """
    if set(feature_matrices) != set(ARMS):
        raise ValueError(f"feature_matrices must contain exactly {ARMS}")
    if set(targets) != set(DIRECTIONS):
        raise ValueError(f"targets must contain exactly {DIRECTIONS}")
    n = len(timestamp_ms)
    if any(len(matrix) != n for matrix in feature_matrices.values()):
        raise ValueError("feature matrices must align with timestamps")
    if any(len(targets[direction]) != n for direction in DIRECTIONS) or len(label_end_ms) != n:
        raise ValueError("targets and label_end arrays must align with timestamps")
    valid_feature_rows = set()
    for arm in ARMS:
        width = None
        for i, row in enumerate(feature_matrices[arm]):
            if not len(row) or (width is not None and len(row) != width):
                raise ValueError(f"{arm} feature rows must have one stable nonzero width")
            width = len(row)
    for i in range(n):
        finite = True
        for arm in ARMS:
            try:
                finite = finite and all(math.isfinite(float(value)) for value in feature_matrices[arm][i])
            except (TypeError, ValueError, OverflowError):
                finite = False
        if finite:
            valid_feature_rows.add(i)
    for i in range(n):
        for direction in DIRECTIONS:
            label = targets[direction][i]
            if label is None:
                continue
            try:
                numeric_label = float(label)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"target {direction} at row {i} must be binary, NaN, or None") from None
            if math.isnan(numeric_label):
                continue
            if not math.isfinite(numeric_label) or numeric_label not in (0.0, 1.0):
                raise ValueError(f"target {direction} at row {i} must be binary, NaN, or None")

    folds = quarterly_folds(timestamp_ms, label_end_ms,
                            dataset_start_ms=dataset_start_ms,
                            build_end_exclusive_ms=build_end_exclusive_ms,
                            warmup_quarters=warmup_quarters)
    if quarters is not None:
        available = {fold.quarter for fold in folds}
        if not quarters <= available:
            raise ValueError(f"requested quarters are unavailable: {sorted(quarters - available)}")
        folds = tuple(fold for fold in folds if fold.quarter in quarters)
    make_model = model_factory or lightgbm_classifier
    outputs = []
    for fold in folds:
        test_indices = tuple(i for i in fold.test_indices if i in valid_feature_rows)
        if not test_indices:
            raise ValueError(f"fold {fold.quarter} has no common finite test features")
        fold = replace(fold, test_indices=test_indices)
        fold_scores: dict[str, tuple[float, ...]] = {}
        thresholds: dict[str, float] = {}
        for arm in ARMS:
            matrix = feature_matrices[arm]
            for direction in DIRECTIONS:
                key = f"{arm}:{direction}"
                target = targets[direction]
                valid_target = lambda i: target[i] is not None and not math.isnan(float(target[i]))
                fit_indices = tuple(i for i in fold.fit_indices
                                    if i in valid_feature_rows and valid_target(i))
                calibration_indices = tuple(i for i in fold.calibration_indices
                                             if i in valid_feature_rows and valid_target(i))
                if not fit_indices or not calibration_indices:
                    raise ValueError(f"{key} fold {fold.quarter} lacks finite fit or calibration rows")
                fit_classes = {int(target[i]) for i in fit_indices}
                calibration_classes = {int(target[i]) for i in calibration_indices}
                if fit_classes != {0, 1} or calibration_classes != {0, 1}:
                    raise ValueError(f"{key} fold {fold.quarter} needs both classes in fit and calibration")
                model = make_model()
                model.fit(_rows(matrix, fit_indices), [int(target[i]) for i in fit_indices])
                calibration_scores = _positive_scores(model, matrix, calibration_indices)
                thresholds[key] = _quantile(calibration_scores, CALIBRATION_QUANTILE)
                fold_scores[key] = _positive_scores(model, matrix, fold.test_indices)
        result = FoldScores(fold, fold_scores, thresholds)
        outputs.append(result)
        if fold_callback is not None:
            fold_callback(result)
    return tuple(outputs)
