"""Label-horizon purging for one shared, chronological two-symbol model."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from quantos.domain.alpha.training_data import TrainingDataset, TrainingExample


class TemporalSplitError(ValueError):
    """Training inputs cannot satisfy the requested temporal isolation."""


def _utc(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise TemporalSplitError("split boundaries must be built-in UTC datetimes")


@dataclass(frozen=True, slots=True)
class ExcludedDecision:
    """A symbol-qualified timestamp, never an imputed model row."""

    timestamp: datetime
    symbol: str

    def __post_init__(self) -> None:
        _utc(self.timestamp)
        if type(self.symbol) is not str or self.symbol not in ("BTCUSDT", "ETHUSDT"):
            raise TemporalSplitError("unsupported excluded-decision symbol")


@dataclass(frozen=True, slots=True)
class PurgedTemporalSplit:
    """Validated source snapshots and derived, globally ordered model rows.

    Source snapshots support revalidation at the training boundary. Only train
    and validation rows enter matrices; future row values never enter metadata.
    Derived fields cannot be supplied independently of the canonical inputs.
    """

    datasets: tuple[TrainingDataset, ...] = field(repr=False)
    train_start: datetime
    validation_start: datetime
    validation_end_exclusive: datetime
    train: tuple[TrainingExample, ...] = field(init=False, repr=False)
    validation: tuple[TrainingExample, ...] = field(init=False, repr=False)
    purged_training_boundary: tuple[ExcludedDecision, ...] = field(init=False)
    purged_validation_tail: tuple[ExcludedDecision, ...] = field(init=False)
    unavailable_train: tuple[ExcludedDecision, ...] = field(init=False)
    unavailable_validation: tuple[ExcludedDecision, ...] = field(init=False)

    def __post_init__(self) -> None:
        try:
            for boundary in (self.train_start, self.validation_start, self.validation_end_exclusive):
                _utc(boundary)
            if not self.train_start < self.validation_start < self.validation_end_exclusive:
                raise TemporalSplitError("require train_start < validation_start < validation_end_exclusive")
            if type(self.datasets) is not tuple or len(self.datasets) != 2:
                raise TemporalSplitError("exactly one BTCUSDT and one ETHUSDT dataset are required")
            if any(type(dataset) is not TrainingDataset for dataset in self.datasets):
                raise TemporalSplitError("inputs must be actual TrainingDataset contracts")
            datasets = tuple(sorted((replace(dataset) for dataset in self.datasets),
                                    key=lambda dataset: dataset.source_identity.symbol))
            if tuple(dataset.source_identity.symbol for dataset in datasets) != ("BTCUSDT", "ETHUSDT"):
                raise TemporalSplitError("exactly one BTCUSDT and one ETHUSDT dataset are required")
            object.__setattr__(self, "datasets", datasets)
            train: list[TrainingExample] = []
            validation: list[TrainingExample] = []
            boundary_purge: list[ExcludedDecision] = []
            tail_purge: list[ExcludedDecision] = []
            unavailable_train: list[ExcludedDecision] = []
            unavailable_validation: list[ExcludedDecision] = []
            for dataset in datasets:
                symbol = dataset.source_identity.symbol
                for example in dataset.examples:
                    timestamp = example.feature.timestamp
                    if self.train_start <= timestamp < self.validation_start:
                        if example.label.exit_reference_time < self.validation_start:
                            train.append(example)
                        else:
                            boundary_purge.append(ExcludedDecision(timestamp, symbol))
                    elif self.validation_start <= timestamp < self.validation_end_exclusive:
                        if example.label.exit_reference_time < self.validation_end_exclusive:
                            validation.append(example)
                        else:
                            tail_purge.append(ExcludedDecision(timestamp, symbol))
                for timestamp in dataset.unavailable_feature_timestamps:
                    if self.train_start <= timestamp < self.validation_start:
                        unavailable_train.append(ExcludedDecision(timestamp, symbol))
                    elif self.validation_start <= timestamp < self.validation_end_exclusive:
                        unavailable_validation.append(ExcludedDecision(timestamp, symbol))
            for name, rows in (("train", train), ("validation", validation)):
                if {row.feature.symbol for row in rows} != {"BTCUSDT", "ETHUSDT"}:
                    raise TemporalSplitError(f"both symbols must contribute actual {name} rows")
                object.__setattr__(self, name, tuple(sorted(
                    rows, key=lambda row: (row.feature.timestamp, row.feature.symbol),
                )))
            for name, rows in (
                ("purged_training_boundary", boundary_purge),
                ("purged_validation_tail", tail_purge),
                ("unavailable_train", unavailable_train),
                ("unavailable_validation", unavailable_validation),
            ):
                object.__setattr__(self, name, tuple(sorted(rows, key=lambda row: (row.timestamp, row.symbol))))
        except TemporalSplitError:
            raise
        except (ValueError, TypeError, AttributeError, OverflowError) as error:
            raise TemporalSplitError(f"invalid training dataset: {error}") from error


def build_purged_temporal_split(
    datasets: tuple[TrainingDataset, ...], *, train_start: datetime,
    validation_start: datetime, validation_end_exclusive: datetime,
) -> PurgedTemporalSplit:
    """Build the split without a shuffle, embargo, or future-period metric."""
    return PurgedTemporalSplit(datasets, train_start, validation_start, validation_end_exclusive)
