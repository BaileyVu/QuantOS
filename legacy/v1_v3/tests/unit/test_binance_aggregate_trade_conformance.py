"""Static conformance tests for Binance Spot aggregate-trade normalization."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Mapping
import unittest

from quantos.domain.market_data import Candle, DatasetIdentity, MarketEvent
from quantos.domain.market_data.research_events import (
    AggregateTrade,
    AggregateTradeDatasetIdentity,
    AggregateTradeValidationError,
    AggressorSide,
    ResearchDatasetRole,
    SourceTimestampUnit,
    ValidatedAggregateTradeSequence,
    normalize_source_timestamp,
    validate_aggregate_trade_sequence,
)
from quantos.infrastructure.binance import (
    AGGREGATE_TRADE_NORMALIZER_VERSION,
    ARCHIVE_AGGREGATE_TRADE_COLUMNS,
    ARCHIVE_MICROSECOND_ERA_START,
    BinanceAggregateTradeNormalizationError,
    BinanceAggregateTradeSource,
    archive_timestamp_unit,
    canonical_aggregate_trade_sequence_bytes,
    canonical_aggregate_trade_sequence_sha256,
    normalize_archive_aggregate_trade,
    normalize_rest_aggregate_trade,
    normalize_websocket_aggregate_trade,
    raw_content_sha256,
)

UTC = timezone.utc
MILLISECOND_DATE = date(2024, 12, 31)
MICROSECOND_DATE = date(2025, 1, 1)
MILLISECOND_TIMESTAMP = 1_735_603_200_123
MICROSECOND_TIMESTAMP = 1_735_689_600_010_866
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NORMALIZER_SOURCE = (
    REPOSITORY_ROOT
    / "src"
    / "quantos"
    / "infrastructure"
    / "binance"
    / "aggregate_trades.py"
)


def archive_row(
    *,
    aggregate_trade_id: str = "700",
    price: str = "93452.12000000",
    quantity: str = "0.00170000",
    first_trade_id: str = "900",
    last_trade_id: str = "902",
    timestamp: str = str(MICROSECOND_TIMESTAMP),
    buyer_is_maker: str = "False",
    best_price_match: str = "True",
) -> tuple[str, ...]:
    return (
        aggregate_trade_id,
        price,
        quantity,
        first_trade_id,
        last_trade_id,
        timestamp,
        buyer_is_maker,
        best_price_match,
    )


def rest_payload(
    *,
    aggregate_trade_id: int = 700,
    price: str = "93452.12000000",
    quantity: str = "0.00170000",
    first_trade_id: int = 900,
    last_trade_id: int = 902,
    timestamp: int = MICROSECOND_TIMESTAMP,
    buyer_is_maker: bool = False,
    best_price_match: bool = True,
) -> dict[str, object]:
    return {
        "a": aggregate_trade_id,
        "p": price,
        "q": quantity,
        "f": first_trade_id,
        "l": last_trade_id,
        "T": timestamp,
        "m": buyer_is_maker,
        "M": best_price_match,
    }


def websocket_payload(
    *,
    symbol: str = "BTCUSDT",
    aggregate_trade_id: int = 700,
    price: str = "93452.12000000",
    quantity: str = "0.00170000",
    first_trade_id: int = 900,
    last_trade_id: int = 902,
    event_timestamp: int = MICROSECOND_TIMESTAMP + 100,
    trade_timestamp: int = MICROSECOND_TIMESTAMP,
    buyer_is_maker: bool = False,
    ignored_best_match_field: bool = True,
) -> dict[str, object]:
    return {
        "e": "aggTrade",
        "E": event_timestamp,
        "s": symbol,
        "a": aggregate_trade_id,
        "p": price,
        "q": quantity,
        "f": first_trade_id,
        "l": last_trade_id,
        "T": trade_timestamp,
        "m": buyer_is_maker,
        "M": ignored_best_match_field,
    }


def expected_event(
    *,
    symbol: str = "BTCUSDT",
    aggregate_trade_id: int = 700,
    price: Decimal = Decimal("93452.12000000"),
    quantity: Decimal = Decimal("0.00170000"),
    first_trade_id: int = 900,
    last_trade_id: int = 902,
    source_timestamp: int = MICROSECOND_TIMESTAMP,
    source_timestamp_unit: SourceTimestampUnit = SourceTimestampUnit.MICROSECOND,
    buyer_is_maker: bool = False,
    best_price_match: bool | None = True,
) -> AggregateTrade:
    return AggregateTrade(
        symbol=symbol,
        aggregate_trade_id=aggregate_trade_id,
        price=price,
        quantity=quantity,
        first_trade_id=first_trade_id,
        last_trade_id=last_trade_id,
        event_time=normalize_source_timestamp(source_timestamp, source_timestamp_unit),
        source_timestamp=source_timestamp,
        source_timestamp_unit=source_timestamp_unit,
        buyer_is_maker=buyer_is_maker,
        best_price_match=best_price_match,
    )


def validated_sequence(
    events: tuple[AggregateTrade, ...],
    *,
    normalizer_version: str = AGGREGATE_TRADE_NORMALIZER_VERSION,
) -> ValidatedAggregateTradeSequence:
    first = events[0]
    identity = AggregateTradeDatasetIdentity(
        symbol=first.symbol,
        requested_start_time=min(event.event_time for event in events),
        requested_end_time_exclusive=max(event.event_time for event in events)
        + timedelta(microseconds=1),
        source_timestamp_unit=first.source_timestamp_unit,
        schema_version="aggregate-trade-v1",
        normalizer_version=normalizer_version,
        provenance="static-binance-provider-fixture",
        research_role=ResearchDatasetRole.DEVELOPMENT,
    )
    return validate_aggregate_trade_sequence(identity, events)


@dataclass(frozen=True, slots=True)
class ConformanceVector:
    source: BinanceAggregateTradeSource
    raw_fixture: tuple[str, ...] | dict[str, object]
    normalization_context: Mapping[str, object]
    expected: AggregateTrade
    expected_timestamp_unit: SourceTimestampUnit
    expected_unavailable_fields: tuple[str, ...]


def normalize_vector(vector: ConformanceVector) -> AggregateTrade:
    if vector.source is BinanceAggregateTradeSource.ARCHIVE:
        assert isinstance(vector.raw_fixture, tuple)
        return normalize_archive_aggregate_trade(
            vector.raw_fixture,
            symbol=vector.normalization_context["symbol"],  # type: ignore[arg-type]
            archive_date=vector.normalization_context["archive_date"],  # type: ignore[arg-type]
        )
    if vector.source is BinanceAggregateTradeSource.REST:
        assert isinstance(vector.raw_fixture, dict)
        return normalize_rest_aggregate_trade(
            vector.raw_fixture,
            symbol=vector.normalization_context["symbol"],  # type: ignore[arg-type]
            source_timestamp_unit=vector.normalization_context["source_timestamp_unit"],  # type: ignore[arg-type]
        )
    assert vector.source is BinanceAggregateTradeSource.WEBSOCKET
    assert isinstance(vector.raw_fixture, dict)
    return normalize_websocket_aggregate_trade(
        vector.raw_fixture,
        source_timestamp_unit=vector.normalization_context["source_timestamp_unit"],  # type: ignore[arg-type]
    )


class ArchiveNormalizerTests(unittest.TestCase):
    def test_archive_schema_has_exact_documented_count_and_order(self) -> None:
        self.assertEqual(
            ARCHIVE_AGGREGATE_TRADE_COLUMNS,
            (
                "aggregate_trade_id",
                "price",
                "quantity",
                "first_trade_id",
                "last_trade_id",
                "timestamp",
                "buyer_is_maker",
                "best_price_match",
            ),
        )

    def test_normalizes_millisecond_archive_before_boundary(self) -> None:
        result = normalize_archive_aggregate_trade(
            archive_row(timestamp=str(MILLISECOND_TIMESTAMP)),
            symbol="BTCUSDT",
            archive_date=MILLISECOND_DATE,
        )
        self.assertEqual(
            result,
            expected_event(
                source_timestamp=MILLISECOND_TIMESTAMP,
                source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
            ),
        )
        self.assertEqual(
            result.event_time,
            datetime(2024, 12, 31, 0, 0, 0, 123000, tzinfo=UTC),
        )

    def test_normalizes_microsecond_archive_at_boundary_exactly(self) -> None:
        result = normalize_archive_aggregate_trade(
            archive_row(), symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        self.assertEqual(result, expected_event())
        self.assertEqual(
            result.event_time,
            datetime(2025, 1, 1, 0, 0, 0, 10866, tzinfo=UTC),
        )
        self.assertEqual(ARCHIVE_MICROSECOND_ERA_START, MICROSECOND_DATE)

    def test_normalizes_eth_archive_without_broadening_symbols(self) -> None:
        result = normalize_archive_aggregate_trade(
            archive_row(), symbol="ETHUSDT", archive_date=MICROSECOND_DATE
        )
        self.assertEqual(result, expected_event(symbol="ETHUSDT"))
        with self.assertRaises(BinanceAggregateTradeNormalizationError):
            normalize_archive_aggregate_trade(
                archive_row(), symbol="SOLUSDT", archive_date=MICROSECOND_DATE
            )

    def test_rejects_bad_column_count_row_type_and_non_string_fields(self) -> None:
        cases: tuple[object, ...] = (
            archive_row()[:-1],
            archive_row() + ("extra",),
            ",".join(archive_row()),
            list(archive_row()[:-1]) + [True],
        )
        for row in cases:
            with self.subTest(row_type=type(row).__name__, length=len(row)):
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_archive_aggregate_trade(  # type: ignore[arg-type]
                        row, symbol="BTCUSDT", archive_date=MICROSECOND_DATE
                    )

    def test_rejects_a_header_instead_of_inferring_one(self) -> None:
        with self.assertRaises(BinanceAggregateTradeNormalizationError):
            normalize_archive_aggregate_trade(
                ARCHIVE_AGGREGATE_TRADE_COLUMNS,
                symbol="BTCUSDT",
                archive_date=MICROSECOND_DATE,
            )

    def test_rejects_malformed_price_tokens_without_coercion(self) -> None:
        for token in (
            "", "NaN", "Infinity", "-1", "+1", "1e3", ".1", "1.",
            "01.0", " 1.0", "1.0 ", "1\x00.0", 1.0,
        ):
            with self.subTest(token=token):
                row = list(archive_row())
                row[1] = token  # type: ignore[list-item]
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_archive_aggregate_trade(
                        row, symbol="BTCUSDT", archive_date=MICROSECOND_DATE
                    )


class RestNormalizerTests(unittest.TestCase):
    def test_maps_documented_rest_fields_in_millisecond_mode(self) -> None:
        result = normalize_rest_aggregate_trade(
            rest_payload(timestamp=MILLISECOND_TIMESTAMP),
            symbol="BTCUSDT",
            source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
        )
        self.assertEqual(
            result,
            expected_event(
                source_timestamp=MILLISECOND_TIMESTAMP,
                source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
            ),
        )

    def test_maps_documented_rest_fields_in_microsecond_mode(self) -> None:
        result = normalize_rest_aggregate_trade(
            rest_payload(),
            symbol="BTCUSDT",
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )
        self.assertEqual(result, expected_event())

    def test_requires_explicit_exact_rest_timestamp_unit(self) -> None:
        for unit in (None, "millisecond", "microsecond"):
            with self.subTest(unit=unit):
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_rest_aggregate_trade(
                        rest_payload(),
                        symbol="BTCUSDT",
                        source_timestamp_unit=unit,  # type: ignore[arg-type]
                    )

    def test_rest_payload_requires_exact_documented_fields_and_dict_type(self) -> None:
        missing = rest_payload()
        missing.pop("M")
        extra = rest_payload()
        extra["x"] = 1
        for payload in (missing, extra, list(rest_payload().items())):
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_rest_aggregate_trade(
                        payload,  # type: ignore[arg-type]
                        symbol="BTCUSDT",
                        source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                    )

    def test_rest_rejects_non_exact_integer_fields(self) -> None:
        for field_name in ("a", "f", "l", "T"):
            for invalid in (True, 1.0, "1", -1):
                with self.subTest(field=field_name, invalid=invalid):
                    payload = rest_payload()
                    payload[field_name] = invalid
                    with self.assertRaises(BinanceAggregateTradeNormalizationError):
                        normalize_rest_aggregate_trade(
                            payload,
                            symbol="BTCUSDT",
                            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                        )

    def test_rest_rejects_malformed_decimal_and_boolean_fields(self) -> None:
        invalid_fields = (
            ("p", 1),
            ("p", "1e3"),
            ("q", "NaN"),
            ("q", "00.1"),
            ("m", 0),
            ("M", "true"),
        )
        for field_name, invalid in invalid_fields:
            with self.subTest(field=field_name, invalid=invalid):
                payload = rest_payload()
                payload[field_name] = invalid
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_rest_aggregate_trade(
                        payload,
                        symbol="BTCUSDT",
                        source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                    )

    def test_rest_preserves_best_price_match_when_present(self) -> None:
        for value in (True, False):
            with self.subTest(value=value):
                result = normalize_rest_aggregate_trade(
                    rest_payload(best_price_match=value),
                    symbol="BTCUSDT",
                    source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                )
                self.assertIs(result.best_price_match, value)


class WebSocketNormalizerTests(unittest.TestCase):
    def test_maps_trade_occurrence_time_without_conflating_event_time(self) -> None:
        payload = websocket_payload(
            event_timestamp=MICROSECOND_TIMESTAMP + 999,
            trade_timestamp=MICROSECOND_TIMESTAMP,
        )
        result = normalize_websocket_aggregate_trade(
            payload, source_timestamp_unit=SourceTimestampUnit.MICROSECOND
        )
        self.assertEqual(result, expected_event(best_price_match=None))
        self.assertEqual(result.source_timestamp, payload["T"])
        self.assertNotEqual(result.source_timestamp, payload["E"])
        self.assertNotIn(
            "provider_event_time", {field.name for field in fields(AggregateTrade)}
        )
        self.assertNotIn(
            "ingestion_time", {field.name for field in fields(AggregateTrade)}
        )

    def test_websocket_validates_event_time_under_explicit_unit(self) -> None:
        payload = websocket_payload(event_timestamp=-1)
        with self.assertRaisesRegex(
            BinanceAggregateTradeNormalizationError, "E must be non-negative"
        ):
            normalize_websocket_aggregate_trade(
                payload, source_timestamp_unit=SourceTimestampUnit.MICROSECOND
            )

    def test_websocket_m_field_is_validated_but_never_invented_as_best_match(self) -> None:
        for ignored_value in (True, False):
            with self.subTest(ignored_value=ignored_value):
                result = normalize_websocket_aggregate_trade(
                    websocket_payload(ignored_best_match_field=ignored_value),
                    source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                )
                self.assertIsNone(result.best_price_match)
        invalid = websocket_payload()
        invalid["M"] = None
        with self.assertRaises(BinanceAggregateTradeNormalizationError):
            normalize_websocket_aggregate_trade(
                invalid, source_timestamp_unit=SourceTimestampUnit.MICROSECOND
            )

    def test_websocket_requires_exact_event_type_symbol_and_field_set(self) -> None:
        invalid_payloads = []
        wrong_event = websocket_payload()
        wrong_event["e"] = "trade"
        invalid_payloads.append(wrong_event)
        invalid_payloads.append(websocket_payload(symbol="SOLUSDT"))
        missing = websocket_payload()
        missing.pop("E")
        invalid_payloads.append(missing)
        extra = websocket_payload()
        extra["x"] = 1
        invalid_payloads.append(extra)
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_websocket_aggregate_trade(
                        payload,
                        source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                    )


class CrossSourceConformanceTests(unittest.TestCase):
    def test_static_conformance_vectors_normalize_to_expected_events(self) -> None:
        vectors = (
            ConformanceVector(
                BinanceAggregateTradeSource.ARCHIVE,
                archive_row(timestamp=str(MILLISECOND_TIMESTAMP)),
                {"symbol": "BTCUSDT", "archive_date": MILLISECOND_DATE},
                expected_event(
                    source_timestamp=MILLISECOND_TIMESTAMP,
                    source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
                ),
                SourceTimestampUnit.MILLISECOND,
                (),
            ),
            ConformanceVector(
                BinanceAggregateTradeSource.ARCHIVE,
                archive_row(),
                {"symbol": "BTCUSDT", "archive_date": MICROSECOND_DATE},
                expected_event(),
                SourceTimestampUnit.MICROSECOND,
                (),
            ),
            ConformanceVector(
                BinanceAggregateTradeSource.ARCHIVE,
                archive_row(buyer_is_maker="True", best_price_match="False"),
                {"symbol": "ETHUSDT", "archive_date": MICROSECOND_DATE},
                expected_event(
                    symbol="ETHUSDT",
                    buyer_is_maker=True,
                    best_price_match=False,
                ),
                SourceTimestampUnit.MICROSECOND,
                (),
            ),
            ConformanceVector(
                BinanceAggregateTradeSource.REST,
                rest_payload(timestamp=MILLISECOND_TIMESTAMP),
                {
                    "symbol": "BTCUSDT",
                    "source_timestamp_unit": SourceTimestampUnit.MILLISECOND,
                },
                expected_event(
                    source_timestamp=MILLISECOND_TIMESTAMP,
                    source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
                ),
                SourceTimestampUnit.MILLISECOND,
                (),
            ),
            ConformanceVector(
                BinanceAggregateTradeSource.REST,
                rest_payload(),
                {
                    "symbol": "BTCUSDT",
                    "source_timestamp_unit": SourceTimestampUnit.MICROSECOND,
                },
                expected_event(),
                SourceTimestampUnit.MICROSECOND,
                (),
            ),
            ConformanceVector(
                BinanceAggregateTradeSource.WEBSOCKET,
                websocket_payload(),
                {"source_timestamp_unit": SourceTimestampUnit.MICROSECOND},
                expected_event(best_price_match=None),
                SourceTimestampUnit.MICROSECOND,
                ("best_price_match",),
            ),
        )
        for vector in vectors:
            with self.subTest(source=vector.source, expected=vector.expected):
                result = normalize_vector(vector)
                self.assertEqual(result, vector.expected)
                self.assertIs(
                    result.source_timestamp_unit, vector.expected_timestamp_unit
                )
                for unavailable in vector.expected_unavailable_fields:
                    self.assertIsNone(getattr(result, unavailable))

    def test_equivalent_archive_and_rest_are_exactly_equal(self) -> None:
        archive = normalize_archive_aggregate_trade(
            archive_row(), symbol="BTCUSDT", archive_date=MICROSECOND_DATE
        )
        rest = normalize_rest_aggregate_trade(
            rest_payload(),
            symbol="BTCUSDT",
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )
        self.assertEqual(archive, rest)

    def test_websocket_is_equal_only_on_semantically_shared_fields(self) -> None:
        rest = normalize_rest_aggregate_trade(
            rest_payload(),
            symbol="BTCUSDT",
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )
        websocket = normalize_websocket_aggregate_trade(
            websocket_payload(),
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )
        self.assertNotEqual(rest, websocket)
        self.assertEqual(replace(rest, best_price_match=None), websocket)

    def test_buyer_maker_flag_proves_taker_aggressor_direction(self) -> None:
        for buyer_is_maker, expected_side in (
            (False, AggressorSide.BUY),
            (True, AggressorSide.SELL),
        ):
            with self.subTest(buyer_is_maker=buyer_is_maker):
                result = normalize_rest_aggregate_trade(
                    rest_payload(buyer_is_maker=buyer_is_maker),
                    symbol="BTCUSDT",
                    source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                )
                self.assertIs(result.aggressor_side, expected_side)

    def test_same_timestamp_different_ids_and_multi_trade_range_are_preserved(self) -> None:
        first = normalize_rest_aggregate_trade(
            rest_payload(aggregate_trade_id=700),
            symbol="BTCUSDT",
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )
        second = normalize_rest_aggregate_trade(
            rest_payload(
                aggregate_trade_id=701,
                first_trade_id=903,
                last_trade_id=910,
            ),
            symbol="BTCUSDT",
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )
        result = validated_sequence((first, second))
        self.assertEqual(
            [event.aggregate_trade_id for event in result.events], [700, 701]
        )
        self.assertEqual((second.first_trade_id, second.last_trade_id), (903, 910))

    def test_non_contiguous_ids_are_accepted_without_claiming_completeness(self) -> None:
        first = expected_event(aggregate_trade_id=700)
        second = expected_event(
            aggregate_trade_id=999,
            source_timestamp=MICROSECOND_TIMESTAMP + 1,
        )
        result = validated_sequence((first, second))
        self.assertEqual(
            tuple(event.aggregate_trade_id for event in result.events), (700, 999)
        )

    def test_no_sorting_or_deduplication_is_hidden_in_normalization(self) -> None:
        first = expected_event(aggregate_trade_id=700)
        second = expected_event(
            aggregate_trade_id=701,
            source_timestamp=MICROSECOND_TIMESTAMP + 1,
        )
        with self.assertRaisesRegex(AggregateTradeValidationError, "not sorted"):
            validated_sequence((second, first))
        with self.assertRaisesRegex(AggregateTradeValidationError, "duplicate"):
            validated_sequence((first, first))

    def test_normalization_is_deterministic_and_does_not_mutate_fixtures(self) -> None:
        archive_fixture = archive_row()
        rest_fixture = rest_payload()
        websocket_fixture = websocket_payload()
        originals = deepcopy((archive_fixture, rest_fixture, websocket_fixture))
        archive_results = tuple(
            normalize_archive_aggregate_trade(
                archive_fixture,
                symbol="BTCUSDT",
                archive_date=MICROSECOND_DATE,
            )
            for _ in range(2)
        )
        rest_results = tuple(
            normalize_rest_aggregate_trade(
                rest_fixture,
                symbol="BTCUSDT",
                source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
            )
            for _ in range(2)
        )
        websocket_results = tuple(
            normalize_websocket_aggregate_trade(
                websocket_fixture,
                source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
            )
            for _ in range(2)
        )
        self.assertEqual(archive_results[0], archive_results[1])
        self.assertEqual(rest_results[0], rest_results[1])
        self.assertEqual(websocket_results[0], websocket_results[1])
        self.assertEqual((archive_fixture, rest_fixture, websocket_fixture), originals)


class ContentIdentityTests(unittest.TestCase):
    def test_raw_hash_is_exact_byte_identity(self) -> None:
        content = b"700,93452.12000000,0.00170000,900,902,1735689600010866,False,True\n"
        self.assertEqual(raw_content_sha256(content), hashlib.sha256(content).hexdigest())
        self.assertNotEqual(raw_content_sha256(content), raw_content_sha256(content + b"\n"))
        with self.assertRaises(TypeError):
            raw_content_sha256(bytearray(content))  # type: ignore[arg-type]

    def test_canonical_sequence_bytes_and_hash_reproduce_exactly(self) -> None:
        sequence = validated_sequence((expected_event(),))
        first_bytes = canonical_aggregate_trade_sequence_bytes(sequence)
        second_bytes = canonical_aggregate_trade_sequence_bytes(sequence)
        first_hash = canonical_aggregate_trade_sequence_sha256(sequence)
        self.assertEqual(first_bytes, second_bytes)
        self.assertTrue(first_bytes.endswith(b"\n"))
        self.assertEqual(first_hash, hashlib.sha256(first_bytes).hexdigest())
        self.assertEqual(first_hash, canonical_aggregate_trade_sequence_sha256(sequence))

    def test_normalizer_version_is_bound_into_canonical_content_identity(self) -> None:
        events = (expected_event(),)
        version_one = validated_sequence(events)
        version_two = validated_sequence(
            events,
            normalizer_version="binance-spot-aggregate-trade-normalizer-v2",
        )
        self.assertNotEqual(
            canonical_aggregate_trade_sequence_bytes(version_one),
            canonical_aggregate_trade_sequence_bytes(version_two),
        )
        self.assertNotEqual(
            canonical_aggregate_trade_sequence_sha256(version_one),
            canonical_aggregate_trade_sequence_sha256(version_two),
        )

    def test_raw_and_canonical_hashes_are_separate_identities(self) -> None:
        raw = b"700,93452.12000000,0.00170000,900,902,1735689600010866,False,True\n"
        sequence = validated_sequence((expected_event(),))
        self.assertNotEqual(
            raw_content_sha256(raw),
            canonical_aggregate_trade_sequence_sha256(sequence),
        )

    def test_canonical_hash_revalidates_instead_of_trusting_forged_wrapper(self) -> None:
        valid = validated_sequence((expected_event(),))
        forged = object.__new__(ValidatedAggregateTradeSequence)
        object.__setattr__(forged, "identity", valid.identity)
        object.__setattr__(forged, "events", (object(),))
        with self.assertRaises(BinanceAggregateTradeNormalizationError):
            canonical_aggregate_trade_sequence_bytes(forged)
        with self.assertRaises(TypeError):
            canonical_aggregate_trade_sequence_bytes(object())  # type: ignore[arg-type]


class ScopeAndArchitectureTests(unittest.TestCase):
    def test_normalizer_has_no_network_file_persistence_or_clock_dependency(self) -> None:
        source = NORMALIZER_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(NORMALIZER_SOURCE))
        forbidden_modules = {
            "aiohttp",
            "duckdb",
            "http",
            "httpx",
            "os",
            "pathlib",
            "pyarrow",
            "requests",
            "socket",
            "urllib",
            "websockets",
        }
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"now", "utcnow", "today"})
        self.assertTrue(imported_roots.isdisjoint(forbidden_modules))

    def test_existing_candle_contract_field_shapes_are_unchanged(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(Candle)),
            (
                "symbol",
                "interval",
                "open_time",
                "close_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
                "trade_count",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(DatasetIdentity)),
            (
                "symbol",
                "timeframe",
                "start_time",
                "end_time",
                "source",
                "schema_version",
                "ingestion_version",
                "validation_status",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(MarketEvent)),
            ("timestamp", "candle"),
        )


class AdditionalNormalizerAdversarialTests(unittest.TestCase):

    def test_websocket_supports_explicit_millisecond_and_microsecond_contexts(self) -> None:
        millisecond = normalize_websocket_aggregate_trade(
            websocket_payload(
                event_timestamp=MILLISECOND_TIMESTAMP + 1,
                trade_timestamp=MILLISECOND_TIMESTAMP,
            ),
            source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
        )
        microsecond = normalize_websocket_aggregate_trade(
            websocket_payload(),
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )
        self.assertIs(
            millisecond.source_timestamp_unit, SourceTimestampUnit.MILLISECOND
        )
        self.assertIs(
            microsecond.source_timestamp_unit, SourceTimestampUnit.MICROSECOND
        )
        for invalid_unit in (None, "microsecond"):
            with self.assertRaises(BinanceAggregateTradeNormalizationError):
                normalize_websocket_aggregate_trade(
                    websocket_payload(),
                    source_timestamp_unit=invalid_unit,  # type: ignore[arg-type]
                )

    def test_websocket_rejects_malformed_integer_decimal_and_maker_fields(self) -> None:
        invalid_fields = (
            ("a", True),
            ("f", "900"),
            ("l", -1),
            ("E", 1.0),
            ("T", "1"),
            ("p", "NaN"),
            ("q", "1e-3"),
            ("m", 0),
        )
        for field_name, invalid in invalid_fields:
            with self.subTest(field=field_name, invalid=invalid):
                payload = websocket_payload()
                payload[field_name] = invalid
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_websocket_aggregate_trade(
                        payload,
                        source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                    )

    def test_rejects_malformed_quantity_tokens_without_coercion(self) -> None:
        for token in ("", "NaN", "-0.1", "1e-3", "00.1", "\t1", False):
            with self.subTest(token=token):
                row = list(archive_row())
                row[2] = token  # type: ignore[list-item]
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    normalize_archive_aggregate_trade(
                        row, symbol="BTCUSDT", archive_date=MICROSECOND_DATE
                    )


    def test_rejects_malformed_archive_integer_tokens(self) -> None:
        for index in (0, 3, 4, 5):
            for token in ("", "-1", "+1", "1.0", "1e3", "01", " 1", "1\n"):
                with self.subTest(index=index, token=token):
                    row = list(archive_row())
                    row[index] = token
                    with self.assertRaises(BinanceAggregateTradeNormalizationError):
                        normalize_archive_aggregate_trade(
                            row, symbol="BTCUSDT", archive_date=MICROSECOND_DATE
                        )

    def test_rejects_malformed_archive_booleans(self) -> None:
        for index in (6, 7):
            for token in ("true", "false", "TRUE", "0", "1", "False ", "True\x00"):
                with self.subTest(index=index, token=token):
                    row = list(archive_row())
                    row[index] = token
                    with self.assertRaises(BinanceAggregateTradeNormalizationError):
                        normalize_archive_aggregate_trade(
                            row, symbol="BTCUSDT", archive_date=MICROSECOND_DATE
                        )

    def test_archive_date_must_be_exact_trustworthy_metadata(self) -> None:
        class DerivedDate(date):
            pass

        for value in (
            "2025-01-01", datetime(2025, 1, 1, tzinfo=UTC),
            DerivedDate(2025, 1, 1), None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(BinanceAggregateTradeNormalizationError):
                    archive_timestamp_unit(value)  # type: ignore[arg-type]

    def test_archive_unit_is_selected_only_from_source_date(self) -> None:
        self.assertIs(
            archive_timestamp_unit(MILLISECOND_DATE), SourceTimestampUnit.MILLISECOND
        )
        self.assertIs(
            archive_timestamp_unit(MICROSECOND_DATE), SourceTimestampUnit.MICROSECOND
        )
        wrong_era_rows = (
            (MILLISECOND_DATE, archive_row(timestamp=str(MICROSECOND_TIMESTAMP))),
            (MICROSECOND_DATE, archive_row(timestamp=str(MILLISECOND_TIMESTAMP))),
        )
        for source_date, row in wrong_era_rows:
            with self.subTest(source_date=source_date):
                with self.assertRaisesRegex(
                    BinanceAggregateTradeNormalizationError,
                    "timestamp|archive_date",
                ):
                    normalize_archive_aggregate_trade(
                        row, symbol="BTCUSDT", archive_date=source_date
                    )

    def test_boundary_day_before_and_boundary_day_have_distinct_units(self) -> None:
        self.assertIs(
            archive_timestamp_unit(date(2024, 12, 31)),
            SourceTimestampUnit.MILLISECOND,
        )
        self.assertIs(
            archive_timestamp_unit(date(2025, 1, 1)),
            SourceTimestampUnit.MICROSECOND,
        )

    def test_wraps_domain_contract_failures(self) -> None:
        cases = (
            archive_row(price="0"),
            archive_row(quantity="0"),
            archive_row(first_trade_id="903", last_trade_id="902"),
        )
        for row in cases:
            with self.subTest(row=row):
                with self.assertRaisesRegex(
                    BinanceAggregateTradeNormalizationError, "canonical contract"
                ):
                    normalize_archive_aggregate_trade(
                        row, symbol="BTCUSDT", archive_date=MICROSECOND_DATE
                    )


if __name__ == "__main__":
    unittest.main()
