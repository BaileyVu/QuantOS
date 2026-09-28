"""Blinded AF4C identities. No discovery, observation export, or evaluation.

The Candle ID callable is a port for the existing canonical storage identity
implementation; it is not a second Candle hashing algorithm. Operational cache
evidence lives in a separate receipt and never participates in binding identity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from quantos.domain.evaluation.alpha_funnel import canonical_candle_content_sha256
from quantos.domain.market_data import (
    DatasetIdentity, DatasetValidationStatus, ValidatedCandleSequence,
)
from quantos.domain.market_data.research_events import (
    AGGREGATE_TRADE_MINUTE_AGGREGATION_VERSION,
    AggregateTradeArchiveManifest, AggregateTradeMinuteAvailabilityState,
    AggregateTradeMinuteDatasetIdentity, AggregateTradeMinuteSourceReference,
    AggregateTradePartitionReference, AggregateTradeRangeManifest,
    ResearchDatasetRole, ResearchEventValidationStatus, SourceTimestampUnit,
    ValidatedAggregateTradeMinuteDataset, aggregate_trade_minute_dataset_id,
    aggregate_trade_range_manifest_from_bytes,
)

SCHEMA = "af4c-development-binding-v1"
RECEIPT_SCHEMA = "af4c-development-binding-receipt-v1"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 10, 1, tzinfo=timezone.utc)
LINEAGE = (
    ("a1_commit", "3722019f461ea28eb9e49c2de27e6cd259dfb609"),
    ("preregistration_commit", "8a48faa0365e990de1bc13511016ca6bef746d96"),
    ("evaluator_commit", "06f68b289b37584dc554924dee3ac131242b427c"),
    ("catalog_id", "71b244b9843bc6c105f9b761f23437984e4ee87ee6c5156f300e72c8bb785350"),
    ("fdr_family_id", "f0d87b0935d56a8c53f3939304c3c071cc4d6ec33d59ef9029a775322432bd42"),
    ("evaluator_id", "af4c-impact-screen-tplus2-v1"),
)
CandleId = Callable[[DatasetIdentity], str]


class BindingError(ValueError):
    """Only fixed, observation-free messages cross the binding boundary."""


def _require(condition: bool) -> None:
    if not condition:
        raise BindingError("binding contract rejected")


def canonical_bytes(value: object) -> bytes:
    """Built-in JSON only, ASCII escapes in UTF-8, sorted keys, exactly one LF."""
    def check(item):
        if type(item) is dict:
            _require(all(type(key) is str for key in item))
            for child in item.values():
                check(child)
        elif type(item) is list:
            for child in item:
                check(child)
        else:
            _require(type(item) in (str, int, bool, type(None)))
    check(value)
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _decode(content: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    _require(type(content) is bytes)
    value = json.loads(content, object_pairs_hook=pairs)
    _require(type(value) is dict and canonical_bytes(value) == content)
    return value


def _keys(value, names: str) -> None:
    _require(type(value) is dict and set(value) == set(names.split()))


def _digest(value) -> str:
    _require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None)
    return value


def _token(value) -> str:
    # Version/source identifiers, never arbitrary strings or locators.
    _require(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is not None)
    return value


def _time(value: datetime) -> str:
    _require(type(value) is datetime and value.utcoffset() == timedelta(0))
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(_time(parsed) == value)
    return parsed


@dataclass(frozen=True, slots=True)
class _TestOnlyContract:
    """Private miniature-fixture seam. Public APIs never accept this object."""
    start: datetime
    end: datetime

    @property
    def days(self):
        _require(self.start.hour == self.end.hour == 0)
        _require(self.start.minute == self.end.minute == 0)
        _require(self.start.second == self.end.second == 0)
        _require(self.start.microsecond == self.end.microsecond == 0)
        _require(self.end > self.start)
        _time(self.start)
        _time(self.end)
        return (self.end - self.start).days

    @property
    def minutes(self):
        return self.days * 1440


_FROZEN = _TestOnlyContract(START, END)


@dataclass(frozen=True, slots=True, repr=False)
class BindingInputs:
    """Trusted validated objects; never include their repr in reports/errors."""
    candles: tuple[ValidatedCandleSequence, ...]
    candle_parquet_sha256: tuple[str, ...]
    ranges: tuple[AggregateTradeRangeManifest, ...]
    source_manifests: tuple[tuple[AggregateTradeArchiveManifest, ...], ...]
    minutes: tuple[ValidatedAggregateTradeMinuteDataset, ...]


def _minute_identity(value: dict) -> AggregateTradeMinuteDatasetIdentity:
    _keys(value, "aggregation_version availability_state market minute_interval_specification "
          "provider requested_end_time_exclusive requested_start_time source_event_family "
          "source_partitions source_range_id state_family state_schema_version symbol validation_status")
    _require(type(value["source_partitions"]) is list)
    refs = []
    for item in value["source_partitions"]:
        _keys(item, "source_date manifest_id source_revision_id dataset_id canonical_sequence_sha256 source_timestamp_unit")
        refs.append(AggregateTradeMinuteSourceReference(
            date.fromisoformat(item["source_date"]), item["manifest_id"],
            item["source_revision_id"], item["dataset_id"], item["canonical_sequence_sha256"],
            SourceTimestampUnit(item["source_timestamp_unit"])))
    identity = AggregateTradeMinuteDatasetIdentity(
        **{key: value[key] for key in (
            "provider", "market", "source_event_family", "state_family", "symbol",
            "source_range_id", "state_schema_version", "aggregation_version", "minute_interval_specification")},
        requested_start_time=_parse_time(value["requested_start_time"]),
        requested_end_time_exclusive=_parse_time(value["requested_end_time_exclusive"]),
        source_partitions=tuple(refs),
        availability_state=AggregateTradeMinuteAvailabilityState(value["availability_state"]),
        validation_status=ResearchEventValidationStatus(value["validation_status"]),
    )
    _require(identity.as_canonical_dict() == value)
    _require(identity.aggregation_version == AGGREGATE_TRADE_MINUTE_AGGREGATION_VERSION)
    _require((identity.provider, identity.market, identity.source_event_family) ==
             ("binance", "spot", "aggregate_trade"))
    return identity


def _candle_identity(value: dict, candle_id: CandleId) -> DatasetIdentity:
    _keys(value, "symbol timeframe dataset_id content_sha256 identity_sha256 start_open_time "
          "end_open_time candle_count source schema_version ingestion_version validation_status parquet_byte_sha256")
    for key in ("dataset_id", "content_sha256", "identity_sha256", "parquet_byte_sha256"):
        _digest(value[key])
    for key in ("source", "schema_version", "ingestion_version"):
        _token(value[key])
    identity = DatasetIdentity(
        symbol=value["symbol"], timeframe=value["timeframe"],
        start_time=_parse_time(value["start_open_time"]),
        end_time=_parse_time(value["end_open_time"]), source=value["source"],
        schema_version=value["schema_version"], ingestion_version=value["ingestion_version"],
    )._validated_copy()
    _require(value["validation_status"] == DatasetValidationStatus.VALIDATED.value)
    _require(candle_id(identity) == value["dataset_id"])
    # Physical encoding is separate evidence, not part of logical Candle identity.
    logical = {key: item for key, item in value.items()
               if key not in ("identity_sha256", "parquet_byte_sha256")}
    _require(sha256(canonical_bytes(logical)).hexdigest() == value["identity_sha256"])
    return identity


def _validate_document(document: dict, contract: _TestOnlyContract, candle_id: CandleId) -> None:
    def exact_counts(value):
        if type(value) is dict:
            for key, child in value.items():
                if key.endswith("count"):
                    _require(type(child) is int and child >= 0)
                exact_counts(child)
        elif type(value) is list:
            for child in value:
                exact_counts(child)
    exact_counts(document)
    _keys(document, "schema_version research_phase data_role development_interval scientific_lineage "
          "candle_bindings de1_range_bindings de1_minute_bindings alignment verification binding_id")
    _require(document["schema_version"] == SCHEMA)
    _require(document["research_phase"] == "BLINDED_INPUT_BINDING")
    _require(document["data_role"] == "DEVELOPMENT")
    _require(document["scientific_lineage"] == dict(LINEAGE))
    _require(document["development_interval"] == {
        "start": _time(contract.start), "end_exclusive": _time(contract.end),
        "timeframe": "1m", "day_count": contract.days})
    _require(document["alignment"] == {
        "common_minute_count": contract.minutes, "grid_count": 4,
        "total_source_partition_count": 2 * contract.days,
        "exact_same_minute_grid": True})
    _require(document["verification"] == {
        "canonical_sources_verified": True, "development_roles_verified": True,
        "exact_lineage_verified": True, "complete_minute_grids_verified": True})
    _require(all(type(value) is bool for value in document["verification"].values()))
    _require(type(document["alignment"]["exact_same_minute_grid"]) is bool)
    # Compare canonical encodings too: True must never impersonate integer 1.
    for key in ("development_interval", "alignment", "verification", "scientific_lineage"):
        _require(type(document[key]) is dict)
    for key in ("candle_bindings", "de1_range_bindings", "de1_minute_bindings"):
        _require(type(document[key]) is list and len(document[key]) == 2)
    for index, symbol in enumerate(SYMBOLS):
        candle = document["candle_bindings"][index]
        ci = _candle_identity(candle, candle_id)
        _require((ci.symbol, ci.timeframe, ci.start_time, ci.end_time) ==
                 (symbol, "1m", contract.start, contract.end - timedelta(minutes=1)))
        _require(type(candle["candle_count"]) is int and candle["candle_count"] == contract.minutes)
        rb = document["de1_range_bindings"][index]
        _keys(rb, "research_role manifest")
        _require(rb["research_role"] == "development")
        source_range = aggregate_trade_range_manifest_from_bytes(canonical_bytes(rb["manifest"]))
        _require(source_range.as_canonical_dict() == rb["manifest"])
        _require((source_range.symbol, source_range.requested_start_date,
                  source_range.requested_end_date_exclusive) ==
                 (symbol, contract.start.date(), contract.end.date()))
        for part in source_range.partitions:
            key = part.logical_partition
            _require((key.provider, key.market, key.event_family) == ("binance", "spot", "aggregate_trade"))
            _token(part.schema_version)
            _token(part.normalizer_version)
            first = datetime.combine(key.source_date, datetime.min.time(), tzinfo=timezone.utc)
            _require(first <= part.observed_first_event_time <= part.observed_last_event_time < first + timedelta(days=1))
            expected_unit = SourceTimestampUnit.MILLISECOND if key.source_date < date(2025, 1, 1) else SourceTimestampUnit.MICROSECOND
            _require(part.source_timestamp_unit is expected_unit)
        minute = document["de1_minute_bindings"][index]
        _keys(minute, "identity minute_state_dataset_id minute_state_content_sha256 minute_count source_partition_count")
        mi = _minute_identity(minute["identity"])
        _require((mi.symbol, mi.requested_start_time, mi.requested_end_time_exclusive,
                  mi.source_range_id) == (symbol, contract.start, contract.end, source_range.range_id))
        _require(mi.source_partitions == tuple(AggregateTradeMinuteSourceReference.from_partition_reference(p)
                                               for p in source_range.partitions))
        _require(type(minute["minute_count"]) is int and minute["minute_count"] == contract.minutes)
        _require(type(minute["source_partition_count"]) is int and minute["source_partition_count"] == contract.days)
        _require(aggregate_trade_minute_dataset_id(mi, _digest(minute["minute_state_content_sha256"]))
                 == _digest(minute["minute_state_dataset_id"]))
    expected = sha256(canonical_bytes({k: v for k, v in document.items() if k != "binding_id"})).hexdigest()
    _require(_digest(document["binding_id"]) == expected)


@dataclass(frozen=True, slots=True, init=False)
class DevelopmentBinding:
    """Deeply immutable canonical bytes; dictionary access returns a fresh copy."""
    content: bytes = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise BindingError("use the validated binding factory")

    @property
    def binding_id(self) -> str:
        return _decode(self.content)["binding_id"]

    def to_evaluator_identity(self) -> dict:
        """Future handoff only. The frozen evaluator has no real-data entrypoint."""
        doc = _decode(self.content)
        return {"binding_id": doc["binding_id"], "data_role": "DEVELOPMENT",
                "scientific_lineage": doc["scientific_lineage"],
                "candles": [{k: c[k] for k in ("symbol", "dataset_id", "content_sha256", "identity_sha256")}
                            for c in doc["candle_bindings"]],
                "minute_states": [{"symbol": m["identity"]["symbol"],
                                   "source_range_id": m["identity"]["source_range_id"],
                                   "dataset_id": m["minute_state_dataset_id"],
                                   "content_sha256": m["minute_state_content_sha256"]}
                                  for m in doc["de1_minute_bindings"]]}


def _load(content: bytes, contract: _TestOnlyContract, candle_id: CandleId) -> DevelopmentBinding:
    try:
        _validate_document(_decode(content), contract, candle_id)
        result = object.__new__(DevelopmentBinding)
        object.__setattr__(result, "content", content)
        return result
    except Exception:
        raise BindingError("binding document rejected") from None


def load_binding(content: bytes, *, candle_id: CandleId) -> DevelopmentBinding:
    """Always enforce the real frozen DEVELOPMENT contract."""
    return _load(content, _FROZEN, candle_id)


def validate_frozen_development(binding: DevelopmentBinding, *, candle_id: CandleId) -> None:
    load_binding(binding.content, candle_id=candle_id)


def _build(inputs: BindingInputs, contract: _TestOnlyContract, candle_id: CandleId) -> DevelopmentBinding:
    try:
        _require(type(inputs) is BindingInputs)
        for collection in (inputs.candles, inputs.candle_parquet_sha256, inputs.ranges,
                           inputs.source_manifests, inputs.minutes):
            _require(type(collection) is tuple and len(collection) == 2)
        candles, ranges, minutes = [], [], []
        for index, symbol in enumerate(SYMBOLS):
            sequence = inputs.candles[index]
            _require(type(sequence) is ValidatedCandleSequence)
            identity = sequence.identity
            _require(type(identity) is DatasetIdentity)
            _require((identity.symbol, identity.timeframe, identity.start_time, identity.end_time) ==
                     (symbol, "1m", contract.start, contract.end - timedelta(minutes=1)))
            _require(len(sequence.candles) == contract.minutes)
            ValidatedCandleSequence.__post_init__(sequence)
            # Revalidate constituent contracts, including forged frozen dataclasses.
            DatasetIdentity.__post_init__(identity)
            for candle in sequence.candles:
                type(candle).__post_init__(candle)
                _require(candle.open_time < candle.close_time < candle.open_time + timedelta(minutes=1))
            cb = {"symbol": identity.symbol, "timeframe": identity.timeframe,
                  "dataset_id": candle_id(identity),
                  "content_sha256": canonical_candle_content_sha256(sequence),
                  "start_open_time": _time(identity.start_time),
                  "end_open_time": _time(identity.end_time), "candle_count": len(sequence.candles),
                  "source": identity.source, "schema_version": identity.schema_version,
                  "ingestion_version": identity.ingestion_version,
                  "validation_status": identity.validation_status.value}
            cb["identity_sha256"] = sha256(canonical_bytes(cb)).hexdigest()
            cb["parquet_byte_sha256"] = _digest(inputs.candle_parquet_sha256[index])
            candles.append(cb)
            source_range = inputs.ranges[index]
            _require(type(source_range) is AggregateTradeRangeManifest)
            AggregateTradeRangeManifest.__post_init__(source_range)
            sources = inputs.source_manifests[index]
            _require(type(sources) is tuple and len(sources) == contract.days)
            for source, reference in zip(sources, source_range.partitions, strict=True):
                _require(type(source) is AggregateTradeArchiveManifest)
                AggregateTradeArchiveManifest.__post_init__(source)
                _require(source.research_role is ResearchDatasetRole.DEVELOPMENT)
                _require(AggregateTradePartitionReference.from_manifest(source) == reference)
            ranges.append({"research_role": "development", "manifest": source_range.as_canonical_dict()})
            dataset = inputs.minutes[index]
            _require(type(dataset) is ValidatedAggregateTradeMinuteDataset)
            _require((dataset.identity.symbol, dataset.identity.requested_start_time,
                      dataset.identity.requested_end_time_exclusive, dataset.identity.source_range_id) ==
                     (symbol, contract.start, contract.end, source_range.range_id))
            _require(len(dataset.states) == contract.minutes)
            ValidatedAggregateTradeMinuteDataset.__post_init__(dataset)
            digest = dataset.content_sha256  # Existing domain content algorithm only.
            minutes.append({"identity": dataset.identity.as_canonical_dict(),
                            "minute_state_dataset_id": aggregate_trade_minute_dataset_id(dataset.identity, digest),
                            "minute_state_content_sha256": digest, "minute_count": len(dataset.states),
                            "source_partition_count": len(dataset.identity.source_partitions)})
        document = {"schema_version": SCHEMA, "research_phase": "BLINDED_INPUT_BINDING",
                    "data_role": "DEVELOPMENT", "scientific_lineage": dict(LINEAGE),
                    "development_interval": {"start": _time(contract.start), "end_exclusive": _time(contract.end),
                                             "timeframe": "1m", "day_count": contract.days},
                    "candle_bindings": candles, "de1_range_bindings": ranges, "de1_minute_bindings": minutes,
                    "alignment": {"common_minute_count": contract.minutes, "grid_count": 4,
                                  "total_source_partition_count": 2 * contract.days, "exact_same_minute_grid": True},
                    "verification": {"canonical_sources_verified": True, "development_roles_verified": True,
                                     "exact_lineage_verified": True, "complete_minute_grids_verified": True}}
        document["binding_id"] = sha256(canonical_bytes(document)).hexdigest()
        return _load(canonical_bytes(document), contract, candle_id)
    except Exception:
        raise BindingError("binding inputs rejected") from None


def bind_development(inputs: BindingInputs, *, candle_id: CandleId) -> DevelopmentBinding:
    return _build(inputs, _FROZEN, candle_id)


def load_receipt(content: bytes) -> bytes:
    """Closed schema: no extensible diagnostic maps and no observation payloads."""
    try:
        doc = _decode(content)
        _keys(doc, "schema_version binding_id catalog symbols")
        _require(doc["schema_version"] == RECEIPT_SCHEMA)
        _digest(doc["binding_id"])
        _keys(doc["catalog"], "certificate_hit_partition_count certificate_miss_partition_count "
              "certificate_built_partition_count canonical_events_replayed certificate_fast_path_used")
        _require(type(doc["symbols"]) is list and len(doc["symbols"]) == 2)
        for index, item in enumerate(doc["symbols"]):
            _keys(item, "symbol o1_verified_range_available cache_hit_partition_count cache_miss_partition_count "
                  "cache_built_partition_count cached_minute_rows_loaded raw_events_consumed warm_cache_used")
            _require(item["symbol"] == SYMBOLS[index])
        for item in [doc["catalog"], *doc["symbols"]]:
            for key, value in item.items():
                if key == "symbol":
                    continue
                if key in ("certificate_fast_path_used", "o1_verified_range_available", "warm_cache_used"):
                    _require(type(value) is bool)
                else:
                    _require(type(value) is int and value >= 0)
        return content
    except Exception:
        raise BindingError("binding receipt rejected") from None
