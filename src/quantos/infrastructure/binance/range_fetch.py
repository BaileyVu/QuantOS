"""Safe, deterministic pagination for Binance Spot historical kline ranges."""

from __future__ import annotations

from datetime import datetime, timedelta

from quantos.domain.common import V1_INTERVAL, require_utc, require_v1_symbol
from quantos.domain.market_data import Candle
from quantos.infrastructure.binance.klines import (
    BinanceMarketDataError,
    BinanceSpotHistoricalKlineAdapter,
)

BINANCE_KLINE_PAGE_LIMIT = 1_000
_ONE_MINUTE = timedelta(minutes=1)
_ONE_MILLISECOND = timedelta(milliseconds=1)


def _require_minute_aligned(value: datetime, field_name: str) -> None:
    require_utc(value, field_name)
    if value.second != 0 or value.microsecond != 0:
        raise ValueError(f"{field_name} must be aligned to an exact UTC minute")


class BinanceSpotHistoricalRangeFetcher:
    """Fetch an exclusive-open-time range through the single-page Binance adapter."""

    def __init__(self, page_adapter: BinanceSpotHistoricalKlineAdapter) -> None:
        self._page_adapter = page_adapter

    def fetch_open_time_range(
        self,
        *,
        symbol: str,
        interval: str,
        start_open_time: datetime,
        end_open_time_exclusive: datetime,
    ) -> tuple[Candle, ...]:
        """Return provider-normalized candles in ``[start_open_time, end_open_time_exclusive)``.

        This is pagination and range filtering only. It neither validates a
        canonical dataset nor repairs provider data.
        """
        require_v1_symbol(symbol)
        if interval != V1_INTERVAL:
            raise ValueError(f"interval must be {V1_INTERVAL!r}")
        _require_minute_aligned(start_open_time, "start_open_time")
        _require_minute_aligned(end_open_time_exclusive, "end_open_time_exclusive")
        if end_open_time_exclusive <= start_open_time:
            raise ValueError("end_open_time_exclusive must be after start_open_time")

        request_end_time = end_open_time_exclusive - _ONE_MILLISECOND
        cursor = start_open_time
        accumulated: list[Candle] = []

        while cursor < end_open_time_exclusive:
            page = self._page_adapter.fetch_klines(
                symbol=symbol,
                interval=interval,
                start_time=cursor,
                end_time=request_end_time,
                limit=BINANCE_KLINE_PAGE_LIMIT,
            )
            if not page:
                break
            if all(candle.open_time < cursor for candle in page):
                raise BinanceMarketDataError(
                    "Binance kline page contains only candles before the pagination cursor"
                )

            last_open_time = page[-1].open_time
            if last_open_time.second != 0 or last_open_time.microsecond != 0:
                raise BinanceMarketDataError(
                    "Binance kline page ended with an open time not aligned to an exact UTC minute"
                )
            next_cursor = last_open_time + _ONE_MINUTE
            if next_cursor <= cursor:
                raise BinanceMarketDataError("Binance kline page did not advance the pagination cursor")

            accumulated.extend(
                candle
                for candle in page
                if start_open_time <= candle.open_time < end_open_time_exclusive
            )
            if len(page) < BINANCE_KLINE_PAGE_LIMIT:
                break
            cursor = next_cursor

        return tuple(accumulated)
