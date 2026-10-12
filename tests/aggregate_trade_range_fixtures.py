"""Shared offline fixtures for immutable aggregate-trade range tests."""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from quantos.domain.market_data.research_events import ResearchDatasetRole
from quantos.infrastructure.binance import (
    BinanceSpotAggregateTradeDailyArchiveAdapter,
    aggregate_trade_archive_resource_urls,
)
from quantos.infrastructure.storage import (
    LocalAggregateTradeArchiveCatalog,
    ParquetAggregateTradeArchiveStore,
)
from tests.unit.test_binance_aggregate_trade_archive import (
    MICROSECOND_DATE,
    MICROSECOND_TIMESTAMP,
    MILLISECOND_DATE,
    MILLISECOND_TIMESTAMP,
    RecordingHttpGet,
    checksum_bytes,
    csv_bytes,
    row,
    zip_bytes,
)


def partition_rows(
    source_date: date,
    *,
    first_id: int,
) -> list[list[str]]:
    timestamp = (
        MILLISECOND_TIMESTAMP
        if source_date == MILLISECOND_DATE
        else MICROSECOND_TIMESTAMP
    )
    return [
        row(
            aggregate_trade_id=str(first_id),
            price="93452.1200",
            quantity="0.00170000",
            first_trade_id=str(first_id + 100),
            last_trade_id=str(first_id + 100),
            timestamp=str(timestamp),
        ),
        row(
            aggregate_trade_id=str(first_id + 1),
            price="93452.130000000000000001",
            quantity="1.2300",
            first_trade_id=str(first_id + 101),
            last_trade_id=str(first_id + 102),
            timestamp=str(timestamp + 1),
            buyer_is_maker="True",
            best_price_match="False",
        ),
    ]


def source_zip(
    rows: list[list[str]],
    *,
    symbol: str,
    source_date: date,
    stored: bool = False,
) -> bytes:
    if not stored:
        return zip_bytes(
            rows, symbol=symbol, archive_date=source_date
        )
    _, _, _, csv_filename = aggregate_trade_archive_resource_urls(
        symbol=symbol, archive_date=source_date
    )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        archive.writestr(csv_filename, csv_bytes(rows))
    return output.getvalue()


def publish_partition(
    root: Path,
    *,
    symbol: str,
    source_date: date,
    first_id: int,
    stored: bool = False,
):
    rows = partition_rows(source_date, first_id=first_id)
    return publish_rows(
        root,
        symbol=symbol,
        source_date=source_date,
        rows=rows,
        stored=stored,
    )


def publish_rows(
    root: Path,
    *,
    symbol: str,
    source_date: date,
    rows: list[list[str]],
    stored: bool = False,
):
    content = source_zip(
        rows, symbol=symbol, source_date=source_date, stored=stored
    )
    _, _, zip_filename, _ = aggregate_trade_archive_resource_urls(
        symbol=symbol, archive_date=source_date
    )
    http_get = RecordingHttpGet(
        checksum_bytes(content, zip_filename), content
    )
    fetched = BinanceSpotAggregateTradeDailyArchiveAdapter(
        http_get=http_get
    ).fetch_daily_archive(
        symbol=symbol,
        archive_date=source_date,
        research_role=ResearchDatasetRole.DEVELOPMENT,
    )
    publication = ParquetAggregateTradeArchiveStore(root).write(
        fetched.archive, raw_zip_bytes=fetched.raw_zip_bytes
    )
    return fetched, publication


def local_catalog(root: Path) -> LocalAggregateTradeArchiveCatalog:
    return LocalAggregateTradeArchiveCatalog(
        root,
        provider="binance",
        market="spot",
        event_family="aggregate_trade",
    )
