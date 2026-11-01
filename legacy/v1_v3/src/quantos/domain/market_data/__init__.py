"""Market Data domain contracts and canonical validation."""

from quantos.domain.market_data.contracts import (
    Candle,
    DatasetIdentity,
    DatasetValidationStatus,
    MarketEvent,
)
from quantos.domain.market_data.validation import (
    DatasetValidationError,
    ValidatedCandleSequence,
    validate_candle_sequence,
)

__all__ = [
    "Candle",
    "DatasetIdentity",
    "DatasetValidationError",
    "DatasetValidationStatus",
    "MarketEvent",
    "ValidatedCandleSequence",
    "validate_candle_sequence",
]
