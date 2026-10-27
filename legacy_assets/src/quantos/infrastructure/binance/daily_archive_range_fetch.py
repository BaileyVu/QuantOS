"""In-memory composition of complete Binance Spot daily archive ranges."""

from __future__ import annotations

from datetime import datetime, timedelta

from quantos.domain.common import V1_INTERVAL, require_utc, require_v1_symbol
from quantos.domain.market_data import Candle
from quantos.infrastructure.binance.daily_archive import BinanceSpotDailyArchiveAdapter


def _require_midnight(value: datetime, field_name: str) -> None:
    require_utc(value, field_name)
    if value.hour or value.minute or value.second or value.microsecond:
        raise ValueError(f"{field_name} must be aligned to exact UTC midnight")


class BinanceSpotDailyArchiveRangeFetcher:
    """Compose whole UTC archive days through the single-day archive adapter."""

    def __init__(self, daily_adapter: BinanceSpotDailyArchiveAdapter) -> None:
        self._daily_adapter = daily_adapter

    def fetch_open_time_range(
        self,
        *,
        symbol: str,
        interval: str,
        start_open_time: datetime,
        end_open_time_exclusive: datetime,
    ) -> tuple[Candle, ...]:
        """Fetch each day in ``[start_open_time, end_open_time_exclusive)`` once.

        Bounds must be UTC midnights. Rows retain provider order, including
        duplicates and gaps; empty days do not stop acquisition. Any adapter
        failure propagates immediately without returning a partial range.
        Dataset identity, canonical validation, and persistence belong downstream.
        """
        if type(symbol) is not str:
            raise ValueError("symbol must be an exact built-in string")
        require_v1_symbol(symbol)
        if type(interval) is not str or interval != V1_INTERVAL:
            raise ValueError(f"interval must be {V1_INTERVAL!r}")
        _require_midnight(start_open_time, "start_open_time")
        _require_midnight(end_open_time_exclusive, "end_open_time_exclusive")
        if end_open_time_exclusive <= start_open_time:
            raise ValueError("end_open_time_exclusive must be after start_open_time")

        archive_date = start_open_time.date()
        end_date_exclusive = end_open_time_exclusive.date()
        accumulated: list[Candle] = []
        while archive_date < end_date_exclusive:
            accumulated.extend(
                self._daily_adapter.fetch_daily_klines(
                    symbol=symbol,
                    interval=interval,
                    archive_date=archive_date,
                )
            )
            archive_date += timedelta(days=1)

        return tuple(accumulated)
