"""Offline source-integrity/access checks; fixtures are not market evidence."""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from research.v1f1.audit_sources import (
    archive_path, fetch, parse_listing, publish, sha256, validate_archive,
)


class V1F1SourceAuditTests(unittest.TestCase):
    def fixture(self, rows, *, family="klines"):
        filename = f"BTCUSDT-{'fundingRate' if family == 'fundingRate' else '1m'}-2020-01.zip"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(filename.replace(".zip", ".csv"), rows)
        data = buffer.getvalue()
        checksum = f"{sha256(data)}  {filename}\n".encode()
        return data, checksum, filename, family

    def test_checksum_and_filename_bind_raw_bytes(self):
        args = self.fixture("1577836800000,10,11,9,10,2,1577836859999,20,4,1,10,0\n")
        result = validate_archive(*args)
        self.assertTrue(result["checksum_verified"])
        self.assertEqual(result["rows"], 1)
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_archive(args[0] + b"corrupt", *args[1:])
        with self.assertRaisesRegex(ValueError, "checksum"):
            validate_archive(args[0], args[1].replace(b"BTCUSDT", b"ETHUSDT"), *args[2:])

    def test_missing_minute_is_quarantined_not_filled(self):
        result = validate_archive(*self.fixture(
            "1577836800000,10,11,9,10,2,1577836859999,20,4,1,10,0\n"
            "1577836920000,10,11,9,10,2,1577836979999,20,4,1,10,0\n"))
        self.assertEqual(result["partition_status"], "QUARANTINED_GAPS")
        self.assertEqual(result["rows"], 2)

    def test_duplicate_and_invalid_ohlc_are_rejected(self):
        row = "1577836800000,10,11,9,10,2,1577836859999,20,4,1,10,0\n"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_archive(*self.fixture(row + row))
        with self.assertRaisesRegex(ValueError, "OHLC"):
            validate_archive(*self.fixture(row.replace(",10,11,9,10,", ",10,8,9,10,")))

    def test_funding_schema_preserves_signed_observation(self):
        result = validate_archive(*self.fixture(
            "calc_time,funding_interval_hours,last_funding_rate\n"
            "1577836800000,8,-0.0001\n", family="fundingRate"))
        self.assertEqual(result["rows"], 1)
        self.assertIsNone(result["minute_discontinuities"])
        with self.assertRaises(ValueError):
            validate_archive(*self.fixture(
                "calc_time,funding_interval_hours,last_funding_rate\n"
                "1577836800000,8,NaN\n", family="fundingRate"))

    def test_prefinal_holdout_and_microseconds_are_denied(self):
        for month in ("2025-01", "2026-01"):
            with self.assertRaisesRegex(ValueError, "development-only"):
                archive_path("BTCUSDT", "klines", month)
        for timestamp in (1735689600000, 1577836800000000):
            with self.assertRaisesRegex(ValueError, "time units"):
                validate_archive(*self.fixture(
                    f"{timestamp},10,11,9,10,2,{timestamp + 59999},20,4,1,10,0\n"))

    def test_public_fetch_rejects_account_and_order_endpoints(self):
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.urlopen") as network:
            for url in ("https://fapi.binance.com/fapi/v1/order",
                        "https://fapi.binance.com/fapi/v2/account",
                        "https://fapi.binance.com/fapi/v1/exchangeInfo?signature=x",
                        "http://fapi.binance.com/fapi/v1/exchangeInfo"):
                with self.assertRaises(ValueError):
                    fetch(Path(directory), url)
            network.assert_not_called()

    def test_truncated_listing_requires_continuation(self):
        xml = (b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
               b'<IsTruncated>true</IsTruncated></ListBucketResult>')
        with self.assertRaisesRegex(ValueError, "continuation"):
            parse_listing(xml)

    def test_evidence_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source"
            publish(path, b"original")
            publish(path, b"original")
            with self.assertRaisesRegex(ValueError, "collision"):
                publish(path, b"changed")
            self.assertEqual(path.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
