"""Synthetic pre-execution checks for the frozen AF4B DEVELOPMENT pipeline."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from quantos.domain.market_data import Candle
from quantos.domain.market_data.research_events import (
    AggregateTradeMinuteAvailabilityState,
    AggregateTradeMinuteCompletenessState,
    AggregateTradeMinuteState,
    ResearchDatasetRole,
    ResearchEventValidationStatus,
    SourceTimestampUnit,
)
from research import alpha_funnel_af4b_execute as af


class AF4BDevelopmentAcquisitionTests(unittest.TestCase):
    def entries(self, omitted=None):
        omitted = omitted or set()
        return tuple(
            SimpleNamespace(
                logical_partition=SimpleNamespace(
                    symbol=symbol, source_date=source_date
                )
            )
            for symbol in af.SYMBOLS
            for source_date in af.required_dates(af.frozen_catalog())
            if (symbol, source_date) not in omitted
        )

    def test_committed_frozen_artifacts_are_exact(self):
        catalog = af.frozen_catalog()
        self.assertEqual(catalog["catalog_id"], af.EXPECTED_CATALOG_ID)
        self.assertEqual(len(catalog["evaluations"]), 11)
        self.assertEqual(
            af.digest(af.CATALOG_PATH.read_bytes()),
            af.EXPECTED_CATALOG_FILE_SHA256,
        )
        self.assertEqual(
            af.digest(af.DOC_011_PATH.read_bytes()),
            af.EXPECTED_DOC_011_SHA256,
        )

    def test_required_date_count_and_boundaries_are_derived(self):
        dates = af.required_dates()
        self.assertEqual(len(dates), 639)
        self.assertEqual(dates[0], date(2024, 1, 1))
        self.assertEqual(dates[-1], date(2025, 9, 30))
        self.assertTrue(all((right - left).days == 1 for left, right in zip(dates, dates[1:])))

    def test_catalog_or_docs_tampering_fails_closed(self):
        with patch.object(af, "EXPECTED_CATALOG_FILE_SHA256", "0" * 64):
            with self.assertRaisesRegex(af.AF4BExecutionError, "catalog file"):
                af.frozen_catalog()
        with patch.object(af, "EXPECTED_DOC_011_SHA256", "0" * 64):
            with self.assertRaisesRegex(af.AF4BExecutionError, "docs/011"):
                af.frozen_catalog()

    def test_complete_verified_catalog_reuses_every_partition(self):
        entries = self.entries()
        fake_catalog = Mock()
        fake_catalog.rebuild.side_effect = [
            SimpleNamespace(entries=entries, catalog_id="initial"),
            SimpleNamespace(entries=entries, catalog_id="final"),
        ]
        progress = Mock()
        with (
            patch.object(af, "_catalog", return_value=fake_catalog),
            patch.object(af, "BinanceSpotAggregateTradeDailyArchiveAdapter") as adapter,
            patch.object(af, "ParquetAggregateTradeArchiveStore") as store,
        ):
            result = af.acquire_required_de1(Path("unused"), progress=progress)
        adapter.return_value.fetch_daily_archive.assert_not_called()
        store.return_value.write.assert_not_called()
        self.assertEqual(result["verified_reused_logical_partitions"], 1278)
        self.assertEqual(result["newly_acquired_logical_partitions"], 0)

    def test_one_missing_partition_uses_only_approved_adapter_and_store(self):
        missing = ("BTCUSDT", date(2024, 1, 1))
        initial_entries = self.entries({missing})
        final_entries = self.entries()
        fake_catalog = Mock()
        fake_catalog.rebuild.side_effect = [
            SimpleNamespace(entries=initial_entries, catalog_id="initial"),
            SimpleNamespace(entries=final_entries, catalog_id="final"),
        ]
        fetched = SimpleNamespace(archive=object(), raw_zip_bytes=b"zip")
        progress = Mock()
        with (
            patch.object(af, "_catalog", return_value=fake_catalog),
            patch.object(af, "BinanceSpotAggregateTradeDailyArchiveAdapter") as adapter,
            patch.object(af, "ParquetAggregateTradeArchiveStore") as store,
        ):
            adapter.return_value.fetch_daily_archive.return_value = fetched
            store.return_value.write.return_value = SimpleNamespace(manifest_id="a" * 64)
            result = af.acquire_required_de1(Path("unused"), progress=progress)
        adapter.assert_called_once_with(timeout_seconds=60.0)
        adapter.return_value.fetch_daily_archive.assert_called_once_with(
            symbol="BTCUSDT",
            archive_date=date(2024, 1, 1),
            research_role=af.ResearchDatasetRole.DEVELOPMENT,
        )
        store.return_value.write.assert_called_once_with(
            fetched.archive, raw_zip_bytes=b"zip"
        )
        self.assertEqual(result["newly_acquired_logical_partitions"], 1)

    def test_acquisition_failure_never_shrinks_required_set(self):
        missing = ("ETHUSDT", date(2025, 9, 30))
        fake_catalog = Mock()
        fake_catalog.rebuild.return_value = SimpleNamespace(
            entries=self.entries({missing}), catalog_id="initial"
        )
        with (
            patch.object(af, "_catalog", return_value=fake_catalog),
            patch.object(af, "BinanceSpotAggregateTradeDailyArchiveAdapter") as adapter,
            patch.object(af, "ParquetAggregateTradeArchiveStore"),
        ):
            adapter.return_value.fetch_daily_archive.side_effect = OSError("offline")
            with self.assertRaisesRegex(af.AF4BExecutionError, "2025-09-30"):
                af.acquire_required_de1(Path("unused"), progress=Mock())
        self.assertEqual(fake_catalog.rebuild.call_count, 1)


START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def minute_state(
    minute: datetime,
    *,
    symbol: str = "BTCUSDT",
    buy: str = "6",
    sell: str = "4",
    buy_count: int = 1,
    sell_count: int = 1,
) -> AggregateTradeMinuteState:
    buy_quote = Decimal(buy)
    sell_quote = Decimal(sell)
    event_count = buy_count + sell_count
    first_id = None if event_count == 0 else 1
    last_id = None if event_count == 0 else event_count
    first_time = None if event_count == 0 else minute + timedelta(seconds=1)
    last_time = None if event_count == 0 else minute + timedelta(seconds=2)
    return AggregateTradeMinuteState(
        symbol=symbol,
        minute_start_time=minute,
        minute_end_time_exclusive=minute + timedelta(minutes=1),
        source_range_id="1" * 64,
        source_manifest_id="2" * 64,
        source_revision_id="3" * 64,
        source_dataset_id="4" * 64,
        source_timestamp_unit=SourceTimestampUnit.MILLISECOND,
        event_count=event_count,
        aggressive_buy_event_count=buy_count,
        aggressive_sell_event_count=sell_count,
        total_base_quantity=Decimal(event_count),
        total_quote_notional=buy_quote + sell_quote,
        aggressive_buy_base_quantity=Decimal(buy_count),
        aggressive_sell_base_quantity=Decimal(sell_count),
        aggressive_buy_quote_notional=buy_quote,
        aggressive_sell_quote_notional=sell_quote,
        first_aggregate_trade_id=first_id,
        last_aggregate_trade_id=last_id,
        first_event_time=first_time,
        last_event_time=last_time,
        completeness_state=(
            AggregateTradeMinuteCompletenessState.VALIDATED_SOURCE_COMPLETE
        ),
        availability_state=(
            AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN
        ),
        validation_status=ResearchEventValidationStatus.VALIDATED,
    )


def candle(
    minute: datetime,
    *,
    symbol: str = "BTCUSDT",
    open_price: str = "100",
    close_price: str = "100",
) -> Candle:
    opening = Decimal(open_price)
    closing = Decimal(close_price)
    return Candle(
        symbol=symbol,
        interval="1m",
        open_time=minute,
        close_time=minute + timedelta(seconds=59),
        open=opening,
        high=max(opening, closing),
        low=min(opening, closing),
        close=closing,
        volume=Decimal("1"),
        quote_volume=Decimal("100"),
        trade_count=1,
    )


def grid(length: int, *, symbol: str = "BTCUSDT") -> tuple[Candle, ...]:
    return tuple(
        candle(START + timedelta(minutes=index), symbol=symbol)
        for index in range(length)
    )


class AF4BFrozenEvaluatorTests(unittest.TestCase):
    def predicate(
        self,
        hypothesis_id: str,
        states: tuple[AggregateTradeMinuteState, ...],
        candles: tuple[Candle, ...],
        index: int,
        *,
        source_symbol: str = "BTCUSDT",
        target_symbol: str = "BTCUSDT",
    ) -> tuple[bool, bool]:
        return af.registered_predicate(
            hypothesis_id,
            states,
            candles,
            index,
            source_symbol=source_symbol,
            target_symbol=target_symbol,
        )

    def family_row(
        self,
        evaluation_id: str,
        *,
        p_value: Decimal | None = Decimal("0.001"),
        base: Decimal = Decimal("0.01"),
        count: int = 100,
        rvs: int = 13,
        comparator_delta: str | None = "0.001",
    ) -> dict[str, object]:
        return {
            "evaluation_id": evaluation_id,
            "deoverlapped_event_count": count,
            "evidence_scores": {"RVS": rvs},
            "comparator": {
                "primary_minus_comparator_gross_expectancy": comparator_delta
            },
            "_p_value": p_value,
            "_base_expectancy": base,
        }

    def test_h1_and_h6_use_exact_positive_quote_imbalance(self):
        states = (minute_state(START),)
        btc = (candle(START),)
        eth = (candle(START, symbol="ETHUSDT"),)
        self.assertEqual(
            self.predicate(
                "af4b.h1.immediate-positive-quote-flow", states, btc, 0
            ),
            (True, True),
        )
        self.assertEqual(
            self.predicate(
                "af4b.h6.btc-positive-flow-leads-eth",
                states,
                eth,
                0,
                target_symbol="ETHUSDT",
            ),
            (True, True),
        )
        equal = (minute_state(START, buy="5", sell="5"),)
        self.assertEqual(
            self.predicate(
                "af4b.h1.immediate-positive-quote-flow", equal, btc, 0
            ),
            (True, False),
        )
        zero = (minute_state(START, buy="0", sell="0"),)
        self.assertEqual(
            self.predicate(
                "af4b.h1.immediate-positive-quote-flow", zero, btc, 0
            ),
            (False, False),
        )

    def test_h2_requires_exact_five_minute_window_without_warmup(self):
        states = tuple(
            minute_state(START + timedelta(minutes=index)) for index in range(5)
        )
        candles = grid(5)
        for index in range(4):
            self.assertEqual(
                self.predicate(
                    "af4b.h2.five-minute-positive-quote-flow-persistence",
                    states,
                    candles,
                    index,
                ),
                (False, False),
            )
        self.assertEqual(
            self.predicate(
                "af4b.h2.five-minute-positive-quote-flow-persistence",
                states,
                candles,
                4,
            ),
            (True, True),
        )
        broken = states[:3] + (minute_state(START + timedelta(minutes=10)),) + states[4:]
        with self.assertRaisesRegex(af.AF4BExecutionError, "five exact consecutive"):
            self.predicate(
                "af4b.h2.five-minute-positive-quote-flow-persistence",
                broken,
                candles,
                4,
            )
        zero = tuple(
            minute_state(START + timedelta(minutes=index), buy="0", sell="0")
            for index in range(5)
        )
        self.assertEqual(
            self.predicate(
                "af4b.h2.five-minute-positive-quote-flow-persistence",
                zero,
                candles,
                4,
            ),
            (False, False),
        )

    def test_h3_requires_exact_previous_state_and_positive_denominators(self):
        states = (
            minute_state(START, buy="4", sell="6"),
            minute_state(START + timedelta(minutes=1), buy="6", sell="4"),
        )
        candles = grid(2)
        self.assertEqual(
            self.predicate(
                "af4b.h3.positive-quote-flow-acceleration", states, candles, 0
            ),
            (False, False),
        )
        self.assertEqual(
            self.predicate(
                "af4b.h3.positive-quote-flow-acceleration", states, candles, 1
            ),
            (True, True),
        )
        broken = (states[0], minute_state(START + timedelta(minutes=2)))
        with self.assertRaisesRegex(af.AF4BExecutionError, "exact preceding"):
            self.predicate(
                "af4b.h3.positive-quote-flow-acceleration",
                broken,
                (candles[0], candle(START + timedelta(minutes=2))),
                1,
            )
        no_denominator = (
            minute_state(START, buy="0", sell="0"),
            states[1],
        )
        self.assertEqual(
            self.predicate(
                "af4b.h3.positive-quote-flow-acceleration",
                no_denominator,
                candles,
                1,
            ),
            (False, False),
        )

    def test_h4_flow_ratio_green_candle_and_comparator_alignment(self):
        states = (minute_state(START, buy="4", sell="8"),)
        green = (candle(START, open_price="100", close_price="100"),)
        red = (candle(START, open_price="100", close_price="99"),)
        self.assertEqual(
            self.predicate(
                "af4b.h4.sell-flow-absorption-reversal-up", states, green, 0
            ),
            (True, True),
        )
        eligible, signal = self.predicate(
            "af4b.h4.sell-flow-absorption-reversal-up", states, red, 0
        )
        self.assertTrue(eligible)
        self.assertFalse(signal)
        self.assertFalse(
            af.comparator_signal(
                "af4b.h4.sell-flow-absorption-reversal-up",
                eligible=eligible,
                candle=red[0],
            )
        )
        shifted = (candle(START + timedelta(minutes=1)),)
        with self.assertRaisesRegex(af.AF4BExecutionError, "exact UTC-minute join"):
            self.predicate(
                "af4b.h4.sell-flow-absorption-reversal-up", states, shifted, 0
            )

    def test_h5_uses_average_quote_per_aggregate_trade_record(self):
        states = (
            minute_state(
                START,
                buy="6",
                sell="10",
                buy_count=1,
                sell_count=2,
            ),
        )
        self.assertEqual(
            self.predicate(
                "af4b.h5.buy-side-aggregate-record-size-asymmetry",
                states,
                grid(1),
                0,
            ),
            (True, True),
        )
        missing_side = (
            minute_state(
                START,
                buy="0",
                sell="10",
                buy_count=0,
                sell_count=2,
            ),
        )
        self.assertEqual(
            self.predicate(
                "af4b.h5.buy-side-aggregate-record-size-asymmetry",
                missing_side,
                grid(1),
                0,
            ),
            (False, False),
        )

    def test_h6_requires_exact_btc_to_eth_synchronization(self):
        states = (minute_state(START, symbol="BTCUSDT"),)
        eth = (candle(START, symbol="ETHUSDT"),)
        self.assertEqual(
            self.predicate(
                "af4b.h6.btc-positive-flow-leads-eth",
                states,
                eth,
                0,
                source_symbol="BTCUSDT",
                target_symbol="ETHUSDT",
            ),
            (True, True),
        )
        with self.assertRaisesRegex(af.AF4BExecutionError, "exact UTC-minute join"):
            self.predicate(
                "af4b.h6.btc-positive-flow-leads-eth",
                states,
                (candle(START + timedelta(minutes=1), symbol="ETHUSDT"),),
                0,
                source_symbol="BTCUSDT",
                target_symbol="ETHUSDT",
            )
        with self.assertRaisesRegex(af.AF4BExecutionError, "exact UTC-minute join"):
            self.predicate(
                "af4b.h6.btc-positive-flow-leads-eth",
                states,
                (candle(START, symbol="BTCUSDT"),),
                0,
                source_symbol="BTCUSDT",
                target_symbol="ETHUSDT",
            )

    def test_t_plus_one_open_is_rejected_and_exact_t_plus_two_and_exit_are_used(self):
        candles = list(grid(7))
        candles[1] = candle(
            START + timedelta(minutes=1), open_price="100000", close_price="100000"
        )
        candles[2] = candle(
            START + timedelta(minutes=2), open_price="100", close_price="110"
        )
        candles[6] = candle(
            START + timedelta(minutes=6), open_price="100", close_price="150"
        )
        values = tuple(candles)
        self.assertEqual(af.directional_return(values, 0, 1), Decimal("0.1"))
        self.assertEqual(af.directional_return(values, 0, 5), Decimal("0.5"))
        gapped = list(values)
        gapped[2] = candle(
            START + timedelta(minutes=3), open_price="100", close_price="110"
        )
        with self.assertRaisesRegex(af.AF4BExecutionError, r"t\+2 entry"):
            af.directional_return(tuple(gapped), 0, 1)

    def test_development_boundaries_and_common_last_minute_are_exact(self):
        catalog = af.frozen_catalog()
        h1 = "af4b.h1.immediate-positive-quote-flow"
        h2 = "af4b.h2.five-minute-positive-quote-flow-persistence"
        last = datetime(2025, 9, 30, 23, 53, tzinfo=timezone.utc)
        self.assertTrue(af.registered_support(catalog, h1, START))
        self.assertFalse(af.registered_support(catalog, h2, START))
        self.assertTrue(
            af.registered_support(catalog, h2, START + timedelta(minutes=4))
        )
        self.assertTrue(af.registered_support(catalog, h1, last))
        self.assertFalse(
            af.registered_support(catalog, h1, last + timedelta(minutes=1))
        )
        with self.assertRaisesRegex(af.AF4BExecutionError, "canonical UTC"):
            af.registered_support(catalog, h1, START.replace(tzinfo=None))

    def test_deoverlap_is_earliest_deterministic_and_horizon_fixed(self):
        self.assertEqual(af.deoverlap((0, 1, 4, 5, 10), 5), (0, 5, 10))
        self.assertEqual(af.deoverlap((0, 1, 4), 1), (0, 1, 4))
        with self.assertRaisesRegex(af.AF4BExecutionError, "de-overlap"):
            af.deoverlap((1, 0), 5)
        with self.assertRaisesRegex(af.AF4BExecutionError, "de-overlap"):
            af.deoverlap((0, 1), 2)

    def test_only_frozen_comparators_exist(self):
        current = candle(START)
        for hypothesis_id in (
            "af4b.h1.immediate-positive-quote-flow",
            "af4b.h2.five-minute-positive-quote-flow-persistence",
            "af4b.h3.positive-quote-flow-acceleration",
            "af4b.h5.buy-side-aggregate-record-size-asymmetry",
            "af4b.h6.btc-positive-flow-leads-eth",
        ):
            self.assertTrue(
                af.comparator_signal(
                    hypothesis_id, eligible=True, candle=current
                )
            )
            self.assertFalse(
                af.comparator_signal(
                    hypothesis_id, eligible=False, candle=current
                )
            )
        with self.assertRaisesRegex(af.AF4BExecutionError, "unregistered"):
            af.comparator_signal("extra", eligible=True, candle=current)

    def test_frozen_costs_are_subtracted_exactly_once(self):
        costs = af.apply_frozen_costs(Decimal("0.01"), af.frozen_catalog())
        self.assertEqual(
            costs["base"],
            Decimal("0.007496871715426354086019002248060"),
        )
        self.assertEqual(
            costs["stress_2c"],
            Decimal("0.004993743430852708172038004496120"),
        )
        self.assertEqual(
            af.apply_frozen_costs(None, af.frozen_catalog()),
            {"base": None, "stress_2c": None},
        )
        with localcontext() as context:
            context.prec = 8
            self.assertEqual(
                af.apply_frozen_costs(Decimal("0.01"), af.frozen_catalog()),
                costs,
            )

    def test_eight_fixed_temporal_blocks_and_coverage_gate(self):
        values = [(index, Decimal("0.01")) for index in range(80)]
        temporal, robustness = af._temporal_stability(
            values,
            first_index=0,
            eligible_count=80,
            blocks=8,
            minimum_block_coverage=Decimal("0.75"),
            minimum_events_per_block=10,
        )
        self.assertEqual(temporal["qualified_block_count"], 8)
        self.assertEqual(temporal["positive_qualified_block_count"], 8)
        self.assertEqual(robustness, Decimal(1))
        _, insufficient = af._temporal_stability(
            values[:50],
            first_index=0,
            eligible_count=80,
            blocks=8,
            minimum_block_coverage=Decimal("0.75"),
            minimum_events_per_block=10,
        )
        self.assertIsNone(insufficient)

    def test_bootstrap_is_deterministic_and_uses_frozen_parameters(self):
        values = [Decimal("0.01"), Decimal("-0.01"), Decimal("0.02")]
        first = af._bootstrap_interval(
            values,
            samples=200,
            confidence=Decimal("0.95"),
            seed_material="20260914|synthetic",
        )
        second = af._bootstrap_interval(
            values,
            samples=200,
            confidence=Decimal("0.95"),
            seed_material="20260914|synthetic",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(
            first,
            af._bootstrap_interval(
                values,
                samples=200,
                confidence=Decimal("0.95"),
                seed_material="20260914|different",
            ),
        )

    def test_bh_closed_family_undefined_policy_and_order_are_exact(self):
        catalog = af.frozen_catalog()
        ids = catalog["fdr_family"]["evaluation_ids"]
        rows = [self.family_row(evaluation_id) for evaluation_id in ids]
        rows[3]["_p_value"] = None
        finalized = af.finalize_closed_family(catalog, list(reversed(rows)))
        self.assertEqual([row["evaluation_id"] for row in finalized], ids)
        self.assertIsNone(finalized[3]["multiple_testing"]["q_value"])
        self.assertEqual(
            finalized[3]["multiple_testing"]["multiplicity_input_p_value"],
            "1",
        )
        self.assertEqual(finalized[3]["classification"], "WATCH")
        with self.assertRaisesRegex(af.AF4BExecutionError, "exact closed 11-row"):
            af.finalize_closed_family(catalog, rows[:-1])

    def test_classification_and_comparator_nonadvancement_are_deterministic(self):
        catalog = af.frozen_catalog()
        ids = catalog["fdr_family"]["evaluation_ids"]
        promoted = af.finalize_closed_family(
            catalog, [self.family_row(evaluation_id) for evaluation_id in ids]
        )
        self.assertTrue(all(row["classification"] == "PROMOTE" for row in promoted))
        nonincremental_rows = [self.family_row(evaluation_id) for evaluation_id in ids]
        nonincremental_rows[0]["comparator"][
            "primary_minus_comparator_gross_expectancy"
        ] = "0"
        nonincremental = af.finalize_closed_family(catalog, nonincremental_rows)
        self.assertEqual(nonincremental[0]["classification"], "WATCH")
        self.assertIn(
            "candle_restatement_inconclusive",
            nonincremental[0]["incremental_information_status"],
        )
        killed_rows = [self.family_row(evaluation_id) for evaluation_id in ids]
        killed_rows[0]["_base_expectancy"] = Decimal("-0.0001")
        killed = af.finalize_closed_family(catalog, killed_rows)
        self.assertEqual(killed[0]["classification"], "KILL")

    def test_score_bands_and_six_positive_block_cap_are_frozen(self):
        scores = af._score_row(
            base_expectancy=Decimal("0.01"),
            t_statistic=Decimal("4"),
            frequency=Decimal("6"),
            robustness=Decimal("1"),
            positive_blocks=5,
            catalog=af.frozen_catalog(),
        )
        self.assertEqual(scores, {"M": 4, "S": 4, "F": 4, "R": 3, "X": 4, "RVS": 19})

    def test_canonical_artifact_hash_is_order_independent_and_newline_terminated(self):
        left = af.canonical_json_bytes({"b": 2, "a": 1})
        right = af.canonical_json_bytes({"a": 1, "b": 2})
        self.assertEqual(left, right)
        self.assertTrue(left.endswith(b"\n"))
        self.assertEqual(af.sha256(left).hexdigest(), af.sha256(right).hexdigest())

    def test_immutable_publication_is_idempotent_and_rejects_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            self.assertEqual(af.publish_immutable_bytes(path, b"first"), path)
            self.assertEqual(path.read_bytes(), b"first")
            self.assertEqual(af.publish_immutable_bytes(path, b"first"), path)
            with self.assertRaisesRegex(af.AF4BExecutionError, "collision"):
                af.publish_immutable_bytes(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")

    def test_protected_roles_are_rejected_before_access(self):
        af.require_development_role(ResearchDatasetRole.DEVELOPMENT)
        for role in (
            ResearchDatasetRole.SCREENING_VALIDATION,
            ResearchDatasetRole.SEALED_OOS,
        ):
            with self.assertRaisesRegex(af.AF4BExecutionError, "DEVELOPMENT"):
                af.require_development_role(role)
        with self.assertRaisesRegex(af.AF4BExecutionError, "DEVELOPMENT"):
            af.require_development_role("development")


if __name__ == "__main__":
    unittest.main()
