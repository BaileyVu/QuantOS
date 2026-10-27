"""AF3B frozen research split and dataset-bound AF1 universe verification."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from quantos.domain.evaluation.alpha_catalog import (
    load_and_verify_fdr_plan,
    load_catalog_bytes,
)
from quantos.domain.evaluation.alpha_funnel import (
    DEFAULT_HORIZONS_MINUTES,
    DatasetRole,
    FdrUniverse,
    ResearchDataset,
    ResearchHypothesis,
    canonical_candle_content_sha256,
    canonical_json_bytes,
    declare_fdr_universe,
)
from quantos.domain.evaluation.alpha_hypotheses import (
    load_and_verify_materialization_manifest,
    materialize_hypotheses,
)
from quantos.domain.market_data import (
    DatasetIdentity,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)


SPLIT_SCHEMA_VERSION = "af3-research-split-v1"
UNIVERSE_SCHEMA_VERSION = "af3b-universe-binding-v1"
AF3B_AUTHORITY_ID = "af3b-development-universe-v1"
FREEZE_CODE_VERSION = "c6e7617577d8536431e288a1689d3fcb57c389c9"
FROZEN_CATALOG_ID = "060147848ab0d3aa3deb4a77fe0cf5af6bdd10a374587c24e5ff7a640a0a5d74"
FROZEN_FDR_PLAN_ID = "32a6a1e1e916a0b012bd5ac22d747b3b57afa53a99e5153d0fb1a0685511a6ae"
FROZEN_AF3A_MANIFEST_ID = "714c712920bf390ebf1f899b1ed411819b444be43be141d3191d816126c177ec"
CONTAMINATION_POLICY = (
    "All canonical historical data available at the AF3 split-freeze point is treated as "
    "DEVELOPMENT because prior exploratory research contamination boundaries are not "
    "sufficiently precise to certify an existing historical segment as unseen. "
    "SCREENING_VALIDATION and SEALED_OOS are future-only."
)
DEVELOPMENT_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
DEVELOPMENT_END_EXCLUSIVE = datetime(2025, 10, 1, tzinfo=timezone.utc)
SCREENING_VALIDATION_END_EXCLUSIVE = DEVELOPMENT_END_EXCLUSIVE + timedelta(days=60)
EXPECTED_SPLIT_DECLARATION_ID = "6c2e037cc844a73bff8ee80b51920b93bdfb05a3fe05730de7abdc8e09ff4a86"
EXPECTED_SYMBOLS = ("BTCUSDT", "ETHUSDT")
EXPECTED_DEFINITION_COUNT = 44
EXPECTED_FDR_TEST_COUNT = 616
_ONE_MINUTE = timedelta(minutes=1)


class AlphaUniverseError(ValueError):
    """AF3B split, dataset, or universe binding is invalid."""


def _timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise AlphaUniverseError("AF3B timestamps must be built-in UTC datetimes")
    if value.second != 0 or value.microsecond != 0:
        raise AlphaUniverseError("AF3B timestamps must be minute aligned")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_timestamp(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise AlphaUniverseError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise AlphaUniverseError(f"{name} must be a canonical UTC timestamp") from error
    if _timestamp(parsed) != value:
        raise AlphaUniverseError(f"{name} must be canonically encoded")
    return parsed


def _parse_canonical_document(payload: bytes, name: str) -> dict[str, object]:
    if type(payload) is not bytes:
        raise AlphaUniverseError(f"{name} payload must be bytes")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AlphaUniverseError(f"{name} contains duplicate keys")
            result[key] = value
        return result

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlphaUniverseError(f"{name} is not valid UTF-8 JSON") from error
    if type(document) is not dict:
        raise AlphaUniverseError(f"{name} must be a JSON object")
    if payload != canonical_json_bytes(document):
        raise AlphaUniverseError(f"{name} must use canonical JSON bytes")
    return document


def derive_split_declaration() -> dict[str, object]:
    """Return the exact source-controlled AF3 split declaration."""
    identity = {
        "schema_version": SPLIT_SCHEMA_VERSION,
        "declaration_purpose": "Freeze AF3 DEVELOPMENT and reserve future validation periods",
        "contamination_policy": CONTAMINATION_POLICY,
        "symbols": list(EXPECTED_SYMBOLS),
        "interval": "1m",
        "development_start": _timestamp(DEVELOPMENT_START),
        "development_end_exclusive": _timestamp(DEVELOPMENT_END_EXCLUSIVE),
        "screening_validation_start": _timestamp(DEVELOPMENT_END_EXCLUSIVE),
        "screening_validation_end_exclusive": _timestamp(SCREENING_VALIDATION_END_EXCLUSIVE),
        "sealed_oos_start": _timestamp(SCREENING_VALIDATION_END_EXCLUSIVE),
        "sealed_oos_end_exclusive": None,
        "freeze_code_version": FREEZE_CODE_VERSION,
    }
    return {
        **identity,
        "split_declaration_id": sha256(canonical_json_bytes(identity)).hexdigest(),
    }


def load_and_verify_split(payload: bytes) -> Mapping[str, object]:
    """Verify exact boundaries, role isolation, identity, and frozen semantics."""
    document = _parse_canonical_document(payload, "AF3 split declaration")
    identity = dict(document)
    claimed = identity.pop("split_declaration_id", None)
    if claimed != sha256(canonical_json_bytes(identity)).hexdigest():
        raise AlphaUniverseError("AF3 split declaration identity mismatch")
    if claimed != EXPECTED_SPLIT_DECLARATION_ID:
        raise AlphaUniverseError("AF3 split declaration is not the frozen declaration")
    if document != derive_split_declaration():
        raise AlphaUniverseError("AF3 split declaration semantics changed")
    if document.get("symbols") != list(EXPECTED_SYMBOLS) or document.get("interval") != "1m":
        raise AlphaUniverseError("AF3 split must contain exactly the two V1 symbols at 1m")
    development_start = _parse_timestamp(document.get("development_start"), "development_start")
    development_end = _parse_timestamp(
        document.get("development_end_exclusive"), "development_end_exclusive"
    )
    validation_start = _parse_timestamp(
        document.get("screening_validation_start"), "screening_validation_start"
    )
    validation_end = _parse_timestamp(
        document.get("screening_validation_end_exclusive"),
        "screening_validation_end_exclusive",
    )
    sealed_start = _parse_timestamp(document.get("sealed_oos_start"), "sealed_oos_start")
    if not development_start < development_end:
        raise AlphaUniverseError("AF3 DEVELOPMENT must have a positive duration")
    if validation_start != development_end:
        raise AlphaUniverseError("AF3 validation must begin exactly after DEVELOPMENT")
    if validation_end - validation_start != timedelta(days=60):
        raise AlphaUniverseError("AF3 validation must span exactly 60 calendar days")
    if sealed_start != validation_end or document.get("sealed_oos_end_exclusive") is not None:
        raise AlphaUniverseError("AF3 sealed OOS must be open-ended after validation")
    return document


def _development_sequence(
    source: ValidatedCandleSequence, split: Mapping[str, object], symbol: str,
) -> ValidatedCandleSequence:
    if type(source) is not ValidatedCandleSequence:
        raise AlphaUniverseError("AF3B source must be a ValidatedCandleSequence")
    if source.identity.validation_status is not DatasetValidationStatus.VALIDATED:
        raise AlphaUniverseError("AF3B source must be canonically validated")
    if symbol not in EXPECTED_SYMBOLS or source.identity.symbol != symbol:
        raise AlphaUniverseError("AF3B source symbol mismatch")
    if source.identity.timeframe != "1m":
        raise AlphaUniverseError("AF3B source interval must be 1m")
    start = _parse_timestamp(split.get("development_start"), "development_start")
    end = _parse_timestamp(split.get("development_end_exclusive"), "development_end_exclusive")
    selected = tuple(candle for candle in source.candles if start <= candle.open_time < end)
    expected_count = int((end - start) / _ONE_MINUTE)
    if (
        len(selected) != expected_count
        or not selected
        or selected[0].open_time != start
        or selected[-1].open_time != end - _ONE_MINUTE
    ):
        raise AlphaUniverseError(f"{symbol} cannot satisfy the frozen DEVELOPMENT range")
    identity = DatasetIdentity(
        symbol=symbol,
        timeframe="1m",
        start_time=start,
        end_time=end - _ONE_MINUTE,
        source=source.identity.source,
        schema_version=source.identity.schema_version,
        ingestion_version=source.identity.ingestion_version,
    )
    try:
        return validate_candle_sequence(identity, selected)
    except (TypeError, ValueError) as error:
        raise AlphaUniverseError(f"{symbol} DEVELOPMENT validation failed: {error}") from error


def build_development_datasets(
    split: Mapping[str, object], sources: Sequence[ValidatedCandleSequence],
) -> tuple[ResearchDataset, ...]:
    """Bind only the fixed DEVELOPMENT slice, even if later source data exists."""
    if type(sources) not in (tuple, list) or len(sources) != 2:
        raise AlphaUniverseError("AF3B requires exactly two canonical source sequences")
    by_symbol = {source.identity.symbol: source for source in sources if type(source) is ValidatedCandleSequence}
    if set(by_symbol) != set(EXPECTED_SYMBOLS):
        raise AlphaUniverseError("AF3B requires exactly BTCUSDT and ETHUSDT sources")
    datasets = []
    for symbol in EXPECTED_SYMBOLS:
        sequence = _development_sequence(by_symbol[symbol], split, symbol)
        datasets.append(ResearchDataset(
            dataset_id=f"af3-development-{symbol.lower()}",
            content_sha256=canonical_candle_content_sha256(sequence),
            role=DatasetRole.DEVELOPMENT,
            sequence=sequence,
        ))
    return tuple(datasets)


def build_af3b_universe(
    datasets: tuple[ResearchDataset, ...], hypotheses: tuple[ResearchHypothesis, ...],
) -> FdrUniverse:
    """Declare and independently check the complete genuine AF1 product."""
    if len(datasets) != 2 or any(item.role is not DatasetRole.DEVELOPMENT for item in datasets):
        raise AlphaUniverseError("AF3B universe requires exactly two DEVELOPMENT datasets")
    if len(hypotheses) != EXPECTED_DEFINITION_COUNT:
        raise AlphaUniverseError("AF3B universe requires exactly 44 hypotheses")
    universe = declare_fdr_universe(datasets, hypotheses, authority_id=AF3B_AUTHORITY_ID)
    if len(universe.tests) != EXPECTED_FDR_TEST_COUNT or len(set(universe.tests)) != len(universe.tests):
        raise AlphaUniverseError("AF3B universe must contain exactly 616 unique keys")
    symbols = Counter(test.symbol for test in universe.tests)
    if symbols != Counter({"BTCUSDT": 308, "ETHUSDT": 308}):
        raise AlphaUniverseError("AF3B universe must contain 308 keys per symbol")
    if {test.horizon_minutes for test in universe.tests} != set(DEFAULT_HORIZONS_MINUTES):
        raise AlphaUniverseError("AF3B universe horizons changed")
    if any(test.dataset_role is not DatasetRole.DEVELOPMENT for test in universe.tests):
        raise AlphaUniverseError("non-DEVELOPMENT data entered the AF3B universe")
    return universe


def verify_af2a_to_af1_mapping(
    plan: Mapping[str, object], manifest: Mapping[str, object], universe: FdrUniverse,
) -> None:
    """Prove every planned registration maps to one genuine AF1 key."""
    definitions = manifest.get("definitions")
    registrations = plan.get("planned_registered_tests")
    if type(definitions) is not list or len(definitions) != EXPECTED_DEFINITION_COUNT:
        raise AlphaUniverseError("AF3A manifest definition mapping changed")
    if type(registrations) is not list or len(registrations) != EXPECTED_FDR_TEST_COUNT:
        raise AlphaUniverseError("AF2A planned registration count changed")
    by_planned = {row["planned_definition_sha256"]: row for row in definitions}
    if len(by_planned) != EXPECTED_DEFINITION_COUNT:
        raise AlphaUniverseError("AF3A planned-definition mapping is not unique")
    actual = {
        (test.hypothesis_id, test.hypothesis_definition_sha256, test.symbol, test.horizon_minutes)
        for test in universe.tests
    }
    mapped = set()
    planned_keys = set()
    for registration in registrations:
        planned_key = (
            registration["planned_definition_sha256"],
            registration["symbol"],
            registration["horizon_minutes"],
        )
        if planned_key in planned_keys:
            raise AlphaUniverseError("AF2A planned registrations are not unique")
        planned_keys.add(planned_key)
        definition = by_planned.get(registration["planned_definition_sha256"])
        if definition is None:
            raise AlphaUniverseError("AF2A registration lacks an AF3A mapping")
        mapped.add((
            definition["af1_stable_id"],
            definition["af1_definition_sha256"],
            registration["symbol"],
            registration["horizon_minutes"],
        ))
    if len(mapped) != EXPECTED_FDR_TEST_COUNT or mapped != actual:
        raise AlphaUniverseError("AF2A registrations do not map one-to-one onto AF1 keys")


def _dataset_summary(dataset: ResearchDataset) -> dict[str, object]:
    identity = dataset.identity_dict()
    return {
        "symbol": identity["symbol"],
        "dataset_id": dataset.dataset_id,
        "content_sha256": dataset.content_sha256,
        "dataset_identity_sha256": sha256(canonical_json_bytes(identity)).hexdigest(),
        "role": dataset.role.value,
        "start": identity["start_time"],
        "end_exclusive": _timestamp(dataset.sequence.identity.end_time + _ONE_MINUTE),
        "candle_count": identity["candle_count"],
    }


def derive_universe_artifact(
    split: Mapping[str, object], datasets: tuple[ResearchDataset, ...],
    hypotheses: tuple[ResearchHypothesis, ...], universe: FdrUniverse,
) -> dict[str, object]:
    """Derive the compact durable proof; AF1 already owns all 616 full keys."""
    expected_universe = build_af3b_universe(datasets, hypotheses)
    if universe != expected_universe:
        raise AlphaUniverseError("supplied AF1 universe differs from the derived universe")
    identity = {
        "schema_version": UNIVERSE_SCHEMA_VERSION,
        "split_declaration_id": split["split_declaration_id"],
        "catalog_id": FROZEN_CATALOG_ID,
        "fdr_plan_id": FROZEN_FDR_PLAN_ID,
        "af3a_manifest_id": FROZEN_AF3A_MANIFEST_ID,
        "authority_id": AF3B_AUTHORITY_ID,
        "datasets": [
            _dataset_summary(dataset)
            for dataset in sorted(datasets, key=lambda item: item.sequence.identity.symbol)
        ],
        "development_start": split["development_start"],
        "development_end_exclusive": split["development_end_exclusive"],
        "hypothesis_count": len(hypotheses),
        "horizons_minutes": list(DEFAULT_HORIZONS_MINUTES),
        "fdr_test_count": len(universe.tests),
        "af1_universe_id": universe.universe_id,
    }
    return {
        **identity,
        "universe_artifact_id": sha256(canonical_json_bytes(identity)).hexdigest(),
    }


def load_and_verify_universe_artifact(
    payload: bytes, expected: Mapping[str, object],
) -> Mapping[str, object]:
    """Verify canonical bytes, the artifact digest, and reconstructed semantics."""
    persisted = _parse_canonical_document(payload, "AF3B universe artifact")
    identity = dict(persisted)
    claimed = identity.pop("universe_artifact_id", None)
    if claimed != sha256(canonical_json_bytes(identity)).hexdigest():
        raise AlphaUniverseError("AF3B universe artifact identity mismatch")
    if persisted != expected:
        raise AlphaUniverseError("persisted AF3B universe binding differs from reconstructed data")
    return persisted


def verify_universe_artifact(
    *, split_payload: bytes, artifact_payload: bytes,
    sources: Sequence[ValidatedCandleSequence], catalog_payload: bytes,
    plan_payload: bytes, materialization_payload: bytes,
) -> tuple[tuple[ResearchDataset, ...], tuple[ResearchHypothesis, ...], FdrUniverse]:
    """Reconstruct every durable AF3B identity and reject any semantic drift."""
    split = load_and_verify_split(split_payload)
    catalog = load_catalog_bytes(catalog_payload)
    plan = load_and_verify_fdr_plan(catalog_payload, plan_payload)
    manifest = load_and_verify_materialization_manifest(
        catalog_payload, plan_payload, materialization_payload
    )
    hypotheses = materialize_hypotheses(catalog, plan)
    if (
        catalog.catalog_id != FROZEN_CATALOG_ID
        or plan.get("fdr_plan_id") != FROZEN_FDR_PLAN_ID
        or manifest.get("materialization_manifest_id") != FROZEN_AF3A_MANIFEST_ID
    ):
        raise AlphaUniverseError("AF2A or AF3A frozen identity changed")
    datasets = build_development_datasets(split, sources)
    universe = build_af3b_universe(datasets, hypotheses)
    verify_af2a_to_af1_mapping(plan, manifest, universe)
    expected = derive_universe_artifact(split, datasets, hypotheses, universe)
    load_and_verify_universe_artifact(artifact_payload, expected)

    return datasets, hypotheses, universe
