"""Execute the frozen AF4B DEVELOPMENT screen without changing its universe.

The acquisition entrypoint is deliberately narrow: it can only acquire the
catalog-declared dates and symbols through the approved DE1D daily adapter.
It prints source-integrity progress, never flow values or predictive results.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import argparse
import ctypes
import gc
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from quantos.application import (
    AggregateTradeStreamingDiagnostics,
    DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
    aggregate_trade_minute_states,
    canonical_range_manifest_bytes,
    compose_aggregate_trade_range,
)
from quantos.domain.evaluation.alpha_funnel import (
    ProvisionalClassificationConfig,
    ResearchClassification,
    ScoreBands,
    _bootstrap_interval,
    _classify,
    _mean,
    _median,
    _standard_error,
    _t_statistic_and_p,
    _temporal_stability,
    af1_decimal_context,
    benjamini_hochberg,
    canonical_candle_content_sha256,
)
from quantos.domain.market_data import Candle, DatasetValidationStatus
from quantos.domain.market_data.research_events import (
    AggregateTradeMinuteAvailabilityState,
    AggregateTradeMinuteCompletenessState,
    AggregateTradeMinuteState,
    AggregateTradeRangeRequest,
    ExactAggregateTradeRevision,
    aggregate_trade_archive_manifest_id,
    ResearchDatasetRole,
    ResearchEventValidationStatus,
    RevisionSelectionPolicy,
)
from quantos.infrastructure.binance.aggregate_trade_archive import (
    BinanceAggregateTradeArchiveError,
    BinanceSpotAggregateTradeDailyArchiveAdapter,
)
from quantos.infrastructure.storage import (
    AggregateTradeCatalogError,
    LocalAggregateTradeArchiveCatalog,
    ParquetAggregateTradeMinuteStateStore,
)
from quantos.infrastructure.storage.parquet import (
    ParquetCandleDatasetStore,
    dataset_id as candle_storage_dataset_id,
)
from quantos.infrastructure.storage.aggregate_trade_parquet import (
    AggregateTradeParquetStorageError,
    ParquetAggregateTradeArchiveStore,
)
from research.alpha_funnel_af4b_preregistration import (
    CATALOG_PATH,
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    SYMBOLS,
    digest,
    load_catalog,
)


EXPECTED_CATALOG_ID = (
    "7bf403626ef205c2b4026d2b9ceecb7ff6c7ab6bd19dbbfcfc726625d5034346"
)
EXPECTED_CATALOG_FILE_SHA256 = (
    "576f5a41de6701855a6c6f68283643f94bfeda3590342f1cce44fc76fa6cc273"
)
EXPECTED_DOC_011_SHA256 = (
    "6bdeb64b9a73e6e4e1b23165598228efd6c5b403955e9942f15f545834388d8e"
)
EXPECTED_EVALUATION_COUNT = 11
EVALUATOR_ID = "af4b-event-screen-tplus2-v1"
DATA_ROOT = Path(r"G:\QuantOS-Data")
REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_011_PATH = REPO_ROOT / "docs/011_ALPHA_DISCOVERY_FUNNEL_AF4B_DE1.md"
AF4B_ARTIFACT_ROOT = DATA_ROOT / "research" / "alpha-funnel-af4b"
CANDLE_STORE_ROOT = DATA_ROOT / "research" / "phase-4c-r"
AF3_UNIVERSE_PATH = REPO_ROOT / "research/alpha-funnel/af3b/universe.json"
EXPECTED_BASE_COMMIT = "d485c7c1c9b275c51f137790e8f3e2ac62eec7cc"
EXPECTED_AF3_UNIVERSE_ID = (
    "fee1a1e4328d2a91d2320c1696f25a97d525122de815a06a3b1ee2a231b292ed"
)
EXPECTED_CANDLE_CONTENT_SHA256 = {
    "BTCUSDT": "09c773962689ee91d3f6b1b17bbbcecfe7a78a62bb9e8393933b83a642880152",
    "ETHUSDT": "81e36168a7be968bd2e4278093f6dcff59e3494e2c9d7af77e2fdf12f5abda82",
}
EXPECTED_CANDLE_COUNT = 920_160
EXPECTED_CANDLE_STORAGE_DATASET_ID = {
    "BTCUSDT": "80f9a175259847fd222fceb05ea15a43197bd0bd1da260bbbcc0d18484fb7a50",
    "ETHUSDT": "6cc17ac0c53abc1877470ec315ad57fad5977d0257c0bbaf563b1efe9eb9050b",
}


class AF4BExecutionError(ValueError):
    """The frozen AF4B execution cannot proceed without changing its contract."""


def file_sha256(path: Path) -> str:
    hasher = sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if len(value) != 40:
        raise AF4BExecutionError("cannot bind the repository HEAD")
    return value


def _relative_data_locator(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(DATA_ROOT.resolve()).as_posix()
    except ValueError as error:
        raise AF4BExecutionError("AF4B input path is outside QuantOS-Data") from error


def publish_immutable_bytes(path: Path, payload: bytes) -> Path:
    """Publish exact bytes without replacing any existing artifact."""
    path = Path(path)
    if path.exists():
        if path.read_bytes() != payload:
            raise AF4BExecutionError(f"immutable artifact collision: {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise AF4BExecutionError(f"immutable artifact collision: {path}")
        return path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _peak_working_set_bytes() -> int | None:
    """Return the Windows process peak working set without adding a dependency."""
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        handle, ctypes.byref(counters), counters.cb
    ):
        return None
    return int(counters.PeakWorkingSetSize)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def frozen_catalog() -> dict[str, Any]:
    """Verify both committed frozen artifacts before any execution work."""
    if digest(CATALOG_PATH.read_bytes()) != EXPECTED_CATALOG_FILE_SHA256:
        raise AF4BExecutionError("frozen AF4B catalog file SHA-256 mismatch")
    if digest(DOC_011_PATH.read_bytes()) != EXPECTED_DOC_011_SHA256:
        raise AF4BExecutionError("frozen docs/011 SHA-256 mismatch")
    catalog = load_catalog()
    if catalog["catalog_id"] != EXPECTED_CATALOG_ID:
        raise AF4BExecutionError("frozen AF4B catalog ID mismatch")
    if len(catalog["evaluations"]) != EXPECTED_EVALUATION_COUNT:
        raise AF4BExecutionError("frozen AF4B evaluation count mismatch")
    return catalog


def require_development_role(role: ResearchDatasetRole) -> None:
    """Reject every protected research role before any AF4B data access."""
    if type(role) is not ResearchDatasetRole or role is not ResearchDatasetRole.DEVELOPMENT:
        raise AF4BExecutionError("AF4B permits DEVELOPMENT data only")


def registered_support(
    catalog: dict[str, Any], hypothesis_id: str, minute: datetime
) -> bool:
    """Return whether one state minute is inside the frozen registered support."""
    if minute.tzinfo is not timezone.utc:
        raise AF4BExecutionError("AF4B support minute must use canonical UTC")
    hypotheses = {
        item["hypothesis_id"]: item for item in catalog["hypotheses"]
    }
    if hypothesis_id not in hypotheses:
        raise AF4BExecutionError(f"unregistered AF4B hypothesis: {hypothesis_id}")
    first = datetime.fromisoformat(
        hypotheses[hypothesis_id]["first_eligible_state_minute"].replace("Z", "+00:00")
    )
    last = datetime.fromisoformat(
        catalog["data_contract"]["common_last_eligible_state_minute"].replace(
            "Z", "+00:00"
        )
    )
    return first <= minute <= last


def required_dates(catalog: dict[str, Any] | None = None) -> tuple[date, ...]:
    """Derive the daily acquisition boundary only from the frozen catalog."""
    catalog = frozen_catalog() if catalog is None else catalog
    contract = catalog["data_contract"]
    boundaries = contract["required_de1_daily_partition_dates"]
    start = date.fromisoformat(boundaries["first_inclusive"])
    last = date.fromisoformat(boundaries["last_inclusive"])
    if (
        catalog["data_contract"]["development_interval"]
        != {"start_inclusive": DEVELOPMENT_START, "end_exclusive": DEVELOPMENT_END}
        or start != date(2024, 1, 1)
        or last != date(2025, 9, 30)
    ):
        raise AF4BExecutionError("frozen DEVELOPMENT acquisition boundary mismatch")
    return tuple(start + timedelta(days=offset) for offset in range((last - start).days + 1))


def _require_final_historical_state(state: AggregateTradeMinuteState) -> None:
    if type(state) is not AggregateTradeMinuteState:
        raise AF4BExecutionError("AF4B requires canonical AggregateTradeMinuteState values")
    AggregateTradeMinuteState.__post_init__(state)
    if (
        state.completeness_state
        is not AggregateTradeMinuteCompletenessState.VALIDATED_SOURCE_COMPLETE
        or state.availability_state
        is not AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN
        or state.validation_status is not ResearchEventValidationStatus.VALIDATED
    ):
        raise AF4BExecutionError("AF4B requires validated completed historical minute state")


def _require_exact_join(
    state: AggregateTradeMinuteState,
    candle: Candle,
    *,
    source_symbol: str,
    target_symbol: str,
) -> None:
    _require_final_historical_state(state)
    Candle.__post_init__(candle)
    if (
        state.symbol != source_symbol
        or candle.symbol != target_symbol
        or state.minute_start_time != candle.open_time
        or state.minute_end_time_exclusive != candle.open_time + timedelta(minutes=1)
    ):
        raise AF4BExecutionError("AF4B state/candle exact UTC-minute join failed")


def registered_predicate(
    hypothesis_id: str,
    states: tuple[AggregateTradeMinuteState, ...],
    target_candles: tuple[Candle, ...],
    index: int,
    *,
    source_symbol: str,
    target_symbol: str,
) -> tuple[bool, bool]:
    """Return (eligible, signal) for exactly one catalog-registered formula."""
    if (
        type(states) is not tuple
        or type(target_candles) is not tuple
        or type(index) is not int
        or index < 0
        or index >= len(states)
        or index >= len(target_candles)
    ):
        raise AF4BExecutionError("invalid AF4B predicate inputs")
    state = states[index]
    candle = target_candles[index]
    _require_exact_join(
        state,
        candle,
        source_symbol=source_symbol,
        target_symbol=target_symbol,
    )
    buy = state.aggressive_buy_quote_notional
    sell = state.aggressive_sell_quote_notional
    total = state.total_quote_notional
    with af1_decimal_context():
        if hypothesis_id in (
            "af4b.h1.immediate-positive-quote-flow",
            "af4b.h6.btc-positive-flow-leads-eth",
        ):
            if total <= 0:
                return False, False
            return True, (buy - sell) / total > 0
        if hypothesis_id == "af4b.h2.five-minute-positive-quote-flow-persistence":
            if index < 4:
                return False, False
            window = states[index - 4 : index + 1]
            expected = state.minute_start_time - timedelta(minutes=4)
            for offset, item in enumerate(window):
                _require_final_historical_state(item)
                if (
                    item.symbol != source_symbol
                    or item.minute_start_time != expected + timedelta(minutes=offset)
                ):
                    raise AF4BExecutionError("H2 requires five exact consecutive completed states")
            denominator = sum((item.total_quote_notional for item in window), Decimal(0))
            if denominator <= 0:
                return False, False
            numerator = sum(
                (
                    item.aggressive_buy_quote_notional
                    - item.aggressive_sell_quote_notional
                    for item in window
                ),
                Decimal(0),
            )
            return True, numerator / denominator > 0
        if hypothesis_id == "af4b.h3.positive-quote-flow-acceleration":
            if index < 1:
                return False, False
            previous = states[index - 1]
            _require_final_historical_state(previous)
            if (
                previous.symbol != source_symbol
                or previous.minute_start_time
                != state.minute_start_time - timedelta(minutes=1)
            ):
                raise AF4BExecutionError("H3 requires the exact preceding completed state")
            previous_total = previous.total_quote_notional
            if total <= 0 or previous_total <= 0:
                return False, False
            current_imbalance = (buy - sell) / total
            previous_imbalance = (
                previous.aggressive_buy_quote_notional
                - previous.aggressive_sell_quote_notional
            ) / previous_total
            return True, current_imbalance > previous_imbalance
        if hypothesis_id == "af4b.h4.sell-flow-absorption-reversal-up":
            if total <= 0:
                return False, False
            return True, sell >= Decimal(2) * buy and candle.close >= candle.open
        if hypothesis_id == "af4b.h5.buy-side-aggregate-record-size-asymmetry":
            buy_count = state.aggressive_buy_event_count
            sell_count = state.aggressive_sell_event_count
            if buy_count <= 0 or sell_count <= 0:
                return False, False
            return True, buy / Decimal(buy_count) > sell / Decimal(sell_count)
    raise AF4BExecutionError(f"unregistered AF4B hypothesis: {hypothesis_id}")


def comparator_signal(
    hypothesis_id: str,
    *,
    eligible: bool,
    candle: Candle,
) -> bool:
    """Apply only the preregistered descriptive comparator."""
    if not eligible:
        return False
    Candle.__post_init__(candle)
    if hypothesis_id == "af4b.h4.sell-flow-absorption-reversal-up":
        return candle.close >= candle.open
    if hypothesis_id in {
        "af4b.h1.immediate-positive-quote-flow",
        "af4b.h2.five-minute-positive-quote-flow-persistence",
        "af4b.h3.positive-quote-flow-acceleration",
        "af4b.h5.buy-side-aggregate-record-size-asymmetry",
        "af4b.h6.btc-positive-flow-leads-eth",
    }:
        return True
    raise AF4BExecutionError(f"unregistered AF4B comparator: {hypothesis_id}")


def deoverlap(indices: tuple[int, ...], horizon_minutes: int) -> tuple[int, ...]:
    if (
        type(indices) is not tuple
        or any(type(value) is not int for value in indices)
        or tuple(sorted(set(indices))) != indices
        or type(horizon_minutes) is not int
        or horizon_minutes not in (1, 5)
    ):
        raise AF4BExecutionError("invalid AF4B de-overlap inputs")
    selected: list[int] = []
    for index in indices:
        if not selected or index - selected[-1] >= horizon_minutes:
            selected.append(index)
    return tuple(selected)


def directional_return(
    candles: tuple[Candle, ...], index: int, horizon_minutes: int
) -> Decimal:
    """Long Spot return using t+2 entry and the exact registered exit."""
    if (
        type(candles) is not tuple
        or type(index) is not int
        or type(horizon_minutes) is not int
        or horizon_minutes not in (1, 5)
        or index < 0
        or index + horizon_minutes + 1 >= len(candles)
    ):
        raise AF4BExecutionError("AF4B outcome lacks the exact forward candle support")
    decision = candles[index]
    entry = candles[index + 2]
    exit_candle = candles[index + horizon_minutes + 1]
    Candle.__post_init__(decision)
    Candle.__post_init__(entry)
    Candle.__post_init__(exit_candle)
    if (
        decision.symbol != entry.symbol
        or decision.symbol != exit_candle.symbol
        or entry.open_time != decision.open_time + timedelta(minutes=2)
        or exit_candle.open_time
        != decision.open_time + timedelta(minutes=horizon_minutes + 1)
        or any(
            item.close_time >= item.open_time + timedelta(minutes=1)
            for item in (decision, entry, exit_candle)
        )
        or entry.open <= 0
    ):
        raise AF4BExecutionError("AF4B t+2 entry or exit chronology mismatch")
    with af1_decimal_context():
        return exit_candle.close / entry.open - Decimal(1)


def apply_frozen_costs(
    mean_gross_return: Decimal | None, catalog: dict[str, Any]
) -> dict[str, Decimal | None]:
    """Subtract each frozen round-trip rate exactly once."""
    with af1_decimal_context():
        return {
            item["name"]: (
                mean_gross_return - Decimal(item["round_trip_rate"])
                if mean_gross_return is not None
                else None
            )
            for item in catalog["cost_contract"]["scenarios"]
        }


def _score_row(
    *,
    base_expectancy: Decimal | None,
    t_statistic: Decimal | None,
    frequency: Decimal,
    robustness: Decimal | None,
    positive_blocks: int,
    catalog: dict[str, Any],
) -> dict[str, int]:
    bands = catalog["statistical_contract"]["score_bands"]
    scores = {
        "M": ScoreBands(tuple(Decimal(value) for value in bands["M"])).score(
            base_expectancy
        ),
        "S": ScoreBands(tuple(Decimal(value) for value in bands["S"])).score(
            max(Decimal(0), t_statistic) if t_statistic is not None else None
        ),
        "F": ScoreBands(tuple(Decimal(value) for value in bands["F"])).score(
            frequency
        ),
        "R": ScoreBands(tuple(Decimal(value) for value in bands["R"])).score(
            robustness
        ),
        "X": 4,
    }
    maximum_positive = catalog["statistical_contract"]["temporal_robustness"][
        "minimum_positive_blocks_for_maximum_robustness"
    ]
    if scores["R"] == 4 and positive_blocks < maximum_positive:
        scores["R"] = 3
    scores["RVS"] = sum(scores[label] for label in "MSFRX")
    return scores


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def screen_registered_evaluation(
    catalog: dict[str, Any],
    evaluation: dict[str, Any],
    source_states: tuple[AggregateTradeMinuteState, ...],
    target_candles: tuple[Candle, ...],
    *,
    input_freeze_id: str,
    target_candle_dataset_id: str,
    target_candle_content_sha256: str,
) -> dict[str, Any]:
    """Evaluate one frozen row; BH and classification are applied afterwards."""
    if catalog != frozen_catalog():
        raise AF4BExecutionError("screen requires the exact frozen catalog")
    if evaluation not in catalog["evaluations"]:
        raise AF4BExecutionError("screen evaluation is outside the frozen family")
    if evaluation["evaluator_id"] != EVALUATOR_ID:
        raise AF4BExecutionError("screen evaluator ID mismatch")
    if type(source_states) is not tuple or type(target_candles) is not tuple:
        raise AF4BExecutionError("screen inputs must be immutable tuples")
    if len(source_states) != len(target_candles):
        raise AF4BExecutionError("source state and target candle grids differ")
    hypothesis_id = evaluation["hypothesis_id"]
    hypothesis = next(
        item for item in catalog["hypotheses"]
        if item["hypothesis_id"] == hypothesis_id
    )
    first_time = datetime.fromisoformat(
        hypothesis["first_eligible_state_minute"].replace("Z", "+00:00")
    )
    last_time = datetime.fromisoformat(
        catalog["data_contract"]["common_last_eligible_state_minute"].replace(
            "Z", "+00:00"
        )
    )
    if not target_candles:
        raise AF4BExecutionError("screen candle grid is empty")
    first_index = int(
        (first_time - target_candles[0].open_time).total_seconds() // 60
    )
    last_index = int(
        (last_time - target_candles[0].open_time).total_seconds() // 60
    )
    if (
        first_index < 0
        or last_index < first_index
        or last_index + evaluation["horizon_minutes"] + 1 >= len(target_candles)
        or target_candles[first_index].open_time != first_time
        or target_candles[last_index].open_time != last_time
    ):
        raise AF4BExecutionError("screen DEVELOPMENT common support mismatch")
    raw: list[int] = []
    comparator_raw: list[int] = []
    for index in range(first_index, last_index + 1):
        eligible, signal = registered_predicate(
            hypothesis_id,
            source_states,
            target_candles,
            index,
            source_symbol=evaluation["source_symbol"],
            target_symbol=evaluation["target_symbol"],
        )
        if signal:
            raw.append(index)
        if comparator_signal(
            hypothesis_id, eligible=eligible, candle=target_candles[index]
        ):
            comparator_raw.append(index)
    horizon = evaluation["horizon_minutes"]
    selected = deoverlap(tuple(raw), horizon)
    comparator_selected = deoverlap(tuple(comparator_raw), horizon)
    returns = [
        directional_return(target_candles, index, horizon)
        for index in selected
    ]
    comparator_returns = [
        directional_return(target_candles, index, horizon)
        for index in comparator_selected
    ]
    mean = _mean(returns)
    comparator_mean = _mean(comparator_returns)
    t_statistic, p_value = _t_statistic_and_p(returns)
    seed_material = "|".join((
        str(catalog["statistical_contract"]["deterministic_bootstrap"]["random_seed"]),
        target_candle_dataset_id,
        evaluation["target_symbol"],
        target_candle_content_sha256,
        hypothesis_id,
        EVALUATOR_ID,
        hypothesis["hypothesis_definition_sha256"],
        str(horizon),
        input_freeze_id,
    ))
    bootstrap = catalog["statistical_contract"]["deterministic_bootstrap"]
    ci_low, ci_high = _bootstrap_interval(
        returns,
        samples=bootstrap["samples"],
        confidence=Decimal(bootstrap["confidence"]),
        seed_material=seed_material,
    )
    eligible_count = last_index - first_index + 1
    indexed_returns = list(zip(selected, returns, strict=True))
    temporal_rules = catalog["statistical_contract"]["temporal_robustness"]
    temporal, robustness = _temporal_stability(
        indexed_returns,
        first_index=first_index,
        eligible_count=eligible_count,
        blocks=temporal_rules["block_count"],
        minimum_block_coverage=Decimal(
            temporal_rules["minimum_qualified_block_coverage"]
        ),
        minimum_events_per_block=temporal_rules["minimum_events_per_block"],
    )
    comparator_temporal, _ = _temporal_stability(
        list(zip(comparator_selected, comparator_returns, strict=True)),
        first_index=first_index,
        eligible_count=eligible_count,
        blocks=temporal_rules["block_count"],
        minimum_block_coverage=Decimal(
            temporal_rules["minimum_qualified_block_coverage"]
        ),
        minimum_events_per_block=temporal_rules["minimum_events_per_block"],
    )
    comparator_block_differences = []
    for primary_block, comparator_block in zip(
        temporal["blocks"], comparator_temporal["blocks"], strict=True
    ):
        primary_block_mean = primary_block["mean_gross_expectancy"]
        comparator_block_mean = comparator_block["mean_gross_expectancy"]
        with af1_decimal_context():
            block_delta = (
                Decimal(primary_block_mean) - Decimal(comparator_block_mean)
                if primary_block_mean is not None and comparator_block_mean is not None
                else None
            )
        comparator_block_differences.append({
            "block": primary_block["block"],
            "primary_minus_comparator_gross_expectancy": _decimal(block_delta),
        })
    costs = apply_frozen_costs(mean, catalog)
    with af1_decimal_context():
        days = Decimal(eligible_count) / Decimal(1_440)
        frequency = Decimal(len(selected)) / days
        comparator_delta = (
            mean - comparator_mean
            if mean is not None and comparator_mean is not None
            else None
        )
    scores = _score_row(
        base_expectancy=costs["base"],
        t_statistic=t_statistic,
        frequency=frequency,
        robustness=robustness,
        positive_blocks=temporal["positive_qualified_block_count"],
        catalog=catalog,
    )
    return {
        "evaluation_id": evaluation["evaluation_id"],
        "hypothesis_id": hypothesis_id,
        "hypothesis_definition_sha256": hypothesis[
            "hypothesis_definition_sha256"
        ],
        "source_symbol": evaluation["source_symbol"],
        "target_symbol": evaluation["target_symbol"],
        "direction": "UP",
        "horizon_minutes": horizon,
        "parameter_set_id": evaluation["parameter_set_id"],
        "evaluator_id": EVALUATOR_ID,
        "raw_eligible_signal_count": len(raw),
        "deoverlapped_event_count": len(selected),
        "eligible_decision_count": eligible_count,
        "gross_expectancy": _decimal(mean),
        "median_gross_return": _decimal(_median(returns)),
        "standard_error": _decimal(_standard_error(returns)),
        "base_net_expectancy": _decimal(costs["base"]),
        "stress_2c_net_expectancy": _decimal(costs["stress_2c"]),
        "descriptive_t_statistic": _decimal(t_statistic),
        "raw_p_value": _decimal(p_value),
        "bootstrap_confidence_interval": {
            "method": "deoverlapped_event_resampling_with_replacement_splitmix64-v1",
            "samples": bootstrap["samples"],
            "confidence": bootstrap["confidence"],
            "seed_material_sha256": sha256(seed_material.encode("utf-8")).hexdigest(),
            "lower": _decimal(ci_low),
            "upper": _decimal(ci_high),
        },
        "temporal_stability": temporal,
        "evidence_scores": scores,
        "comparator": {
            "raw_eligible_count": len(comparator_raw),
            "deoverlapped_count": len(comparator_selected),
            "gross_expectancy": _decimal(comparator_mean),
            "primary_minus_comparator_gross_expectancy": _decimal(
                comparator_delta
            ),
            "temporal_stability": comparator_temporal,
            "primary_minus_comparator_by_block": comparator_block_differences,
            "inferential_test_registered": False,
        },
        "_p_value": p_value,
        "_base_expectancy": costs["base"],
    }


def finalize_closed_family(
    catalog: dict[str, Any], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply BH once over all 11 rows and derive deterministic classifications."""
    expected = catalog["fdr_family"]["evaluation_ids"]
    if (
        len(rows) != 11
        or len({row.get("evaluation_id") for row in rows}) != 11
        or {row.get("evaluation_id") for row in rows} != set(expected)
    ):
        raise AF4BExecutionError("BH requires the exact closed 11-row family")
    by_id = {row["evaluation_id"]: row for row in rows}
    ordered = [by_id[evaluation_id] for evaluation_id in expected]
    q_values = benjamini_hochberg(
        tuple(row["_p_value"] for row in ordered)
    )
    rules = catalog["statistical_contract"]["promotion_gates"]
    classification = ProvisionalClassificationConfig(
        promote_min_rvs=rules["research_viability_score_at_least"],
        promote_min_deoverlapped_events=rules[
            "deoverlapped_event_count_at_least"
        ],
        promote_min_base_cost_expectancy=Decimal(
            rules["base_cost_adjusted_expectancy_at_least"]
        ),
        kill_below_base_cost_expectancy=Decimal(0),
    )
    threshold = Decimal(catalog["fdr_family"]["alpha"])
    finalized: list[dict[str, Any]] = []
    for row, q_value in zip(ordered, q_values, strict=True):
        result = {
            key: value
            for key, value in row.items()
            if key not in ("_p_value", "_base_expectancy")
        }
        p_value = row["_p_value"]
        result["multiple_testing"] = {
            "method": "Benjamini-Hochberg",
            "fdr_family_id": catalog["fdr_family"]["fdr_family_id"],
            "registered_tests": 11,
            "raw_p_value": _decimal(p_value),
            "multiplicity_input_p_value": _decimal(
                p_value if p_value is not None else Decimal(1)
            ),
            "q_value": _decimal(q_value),
            "passes_fdr_threshold": (
                q_value is not None and q_value <= threshold
            ),
            "undefined_p_value_policy": catalog["fdr_family"][
                "undefined_test_policy"
            ],
        }
        primary_classification = _classify(
            deoverlapped_count=row["deoverlapped_event_count"],
            base_expectancy=row["_base_expectancy"],
            q_value=q_value,
            rvs=row["evidence_scores"]["RVS"],
            classification=classification,
            fdr_threshold=threshold,
        )
        comparator_delta_text = result["comparator"][
            "primary_minus_comparator_gross_expectancy"
        ]
        comparator_delta = (
            Decimal(comparator_delta_text)
            if comparator_delta_text is not None
            else None
        )
        if (
            primary_classification is ResearchClassification.PROMOTE
            and (comparator_delta is None or comparator_delta <= 0)
        ):
            result["classification"] = ResearchClassification.WATCH.value
            result["incremental_information_status"] = (
                "candle_restatement_inconclusive_nonpositive_comparator_delta"
            )
        elif primary_classification is ResearchClassification.PROMOTE:
            result["classification"] = ResearchClassification.PROMOTE.value
            result["incremental_information_status"] = (
                "positive_incremental_information_under_frozen_gates"
            )
        else:
            result["classification"] = primary_classification.value
            result["incremental_information_status"] = (
                "primary_did_not_pass_all_promotion_gates"
            )
        finalized.append(result)
    return finalized


def _af3_candle_contract() -> dict[str, dict[str, Any]]:
    document = json.loads(AF3_UNIVERSE_PATH.read_text(encoding="utf-8"))
    if (
        document.get("universe_artifact_id") != EXPECTED_AF3_UNIVERSE_ID
        or document.get("development_start")
        != "2024-01-01T00:00:00.000000+00:00"
        or document.get("development_end_exclusive")
        != "2025-10-01T00:00:00.000000+00:00"
        or document.get("schema_version") != "af3b-universe-binding-v1"
    ):
        raise AF4BExecutionError("committed AF3 DEVELOPMENT universe mismatch")
    datasets = document.get("datasets")
    if type(datasets) is not list or len(datasets) != 2:
        raise AF4BExecutionError("AF3 DEVELOPMENT candle binding must contain two datasets")
    by_symbol = {item.get("symbol"): item for item in datasets}
    if set(by_symbol) != set(SYMBOLS):
        raise AF4BExecutionError("AF3 DEVELOPMENT candle symbols mismatch")
    for symbol, item in by_symbol.items():
        if (
            item.get("role") != "development"
            or item.get("candle_count") != EXPECTED_CANDLE_COUNT
            or item.get("content_sha256")
            != EXPECTED_CANDLE_CONTENT_SHA256[symbol]
        ):
            raise AF4BExecutionError(f"AF3 DEVELOPMENT candle binding mismatch for {symbol}")
    return by_symbol


def _candle_input_descriptor(
    symbol: str, logical_binding: dict[str, Any]
) -> dict[str, Any]:
    storage_id = EXPECTED_CANDLE_STORAGE_DATASET_ID[symbol]
    path = (
        CANDLE_STORE_ROOT
        / "market_data"
        / "candles"
        / "parquet-v1"
        / symbol
        / "1m"
        / f"{storage_id}.parquet"
    )
    if not path.is_file():
        raise AF4BExecutionError(f"required canonical candle dataset is missing: {symbol}")
    sequence = ParquetCandleDatasetStore(CANDLE_STORE_ROOT).read(path)
    identity = sequence.identity
    expected_end = datetime.fromisoformat(DEVELOPMENT_END.replace("Z", "+00:00"))
    expected_start = datetime.fromisoformat(DEVELOPMENT_START.replace("Z", "+00:00"))
    content_hash = canonical_candle_content_sha256(sequence)
    if (
        identity.symbol != symbol
        or identity.timeframe != "1m"
        or identity.start_time != expected_start
        or identity.end_time != expected_end - timedelta(minutes=1)
        or identity.validation_status is not DatasetValidationStatus.VALIDATED
        or len(sequence.candles) != EXPECTED_CANDLE_COUNT
        or content_hash != EXPECTED_CANDLE_CONTENT_SHA256[symbol]
        or content_hash != logical_binding["content_sha256"]
        or candle_storage_dataset_id(identity) != storage_id
        or path.stem != storage_id
    ):
        raise AF4BExecutionError(f"canonical DEVELOPMENT candle validation failed: {symbol}")
    descriptor = {
        "symbol": symbol,
        "research_role": "DEVELOPMENT",
        "logical_research_dataset_id": logical_binding["dataset_id"],
        "logical_dataset_identity_sha256": logical_binding[
            "dataset_identity_sha256"
        ],
        "storage_dataset_id": storage_id,
        "content_sha256": content_hash,
        "file_sha256": file_sha256(path),
        "file_bytes": path.stat().st_size,
        "locator": _relative_data_locator(path),
        "candle_count": len(sequence.candles),
        "identity": {
            "symbol": identity.symbol,
            "timeframe": identity.timeframe,
            "start_time": identity.start_time.isoformat(timespec="microseconds"),
            "end_time": identity.end_time.isoformat(timespec="microseconds"),
            "source": identity.source,
            "schema_version": identity.schema_version,
            "ingestion_version": identity.ingestion_version,
            "validation_status": identity.validation_status.value,
        },
    }
    del sequence
    gc.collect()
    return descriptor


def _exact_revision_selection(
    catalog: LocalAggregateTradeArchiveCatalog,
    *,
    symbol: str,
    dates: tuple[date, ...],
) -> tuple[ExactAggregateTradeRevision, ...]:
    selected: list[ExactAggregateTradeRevision] = []
    for source_date in dates:
        revisions = catalog.revisions(symbol=symbol, source_date=source_date)
        if not revisions:
            raise AF4BExecutionError(
                f"missing required DE1 partition {symbol} {source_date.isoformat()}"
            )
        for manifest in revisions:
            require_development_role(manifest.research_role)
            if manifest.symbol != symbol or manifest.source_date != source_date:
                raise AF4BExecutionError("DE1 catalog logical partition mismatch")
        manifest = min(
            revisions,
            key=lambda item: (
                aggregate_trade_archive_manifest_id(item),
                item.source_revision_id,
            ),
        )
        selected.append(
            ExactAggregateTradeRevision(
                source_date=source_date,
                manifest_id=aggregate_trade_archive_manifest_id(manifest),
                source_revision_id=manifest.source_revision_id,
            )
        )
    return tuple(selected)


def _materialize_symbol_inputs(
    catalog: LocalAggregateTradeArchiveCatalog,
    *,
    symbol: str,
    dates: tuple[date, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    exact = _exact_revision_selection(catalog, symbol=symbol, dates=dates)
    request = AggregateTradeRangeRequest(
        symbol=symbol,
        start_date=dates[0],
        end_date_exclusive=dates[-1] + timedelta(days=1),
        selection_policy=RevisionSelectionPolicy.EXACT,
        exact_revisions=exact,
    )
    range_started = time.perf_counter()
    source_range = compose_aggregate_trade_range(
        catalog,
        request,
        batch_size=DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
    )
    range_runtime = time.perf_counter() - range_started
    range_payload = canonical_range_manifest_bytes(source_range)
    range_path = (
        AF4B_ARTIFACT_ROOT
        / "input-preparation"
        / "ranges"
        / symbol
        / f"{source_range.range_id}.json"
    )
    publish_immutable_bytes(range_path, range_payload)

    diagnostics = AggregateTradeStreamingDiagnostics()
    state_started = time.perf_counter()
    state_dataset = aggregate_trade_minute_states(
        catalog,
        source_range,
        batch_size=DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
        diagnostics=diagnostics,
    )
    state_store = ParquetAggregateTradeMinuteStateStore(DATA_ROOT)
    publication = state_store.write(state_dataset)
    state_runtime = time.perf_counter() - state_started
    if (
        publication.dataset_id != state_dataset.dataset_id
        or publication.manifest.dataset_id != state_dataset.dataset_id
        or publication.manifest.content_sha256 != state_dataset.content_sha256
        or len(state_dataset.states) != EXPECTED_CANDLE_COUNT
    ):
        raise AF4BExecutionError(f"minute-state publication mismatch for {symbol}")
    selected_entries = [
        catalog.view.entry_for_manifest_id(item.manifest_id) for item in exact
    ]
    raw_bytes = sum(item.raw_archive_path.stat().st_size for item in selected_entries)
    canonical_bytes = sum(
        item.canonical_parquet_path.stat().st_size for item in selected_entries
    )
    descriptor = {
        "symbol": symbol,
        "research_role": "DEVELOPMENT",
        "revision_selection_policy": "EXACT",
        "revision_selection_rule": (
            "lexicographically_smallest_manifest_id_then_source_revision_id"
        ),
        "range_id": source_range.range_id,
        "range_manifest_sha256": sha256(range_payload).hexdigest(),
        "range_manifest_locator": _relative_data_locator(range_path),
        "partition_count": len(source_range.partitions),
        "raw_source_bytes": raw_bytes,
        "canonical_parquet_bytes": canonical_bytes,
        "partitions": [item.as_canonical_dict() for item in source_range.partitions],
        "minute_state": {
            "dataset_id": state_dataset.dataset_id,
            "content_sha256": state_dataset.content_sha256,
            "manifest": publication.manifest.as_canonical_dict(),
            "canonical_parquet_sha256": file_sha256(
                publication.canonical_parquet_path
            ),
            "canonical_parquet_bytes": publication.canonical_parquet_path.stat().st_size,
            "canonical_parquet_locator": _relative_data_locator(
                publication.canonical_parquet_path
            ),
            "state_count": len(state_dataset.states),
        },
    }
    metrics = {
        "symbol": symbol,
        "range_composition_seconds": repr(range_runtime),
        "state_materialization_and_publication_seconds": repr(state_runtime),
        "raw_event_batch_size": diagnostics.batch_size,
        "max_batch_event_count": diagnostics.max_batch_event_count,
        "max_minute_event_count": diagnostics.max_minute_event_count,
        "max_raw_events_buffered": diagnostics.max_raw_events_buffered,
        "peak_working_set_bytes": _peak_working_set_bytes(),
    }
    del state_dataset
    gc.collect()
    return descriptor, metrics


def freeze_development_inputs() -> dict[str, Any]:
    """Validate, materialize, and immutably bind all frozen AF4B inputs."""
    catalog_document = frozen_catalog()
    if _git_head() != EXPECTED_BASE_COMMIT:
        raise AF4BExecutionError("AF4B input freeze repository HEAD mismatch")
    dates = required_dates(catalog_document)
    if len(dates) != 639:
        raise AF4BExecutionError("AF4B input freeze requires exactly 639 dates")
    af3_bindings = _af3_candle_contract()
    candle_inputs = {
        symbol: _candle_input_descriptor(symbol, af3_bindings[symbol])
        for symbol in SYMBOLS
    }
    archive_catalog = _catalog(DATA_ROOT)
    view = archive_catalog.rebuild()
    required_keys = {(symbol, source_date) for symbol in SYMBOLS for source_date in dates}
    available_keys = {
        (entry.logical_partition.symbol, entry.logical_partition.source_date)
        for entry in view.entries
    }
    missing = sorted(required_keys - available_keys, key=lambda item: (item[0], item[1]))
    if missing:
        raise AF4BExecutionError(
            f"AF4B input freeze is missing {len(missing)} required DE1 partitions"
        )

    de1_inputs: dict[str, Any] = {}
    materialization_metrics: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        descriptor, metrics = _materialize_symbol_inputs(
            archive_catalog,
            symbol=symbol,
            dates=dates,
        )
        de1_inputs[symbol] = descriptor
        materialization_metrics.append(metrics)

    executor_sha = file_sha256(Path(__file__).resolve())
    body = {
        "schema_version": "af4b-development-input-freeze-v1",
        "phase": "AF4B",
        "catalog_id": catalog_document["catalog_id"],
        "catalog_file_sha256": EXPECTED_CATALOG_FILE_SHA256,
        "docs_011_sha256": EXPECTED_DOC_011_SHA256,
        "fdr_family_id": catalog_document["fdr_family"]["fdr_family_id"],
        "base_commit": EXPECTED_BASE_COMMIT,
        "evaluator": {
            "evaluator_id": EVALUATOR_ID,
            "source_sha256": executor_sha,
            "source_locator": "research/alpha_funnel_af4b_execute.py",
        },
        "research_role": "DEVELOPMENT",
        "development_interval": catalog_document["data_contract"][
            "development_interval"
        ],
        "de1_logical_date_range": catalog_document["data_contract"][
            "required_de1_daily_partition_dates"
        ],
        "required_partition_count": len(required_keys),
        "af3_universe": {
            "universe_artifact_id": EXPECTED_AF3_UNIVERSE_ID,
            "file_sha256": file_sha256(AF3_UNIVERSE_PATH),
        },
        "candles": candle_inputs,
        "de1": de1_inputs,
        "validation_gate": {
            "all_candles_fully_revalidated": True,
            "all_raw_and_canonical_de1_partitions_fully_revalidated": True,
            "all_ranges_complete_and_exact": True,
            "all_minute_state_datasets_validated_and_immutable": True,
            "protected_roles_accessed": False,
            "predictive_evaluation_executed": False,
        },
        "production_approved": False,
        "allow_screening_validation": False,
        "allow_sealed_oos": False,
        "allow_2026_research": False,
    }
    input_freeze_id = sha256(canonical_json_bytes(body)).hexdigest()
    document = {**body, "input_freeze_id": input_freeze_id}
    manifest_path = (
        AF4B_ARTIFACT_ROOT
        / "input-freezes"
        / input_freeze_id
        / "manifest.json"
    )
    publish_immutable_bytes(manifest_path, canonical_json_bytes(document))
    metrics_document = {
        "schema_version": "af4b-input-operator-metrics-v1",
        "input_freeze_id": input_freeze_id,
        "materialization": materialization_metrics,
        "peak_working_set_bytes": _peak_working_set_bytes(),
    }
    publish_immutable_bytes(
        manifest_path.with_name("operator_metrics.json"),
        canonical_json_bytes(metrics_document),
    )
    return document


def load_input_freeze(input_freeze_id: str) -> dict[str, Any]:
    if (
        type(input_freeze_id) is not str
        or len(input_freeze_id) != 64
        or any(character not in "0123456789abcdef" for character in input_freeze_id)
    ):
        raise AF4BExecutionError("invalid AF4B input-freeze ID")
    path = AF4B_ARTIFACT_ROOT / "input-freezes" / input_freeze_id / "manifest.json"
    try:
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AF4BExecutionError("cannot read AF4B input-freeze manifest") from error
    if payload != canonical_json_bytes(document):
        raise AF4BExecutionError("AF4B input-freeze manifest is not canonical")
    recorded = document.pop("input_freeze_id", None)
    actual = sha256(canonical_json_bytes(document)).hexdigest()
    document["input_freeze_id"] = recorded
    if recorded != input_freeze_id or actual != input_freeze_id:
        raise AF4BExecutionError("AF4B input-freeze identity mismatch")
    if (
        document.get("research_role") != "DEVELOPMENT"
        or document.get("allow_screening_validation") is not False
        or document.get("allow_sealed_oos") is not False
        or document.get("allow_2026_research") is not False
        or document.get("catalog_id") != EXPECTED_CATALOG_ID
        or document.get("evaluator", {}).get("source_sha256")
        != file_sha256(Path(__file__).resolve())
    ):
        raise AF4BExecutionError("AF4B input-freeze contract mismatch")
    return document

def record_execution_gate(
    input_freeze_id: str,
    *,
    focused_test_count: int,
    full_test_count: int,
) -> dict[str, Any]:
    """Record that the frozen evaluator passed required tests before execution."""
    load_input_freeze(input_freeze_id)
    if (
        type(focused_test_count) is not int
        or focused_test_count < 24
        or type(full_test_count) is not int
        or full_test_count < focused_test_count
    ):
        raise AF4BExecutionError("invalid AF4B pre-execution test totals")
    body = {
        "schema_version": "af4b-execution-gate-v1",
        "input_freeze_id": input_freeze_id,
        "catalog_id": EXPECTED_CATALOG_ID,
        "evaluator_id": EVALUATOR_ID,
        "evaluator_source_sha256": file_sha256(Path(__file__).resolve()),
        "focused_test_file_sha256": file_sha256(
            REPO_ROOT / "tests/unit/test_af4b_development_screen.py"
        ),
        "focused_tests": {
            "command": (
                r"G:\QuantOS\.venv\Scripts\python.exe -m unittest "
                "tests.unit.test_af4b_development_screen -v"
            ),
            "passed": focused_test_count,
        },
        "full_repository_tests": {
            "command": (
                r"G:\QuantOS\.venv\Scripts\python.exe -m unittest "
                "discover -s tests -t . -v"
            ),
            "passed": full_test_count,
        },
        "gate_passed_before_predictive_execution": True,
    }
    gate_id = sha256(canonical_json_bytes(body)).hexdigest()
    document = {**body, "execution_gate_id": gate_id}
    path = (
        AF4B_ARTIFACT_ROOT
        / "input-freezes"
        / input_freeze_id
        / "execution_gate.json"
    )
    publish_immutable_bytes(path, canonical_json_bytes(document))
    return document


def load_execution_gate(input_freeze_id: str) -> dict[str, Any]:
    path = (
        AF4B_ARTIFACT_ROOT
        / "input-freezes"
        / input_freeze_id
        / "execution_gate.json"
    )
    try:
        payload = path.read_bytes()
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AF4BExecutionError("AF4B execution gate is unavailable") from error
    if payload != canonical_json_bytes(document):
        raise AF4BExecutionError("AF4B execution gate is not canonical")
    recorded = document.pop("execution_gate_id", None)
    actual = sha256(canonical_json_bytes(document)).hexdigest()
    document["execution_gate_id"] = recorded
    if (
        recorded != actual
        or document.get("input_freeze_id") != input_freeze_id
        or document.get("evaluator_source_sha256")
        != file_sha256(Path(__file__).resolve())
        or document.get("gate_passed_before_predictive_execution") is not True
        or document.get("focused_tests", {}).get("passed", 0) < 24
    ):
        raise AF4BExecutionError("AF4B execution gate verification failed")
    return document


def _resolved_data_path(locator: str) -> Path:
    if type(locator) is not str or not locator:
        raise AF4BExecutionError("invalid QuantOS-Data locator")
    path = (DATA_ROOT / Path(locator)).resolve()
    try:
        path.relative_to(DATA_ROOT.resolve())
    except ValueError as error:
        raise AF4BExecutionError("QuantOS-Data locator escapes the data root") from error
    return path


def _load_bound_candles(descriptor: dict[str, Any]) -> tuple[Candle, ...]:
    if descriptor.get("research_role") != "DEVELOPMENT":
        raise AF4BExecutionError("protected candle role rejected")
    path = _resolved_data_path(descriptor["locator"])
    if file_sha256(path) != descriptor["file_sha256"]:
        raise AF4BExecutionError("bound candle file SHA-256 mismatch")
    sequence = ParquetCandleDatasetStore(CANDLE_STORE_ROOT).read(path)
    if (
        candle_storage_dataset_id(sequence.identity)
        != descriptor["storage_dataset_id"]
        or canonical_candle_content_sha256(sequence)
        != descriptor["content_sha256"]
        or len(sequence.candles) != descriptor["candle_count"]
    ):
        raise AF4BExecutionError("bound candle dataset identity mismatch")
    return sequence.candles


def _load_bound_states(descriptor: dict[str, Any]) -> tuple[AggregateTradeMinuteState, ...]:
    if descriptor.get("research_role") != "DEVELOPMENT":
        raise AF4BExecutionError("protected DE1 role rejected")
    state = descriptor["minute_state"]
    path = _resolved_data_path(state["canonical_parquet_locator"])
    if file_sha256(path) != state["canonical_parquet_sha256"]:
        raise AF4BExecutionError("bound minute-state file SHA-256 mismatch")
    dataset = ParquetAggregateTradeMinuteStateStore(DATA_ROOT).read(path)
    if (
        dataset.dataset_id != state["dataset_id"]
        or dataset.content_sha256 != state["content_sha256"]
        or len(dataset.states) != state["state_count"]
        or dataset.identity.source_range_id != descriptor["range_id"]
    ):
        raise AF4BExecutionError("bound minute-state dataset identity mismatch")
    return dataset.states


def _compute_frozen_results(
    catalog: dict[str, Any], input_document: dict[str, Any]
) -> list[dict[str, Any]]:
    """Run all 11 registered rows without result-dependent branching."""
    evaluations = catalog["evaluations"]
    rows: list[dict[str, Any]] = []
    input_id = input_document["input_freeze_id"]

    btc_candles = _load_bound_candles(input_document["candles"]["BTCUSDT"])
    btc_states = _load_bound_states(input_document["de1"]["BTCUSDT"])
    for evaluation in evaluations:
        if (
            evaluation["source_symbol"] == "BTCUSDT"
            and evaluation["target_symbol"] == "BTCUSDT"
        ):
            candle_descriptor = input_document["candles"]["BTCUSDT"]
            rows.append(
                screen_registered_evaluation(
                    catalog,
                    evaluation,
                    btc_states,
                    btc_candles,
                    input_freeze_id=input_id,
                    target_candle_dataset_id=candle_descriptor[
                        "logical_research_dataset_id"
                    ],
                    target_candle_content_sha256=candle_descriptor[
                        "content_sha256"
                    ],
                )
            )
    del btc_candles
    gc.collect()

    eth_candles = _load_bound_candles(input_document["candles"]["ETHUSDT"])
    for evaluation in evaluations:
        if (
            evaluation["source_symbol"] == "BTCUSDT"
            and evaluation["target_symbol"] == "ETHUSDT"
        ):
            candle_descriptor = input_document["candles"]["ETHUSDT"]
            rows.append(
                screen_registered_evaluation(
                    catalog,
                    evaluation,
                    btc_states,
                    eth_candles,
                    input_freeze_id=input_id,
                    target_candle_dataset_id=candle_descriptor[
                        "logical_research_dataset_id"
                    ],
                    target_candle_content_sha256=candle_descriptor[
                        "content_sha256"
                    ],
                )
            )
    del btc_states
    gc.collect()

    eth_states = _load_bound_states(input_document["de1"]["ETHUSDT"])
    for evaluation in evaluations:
        if (
            evaluation["source_symbol"] == "ETHUSDT"
            and evaluation["target_symbol"] == "ETHUSDT"
        ):
            candle_descriptor = input_document["candles"]["ETHUSDT"]
            rows.append(
                screen_registered_evaluation(
                    catalog,
                    evaluation,
                    eth_states,
                    eth_candles,
                    input_freeze_id=input_id,
                    target_candle_dataset_id=candle_descriptor[
                        "logical_research_dataset_id"
                    ],
                    target_candle_content_sha256=candle_descriptor[
                        "content_sha256"
                    ],
                )
            )
    del eth_states, eth_candles
    gc.collect()
    return finalize_closed_family(catalog, rows)


def _results_report(rows: list[dict[str, Any]]) -> bytes:
    headings = (
        "evaluation_id|hypothesis|target|H|raw|deoverlap|gross|base_net|"
        "stress_2c|t|p|q|qualified_blocks|positive_blocks|M|S|F|R|RVS|"
        "comparator_gross|delta|classification"
    )
    lines = [
        "# AF4B Frozen DE1 DEVELOPMENT Screen",
        "",
        headings,
        "|".join("---" for _ in headings.split("|")),
    ]
    for row in rows:
        temporal = row["temporal_stability"]
        scores = row["evidence_scores"]
        comparator = row["comparator"]
        multiple = row["multiple_testing"]
        values = (
            row["evaluation_id"],
            row["hypothesis_id"],
            row["target_symbol"],
            str(row["horizon_minutes"]),
            str(row["raw_eligible_signal_count"]),
            str(row["deoverlapped_event_count"]),
            str(row["gross_expectancy"]),
            str(row["base_net_expectancy"]),
            str(row["stress_2c_net_expectancy"]),
            str(row["descriptive_t_statistic"]),
            str(row["raw_p_value"]),
            str(multiple["q_value"]),
            str(temporal["qualified_block_count"]),
            str(temporal["positive_qualified_block_count"]),
            str(scores["M"]),
            str(scores["S"]),
            str(scores["F"]),
            str(scores["R"]),
            str(scores["RVS"]),
            str(comparator["gross_expectancy"]),
            str(comparator["primary_minus_comparator_gross_expectancy"]),
            row["classification"],
        )
        lines.append("|".join(values))
    lines.extend(("", "Research only. Production approval: false.", ""))
    return "\n".join(lines).encode("utf-8")


def execute_frozen_screen(input_freeze_id: str) -> dict[str, Any]:
    """Execute and immutably publish exactly the frozen 11-row family."""
    catalog = frozen_catalog()
    input_document = load_input_freeze(input_freeze_id)
    gate = load_execution_gate(input_freeze_id)
    started = time.perf_counter()
    rows = _compute_frozen_results(catalog, input_document)
    evaluator_runtime = time.perf_counter() - started
    expected_ids = catalog["fdr_family"]["evaluation_ids"]
    if [row["evaluation_id"] for row in rows] != expected_ids:
        raise AF4BExecutionError("AF4B execution did not produce the exact 11 rows")
    classification_counts = {
        name: sum(row["classification"] == name for row in rows)
        for name in ("KILL", "WATCH", "PROMOTE")
    }
    results_document = {
        "schema_version": "af4b-development-screen-results-v1",
        "phase": "AF4B",
        "catalog_id": catalog["catalog_id"],
        "fdr_family_id": catalog["fdr_family"]["fdr_family_id"],
        "evaluator_id": EVALUATOR_ID,
        "evaluator_source_sha256": file_sha256(Path(__file__).resolve()),
        "input_freeze_id": input_freeze_id,
        "execution_gate_id": gate["execution_gate_id"],
        "registered_evaluation_count": 11,
        "rows": rows,
        "classification_counts": classification_counts,
        "execution_gate_passed_before_predictive_access": True,
        "production_approved": False,
        "allow_screening_validation": False,
        "allow_sealed_oos": False,
        "allow_2026_research": False,
    }
    results_payload = canonical_json_bytes(results_document)
    results_sha = sha256(results_payload).hexdigest()
    report_payload = _results_report(rows)
    evaluator_payload = Path(__file__).resolve().read_bytes()
    manifest_body = {
        "schema_version": "af4b-development-screen-run-v1",
        "phase": "AF4B",
        "catalog_id": catalog["catalog_id"],
        "fdr_family_id": catalog["fdr_family"]["fdr_family_id"],
        "input_freeze_id": input_freeze_id,
        "execution_gate_id": gate["execution_gate_id"],
        "evaluator_id": EVALUATOR_ID,
        "evaluator_source_sha256": sha256(evaluator_payload).hexdigest(),
        "results_sha256": results_sha,
        "report_sha256": sha256(report_payload).hexdigest(),
        "classification_counts": classification_counts,
        "registered_evaluation_count": 11,
        "production_approved": False,
        "protected_roles_accessed": False,
    }
    run_id = sha256(canonical_json_bytes(manifest_body)).hexdigest()
    manifest_document = {**manifest_body, "run_id": run_id}
    run_root = AF4B_ARTIFACT_ROOT / "runs" / run_id
    publish_immutable_bytes(run_root / "manifest.json", canonical_json_bytes(manifest_document))
    publish_immutable_bytes(run_root / "results.json", results_payload)
    publish_immutable_bytes(run_root / "report.md", report_payload)
    publish_immutable_bytes(run_root / "evaluator.py", evaluator_payload)
    metrics_path = run_root / "operator_metrics.json"
    if not metrics_path.exists():
        publish_immutable_bytes(
            metrics_path,
            canonical_json_bytes({
                "schema_version": "af4b-run-operator-metrics-v1",
                "run_id": run_id,
                "evaluator_runtime_seconds": repr(evaluator_runtime),
                "peak_working_set_bytes": _peak_working_set_bytes(),
            }),
        )
    verify_result_artifact(run_id, replay=False)
    return manifest_document


def verify_result_artifact(run_id: str, *, replay: bool) -> dict[str, Any]:
    """Independently verify identities, BH, classification, comparators, and replay."""
    if (
        type(run_id) is not str
        or len(run_id) != 64
        or any(character not in "0123456789abcdef" for character in run_id)
    ):
        raise AF4BExecutionError("invalid AF4B run ID")
    run_root = AF4B_ARTIFACT_ROOT / "runs" / run_id
    try:
        manifest_payload = (run_root / "manifest.json").read_bytes()
        manifest = json.loads(manifest_payload.decode("utf-8"))
        results_payload = (run_root / "results.json").read_bytes()
        results = json.loads(results_payload.decode("utf-8"))
        report_payload = (run_root / "report.md").read_bytes()
        evaluator_payload = (run_root / "evaluator.py").read_bytes()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AF4BExecutionError("AF4B result artifact is unreadable") from error
    if (
        manifest_payload != canonical_json_bytes(manifest)
        or results_payload != canonical_json_bytes(results)
    ):
        raise AF4BExecutionError("AF4B result JSON is not canonical")
    recorded_run_id = manifest.pop("run_id", None)
    computed_run_id = sha256(canonical_json_bytes(manifest)).hexdigest()
    manifest["run_id"] = recorded_run_id
    if (
        recorded_run_id != run_id
        or computed_run_id != run_id
        or sha256(results_payload).hexdigest() != manifest["results_sha256"]
        or sha256(report_payload).hexdigest() != manifest["report_sha256"]
        or sha256(evaluator_payload).hexdigest()
        != manifest["evaluator_source_sha256"]
        or evaluator_payload != Path(__file__).resolve().read_bytes()
    ):
        raise AF4BExecutionError("AF4B result artifact identity mismatch")
    catalog = frozen_catalog()
    input_document = load_input_freeze(manifest["input_freeze_id"])
    gate = load_execution_gate(manifest["input_freeze_id"])
    rows = results.get("rows")
    expected_ids = catalog["fdr_family"]["evaluation_ids"]
    if (
        manifest.get("phase") != "AF4B"
        or results.get("phase") != "AF4B"
        or manifest.get("catalog_id") != catalog["catalog_id"]
        or results.get("catalog_id") != catalog["catalog_id"]
        or manifest.get("fdr_family_id") != catalog["fdr_family"]["fdr_family_id"]
        or results.get("fdr_family_id") != catalog["fdr_family"]["fdr_family_id"]
        or manifest.get("input_freeze_id") != results.get("input_freeze_id")
        or manifest.get("execution_gate_id") != gate["execution_gate_id"]
        or results.get("execution_gate_id") != gate["execution_gate_id"]
        or manifest.get("evaluator_id") != EVALUATOR_ID
        or results.get("evaluator_id") != EVALUATOR_ID
        or results.get("evaluator_source_sha256")
        != manifest["evaluator_source_sha256"]
        or manifest.get("registered_evaluation_count") != 11
        or manifest.get("production_approved") is not False
        or results.get("production_approved") is not False
        or manifest.get("protected_roles_accessed") is not False
        or results.get("execution_gate_passed_before_predictive_access") is not True
        or type(rows) is not list
        or len(rows) != 11
        or [row.get("evaluation_id") for row in rows] != expected_ids
        or results.get("registered_evaluation_count") != 11
        or results.get("allow_screening_validation") is not False
        or results.get("allow_sealed_oos") is not False
        or results.get("allow_2026_research") is not False
    ):
        raise AF4BExecutionError("AF4B result universe or role boundary mismatch")

    p_values = tuple(
        Decimal(row["raw_p_value"]) if row["raw_p_value"] is not None else None
        for row in rows
    )
    expected_q = benjamini_hochberg(p_values)
    observed_q = tuple(
        Decimal(row["multiple_testing"]["q_value"])
        if row["multiple_testing"]["q_value"] is not None
        else None
        for row in rows
    )
    if observed_q != expected_q:
        raise AF4BExecutionError("AF4B independent BH verification failed")

    raw_rows: list[dict[str, Any]] = []
    for row in rows:
        comparator = row["comparator"]
        gross = Decimal(row["gross_expectancy"]) if row["gross_expectancy"] is not None else None
        comparator_gross = (
            Decimal(comparator["gross_expectancy"])
            if comparator["gross_expectancy"] is not None
            else None
        )
        delta = (
            Decimal(comparator["primary_minus_comparator_gross_expectancy"])
            if comparator["primary_minus_comparator_gross_expectancy"] is not None
            else None
        )
        with af1_decimal_context():
            expected_delta = (
                gross - comparator_gross
                if gross is not None and comparator_gross is not None
                else None
            )
        if delta != expected_delta:
            raise AF4BExecutionError("AF4B comparator arithmetic verification failed")
        raw = {
            key: value
            for key, value in row.items()
            if key not in (
                "multiple_testing",
                "classification",
                "incremental_information_status",
            )
        }
        raw["_p_value"] = (
            Decimal(row["raw_p_value"]) if row["raw_p_value"] is not None else None
        )
        raw["_base_expectancy"] = (
            Decimal(row["base_net_expectancy"])
            if row["base_net_expectancy"] is not None
            else None
        )
        raw_rows.append(raw)
    if finalize_closed_family(catalog, raw_rows) != rows:
        raise AF4BExecutionError("AF4B classification replay failed")
    classification_counts = {
        name: sum(row["classification"] == name for row in rows)
        for name in ("KILL", "WATCH", "PROMOTE")
    }
    if (
        manifest.get("classification_counts") != classification_counts
        or results.get("classification_counts") != classification_counts
        or report_payload != _results_report(rows)
    ):
        raise AF4BExecutionError("AF4B derived artifact verification failed")
    if replay and _compute_frozen_results(catalog, input_document) != rows:
        raise AF4BExecutionError("AF4B deterministic result replay failed")
    return manifest

def _catalog(root: Path) -> LocalAggregateTradeArchiveCatalog:
    return LocalAggregateTradeArchiveCatalog(
        root,
        provider="binance",
        market="spot",
        event_family="aggregate_trade",
    )


def _progress(payload: dict[str, object]) -> None:
    print(canonical_json_bytes(payload).decode("utf-8").rstrip(), flush=True)


def _acquire_partition_process(
    arguments: tuple[str, str, str],
) -> tuple[str, str, str | None, dict[str, str] | None]:
    """Acquire one disjoint partition in an isolated interpreter process."""
    data_root_text, symbol, source_date_text = arguments
    source_date = date.fromisoformat(source_date_text)
    try:
        require_development_role(ResearchDatasetRole.DEVELOPMENT)
        fetched = BinanceSpotAggregateTradeDailyArchiveAdapter(
            timeout_seconds=60.0
        ).fetch_daily_archive(
            symbol=symbol,
            archive_date=source_date,
            research_role=ResearchDatasetRole.DEVELOPMENT,
        )
        publication = ParquetAggregateTradeArchiveStore(Path(data_root_text)).write(
            fetched.archive,
            raw_zip_bytes=fetched.raw_zip_bytes,
        )
        return symbol, source_date_text, publication.manifest_id, None
    except (
        BinanceAggregateTradeArchiveError,
        AggregateTradeParquetStorageError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        return symbol, source_date_text, None, {
            "symbol": symbol,
            "source_date": source_date_text,
            "error": f"{type(error).__name__}: {error}",
        }

def acquire_required_de1(
    data_root: Path = DATA_ROOT,
    *,
    progress: Callable[[dict[str, object]], None] = _progress,
    max_workers: int = 1,
) -> dict[str, object]:
    """Acquire missing frozen partitions; existing verified revisions are reused."""
    catalog_document = frozen_catalog()
    dates = required_dates(catalog_document)
    if tuple(catalog_document["data_contract"]["required_symbols"]) != SYMBOLS:
        raise AF4BExecutionError("frozen symbol boundary mismatch")
    if len(dates) != 639:
        raise AF4BExecutionError("frozen logical date count is not 639")
    data_root = Path(data_root)
    catalog = _catalog(data_root)
    try:
        initial = catalog.rebuild()
    except (AggregateTradeCatalogError, AggregateTradeParquetStorageError) as error:
        raise AF4BExecutionError(f"existing DE1 catalog failed verification: {error}") from error
    existing = {
        (entry.logical_partition.symbol, entry.logical_partition.source_date)
        for entry in initial.entries
    }
    required = {(symbol, source_date) for symbol in SYMBOLS for source_date in dates}
    outside = existing - required
    if outside:
        # Other immutable data may coexist, but it must not enter this run.
        progress({"event": "af4b_de1_catalog_outside_scope", "partition_count": len(outside)})
    if type(max_workers) is not int or not 1 <= max_workers <= 8:
        raise AF4BExecutionError("acquisition max_workers must be from 1 through 8")
    reused = len(existing & required)
    acquired = 0
    failures: list[dict[str, str]] = []
    missing = sorted(required - existing, key=lambda item: (item[0], item[1]))
    progress({
        "event": "af4b_de1_acquisition_start",
        "required_partitions": len(required),
        "verified_reused_partitions": reused,
        "missing_partitions": len(missing),
    })
    def attempt(
        symbol: str,
        source_date: date,
        *,
        adapter: BinanceSpotAggregateTradeDailyArchiveAdapter,
        store: ParquetAggregateTradeArchiveStore,
    ) -> tuple[object | None, dict[str, str] | None]:
        try:
            require_development_role(ResearchDatasetRole.DEVELOPMENT)
            fetched = adapter.fetch_daily_archive(
                symbol=symbol,
                archive_date=source_date,
                research_role=ResearchDatasetRole.DEVELOPMENT,
            )
            publication = store.write(
                fetched.archive,
                raw_zip_bytes=fetched.raw_zip_bytes,
            )
            return publication.manifest_id, None
        except (
            BinanceAggregateTradeArchiveError,
            AggregateTradeParquetStorageError,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            return None, {
                "symbol": symbol,
                "source_date": source_date.isoformat(),
                "error": f"{type(error).__name__}: {error}",
            }

    if max_workers == 1:
        adapter = BinanceSpotAggregateTradeDailyArchiveAdapter(timeout_seconds=60.0)
        store = ParquetAggregateTradeArchiveStore(data_root)
        outcomes = (
            (symbol, source_date, *attempt(
                symbol,
                source_date,
                adapter=adapter,
                store=store,
            ))
            for symbol, source_date in missing
        )
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=max_workers)
        outcomes = (
            (
                symbol,
                date.fromisoformat(source_date_text),
                manifest_id,
                failure,
            )
            for symbol, source_date_text, manifest_id, failure in executor.map(
                _acquire_partition_process,
                (
                    (str(data_root), symbol, source_date.isoformat())
                    for symbol, source_date in missing
                ),
                chunksize=1,
            )
        )
    try:
        for ordinal, (symbol, source_date, manifest_id, failure) in enumerate(
            outcomes, start=1
        ):
            if failure is not None:
                failures.append(failure)
                progress({"event": "af4b_de1_acquisition_failure", **failure})
                continue
            assert manifest_id is not None
            acquired += 1
            if ordinal == 1 or ordinal % 10 == 0 or ordinal == len(missing):
                progress({
                    "event": "af4b_de1_acquisition_progress",
                    "processed_missing_partitions": ordinal,
                    "newly_acquired_partitions": acquired,
                    "failed_partitions": len(failures),
                    "last_manifest_id": manifest_id,
                    "last_source_date": source_date.isoformat(),
                    "last_symbol": symbol,
                })
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
    if failures:
        raise AF4BExecutionError(
            "required DE1 acquisition failed: "
            + json.dumps(failures, sort_keys=True, separators=(",", ":"))
        )
    try:
        final = catalog.rebuild()
    except (AggregateTradeCatalogError, AggregateTradeParquetStorageError) as error:
        raise AF4BExecutionError(f"final DE1 catalog verification failed: {error}") from error
    final_keys = {
        (entry.logical_partition.symbol, entry.logical_partition.source_date)
        for entry in final.entries
    }
    absent = sorted(required - final_keys, key=lambda item: (item[0], item[1]))
    if absent:
        raise AF4BExecutionError(f"final DE1 catalog is missing {len(absent)} partitions")
    summary: dict[str, object] = {
        "event": "af4b_de1_acquisition_complete",
        "required_logical_partitions": len(required),
        "successfully_frozen_logical_partitions": len(required),
        "verified_reused_logical_partitions": reused,
        "newly_acquired_logical_partitions": acquired,
        "failed_logical_partitions": 0,
        "catalog_id": final.catalog_id,
    }
    progress(summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute the frozen AF4B pipeline")
    parser.add_argument(
        "stage",
        nargs="?",
        default="acquire",
        choices=("acquire", "freeze", "gate", "execute", "verify"),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--input-freeze-id")
    parser.add_argument("--focused-test-count", type=int)
    parser.add_argument("--full-test-count", type=int)
    parser.add_argument("--run-id")
    parser.add_argument("--replay", action="store_true")
    arguments = parser.parse_args()
    if arguments.stage == "acquire":
        result = acquire_required_de1(max_workers=arguments.workers)
    elif arguments.stage == "freeze":
        result = freeze_development_inputs()
    elif arguments.stage == "gate":
        if (
            arguments.input_freeze_id is None
            or arguments.focused_test_count is None
            or arguments.full_test_count is None
        ):
            parser.error("gate requires input-freeze ID and both test counts")
        result = record_execution_gate(
            arguments.input_freeze_id,
            focused_test_count=arguments.focused_test_count,
            full_test_count=arguments.full_test_count,
        )
    elif arguments.stage == "execute":
        if arguments.input_freeze_id is None:
            parser.error("execute requires --input-freeze-id")
        result = execute_frozen_screen(arguments.input_freeze_id)
    else:
        if arguments.run_id is None:
            parser.error("verify requires --run-id")
        result = verify_result_artifact(arguments.run_id, replay=arguments.replay)
    _progress(result)


if __name__ == "__main__":
    main()
