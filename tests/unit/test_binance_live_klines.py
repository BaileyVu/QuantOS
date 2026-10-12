"""Offline strict-schema and causality checks for live Binance klines."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, FloatOperation, localcontext
import json
import unittest
from unittest.mock import patch

from quantos.domain.market_data import Candle, MarketEvent
from quantos.infrastructure.binance import BinanceLiveMarketDataError
from quantos.infrastructure.binance.live_klines import _ServerShutdown, normalize_live_kline

OPEN_US = 1_735_689_600_000_000
STREAMS = frozenset({"btcusdt@kline_1m", "ethusdt@kline_1m"})


def combined_kline(symbol: str = "BTCUSDT", minute: int = 0, *, closed: bool = True) -> dict:
    start = OPEN_US + minute * 60_000_000
    return {
        "stream": f"{symbol.lower()}@kline_1m",
        "data": {
            "e": "kline", "E": start + 60_123_456, "s": symbol,
            "k": {
                "t": start, "T": start + 59_999_999, "s": symbol, "i": "1m",
                "o": "100.123456789012345678", "h": "105.00000000",
                "l": "99.00000000", "c": "102.123456789012345678",
                "v": "12.123456789012345678", "q": "1234.567890123456789012",
                "n": 17, "x": closed,
                "f": 1, "L": 17, "V": "1.00", "Q": "102.00", "B": "0",
            },
        },
    }


def encode(payload: dict) -> str:
    return json.dumps(payload)


def normalize(payload: dict) -> MarketEvent | None:
    return normalize_live_kline(encode(payload), expected_streams=STREAMS)


class LiveKlineNormalizationTests(unittest.TestCase):
    def assert_invalid(self, payload: dict) -> None:
        with self.assertRaises(BinanceLiveMarketDataError):
            normalize(payload)

    def test_closed_btc_produces_existing_canonical_contracts(self) -> None:
        event = normalize(combined_kline())
        self.assertIs(type(event), MarketEvent)
        self.assertIs(type(event.candle), Candle)
        self.assertEqual((event.candle.symbol, event.candle.interval), ("BTCUSDT", "1m"))
        self.assertEqual(event.candle.trade_count, 17)
        self.assertTrue(event.candle.is_complete_at(event.timestamp))

    def test_closed_eth_produces_existing_canonical_contracts(self) -> None:
        event = normalize(combined_kline("ETHUSDT"))
        self.assertIs(type(event), MarketEvent)
        self.assertEqual(event.candle.symbol, "ETHUSDT")

    def test_all_market_values_preserve_exact_decimal_strings_without_float(self) -> None:
        payload = combined_kline()
        with localcontext() as context:
            context.traps[FloatOperation] = True
            event = normalize(payload)
        for field, key in (
            ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"),
            ("volume", "v"), ("quote_volume", "q"),
        ):
            with self.subTest(field=field):
                actual = getattr(event.candle, field)
                self.assertIs(type(actual), Decimal)
                self.assertEqual(actual.as_tuple(), Decimal(payload["data"]["k"][key]).as_tuple())

    def test_exact_microsecond_utc_open_close_and_provider_event_time(self) -> None:
        event = normalize(combined_kline())
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(event.candle.open_time, start)
        self.assertEqual(event.candle.close_time, start + timedelta(microseconds=59_999_999))
        self.assertEqual(event.timestamp, start + timedelta(microseconds=60_123_456))
        for value in (event.candle.open_time, event.candle.close_time, event.timestamp):
            self.assertIs(type(value), datetime)
            self.assertIs(value.tzinfo, timezone.utc)

    def test_microseconds_above_float_exact_integer_range_are_preserved(self) -> None:
        payload = combined_kline()
        start = datetime(2256, 1, 1, tzinfo=timezone.utc)
        delta = start - datetime(1970, 1, 1, tzinfo=timezone.utc)
        start_us = (delta.days * 86_400 + delta.seconds) * 1_000_000
        self.assertGreater(start_us, 2**53)
        payload["data"]["k"].update(t=start_us, T=start_us + 59_999_999)
        payload["data"]["E"] = start_us + 60_000_001
        event = normalize(payload)
        self.assertEqual(event.candle.close_time, start + timedelta(microseconds=59_999_999))
        self.assertEqual(event.timestamp, start + timedelta(microseconds=60_000_001))

    def test_supplied_close_time_is_not_manufactured_or_rewritten(self) -> None:
        payload = combined_kline()
        payload["data"]["k"]["T"] = OPEN_US + 59_123_456
        self.assertEqual(normalize(payload).candle.close_time.microsecond, 123456)

    def test_partial_update_never_constructs_candle_or_market_event(self) -> None:
        payload = combined_kline(closed=False)
        payload["data"]["E"] = OPEN_US + 5_000_000
        with patch("quantos.infrastructure.binance.live_klines.Candle") as candle:
            with patch("quantos.infrastructure.binance.live_klines.MarketEvent") as event:
                self.assertIsNone(normalize(payload))
        candle.assert_not_called()
        event.assert_not_called()

    def test_partial_stays_suppressed_even_when_event_time_is_after_close(self) -> None:
        self.assertIsNone(normalize(combined_kline(closed=False)))

    def test_valid_utf8_bytes_are_supported(self) -> None:
        payload = combined_kline()
        self.assertEqual(
            normalize_live_kline(encode(payload).encode(), expected_streams=STREAMS),
            normalize(payload),
        )

    def test_malformed_json_and_non_text_payload_fail(self) -> None:
        for message in ("{", "not-json", b"\xff", {}, None, 123):
            with self.subTest(message=message), self.assertRaises(BinanceLiveMarketDataError):
                normalize_live_kline(message, expected_streams=STREAMS)

    def test_non_object_json_and_invalid_nested_objects_fail(self) -> None:
        for value in ([], None, "text", 12, True):
            with self.subTest(wrapper=value), self.assertRaises(BinanceLiveMarketDataError):
                normalize_live_kline(json.dumps(value), expected_streams=STREAMS)
            for field in ("data", "k"):
                payload = combined_kline()
                target = payload if field == "data" else payload["data"]
                target[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_invalid(payload)

    def test_every_required_field_must_exist(self) -> None:
        for level, fields in (("wrapper", ("stream", "data")), ("data", ("e", "E", "s", "k")),
                              ("k", ("t", "T", "s", "i", "o", "h", "l", "c", "v", "q", "n", "x"))):
            for field in fields:
                payload = combined_kline()
                target = payload if level == "wrapper" else payload["data"]
                if level == "k":
                    target = target["k"]
                del target[field]
                with self.subTest(level=level, field=field):
                    self.assert_invalid(payload)

    def test_duplicate_json_keys_fail_instead_of_selecting_last_value(self) -> None:
        text = encode(combined_kline()).replace('"x": true', '"x": false, "x": true')
        with self.assertRaises(BinanceLiveMarketDataError):
            normalize_live_kline(text, expected_streams=STREAMS)

    def test_non_standard_json_constants_fail_even_in_unused_fields(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity"):
            text = encode(combined_kline()).replace('"B": "0"', f'"B": {value}')
            with self.subTest(value=value), self.assertRaises(BinanceLiveMarketDataError):
                normalize_live_kline(text, expected_streams=STREAMS)

    def test_wrong_event_type_fails(self) -> None:
        for value in ("trade", "serverShutdown", "", True, None, 123):
            payload = combined_kline()
            payload["data"]["e"] = value
            self.assert_invalid(payload)

    def test_wrong_outer_symbol_and_inner_symbol_mismatch_fail(self) -> None:
        for level in ("outer", "inner"):
            for value in ("ETHUSDT", "btcusdt", "BTCUSD", None, 123, True):
                payload = combined_kline()
                target = payload["data"] if level == "outer" else payload["data"]["k"]
                target["s"] = value
                with self.subTest(level=level, value=value):
                    self.assert_invalid(payload)

    def test_unsupported_symbol_fails_even_with_matching_envelope(self) -> None:
        self.assert_invalid(combined_kline("BNBUSDT"))

    def test_wrong_interval_or_interval_type_fails(self) -> None:
        for value in ("5m", "1M", 1, True, None):
            payload = combined_kline()
            payload["data"]["k"]["i"] = value
            self.assert_invalid(payload)

    def test_unexpected_streams_and_timezone_variants_fail(self) -> None:
        for value in ("ethusdt@kline_1m", "btcusdt@kline_1m@+08:00", "btcusdt@trade",
                      "BTCUSDT@kline_1m", "btcusdt@kline_5m", "", None, 123, True):
            payload = combined_kline()
            payload["stream"] = value
            with self.subTest(stream=value):
                self.assert_invalid(payload)

    def test_supported_but_unsubscribed_symbol_fails(self) -> None:
        with self.assertRaises(BinanceLiveMarketDataError):
            normalize_live_kline(encode(combined_kline("ETHUSDT")),
                                 expected_streams=frozenset({"btcusdt@kline_1m"}))

    def test_timestamp_strings_floats_bools_and_null_fail_for_all_timestamps(self) -> None:
        for field in ("E", "t", "T"):
            for value in (str(OPEN_US), float(OPEN_US), True, False, None, []):
                payload = combined_kline()
                target = payload["data"] if field == "E" else payload["data"]["k"]
                target[field] = value
                with self.subTest(field=field, value=value):
                    self.assert_invalid(payload)

    def test_timestamp_overflow_fails_as_provider_error(self) -> None:
        for field in ("E", "t", "T"):
            for value in (10**30, -(10**30)):
                payload = combined_kline()
                target = payload["data"] if field == "E" else payload["data"]["k"]
                target[field] = value
                self.assert_invalid(payload)

    def test_closed_flag_is_exact_bool(self) -> None:
        for value in (0, 1, "true", "false", None, [], {}):
            payload = combined_kline()
            payload["data"]["k"]["x"] = value
            self.assert_invalid(payload)

    def test_market_values_require_strict_decimal_strings(self) -> None:
        for field in "ohlcvq":
            for value in (100, 100.1, True, None, "NaN", "sNaN", "Infinity", "1_00",
                          " 100 ", "1e2", "", "1.2.3", "١٠٠"):
                payload = combined_kline()
                payload["data"]["k"][field] = value
                with self.subTest(field=field, value=value):
                    self.assert_invalid(payload)

    def test_trade_count_requires_non_negative_integer_not_bool(self) -> None:
        for value in (True, False, 17.0, "17", -1, None):
            payload = combined_kline()
            payload["data"]["k"]["n"] = value
            self.assert_invalid(payload)

    def test_invalid_ohlc_and_negative_market_values_fail(self) -> None:
        for field, value in (("h", "100"), ("l", "103"), ("o", "106"), ("c", "98"),
                             ("o", "-1"), ("v", "-1"), ("q", "-1")):
            payload = combined_kline()
            payload["data"]["k"][field] = value
            with self.subTest(field=field, value=value):
                self.assert_invalid(payload)

    def test_zero_volumes_and_trade_count_are_valid(self) -> None:
        payload = combined_kline()
        payload["data"]["k"].update(v="0.0000", q="0", n=0)
        self.assertEqual(normalize(payload).candle.volume, Decimal("0"))

    def test_open_time_must_be_exact_minute(self) -> None:
        for increment in (1, 1_000_000):
            payload = combined_kline()
            payload["data"]["k"]["t"] += increment
            self.assert_invalid(payload)

    def test_close_time_must_be_strictly_after_open(self) -> None:
        for value in (OPEN_US, OPEN_US - 1):
            payload = combined_kline()
            payload["data"]["k"]["T"] = value
            self.assert_invalid(payload)

    def test_event_before_close_is_rejected_but_equality_is_valid(self) -> None:
        payload = combined_kline()
        payload["data"]["E"] = payload["data"]["k"]["T"] - 1
        self.assert_invalid(payload)
        payload["data"]["E"] += 1
        event = normalize(payload)
        self.assertEqual(event.timestamp, event.candle.close_time)

    def test_malformed_partial_schema_is_not_silently_ignored(self) -> None:
        payload = combined_kline(closed=False)
        del payload["data"]["k"]["c"]
        self.assert_invalid(payload)

    def test_documented_combined_shutdown_is_distinct_from_market_event(self) -> None:
        with self.assertRaises(_ServerShutdown):
            normalize({"stream": "!serverShutdown", "data": {"e": "serverShutdown", "E": OPEN_US}})

    def test_raw_or_malformed_shutdown_does_not_bypass_envelope_validation(self) -> None:
        payloads = (
            {"e": "serverShutdown", "E": OPEN_US},
            {"stream": "!serverShutdown", "data": {"e": "kline", "E": OPEN_US}},
            {"stream": "!serverShutdown", "data": {"e": "serverShutdown", "E": True}},
            {"stream": "!serverShutdown", "data": {"e": "serverShutdown"}},
            {"stream": "!serverShutdown", "data": {"e": "serverShutdown", "E": OPEN_US, "k": {}}},
        )
        for payload in payloads:
            self.assert_invalid(payload)
