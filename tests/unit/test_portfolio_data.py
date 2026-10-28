from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import zipfile
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from quantos.paper.research.data import (
    ArchiveStore, archive_ref, parse_funding, parse_klines, parse_metrics,
)


KLINE_HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


def zip_bytes(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


class Response:
    def __init__(self, body: bytes): self.body = body
    def read(self, size=-1): return self.body if size < 0 else self.body[:size]
    def close(self): pass


class PortfolioDataTests(unittest.TestCase):
    def test_archive_paths_validate_types_and_support_daily_latest_files(self):
        self.assertTrue(archive_ref("klines", "BTCUSDT", "2022-01", "1h").url.endswith(
            "/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2022-01.zip"))
        self.assertTrue(archive_ref("klines", "BTCUSDT", "2026-10-27", "5m").url.endswith(
            "/daily/klines/BTCUSDT/5m/BTCUSDT-5m-2026-10-27.zip"))
        self.assertTrue(archive_ref("metrics", "BTCUSDT", "2026-10-27").url.endswith(
            "/daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-10-27.zip"))
        with self.assertRaises(ValueError): archive_ref("metrics", "../BTC", "2026-10-27")
        with self.assertRaises(ValueError): archive_ref("fundingRate", "BTCUSDT", "2022-01-01")

    def test_kline_parser_converts_close_to_candle_boundary_and_rejects_gaps_only_when_duplicated(self):
        rows = KLINE_HEADER + "1640995200000,10,12,9,11,3,1640998799999,31,2,1,10,0\n"
        parsed = parse_klines(rows.encode(), "BTCUSDT", "1h")
        bar = parsed[0]
        self.assertEqual(bar.close_ms, 1640998799999)
        self.assertEqual(bar.close_exclusive_ms, 1640998800000)
        self.assertEqual(bar.quote_volume, Decimal("31"))
        self.assertEqual(bar.to_candle().close, bar.close)
        # Missing periods are preserved for downstream coverage checks; never filled.
        two = rows + "1641002400000,11,12,10,11,0,1641005999999,0,0,0,0,0\n"
        self.assertEqual(len(parse_klines(two.encode(), "BTCUSDT", "1h")), 2)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_klines((rows + rows.splitlines()[1] + "\n").encode(), "BTCUSDT", "1h")

    def test_funding_and_metrics_preserve_event_and_label_times_separately(self):
        funding = b"calc_time,funding_interval_hours,last_funding_rate\n1640995200006,8,0.0001\n"
        event = parse_funding(funding, "BTCUSDT")[0]
        self.assertEqual((event.event_ms, event.interval_hours), (1640995200006, 8))
        metrics = (b"create_time,symbol,sum_open_interest,sum_open_interest_value,count_long_short_ratio\n"
                   b"2026-06-25 00:00:00,BTCUSDT,10,100,1.2\n")
        row = parse_metrics(metrics, "BTCUSDT")[0]
        self.assertEqual(row.label_ms, 1782345600000)
        self.assertEqual(row.conservative_available_ms, 1782349200000)
        self.assertEqual(dict(row.other_fields)["count_long_short_ratio"], Decimal("1.2"))
        with self.assertRaisesRegex(ValueError, "symbol"):
            parse_metrics(metrics, "ETHUSDT")

    def test_store_checks_sidecar_zip_and_caches_content_addressed_blob(self):
        payload = zip_bytes("BTCUSDT-1h-2022-01.csv", KLINE_HEADER +
                            "1640995200000,10,12,9,11,3,1640998799999,31,2,1,10,0\n")
        digest = hashlib.sha256(payload).hexdigest()
        requested = []
        def opener(request, timeout):
            url = request.full_url
            requested.append(url)
            return Response((digest + "  BTCUSDT-1h-2022-01.zip\n").encode()
                            if url.endswith(".CHECKSUM") else payload)
        with tempfile.TemporaryDirectory() as tmp:
            store = ArchiveStore(tmp, opener=opener, sleep=lambda _: None)
            path = store.fetch("klines", "BTCUSDT", "2022-01", "1h")
            self.assertEqual(path.read_bytes(), payload)
            store.fetch("klines", "BTCUSDT", "2022-01", "1h")
            self.assertEqual(len(requested), 3)  # checksum rechecked; immutable blob reused
            self.assertEqual(len(parse_klines(path, "BTCUSDT", "1h")), 1)

    def test_store_rejects_bad_checksum_and_archive_size_limit(self):
        payload = zip_bytes("x.csv", "x\n1\n")
        def opener(request, timeout): return Response(b"0" * 64 if request.full_url.endswith(".CHECKSUM") else payload)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "checksum"):
                ArchiveStore(tmp, opener=opener, sleep=lambda _: None).fetch("klines", "BTCUSDT", "2022-01", "1h")

    def test_coverage_lists_monthly_and_daily_latest_partitions(self):
        def opener(request, timeout):
            query = parse_qs(urlparse(request.full_url).query)
            prefix = query["prefix"][0]
            key = (prefix + "BTCUSDT-1h-2022-01.zip" if "/monthly/" in prefix
                   else prefix + "BTCUSDT-1h-2026-10-27.zip")
            xml = ("<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>"
                   f"<Contents><Key>{key}</Key></Contents><IsTruncated>false</IsTruncated></ListBucketResult>").encode()
            return Response(xml)
        with tempfile.TemporaryDirectory() as tmp:
            coverage = ArchiveStore(tmp, opener=opener).coverage("klines", "BTCUSDT", "1h")
        self.assertEqual(coverage.periods, ("2022-01", "2026-10-27"))
        self.assertEqual((coverage.first_period, coverage.last_period), ("2022-01", "2026-10-27"))


if __name__ == "__main__":
    unittest.main()
