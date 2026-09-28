"""Deterministic synthetic-only AF4C proofs; repository catalog is the only input file."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta, timezone
from decimal import Decimal, Inexact, ROUND_DOWN, ROUND_UP, localcontext
from hashlib import sha256
import ast
import inspect
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quantos.domain.evaluation import af4c as a
from quantos.domain.evaluation import alpha_funnel as af1
from quantos.domain.market_data.contracts import Candle
from quantos.domain.market_data.research_events.aggregate_trade import (
    ResearchEventValidationStatus, SourceTimestampUnit,
)
from quantos.domain.market_data.research_events.minute_state import (
    AggregateTradeMinuteState, AggregateTradeMinuteCompletenessState,
    AggregateTradeMinuteAvailabilityState,
)

D = Decimal
ROOT = Path(__file__).resolve().parents[2]
CATALOG = a.FrozenCatalog((ROOT / "research/alpha-funnel/af4c/catalog.json").read_bytes())
EVALUATIONS = CATALOG.evaluations


def observation(index=0, *, symbol="BTCUSDT", close="11", open="10",
                buy_b="5", sell_b="5", buy_q="60", sell_q="40"):
    with a.af1_decimal_context():
        return a.MinuteObservation(symbol, a.START + index * a.MINUTE, D(open), D(close),
            D(buy_b) + D(sell_b), D(buy_q) + D(sell_q),
            D(buy_b), D(sell_b), D(buy_q), D(sell_q))


def zero(index=0, symbol="BTCUSDT"):
    return observation(index, symbol=symbol, buy_b="0", sell_b="0", buy_q="0", sell_q="0")


def fixture(observations=None, count=24):
    if observations is None:
        observations = tuple(observation(i, symbol=s, close=str(11 + i % 3))
                             for s in ("BTCUSDT", "ETHUSDT") for i in range(count))
    points = {(r.symbol, r.minute): a.CandlePoint(r.symbol, r.minute, r.open, r.close)
              for r in observations}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for i in range(count + 6):
            points.setdefault((symbol, a.START + i * a.MINUTE),
                              a.CandlePoint(symbol, a.START + i * a.MINUTE, D(10), D(11 + i % 3)))
    return a.SyntheticInputs("synthetic:hand-fixture-v1", tuple(observations), tuple(points.values()))


def domain_candle(index=0, symbol="BTCUSDT"):
    minute = a.START + index * a.MINUTE
    return Candle(symbol, "1m", minute, minute + a.MINUTE - timedelta(microseconds=1),
                  D(10), D(12), D(9), D(11), D(10), D(100), 2)


def domain_state(index=0):
    minute = a.START + index * a.MINUTE
    return AggregateTradeMinuteState(
        symbol="BTCUSDT", minute_start_time=minute, minute_end_time_exclusive=minute + a.MINUTE,
        source_range_id="1" * 64, source_manifest_id="2" * 64,
        source_revision_id="3" * 64, source_dataset_id="4" * 64,
        source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
        event_count=2, aggressive_buy_event_count=1, aggressive_sell_event_count=1,
        total_base_quantity=D(10), total_quote_notional=D(100),
        aggressive_buy_base_quantity=D(5), aggressive_sell_base_quantity=D(5),
        aggressive_buy_quote_notional=D(60), aggressive_sell_quote_notional=D(40),
        first_aggregate_trade_id=1, last_aggregate_trade_id=2,
        first_event_time=minute, last_event_time=minute + timedelta(seconds=1),
        completeness_state=AggregateTradeMinuteCompletenessState.VALIDATED_SOURCE_COMPLETE,
        availability_state=AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN,
        validation_status=ResearchEventValidationStatus.VALIDATED,
    )


class FormulaTests(unittest.TestCase):
    def test_h1_true_and_sign_boundaries(self):
        for row, expected in ((observation(), True),
                              (observation(buy_q="50", sell_q="50"), False),
                              (observation(close="10"), False)):
            with self.subTest(row=row):
                self.assertEqual(a.triggers(EVALUATIONS[0], (row,)), (expected, True))

    def test_h1_undefined_denominators(self):
        self.assertIsNone(a.triggers(EVALUATIONS[0], (zero(),)))
        for row in (observation(buy_b="0", sell_b="0"),
                    observation(buy_q="0", sell_q="0")):
            self.assertIsNone(a.triggers(EVALUATIONS[0], (row,)))

    def test_h2_side_order_and_displacement(self):
        for buy, sell, close, expected in (("60", "40", "11", True),
                                         ("50", "50", "11", False),
                                         ("40", "60", "11", False),
                                         ("60", "40", "10", False)):
            with self.subTest(buy=buy, close=close):
                result = a.triggers(EVALUATIONS[2], (observation(buy_q=buy, sell_q=sell, close=close),))
                self.assertEqual(result[0], expected)

    def test_h2_each_undefined_side_is_ineligible(self):
        for name in ("buy_b", "sell_b", "buy_q", "sell_q"):
            with self.subTest(name=name):
                self.assertIsNone(a.triggers(EVALUATIONS[2], (observation(**{name: "0"}),)))

    def test_h3_acceleration_boundaries(self):
        for prior, current, expected in (("11", "12", True), ("11", "11", False),
                                         ("12", "11", False), ("9", "10", False),
                                         ("8", "9", False)):
            with self.subTest(prior=prior, current=current):
                self.assertEqual(a.triggers(EVALUATIONS[4],
                    (observation(0, close=prior), observation(1, close=current)))[0], expected)

    def test_h3_prior_undefined_and_missing(self):
        self.assertIsNone(a.triggers(EVALUATIONS[4], (zero(), observation(1))))
        self.assertIsNone(a.triggers(EVALUATIONS[4], (observation(1),)))
        self.assertIsNone(a.triggers(EVALUATIONS[4], (observation(), observation(2))))

    def test_h4_window_and_boundaries(self):
        window = tuple(observation(i) for i in range(5))
        self.assertEqual(a.triggers(EVALUATIONS[6], window), (True, True))
        self.assertIsNone(a.triggers(EVALUATIONS[6], window[1:]))
        self.assertIsNone(a.triggers(EVALUATIONS[6], window[:4] + (observation(5),)))
        self.assertFalse(a.triggers(EVALUATIONS[6], tuple(
            observation(i, buy_q="50", sell_q="50") for i in range(5)))[0])
        self.assertFalse(a.triggers(EVALUATIONS[6], tuple(
            observation(i, close="10") for i in range(5)))[0])

    def test_h4_aggregate_denominators_not_individual_vwaps(self):
        self.assertIsNone(a.triggers(EVALUATIONS[6], tuple(zero(i) for i in range(5))))
        # One validated empty minute is valid when the five-minute sums are defined.
        window = (zero(),) + tuple(observation(i) for i in range(1, 5))
        self.assertTrue(a.triggers(EVALUATIONS[6], window)[0])

    def test_h5_eth_nonpositive_inclusive(self):
        for close, expected in (("9", True), ("10", True), ("11", False)):
            with self.subTest(close=close):
                result = a.triggers(EVALUATIONS[8], (observation(),),
                                    observation(symbol="ETHUSDT", close=close))
                self.assertEqual(result[0], expected)

    def test_h5_btc_zero_boundaries(self):
        for btc in (observation(buy_q="50", sell_q="50"), observation(close="10")):
            self.assertFalse(a.triggers(EVALUATIONS[8], (btc,),
                                       observation(symbol="ETHUSDT", close="10"))[0])

    def test_h5_alignment_and_missing(self):
        for eth in (None, observation(1, symbol="ETHUSDT"), observation(), zero(symbol="ETHUSDT")):
            self.assertIsNone(a.triggers(EVALUATIONS[8], (observation(),), eth))
        self.assertEqual([e.row["target_symbol"] for e in EVALUATIONS if e.hypothesis == 5], ["ETHUSDT"])

    def test_fixed_comparators_independently(self):
        # H1 unconditional; H2/H3/H4 candle rules can disagree with DE1 rules.
        self.assertEqual(a.triggers(EVALUATIONS[0], (observation(close="9"),)), (False, True))
        self.assertEqual(a.triggers(EVALUATIONS[2], (observation(open="12"),)), (True, False))
        self.assertEqual(a.triggers(EVALUATIONS[4],
            (observation(0, close="11", open="5"), observation(1, close="12", open="10"))), (True, False))
        window = (observation(0, open="12"),) + tuple(observation(i) for i in range(1, 5))
        self.assertEqual(a.triggers(EVALUATIONS[6], window), (True, False))
        self.assertEqual(a.triggers(EVALUATIONS[8], (observation(open="12"),),
            observation(symbol="ETHUSDT", close="10")), (True, False))
        # The comparator may trigger while the candidate does not, for every H.
        cases = [(2, (observation(buy_q="40", sell_q="60"),), None),
                 (4, (observation(0, close="12", open="12"), observation(1, close="11")), None),
                 (6, tuple(observation(i, buy_q="40", sell_q="60") for i in range(5)), None),
                 (8, (observation(buy_q="50", sell_q="50"),), observation(symbol="ETHUSDT", close="10"))]
        for index, window, eth in cases:
            self.assertEqual(a.triggers(EVALUATIONS[index], window, eth), (False, True))


class BoundaryTests(unittest.TestCase):
    def test_strict_domain_adapter(self):
        row = a.align_observation(domain_state(), domain_candle(), completed_as_of=a.START + a.MINUTE + timedelta(seconds=5))
        self.assertEqual(row, observation())
        self.assertEqual(set(f.name for f in fields(row)),
                         {"symbol", "minute", "open", "close", *a.DE1_FIELDS})
        self.assertFalse(any(hasattr(row, key) for key in ("high", "low", "volume", "trade_count")))

    def test_adapter_symbol_and_minute_mismatch(self):
        for candle in (domain_candle(1), domain_candle(symbol="ETHUSDT")):
            self.assertIsNone(a.align_observation(domain_state(), candle, completed_as_of=a.START + 3 * a.MINUTE))

    def test_incomplete_or_not_yet_knowable_rejected(self):
        with self.assertRaises(ValueError):
            a.adapt_candle(domain_candle(), completed_as_of=a.START)
        with self.assertRaises(ValueError):
            a.align_observation(domain_state(), domain_candle(), completed_as_of=a.START + a.MINUTE)

    def test_bad_state_and_bad_candle_interval_rejected(self):
        state = domain_state()
        object.__setattr__(state, "validation_status", ResearchEventValidationStatus.UNVALIDATED)
        with self.assertRaises(ValueError):
            a.align_observation(state, domain_candle(), completed_as_of=a.END)
        with self.assertRaises(ValueError):
            a.adapt_candle(replace(domain_candle(), close_time=a.START + 2 * a.MINUTE), completed_as_of=a.END)

    def test_utc_exactness_prices_and_input_types(self):
        for minute in (a.START.replace(tzinfo=None), a.START + timedelta(seconds=1),
                       a.START.astimezone(timezone(timedelta(hours=7)))):
            with self.subTest(minute=minute), self.assertRaises(ValueError):
                replace(observation(), minute=minute)
        for value in (D(0), D(-1), D("NaN"), 10.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(observation(), open=value)

    def test_support_first_and_common_last(self):
        for evaluation in EVALUATIONS:
            first = a.START + (evaluation.lookback - 1) * a.MINUTE
            self.assertTrue(a.state_supported(evaluation, first))
            self.assertFalse(a.state_supported(evaluation, first - a.MINUTE))
            self.assertTrue(a.state_supported(evaluation, a.LAST_STATE))
            self.assertFalse(a.state_supported(evaluation, a.LAST_STATE + a.MINUTE))
        with self.assertRaises(ValueError):
            observation(-1)

    def test_tplus2_h1_adversarial(self):
        forward = (a.CandlePoint("BTCUSDT", a.START + 2 * a.MINUTE, D(100), D(110)),)
        self.assertEqual(a.tplus2_outcome(a.START, 1, forward, "BTCUSDT"), D("0.1"))
        tplus1 = (a.CandlePoint("BTCUSDT", a.START + a.MINUTE, D(1), D(900)),)
        self.assertIsNone(a.tplus2_outcome(a.START, 1, tplus1, "BTCUSDT"))

    def test_tplus2_h5_and_complete_path(self):
        forward = tuple(a.CandlePoint("BTCUSDT", a.START + i * a.MINUTE, D(100 + i - 2), D(110 + i))
                        for i in range(2, 7))
        self.assertEqual(a.tplus2_outcome(a.START, 5, forward, "BTCUSDT"), D("0.16"))
        self.assertIsNone(a.tplus2_outcome(a.START, 5, forward[:2] + forward[3:], "BTCUSDT"))
        self.assertIsNone(a.tplus2_outcome(a.START, 5, forward, "ETHUSDT"))

    def test_full_engine_tplus2_and_last_state(self):
        for evaluation_index, horizon in ((0, 1), (2, 5)):
            minute = a.LAST_STATE
            row = replace(observation(), minute=minute)
            candles = [a.CandlePoint("BTCUSDT", minute, row.open, row.close),
                       a.CandlePoint("BTCUSDT", minute + a.MINUTE, D(1), D(900))]
            candles.extend(a.CandlePoint("BTCUSDT", minute + i * a.MINUTE, D(100), D(110 + i))
                           for i in range(2, 7))
            inputs = a.SyntheticInputs("synthetic:chronology", (row,), tuple(candles))
            result = a.evaluate_synthetic(CATALOG, inputs).validate()["results"][evaluation_index]
            self.assertEqual(D(result["gross_expectancy"]), D("0.12") if horizon == 1 else D("0.16"))

    def test_missing_outcome_excluded_from_base_eligibility(self):
        inputs = fixture((observation(),))
        missing = replace(inputs, candles=tuple(c for c in inputs.candles
                                               if c.minute != a.START + 2 * a.MINUTE))
        rows = a.evaluate_synthetic(CATALOG, missing).validate()["results"]
        self.assertEqual(rows[0]["eligible_minutes"], 0)

    def test_input_identity_and_deep_immutability(self):
        inputs = fixture()
        with self.assertRaises(FrozenInstanceError):
            inputs.identity = "synthetic:other"
        with self.assertRaises(ValueError):
            replace(inputs, observations=list(inputs.observations))
        with self.assertRaises(ValueError):
            replace(inputs, identity="DEVELOPMENT")
        with self.assertRaises(ValueError):
            replace(inputs, observations=inputs.observations + inputs.observations[:1])
        with self.assertRaises(ValueError):
            replace(inputs, candles=(replace(inputs.candles[0], close=D(999)),) + inputs.candles[1:])


class StatisticsTests(unittest.TestCase):
    def test_inherited_exact_statistics_and_costs(self):
        vectors = ((), (D(0),), (D("0.2"), D("0.2")),
                   (D("-0.03"), D("0.01"), D("0.02"), D("0.04")))
        for values in vectors:
            with self.subTest(values=values), a.af1_decimal_context():
                row = a.return_evidence(values, "deterministic-parity")
                self.assertEqual(row["gross_expectancy"], af1._decimal(af1._mean(values)))
                self.assertEqual(row["standard_error"], af1._decimal(af1._standard_error(values)))
                t, p = af1._t_statistic_and_p(values)
                self.assertEqual((row["t_statistic"], row["p_value"]), (af1._decimal(t), af1._decimal(p)))
                ci = af1._bootstrap_interval(values, samples=200, confidence=D("0.95"), seed_material="deterministic-parity")
                self.assertEqual(row["bootstrap_interval"], [af1._decimal(v) for v in ci])
                if values:
                    self.assertEqual(D(row["base_cost_expectancy"]), af1._mean(values) - a.BASE_COST)
                    self.assertEqual(D(row["stress_2c_expectancy"]), af1._mean(values) - a.STRESS_2C)
                self.assertEqual(a.STRESS_2C, 2 * a.BASE_COST)

    def test_greedy_exact_state_spacing(self):
        indices = (0, 1, 4, 5, 9, 10)
        self.assertEqual(a._deoverlapped_events(indices, 5), (0, 5, 10))
        self.assertEqual(a._deoverlapped_events(indices, 1), indices)
        self.assertIs(a._deoverlapped_events, af1._deoverlapped_events)

    def test_fixed_bh_parity_ties_and_undefined(self):
        for values in ((None,) * 9, (D("0.001"),) + (None,) * 8,
                       (D("0.01"),) * 8 + (None,), (D("0.01"),) * 9):
            rows = tuple((e.evaluation_id, p) for e, p in zip(EVALUATIONS, values))
            self.assertEqual(a.fixed_bh(CATALOG, rows), af1.benjamini_hochberg(values))
            self.assertEqual(len(a.fixed_bh(CATALOG, rows)), 9)
        self.assertEqual(a.fixed_bh(CATALOG, tuple((e.evaluation_id, D("0.001") if i == 0 else None)
                                                  for i, e in enumerate(EVALUATIONS)))[0], D("0.009"))

    def test_bh_missing_extra_duplicate_unknown_and_reordering(self):
        rows = tuple((e.evaluation_id, D("0.01")) for e in EVALUATIONS)
        for bad in (rows[:-1], rows + rows[:1], rows[:-1] + rows[:1],
                    (("unknown", D("0.01")),) + rows[1:], tuple(reversed(rows))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                a.fixed_bh(CATALOG, bad)

    def test_block_boundaries_cover_exact_full_interval(self):
        self.assertEqual(a.block_index(a.START), 0)
        self.assertEqual(a.block_index(a.END - a.MINUTE), 7)
        boundaries = [(k * a.TOTAL_MINUTES + 7) // 8 for k in range(9)]
        self.assertEqual(sum(b - c for c, b in zip(boundaries, boundaries[1:])), a.TOTAL_MINUTES)
        for k in range(1, 8):
            minute = a.START + boundaries[k] * a.MINUTE
            self.assertEqual(a.block_index(minute - a.MINUTE), k - 1)
            self.assertEqual(a.block_index(minute), k)
        for minute in (a.START - a.MINUTE, a.END):
            with self.assertRaises(ValueError):
                a.block_index(minute)

    def test_robustness_coverage_and_positive_expected_denominator(self):
        for qualified, coverage in ((5, "0.625"), (6, "0.75"), (8, "1")):
            returns = tuple(((k * a.TOTAL_MINUTES + 7) // 8 + j, D("0.1"))
                            for k in range(qualified) for j in range(10))
            rows, evidence = a.robustness(returns)
            self.assertEqual(D(rows["qualified_block_coverage"]), D(coverage))
            self.assertEqual(rows["positive_qualified_block_count"], qualified)
            self.assertEqual(evidence, None if qualified == 5 else D(coverage))
            if qualified == 5:
                self.assertEqual(a.robustness_score(CATALOG.scoring.robustness, evidence, qualified)[0], 0)
            with a.af1_decimal_context():
                self.assertEqual((rows, evidence), af1._temporal_stability(returns, first_index=0,
                    eligible_count=a.TOTAL_MINUTES, blocks=8, minimum_block_coverage=D("0.75"), minimum_events_per_block=10))
        rows, _ = a.robustness(tuple((i, D(1)) for i in range(9)))
        self.assertFalse(rows["blocks"][0]["qualifies_for_robustness"])

    def test_maximum_r_cap(self):
        bands = CATALOG.scoring.robustness
        self.assertEqual(a.robustness_score(bands, D(1), 5), (3, 4, False))
        self.assertEqual(a.robustness_score(bands, D(1), 6), (4, 4, True))

    def test_score_thresholds_and_classification_parity(self):
        bands = CATALOG.scoring
        for band in (bands.magnitude, bands.statistical_strength, bands.opportunity_frequency, bands.robustness):
            for index, threshold in enumerate(band.thresholds):
                self.assertEqual(band.score(threshold), index + 1)
            self.assertEqual(band.score(None), 0)
        for count, base, q, rvs, expected in (
                (0, None, None, 4, "KILL"), (100, D("-0.1"), D(0), 20, "KILL"),
                (100, a.BASE_COST, D("0.05"), 13, "PROMOTE"),
                (99, a.BASE_COST, D("0.05"), 13, "WATCH"),
                (100, a.BASE_COST, D("0.051"), 13, "WATCH"),
                (100, D(0), D("0.05"), 13, "WATCH"),
                (100, a.BASE_COST, D("0.05"), 12, "WATCH")):
            self.assertEqual(a._classify(deoverlapped_count=count, base_expectancy=base, q_value=q,
                rvs=rvs, classification=a.CLASSIFICATION, fdr_threshold=D("0.05")).value, expected)

    def test_incremental_gate_separate_from_primary(self):
        for classification in a.ResearchClassification:
            for difference in (None, D(-1), D(0), D(1)):
                self.assertEqual(a.incremental_advance(classification, difference),
                    classification is a.ResearchClassification.PROMOTE and difference == D(1))

    def test_seed_binds_all_required_identities(self):
        seed = json.loads(a.seed_material(EVALUATIONS[0], "a" * 64))
        self.assertEqual(seed, {"root_seed": 20260914, "catalog_id": a.CATALOG_ID,
            "evaluation_id": EVALUATIONS[0].evaluation_id, "evaluator_id": a.EVALUATOR_ID,
            "input_identity_sha256": "a" * 64})


class EngineTests(unittest.TestCase):
    def test_all_nine_complete_engine_scores_match_inherited_rules(self):
        rows = a.evaluate_synthetic(CATALOG, fixture()).validate()["results"]
        with a.af1_decimal_context():
            for row in rows:
                base = D(row["base_cost_expectancy"]) if row["base_cost_expectancy"] is not None else None
                t = D(row["t_statistic"]) if row["t_statistic"] is not None else None
                frequency = (D(row["deoverlapped_event_count"]) * 1440 / row["eligible_minutes"]
                             if row["eligible_minutes"] else None)
                temporal = row["temporal_robustness"]
                ratio = temporal["robustness_evidence_ratio"]
                ratio = None if ratio is None else D(ratio)
                scoring = CATALOG.scoring
                uncapped = scoring.robustness.score(ratio)
                expected_r = min(uncapped, 3) if temporal["positive_qualified_block_count"] < 6 else uncapped
                expected = {"M": scoring.magnitude.score(base),
                            "S": scoring.statistical_strength.score(None if t is None else max(t, D(0))),
                            "F": scoring.opportunity_frequency.score(frequency), "R": expected_r, "X": 4}
                self.assertEqual(row["scores"], expected)
                self.assertEqual(row["rvs"], sum(expected.values()))
                q = None if row["q_value"] is None else D(row["q_value"])
                self.assertEqual(row["classification"], af1._classify(
                    deoverlapped_count=row["deoverlapped_event_count"], base_expectancy=base,
                    q_value=q, rvs=sum(expected.values()), classification=a.CLASSIFICATION,
                    fdr_threshold=D("0.05")).value)

    def test_negative_t_has_zero_strength(self):
        inputs = fixture((observation(), observation(1)))
        candles = tuple(replace(c, close=D(8) if c.minute == a.START + 2 * a.MINUTE else D(9))
                        if c.minute in (a.START + 2 * a.MINUTE, a.START + 3 * a.MINUTE)
                        else c for c in inputs.candles)
        row = a.evaluate_synthetic(CATALOG, replace(inputs, candles=candles)).validate()["results"][0]
        self.assertLess(D(row["t_statistic"]), 0)
        self.assertEqual(row["scores"]["S"], 0)
        self.assertEqual(row["classification"], "KILL")

    def test_qualified_block_event_counts_remain_fixed_across_sparse_input(self):
        observations, candles = [], {}
        for block in range(8):
            for offset in range(13):
                index = (block * a.TOTAL_MINUTES + 7) // 8 + offset * 3
                obs = observation(index)
                observations.append(obs)
                candles[(obs.symbol, obs.minute)] = a.CandlePoint(obs.symbol, obs.minute, obs.open, obs.close)
                point = a.CandlePoint(obs.symbol, obs.minute + 2 * a.MINUTE, D(100), D(110 + offset % 3))
                candles[(point.symbol, point.minute)] = point
        inputs = a.SyntheticInputs("synthetic:eight-blocks", tuple(observations), tuple(candles.values()))
        row = a.evaluate_synthetic(CATALOG, inputs).validate()["results"][0]
        self.assertEqual([b["event_count"] for b in row["temporal_robustness"]["blocks"]], [13] * 8)
        self.assertEqual(row["scores"]["R"], 4)
        self.assertEqual(row["classification"], "PROMOTE")
        # H1's unconditional comparator has identical selected events here.
        self.assertEqual(D(row["gross_expectancy_difference"]), 0)
        self.assertFalse(row["incremental_advance_eligible"])

    def test_undefined_is_not_eligible_for_candidate_or_comparator(self):
        inputs = fixture((zero(), observation(1)))
        row = a.evaluate_synthetic(CATALOG, inputs).validate()["results"][0]
        self.assertEqual(row["eligible_minutes"], 1)
        self.assertEqual(row["comparator_event_count"], 1)
        self.assertEqual(D(row["event_frequency_per_1440_eligible_minutes"]), D(1440))

    def test_candidate_and_comparator_independently_deoverlap(self):
        observations = tuple(observation(i, buy_q="40" if i == 0 else "60",
                                        sell_q="60" if i == 0 else "40") for i in range(11))
        row = a.evaluate_synthetic(CATALOG, fixture(observations)).validate()["results"][2]
        self.assertEqual([i for i, _ in row["events"]], [1, 6])
        self.assertEqual([i for i, _ in row["comparator_events"]], [0, 5, 10])
        self.assertEqual((row["deoverlapped_event_count"], row["comparator_event_count"]), (2, 3))
        with a.af1_decimal_context():
            expected = af1._mean(tuple(D(v) for _, v in row["events"])) - af1._mean(tuple(D(v) for _, v in row["comparator_events"]))
            self.assertEqual(D(row["gross_expectancy_difference"]), expected)
            self.assertEqual(D(row["comparator_blocks"][0]["difference"]), expected)
        self.assertIsNone(row["comparator_blocks"][1]["difference"])

    def test_comparator_cannot_change_candidate_p_or_q(self):
        inputs = fixture()
        original = a.evaluate_synthetic(CATALOG, inputs).validate()["results"]
        function = a.triggers
        def altered(*args):
            value = function(*args)
            return None if value is None else (value[0], False)
        with patch.object(a, "triggers", side_effect=altered):
            changed = a.evaluate_synthetic(CATALOG, inputs).validate()["results"]
        self.assertEqual([(r["p_value"], r["q_value"]) for r in original],
                         [(r["p_value"], r["q_value"]) for r in changed])

    def test_empty_family_stays_nine(self):
        result = a.evaluate_synthetic(CATALOG, a.SyntheticInputs("synthetic:empty", (), ())).validate()
        self.assertEqual(len(result["results"]), 9)
        self.assertTrue(all(r["p_value"] is None and r["q_value"] is None and r["classification"] == "KILL"
                            for r in result["results"]))

    def test_ambient_context_does_not_change_canonical_bytes(self):
        results = []
        for precision, rounding in ((6, ROUND_DOWN), (80, ROUND_UP)):
            with localcontext() as context:
                context.prec = precision
                context.rounding = rounding
                context.traps[Inexact] = True
                results.append(a.evaluate_synthetic(CATALOG, fixture()).payload)
        self.assertEqual(*results)

    def test_result_identity_immutability_and_input_order(self):
        inputs = fixture()
        result = a.evaluate_synthetic(CATALOG, inputs)
        self.assertEqual(result.payload_sha256, sha256(result.payload).hexdigest())
        with self.assertRaises(FrozenInstanceError):
            result.payload = b"changed"
        document = result.validate()
        document["results"][0]["classification"] = "changed"
        self.assertNotEqual(document, result.validate())
        reordered = replace(inputs, observations=tuple(reversed(inputs.observations)), candles=tuple(reversed(inputs.candles)))
        self.assertEqual(result.payload, a.evaluate_synthetic(CATALOG, reordered).payload)
        self.assertNotEqual(result.payload_sha256, a.evaluate_synthetic(CATALOG, replace(inputs, identity="synthetic:other")).payload_sha256)
        for key in ("catalog_id", "evaluator_id", "schema", "input_identity", "registered_evaluations", "results"):
            changed = result.validate()
            changed[key] = "changed"
            self.assertNotEqual(result.payload_sha256, sha256(af1.canonical_json_bytes(changed)).hexdigest())

    def test_changed_actual_inputs_change_result_identity(self):
        inputs = fixture((observation(),))
        changed = replace(inputs, candles=tuple(replace(c, close=D(900))
            if c.symbol == "BTCUSDT" and c.minute == a.START + 2 * a.MINUTE else c for c in inputs.candles))
        before, after = (a.evaluate_synthetic(CATALOG, data) for data in (inputs, changed))
        self.assertNotEqual(before.payload_sha256, after.payload_sha256)
        self.assertNotEqual(before.validate()["input_identity_sha256"], after.validate()["input_identity_sha256"])

    def test_metadata_records_inherited_method_and_frozen_configuration(self):
        doc = a.evaluate_synthetic(CATALOG, fixture()).validate()
        self.assertEqual(doc["p_value_method"], "two_sided_one_sample_student_t_against_zero")
        self.assertEqual(doc["numerical_policy"], af1.AF1_NUMERICS_VERSION)
        self.assertEqual(doc["preregistration_commit"], a.PREREGISTRATION_COMMIT)
        self.assertEqual(doc["classification_configuration"], a.CLASSIFICATION.as_dict())
        self.assertEqual(doc["catalog"]["statistical_contract"]["deterministic_bootstrap"]["samples"], 200)


class CatalogTests(unittest.TestCase):
    def test_exact_frozen_universe(self):
        document = json.loads(CATALOG.payload)
        self.assertEqual(document["catalog_id"], a.CATALOG_ID)
        self.assertEqual(document["fdr_family"]["fdr_family_id"], a.FDR_FAMILY_ID)
        self.assertEqual(len(document["hypotheses"]), 5)
        self.assertEqual(len(EVALUATIONS), 9)
        self.assertTrue(all(e.row["evaluator_id"] == a.EVALUATOR_ID for e in EVALUATIONS))
        self.assertEqual([e.hypothesis for e in EVALUATIONS], [1, 1, 2, 2, 3, 3, 4, 4, 5])
        self.assertEqual([e.horizon for e in EVALUATIONS], [1, 1, 5, 5, 1, 1, 5, 5, 5])

    def test_any_catalog_mutation_even_reidentified_fails(self):
        document = json.loads(CATALOG.payload)
        mutations = [lambda d: d["evaluations"].pop(),
                     lambda d: d["evaluations"].append(d["evaluations"][0]),
                     lambda d: d["evaluations"].reverse(),
                     lambda d: d["evaluations"][0].update(horizon_minutes=5),
                     lambda d: d["evaluations"][0].update(source_symbols=["ETHUSDT"]),
                     lambda d: d["evaluations"][0].update(target_symbol="ETHUSDT"),
                     lambda d: d["evaluations"][0].update(evaluator_id="changed"),
                     lambda d: d["hypotheses"][0].update(formula="changed")]
        for mutate in mutations:
            altered = deepcopy(document)
            mutate(altered)
            for rehash in (False, True):
                if rehash:
                    altered.pop("catalog_id")
                    altered["catalog_id"] = sha256(af1.canonical_json_bytes(altered)).hexdigest()
                with self.assertRaises(ValueError):
                    a.FrozenCatalog(af1.canonical_json_bytes(altered))
        for key in document:
            altered = deepcopy(document)
            altered[key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                a.FrozenCatalog(af1.canonical_json_bytes(altered))

    def test_noncanonical_and_duplicate_keys_fail(self):
        for payload in (b"{}", b"[]", b'{"a":1,"a":2}\n', CATALOG.payload.rstrip(),
                        json.dumps(json.loads(CATALOG.payload), indent=2).encode()):
            with self.assertRaises(ValueError):
                a.FrozenCatalog(payload)

    def test_no_io_or_acquisition_in_domain(self):
        source = inspect.getsource(a)
        imports = [node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and "infrastructure" in name for name in imports))
        for forbidden in ("open(", "read_bytes(", "read_parquet", "requests", "urllib", "httpx", "Binance"):
            self.assertNotIn(forbidden, source)
        self.assertFalse(hasattr(a, "main"))


if __name__ == "__main__":
    unittest.main()
