"""Local canonical historical dataset persistence."""

from quantos.infrastructure.storage.aggregate_trade_duckdb import (
    DuckDBAggregateTradeArchiveQuery,
    DuckDBAggregateTradeQueryError,
)
from quantos.infrastructure.storage.aggregate_trade_catalog import (
    AggregateTradeArchiveCatalogView,
    AggregateTradeCatalogError,
    CatalogedAggregateTradeRevision,
    LocalAggregateTradeArchiveCatalog,
)
from quantos.infrastructure.storage.aggregate_trade_parquet import (
    AGGREGATE_TRADE_SCHEMA,
    AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION,
    AggregateTradeArchivePublication,
    AggregateTradeDatasetCollisionError,
    AggregateTradeParquetStorageError,
    ParquetAggregateTradeArchiveStore,
)
from quantos.infrastructure.storage.duckdb_query import (
    DuckDBCandleDatasetQuery,
    DuckDBQueryError,
)
from quantos.infrastructure.storage.parquet import (
    CANDLE_SCHEMA,
    STORAGE_SCHEMA_VERSION,
    DatasetCollisionError,
    ParquetCandleDatasetStore,
    ParquetStorageError,
    dataset_id,
)

__all__ = [
    "AGGREGATE_TRADE_SCHEMA",
    "AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION",
    "AggregateTradeArchivePublication",
    "AggregateTradeArchiveCatalogView",
    "AggregateTradeCatalogError",
    "CatalogedAggregateTradeRevision",
    "AggregateTradeDatasetCollisionError",
    "AggregateTradeParquetStorageError",
    "CANDLE_SCHEMA",
    "DatasetCollisionError",
    "DuckDBAggregateTradeArchiveQuery",
    "DuckDBAggregateTradeQueryError",
    "DuckDBCandleDatasetQuery",
    "DuckDBQueryError",
    "ParquetAggregateTradeArchiveStore",
    "LocalAggregateTradeArchiveCatalog",
    "ParquetCandleDatasetStore",
    "ParquetStorageError",
    "STORAGE_SCHEMA_VERSION",
    "dataset_id",
]
