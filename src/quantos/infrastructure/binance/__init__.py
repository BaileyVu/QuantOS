"""Binance Spot infrastructure adapters."""

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
from quantos.infrastructure.binance.range_fetch import BinanceSpotHistoricalRangeFetcher
from quantos.infrastructure.binance.live_klines import BinanceLiveMarketDataError
from quantos.infrastructure.binance.live_stream import BinanceSpotLiveMarketDataAdapter

__all__ = [
    "BinanceDailyArchiveError",
    "BinanceMarketDataError",
    "BinanceLiveMarketDataError",
    "BinanceSpotDailyArchiveAdapter",
    "BinanceSpotDailyArchiveRangeFetcher",
    "BinanceSpotHistoricalKlineAdapter",
    "BinanceSpotHistoricalRangeFetcher",
    "BinanceSpotLiveMarketDataAdapter",
]
