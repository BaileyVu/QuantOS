"""Adversarial AF1 causality, statistics, identity, and publication tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quantos.domain.evaluation.alpha_funnel import (
    AF1_SCHEMA_VERSION,
    COST_RATE_UNIT,
    AlphaFunnelError,
    CostKind,
    CostScenario,
    DatasetRole,
    Direction,
    EvidenceScoringConfig,
    FdrUniverse,
    HypothesisMetadata,
    ProvisionalClassificationConfig,
    ResearchDataset,
    ResearchHypothesis,
    ScoreBands,
    ScreeningConfig,
    ScreeningRun,
    VerifiedScreeningArtifact,
    _classify,
    _deoverlapped_events,
    _mean,
    _median,
    _standard_error,
    _t_statistic_and_p,
    benjamini_hochberg,
    af1_decimal_context,
    canonical_candle_content_sha256,
    canonical_json_bytes,
    declare_fdr_universe,
    screen_alpha_funnel,
)
from quantos.domain.evaluation.reference_hypotheses import (
    af1_reference_hypotheses,
    close_location_extreme,
)
from quantos.domain.market_data.contracts import Candle, DatasetIdentity
from quantos.domain.market_data.validation import (
    ValidatedCandleSequence,
    validate_candle_sequence,
)
from quantos.infrastructure.storage.alpha_funnel import AlphaFunnelArtifactStore


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
MICROSECOND = timedelta(microseconds=1)
D = Decimal


def candle_sequence(
    *,
    count: int = 130,
    close_offsets: dict[int, Decimal] | None = None,
    close_times: dict[int, datetime] | None = None,
    symbol: str = "BTCUSDT",
) -> ValidatedCandleSequence:
    offsets = close_offsets or {}
    timestamp_overrides = close_times or {}
    candles = []
    for index in range(count):
        opened = D(100 + index)
        closed = opened + offsets.get(index, D(0))
        high = max(opened, closed) + D(1)
        low = min(opened, closed) - D(1)
        candles.append(Candle(
            symbol=symbol,
            interval="1m",
            open_time=START + index * MINUTE,
            close_time=timestamp_overrides.get(
                index,
                START + (index + 1) * MINUTE - MICROSECOND,
            ),
            open=opened,
            high=high,
            low=low,
            close=closed,
            volume=D(10),
            quote_volume=D(1000),
            trade_count=5,
        ))
    identity = DatasetIdentity(
        symbol=symbol,
        timeframe="1m",
        start_time=candles[0].open_time,
        end_time=candles[-1].open_time,
        source="test",
        schema_version="candle-v1",
        ingestion_version="af1-test-v2",
    )
    return validate_candle_sequence(identity, candles)


def dataset(
    *,
    count: int = 130,
    close_offsets: dict[int, Decimal] | None = None,
    close_times: dict[int, datetime] | None = None,
    role: DatasetRole = DatasetRole.DEVELOPMENT,
    symbol: str = "BTCUSDT",
    dataset_id: str | None = None,
    asserted_digest: str | None = None,
    sequence: ValidatedCandleSequence | None = None,
) -> ResearchDataset:
    sequence = sequence or candle_sequence(
        count=count,
        close_offsets=close_offsets,
        close_times=close_times,
        symbol=symbol,
    )
    digest = canonical_candle_content_sha256(sequence)
    return ResearchDataset(
        dataset_id=dataset_id or f"fixture-{symbol.lower()}",
        content_sha256=asserted_digest or digest,
        role=role,
        sequence=sequence,
    )


def hypothesis(
    evaluator=lambda window: True,
    *,
    stable_id: str = "test.explicit",
    implementation_id: str = "test-explicit-v1",
    lookback: int = 1,
    direction: Direction = Direction.UP,
    explainability: int | None = 2,
) -> ResearchHypothesis:
    return ResearchHypothesis(
        HypothesisMetadata(
            stable_id=stable_id,
            implementation_id=implementation_id,
            family="test-family",
            description="Explicit test hypothesis.",
            direction=direction,
            interpretation="Test a stated directional expectation.",
            required_inputs=("close",),
            parameters={"fixture": True},
            causal_lookback=lookback,
            parameter_neighborhood={"tested": True},
            human_explainability_score=explainability,
        ),
        evaluator,
    )


def config_for(
    datasets: tuple[ResearchDataset, ...],
    hypotheses: tuple[ResearchHypothesis, ...],
    **overrides,
) -> ScreeningConfig:
    authority_id = overrides.pop("authority_id", "test-split-manifest-v1")
    universe = overrides.pop(
        "fdr_universe",
        declare_fdr_universe(
            datasets,
            hypotheses,
            authority_id=authority_id,
        ),
    )
    fields = dict(
        code_version="test-sha",
        fdr_universe=universe,
        costs=(
            CostScenario("base", CostKind.BASE, D("0.001")),
            CostScenario("stress", CostKind.STRESS, D("0.0025")),
        ),
        scoring=EvidenceScoringConfig(
            magnitude=ScoreBands((D("-0.01"), D("0"), D("0.001"), D("0.01"))),
            statistical_strength=ScoreBands((D("0"), D("1"), D("2"), D("3"))),
            opportunity_frequency=ScoreBands((D("0"), D("1"), D("10"), D("100"))),
            robustness=ScoreBands((D("0"), D("0.25"), D("0.5"), D("1"))),
        ),
        classification=ProvisionalClassificationConfig(
            promote_min_rvs=12,
            promote_min_deoverlapped_events=2,
            promote_min_base_cost_expectancy=D("0"),
            kill_below_base_cost_expectancy=D("-0.02"),
        ),
        random_seed=42,
        bootstrap_samples=80,
        confidence_level=D("0.90"),
        fdr_threshold=D("0.05"),
        stability_blocks=4,
        minimum_stability_block_coverage=D("0.50"),
        minimum_events_per_stability_block=1,
    )
    fields.update(overrides)
    return ScreeningConfig(**fields)


def run_screen(
    datasets: tuple[ResearchDataset, ...],
    hypotheses: tuple[ResearchHypothesis, ...],
    **overrides,
) -> ScreeningRun:
    return screen_alpha_funnel(
        datasets,
        hypotheses,
        config_for(datasets, hypotheses, **overrides),
    )


def row(run: ScreeningRun, horizon: int, *, hypothesis_id: str = "test.explicit"):
    return next(
        item
        for item in run.results
        if item["horizon_minutes"] == horizon
        and item["hypothesis_id"] == hypothesis_id
    )


def forged_screening_run(
    identity_payload: dict[str, object],
    results: list[dict[str, object]],
    *,
    proof: bytes = b"\x00" * 32,
) -> ScreeningRun:
    """Build a self-consistent byte envelope without evaluator provenance."""
    identity = json.loads(canonical_json_bytes(identity_payload))
    plain_results = json.loads(canonical_json_bytes(results))
    run_id = sha256(canonical_json_bytes(identity)).hexdigest()
    results_document = {
        "schema_version": AF1_SCHEMA_VERSION,
        "run_id": run_id,
        "results": plain_results,
    }
    results_bytes = canonical_json_bytes(results_document)
    manifest = dict(identity)
    manifest["run_id"] = run_id
    manifest["results_sha256"] = sha256(results_bytes).hexdigest()
    manifest_bytes = canonical_json_bytes(manifest)
    instance = object.__new__(ScreeningRun)
    object.__setattr__(instance, "run_id", run_id)
    object.__setattr__(instance, "manifest", manifest)
    object.__setattr__(instance, "results", tuple(plain_results))
    object.__setattr__(instance, "_manifest_bytes", manifest_bytes)
    object.__setattr__(instance, "_results_bytes", results_bytes)
    object.__setattr__(instance, "_provenance_proof", proof)
    return instance


class AlphaFunnelTests(unittest.TestCase):
    def test_all_horizon_exit_sentinels_and_single_cost_subtraction(self) -> None:
        offsets = {horizon: D(horizon) / D(10) for horizon in (1, 3, 5, 10, 15, 30, 60)}
        source = dataset(close_offsets=offsets)
        explicit = hypothesis(lambda window: window[-1].open == D(100))

        run = run_screen((source,), (explicit,))

        for horizon in (1, 3, 5, 10, 15, 30, 60):
            with self.subTest(horizon=horizon):
                with af1_decimal_context():
                    expected = (
                        D(100 + horizon) + offsets[horizon]
                    ) / D(101) - D(1)
                    expected_cost_adjusted = expected - D("0.001")
                result = row(run, horizon)
                self.assertEqual(D(result["mean_forward_return"]), expected)
                self.assertEqual(D(result["gross_expectancy"]), expected)
                self.assertEqual(
                    D(result["cost_adjusted_expectancy"]["base"]),
                    expected_cost_adjusted,
                )
                self.assertEqual(
                    result["cost_adjusted_expectancy_unit"],
                    COST_RATE_UNIT,
                )

    def test_impossible_decision_or_forward_timeline_fails_closed(self) -> None:
        for bad_index in (0, 20):
            with self.subTest(bad_index=bad_index):
                source = dataset(close_times={
                    bad_index: START + (bad_index + 2) * MINUTE,
                })
                explicit = hypothesis()
                with self.assertRaisesRegex(AlphaFunnelError, "close_time"):
                    run_screen((source,), (explicit,))

    def test_callback_receives_only_exact_causal_window(self) -> None:
        observed = []

        def evaluator(window):
            observed.append(tuple(candle.open_time for candle in window))
            return window[-1].open == D(102)

        source = dataset()
        run = run_screen((source,), (hypothesis(evaluator, lookback=3),))

        self.assertTrue(observed)
        self.assertTrue(all(len(window) == 3 for window in observed))
        self.assertEqual(observed[0], (START, START + MINUTE, START + 2 * MINUTE))
        self.assertEqual(row(run, 1)["raw_event_count"], 1)

    def test_future_changes_cannot_change_prior_signal_selection(self) -> None:
        explicit = hypothesis(lambda window: window[-1].open == D(100))
        first = run_screen((dataset(),), (explicit,))
        second = run_screen(
            (dataset(close_offsets={1: D(50)}),),
            (explicit,),
        )

        self.assertEqual(row(first, 1)["raw_event_count"], row(second, 1)["raw_event_count"])
        self.assertNotEqual(row(first, 1)["mean_forward_return"], row(second, 1)["mean_forward_return"])

    def test_common_support_exact_sixty_and_sixty_one_candle_boundary(self) -> None:
        sixty = dataset(count=60)
        sixty_one = dataset(count=61)

        no_support = run_screen((sixty,), (hypothesis(),))
        one_decision = run_screen((sixty_one,), (hypothesis(),))

        self.assertEqual(row(no_support, 1)["eligible_decision_count"], 0)
        self.assertEqual(row(no_support, 60)["eligible_decision_count"], 0)
        self.assertEqual(row(one_decision, 1)["eligible_decision_count"], 1)
        self.assertEqual(row(one_decision, 60)["eligible_decision_count"], 1)
        self.assertIn(
            "complete_60m_forward_window",
            one_decision.manifest["common_support_rule"],
        )

    def test_exact_deoverlap_boundary(self) -> None:
        self.assertEqual(_deoverlapped_events((0, 1, 2, 3), 3), (0, 3))

    def test_deoverlapped_counts_and_nomenclature(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))

        self.assertEqual(row(run, 1)["raw_event_count"], 70)
        self.assertEqual(row(run, 1)["deoverlapped_event_count"], 70)
        self.assertEqual(row(run, 3)["deoverlapped_event_count"], 24)
        self.assertEqual(row(run, 60)["deoverlapped_event_count"], 2)
        self.assertTrue(row(run, 3)["event_deoverlap"]["does_not_assert_iid"])
        self.assertNotIn("event_independence", row(run, 3))

    def test_mixed_return_estimator_oracle(self) -> None:
        values = (D("-0.02"), D("0"), D("0.01"), D("0.03"))

        self.assertEqual(_mean(values), D("0.005"))
        self.assertEqual(_median(values), D("0.005"))
        self.assertAlmostEqual(float(_standard_error(values)), 0.010408329997330664, places=15)
        statistic, p_value = _t_statistic_and_p(values)
        self.assertAlmostEqual(float(statistic), 0.4803844614152614, places=14)
        self.assertAlmostEqual(float(p_value), 0.6638080120323687, places=14)

    def test_all_wins_all_losses_and_zero_return_policy(self) -> None:
        winning = run_screen(
            (dataset(close_offsets={index: D(1) for index in range(130)}),),
            (hypothesis(),),
        )
        losing = run_screen(
            (dataset(close_offsets={index: D(-1) for index in range(130)}),),
            (hypothesis(),),
        )
        tied = run_screen((dataset(),), (hypothesis(),))

        self.assertEqual(row(winning, 1)["win_rate"], "1")
        self.assertIsNone(row(winning, 1)["average_negative_return"])
        self.assertIsNone(row(winning, 1)["payoff_ratio"])
        self.assertEqual(row(losing, 1)["win_rate"], "0")
        self.assertIsNone(row(losing, 1)["average_positive_return"])
        self.assertIsNone(row(losing, 1)["payoff_ratio"])
        self.assertEqual(row(tied, 1)["win_rate"], "0")
        self.assertIsNone(row(tied, 1)["average_positive_return"])
        self.assertIsNone(row(tied, 1)["average_negative_return"])
        self.assertEqual(
            row(tied, 1)["zero_return_policy"]["win_rate"],
            "zero_is_not_a_win",
        )

    def test_nonzero_zero_variance_returns_are_explicitly_undefined_for_t(self) -> None:
        offsets = {
            index: D(100 + index) * D("0.01")
            for index in range(130)
        }
        result = row(run_screen(
            (dataset(close_offsets=offsets),),
            (hypothesis(),),
        ), 1)

        self.assertEqual(result["standard_error"], "0")
        self.assertIsNone(result["descriptive_t_statistic"])
        self.assertIsNone(result["raw_p_value"])

    def test_down_return_excursions_and_cost(self) -> None:
        source = dataset(close_offsets={1: D(-2)})
        explicit = hypothesis(
            lambda window: window[-1].open == D(100),
            direction=Direction.DOWN,
        )
        result = row(run_screen((source,), (explicit,)), 1)

        with af1_decimal_context():
            expected = D(2) / D(101)
            expected_favorable = D(3) / D(101)
            expected_adverse = D(1) - D(102) / D(101)
            expected_cost_adjusted = expected - D("0.001")
        self.assertEqual(D(result["mean_directional_return"]), expected)
        self.assertEqual(
            D(result["average_maximum_favorable_excursion"]),
            expected_favorable,
        )
        self.assertEqual(
            D(result["average_maximum_adverse_excursion"]),
            expected_adverse,
        )
        self.assertEqual(
            D(result["cost_adjusted_expectancy"]["base"]),
            expected_cost_adjusted,
        )

    def test_external_decimal_context_cannot_change_results_or_hashes(self) -> None:
        source = dataset(close_offsets={
            index: D(index % 5 - 2) / D(7)
            for index in range(130)
        })
        explicit = hypothesis()
        configuration = config_for((source,), (explicit,))

        with localcontext() as context:
            context.prec = 7
            context.rounding = ROUND_DOWN
            low = screen_alpha_funnel((source,), (explicit,), configuration)
        with localcontext() as context:
            context.prec = 42
            context.rounding = ROUND_UP
            high = screen_alpha_funnel((source,), (explicit,), configuration)

        self.assertEqual(low.run_id, high.run_id)
        self.assertEqual(low.results_bytes(), high.results_bytes())
        self.assertEqual(low.manifest_bytes(), high.manifest_bytes())
        self.assertEqual(
            low.manifest["results_sha256"],
            high.manifest["results_sha256"],
        )

    def test_cost_fraction_unit_range_and_stress_order(self) -> None:
        scenario = CostScenario("ten-bps", CostKind.BASE, D("0.001"))
        self.assertEqual(scenario.as_dict()["round_trip_rate_unit"], COST_RATE_UNIT)
        for invalid in (D("-0.001"), D("1"), D("10"), D("NaN"), D("Infinity")):
            with self.subTest(invalid=invalid), self.assertRaises(AlphaFunnelError):
                CostScenario("invalid", CostKind.BASE, invalid)

        source = dataset()
        explicit = hypothesis()
        with self.assertRaisesRegex(AlphaFunnelError, "stress scenario"):
            config_for(
                (source,),
                (explicit,),
                costs=(
                    CostScenario("base", CostKind.BASE, D("0.002")),
                    CostScenario("stress", CostKind.STRESS, D("0.001")),
                ),
            )

    def test_closed_fdr_universe_rejects_batch_splitting(self) -> None:
        source = dataset()
        first = hypothesis(stable_id="test.first", implementation_id="test-first-v1")
        second = hypothesis(stable_id="test.second", implementation_id="test-second-v1")
        full_datasets = (source,)
        full_hypotheses = (first, second)
        universe = declare_fdr_universe(
            full_datasets,
            full_hypotheses,
            authority_id="declared-study-v1",
        )
        configuration = config_for(
            full_datasets,
            full_hypotheses,
            fdr_universe=universe,
        )

        complete = screen_alpha_funnel(
            full_datasets,
            full_hypotheses,
            configuration,
        )
        self.assertEqual(
            complete.results[0]["multiple_testing"]["batch_id"],
            universe.universe_id,
        )
        with self.assertRaisesRegex(AlphaFunnelError, "exactly match"):
            screen_alpha_funnel(full_datasets, (first,), configuration)

        subset_universe = declare_fdr_universe(
            full_datasets,
            (first,),
            authority_id="declared-study-v1",
        )
        self.assertNotEqual(universe.universe_id, subset_universe.universe_id)

    def test_hypothesis_definition_digest_closes_parameterized_universe(self) -> None:
        source = dataset()
        base = hypothesis(
            stable_id="test.parameterized",
            implementation_id="test-parameterized-v1",
        )
        first = ResearchHypothesis(
            replace(
                base.metadata,
                parameters={"upper": 1000, "threshold": 0},
                parameter_neighborhood={"step": 1, "tested": True},
            ),
            lambda window: window[-1].close > D(0),
        )
        reordered = ResearchHypothesis(
            replace(
                base.metadata,
                parameters={"threshold": 0, "upper": 1000},
                parameter_neighborhood={"tested": True, "step": 1},
            ),
            lambda window: window[-1].close > D(0),
        )
        changed = ResearchHypothesis(
            replace(
                base.metadata,
                parameters={"upper": 1000, "threshold": 999},
                parameter_neighborhood={"step": 1, "tested": True},
            ),
            lambda window: window[-1].close > D(999),
        )

        self.assertEqual(first.metadata.definition_sha256, reordered.metadata.definition_sha256)
        self.assertNotEqual(first.metadata.definition_sha256, changed.metadata.definition_sha256)
        first_universe = declare_fdr_universe(
            (source,), (first,), authority_id="parameter-study-v1"
        )
        reordered_universe = declare_fdr_universe(
            (source,), (reordered,), authority_id="parameter-study-v1"
        )
        changed_universe = declare_fdr_universe(
            (source,), (changed,), authority_id="parameter-study-v1"
        )
        self.assertEqual(first_universe.universe_id, reordered_universe.universe_id)
        self.assertNotEqual(first_universe.universe_id, changed_universe.universe_id)
        self.assertEqual(
            first_universe.tests[0].hypothesis_definition_sha256,
            first.metadata.definition_sha256,
        )
        configuration = config_for(
            (source,), (first,), fdr_universe=first_universe
        )
        with self.assertRaisesRegex(AlphaFunnelError, "exactly match"):
            screen_alpha_funnel((source,), (changed,), configuration)

    def test_bh_ties_nulls_and_conservative_registered_count(self) -> None:
        self.assertEqual(
            benjamini_hochberg((D("0.01"), D("0.01"), None)),
            (D("0.015"), D("0.015"), None),
        )
        self.assertEqual(
            benjamini_hochberg((D("0.01"), None)),
            (D("0.02"), None),
        )
        self.assertEqual(benjamini_hochberg((None, None)), (None, None))

    def test_one_fdr_threshold_controls_reporting_and_promotion(self) -> None:
        classification = ProvisionalClassificationConfig(
            promote_min_rvs=1,
            promote_min_deoverlapped_events=1,
            promote_min_base_cost_expectancy=D("0"),
            kill_below_base_cost_expectancy=D("-1"),
        )
        actual = _classify(
            deoverlapped_count=100,
            base_expectancy=D("0.01"),
            q_value=D("0.02"),
            rvs=20,
            classification=classification,
            fdr_threshold=D("0.01"),
        )
        self.assertEqual(actual.value, "WATCH")

    def test_reported_fdr_failure_cannot_promote_a_screened_row(self) -> None:
        source = dataset(close_offsets={
            index: D(index % 2)
            for index in range(130)
        })
        explicit = hypothesis()
        permissive = ProvisionalClassificationConfig(
            promote_min_rvs=0,
            promote_min_deoverlapped_events=1,
            promote_min_base_cost_expectancy=D("0"),
            kill_below_base_cost_expectancy=D("-1"),
        )
        zero_costs = (
            CostScenario("base", CostKind.BASE, D("0")),
            CostScenario("stress", CostKind.STRESS, D("0")),
        )
        result = row(run_screen(
            (source,),
            (explicit,),
            classification=permissive,
            costs=zero_costs,
            fdr_threshold=D("1E-100"),
        ), 1)

        self.assertIsNotNone(result["multiple_testing"]["q_value"])
        self.assertFalse(result["multiple_testing"]["passes_fdr_threshold"])
        self.assertEqual(result["provisional_classification"], "WATCH")

    def test_maximum_robustness_requires_multiple_positive_blocks(self) -> None:
        one_source = dataset(close_offsets={1: D(2)})
        one_hypothesis = hypothesis(lambda window: window[-1].open == D(100))
        one_config = config_for((one_source,), (one_hypothesis,))
        one_scoring = EvidenceScoringConfig(
            magnitude=one_config.scoring.magnitude,
            statistical_strength=one_config.scoring.statistical_strength,
            opportunity_frequency=one_config.scoring.opportunity_frequency,
            robustness=ScoreBands((D("0"), D("0.10"), D("0.20"), D("0.25"))),
        )
        one_result = row(screen_alpha_funnel(
            (one_source,),
            (one_hypothesis,),
            replace(
                one_config,
                minimum_stability_block_coverage=D("0.25"),
                scoring=one_scoring,
            ),
        ), 1)
        self.assertEqual(one_result["temporal_stability"]["positive_qualified_block_count"], 1)
        self.assertEqual(one_result["research_viability_score"]["R_uncapped"], 4)
        self.assertEqual(one_result["research_viability_score"]["R"], 3)
        self.assertFalse(
            one_result["research_viability_score"]["R_maximum_prerequisite_met"]
        )

        two_indices = (0, 18)
        two_source = dataset(close_offsets={index + 1: D(2) for index in two_indices})
        two_hypothesis = hypothesis(
            lambda window: window[-1].open in {D(100), D(118)}
        )
        two_config = config_for((two_source,), (two_hypothesis,))
        two_scoring = EvidenceScoringConfig(
            magnitude=two_config.scoring.magnitude,
            statistical_strength=two_config.scoring.statistical_strength,
            opportunity_frequency=two_config.scoring.opportunity_frequency,
            robustness=ScoreBands((D("0"), D("0.10"), D("0.25"), D("0.50"))),
        )
        two_result = row(screen_alpha_funnel(
            (two_source,),
            (two_hypothesis,),
            replace(two_config, scoring=two_scoring),
        ), 1)
        self.assertEqual(two_result["temporal_stability"]["positive_qualified_block_count"], 2)
        self.assertEqual(two_result["research_viability_score"]["R"], 4)
        self.assertTrue(
            two_result["research_viability_score"]["R_maximum_prerequisite_met"]
        )

        all_indices = (0, 18, 35, 53)
        all_source = dataset(close_offsets={index + 1: D(2) for index in all_indices})
        all_hypothesis = hypothesis(
            lambda window: window[-1].open in {D(100), D(118), D(135), D(153)}
        )
        all_config = config_for((all_source,), (all_hypothesis,))
        all_result = row(screen_alpha_funnel(
            (all_source,),
            (all_hypothesis,),
            replace(all_config, minimum_stability_block_coverage=D("1")),
        ), 1)
        self.assertEqual(all_result["temporal_stability"]["positive_qualified_block_count"], 4)
        self.assertEqual(all_result["research_viability_score"]["R"], 4)

        with self.assertRaisesRegex(AlphaFunnelError, "from 2 through"):
            replace(one_config, minimum_positive_blocks_for_max_robustness=1)
        with self.assertRaisesRegex(AlphaFunnelError, "from 2 through"):
            replace(one_config, minimum_positive_blocks_for_max_robustness=5)

    def test_sparse_temporal_blocks_do_not_receive_robustness_score(self) -> None:
        explicit = hypothesis(lambda window: window[-1].open == D(100))
        result = row(run_screen((dataset(),), (explicit,)), 1)

        stability = result["temporal_stability"]
        self.assertEqual(stability["qualified_block_count"], 1)
        self.assertEqual(stability["qualified_block_coverage"], "0.25")
        self.assertIsNone(stability["robustness_evidence_ratio"])
        self.assertEqual(result["research_viability_score"]["R"], 0)

    def test_every_reference_hypothesis_executes(self) -> None:
        source = dataset()
        references = af1_reference_hypotheses()

        run = run_screen((source,), references)

        self.assertEqual(len(run.results), len(references) * 7)
        self.assertEqual(
            {item["hypothesis_id"] for item in run.results},
            {item.metadata.stable_id for item in references},
        )

    def test_close_location_high_equals_low_is_false(self) -> None:
        reference = close_location_extreme(maximum_location=D("0.5"))
        flat = Candle(
            symbol="BTCUSDT",
            interval="1m",
            open_time=START,
            close_time=START + MINUTE - MICROSECOND,
            open=D(100),
            high=D(100),
            low=D(100),
            close=D(100),
            volume=D(0),
            quote_volume=D(0),
            trade_count=0,
        )
        self.assertFalse(reference.evaluator((flat,)))

    def test_sealed_oos_gate_and_declared_role_binding(self) -> None:
        sealed = dataset(role=DatasetRole.SEALED_OOS)
        explicit = hypothesis()
        universe = declare_fdr_universe(
            (sealed,),
            (explicit,),
            authority_id="sealed-split-v1",
        )
        with self.assertRaisesRegex(AlphaFunnelError, "allow_sealed_oos"):
            screen_alpha_funnel(
                (sealed,),
                (explicit,),
                config_for((sealed,), (explicit,), fdr_universe=universe),
            )

        allowed = screen_alpha_funnel(
            (sealed,),
            (explicit,),
            config_for(
                (sealed,),
                (explicit,),
                fdr_universe=universe,
                allow_sealed_oos=True,
            ),
        )
        self.assertTrue(allowed.manifest["configuration"]["allow_sealed_oos"])

        relabeled = replace(sealed, role=DatasetRole.DEVELOPMENT)
        with self.assertRaisesRegex(AlphaFunnelError, "exactly match"):
            screen_alpha_funnel(
                (relabeled,),
                (explicit,),
                config_for(
                    (relabeled,),
                    (explicit,),
                    fdr_universe=universe,
                ),
            )

    def test_malformed_role_is_rejected(self) -> None:
        sequence = candle_sequence()
        with self.assertRaisesRegex(AlphaFunnelError, "DatasetRole"):
            ResearchDataset(
                dataset_id="bad-role",
                content_sha256=canonical_candle_content_sha256(sequence),
                role="SEALED_OOS",
                sequence=sequence,
            )

    def test_dataset_digest_is_verified_and_changes_identity(self) -> None:
        first_sequence = candle_sequence()
        with self.assertRaisesRegex(AlphaFunnelError, "does not match"):
            dataset(sequence=first_sequence, asserted_digest="a" * 64)

        first = dataset(sequence=first_sequence)
        second = dataset(close_offsets={1: D(1)})
        self.assertNotEqual(first.content_sha256, second.content_sha256)
        first_run = run_screen((first,), (hypothesis(),))
        second_run = run_screen((second,), (hypothesis(),))
        self.assertNotEqual(first_run.run_id, second_run.run_id)

    def test_evaluator_implementation_identity_changes_run_identity(self) -> None:
        source = dataset()
        first = hypothesis(
            lambda window: True,
            implementation_id="test-evaluator-v1",
        )
        second = hypothesis(
            lambda window: False,
            implementation_id="test-evaluator-v2",
        )

        first_run = run_screen((source,), (first,))
        second_run = run_screen((source,), (second,))

        self.assertNotEqual(first_run.run_id, second_run.run_id)
        self.assertIn(
            "trusted_reviewed_research_code",
            first_run.manifest["callback_trust_boundary"],
        )

    def test_screening_run_is_deeply_immutable(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))

        with self.assertRaises(TypeError):
            run.manifest["run_id"] = "0" * 64
        with self.assertRaises(TypeError):
            run.manifest["configuration"]["allow_sealed_oos"] = True
        with self.assertRaises(TypeError):
            run.results[0]["description"] = "changed"
        with self.assertRaises(TypeError):
            run.results[0]["multiple_testing"]["q_value"] = "0"
        with self.assertRaises(AlphaFunnelError):
            ScreeningRun()

    def test_arbitrary_screening_run_payloads_lack_evaluator_provenance(self) -> None:
        self.assertFalse(hasattr(ScreeningRun, "_create"))
        legitimate = run_screen((dataset(),), (hypothesis(),))
        identity = json.loads(legitimate.manifest_bytes())
        identity.pop("run_id")
        identity.pop("results_sha256")
        results = json.loads(legitimate.results_bytes())["results"]
        reconstructed = forged_screening_run(identity, results)
        minimal = forged_screening_run(
            {
                "schema_version": AF1_SCHEMA_VERSION,
                "research_only": True,
                "production_approved": False,
            },
            [],
        )

        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            with self.assertRaisesRegex(AlphaFunnelError, "evaluator-issued provenance"):
                store.write(reconstructed)
            with self.assertRaises(AlphaFunnelError):
                store.write(minimal)

    def test_evaluator_mismatch_is_rejected_before_publication(self) -> None:
        legitimate = run_screen((dataset(),), (hypothesis(),))
        identity = json.loads(legitimate.manifest_bytes())
        identity.pop("run_id")
        identity.pop("results_sha256")
        results = json.loads(legitimate.results_bytes())["results"]

        hypothesis_definition = identity["hypotheses"][0]
        hypothesis_definition["implementation_id"] = "forged-evaluator-v999"
        definition_sha256 = sha256(
            canonical_json_bytes(hypothesis_definition)
        ).hexdigest()
        universe = identity["configuration"]["fdr_universe"]
        for test in universe["tests"]:
            test["hypothesis_definition_sha256"] = definition_sha256
            test["evaluator_implementation_id"] = "forged-evaluator-v999"
        universe_identity = dict(universe)
        universe_identity.pop("universe_id")
        universe_id = sha256(canonical_json_bytes(universe_identity)).hexdigest()
        universe["universe_id"] = universe_id
        identity["configuration"]["batch_id"] = universe_id
        for result in results:
            result["hypothesis_definition_sha256"] = definition_sha256
            result["multiple_testing"]["batch_id"] = universe_id
            result["multiple_testing"]["fdr_universe_id"] = universe_id

        mismatched = forged_screening_run(identity, results)
        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            with self.assertRaisesRegex(
                AlphaFunnelError,
                "evaluator_implementation_id provenance mismatch",
            ):
                store.write(mismatched)

    def test_artifact_store_rejects_corrupted_run_and_path_ids(self) -> None:
        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            corrupted = run_screen((dataset(),), (hypothesis(),))
            object.__setattr__(corrupted, "_results_bytes", b"corrupt")
            with self.assertRaisesRegex(AlphaFunnelError, "canonical bytes"):
                store.write(corrupted)

            for bad in (
                "../escape",
                r"..\escape",
                r"G:\escape",
                "/absolute",
                "subdir/name",
                "0" * 63,
            ):
                with self.subTest(run_id=bad):
                    forged = run_screen((dataset(),), (hypothesis(),))
                    object.__setattr__(forged, "run_id", bad)
                    with self.assertRaises(AlphaFunnelError):
                        store.run_path(forged)

    def test_artifact_store_rejects_stale_manifest_results_hash(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))
        stale = {
            key: value
            for key, value in run.manifest.items()
        }
        stale["results_sha256"] = "0" * 64
        object.__setattr__(run, "manifest", stale)

        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            with self.assertRaisesRegex(AlphaFunnelError, "results SHA-256"):
                store.write(run)

    def test_artifact_store_is_idempotent_and_rejects_changed_existing_output(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))
        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            destination = store.write(run)
            self.assertEqual(store.write(run), destination)
            self.assertEqual((destination / "manifest.json").read_bytes(), run.manifest_bytes())
            (destination / "results.json").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(AlphaFunnelError, "differs"):
                store.write(run)

    def test_existing_artifact_child_symlink_is_rejected_before_read(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))
        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            destination = store.write(run)
            original_is_symlink = Path.is_symlink

            def report_manifest_as_symlink(candidate: Path) -> bool:
                return (
                    candidate == destination / "manifest.json"
                    or original_is_symlink(candidate)
                )

            with patch.object(Path, "is_symlink", new=report_manifest_as_symlink):
                with self.assertRaisesRegex(AlphaFunnelError, "artifact child"):
                    store.write(run)

    def test_concurrent_identical_publication_is_complete_and_idempotent(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))
        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            with ThreadPoolExecutor(max_workers=2) as executor:
                destinations = tuple(executor.map(lambda _: store.write(run), range(2)))
            self.assertEqual(destinations[0], destinations[1])
            self.assertEqual(
                {item.name for item in destinations[0].iterdir()},
                {"manifest.json", "results.json"},
            )
            self.assertEqual(
                (destinations[0] / "manifest.json").read_bytes(),
                run.manifest_bytes(),
            )
            self.assertEqual(
                (destinations[0] / "results.json").read_bytes(),
                run.results_bytes(),
            )

    def test_durable_artifact_load_is_immutable_and_not_write_authority(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))
        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            destination = store.write(run)

            verified = store.load_verified(run.run_id)

            self.assertIs(type(verified), VerifiedScreeningArtifact)
            self.assertEqual(verified.run_id, run.run_id)
            self.assertEqual(verified.results_sha256, run.manifest["results_sha256"])
            self.assertEqual(verified.manifest_bytes(), (destination / "manifest.json").read_bytes())
            self.assertEqual(verified.results_bytes(), (destination / "results.json").read_bytes())
            with self.assertRaises(TypeError):
                verified.manifest["run_id"] = "0" * 64
            with self.assertRaises(TypeError):
                verified.results[0]["hypothesis_id"] = "forged"
            with self.assertRaisesRegex(AlphaFunnelError, "requires ScreeningRun"):
                store.write(verified)
            with self.assertRaises(AlphaFunnelError):
                VerifiedScreeningArtifact()

    def test_durable_load_rejects_wrong_run_id_and_unsafe_child_paths(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))
        with TemporaryDirectory() as temporary:
            store = AlphaFunnelArtifactStore(Path(temporary))
            destination = store.write(run)
            for invalid in (
                "../escape", r"..\escape", "0" * 63, "A" * 64, "0" * 64,
            ):
                with self.subTest(run_id=invalid), self.assertRaises(AlphaFunnelError):
                    store.load_verified(invalid)

            original_is_symlink = Path.is_symlink
            def report_manifest_as_symlink(candidate: Path) -> bool:
                return (
                    candidate == destination / "manifest.json"
                    or original_is_symlink(candidate)
                )
            with patch.object(Path, "is_symlink", new=report_manifest_as_symlink):
                with self.assertRaisesRegex(AlphaFunnelError, "artifact child"):
                    store.load_verified(run.run_id)

            outside = Path(temporary) / "outside-manifest.json"
            outside.write_bytes(run.manifest_bytes())
            original_resolve = Path.resolve
            manifest_path = destination / "manifest.json"
            def redirect_manifest(candidate: Path, strict: bool = False) -> Path:
                if candidate == manifest_path:
                    return original_resolve(outside, strict=strict)
                return original_resolve(candidate, strict=strict)
            with patch.object(Path, "resolve", new=redirect_manifest):
                with self.assertRaisesRegex(AlphaFunnelError, "artifact child"):
                    store.load_verified(run.run_id)

            manifest_path.unlink()
            manifest_path.mkdir()
            with self.assertRaisesRegex(AlphaFunnelError, "artifact child"):
                store.load_verified(run.run_id)

    def test_durable_load_rejects_manifest_and_result_tampering(self) -> None:
        def write_document(path: Path, document: object) -> None:
            path.write_bytes(canonical_json_bytes(document))

        cases = (
            "result_value",
            "results_hash",
            "manifest_run_id",
            "fdr_universe",
            "missing_fdr_test",
            "additional_fdr_test",
            "hypothesis_definition",
            "evaluator_identity",
            "q_value",
            "dataset_role",
            "dataset_digest",
            "missing_result",
            "additional_result",
            "noncanonical_json",
        )
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                run = run_screen((dataset(),), (hypothesis(),))
                store = AlphaFunnelArtifactStore(Path(temporary))
                destination = store.write(run)
                manifest_path = destination / "manifest.json"
                results_path = destination / "results.json"
                manifest = json.loads(manifest_path.read_bytes())
                results_document = json.loads(results_path.read_bytes())

                if case == "result_value":
                    results_document["results"][0]["raw_event_count"] += 1
                    write_document(results_path, results_document)
                elif case == "results_hash":
                    manifest["results_sha256"] = "0" * 64
                    write_document(manifest_path, manifest)
                elif case == "manifest_run_id":
                    manifest["run_id"] = "0" * 64
                    write_document(manifest_path, manifest)
                elif case == "fdr_universe":
                    manifest["configuration"]["batch_id"] = "0" * 64
                    write_document(manifest_path, manifest)
                elif case == "missing_fdr_test":
                    manifest["configuration"]["fdr_universe"]["tests"].pop()
                    write_document(manifest_path, manifest)
                elif case == "additional_fdr_test":
                    universe_tests = manifest["configuration"]["fdr_universe"]["tests"]
                    universe_tests.append(dict(universe_tests[0]))
                    write_document(manifest_path, manifest)
                elif case == "hypothesis_definition":
                    manifest["configuration"]["fdr_universe"]["tests"][0][
                        "hypothesis_definition_sha256"
                    ] = "0" * 64
                    write_document(manifest_path, manifest)
                elif case == "evaluator_identity":
                    results_document["results"][0][
                        "evaluator_implementation_id"
                    ] = "forged-evaluator-v999"
                    write_document(results_path, results_document)
                    manifest["results_sha256"] = sha256(results_path.read_bytes()).hexdigest()
                    write_document(manifest_path, manifest)
                elif case == "q_value":
                    results_document["results"][0]["multiple_testing"][
                        "q_value"
                    ] = "0"
                    write_document(results_path, results_document)
                    manifest["results_sha256"] = sha256(results_path.read_bytes()).hexdigest()
                    write_document(manifest_path, manifest)
                elif case == "dataset_role":
                    manifest["datasets"][0]["role"] = "sealed_oos"
                    write_document(manifest_path, manifest)
                elif case == "dataset_digest":
                    manifest["datasets"][0]["content_sha256"] = "0" * 64
                    write_document(manifest_path, manifest)
                elif case == "missing_result":
                    results_document["results"].pop()
                    write_document(results_path, results_document)
                    manifest["results_sha256"] = sha256(results_path.read_bytes()).hexdigest()
                    write_document(manifest_path, manifest)
                elif case == "additional_result":
                    results_document["results"].append(results_document["results"][0])
                    write_document(results_path, results_document)
                    manifest["results_sha256"] = sha256(results_path.read_bytes()).hexdigest()
                    write_document(manifest_path, manifest)
                else:
                    manifest_path.write_text(
                        json.dumps(manifest, indent=2), encoding="utf-8"
                    )

                with self.assertRaises(AlphaFunnelError):
                    store.load_verified(run.run_id)

    def test_durable_load_survives_a_fresh_python_process(self) -> None:
        process_a = r'''
import sys
from pathlib import Path
from tests.unit.test_alpha_funnel import dataset, hypothesis, run_screen
from quantos.infrastructure.storage.alpha_funnel import AlphaFunnelArtifactStore
run = run_screen((dataset(),), (hypothesis(),))
AlphaFunnelArtifactStore(Path(sys.argv[1])).write(run)
print(run.run_id)
'''
        process_b = r'''
import json
import sys
from hashlib import sha256
from pathlib import Path
import quantos.domain.evaluation.alpha_funnel as af1
from quantos.infrastructure.storage.alpha_funnel import AlphaFunnelArtifactStore
def forbidden(*args, **kwargs):
    raise AssertionError("evaluator must not execute during durable load")
af1.screen_alpha_funnel = forbidden
artifact = AlphaFunnelArtifactStore(Path(sys.argv[1])).load_verified(sys.argv[2])
manifest = artifact.manifest
universe = manifest["configuration"]["fdr_universe"]
hypotheses = {item["stable_id"]: item for item in manifest["hypotheses"]}
assert artifact.run_id == sys.argv[2]
assert artifact.results_sha256 == sha256(artifact.results_bytes()).hexdigest()
assert manifest["run_id"] == artifact.run_id
assert universe["universe_id"] == manifest["configuration"]["batch_id"]
for result in artifact.results:
    hypothesis = hypotheses[result["hypothesis_id"]]
    assert result["evaluator_implementation_id"] == hypothesis["implementation_id"]
    assert result["multiple_testing"]["fdr_universe_id"] == universe["universe_id"]
assert json.loads(artifact.manifest_bytes())["run_id"] == artifact.run_id
assert json.loads(artifact.results_bytes())["run_id"] == artifact.run_id
print(artifact.run_id)
'''
        repository = Path(__file__).resolve().parents[2]
        with TemporaryDirectory() as temporary:
            created = subprocess.run(
                [sys.executable, "-B", "-c", process_a, temporary],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            run_id = created.stdout.strip()
            loaded = subprocess.run(
                [sys.executable, "-B", "-c", process_b, temporary, run_id],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(loaded.stdout.strip(), run_id)

    def test_canonical_json_bytes_and_hash_golden(self) -> None:
        payload = canonical_json_bytes({"b": 2, "a": [1, True]})

        self.assertEqual(payload, b'{"a":[1,true],"b":2}\n')
        self.assertEqual(
            sha256(payload).hexdigest(),
            "e30c4e7dc6f4a2a58bb7c2fe9f130aa745858fb852a0e736e1e084c3182e2d58",
        )

    def test_custom_t_distribution_matches_representative_oracle(self) -> None:
        statistic, p_value = _t_statistic_and_p((D(1), D(2), D(4), D(8)))

        self.assertAlmostEqual(float(statistic), 2.422718559261745, places=14)
        self.assertAlmostEqual(float(p_value), 0.09393995513578189, places=15)

    def test_empty_and_one_event_behavior(self) -> None:
        empty = row(run_screen(
            (dataset(),),
            (hypothesis(lambda window: False),),
        ), 5)
        self.assertEqual(empty["deoverlapped_event_count"], 0)
        self.assertIsNone(empty["gross_expectancy"])
        self.assertIsNone(empty["bootstrap_confidence_interval"]["lower"])
        self.assertEqual(empty["provisional_classification"], "KILL")

        explicit = hypothesis(lambda window: window[-1].open == D(100))
        one = row(run_screen((dataset(),), (explicit,)), 10)
        self.assertEqual(one["deoverlapped_event_count"], 1)
        self.assertEqual(
            one["bootstrap_confidence_interval"]["lower"],
            one["bootstrap_confidence_interval"]["upper"],
        )
        self.assertIsNone(one["standard_error"])
        self.assertIsNone(one["descriptive_t_statistic"])
        self.assertIsNone(one["raw_p_value"])

    def test_human_explainability_is_never_invented(self) -> None:
        result = row(run_screen(
            (dataset(),),
            (hypothesis(explainability=None),),
        ), 1)
        scores = result["research_viability_score"]

        self.assertIsNone(scores["X"])
        self.assertIsNone(scores["RVS"])
        self.assertEqual(scores["X_source"], "not_supplied")
        self.assertNotEqual(result["provisional_classification"], "PROMOTE")

    def test_malformed_hypothesis_metadata_is_rejected(self) -> None:
        base = hypothesis().metadata
        cases = (
            {"stable_id": "Bad ID"},
            {"implementation_id": "Bad Implementation"},
            {"required_inputs": ("future_close",)},
            {"causal_lookback": 0},
            {"human_explainability_score": 5},
            {"parameters": {"float": 1.5}},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(AlphaFunnelError):
                replace(base, **changes)

    def test_manifest_records_required_research_contracts(self) -> None:
        run = run_screen((dataset(),), (hypothesis(),))
        manifest = run.manifest

        self.assertEqual(manifest["schema_version"], AF1_SCHEMA_VERSION)
        self.assertEqual(manifest["decimal_policy"]["precision"], 50)
        self.assertEqual(
            manifest["configuration"]["batch_id"],
            manifest["configuration"]["fdr_universe"]["universe_id"],
        )
        self.assertEqual(
            manifest["configuration"]["costs"][0]["round_trip_rate_unit"],
            COST_RATE_UNIT,
        )
        self.assertFalse(manifest["production_approved"])
        self.assertTrue(manifest["research_only"])
        self.assertEqual(
            sha256(run.results_bytes()).hexdigest(),
            manifest["results_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
