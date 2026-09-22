"""Application use cases and runtime coordination."""

from quantos.application.historical_ingestion import (
    CanonicalCandleDatasetWriter,
    HistoricalCandleRangeFetcher,
    HistoricalIngestionError,
    PersistedHistoricalDataset,
    extend_and_persist_historical_range,
    ingest_and_persist_historical_range,
    ingest_historical_range,
)
from quantos.application.runtime import run
from quantos.application.aggregate_trade_ranges import (
    AggregateTradePartitionCatalog,
    AggregateTradeRangeStreamReport,
    AggregateTradeRangeCompositionError,
    DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
    canonical_range_manifest_bytes,
    compose_aggregate_trade_range,
    iter_aggregate_trade_range,
    replay_aggregate_trade_range,
    validate_aggregate_trade_partition_boundary,
)
from quantos.application.aggregate_trade_minute_states import (
    AggregateTradeMinuteAggregationError,
    AggregateTradeStreamingDiagnostics,
    aggregate_trade_minute_states,
    replay_aggregate_trade_minute_states,
)
from quantos.application.live_aggregate_trade_minutes import (
    LiveAggregateTradeMinuteEngine,
    LiveAggregateTradeMinuteEngineError,
)

__all__ = [
    "CanonicalCandleDatasetWriter",
    "AggregateTradePartitionCatalog",
    "AggregateTradeRangeStreamReport",
    "AggregateTradeRangeCompositionError",
    "DEFAULT_AGGREGATE_TRADE_BATCH_SIZE",
    "HistoricalCandleRangeFetcher",
    "HistoricalIngestionError",
    "PersistedHistoricalDataset",
    "extend_and_persist_historical_range",
    "ingest_and_persist_historical_range",
    "ingest_historical_range",
    "canonical_range_manifest_bytes",
    "compose_aggregate_trade_range",
    "iter_aggregate_trade_range",
    "replay_aggregate_trade_range",
    "run",
    "validate_aggregate_trade_partition_boundary",
    "AggregateTradeMinuteAggregationError",
    "AggregateTradeStreamingDiagnostics",
    "aggregate_trade_minute_states",
    "replay_aggregate_trade_minute_states",
    "LiveAggregateTradeMinuteEngine",
    "LiveAggregateTradeMinuteEngineError",
]
