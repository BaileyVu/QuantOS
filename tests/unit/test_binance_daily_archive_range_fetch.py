"""Offline tests for whole-day Binance Spot archive composition."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock, call, create_autospec, patch

from quantos.domain.market_data import Candle
from quantos.infrastructure.binance import (
    BinanceDailyArchiveError,
    BinanceSpotDailyArchiveAdapter,
    BinanceSpotDailyArchiveRangeFetcher,
)
from tests.unit.test_binance_daily_archive import (
    archive_row,
    checksum_payload,
    csv_payload,
    valid_zip,
)

UTC = timezone.utc
START = datetime(2025, 1, 1, tzinfo=UTC)
ONE_DAY = timedelta(days=1)


def candle(open_time: datetime, *, symbol: str = "BTCUSDT") -> Candle:
    return Candle(
        symbol=symbol,
        interval="1m",
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59, microseconds=999_999),
        open=Decimal("100"),
        high=Decimal("103"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=Decimal("1"),
        quote_volume=Decimal("102"),
        trade_count=1,
    )


def fake_adapter(*days: tuple[Candle, ...] | Exception) -> Mock:
    adapter = create_autospec(BinanceSpotDailyArchiveAdapter, instance=True)
    adapter.fetch_daily_klines.side_effect = days
    return adapter


def daily_call(archive_date: date, *, symbol: str = "BTCUSDT") -> object:
    return call(symbol=symbol, interval="1m", archive_date=archive_date)


class BinanceSpotDailyArchiveRangeFetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        for module in ("daily_archive", "klines"):
            guard = patch(
                f"quantos.infrastructure.binance.{module}.urlopen",
                side_effect=AssertionError("real network access is forbidden"),
            )
            guard.start()
            self.addCleanup(guard.stop)

    def fetch(self, adapter: Mock, **overrides: object) -> tuple[Candle, ...]:
        arguments: dict[str, object] = {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "start_open_time": START,
            "end_open_time_exclusive": START + ONE_DAY,
        }
        arguments.update(overrides)
        return BinanceSpotDailyArchiveRangeFetcher(adapter).fetch_open_time_range(
            **arguments
        )

    def assert_rejected(self, message: str, **overrides: object) -> None:
        adapter = fake_adapter(())
        with self.assertRaisesRegex(ValueError, message):
            self.fetch(adapter, **overrides)
        adapter.fetch_daily_klines.assert_not_called()

    def test_one_day_for_both_v1_symbols(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                day = (candle(START, symbol=symbol),)
                adapter = fake_adapter(day)

                result = self.fetch(adapter, symbol=symbol)

                self.assertEqual(result, day)
                self.assertIs(result[0], day[0])
                self.assertEqual(
                    adapter.fetch_daily_klines.call_args_list,
                    [daily_call(date(2025, 1, 1), symbol=symbol)],
                )

    def test_three_days_are_requested_once_each_in_ascending_order(self) -> None:
        days = tuple((candle(START + ONE_DAY * index),) for index in range(3))
        adapter = fake_adapter(*days)

        result = self.fetch(adapter, end_open_time_exclusive=START + ONE_DAY * 3)

        self.assertEqual(result, days[0] + days[1] + days[2])
        self.assertEqual(
            adapter.fetch_daily_klines.call_args_list,
            [daily_call(date(2025, 1, day)) for day in (1, 2, 3)],
        )

    def test_preserves_provider_order_within_each_day(self) -> None:
        day = tuple(candle(START + timedelta(minutes=minute)) for minute in (2, 0, 1))

        self.assertEqual(self.fetch(fake_adapter(day)), day)

    def test_concatenates_days_without_sorting_or_filtering_rows(self) -> None:
        # Deliberately inconsistent provider dates remain visible downstream.
        first = (candle(START + ONE_DAY), candle(START - ONE_DAY))
        second = (candle(START),)
        adapter = fake_adapter(first, second)

        result = self.fetch(adapter, end_open_time_exclusive=START + ONE_DAY * 2)

        self.assertEqual(result, first + second)
        for actual, expected in zip(result, first + second, strict=True):
            self.assertIs(actual, expected)

    def test_preserves_identical_and_conflicting_duplicates_across_days(self) -> None:
        original = candle(START)
        conflicting = replace(original, volume=Decimal("2"))
        first = (original, original)
        second = (original, conflicting)

        result = self.fetch(
            fake_adapter(first, second), end_open_time_exclusive=START + ONE_DAY * 2
        )

        self.assertEqual(result, first + second)
        self.assertEqual(len(result), 4)

    def test_preserves_missing_minutes_without_filling(self) -> None:
        day = (candle(START), candle(START + timedelta(minutes=2)))

        self.assertEqual(self.fetch(fake_adapter(day)), day)

    def test_empty_single_day_returns_empty_tuple(self) -> None:
        result = self.fetch(fake_adapter(()))

        self.assertEqual(result, ())
        self.assertIsInstance(result, tuple)

    def test_empty_days_do_not_stop_or_replace_other_days(self) -> None:
        for empty_index in range(3):
            with self.subTest(empty_index=empty_index):
                days = [(candle(START + ONE_DAY * index),) for index in range(3)]
                days[empty_index] = ()
                adapter = fake_adapter(*days)

                result = self.fetch(
                    adapter, end_open_time_exclusive=START + ONE_DAY * 3
                )

                self.assertEqual(result, days[0] + days[1] + days[2])
                self.assertEqual(
                    adapter.fetch_daily_klines.call_args_list,
                    [daily_call(date(2025, 1, day)) for day in (1, 2, 3)],
                )

    def test_all_empty_days_are_still_requested(self) -> None:
        adapter = fake_adapter((), (), ())

        result = self.fetch(adapter, end_open_time_exclusive=START + ONE_DAY * 3)

        self.assertEqual(result, ())
        self.assertEqual(
            adapter.fetch_daily_klines.call_args_list,
            [daily_call(date(2025, 1, day)) for day in (1, 2, 3)],
        )

    def test_result_is_an_immutable_tuple(self) -> None:
        result = self.fetch(fake_adapter((candle(START),)))

        self.assertIsInstance(result, tuple)
        with self.assertRaises(TypeError):
            result[0] = candle(START + ONE_DAY)

    def test_failure_propagates_without_partial_success_or_later_calls(self) -> None:
        for failure_index in range(3):
            for error in (BinanceDailyArchiveError("checksum mismatch"), RuntimeError("offline")):
                with self.subTest(day=failure_index, error=type(error).__name__):
                    days = [(candle(START + ONE_DAY * index),) for index in range(3)]
                    days[failure_index] = error
                    adapter = fake_adapter(*days)

                    with self.assertRaises(type(error)) as captured:
                        self.fetch(adapter, end_open_time_exclusive=START + ONE_DAY * 3)

                    self.assertIs(captured.exception, error)
                    self.assertEqual(
                        adapter.fetch_daily_klines.call_args_list,
                        [daily_call(date(2025, 1, day + 1)) for day in range(failure_index + 1)],
                    )

    def test_rejects_naive_bounds_before_calls(self) -> None:
        for field in ("start_open_time", "end_open_time_exclusive"):
            with self.subTest(field=field):
                self.assert_rejected("UTC", **{field: datetime(2025, 1, 1)})

    def test_rejects_non_utc_offsets_before_calls(self) -> None:
        for field in ("start_open_time", "end_open_time_exclusive"):
            for hours in (-7, 7):
                with self.subTest(field=field, hours=hours):
                    value = datetime(2025, 1, 1, tzinfo=timezone(timedelta(hours=hours)))
                    self.assert_rejected("UTC", **{field: value})

    def test_rejects_non_midnight_bounds_before_calls(self) -> None:
        for field in ("start_open_time", "end_open_time_exclusive"):
            for offset in (
                timedelta(hours=1), timedelta(minutes=1),
                timedelta(seconds=1), timedelta(microseconds=1),
            ):
                with self.subTest(field=field, offset=offset):
                    base = START if field == "start_open_time" else START + ONE_DAY
                    self.assert_rejected("midnight", **{field: base + offset})

    def test_rejects_equal_and_reversed_ranges_before_calls(self) -> None:
        for end in (START, START - ONE_DAY):
            with self.subTest(end=end):
                self.assert_rejected("after", end_open_time_exclusive=end)

    def test_rejects_invalid_symbols_before_calls(self) -> None:
        for symbol in ("SOLUSDT", "btcusdt", "", "BTCUSDT ", None, 1, []):
            with self.subTest(symbol=symbol):
                self.assert_rejected("symbol", symbol=symbol)

    def test_rejects_invalid_intervals_before_calls(self) -> None:
        for interval in ("5m", "1M", "", "1m ", None, 1):
            with self.subTest(interval=interval):
                self.assert_rejected("interval", interval=interval)

    def test_rejects_non_datetime_bounds_before_calls(self) -> None:
        for field in ("start_open_time", "end_open_time_exclusive"):
            for value in (None, START.date(), "2025-01-01"):
                with self.subTest(field=field, value=value):
                    self.assert_rejected("UTC", **{field: value})

    def test_cross_era_composition_delegates_normalization_to_single_day_adapter(self) -> None:
        responses: list[bytes] = []
        expected_calls = []
        for archive_date, minute, unit in (
            (date(2024, 12, 31), 1439, "milliseconds"),
            (date(2025, 1, 1), 0, "microseconds"),
        ):
            archive = valid_zip(
                archive_date=archive_date,
                csv_bytes=csv_payload([
                    archive_row(archive_date, minute=minute, timestamp_unit=unit)
                ]),
            )
            responses.extend((checksum_payload(archive, archive_date=archive_date), archive))
            url = (
                "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/"
                f"BTCUSDT-1m-{archive_date.isoformat()}.zip"
            )
            expected_calls.extend((call(f"{url}.CHECKSUM", 10.0), call(url, 10.0)))
        http_get = Mock(side_effect=responses)
        adapter = BinanceSpotDailyArchiveAdapter(http_get=http_get)
        fetcher = BinanceSpotDailyArchiveRangeFetcher(adapter)

        result = fetcher.fetch_open_time_range(
            symbol="BTCUSDT", interval="1m",
            start_open_time=datetime(2024, 12, 31, tzinfo=UTC),
            end_open_time_exclusive=datetime(2025, 1, 2, tzinfo=UTC),
        )

        self.assertEqual(http_get.call_args_list, expected_calls)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].open_time, datetime(2024, 12, 31, 23, 59, tzinfo=UTC))
        self.assertEqual(result[0].close_time, datetime(2024, 12, 31, 23, 59, 59, 999_000, tzinfo=UTC))
        self.assertEqual(result[1].open_time, START)
        self.assertEqual(result[1].close_time, datetime(2025, 1, 1, 0, 0, 59, 999_999, tzinfo=UTC))
