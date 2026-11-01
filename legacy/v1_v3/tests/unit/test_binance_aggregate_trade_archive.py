"""Adversarial offline tests for Binance aggregate-trade daily archives."""

from __future__ import annotations

import ast
import csv
from datetime import date, datetime, timezone
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
import stat
from urllib.error import HTTPError, URLError
import unittest
import warnings
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from quantos.domain.market_data.research_events import (
    AggregateIdContinuityStatus,
    ArchiveChecksumStatus,
    ResearchDatasetRole,
    SourceTimestampUnit,
)
from quantos.infrastructure.binance import (
    AGGREGATE_TRADE_ARCHIVE_ADAPTER_VERSION,
    AGGREGATE_TRADE_ARCHIVE_SCHEMA_FINGERPRINT,
    AGGREGATE_TRADE_ARCHIVE_SCHEMA_VERSION,
    BINANCE_PUBLIC_ARCHIVE_URL,
    BinanceAggregateTradeArchiveError,
    BinanceSpotAggregateTradeDailyArchiveAdapter,
    aggregate_trade_archive_resource_urls,
)

UTC = timezone.utc
MILLISECOND_DATE = date(2024, 12, 31)
MICROSECOND_DATE = date(2025, 1, 1)
MILLISECOND_TIMESTAMP = 1_735_603_200_123
MICROSECOND_TIMESTAMP = 1_735_689_600_010_866
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_SOURCE = (
    REPOSITORY_ROOT
    / "src"
    / "quantos"
    / "infrastructure"
    / "binance"
    / "aggregate_trade_archive.py"
)


def row(
    *,
    aggregate_trade_id: str = "700",
    price: str = "93452.12000000",
    quantity: str = "0.00170000",
    first_trade_id: str = "900",
    last_trade_id: str = "902",
    timestamp: str = str(MICROSECOND_TIMESTAMP),
    buyer_is_maker: str = "False",
    best_price_match: str = "True",
) -> list[str]:
    return [
        aggregate_trade_id,
        price,
        quantity,
        first_trade_id,
        last_trade_id,
        timestamp,
        buyer_is_maker,
        best_price_match,
    ]


def csv_bytes(rows: list[list[str]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def zip_bytes(
    rows: list[list[str]],
    *,
    symbol: str = "BTCUSDT",
    archive_date: date = MICROSECOND_DATE,
    member_name: str | None = None,
    extra_members: tuple[tuple[str, bytes], ...] = (),
    duplicate_expected_member: bool = False,
    symlink_member: bool = False,
) -> bytes:
    _, _, _, expected_csv = aggregate_trade_archive_resource_urls(
        symbol=symbol, archive_date=archive_date
    )
    selected_name = member_name or expected_csv
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        if symlink_member:
            info = ZipInfo(selected_name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
        else:
            archive.writestr(selected_name, csv_bytes(rows))
        if duplicate_expected_member:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(selected_name, csv_bytes(rows))
        for name, content in extra_members:
            archive.writestr(name, content)
    return output.getvalue()


def checksum_bytes(content: bytes, filename: str, *, digest: str | None = None) -> bytes:
    selected_digest = digest or sha256(content).hexdigest()
    return f"{selected_digest}  {filename}\n".encode("ascii")


class RecordingHttpGet:
    def __init__(self, *responses: bytes | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout_seconds: float) -> bytes:
        self.calls.append((url, timeout_seconds))
        if not self.responses:
            raise AssertionError("unexpected network call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def adapter_for_zip(
    content: bytes,
    *,
    symbol: str = "BTCUSDT",
    archive_date: date = MICROSECOND_DATE,
    checksum_payload: bytes | None = None,
) -> tuple[BinanceSpotAggregateTradeDailyArchiveAdapter, RecordingHttpGet]:
    _, _, zip_filename, _ = aggregate_trade_archive_resource_urls(
        symbol=symbol, archive_date=archive_date
    )
    http_get = RecordingHttpGet(
        checksum_bytes(content, zip_filename)
        if checksum_payload is None
        else checksum_payload,
        content,
    )
    return BinanceSpotAggregateTradeDailyArchiveAdapter(http_get=http_get), http_get


def fetch(
    adapter: BinanceSpotAggregateTradeDailyArchiveAdapter,
    *,
    symbol: str = "BTCUSDT",
    archive_date: date = MICROSECOND_DATE,
):
    return adapter.fetch_daily_archive(
        symbol=symbol,
        archive_date=archive_date,
        research_role=ResearchDatasetRole.DEVELOPMENT,
    )


class ArchiveUrlAndTransportTests(unittest.TestCase):
    def test_exact_btc_and_eth_daily_resource_urls(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                archive_url, checksum_url, zip_filename, csv_filename = (
                    aggregate_trade_archive_resource_urls(
                        symbol=symbol, archive_date=MICROSECOND_DATE
                    )
                )
                stem = f"{symbol}-aggTrades-2025-01-01"
                self.assertEqual(
                    archive_url,
                    f"{BINANCE_PUBLIC_ARCHIVE_URL}/data/spot/daily/aggTrades/"
                    f"{symbol}/{stem}.zip",
                )
                self.assertEqual(checksum_url, archive_url + ".CHECKSUM")
                self.assertEqual(zip_filename, stem + ".zip")
                self.assertEqual(csv_filename, stem + ".csv")

    def test_rejects_bad_symbols_dates_injection_and_subclasses(self) -> None:
        class TextSubclass(str):
            pass

        class DateSubclass(date):
            pass

        invalid_symbols = (
            "SOLUSDT",
            "BTCUSDT/../ETHUSDT",
            "BTCUSDT?x=1",
            "BTCUSDT#x",
            "BTCUSDT\x00",
            TextSubclass("BTCUSDT"),
            1,
        )
        for symbol in invalid_symbols:
            with self.subTest(symbol=symbol):
                with self.assertRaises((TypeError, ValueError)):
                    aggregate_trade_archive_resource_urls(
                        symbol=symbol,  # type: ignore[arg-type]
                        archive_date=MICROSECOND_DATE,
                    )
        for archive_date in (
            "2025-01-01",
            datetime(2025, 1, 1, tzinfo=UTC),
            DateSubclass(2025, 1, 1),
            None,
        ):
            with self.subTest(archive_date=archive_date):
                with self.assertRaises(ValueError):
                    aggregate_trade_archive_resource_urls(
                        symbol="BTCUSDT", archive_date=archive_date  # type: ignore[arg-type]
                    )

    def test_fetches_checksum_then_zip_once_with_finite_timeout(self) -> None:
        content = zip_bytes([row()])
        adapter, http_get = adapter_for_zip(content)

        result = fetch(adapter)

        archive_url, checksum_url, _, _ = aggregate_trade_archive_resource_urls(
            symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        self.assertEqual(
            http_get.calls,
            [(checksum_url, 10.0), (archive_url, 10.0)],
        )
        self.assertEqual(result.raw_zip_bytes, content)

    def test_missing_checksum_http_error_stops_without_zip_or_fallback(self) -> None:
        archive_url, checksum_url, _, _ = aggregate_trade_archive_resource_urls(
            symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        missing = HTTPError(checksum_url, 404, "missing", None, None)
        http_get = RecordingHttpGet(missing)
        adapter = BinanceSpotAggregateTradeDailyArchiveAdapter(http_get=http_get)

        with self.assertRaises(BinanceAggregateTradeArchiveError) as captured:
            fetch(adapter)

        self.assertEqual(captured.exception.http_status, 404)
        self.assertEqual(http_get.calls, [(checksum_url, 10.0)])
        self.assertNotIn(archive_url, [call[0] for call in http_get.calls])

    def test_transport_failures_are_not_retried_or_fallbacked(self) -> None:
        for failure in (TimeoutError("timeout"), URLError("offline")):
            with self.subTest(failure=type(failure).__name__):
                http_get = RecordingHttpGet(failure)
                adapter = BinanceSpotAggregateTradeDailyArchiveAdapter(
                    http_get=http_get
                )
                with self.assertRaises(BinanceAggregateTradeArchiveError):
                    fetch(adapter)

    def test_wrong_sha_rejected_before_zip_parsing(self) -> None:
        content = b"not a zip"
        _, _, filename, _ = aggregate_trade_archive_resource_urls(
            symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        adapter, _ = adapter_for_zip(
            content,
            checksum_payload=checksum_bytes(
                content, filename, digest="0" * 64
            ),
        )
        with self.assertRaisesRegex(
            BinanceAggregateTradeArchiveError, "checksum mismatch"
        ):
            fetch(adapter)

    def test_rejects_corrupt_zip_wrong_member_traversal_extra_duplicate_and_symlink(
        self,
    ) -> None:
        _, _, zip_filename, csv_filename = aggregate_trade_archive_resource_urls(
            symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        cases = (
            b"not a zip",
            zip_bytes([row()], member_name="wrong.csv"),
            zip_bytes([row()], member_name="../" + csv_filename),
            zip_bytes([row()], extra_members=(("extra.txt", b"x"),)),
            zip_bytes([row()], duplicate_expected_member=True),
            zip_bytes([row()], symlink_member=True),
        )
        for content in cases:
            with self.subTest(content_size=len(content)):
                adapter, _ = adapter_for_zip(
                    content,
                    checksum_payload=checksum_bytes(content, zip_filename),
                )
                with self.assertRaises(BinanceAggregateTradeArchiveError):
                    fetch(adapter)

    def test_timeout_requires_exact_finite_positive_number(self) -> None:
        class FloatSubclass(float):
            pass

        for value in (True, 0, -1, float("nan"), float("inf"), FloatSubclass(1.0)):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    BinanceSpotAggregateTradeDailyArchiveAdapter(
                        http_get=RecordingHttpGet(), timeout_seconds=value
                    )


class ChecksumAndZipSafetyTests(unittest.TestCase):
    def test_checksum_is_retained_and_matches_exact_raw_zip_hash(self) -> None:
        content = zip_bytes([row()])
        adapter, _ = adapter_for_zip(content)

        result = fetch(adapter)

        expected = sha256(content).hexdigest()
        self.assertEqual(result.archive.manifest.published_checksum, expected)
        self.assertEqual(result.archive.manifest.raw_zip_sha256, expected)
        self.assertIs(
            result.archive.manifest.checksum_status, ArchiveChecksumStatus.VERIFIED
        )

    def test_rejects_malformed_ambiguous_and_control_character_checksums(self) -> None:
        content = zip_bytes([row()])
        _, _, filename, _ = aggregate_trade_archive_resource_urls(
            symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        digest = sha256(content).hexdigest()
        invalid = (
            b"",
            digest.encode("ascii"),
            f"{digest} wrong.zip\n".encode("ascii"),
            f"{digest[:-1]}z  {filename}\n".encode("ascii"),
            f"{digest}  {filename}\n{digest}  {filename}\n".encode("ascii"),
            f"{digest}\x00  {filename}\n".encode("ascii"),
            b"\xff",
        )
        for checksum_payload in invalid:
            with self.subTest(payload=checksum_payload[:40]):
                adapter, http_get = adapter_for_zip(
                    content, checksum_payload=checksum_payload
                )
                with self.assertRaises(BinanceAggregateTradeArchiveError):
                    fetch(adapter)
                self.assertEqual(len(http_get.calls), 1)


class CsvTimestampAndSequenceTests(unittest.TestCase):
    def assert_rejected(
        self, rows: list[list[str]], *, archive_date: date = MICROSECOND_DATE
    ) -> None:
        content = zip_bytes(rows, archive_date=archive_date)
        adapter, _ = adapter_for_zip(content, archive_date=archive_date)
        with self.assertRaises(BinanceAggregateTradeArchiveError):
            fetch(adapter, archive_date=archive_date)

    def test_rejects_wrong_column_count_header_and_malformed_scalars(self) -> None:
        invalid_rows = (
            row()[:-1],
            row() + ["extra"],
            [
                "agg_trade_id",
                "price",
                "quantity",
                "first_trade_id",
                "last_trade_id",
                "timestamp",
                "buyer_is_maker",
                "best_price_match",
            ],
            row(price="not-a-decimal"),
            row(quantity="NaN"),
            row(aggregate_trade_id="1.0"),
            row(first_trade_id="-1"),
            row(buyer_is_maker="false"),
            row(best_price_match="1"),
        )
        for invalid in invalid_rows:
            with self.subTest(row=invalid):
                self.assert_rejected([invalid])

    def test_rejects_empty_and_non_utf8_csv(self) -> None:
        self.assert_rejected([])
        _, _, _, csv_filename = aggregate_trade_archive_resource_urls(
            symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(csv_filename, b"\xff")
        content = output.getvalue()
        adapter, _ = adapter_for_zip(content)
        with self.assertRaises(BinanceAggregateTradeArchiveError):
            fetch(adapter)

    def test_timestamp_unit_is_date_driven_at_2025_boundary_for_both_symbols(self) -> None:
        cases = (
            (
                MILLISECOND_DATE,
                str(MILLISECOND_TIMESTAMP),
                SourceTimestampUnit.MILLISECOND,
            ),
            (
                MICROSECOND_DATE,
                str(MICROSECOND_TIMESTAMP),
                SourceTimestampUnit.MICROSECOND,
            ),
        )
        for symbol in ("BTCUSDT", "ETHUSDT"):
            for archive_date, timestamp, expected_unit in cases:
                with self.subTest(symbol=symbol, archive_date=archive_date):
                    content = zip_bytes(
                        [row(timestamp=timestamp)],
                        symbol=symbol,
                        archive_date=archive_date,
                    )
                    adapter, _ = adapter_for_zip(
                        content, symbol=symbol, archive_date=archive_date
                    )
                    result = fetch(
                        adapter, symbol=symbol, archive_date=archive_date
                    )
                    event = result.archive.sequence.events[0]
                    self.assertIs(event.source_timestamp_unit, expected_unit)
                    self.assertEqual(event.source_timestamp, int(timestamp))
                    self.assertIs(
                        result.archive.manifest.source_timestamp_unit, expected_unit
                    )

    def test_timestamp_unit_is_never_inferred_from_magnitude(self) -> None:
        self.assert_rejected(
            [row(timestamp=str(MICROSECOND_TIMESTAMP))],
            archive_date=MILLISECOND_DATE,
        )
        self.assert_rejected(
            [row(timestamp=str(MILLISECOND_TIMESTAMP))],
            archive_date=MICROSECOND_DATE,
        )

    def test_rejects_event_outside_declared_source_day(self) -> None:
        before_day = str(MICROSECOND_TIMESTAMP - 86_400_000_000)
        after_day = str(MICROSECOND_TIMESTAMP + 86_400_000_000)
        for timestamp in (before_day, after_day):
            with self.subTest(timestamp=timestamp):
                self.assert_rejected([row(timestamp=timestamp)])

    def test_rejects_duplicate_conflicting_and_reversed_sequences(self) -> None:
        duplicate = row()
        conflict = row(price="93453.12000000")
        reversed_rows = [
            row(
                aggregate_trade_id="701",
                timestamp=str(MICROSECOND_TIMESTAMP + 1),
            ),
            row(
                aggregate_trade_id="700",
                timestamp=str(MICROSECOND_TIMESTAMP),
            ),
        ]
        cases = ([duplicate, duplicate], [duplicate, conflict], reversed_rows)
        for rows in cases:
            with self.subTest(rows=rows):
                self.assert_rejected(rows)

    def test_same_time_different_ids_is_valid_and_numerical_gap_is_observed_only(self) -> None:
        rows = [
            row(aggregate_trade_id="700"),
            row(
                aggregate_trade_id="705",
                price="93452.13000000",
                first_trade_id="903",
                last_trade_id="904",
            ),
        ]
        content = zip_bytes(rows)
        adapter, _ = adapter_for_zip(content)

        result = fetch(adapter)

        manifest = result.archive.manifest
        self.assertEqual(
            [
                event.aggregate_trade_id
                for event in result.archive.sequence.events
            ],
            [700, 705],
        )
        self.assertEqual(manifest.observed_numerical_id_gap_count, 1)
        self.assertIs(
            manifest.id_continuity_status,
            AggregateIdContinuityStatus.NOT_ASSERTED,
        )


class ManifestAndIdentityTests(unittest.TestCase):
    def test_manifest_records_exact_requested_and_observed_coverage(self) -> None:
        rows = [
            row(timestamp=str(MICROSECOND_TIMESTAMP)),
            row(
                aggregate_trade_id="701",
                first_trade_id="903",
                last_trade_id="903",
                timestamp=str(MICROSECOND_TIMESTAMP + 2_000_001),
            ),
        ]
        content = zip_bytes(rows)
        adapter, _ = adapter_for_zip(content)

        result = fetch(adapter)
        manifest = result.archive.manifest

        self.assertEqual(manifest.provider, "binance")
        self.assertEqual(manifest.market, "spot")
        self.assertEqual(manifest.source_family, "aggregate_trade")
        self.assertEqual(manifest.source_type, "daily_public_archive")
        self.assertEqual(
            manifest.research_role, ResearchDatasetRole.DEVELOPMENT
        )
        self.assertEqual(manifest.source_date, MICROSECOND_DATE)
        self.assertEqual(
            manifest.requested_start_time,
            datetime(2025, 1, 1, tzinfo=UTC),
        )
        self.assertEqual(
            manifest.requested_end_time_exclusive,
            datetime(2025, 1, 2, tzinfo=UTC),
        )
        self.assertEqual(manifest.observed_first_event_time.microsecond, 10_866)
        self.assertEqual(manifest.observed_last_event_time.microsecond, 10_867)
        self.assertEqual(manifest.parsed_row_count, 2)
        self.assertEqual(manifest.accepted_row_count, 2)
        self.assertEqual(manifest.rejected_row_count, 0)
        self.assertEqual(manifest.duplicate_count, 0)
        self.assertEqual(manifest.conflicting_id_count, 0)
        self.assertEqual(manifest.fetched_resource_count, 2)
        self.assertEqual(
            manifest.schema_version, AGGREGATE_TRADE_ARCHIVE_SCHEMA_VERSION
        )
        self.assertEqual(
            manifest.schema_fingerprint,
            AGGREGATE_TRADE_ARCHIVE_SCHEMA_FINGERPRINT,
        )
        self.assertIn(
            AGGREGATE_TRADE_ARCHIVE_ADAPTER_VERSION, manifest.provenance
        )
        self.assertNotEqual(
            manifest.observed_first_event_time,
            manifest.requested_start_time,
        )
        self.assertNotEqual(
            manifest.observed_last_event_time,
            manifest.requested_end_time_exclusive,
        )

    def test_hashes_and_identity_are_deterministic_and_raw_revision_changes_identity(self) -> None:
        rows = [row()]
        first_bytes = zip_bytes(rows)
        second_bytes = zip_bytes(rows, extra_members=())
        self.assertEqual(first_bytes, second_bytes)

        first = fetch(adapter_for_zip(first_bytes)[0])
        rerun = fetch(adapter_for_zip(first_bytes)[0])
        self.assertEqual(first.archive, rerun.archive)
        self.assertEqual(
            first.archive.manifest.canonical_sequence_sha256,
            rerun.archive.manifest.canonical_sequence_sha256,
        )
        self.assertEqual(
            first.archive.manifest.source_revision_id,
            rerun.archive.manifest.source_revision_id,
        )

        output = BytesIO()
        _, _, _, csv_filename = aggregate_trade_archive_resource_urls(
            symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        with ZipFile(
            output, "w", compression=ZIP_DEFLATED, compresslevel=1
        ) as archive:
            archive.writestr(csv_filename, csv_bytes(rows))
        revised_bytes = output.getvalue()
        if revised_bytes == first_bytes:
            output = BytesIO()
            with ZipFile(output, "w") as archive:
                archive.writestr(
                    csv_filename, csv_bytes(rows), compress_type=0
                )
            revised_bytes = output.getvalue()
        revised = fetch(adapter_for_zip(revised_bytes)[0])
        self.assertNotEqual(
            first.archive.manifest.raw_zip_sha256,
            revised.archive.manifest.raw_zip_sha256,
        )
        self.assertNotEqual(
            first.archive.manifest.source_revision_id,
            revised.archive.manifest.source_revision_id,
        )
        self.assertNotEqual(
            first.archive.manifest.dataset_id,
            revised.archive.manifest.dataset_id,
        )
        self.assertNotEqual(
            first.archive.manifest.canonical_sequence_sha256,
            revised.archive.manifest.canonical_sequence_sha256,
        )

    def test_research_role_must_be_explicit_and_exact(self) -> None:
        content = zip_bytes([row()])
        adapter, _ = adapter_for_zip(content)
        for invalid in ("development", None, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    adapter.fetch_daily_archive(
                        symbol="BTCUSDT",
                        archive_date=MICROSECOND_DATE,
                        research_role=invalid,  # type: ignore[arg-type]
                    )

    def test_adapter_has_no_rest_or_websocket_fallback_path(self) -> None:
        source = ADAPTER_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        lowered = source.lower()
        self.assertNotIn(
            "websocket", " ".join(imported_modules).lower()
        )
        self.assertNotIn("api.binance.com", lowered)
        self.assertNotIn("/api/", lowered)
        self.assertEqual(lowered.count("self._download("), 2)


if __name__ == "__main__":
    unittest.main()
