"""Adversarial tests for the research-only aggregate-trade contracts."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import unittest

import quantos.domain.market_data as production_market_data
from quantos.domain.market_data import Candle, DatasetIdentity, MarketEvent
from quantos.domain.market_data.research_events import (
    AggregateTrade,
    AggregateTradeDatasetIdentity,
    AggregateTradeValidationError,
    AggressorSide,
    ResearchDatasetRole,
    ResearchEventValidationStatus,
    SourceTimestampUnit,
    ValidatedAggregateTradeSequence,
    aggregate_trade_dataset_id,
    aggregate_trade_dataset_identity_bytes,
    normalize_source_timestamp,
    validate_aggregate_trade_sequence,
)

UTC = timezone.utc
BASE_MS = 1_735_689_600_000
BASE_TIME = datetime(2025, 1, 1, tzinfo=UTC)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "quantos" / "domain" / "market_data"
RESEARCH_SOURCE = SOURCE_ROOT / "research_events"


def aggregate_trade(
    aggregate_trade_id: int = 100,
    *,
    symbol: str = "BTCUSDT",
    source_timestamp: int = BASE_MS,
    source_timestamp_unit: SourceTimestampUnit = SourceTimestampUnit.MILLISECOND,
    price: Decimal = Decimal("93452.120000000000000001"),
    quantity: Decimal = Decimal("0.000170000000000009"),
    first_trade_id: int = 900,
    last_trade_id: int = 902,
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
        event_time=normalize_source_timestamp(
            source_timestamp, source_timestamp_unit
        ),
        source_timestamp=source_timestamp,
        source_timestamp_unit=source_timestamp_unit,
        buyer_is_maker=buyer_is_maker,
        best_price_match=best_price_match,
    )


def dataset_identity(**overrides: object) -> AggregateTradeDatasetIdentity:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "requested_start_time": BASE_TIME,
        "requested_end_time_exclusive": BASE_TIME + timedelta(seconds=10),
        "source_timestamp_unit": SourceTimestampUnit.MILLISECOND,
        "schema_version": "aggregate-trade-v1",
        "normalizer_version": "normalizer-v1",
        "provenance": "static-test-fixture",
        "research_role": ResearchDatasetRole.DEVELOPMENT,
    }
    values.update(overrides)
    return AggregateTradeDatasetIdentity(**values)  # type: ignore[arg-type]


class AggregateTradeContractTests(unittest.TestCase):
    def test_accepts_both_v1_symbols_and_preserves_decimal_precision(self) -> None:
        btc = aggregate_trade()
        eth = aggregate_trade(symbol="ETHUSDT")

        self.assertEqual(btc.symbol, "BTCUSDT")
        self.assertEqual(eth.symbol, "ETHUSDT")
        self.assertEqual(btc.price, Decimal("93452.120000000000000001"))
        self.assertEqual(btc.quantity, Decimal("0.000170000000000009"))

    def test_rejects_every_symbol_outside_the_frozen_v1_universe(self) -> None:
        for symbol in ("SOLUSDT", "btcusdt", "", 1):
            with self.subTest(symbol=symbol):
                with self.assertRaises((TypeError, ValueError)):
                    aggregate_trade(symbol=symbol)  # type: ignore[arg-type]

    def test_aggressor_side_mapping_is_exhaustive_and_explicit(self) -> None:
        self.assertIs(
            aggregate_trade(buyer_is_maker=False).aggressor_side,
            AggressorSide.BUY,
        )
        self.assertIs(
            aggregate_trade(buyer_is_maker=True).aggressor_side,
            AggressorSide.SELL,
        )

    def test_best_price_match_is_optional_without_fabricating_a_value(self) -> None:
        self.assertIsNone(aggregate_trade(best_price_match=None).best_price_match)
        self.assertTrue(aggregate_trade(best_price_match=True).best_price_match)
        self.assertFalse(aggregate_trade(best_price_match=False).best_price_match)

    def test_rejects_non_decimal_and_non_positive_price_or_quantity(self) -> None:
        cases = (
            ("price", 1),
            ("price", 1.0),
            ("price", Decimal("0")),
            ("price", Decimal("-0.1")),
            ("quantity", "1"),
            ("quantity", Decimal("0")),
            ("quantity", Decimal("-0.1")),
        )
        for field_name, value in cases:
            with self.subTest(field=field_name, value=value):
                with self.assertRaises((TypeError, ValueError)):
                    replace(aggregate_trade(), **{field_name: value})

    def test_rejects_non_finite_decimals(self) -> None:
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            for field_name in ("price", "quantity"):
                with self.subTest(field=field_name, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        replace(aggregate_trade(), **{field_name: value})

    def test_rejects_boolean_non_integer_and_negative_identifiers(self) -> None:
        for field_name in (
            "aggregate_trade_id",
            "first_trade_id",
            "last_trade_id",
        ):
            for value in (True, 1.0, "1", -1):
                with self.subTest(field=field_name, value=value):
                    with self.assertRaises((TypeError, ValueError)):
                        replace(aggregate_trade(), **{field_name: value})

    def test_rejects_reversed_underlying_trade_identifier_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "first_trade_id"):
            aggregate_trade(first_trade_id=903, last_trade_id=902)

    def test_rejects_non_boolean_maker_flags(self) -> None:
        for field_name in ("buyer_is_maker", "best_price_match"):
            for value in (0, 1, "false"):
                with self.subTest(field=field_name, value=value):
                    with self.assertRaisesRegex(TypeError, "bool"):
                        replace(aggregate_trade(), **{field_name: value})

    def test_event_is_immutable(self) -> None:
        event = aggregate_trade()

        with self.assertRaises(FrozenInstanceError):
            setattr(event, "price", Decimal("1"))

    def test_contract_does_not_invent_individual_trade_or_clock_semantics(self) -> None:
        field_names = {field.name for field in fields(AggregateTrade)}

        self.assertNotIn("individual_trade_count", field_names)
        self.assertNotIn("trades", field_names)
        self.assertNotIn("provider_emission_time", field_names)
        self.assertNotIn("ingestion_time", field_names)
        self.assertNotIn("timeframe", field_names)


class TimestampSemanticsTests(unittest.TestCase):
    def test_millisecond_normalization_is_exact(self) -> None:
        self.assertEqual(
            normalize_source_timestamp(
                1_735_689_600_123, SourceTimestampUnit.MILLISECOND
            ),
            datetime(2025, 1, 1, 0, 0, 0, 123000, tzinfo=UTC),
        )

    def test_microsecond_normalization_is_exact(self) -> None:
        self.assertEqual(
            normalize_source_timestamp(
                1_735_689_600_010_866, SourceTimestampUnit.MICROSECOND
            ),
            datetime(2025, 1, 1, 0, 0, 0, 10866, tzinfo=UTC),
        )

    def test_timestamp_unit_is_never_inferred_from_integer_magnitude(self) -> None:
        raw_value = 1_000_000

        millisecond_time = normalize_source_timestamp(
            raw_value, SourceTimestampUnit.MILLISECOND
        )
        microsecond_time = normalize_source_timestamp(
            raw_value, SourceTimestampUnit.MICROSECOND
        )

        self.assertEqual(millisecond_time - microsecond_time, timedelta(seconds=999))

    def test_rejects_missing_or_string_timestamp_units(self) -> None:
        for unit in (None, "millisecond", "microsecond"):
            with self.subTest(unit=unit):
                with self.assertRaises(TypeError):
                    normalize_source_timestamp(1, unit)  # type: ignore[arg-type]

    def test_rejects_invalid_raw_timestamp_types_values_and_overflow(self) -> None:
        for value in (True, 1.0, "1", -1, 10**30):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    normalize_source_timestamp(
                        value, SourceTimestampUnit.MILLISECOND  # type: ignore[arg-type]
                    )

    def test_event_rejects_raw_to_utc_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly match"):
            replace(
                aggregate_trade(),
                event_time=BASE_TIME + timedelta(microseconds=1),
            )

    def test_event_rejects_naive_and_non_utc_times(self) -> None:
        invalid_times = (
            BASE_TIME.replace(tzinfo=None),
            BASE_TIME.astimezone(timezone(timedelta(hours=7))),
        )
        for value in invalid_times:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "UTC"):
                    replace(aggregate_trade(), event_time=value)

    def test_same_integer_with_different_explicit_unit_is_not_interchangeable(self) -> None:
        millisecond_event = aggregate_trade()
        with self.assertRaisesRegex(ValueError, "exactly match"):
            replace(
                millisecond_event,
                source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
            )


class AggregateTradeDatasetIdentityTests(unittest.TestCase):
    def test_identity_binds_fixed_provider_market_event_and_boundary_semantics(self) -> None:
        identity = dataset_identity()

        self.assertEqual(identity.provider, "binance")
        self.assertEqual(identity.market, "spot")
        self.assertEqual(identity.event_family, "aggregate_trade")
        self.assertEqual(identity.event_granularity, "event")
        self.assertEqual(
            identity.boundary_convention,
            "[requested_start_time,requested_end_time_exclusive)",
        )
        self.assertNotIn("timeframe", {field.name for field in fields(identity)})
        self.assertIs(
            identity.validation_status,
            ResearchEventValidationStatus.UNVALIDATED,
        )

    def test_identity_rejects_invalid_range_and_timezones(self) -> None:
        invalid_cases = (
            {"requested_end_time_exclusive": BASE_TIME},
            {"requested_end_time_exclusive": BASE_TIME - timedelta(seconds=1)},
            {"requested_start_time": BASE_TIME.replace(tzinfo=None)},
            {
                "requested_end_time_exclusive": datetime(
                    2025, 1, 2, tzinfo=timezone(timedelta(hours=1))
                )
            },
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    dataset_identity(**overrides)

    def test_identity_rejects_invalid_versions_provenance_and_enums(self) -> None:
        invalid_cases = (
            {"schema_version": ""},
            {"normalizer_version": "  "},
            {"provenance": ""},
            {"source_timestamp_unit": "millisecond"},
            {"research_role": "development"},
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises((TypeError, ValueError)):
                    dataset_identity(**overrides)

    def test_identity_cannot_be_constructed_directly_as_validated(self) -> None:
        with self.assertRaises(TypeError):
            AggregateTradeDatasetIdentity(
                symbol="BTCUSDT",
                requested_start_time=BASE_TIME,
                requested_end_time_exclusive=BASE_TIME + timedelta(seconds=1),
                source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
                schema_version="v1",
                normalizer_version="v1",
                provenance="fixture",
                research_role=ResearchDatasetRole.DEVELOPMENT,
                validation_status=ResearchEventValidationStatus.VALIDATED,  # type: ignore[call-arg]
            )

    def test_canonical_identity_bytes_are_frozen_and_newline_terminated(self) -> None:
        expected = (
            b'{"boundary_convention":"[requested_start_time,'
            b'requested_end_time_exclusive)","event_family":"aggregate_trade",'
            b'"event_granularity":"event","market":"spot",'
            b'"normalizer_version":"normalizer-v1",'
            b'"provenance":"static-test-fixture","provider":"binance",'
            b'"requested_end_time_exclusive":"2025-01-01T00:00:10.000000Z",'
            b'"requested_start_time":"2025-01-01T00:00:00.000000Z",'
            b'"research_role":"development",'
            b'"schema_version":"aggregate-trade-v1",'
            b'"source_timestamp_unit":"millisecond","symbol":"BTCUSDT",'
            b'"validation_status":"unvalidated"}\n'
        )

        actual = aggregate_trade_dataset_identity_bytes(dataset_identity())

        self.assertEqual(actual, expected)
        self.assertEqual(
            aggregate_trade_dataset_id(dataset_identity()),
            hashlib.sha256(expected).hexdigest(),
        )

    def test_each_configurable_identity_field_changes_the_dataset_id(self) -> None:
        baseline = dataset_identity()
        variants = (
            replace(baseline, symbol="ETHUSDT"),
            replace(
                baseline,
                requested_start_time=baseline.requested_start_time
                - timedelta(seconds=1),
            ),
            replace(
                baseline,
                requested_end_time_exclusive=baseline.requested_end_time_exclusive
                + timedelta(seconds=1),
            ),
            replace(
                baseline,
                source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
            ),
            replace(baseline, schema_version="aggregate-trade-v2"),
            replace(baseline, normalizer_version="normalizer-v2"),
            replace(baseline, provenance="different-static-fixture"),
            replace(baseline, research_role=ResearchDatasetRole.SCREENING_VALIDATION),
        )

        baseline_id = aggregate_trade_dataset_id(baseline)
        self.assertEqual(len({aggregate_trade_dataset_id(item) for item in variants}), 8)
        self.assertTrue(
            all(aggregate_trade_dataset_id(item) != baseline_id for item in variants)
        )

    def test_semantically_equal_utc_inputs_have_identical_identity(self) -> None:
        baseline = dataset_identity()
        equivalent_utc = timezone(timedelta(0), name="Equivalent UTC")
        equivalent = replace(
            baseline,
            requested_start_time=baseline.requested_start_time.astimezone(
                equivalent_utc
            ),
            requested_end_time_exclusive=(
                baseline.requested_end_time_exclusive.astimezone(equivalent_utc)
            ),
        )

        self.assertEqual(
            aggregate_trade_dataset_identity_bytes(baseline),
            aggregate_trade_dataset_identity_bytes(equivalent),
        )
        self.assertEqual(
            aggregate_trade_dataset_id(baseline),
            aggregate_trade_dataset_id(equivalent),
        )

    def test_validation_lifecycle_state_changes_the_dataset_id(self) -> None:
        unvalidated = dataset_identity()
        validated = validate_aggregate_trade_sequence(
            unvalidated, [aggregate_trade()]
        ).identity

        self.assertNotEqual(
            aggregate_trade_dataset_id(unvalidated),
            aggregate_trade_dataset_id(validated),
        )

    def test_identity_is_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            setattr(dataset_identity(), "provenance", "changed")


class AggregateTradeSequenceValidationTests(unittest.TestCase):
    def test_validates_without_mutating_input_and_promotes_a_new_identity(self) -> None:
        source_identity = dataset_identity()
        source_events = [
            aggregate_trade(100, source_timestamp=BASE_MS),
            aggregate_trade(101, source_timestamp=BASE_MS + 1),
        ]

        result = validate_aggregate_trade_sequence(source_identity, source_events)

        self.assertEqual(result.events, tuple(source_events))
        self.assertIsInstance(result.events, tuple)
        self.assertIs(
            result.identity.validation_status,
            ResearchEventValidationStatus.VALIDATED,
        )
        self.assertIs(
            source_identity.validation_status,
            ResearchEventValidationStatus.UNVALIDATED,
        )
        self.assertIsNot(result.identity, source_identity)
        self.assertEqual(result.observed_start_time, source_events[0].event_time)
        self.assertEqual(result.observed_end_time, source_events[-1].event_time)

    def test_validation_accepts_each_symbol_under_its_own_identity(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                result = validate_aggregate_trade_sequence(
                    dataset_identity(symbol=symbol),
                    [aggregate_trade(symbol=symbol)],
                )
                self.assertEqual(result.identity.symbol, symbol)

    def test_rejects_empty_sequence(self) -> None:
        with self.assertRaisesRegex(AggregateTradeValidationError, "at least one"):
            validate_aggregate_trade_sequence(dataset_identity(), [])

    def test_rejects_mixed_symbol_sequence(self) -> None:
        events = [
            aggregate_trade(100),
            aggregate_trade(101, symbol="ETHUSDT", source_timestamp=BASE_MS + 1),
        ]

        with self.assertRaisesRegex(AggregateTradeValidationError, "symbol"):
            validate_aggregate_trade_sequence(dataset_identity(), events)

    def test_rejects_timestamp_unit_mismatch(self) -> None:
        event = aggregate_trade(
            source_timestamp=BASE_MS * 1000,
            source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        )

        with self.assertRaisesRegex(AggregateTradeValidationError, "timestamp unit"):
            validate_aggregate_trade_sequence(dataset_identity(), [event])

    def test_half_open_requested_coverage_includes_start_and_excludes_end(self) -> None:
        start_event = aggregate_trade(source_timestamp=BASE_MS)
        end_event = aggregate_trade(source_timestamp=BASE_MS + 10_000)

        self.assertEqual(
            validate_aggregate_trade_sequence(dataset_identity(), [start_event]).events,
            (start_event,),
        )
        with self.assertRaisesRegex(AggregateTradeValidationError, "half-open"):
            validate_aggregate_trade_sequence(dataset_identity(), [end_event])

    def test_rejects_identical_duplicate_aggregate_trade_ids(self) -> None:
        event = aggregate_trade()

        with self.assertRaisesRegex(AggregateTradeValidationError, "duplicate"):
            validate_aggregate_trade_sequence(dataset_identity(), [event, event])

    def test_rejects_conflicting_duplicate_aggregate_trade_ids(self) -> None:
        events = [
            aggregate_trade(100),
            aggregate_trade(100, source_timestamp=BASE_MS + 1),
        ]

        with self.assertRaisesRegex(AggregateTradeValidationError, "conflicting"):
            validate_aggregate_trade_sequence(dataset_identity(), events)

    def test_rejects_chronological_regression_without_sorting(self) -> None:
        events = [
            aggregate_trade(101, source_timestamp=BASE_MS + 1),
            aggregate_trade(100, source_timestamp=BASE_MS),
        ]

        with self.assertRaisesRegex(AggregateTradeValidationError, "strictly ordered"):
            validate_aggregate_trade_sequence(dataset_identity(), events)
        self.assertEqual(events[0].aggregate_trade_id, 101)

    def test_same_timestamp_requires_ascending_aggregate_trade_id(self) -> None:
        valid = [aggregate_trade(100), aggregate_trade(101)]
        invalid = list(reversed(valid))

        self.assertEqual(
            validate_aggregate_trade_sequence(dataset_identity(), valid).events,
            tuple(valid),
        )
        with self.assertRaisesRegex(AggregateTradeValidationError, "strictly ordered"):
            validate_aggregate_trade_sequence(dataset_identity(), invalid)

    def test_does_not_assume_id_continuity_or_global_id_monotonicity(self) -> None:
        events = [
            aggregate_trade(10_000, source_timestamp=BASE_MS),
            aggregate_trade(1, source_timestamp=BASE_MS + 1),
            aggregate_trade(500_000, source_timestamp=BASE_MS + 2),
        ]

        result = validate_aggregate_trade_sequence(dataset_identity(), events)

        self.assertEqual(result.events, tuple(events))

    def test_validated_sequence_copies_mutable_input(self) -> None:
        events = [aggregate_trade()]
        result = validate_aggregate_trade_sequence(dataset_identity(), events)

        events.append(aggregate_trade(101, source_timestamp=BASE_MS + 1))

        self.assertEqual(len(result.events), 1)

    def test_direct_validated_wrapper_revalidates_invariants_and_copies(self) -> None:
        validated_identity = validate_aggregate_trade_sequence(
            dataset_identity(), [aggregate_trade()]
        ).identity
        source_events = [aggregate_trade()]

        direct = ValidatedAggregateTradeSequence(validated_identity, source_events)
        source_events.append(aggregate_trade(101, source_timestamp=BASE_MS + 1))

        self.assertEqual(direct.events, (aggregate_trade(),))
        with self.assertRaisesRegex(AggregateTradeValidationError, "at least one"):
            ValidatedAggregateTradeSequence(validated_identity, [])

    def test_rejects_revalidation_and_unvalidated_direct_wrapper(self) -> None:
        validated = validate_aggregate_trade_sequence(
            dataset_identity(), [aggregate_trade()]
        )

        with self.assertRaisesRegex(AggregateTradeValidationError, "unvalidated"):
            validate_aggregate_trade_sequence(validated.identity, validated.events)
        with self.assertRaisesRegex(AggregateTradeValidationError, "validated"):
            ValidatedAggregateTradeSequence(dataset_identity(), [aggregate_trade()])

    def test_rejects_non_event_values_and_non_iterables(self) -> None:
        with self.assertRaisesRegex(TypeError, "AggregateTrade"):
            validate_aggregate_trade_sequence(dataset_identity(), [object()])
        with self.assertRaisesRegex(TypeError, "iterable"):
            validate_aggregate_trade_sequence(dataset_identity(), None)  # type: ignore[arg-type]

    def test_generator_input_is_materialized_once_in_original_order(self) -> None:
        source = (
            aggregate_trade(identifier, source_timestamp=BASE_MS + offset)
            for offset, identifier in enumerate((100, 101, 102))
        )

        result = validate_aggregate_trade_sequence(dataset_identity(), source)

        self.assertEqual(
            tuple(event.aggregate_trade_id for event in result.events),
            (100, 101, 102),
        )

    def test_validation_rechecks_forged_event_and_identity_invariants(self) -> None:
        invalid_event = aggregate_trade()
        object.__setattr__(invalid_event, "price", Decimal("0"))
        with self.assertRaisesRegex(AggregateTradeValidationError, "price"):
            validate_aggregate_trade_sequence(dataset_identity(), [invalid_event])

        invalid_identity = dataset_identity()
        object.__setattr__(invalid_identity, "provider", "other")
        with self.assertRaisesRegex(AggregateTradeValidationError, "provider"):
            validate_aggregate_trade_sequence(invalid_identity, [aggregate_trade()])


class ProductionBoundaryTests(unittest.TestCase):
    def test_frozen_candle_and_market_event_field_shapes_are_unchanged(self) -> None:
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

    def test_research_contract_is_not_added_to_production_market_data_exports(self) -> None:
        self.assertNotIn("AggregateTrade", production_market_data.__all__)
        self.assertFalse(hasattr(production_market_data, "AggregateTrade"))

    def test_research_package_imports_only_standard_library_and_domain_code(self) -> None:
        allowed_roots = {
            "__future__",
            "dataclasses",
            "datetime",
            "decimal",
            "enum",
            "hashlib",
            "json",
            "typing",
            "quantos",
        }
        imported_roots: set[str] = set()
        for path in RESEARCH_SOURCE.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots.add(node.module.split(".")[0])

        self.assertEqual(imported_roots - allowed_roots, set())

    def test_domain_contract_contains_no_network_acquisition_or_persistence_hooks(self) -> None:
        combined_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(RESEARCH_SOURCE.glob("*.py"))
        ).lower()
        forbidden_tokens = (
            "requests",
            "httpx",
            "urllib",
            "websocket",
            "socket",
            "binance.com",
            "open(",
            "write_bytes",
            "write_text",
            ".sort(",
            "sorted(",
        )

        for token in forbidden_tokens:
            with self.subTest(token=token):
                self.assertNotIn(token, combined_source)


if __name__ == "__main__":
    unittest.main()
