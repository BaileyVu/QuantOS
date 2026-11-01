"""Offline daily archive composition through canonical historical ingestion."""

from __future__ import annotations

from datetime import timedelta
import unittest
from unittest.mock import patch

from quantos.application import HistoricalIngestionError, ingest_historical_range
from quantos.domain.market_data import (
    DatasetValidationError,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)
from quantos.infrastructure.binance import BinanceSpotDailyArchiveRangeFetcher
from tests.unit.test_binance_daily_archive_range_fetch import (
    ONE_DAY,
    START,
    candle,
    daily_call,
    fake_adapter,
)


class DailyArchiveHistoricalIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        for module in ("daily_archive", "klines"):
            guard = patch(
                f"quantos.infrastructure.binance.{module}.urlopen",
                side_effect=AssertionError("real network access is forbidden"),
            )
            guard.start()
            self.addCleanup(guard.stop)

    def ingest(
        self, fetcher: BinanceSpotDailyArchiveRangeFetcher, *, symbol: str = "BTCUSDT"
    ) -> ValidatedCandleSequence:
        return ingest_historical_range(
            fetcher,
            symbol=symbol,
            interval="1m",
            start_open_time=START,
            end_open_time_exclusive=START + ONE_DAY * 2,
            source="binance-spot-daily-archive",
            schema_version="candle-v1",
            ingestion_version="fixture-2b5-v1",
        )

    def test_clean_multi_day_sequence_uses_existing_identity_and_validation(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                candidates = tuple(
                    candle(START + timedelta(minutes=minute), symbol=symbol)
                    for minute in range(2880)
                )
                adapter = fake_adapter(candidates[:1440], candidates[1440:])
                fetcher = BinanceSpotDailyArchiveRangeFetcher(adapter)

                with patch(
                    "quantos.application.historical_ingestion.validate_candle_sequence",
                    wraps=validate_candle_sequence,
                ) as validation:
                    result = self.ingest(fetcher, symbol=symbol)

                validation.assert_called_once()
                initial_identity, received = validation.call_args.args
                self.assertIs(initial_identity.validation_status, DatasetValidationStatus.UNVALIDATED)
                self.assertEqual(received, candidates)
                self.assertIsInstance(result, ValidatedCandleSequence)
                self.assertEqual(result.candles, candidates)
                self.assertEqual(len(result.candles), 2880)
                self.assertEqual(result.identity.symbol, symbol)
                self.assertEqual(result.identity.timeframe, "1m")
                self.assertEqual(result.identity.start_time, START)
                self.assertEqual(
                    result.identity.end_time, START + ONE_DAY * 2 - timedelta(minutes=1)
                )
                self.assertEqual(result.identity.source, "binance-spot-daily-archive")
                self.assertEqual(result.identity.schema_version, "candle-v1")
                self.assertEqual(result.identity.ingestion_version, "fixture-2b5-v1")
                self.assertIs(result.identity.validation_status, DatasetValidationStatus.VALIDATED)
                self.assertEqual(
                    adapter.fetch_daily_klines.call_args_list,
                    [
                        daily_call(START.date(), symbol=symbol),
                        daily_call((START + ONE_DAY).date(), symbol=symbol),
                    ],
                )

    def test_missing_minute_survives_composition_and_fails_domain_validation(self) -> None:
        first_day = tuple(candle(START + timedelta(minutes=m)) for m in range(1439))
        second_day = tuple(candle(START + timedelta(minutes=m)) for m in range(1440, 2880))
        candidates = first_day + second_day  # Missing 23:59 at the archive boundary.
        adapter = fake_adapter(first_day, second_day)
        fetcher = BinanceSpotDailyArchiveRangeFetcher(adapter)

        with patch(
            "quantos.application.historical_ingestion.validate_candle_sequence",
            wraps=validate_candle_sequence,
        ) as validation:
            with self.assertRaisesRegex(DatasetValidationError, "missing 1m candle timestamp"):
                self.ingest(fetcher)

        validation.assert_called_once()
        identity, received = validation.call_args.args
        self.assertIs(identity.validation_status, DatasetValidationStatus.UNVALIDATED)
        self.assertEqual(received, candidates)
        self.assertEqual(len(received), 2879)
        self.assertEqual(
            adapter.fetch_daily_klines.call_args_list,
            [daily_call(START.date()), daily_call((START + ONE_DAY).date())],
        )

    def test_missing_boundary_minute_fails_application_range_completeness(self) -> None:
        complete = tuple(candle(START + timedelta(minutes=m)) for m in range(2880))
        for missing_minute in (0, 2879):
            with self.subTest(missing_minute=missing_minute):
                first_day = tuple(complete[m] for m in range(1440) if m != missing_minute)
                second_day = tuple(complete[m] for m in range(1440, 2880) if m != missing_minute)
                fetcher = BinanceSpotDailyArchiveRangeFetcher(fake_adapter(first_day, second_day))

                with patch(
                    "quantos.application.historical_ingestion.validate_candle_sequence",
                    wraps=validate_candle_sequence,
                ) as validation:
                    with self.assertRaisesRegex(HistoricalIngestionError, "incomplete"):
                        self.ingest(fetcher)

                validation.assert_called_once()
                self.assertEqual(validation.call_args.args[1], first_day + second_day)
