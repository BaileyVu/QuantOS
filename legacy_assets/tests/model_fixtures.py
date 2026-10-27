"""Small, deterministic Phase 4A inputs shared by offline model tests."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from quantos.domain.alpha import build_training_dataset, build_purged_temporal_split
from quantos.infrastructure.models import LightGBMConfig
from tests.unit.test_alpha_training_data import source, START, MINUTE, US


def datasets(count=180):
    return tuple(build_training_dataset(source(count, symbol=symbol)) for symbol in ("BTCUSDT", "ETHUSDT"))


def split(inputs=None, **overrides):
    bounds = dict(train_start=START+20*MINUTE+MINUTE-US,
                  validation_start=START+100*MINUTE+MINUTE-US,
                  validation_end_exclusive=START+150*MINUTE+MINUTE-US)
    bounds.update(overrides)
    return build_purged_temporal_split(datasets() if inputs is None else inputs, **bounds)


def config(**overrides):
    fields = dict(learning_rate=Decimal("0.05"), num_leaves=7, max_depth=3,
                  min_data_in_leaf=3, lambda_l2=Decimal("1"), num_boost_round=12,
                  early_stopping_rounds=3, random_seed=20260911)
    fields.update(overrides)
    return LightGBMConfig(**fields)


def change_example(dataset, index, *, value=None, feature=None):
    rows = list(dataset.examples)
    example = rows[index-20]
    rows[index-20] = replace(example,
                            label=example.label if value is None else replace(example.label, value=value),
                            feature=example.feature if feature is None else feature)
    return replace(dataset, examples=tuple(rows))


def unavailable(dataset, *indices):
    removed = {START+i*MINUTE+MINUTE-US for i in indices}
    return replace(dataset,
                   examples=tuple(row for row in dataset.examples if row.feature.timestamp not in removed),
                   unavailable_feature_timestamps=tuple(sorted(removed)))
