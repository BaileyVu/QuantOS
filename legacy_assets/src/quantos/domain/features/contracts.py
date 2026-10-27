"""Provider-independent feature contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from quantos.domain.common import require_decimal, require_non_empty, require_utc, require_v1_symbol


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """A deterministic, versioned feature vector at a decision timestamp."""

    timestamp: datetime
    symbol: str
    feature_version: str
    values: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        require_v1_symbol(self.symbol)
        require_non_empty(self.feature_version, "feature_version")
        if not self.values:
            raise ValueError("values must not be empty")
        copied_values: dict[str, Decimal] = {}
        for name, value in self.values.items():
            require_non_empty(name, "feature name")
            copied_values[name] = require_decimal(value, f"feature {name}")
        object.__setattr__(self, "values", MappingProxyType(copied_values))

