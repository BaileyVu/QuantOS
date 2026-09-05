"""Tests for checksum-verified Binance Spot daily kline archives."""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO, StringIO
import socket
from urllib.error import HTTPError, URLError
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from quantos.domain.market_data import Candle, ValidatedCandleSequence
from quantos.infrastructure.binance import (
    BinanceDailyArchiveError,
    BinanceSpotDailyArchiveAdapter,
)

UTC = timezone.utc
MILLISECOND_DATE = date(2024, 12, 31)
MICROSECOND_DATE = date(2025, 1, 1)
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def archive_filename(symbol: str, archive_date: date) -> str:
    return f"{symbol}-1m-{archive_date.isoformat()}.zip"


def csv_filename(symbol: str, archive_date: date) -> str:
    return f"{symbol}-1m-{archive_date.isoformat()}.csv"


def timestamp_text(value: datetime, unit: str) -> str:
    delta = value - UNIX_EPOCH
    microseconds = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    )
    if unit == "milliseconds":
        return str(microseconds // 1_000)
    if unit == "microseconds":
        return str(microseconds)
    raise AssertionError(f"unsupported fixture timestamp unit: {unit}")


def archive_row(
    archive_date: date,
    *,
    minute: int = 0,
    timestamp_unit: str | None = None,
    open_price: str = "100.12345678",
    high: str = "103.00000000",
    low: str = "99.00000000",
    close: str = "102.87654321",
    volume: str = "1.50000000",
    quote_volume: str = "151.50000000",
    trade_count: str = "10",
) -> list[str]:
    unit = timestamp_unit or (
        "milliseconds" if archive_date < MICROSECOND_DATE else "microseconds"
    )
    open_time = datetime.combine(archive_date, time.min, tzinfo=UTC) + timedelta(
        minutes=minute
    )
    close_precision = (
        timedelta(milliseconds=59_999)
        if unit == "milliseconds"
        else timedelta(microseconds=59_999_999)
    )
    return [
        timestamp_text(open_time, unit),
        open_price,
        high,
        low,
        close,
        volume,
        timestamp_text(open_time + close_precision, unit),
        quote_volume,
        trade_count,
        "0.75000000",
        "75.75000000",
        "0",
    ]


def csv_payload(rows: list[list[str]]) -> bytes:
    stream = StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerows(rows)
    return stream.getvalue().encode("utf-8")


def zip_payload(entries: list[tuple[str, bytes]]) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for filename, payload in entries:
            archive.writestr(filename, payload)
    return stream.getvalue()


def valid_zip(
    *,
    symbol: str = "BTCUSDT",
    archive_date: date = MICROSECOND_DATE,
    csv_bytes: bytes | None = None,
) -> bytes:
    payload = csv_bytes
    if payload is None:
        payload = csv_payload([archive_row(archive_date)])
    return zip_payload([(csv_filename(symbol, archive_date), payload)])


def corrupt_first_member_data(archive_bytes: bytes) -> bytes:
    with ZipFile(BytesIO(archive_bytes)) as archive:
        member = archive.infolist()[0]
    corrupted = bytearray(archive_bytes)
    offset = member.header_offset
    filename_length = int.from_bytes(corrupted[offset + 26 : offset + 28], "little")
    extra_length = int.from_bytes(corrupted[offset + 28 : offset + 30], "little")
    data_offset = offset + 30 + filename_length + extra_length
    corrupted[data_offset] ^= 0xFF
    return bytes(corrupted)


def mark_first_member_encrypted(archive_bytes: bytes) -> bytes:
    marked = bytearray(archive_bytes)
    central_header_offset = marked.find(b"PK\x01\x02")
    if central_header_offset < 0:
        raise AssertionError("fixture ZIP has no central directory header")
    flags_offset = central_header_offset + 8
    flags = int.from_bytes(marked[flags_offset : flags_offset + 2], "little") | 0x1
    marked[flags_offset : flags_offset + 2] = flags.to_bytes(2, "little")
    return bytes(marked)


def checksum_payload(
    archive_bytes: bytes,
    *,
    symbol: str = "BTCUSDT",
    archive_date: date = MICROSECOND_DATE,
    digest: str | None = None,
    recorded_filename: str | None = None,
) -> bytes:
    expected_digest = digest or sha256(archive_bytes).hexdigest()
    filename = recorded_filename or archive_filename(symbol, archive_date)
    return f"{expected_digest}  {filename}\n".encode("ascii")


class RecordingHttpGet:
    def __init__(self, *responses: bytes | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout_seconds: float) -> bytes:
        self.calls.append((url, timeout_seconds))
        if not self._responses:
            raise AssertionError("unexpected additional archive HTTP request")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BinanceSpotDailyArchiveAdapterTests(unittest.TestCase):
    def adapter(
        self,
        checksum_bytes: bytes,
        archive_bytes: bytes,
    ) -> tuple[BinanceSpotDailyArchiveAdapter, RecordingHttpGet]:
        http_get = RecordingHttpGet(checksum_bytes, archive_bytes)
        return BinanceSpotDailyArchiveAdapter(http_get=http_get), http_get

    def fetch(
        self,
        adapter: BinanceSpotDailyArchiveAdapter,
        *,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        archive_date: date = MICROSECOND_DATE,
    ) -> tuple[Candle, ...]:
        return adapter.fetch_daily_klines(
            symbol=symbol,
            interval=interval,
            archive_date=archive_date,
        )

    def adapter_for_csv(
        self,
        csv_bytes: bytes,
        *,
        symbol: str = "BTCUSDT",
        archive_date: date = MICROSECOND_DATE,
    ) -> tuple[BinanceSpotDailyArchiveAdapter, RecordingHttpGet, bytes, bytes]:
        archive_bytes = valid_zip(
            symbol=symbol, archive_date=archive_date, csv_bytes=csv_bytes
        )
        checksum_bytes = checksum_payload(
            archive_bytes, symbol=symbol, archive_date=archive_date
        )
        adapter, http_get = self.adapter(checksum_bytes, archive_bytes)
        return adapter, http_get, checksum_bytes, archive_bytes

    def test_constructs_exact_fixed_daily_urls_for_both_symbols(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                archive_bytes = valid_zip(symbol=symbol)
                adapter, http_get = self.adapter(
                    checksum_payload(archive_bytes, symbol=symbol), archive_bytes
                )

                result = self.fetch(adapter, symbol=symbol)

                expected_base = (
                    "https://data.binance.vision/data/spot/daily/klines/"
                    f"{symbol}/1m/{symbol}-1m-2025-01-01.zip"
                )
                self.assertEqual(
                    http_get.calls,
                    [(f"{expected_base}.CHECKSUM", 10.0), (expected_base, 10.0)],
                )
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0].symbol, symbol)

    def test_does_not_accept_a_base_url_override(self) -> None:
        with self.assertRaisesRegex(TypeError, "base_url"):
            BinanceSpotDailyArchiveAdapter(base_url="https://example.test")

    def test_rejects_invalid_requests_before_http(self) -> None:
        invalid_requests = (
            ({"symbol": "SOLUSDT"}, "symbol"),
            ({"interval": "5m"}, "interval"),
            ({"archive_date": datetime(2025, 1, 1, tzinfo=UTC)}, "date"),
            ({"archive_date": "2025-01-01"}, "date"),
            ({"archive_date": True}, "date"),
        )
        for overrides, message in invalid_requests:
            with self.subTest(overrides=overrides):
                http_get = RecordingHttpGet()
                adapter = BinanceSpotDailyArchiveAdapter(http_get=http_get)

                with self.assertRaisesRegex(ValueError, message):
                    self.fetch(adapter, **overrides)

                self.assertEqual(http_get.calls, [])

    def test_rejects_custom_input_subclasses_before_http(self) -> None:
        class InjectedString(str):
            def __format__(self, format_spec: str) -> str:
                return "../../unexpected?host=example.test#"

        class InjectedDate(date):
            def isoformat(self) -> str:
                return "2025-01-01.zip?host=example.test#"

        invalid_requests = (
            {"symbol": InjectedString("BTCUSDT")},
            {"interval": InjectedString("1m")},
            {"archive_date": InjectedDate(2025, 1, 1)},
        )
        for overrides in invalid_requests:
            with self.subTest(overrides=overrides):
                http_get = RecordingHttpGet()
                adapter = BinanceSpotDailyArchiveAdapter(http_get=http_get)

                with self.assertRaises(ValueError):
                    self.fetch(adapter, **overrides)

                self.assertEqual(http_get.calls, [])

    def test_accepts_lowercase_uppercase_whitespace_and_star_checksum_forms(self) -> None:
        archive_bytes = valid_zip()
        digest = sha256(archive_bytes).hexdigest()
        filename = archive_filename("BTCUSDT", MICROSECOND_DATE)
        checksum_forms = (
            f"{digest} {filename}".encode("ascii"),
            f"{digest} {filename}\n".encode("ascii"),
            f"{digest} {filename}\r\n".encode("ascii"),
            f"{digest}  {filename}\n".encode("ascii"),
            f"{digest}\t{filename}\n".encode("ascii"),
            f"  {digest.upper()}\t*{filename}  \n".encode("ascii"),
        )
        for checksum_bytes in checksum_forms:
            with self.subTest(checksum_bytes=checksum_bytes):
                adapter, http_get = self.adapter(checksum_bytes, archive_bytes)

                self.assertEqual(len(self.fetch(adapter)), 1)
                self.assertEqual(len(http_get.calls), 2)

    def test_rejects_malformed_checksum_before_requesting_zip(self) -> None:
        archive_bytes = valid_zip()
        digest = sha256(archive_bytes).hexdigest()
        filename = archive_filename("BTCUSDT", MICROSECOND_DATE)
        malformed_checksums = (
            b"",
            b"\xff",
            digest.encode("ascii"),
            f"{digest} wrong.zip\n".encode("ascii"),
            f"{digest} ../{filename}\n".encode("ascii"),
            f"{'a' * 63} {filename}\n".encode("ascii"),
            f"{'a' * 65} {filename}\n".encode("ascii"),
            f"{'g' * 64} {filename}\n".encode("ascii"),
            f"{digest} {filename} extra\n".encode("ascii"),
            f"{digest} {filename}\n{digest} {filename}\n".encode("ascii"),
        )
        for checksum_bytes in malformed_checksums:
            with self.subTest(checksum_bytes=checksum_bytes[:80]):
                http_get = RecordingHttpGet(checksum_bytes)
                adapter = BinanceSpotDailyArchiveAdapter(http_get=http_get)

                with self.assertRaises(BinanceDailyArchiveError):
                    self.fetch(adapter)

                self.assertEqual(len(http_get.calls), 1)

    def test_rejects_unsupported_checksum_control_characters_before_zip(self) -> None:
        archive_bytes = valid_zip()
        digest = sha256(archive_bytes).hexdigest()
        filename = archive_filename("BTCUSDT", MICROSECOND_DATE)
        malformed_checksums = (
            f"{digest} {filename}\v".encode("ascii"),
            f"{digest}\v{filename}".encode("ascii"),
            f"{digest} {filename}\f".encode("ascii"),
            f"{digest}\f{filename}".encode("ascii"),
            f"{digest} {filename}\r".encode("ascii"),
            f"{digest}\0{filename}".encode("ascii"),
        )
        for checksum_bytes in malformed_checksums:
            with self.subTest(checksum_bytes=checksum_bytes):
                http_get = RecordingHttpGet(checksum_bytes)
                adapter = BinanceSpotDailyArchiveAdapter(http_get=http_get)

                with self.assertRaises(BinanceDailyArchiveError):
                    self.fetch(adapter)

                self.assertEqual(len(http_get.calls), 1)

    def test_checksum_mismatch_prevents_zip_parsing(self) -> None:
        invalid_zip = b"not a ZIP or CSV"
        checksum_bytes = checksum_payload(invalid_zip, digest="0" * 64)
        adapter, http_get = self.adapter(checksum_bytes, invalid_zip)

        with self.assertRaisesRegex(BinanceDailyArchiveError, "checksum mismatch"):
            self.fetch(adapter)

        self.assertEqual(len(http_get.calls), 2)

    def test_rejects_corrupt_or_unexpected_zip_structures(self) -> None:
        expected_csv = csv_filename("BTCUSDT", MICROSECOND_DATE)
        valid_csv = csv_payload([archive_row(MICROSECOND_DATE)])
        archives = (
            b"not a zip",
            corrupt_first_member_data(valid_zip()),
            mark_first_member_encrypted(valid_zip()),
            zip_payload([]),
            zip_payload([("wrong.csv", valid_csv)]),
            zip_payload([(expected_csv, valid_csv), ("extra.csv", valid_csv)]),
            zip_payload([(f"nested/{expected_csv}", valid_csv)]),
            zip_payload([(f"../{expected_csv}", valid_csv)]),
        )
        for archive_bytes in archives:
            with self.subTest(archive_size=len(archive_bytes)):
                adapter, http_get = self.adapter(
                    checksum_payload(archive_bytes), archive_bytes
                )

                with self.assertRaises(BinanceDailyArchiveError):
                    self.fetch(adapter)

                self.assertEqual(len(http_get.calls), 2)

    def test_reads_expected_member_in_memory_without_filesystem_extraction(self) -> None:
        archive_bytes = valid_zip()
        adapter, _ = self.adapter(checksum_payload(archive_bytes), archive_bytes)

        with (
            patch.object(ZipFile, "extract") as extract,
            patch.object(ZipFile, "extractall") as extractall,
        ):
            result = self.fetch(adapter)

        extract.assert_not_called()
        extractall.assert_not_called()
        self.assertEqual(len(result), 1)

    def test_rejects_wrong_field_counts_blank_rows_and_malformed_csv(self) -> None:
        row = archive_row(MICROSECOND_DATE)
        invalid_csv_payloads = (
            csv_payload([row[:11]]),
            csv_payload([row + ["unexpected"]]),
            b"\n",
            b'"unterminated',
        )
        for csv_bytes in invalid_csv_payloads:
            with self.subTest(csv_bytes=csv_bytes[:80]):
                adapter, _, _, _ = self.adapter_for_csv(csv_bytes)

                with self.assertRaises(BinanceDailyArchiveError):
                    self.fetch(adapter)

    def test_rejects_malformed_and_extreme_integer_tokens(self) -> None:
        invalid_tokens = ("", "-1", "+1", "1.5", "1e3", " 1", "1 ", "x", "9" * 5_000)
        for field_index, field_name in ((0, "timestamp"), (8, "trade count")):
            for token in invalid_tokens:
                with self.subTest(field=field_name, token=token[:20]):
                    row = archive_row(MICROSECOND_DATE)
                    row[field_index] = token
                    adapter, _, _, _ = self.adapter_for_csv(csv_payload([row]))

                    with self.assertRaises(BinanceDailyArchiveError):
                        self.fetch(adapter)

    def test_rejects_malformed_nonfinite_and_padded_decimal_tokens(self) -> None:
        invalid_tokens = ("", "not-decimal", "NaN", "Infinity", " 1.0", "1.0 ")
        for token in invalid_tokens:
            with self.subTest(token=token):
                row = archive_row(MICROSECOND_DATE)
                row[1] = token
                adapter, _, _, _ = self.adapter_for_csv(csv_payload([row]))

                with self.assertRaises(BinanceDailyArchiveError):
                    self.fetch(adapter)

    def test_rejects_invalid_utf8_csv(self) -> None:
        adapter, _, _, _ = self.adapter_for_csv(b"\xff")

        with self.assertRaisesRegex(BinanceDailyArchiveError, "UTF-8"):
            self.fetch(adapter)

    def test_preserves_provider_order_duplicates_gaps_and_fixture_bytes(self) -> None:
        rows = [
            archive_row(MICROSECOND_DATE, minute=2),
            archive_row(MICROSECOND_DATE, minute=0),
            archive_row(MICROSECOND_DATE, minute=0, volume="2.00000000"),
        ]
        csv_bytes = csv_payload(rows)
        adapter, _, checksum_bytes, archive_bytes = self.adapter_for_csv(csv_bytes)
        original_bytes = (checksum_bytes, archive_bytes, csv_bytes)

        result = self.fetch(adapter)

        self.assertEqual(
            [candle.open_time for candle in result],
            [
                datetime(2025, 1, 1, 0, 2, tzinfo=UTC),
                datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            ],
        )
        self.assertEqual(result[2].volume.as_tuple(), Decimal("2.00000000").as_tuple())
        self.assertEqual((checksum_bytes, archive_bytes, csv_bytes), original_bytes)
        self.assertIsInstance(result, tuple)
        self.assertNotIsInstance(result, ValidatedCandleSequence)

    def test_empty_valid_csv_returns_an_empty_tuple(self) -> None:
        adapter, _, _, _ = self.adapter_for_csv(b"")

        result = self.fetch(adapter)

        self.assertEqual(result, ())
        self.assertIsInstance(result, tuple)

    def test_normalizes_timestamp_eras_exactly_at_2025_boundary(self) -> None:
        scenarios = (
            (
                MILLISECOND_DATE,
                datetime(2024, 12, 31, tzinfo=UTC),
                datetime(2024, 12, 31, 0, 0, 59, 999_000, tzinfo=UTC),
            ),
            (
                MICROSECOND_DATE,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 1, 0, 0, 59, 999_999, tzinfo=UTC),
            ),
        )
        for archive_date, expected_open, expected_close in scenarios:
            with self.subTest(archive_date=archive_date):
                csv_bytes = csv_payload([archive_row(archive_date)])
                adapter, _, _, _ = self.adapter_for_csv(
                    csv_bytes, archive_date=archive_date
                )

                result = self.fetch(adapter, archive_date=archive_date)

                self.assertEqual(len(result), 1)
                candle = result[0]
                self.assertEqual(candle.open_time, expected_open)
                self.assertEqual(candle.close_time, expected_close)
                self.assertIs(candle.open_time.tzinfo, UTC)
                self.assertIs(candle.close_time.tzinfo, UTC)
                self.assertEqual(candle.open.as_tuple(), Decimal("100.12345678").as_tuple())
                self.assertEqual(candle.high.as_tuple(), Decimal("103.00000000").as_tuple())
                self.assertEqual(candle.low.as_tuple(), Decimal("99.00000000").as_tuple())
                self.assertEqual(candle.close.as_tuple(), Decimal("102.87654321").as_tuple())
                self.assertEqual(candle.volume.as_tuple(), Decimal("1.50000000").as_tuple())
                self.assertEqual(
                    candle.quote_volume.as_tuple(), Decimal("151.50000000").as_tuple()
                )
                self.assertEqual(candle.trade_count, 10)

    def test_rejects_timestamps_encoded_for_the_wrong_archive_era(self) -> None:
        scenarios = (
            (MILLISECOND_DATE, "microseconds"),
            (MICROSECOND_DATE, "milliseconds"),
        )
        for archive_date, wrong_unit in scenarios:
            with self.subTest(archive_date=archive_date, wrong_unit=wrong_unit):
                csv_bytes = csv_payload(
                    [archive_row(archive_date, timestamp_unit=wrong_unit)]
                )
                adapter, _, _, _ = self.adapter_for_csv(
                    csv_bytes, archive_date=archive_date
                )

                with self.assertRaises(BinanceDailyArchiveError):
                    self.fetch(adapter, archive_date=archive_date)

    def test_wraps_canonical_candle_failures(self) -> None:
        invalid_rows: list[list[str]] = []
        invalid_high = archive_row(MICROSECOND_DATE, high="101")
        invalid_rows.append(invalid_high)
        invalid_rows.append(archive_row(MICROSECOND_DATE, volume="-1"))
        invalid_close_time = archive_row(MICROSECOND_DATE)
        invalid_close_time[6] = invalid_close_time[0]
        invalid_rows.append(invalid_close_time)
        invalid_rows.append(archive_row(MICROSECOND_DATE, quote_volume="NaN"))
        for row in invalid_rows:
            with self.subTest(row=row):
                adapter, _, _, _ = self.adapter_for_csv(csv_payload([row]))

                with self.assertRaises(BinanceDailyArchiveError):
                    self.fetch(adapter)

    def test_wraps_checksum_and_zip_transport_failures_without_retrying(self) -> None:
        failures = (
            HTTPError("https://data.binance.vision", 503, "unavailable", None, None),
            URLError("offline"),
            socket.timeout("timed out"),
        )
        valid_checksum = checksum_payload(b"unused", digest="0" * 64)
        for stage in ("checksum", "ZIP"):
            for failure in failures:
                with self.subTest(stage=stage, failure=type(failure).__name__):
                    responses: tuple[bytes | Exception, ...]
                    if stage == "checksum":
                        responses = (failure,)
                        expected_calls = 1
                    else:
                        responses = (valid_checksum, failure)
                        expected_calls = 2
                    http_get = RecordingHttpGet(*responses)
                    adapter = BinanceSpotDailyArchiveAdapter(http_get=http_get)

                    with self.assertRaises(BinanceDailyArchiveError) as captured:
                        self.fetch(adapter)

                    self.assertEqual(len(http_get.calls), expected_calls)
                    if isinstance(failure, HTTPError):
                        self.assertEqual(captured.exception.http_status, 503)
