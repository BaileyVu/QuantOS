"""Frozen AF4C research screen, currently restricted to synthetic in-memory inputs.

Private AF1 imports intentionally pin AF1/AF3 methodology. No AF1 outcome
indexing is reused. This module has no filesystem, acquisition or runtime hooks.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Sequence

from quantos.domain.common import require_utc, require_v1_symbol
from quantos.domain.evaluation.alpha_funnel import (
    AF1_NUMERICS_VERSION, EvidenceScoringConfig,
    ProvisionalClassificationConfig, ResearchClassification, ScoreBands,
    _bootstrap_interval, _classify, _decimal, _deoverlapped_events, _mean,
    _standard_error, _t_statistic_and_p, _temporal_stability,
    af1_decimal_context, benjamini_hochberg, canonical_json_bytes,
)
from quantos.domain.market_data.contracts import Candle
from quantos.domain.market_data.research_events.minute_state import AggregateTradeMinuteState

CATALOG_ID = "71b244b9843bc6c105f9b761f23437984e4ee87ee6c5156f300e72c8bb785350"
FDR_FAMILY_ID = "f0d87b0935d56a8c53f3939304c3c071cc4d6ec33d59ef9029a775322432bd42"
EVALUATOR_ID = "af4c-impact-screen-tplus2-v1"
PREREGISTRATION_COMMIT = "8a48faa0365e990de1bc13511016ca6bef746d96"
IMPLEMENTATION_VERSION = "af4c-synthetic-evaluator-v1"
P_VALUE_METHOD = "two_sided_one_sample_student_t_against_zero"
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 10, 1, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
LAST_STATE = END - 7 * MINUTE
TOTAL_MINUTES = (END - START) // MINUTE
BASE_COST = Decimal("0.002503128284573645913980997751940")
STRESS_2C = Decimal("0.005006256569147291827961995503880")
CLASSIFICATION = ProvisionalClassificationConfig(13, 100, BASE_COST, Decimal(0))
DE1_FIELDS = (
    "total_base_quantity", "total_quote_notional",
    "aggressive_buy_base_quantity", "aggressive_sell_base_quantity",
    "aggressive_buy_quote_notional", "aggressive_sell_quote_notional",
)


def _parse(payload: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate evidence key")
            result[key] = value
        return result
    if type(payload) is not bytes:
        raise ValueError("canonical evidence must be bytes")
    document = json.loads(payload, object_pairs_hook=unique)
    if type(document) is not dict or canonical_json_bytes(document) != payload:
        raise ValueError("noncanonical evidence")
    return document


@dataclass(frozen=True, slots=True)
class FrozenCatalog:
    """Caller supplies repository-local catalog bytes; no discovery or loading here."""

    payload: bytes

    def __post_init__(self):
        document = _parse(self.payload)
        identity = document.pop("catalog_id", None)
        if identity != CATALOG_ID or sha256(canonical_json_bytes(document)).hexdigest() != CATALOG_ID:
            raise ValueError("catalog differs from frozen AF4C declaration")
        # The pinned digest binds every field, type, formula, order and identity.
        if len(document["hypotheses"]) != 5 or len(document["evaluations"]) != 9:
            raise ValueError("AF4C requires five hypotheses and nine evaluations")

    @property
    def evaluations(self) -> tuple[Evaluation, ...]:
        return tuple(Evaluation(canonical_json_bytes(row))
                     for row in _parse(self.payload)["evaluations"])

    @property
    def scoring(self) -> EvidenceScoringConfig:
        bands = _parse(self.payload)["statistical_contract"]["score_bands"]
        return EvidenceScoringConfig(*(ScoreBands(tuple(Decimal(v) for v in bands[k]))
                                       for k in ("M", "S", "F", "R")))


@dataclass(frozen=True, slots=True)
class Evaluation:
    definition: bytes

    @property
    def row(self):
        return _parse(self.definition)

    @property
    def evaluation_id(self):
        return self.row["evaluation_id"]

    @property
    def hypothesis(self):
        return int(self.row["hypothesis_id"].split(".")[1][1:])

    @property
    def horizon(self):
        return self.row["horizon_minutes"]

    @property
    def lookback(self):
        return {1: 1, 2: 1, 3: 2, 4: 5, 5: 1}[self.hypothesis]


def _minute(value: datetime) -> None:
    require_utc(value, "minute")
    if value.second or value.microsecond:
        raise ValueError("exact UTC minute required")


def minute_index(value: datetime) -> int:
    _minute(value)
    if not START <= value < END:
        raise ValueError("minute outside frozen support")
    return (value - START) // MINUTE


def block_index(value: datetime) -> int:
    return minute_index(value) * 8 // TOTAL_MINUTES


def state_supported(evaluation: Evaluation, value: datetime) -> bool:
    _minute(value)
    return START + (evaluation.lookback - 1) * MINUTE <= value <= LAST_STATE


def _value(value: Decimal, *, positive: bool = False) -> None:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError("finite nonnegative Decimal required")
    if positive and value == 0:
        raise ValueError("positive price required")


@dataclass(frozen=True, slots=True)
class CandlePoint:
    """Only permitted prices cross the outcome boundary."""

    symbol: str
    minute: datetime
    open: Decimal
    close: Decimal

    def __post_init__(self):
        require_v1_symbol(self.symbol)
        minute_index(self.minute)
        _value(self.open, positive=True)
        _value(self.close, positive=True)


@dataclass(frozen=True, slots=True)
class MinuteObservation:
    symbol: str
    minute: datetime
    open: Decimal
    close: Decimal
    total_base_quantity: Decimal
    total_quote_notional: Decimal
    aggressive_buy_base_quantity: Decimal
    aggressive_sell_base_quantity: Decimal
    aggressive_buy_quote_notional: Decimal
    aggressive_sell_quote_notional: Decimal

    def __post_init__(self):
        CandlePoint(self.symbol, self.minute, self.open, self.close)
        for name in DE1_FIELDS:
            _value(getattr(self, name))
        with af1_decimal_context():
            for total, buy, sell in ((self.total_base_quantity,
                                      self.aggressive_buy_base_quantity,
                                      self.aggressive_sell_base_quantity),
                                     (self.total_quote_notional,
                                      self.aggressive_buy_quote_notional,
                                      self.aggressive_sell_quote_notional)):
                if buy + sell != total:
                    raise ValueError("inconsistent side totals")


def adapt_candle(candle: Candle, *, completed_as_of: datetime) -> CandlePoint:
    if type(candle) is not Candle:
        raise ValueError("domain Candle required")
    require_utc(completed_as_of, "completed_as_of")
    _minute(candle.open_time)
    if (candle.interval != "1m" or not candle.is_complete_at(completed_as_of)
            or not candle.open_time < candle.close_time < candle.open_time + MINUTE):
        raise ValueError("completed canonical minute Candle required")
    return CandlePoint(candle.symbol, candle.open_time, candle.open, candle.close)


def align_observation(state: AggregateTradeMinuteState, candle: Candle,
                      *, completed_as_of: datetime) -> MinuteObservation | None:
    """Strict join of already validated historical state; no live availability claim.

    Future authorization/source-health/DE1G gates belong upstream. This phase's
    only screen entry point accepts explicitly synthetic identities.
    """
    if type(state) is not AggregateTradeMinuteState:
        raise ValueError("validated domain DE1 state required")
    with af1_decimal_context():
        state.__post_init__()
        point = adapt_candle(candle, completed_as_of=completed_as_of)
        if state.symbol != point.symbol or state.minute_start_time != point.minute:
            return None
        if completed_as_of < state.minute_end_time_exclusive + timedelta(seconds=5):
            raise ValueError("DE1 t+1m+5s knowability not reached")
        return MinuteObservation(point.symbol, point.minute, point.open, point.close,
                                 *(getattr(state, name) for name in DE1_FIELDS))


def _displacement(row: MinuteObservation) -> Decimal | None:
    if row.total_base_quantity <= 0 or row.total_quote_notional <= 0:
        return None
    vwap = row.total_quote_notional / row.total_base_quantity
    return (row.close - vwap) / vwap


def triggers(evaluation: Evaluation, window: tuple[MinuteObservation, ...],
             eth: MinuteObservation | None = None) -> tuple[bool, bool] | None:
    """Return candidate/comparator triggers, or undefined base signal eligibility.

    Receives only the exact causal lookback. No outcomes or future candles.
    """
    if type(window) is not tuple or len(window) != evaluation.lookback:
        return None
    current = window[-1]
    expected_symbol = evaluation.row["source_symbols"][0]
    if (not state_supported(evaluation, current.minute)
            or any(row.symbol != expected_symbol or row.minute != current.minute
                   - (len(window) - 1 - i) * MINUTE for i, row in enumerate(window))):
        return None
    with af1_decimal_context():
        h = evaluation.hypothesis
        if h == 4:
            q = sum((r.total_quote_notional for r in window), Decimal(0))
            b = sum((r.total_base_quantity for r in window), Decimal(0))
            if q <= 0 or b <= 0:
                return None
            imbalance = sum((r.aggressive_buy_quote_notional - r.aggressive_sell_quote_notional
                             for r in window), Decimal(0)) / q
            return imbalance > 0 and current.close > q / b, current.close > window[0].open
        displacement = _displacement(current)
        if displacement is None:
            return None
        imbalance = (current.aggressive_buy_quote_notional - current.aggressive_sell_quote_notional) / current.total_quote_notional
        if h == 1:
            return imbalance > 0 and displacement > 0, True
        if h == 2:
            if any(getattr(current, name) <= 0 for name in DE1_FIELDS[2:]):
                return None
            buy = current.aggressive_buy_quote_notional / current.aggressive_buy_base_quantity
            sell = current.aggressive_sell_quote_notional / current.aggressive_sell_base_quantity
            return buy > sell and displacement > 0, current.close > current.open
        if h == 3:
            previous = window[0]
            prior = _displacement(previous)
            if prior is None:
                return None
            raw = current.close / current.open - 1
            prior_raw = previous.close / previous.open - 1
            return displacement > 0 and displacement > prior, raw > 0 and raw > prior_raw
        if h == 5:
            if eth is None or eth.symbol != "ETHUSDT" or eth.minute != current.minute:
                return None
            eth_displacement = _displacement(eth)
            if eth_displacement is None:
                return None
            return (imbalance > 0 and displacement > 0 and eth_displacement <= 0,
                    current.close > current.open and eth.close <= eth.open)
        raise ValueError("unregistered hypothesis")


def tplus2_outcome(state_minute: datetime, horizon: int,
                   forward: tuple[CandlePoint, ...], target_symbol: str) -> Decimal | None:
    if type(horizon) is not int or horizon not in (1, 5):
        raise ValueError("unregistered horizon")
    _minute(state_minute)
    if not START <= state_minute <= LAST_STATE or len(forward) != horizon:
        return None
    if any(row.symbol != target_symbol or row.minute != state_minute + (i + 2) * MINUTE
           for i, row in enumerate(forward)):
        return None
    with af1_decimal_context():
        return forward[-1].close / forward[0].open - 1


def _record(row) -> dict:
    return {field.name: (value.isoformat() if isinstance(value, datetime)
                         else str(value) if isinstance(value, Decimal) else value)
            for field in fields(row) for value in (getattr(row, field.name),)}


@dataclass(frozen=True, slots=True)
class SyntheticInputs:
    """Explicit identity plus immutable materialized fixtures; never real evidence."""

    identity: str
    observations: tuple[MinuteObservation, ...]
    candles: tuple[CandlePoint, ...]

    def __post_init__(self):
        if type(self.identity) is not str or re.fullmatch(r"synthetic:[a-z0-9._-]+", self.identity) is None:
            raise ValueError("explicit synthetic identity required")
        for rows, kind in ((self.observations, MinuteObservation), (self.candles, CandlePoint)):
            if type(rows) is not tuple or any(type(row) is not kind for row in rows):
                raise ValueError("immutable typed fixture tuples required")
            keys = [(row.symbol, row.minute) for row in rows]
            if len(set(keys)) != len(keys):
                raise ValueError("duplicate symbol/minute")
            for row in rows:
                row.__post_init__()
        candles = {(r.symbol, r.minute): r for r in self.candles}
        for row in self.observations:
            candle = candles.get((row.symbol, row.minute))
            if candle is None or (row.open, row.close) != (candle.open, candle.close):
                raise ValueError("signal and outcome candle evidence disagree")

    @property
    def metadata(self) -> dict:
        def digest(rows):
            return sha256(canonical_json_bytes([_record(r) for r in sorted(
                rows, key=lambda r: (r.symbol, r.minute))])).hexdigest()
        return {"data_kind": "SYNTHETIC", "identity": self.identity,
                "observation_sha256": digest(self.observations),
                "candle_sha256": digest(self.candles)}


def fixed_bh(catalog: FrozenCatalog, rows: Sequence[tuple[str, Decimal | None]]) -> tuple:
    expected = tuple(e.evaluation_id for e in catalog.evaluations)
    if tuple(row[0] for row in rows) != expected:
        raise ValueError("exact nine-member ordered family required")
    return benjamini_hochberg(tuple(row[1] for row in rows))


def robustness(indexed_returns: Sequence[tuple[int, Decimal]]) -> tuple[dict, Decimal | None]:
    if any(type(i) is not int or not 0 <= i < TOTAL_MINUTES for i, _ in indexed_returns):
        raise ValueError("absolute DEVELOPMENT minute indices required")
    with af1_decimal_context():
        return _temporal_stability(indexed_returns, first_index=0,
                                  eligible_count=TOTAL_MINUTES, blocks=8,
                                  minimum_block_coverage=Decimal("0.75"),
                                  minimum_events_per_block=10)


def robustness_score(bands: ScoreBands, evidence: Decimal | None,
                     positive_qualified: int) -> tuple[int, int, bool]:
    uncapped = bands.score(evidence)
    prerequisite = positive_qualified >= 6
    return (uncapped if uncapped < 4 or prerequisite else 3), uncapped, prerequisite


def seed_material(evaluation: Evaluation, input_digest: str) -> str:
    return canonical_json_bytes({"root_seed": 20260914, "catalog_id": CATALOG_ID,
                                 "evaluation_id": evaluation.evaluation_id,
                                 "evaluator_id": EVALUATOR_ID,
                                 "input_identity_sha256": input_digest}).decode("ascii")


def return_evidence(returns: tuple[Decimal, ...], seed: str) -> dict:
    with af1_decimal_context():
        mean = _mean(returns)
        statistic, p = _t_statistic_and_p(returns)
        low, high = _bootstrap_interval(returns, samples=200,
                                        confidence=Decimal("0.95"), seed_material=seed)
        return {"deoverlapped_event_count": len(returns), "gross_expectancy": _decimal(mean),
                "base_cost_expectancy": _decimal(None if mean is None else mean - BASE_COST),
                "stress_2c_expectancy": _decimal(None if mean is None else mean - STRESS_2C),
                "standard_error": _decimal(_standard_error(returns)),
                "t_statistic": _decimal(statistic), "p_value": _decimal(p),
                "bootstrap_interval": [_decimal(low), _decimal(high)]}


def incremental_advance(classification: ResearchClassification, difference: Decimal | None) -> bool:
    return classification is ResearchClassification.PROMOTE and difference is not None and difference > 0


def _difference(left, right):
    return None if left is None or right is None else Decimal(left) - Decimal(right)


def _finish(catalog: FrozenCatalog, partial: list[dict]) -> list[dict]:
    q_values = fixed_bh(catalog, tuple((r["evaluation_id"], None if r["p_value"] is None
                                      else Decimal(r["p_value"])) for r in partial))
    scoring = catalog.scoring
    for row, q in zip(partial, q_values, strict=True):
        mean = row["base_cost_expectancy"]
        base = None if mean is None else Decimal(mean)
        t = None if row["t_statistic"] is None else max(Decimal(0), Decimal(row["t_statistic"]))
        frequency = (Decimal(row["deoverlapped_event_count"]) * 1440 / row["eligible_minutes"]
                     if row["eligible_minutes"] else None)
        temporal = row["temporal_robustness"]
        ratio = temporal["robustness_evidence_ratio"]
        r, uncapped, prerequisite = robustness_score(scoring.robustness,
            None if ratio is None else Decimal(ratio), temporal["positive_qualified_block_count"])
        components = {"M": scoring.magnitude.score(base), "S": scoring.statistical_strength.score(t),
                      "F": scoring.opportunity_frequency.score(frequency), "R": r, "X": 4}
        rvs = sum(components.values())
        classification = _classify(deoverlapped_count=row["deoverlapped_event_count"],
            base_expectancy=base, q_value=q, rvs=rvs, classification=CLASSIFICATION,
            fdr_threshold=Decimal("0.05"))
        difference = _difference(row["gross_expectancy"], row["comparator_gross_expectancy"])
        row.update(q_value=_decimal(q), event_frequency_per_1440_eligible_minutes=_decimal(frequency),
                   scores=components, rvs=rvs, R_uncapped=uncapped,
                   R_maximum_prerequisite_met=prerequisite,
                   classification=classification.value, gross_expectancy_difference=_decimal(difference),
                   incremental_advance_eligible=incremental_advance(classification, difference))
    return partial


def _summarize(evaluation: Evaluation, input_digest: str, eligible: int,
               raw_count: int, control_raw_count: int,
               selected: tuple[tuple[int, Decimal], ...],
               control: tuple[tuple[int, Decimal], ...]) -> dict:
    """Shared calculation and pre-publication verification of sealed evidence."""
    evidence = return_evidence(tuple(v for _, v in selected), seed_material(evaluation, input_digest))
    temporal, _ = robustness(selected)
    control_temporal, _ = robustness(control)
    blocks = [{"block": left["block"], "candidate_event_count": left["event_count"],
               "candidate_gross_mean": left["mean_gross_expectancy"],
               "comparator_event_count": right["event_count"],
               "comparator_gross_mean": right["mean_gross_expectancy"],
               "difference": _decimal(_difference(left["mean_gross_expectancy"], right["mean_gross_expectancy"]))}
              for left, right in zip(temporal["blocks"], control_temporal["blocks"], strict=True)]
    return {"evaluation_id": evaluation.evaluation_id, **evidence,
            "eligible_minutes": eligible, "raw_event_count": raw_count,
            "events": [[i, str(v)] for i, v in selected],
            "comparator_events": [[i, str(v)] for i, v in control],
            "comparator_raw_event_count": control_raw_count,
            "comparator_event_count": len(control),
            "comparator_gross_expectancy": _decimal(_mean(tuple(v for _, v in control))),
            "temporal_robustness": temporal, "comparator_blocks": blocks}


def _document(catalog: FrozenCatalog, metadata: dict, partial: list[dict]) -> dict:
    return {"schema": IMPLEMENTATION_VERSION, "data_kind": "SYNTHETIC",
            "shadow": True, "sealed": True, "research_only": True,
            "catalog_id": CATALOG_ID, "fdr_family_id": FDR_FAMILY_ID,
            "evaluator_id": EVALUATOR_ID, "preregistration_commit": PREREGISTRATION_COMMIT,
            "numerical_policy": AF1_NUMERICS_VERSION, "p_value_method": P_VALUE_METHOD,
            "bootstrap_generator": "splitmix64-v1", "input_identity": metadata,
            "input_identity_sha256": sha256(canonical_json_bytes(metadata)).hexdigest(),
            "catalog": _parse(catalog.payload),
            "registered_evaluations": [e.row for e in catalog.evaluations],
            "classification_configuration": CLASSIFICATION.as_dict(),
            "block_index_contract": "floor(absolute_development_minute_index*8/total_minutes)",
            "event_deoverlap": "greedy_state_minute_spacing_H; does_not_assert_iid",
            "results": _finish(catalog, partial)}


def _validate_event_rows(rows: list, evaluation: Evaluation) -> tuple[tuple[int, Decimal], ...]:
    if type(rows) is not list:
        raise ValueError("sealed event list required")
    result = []
    for row in rows:
        if (type(row) is not list or len(row) != 2 or type(row[0]) is not int
                or type(row[1]) is not str):
            raise ValueError("invalid sealed event")
        index, text = row
        if not evaluation.lookback - 1 <= index <= minute_index(LAST_STATE):
            raise ValueError("sealed event outside frozen state support")
        value = Decimal(text)
        if not value.is_finite() or value <= -1:
            raise ValueError("invalid long simple return")
        if result and index - result[-1][0] < evaluation.horizon:
            raise ValueError("sealed events violate greedy chronological spacing")
        result.append((index, value))
    return tuple(result)


@dataclass(frozen=True, slots=True, init=False)
class SyntheticResult:
    """Immutable canonical scientific bytes and content-derived identity."""

    payload: bytes
    payload_sha256: str

    def validate(self) -> dict:
        if sha256(self.payload).hexdigest() != self.payload_sha256:
            raise ValueError("sealed evidence digest mismatch")
        document = _parse(self.payload)
        try:
            catalog = FrozenCatalog(canonical_json_bytes(document["catalog"]))
            metadata = document["input_identity"]
            if (set(metadata) != {"data_kind", "identity", "observation_sha256", "candle_sha256"}
                    or metadata["data_kind"] != "SYNTHETIC"
                    or re.fullmatch(r"synthetic:[a-z0-9._-]+", metadata["identity"]) is None
                    or any(re.fullmatch(r"[0-9a-f]{64}", metadata[key]) is None
                           for key in ("observation_sha256", "candle_sha256"))):
                raise ValueError("invalid synthetic input identity")
            digest = sha256(canonical_json_bytes(metadata)).hexdigest()
            original = document["results"]
            if type(original) is not list or len(original) != 9:
                raise ValueError("exact nine-member result family required")
            with af1_decimal_context():
                recomputed = []
                for evaluation, row in zip(catalog.evaluations, original, strict=True):
                    if row["evaluation_id"] != evaluation.evaluation_id:
                        raise ValueError("result order/identity mismatch")
                    selected = _validate_event_rows(row["events"], evaluation)
                    control = _validate_event_rows(row["comparator_events"], evaluation)
                    eligible, raw, control_raw = (row[key] for key in (
                        "eligible_minutes", "raw_event_count", "comparator_raw_event_count"))
                    max_eligible = minute_index(LAST_STATE) - evaluation.lookback + 2
                    if (any(type(v) is not int for v in (eligible, raw, control_raw))
                            or not 0 <= eligible <= max_eligible
                            or not len(selected) <= raw <= eligible
                            or not len(control) <= control_raw <= eligible
                            or bool(selected) != bool(raw) or bool(control) != bool(control_raw)):
                        raise ValueError("invalid sealed eligibility/event counts")
                    recomputed.append(_summarize(evaluation, digest, eligible, raw, control_raw,
                                                 selected, control))
                if canonical_json_bytes(_document(catalog, metadata, recomputed)) != self.payload:
                    raise ValueError("sealed evidence semantics mismatch")
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError("malformed sealed evidence") from error
        return document


def evaluate_synthetic(catalog: FrozenCatalog, inputs: SyntheticInputs) -> SyntheticResult:
    """Evaluate fixtures only. No loader, real-data binding, or execution CLI."""
    if type(catalog) is not FrozenCatalog or type(inputs) is not SyntheticInputs:
        raise ValueError("frozen catalog and explicit synthetic inputs required")
    catalog.__post_init__()
    inputs.__post_init__()
    with af1_decimal_context():
        if STRESS_2C != 2 * BASE_COST:
            raise ValueError("frozen cost mismatch")
        metadata = inputs.metadata
        input_digest = sha256(canonical_json_bytes(metadata)).hexdigest()
        observations = {(r.symbol, r.minute): r for r in inputs.observations}
        candles = {(r.symbol, r.minute): r for r in inputs.candles}
        partial = []
        for evaluation in catalog.evaluations:
            definition = evaluation.row
            source = definition["source_symbols"][0]
            target = definition["target_symbol"]
            candidate, comparator = {}, {}
            eligible = 0
            for symbol, minute in sorted(observations):
                if symbol != source or not state_supported(evaluation, minute):
                    continue
                window = tuple(observations.get((source, minute - i * MINUTE))
                               for i in reversed(range(evaluation.lookback)))
                if any(r is None for r in window):
                    continue
                flags = triggers(evaluation, window, observations.get(("ETHUSDT", minute)))
                if flags is None:
                    continue
                forward = tuple(candles.get((target, minute + (i + 2) * MINUTE))
                                for i in range(evaluation.horizon))
                if any(r is None for r in forward):
                    continue
                outcome = tplus2_outcome(minute, evaluation.horizon, forward, target)
                if outcome is None:
                    continue
                eligible += 1
                index = minute_index(minute)
                if flags[0]:
                    candidate[index] = outcome
                if flags[1]:
                    comparator[index] = outcome
            selected = tuple((i, candidate[i]) for i in _deoverlapped_events(tuple(candidate), evaluation.horizon))
            control = tuple((i, comparator[i]) for i in _deoverlapped_events(tuple(comparator), evaluation.horizon))
            partial.append(_summarize(evaluation, input_digest, eligible, len(candidate),
                                      len(comparator), selected, control))
        document = _document(catalog, metadata, partial)
        payload = canonical_json_bytes(document)
        result = object.__new__(SyntheticResult)
        object.__setattr__(result, "payload", payload)
        object.__setattr__(result, "payload_sha256", sha256(payload).hexdigest())
        result.validate()
        return result
