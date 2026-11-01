"""Tests for the single-page Binance Spot historical-kline adapter."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.message import Message
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
import unittest

from quantos.domain.market_data import ValidatedCandleSequence
from quantos.infrastructure.binance import (
    BinanceMarketDataError,
    BinanceSpotHistoricalKlineAdapter,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
OPEN_TIME_MS = 1_767_225_600_000
CLOSE_TIME_MS = OPEN_TIME_MS + 59_999


def kline(
    *,
    open_time: int = OPEN_TIME_MS,
    close_time: int = CLOSE_TIME_MS,
    open_price: object = "100.12345678",
    high: object = "103.00000000",
    low: object = "99.00000000",
    close: object = "102.87654321",
    volume: object = "1.50000000",
    quote_volume: object = "151.50000000",
    trade_count: object = 10,
) -> list[object]:
    return [
        open_time,
        open_price,
        high,
        low,
        close,
        volume,
        close_time,
        quote_volume,
        trade_count,
        "0.75000000",
        "75.75000000",
        "0",
    ]


class RecordingHttpGet:
    def __init__(self, payload: bytes | Exception) -> None:
        self.payload = payload
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout_seconds: float) -> bytes:
        self.calls.append((url, timeout_seconds))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def response(rows: object) -> bytes:
    return json.dumps(rows).encode("utf-8")


class BinanceSpotHistoricalKlineAdapterTests(unittest.TestCase):
    def adapter(self, payload: bytes | Exception = response([])) -> tuple[BinanceSpotHistoricalKlineAdapter, RecordingHttpGet]:
        http_get = RecordingHttpGet(payload)
        return BinanceSpotHistoricalKlineAdapter(http_get=http_get), http_get

    def query_for(self, http_get: RecordingHttpGet) -> dict[str, list[str]]:
        self.assertEqual(len(http_get.calls), 1)
        url, timeout_seconds = http_get.calls[0]
        self.assertEqual(timeout_seconds, 10.0)
        parsed = urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "data-api.binance.vision")
        self.assertEqual(parsed.path, "/api/v3/klines")
        return parse_qs(parsed.query, keep_blank_values=True)

    def test_constructs_unauthenticated_utc_requests_for_both_v1_symbols(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                adapter, http_get = self.adapter()

                self.assertEqual(adapter.fetch_klines(symbol=symbol, interval="1m"), ())

                self.assertEqual(
                    self.query_for(http_get),
                    {"symbol": [symbol], "interval": ["1m"], "timeZone": ["0"]},
                )

    def test_does_not_accept_a_base_url_override(self) -> None:
        with self.assertRaisesRegex(TypeError, "base_url"):
            BinanceSpotHistoricalKlineAdapter(base_url="https://example.test")

    def test_constructs_exact_millisecond_range_and_limit_parameters(self) -> None:
        adapter, http_get = self.adapter()
        start_time = START + timedelta(milliseconds=123)
        end_time = START + timedelta(minutes=1, milliseconds=999)

        adapter.fetch_klines(
            symbol="BTCUSDT",
            interval="1m",
            start_time=start_time,
            end_time=end_time,
            limit=500,
        )

        self.assertEqual(
            self.query_for(http_get),
            {
                "symbol": ["BTCUSDT"],
                "interval": ["1m"],
                "timeZone": ["0"],
                "startTime": ["1767225600123"],
                "endTime": ["1767225660999"],
                "limit": ["500"],
            },
        )

    def test_accepts_documented_limit_boundaries(self) -> None:
        for limit in (1, 1_000):
            with self.subTest(limit=limit):
                adapter, http_get = self.adapter()

                adapter.fetch_klines(symbol="BTCUSDT", interval="1m", limit=limit)

                self.assertEqual(self.query_for(http_get)["limit"], [str(limit)])

    def test_rejects_invalid_request_parameters_without_making_a_request(self) -> None:
        invalid_requests = (
            ({"symbol": "SOLUSDT", "interval": "1m"}, "symbol"),
            ({"symbol": "BTCUSDT", "interval": "5m"}, "interval"),
            ({"symbol": "BTCUSDT", "interval": "1m", "start_time": datetime(2026, 1, 1)}, "UTC"),
            (
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "end_time": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=7))),
                },
                "UTC",
            ),
            (
                {
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "start_time": START + timedelta(minutes=1),
                    "end_time": START,
                },
                "end_time",
            ),
            ({"symbol": "BTCUSDT", "interval": "1m", "limit": 0}, "limit"),
            ({"symbol": "BTCUSDT", "interval": "1m", "limit": 1_001}, "limit"),
            ({"symbol": "BTCUSDT", "interval": "1m", "limit": True}, "integer"),
        )
        for arguments, message in invalid_requests:
            with self.subTest(arguments=arguments):
                adapter, http_get = self.adapter()

                with self.assertRaisesRegex(ValueError, message):
                    adapter.fetch_klines(**arguments)

                self.assertEqual(http_get.calls, [])

    def test_normalizes_every_canonical_field_with_exact_decimal_and_timestamp_values(self) -> None:
        adapter, _ = self.adapter(response([kline()]))

        candles = adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.symbol, "BTCUSDT")
        self.assertEqual(candle.interval, "1m")
        self.assertEqual(candle.open_time, datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(candle.close_time, datetime(2026, 1, 1, 0, 0, 59, 999_000, tzinfo=UTC))
        self.assertEqual(candle.open, Decimal("100.12345678"))
        self.assertEqual(candle.high, Decimal("103.00000000"))
        self.assertEqual(candle.low, Decimal("99.00000000"))
        self.assertEqual(candle.close, Decimal("102.87654321"))
        self.assertEqual(candle.volume, Decimal("1.50000000"))
        self.assertEqual(candle.quote_volume, Decimal("151.50000000"))
        self.assertEqual(candle.trade_count, 10)

    def test_preserves_provider_order_duplicates_gaps_and_source_fixture(self) -> None:
        rows = [
            kline(open_time=OPEN_TIME_MS + 120_000, close_time=CLOSE_TIME_MS + 120_000),
            kline(open_time=OPEN_TIME_MS, close_time=CLOSE_TIME_MS),
            kline(open_time=OPEN_TIME_MS, close_time=CLOSE_TIME_MS, volume="2.0"),
        ]
        original_rows = deepcopy(rows)
        adapter, _ = self.adapter(response(rows))

        candles = adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

        self.assertEqual([candle.open_time for candle in candles], [
            START + timedelta(minutes=2),
            START,
            START,
        ])
        self.assertEqual(candles[1].volume, Decimal("1.50000000"))
        self.assertEqual(candles[2].volume, Decimal("2.0"))
        self.assertEqual(rows, original_rows)

    def test_empty_array_returns_empty_immutable_tuple_not_a_validated_dataset(self) -> None:
        adapter, _ = self.adapter(response([]))

        result = adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

        self.assertEqual(result, ())
        self.assertIsInstance(result, tuple)
        self.assertNotIsInstance(result, ValidatedCandleSequence)

    def test_rejects_non_array_response_and_changed_row_shapes(self) -> None:
        malformed_responses = (
            response({"code": -1, "msg": "bad request"}),
            response([kline()[:11]]),
            response([kline() + ["unexpected"]]),
            response([{"open_time": OPEN_TIME_MS}]),
        )
        for payload in malformed_responses:
            with self.subTest(payload=payload):
                adapter, _ = self.adapter(payload)

                with self.assertRaisesRegex(BinanceMarketDataError, "array|12-field"):
                    adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

    def test_rejects_non_integer_and_boolean_provider_timestamps(self) -> None:
        invalid_rows = (
            kline(open_time="1767225600000"),
            kline(close_time=1767225659999.0),
            kline(open_time=True),
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                adapter, _ = self.adapter(response([row]))

                with self.assertRaisesRegex(BinanceMarketDataError, "timestamp"):
                    adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

    def test_rejects_non_string_or_malformed_provider_decimals(self) -> None:
        invalid_rows = (
            kline(open_price=100),
            kline(high="not-a-decimal"),
            kline(volume="NaN"),
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                adapter, _ = self.adapter(response([row]))

                with self.assertRaisesRegex(BinanceMarketDataError, "decimal"):
                    adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

    def test_rejects_non_integer_and_boolean_trade_counts(self) -> None:
        for trade_count in ("10", 10.0, True):
            with self.subTest(trade_count=trade_count):
                adapter, _ = self.adapter(response([kline(trade_count=trade_count)]))

                with self.assertRaisesRegex(BinanceMarketDataError, "trade count"):
                    adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

    def test_wraps_canonical_ohlcv_and_timestamp_violations(self) -> None:
        invalid_rows = (
            kline(high="101"),
            kline(volume="-1"),
            kline(close_time=OPEN_TIME_MS),
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                adapter, _ = self.adapter(response([row]))

                with self.assertRaisesRegex(BinanceMarketDataError, "canonical candle"):
                    adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

    def test_rejects_invalid_json(self) -> None:
        adapter, _ = self.adapter(b"not-json")

        with self.assertRaisesRegex(BinanceMarketDataError, "invalid JSON"):
            adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

    def test_wraps_http_network_and_timeout_failures(self) -> None:
        failures = (
            (HTTPError("https://data-api.binance.vision/api/v3/klines", 503, "unavailable", None, None), "HTTP"),
            (URLError("offline"), "network"),
            (socket.timeout("timed out"), "timed out"),
        )
        for failure, message in failures:
            with self.subTest(failure=failure):
                adapter, _ = self.adapter(failure)

                with self.assertRaisesRegex(BinanceMarketDataError, message):
                    adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

    def test_http_error_preserves_status_and_only_valid_retry_after_seconds(self) -> None:
        valid_headers = Message()
        valid_headers["Retry-After"] = "30"
        adapter, _ = self.adapter(
            HTTPError(
                "https://data-api.binance.vision/api/v3/klines",
                429,
                "too many requests",
                valid_headers,
                None,
            )
        )

        with self.assertRaises(BinanceMarketDataError) as captured:
            adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

        self.assertEqual(captured.exception.http_status, 429)
        self.assertEqual(captured.exception.retry_after_seconds, 30)
        for retry_after in (None, "not-a-delay", "-1"):
            with self.subTest(retry_after=retry_after):
                headers = Message()
                if retry_after is not None:
                    headers["Retry-After"] = retry_after
                adapter, _ = self.adapter(
                    HTTPError(
                        "https://data-api.binance.vision/api/v3/klines",
                        429,
                        "too many requests",
                        headers,
                        None,
                    )
                )

                with self.assertRaises(BinanceMarketDataError) as captured:
                    adapter.fetch_klines(symbol="BTCUSDT", interval="1m")

                self.assertEqual(captured.exception.http_status, 429)
                self.assertIsNone(captured.exception.retry_after_seconds)
