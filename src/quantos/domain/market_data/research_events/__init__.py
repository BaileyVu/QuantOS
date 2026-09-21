"""Research-only event contracts owned by the Market Data domain."""

from quantos.domain.market_data.research_events.aggregate_trade import (
    AggregateTrade,
    AggregateTradeDatasetIdentity,
    AggressorSide,
    ResearchDatasetRole,
    ResearchEventValidationStatus,
    SourceTimestampUnit,
    aggregate_trade_dataset_id,
    aggregate_trade_dataset_identity_bytes,
    normalize_source_timestamp,
)
from quantos.domain.market_data.research_events.validation import (
    AggregateTradeValidationError,
    ValidatedAggregateTradeSequence,
    validate_aggregate_trade_sequence,
)
from quantos.domain.market_data.research_events.content_identity import (
    AggregateTradeContentIdentityError,
    canonical_aggregate_trade_sequence_bytes,
    canonical_aggregate_trade_sequence_sha256,
)
from quantos.domain.market_data.research_events.archive_dataset import (
    AggregateIdContinuityStatus,
    AggregateTradeArchiveManifest,
    ArchiveChecksumStatus,
    ValidatedAggregateTradeArchive,
    aggregate_trade_archive_manifest_bytes,
    aggregate_trade_archive_manifest_from_bytes,
    aggregate_trade_archive_manifest_id,
)

__all__ = [
    "AggregateTrade",
    "AggregateTradeDatasetIdentity",
    "AggregateTradeContentIdentityError",
    "AggregateIdContinuityStatus",
    "AggregateTradeArchiveManifest",
    "AggregateTradeValidationError",
    "AggressorSide",
    "ArchiveChecksumStatus",
    "ResearchDatasetRole",
    "ResearchEventValidationStatus",
    "SourceTimestampUnit",
    "ValidatedAggregateTradeSequence",
    "ValidatedAggregateTradeArchive",
    "aggregate_trade_archive_manifest_bytes",
    "aggregate_trade_archive_manifest_from_bytes",
    "aggregate_trade_archive_manifest_id",
    "aggregate_trade_dataset_id",
    "aggregate_trade_dataset_identity_bytes",
    "canonical_aggregate_trade_sequence_bytes",
    "canonical_aggregate_trade_sequence_sha256",
    "normalize_source_timestamp",
    "validate_aggregate_trade_sequence",
]
