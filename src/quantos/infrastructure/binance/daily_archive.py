"""Checksum-verified Binance Spot daily kline archive adapter."""

from __future__ import annotations

from collections.abc import Callable
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
from io import BytesIO, StringIO
import math
import socket
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile
import zlib

from quantos.domain.common import V1_INTERVAL, require_v1_symbol
from quantos.domain.market_data import Candle
from quantos.infrastructure.binance.klines import BinanceMarketDataError

BINANCE_ARCHIVE_DATA_URL = "https://data.binance.vision"
_MICROSECOND_ERA_START = date(2025, 1, 1)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_HTTP_GET = Callable[[str, float], bytes]


class BinanceDailyArchiveError(BinanceMarketDataError):
    """Raised when a Binance daily archive cannot be safely normalized."""


def _default_http_get(url: str, timeout_seconds: float) -> bytes:
    """Fetch one public archive resource with a finite timeout."""
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed HTTPS host
        return response.read()


def _archive_filenames(symbol: str, archive_date: date) -> tuple[str, str]:
    stem = f"{symbol}-{V1_INTERVAL}-{archive_date.isoformat()}"
    return f"{stem}.zip", f"{stem}.csv"


def _archive_url(filename: str, symbol: str) -> str:
    return (
        f"{BINANCE_ARCHIVE_DATA_URL}/data/spot/daily/klines/"
        f"{symbol}/{V1_INTERVAL}/{filename}"
    )


def _parse_checksum(payload: bytes, *, expected_filename: str) -> str:
    if not isinstance(payload, bytes):
        raise BinanceDailyArchiveError(
            f"checksum for {expected_filename} must be returned as bytes"
        )
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise BinanceDailyArchiveError(
            f"checksum for {expected_filename} is not valid ASCII"
        ) from error

    if text.endswith("\r\n"):
        record = text[:-2]
    elif text.endswith("\n"):
        record = text[:-1]
    else:
        record = text
    if any(
        character in "\r\n"
        or (ord(character) < 32 and character != "\t")
        or ord(character) == 127
        for character in record
    ):
        raise BinanceDailyArchiveError(
            f"checksum for {expected_filename} must contain exactly one record"
        )
    record = record.strip(" \t")
    separator_index = next(
        (index for index, character in enumerate(record) if character in " \t"),
        None,
    )
    if separator_index is None:
        raise BinanceDailyArchiveError(
            f"checksum for {expected_filename} must contain one digest and filename"
        )
    digest = record[:separator_index]
    filename_start = separator_index
    while filename_start < len(record) and record[filename_start] in " \t":
        filename_start += 1
    recorded_filename = record[filename_start:]
    if not recorded_filename or any(
        character in " \t" for character in recorded_filename
    ):
        raise BinanceDailyArchiveError(
            f"checksum for {expected_filename} must contain one digest and filename"
        )
    if recorded_filename.startswith("*"):
        recorded_filename = recorded_filename[1:]
    if recorded_filename != expected_filename:
        raise BinanceDailyArchiveError(
            f"checksum filename does not match {expected_filename}"
        )
    if len(digest) != 64 or not all(
        character in "0123456789abcdefABCDEF" for character in digest
    ):
        raise BinanceDailyArchiveError(
            f"checksum for {expected_filename} is not a valid SHA-256 digest"
        )
    return digest.lower()


def _parse_unsigned_integer(value: str, *, row_number: int, field_name: str) -> int:
    if not value or not all("0" <= character <= "9" for character in value):
        raise BinanceDailyArchiveError(
            f"archive row {row_number} {field_name} must be an unsigned integer"
        )
    try:
        return int(value)
    except ValueError as error:
        raise BinanceDailyArchiveError(
            f"archive row {row_number} {field_name} is not representable as an integer"
        ) from error


def _parse_decimal(value: str, *, row_number: int, field_name: str) -> Decimal:
    if not value or value != value.strip():
        raise BinanceDailyArchiveError(
            f"archive row {row_number} {field_name} must be a decimal token"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise BinanceDailyArchiveError(
            f"archive row {row_number} {field_name} is not a valid decimal"
        ) from error
    if not parsed.is_finite():
        raise BinanceDailyArchiveError(
            f"archive row {row_number} {field_name} must be finite"
        )
    return parsed


def _archive_timestamp(
    value: str,
    *,
    archive_date: date,
    row_number: int,
    field_name: str,
) -> datetime:
    integer_value = _parse_unsigned_integer(
        value, row_number=row_number, field_name=field_name
    )
    try:
        if archive_date < _MICROSECOND_ERA_START:
            normalized = _UNIX_EPOCH + timedelta(milliseconds=integer_value)
        else:
            normalized = _UNIX_EPOCH + timedelta(microseconds=integer_value)
    except OverflowError as error:
        raise BinanceDailyArchiveError(
            f"archive row {row_number} {field_name} is outside the supported datetime range"
        ) from error
    if normalized.date() != archive_date:
        raise BinanceDailyArchiveError(
            f"archive row {row_number} {field_name} is inconsistent with the archive date"
        )
    return normalized


def _normalize_csv(
    payload: bytes,
    *,
    symbol: str,
    interval: str,
    archive_date: date,
    archive_filename: str,
) -> tuple[Candle, ...]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BinanceDailyArchiveError(f"{archive_filename} is not valid UTF-8") from error

    candles: list[Candle] = []
    try:
        rows = csv.reader(StringIO(text, newline=""), strict=True)
        for row_number, row in enumerate(rows, start=1):
            if len(row) != 12:
                raise BinanceDailyArchiveError(
                    f"archive row {row_number} must contain exactly 12 fields"
                )
            open_time = _archive_timestamp(
                row[0],
                archive_date=archive_date,
                row_number=row_number,
                field_name="open time",
            )
            close_time = _archive_timestamp(
                row[6],
                archive_date=archive_date,
                row_number=row_number,
                field_name="close time",
            )
            trade_count = _parse_unsigned_integer(
                row[8], row_number=row_number, field_name="trade count"
            )
            try:
                candle = Candle(
                    symbol=symbol,
                    interval=interval,
                    open_time=open_time,
                    close_time=close_time,
                    open=_parse_decimal(row[1], row_number=row_number, field_name="open"),
                    high=_parse_decimal(row[2], row_number=row_number, field_name="high"),
                    low=_parse_decimal(row[3], row_number=row_number, field_name="low"),
                    close=_parse_decimal(row[4], row_number=row_number, field_name="close"),
                    volume=_parse_decimal(row[5], row_number=row_number, field_name="volume"),
                    quote_volume=_parse_decimal(
                        row[7], row_number=row_number, field_name="quote volume"
                    ),
                    trade_count=trade_count,
                )
            except BinanceDailyArchiveError:
                raise
            except ValueError as error:
                raise BinanceDailyArchiveError(
                    f"archive row {row_number} violates canonical candle validation: {error}"
                ) from error
            candles.append(candle)
    except csv.Error as error:
        raise BinanceDailyArchiveError(f"{archive_filename} contains malformed CSV") from error
    return tuple(candles)


def _read_expected_csv(
    zip_bytes: bytes, *, expected_csv_filename: str, archive_filename: str
) -> bytes:
    try:
        with ZipFile(BytesIO(zip_bytes)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise BinanceDailyArchiveError(
                    f"{archive_filename} must contain exactly one CSV file"
                )
            member = members[0]
            if member.is_dir() or member.filename != expected_csv_filename:
                raise BinanceDailyArchiveError(
                    f"{archive_filename} does not contain the exact expected CSV filename"
                )
            if member.flag_bits & 0x1:
                raise BinanceDailyArchiveError(
                    f"{archive_filename} contains an unsupported encrypted CSV"
                )
            try:
                return archive.read(member)
            except (BadZipFile, RuntimeError, NotImplementedError, zlib.error) as error:
                raise BinanceDailyArchiveError(
                    f"{archive_filename} CSV member could not be read safely"
                ) from error
    except BadZipFile as error:
        raise BinanceDailyArchiveError(f"{archive_filename} is not a valid ZIP archive") from error


class BinanceSpotDailyArchiveAdapter:
    """Fetch and normalize exactly one checksum-verified Spot daily archive."""

    def __init__(
        self,
        *,
        http_get: _HTTP_GET | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a finite positive number")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        self._http_get = http_get or _default_http_get
        self._timeout_seconds = float(timeout_seconds)

    def fetch_daily_klines(
        self,
        *,
        symbol: str,
        interval: str,
        archive_date: date,
    ) -> tuple[Candle, ...]:
        """Return normalized rows from one verified Binance Spot daily archive."""
        if type(symbol) is not str:
            raise ValueError("symbol must be an exact built-in string")
        require_v1_symbol(symbol)
        if type(interval) is not str or interval != V1_INTERVAL:
            raise ValueError(f"interval must be {V1_INTERVAL!r}")
        if isinstance(archive_date, datetime) or type(archive_date) is not date:
            raise ValueError("archive_date must be a date, not a datetime")

        archive_filename, csv_filename = _archive_filenames(symbol, archive_date)
        archive_url = _archive_url(archive_filename, symbol)
        checksum_url = f"{archive_url}.CHECKSUM"

        checksum_payload = self._download(
            checksum_url, resource_name="checksum", archive_filename=archive_filename
        )
        expected_digest = _parse_checksum(
            checksum_payload, expected_filename=archive_filename
        )
        zip_bytes = self._download(
            archive_url, resource_name="ZIP", archive_filename=archive_filename
        )
        actual_digest = sha256(zip_bytes).hexdigest()
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise BinanceDailyArchiveError(
                f"SHA-256 checksum mismatch for {archive_filename}"
            )

        csv_bytes = _read_expected_csv(
            zip_bytes,
            expected_csv_filename=csv_filename,
            archive_filename=archive_filename,
        )
        return _normalize_csv(
            csv_bytes,
            symbol=symbol,
            interval=interval,
            archive_date=archive_date,
            archive_filename=archive_filename,
        )

    def _download(
        self, url: str, *, resource_name: str, archive_filename: str
    ) -> bytes:
        try:
            payload = self._http_get(url, self._timeout_seconds)
        except HTTPError as error:
            raise BinanceDailyArchiveError(
                f"Binance {resource_name} request failed for {archive_filename}: {error.code}",
                http_status=error.code,
            ) from error
        except (socket.timeout, TimeoutError) as error:
            raise BinanceDailyArchiveError(
                f"Binance {resource_name} request timed out for {archive_filename}"
            ) from error
        except URLError as error:
            raise BinanceDailyArchiveError(
                f"Binance {resource_name} network request failed for {archive_filename}: "
                f"{error.reason}"
            ) from error
        if not isinstance(payload, bytes):
            raise BinanceDailyArchiveError(
                f"Binance {resource_name} response for {archive_filename} must be bytes"
            )
        return payload
