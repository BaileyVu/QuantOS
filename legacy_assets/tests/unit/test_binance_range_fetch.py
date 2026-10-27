"""Tests for safe Binance Spot historical open-time range pagination."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.message import Message
from urllib.error import HTTPError
import unittest

from quantos.domain.market_data import Candle, ValidatedCandleSequence
from quantos.infrastructure.binance import (
    BinanceMarketDataError,
    BinanceSpotHistoricalKlineAdapter,
    BinanceSpotHistoricalRangeFetcher,
)
from quantos.infrastructure.binance.range_fetch import BINANCE_KLINE_PAGE_LIMIT

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def candle(
    minute: int,
    *,
    close_offset: timedelta = timedelta(seconds=59, milliseconds=999),
    volume: Decimal = Decimal("1"),
) -> Candle:
    open_time = START + timedelta(minutes=minute)
    return Candle(
        symbol="BTCUSDT",
        interval="1m",
        open_time=open_time,
        close_time=open_time + close_offset,
        open=Decimal("100"),
        high=Decimal("103"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=volume,
        quote_volume=Decimal("102"),
        trade_count=1,
    )


def candles(
    start_minute: int, count: int, *, close_offset: timedelta = timedelta(seconds=59, milliseconds=999)) -> tuple[Candle, ...]:
    return tuple(candle(minute, close_offset=close_offset) for minute in range(start_minute, start_minute + count))


class FakePageAdapter:
    def __init__(self, pages: tuple[tuple[Candle, ...] | Exception, ...]) -> None:
        self._pages = list(pages)
        self.calls: list[dict[str, object]] = []

    def fetch_klines(self, **arguments: object) -> tuple[Candle, ...]:
        self.calls.append(arguments)
        if not self._pages:
            raise AssertionError("unexpected additional page request")
        page = self._pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


class BinanceSpotHistoricalRangeFetcherTests(unittest.TestCase):
    def fetcher(self, *pages: tuple[Candle, ...] | Exception) -> tuple[BinanceSpotHistoricalRangeFetcher, FakePageAdapter]:
        adapter = FakePageAdapter(tuple(pages))
        return BinanceSpotHistoricalRangeFetcher(adapter), adapter

    def fetch(
        self,
        fetcher: BinanceSpotHistoricalRangeFetcher,
        *,
        start_open_time: datetime = START,
        end_open_time_exclusive: datetime = START + timedelta(minutes=1),
    ) -> tuple[Candle, ...]:
        return fetcher.fetch_open_time_range(
            symbol="BTCUSDT",
            interval="1m",
            start_open_time=start_open_time,
            end_open_time_exclusive=end_open_time_exclusive,
        )

    def test_one_candle_range_uses_inclusive_start_and_exclusive_end(self) -> None:
        fetcher, adapter = self.fetcher((candle(0),))

        result = self.fetch(fetcher)

        self.assertEqual(result, (candle(0),))
        self.assertEqual(adapter.calls, [{
            "symbol": "BTCUSDT",
            "interval": "1m",
            "start_time": START,
            "end_time": START + timedelta(minutes=1, milliseconds=-1),
            "limit": BINANCE_KLINE_PAGE_LIMIT,
        }])

    def test_rejects_invalid_ranges_before_page_fetching(self) -> None:
        invalid_ranges = (
            (datetime(2026, 1, 1), START + timedelta(minutes=1), "UTC"),
            (
                datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=7))),
                START + timedelta(minutes=1),
                "UTC",
            ),
            (START, START, "after"),
            (START + timedelta(minutes=1), START, "after"),
            (START + timedelta(seconds=1), START + timedelta(minutes=1), "aligned"),
            (START, START + timedelta(minutes=1, microseconds=1), "aligned"),
        )
        for start_open_time, end_open_time_exclusive, message in invalid_ranges:
            with self.subTest(start=start_open_time, end=end_open_time_exclusive):
                fetcher, adapter = self.fetcher(())

                with self.assertRaisesRegex(ValueError, message):
                    self.fetch(
                        fetcher,
                        start_open_time=start_open_time,
                        end_open_time_exclusive=end_open_time_exclusive,
                    )

                self.assertEqual(adapter.calls, [])

    def test_rejects_unsupported_symbol_and_interval_before_page_fetching(self) -> None:
        invalid_requests = (
            ("SOLUSDT", "1m", "symbol"),
            ("BTCUSDT", "5m", "interval"),
        )
        for symbol, interval, message in invalid_requests:
            with self.subTest(symbol=symbol, interval=interval):
                fetcher, adapter = self.fetcher(())

                with self.assertRaisesRegex(ValueError, message):
                    fetcher.fetch_open_time_range(
                        symbol=symbol,
                        interval=interval,
                        start_open_time=START,
                        end_open_time_exclusive=START + timedelta(minutes=1),
                    )

                self.assertEqual(adapter.calls, [])

    def test_exactly_one_thousand_candles_use_one_maximum_size_page(self) -> None:
        page = candles(0, BINANCE_KLINE_PAGE_LIMIT)
        fetcher, adapter = self.fetcher(page)

        result = self.fetch(
            fetcher,
            end_open_time_exclusive=START + timedelta(minutes=BINANCE_KLINE_PAGE_LIMIT),
        )

        self.assertEqual(result, page)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0]["limit"], BINANCE_KLINE_PAGE_LIMIT)

    def test_multiple_pages_advance_by_last_open_time_not_close_time(self) -> None:
        first_page = candles(
            0,
            BINANCE_KLINE_PAGE_LIMIT,
            close_offset=timedelta(seconds=10),
        )
        second_page = candles(BINANCE_KLINE_PAGE_LIMIT, BINANCE_KLINE_PAGE_LIMIT)
        third_page = candles(BINANCE_KLINE_PAGE_LIMIT * 2, 2)
        fetcher, adapter = self.fetcher(first_page, second_page, third_page)

        result = self.fetch(
            fetcher,
            end_open_time_exclusive=START + timedelta(minutes=BINANCE_KLINE_PAGE_LIMIT * 2 + 2),
        )

        self.assertEqual(result, first_page + second_page + third_page)
        self.assertEqual(
            [call["start_time"] for call in adapter.calls],
            [
                START,
                START + timedelta(minutes=BINANCE_KLINE_PAGE_LIMIT),
                START + timedelta(minutes=BINANCE_KLINE_PAGE_LIMIT * 2),
            ],
        )

    def test_short_and_empty_pages_terminate_without_extra_requests(self) -> None:
        scenarios = (
            (((),), (), 1),
            ((candles(0, 2),), candles(0, 2), 1),
            ((candles(0, BINANCE_KLINE_PAGE_LIMIT), ()), candles(0, BINANCE_KLINE_PAGE_LIMIT), 2),
        )
        for pages, expected, call_count in scenarios:
            with self.subTest(pages=pages):
                fetcher, adapter = self.fetcher(*pages)

                result = self.fetch(
                    fetcher,
                    end_open_time_exclusive=START + timedelta(minutes=BINANCE_KLINE_PAGE_LIMIT + 2),
                )

                self.assertEqual(result, expected)
                self.assertEqual(len(adapter.calls), call_count)

    def test_rejects_replayed_or_entirely_before_cursor_page(self) -> None:
        first_page = candles(0, BINANCE_KLINE_PAGE_LIMIT)
        fetcher, adapter = self.fetcher(first_page, first_page)

        with self.assertRaisesRegex(BinanceMarketDataError, "before the pagination cursor"):
            self.fetch(
                fetcher,
                end_open_time_exclusive=START + timedelta(minutes=BINANCE_KLINE_PAGE_LIMIT + 2),
            )

        self.assertEqual(len(adapter.calls), 2)

    def test_rejects_page_whose_last_open_time_cannot_advance_cursor(self) -> None:
        non_progressing_page = (candle(0),) + tuple(
            candle(-1) for _ in range(BINANCE_KLINE_PAGE_LIMIT - 1)
        )
        fetcher, adapter = self.fetcher(non_progressing_page)

        with self.assertRaisesRegex(BinanceMarketDataError, "did not advance"):
            self.fetch(fetcher, end_open_time_exclusive=START + timedelta(minutes=2))

        self.assertEqual(len(adapter.calls), 1)

    def test_rejects_a_provider_page_that_would_create_a_misaligned_cursor(self) -> None:
        misaligned_open_time = START + timedelta(seconds=30)
        misaligned_candle = Candle(
            symbol="BTCUSDT",
            interval="1m",
            open_time=misaligned_open_time,
            close_time=misaligned_open_time + timedelta(seconds=10),
            open=Decimal("100"),
            high=Decimal("103"),
            low=Decimal("99"),
            close=Decimal("102"),
            volume=Decimal("1"),
            quote_volume=Decimal("102"),
            trade_count=1,
        )
        fetcher, adapter = self.fetcher((misaligned_candle,))

        with self.assertRaisesRegex(BinanceMarketDataError, "aligned"):
            self.fetch(fetcher, end_open_time_exclusive=START + timedelta(minutes=2))

        self.assertEqual(len(adapter.calls), 1)

    def test_filters_boundaries_without_sorting_deduplicating_or_filling(self) -> None:
        page = (
            candle(0),
            candle(1),
            candle(3),
            candle(3, volume=Decimal("2")),
            candle(4),
            candle(5),
        )
        fetcher, _ = self.fetcher(page)

        result = self.fetch(
            fetcher,
            start_open_time=START + timedelta(minutes=1),
            end_open_time_exclusive=START + timedelta(minutes=4),
        )

        self.assertEqual(result, (candle(1), candle(3), candle(3, volume=Decimal("2"))))
        self.assertIsInstance(result, tuple)
        self.assertNotIsInstance(result, ValidatedCandleSequence)

    def test_rate_limit_and_ban_errors_stop_pagination_without_retrying(self) -> None:
        cases = (
            (
                BinanceMarketDataError(
                    "Binance kline HTTP request failed: 429",
                    http_status=429,
                    retry_after_seconds=30,
                ),
                429,
                30,
            ),
            (
                BinanceMarketDataError(
                    "Binance kline HTTP request failed: 429",
                    http_status=429,
                ),
                429,
                None,
            ),
            (
                BinanceMarketDataError("Binance kline HTTP request failed: 418", http_status=418),
                418,
                None,
            ),
        )
        for error, status, retry_after in cases:
            with self.subTest(status=status, retry_after=retry_after):
                fetcher, adapter = self.fetcher(error)

                with self.assertRaises(BinanceMarketDataError) as captured:
                    self.fetch(fetcher)

                self.assertIs(captured.exception, error)
                self.assertEqual(captured.exception.http_status, status)
                self.assertEqual(captured.exception.retry_after_seconds, retry_after)
                self.assertEqual(len(adapter.calls), 1)

    def test_rate_limit_on_a_later_page_stops_without_a_third_request(self) -> None:
        first_page = candles(0, BINANCE_KLINE_PAGE_LIMIT)
        rate_limited = BinanceMarketDataError(
            "Binance kline HTTP request failed: 429",
            http_status=429,
            retry_after_seconds=5,
        )
        fetcher, adapter = self.fetcher(first_page, rate_limited)

        with self.assertRaises(BinanceMarketDataError):
            self.fetch(
                fetcher,
                end_open_time_exclusive=START + timedelta(minutes=BINANCE_KLINE_PAGE_LIMIT + 2),
            )

        self.assertEqual(len(adapter.calls), 2)

    def test_unrepresentable_retry_after_stops_range_fetch_without_another_request(self) -> None:
        headers = Message()
        headers["Retry-After"] = "9" * 5_000
        http_error = HTTPError(
            "https://data-api.binance.vision/api/v3/klines",
            429,
            "too many requests",
            headers,
            None,
        )
        http_calls: list[tuple[str, float]] = []

        def http_get(url: str, timeout_seconds: float) -> bytes:
            http_calls.append((url, timeout_seconds))
            raise http_error

        fetcher = BinanceSpotHistoricalRangeFetcher(
            BinanceSpotHistoricalKlineAdapter(http_get=http_get)
        )

        with self.assertRaises(BinanceMarketDataError) as captured:
            self.fetch(fetcher)

        self.assertEqual(captured.exception.http_status, 429)
        self.assertIsNone(captured.exception.retry_after_seconds)
        self.assertEqual(len(http_calls), 1)
