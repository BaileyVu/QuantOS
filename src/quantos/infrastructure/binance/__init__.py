"""Binance Spot infrastructure adapters."""

from quantos.infrastructure.binance.aggregate_trades import (
    AGGREGATE_TRADE_NORMALIZER_VERSION,
    ARCHIVE_AGGREGATE_TRADE_COLUMNS,
    ARCHIVE_MICROSECOND_ERA_START,
    BinanceAggregateTradeNormalizationError,
    BinanceAggregateTradeSource,
    archive_timestamp_unit,
    canonical_aggregate_trade_sequence_bytes,
    canonical_aggregate_trade_sequence_sha256,
    normalize_archive_aggregate_trade,
    normalize_rest_aggregate_trade,
    normalize_websocket_aggregate_trade,
    raw_content_sha256,
)
from quantos.infrastructure.binance.daily_archive import (
    BinanceDailyArchiveError,
    BinanceSpotDailyArchiveAdapter,
)
from quantos.infrastructure.binance.daily_archive_range_fetch import (
    BinanceSpotDailyArchiveRangeFetcher,
)
from quantos.infrastructure.binance.klines import (
    BinanceMarketDataError,
    BinanceSpotHistoricalKlineAdapter,
)
from quantos.infrastructure.binance.live_klines import BinanceLiveMarketDataError
from quantos.infrastructure.binance.live_stream import BinanceSpotLiveMarketDataAdapter
from quantos.infrastructure.binance.range_fetch import BinanceSpotHistoricalRangeFetcher

__all__ = [
    "AGGREGATE_TRADE_NORMALIZER_VERSION",
    "ARCHIVE_AGGREGATE_TRADE_COLUMNS",
    "ARCHIVE_MICROSECOND_ERA_START",
    "BinanceAggregateTradeNormalizationError",
    "BinanceAggregateTradeSource",
    "BinanceDailyArchiveError",
    "BinanceLiveMarketDataError",
    "BinanceMarketDataError",
    "BinanceSpotDailyArchiveAdapter",
    "BinanceSpotDailyArchiveRangeFetcher",
    "BinanceSpotHistoricalKlineAdapter",
    "BinanceSpotHistoricalRangeFetcher",
    "BinanceSpotLiveMarketDataAdapter",
    "archive_timestamp_unit",
    "canonical_aggregate_trade_sequence_bytes",
    "canonical_aggregate_trade_sequence_sha256",
    "normalize_archive_aggregate_trade",
    "normalize_rest_aggregate_trade",
    "normalize_websocket_aggregate_trade",
    "raw_content_sha256",
]
