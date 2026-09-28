"""Synthetic, temporary-only proofs of the blinded binding contract."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.application.af4c_development_binding import (
    BindingError, BindingInputs, DevelopmentBinding, END, LINEAGE, START, SYMBOLS,
    _FROZEN, _TestOnlyContract, _build, _load, _minute_identity,
    bind_development, canonical_bytes, load_binding, load_receipt,
)
from quantos.application.aggregate_trade_minute_states import aggregate_trade_minute_states
from quantos.application.aggregate_trade_ranges import compose_aggregate_trade_range
from quantos.domain.evaluation.alpha_funnel import canonical_candle_content_sha256
from quantos.domain.market_data.research_events import (
    AggregateTradeRangeRequest, ResearchDatasetRole, RevisionSelectionPolicy,
    aggregate_trade_minute_dataset_id,
)
from quantos.infrastructure.storage.af4c_binding import _preflight
from quantos.infrastructure.storage.aggregate_trade_minute_primitive_cache import ParquetAggregateTradeMinutePrimitiveCache
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore, dataset_id
from tests.aggregate_trade_range_fixtures import local_catalog, publish_partition
from tests.unit.test_parquet_store import sample_sequence

MINI = _TestOnlyContract(datetime(2024, 12, 31, tzinfo=timezone.utc),
                         datetime(2025, 1, 2, tzinfo=timezone.utc))


def fixture(root):
    """Existing archive helper uses an injected in-memory HTTP fake only."""
    source = root / "sources"
    cache = ParquetAggregateTradeMinutePrimitiveCache(root / "primitives")
    paths, sources = [], []
    store = ParquetCandleDatasetStore(root / "candles")
    for symbol in SYMBOLS:
        paths.append(store.write(sample_sequence(symbol=symbol, start=MINI.start, count=MINI.minutes)))
        manifests = []
        for index in range(MINI.days):
            fetched, _ = publish_partition(source, symbol=symbol,
                source_date=(MINI.start + timedelta(days=index)).date(), first_id=10 + index * 10)
            manifests.append(fetched.archive.manifest)
        sources.append(tuple(manifests))
    return tuple(paths), source, cache, tuple(sources)


def rehash(doc):
    doc["binding_id"] = sha256(canonical_bytes({k: v for k, v in doc.items() if k != "binding_id"})).hexdigest()
    return canonical_bytes(doc)


def nested_keys(value):
    if type(value) is dict:
        for key, child in value.items():
            yield key
            yield from nested_keys(child)
    elif type(value) is list:
        for child in value:
            yield from nested_keys(child)


FORBIDDEN_KEYS = {
    "open", "high", "low", "close", "price", "quantity", "volume", "quote_volume",
    "trade_count", "total_base_quantity", "total_quote_notional", "base", "quote",
    "aggressive_buy_quote_notional", "aggressive_sell_quote_notional", "vwap",
    "imbalance", "displacement", "returns", "statistics", "classification",
    "signal_count", "eligible_signal_count", "p_value", "q_value", "rvs", "score",
}


class BindingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory(prefix="af4c-binding-contract-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.paths, cls.source, cls.cache, cls.sources = fixture(Path(cls.temporary.name))
        cls.binding, cls.receipt = _preflight(cls.paths, cls.source, cls.cache, None, MINI)
        catalog = local_catalog(cls.source)
        catalog.rebuild()
        ranges = tuple(compose_aggregate_trade_range(catalog, AggregateTradeRangeRequest(
            symbol, MINI.start.date(), MINI.end.date(), RevisionSelectionPolicy.UNIQUE)) for symbol in SYMBOLS)
        cls.inputs = BindingInputs(
            tuple(ParquetCandleDatasetStore(p.parent).read(p) for p in cls.paths),
            tuple(sha256(p.read_bytes()).hexdigest() for p in cls.paths), ranges, cls.sources,
            tuple(aggregate_trade_minute_states(catalog, r, primitive_cache=cls.cache) for r in ranges))

    def test_build_load_and_exact_id_algorithm(self):
        built = _build(self.inputs, MINI, dataset_id)
        self.assertEqual(built.content, self.binding.content)
        self.assertEqual(_load(built.content, MINI, dataset_id), built)
        doc = json.loads(built.content)
        identity = doc.pop("binding_id")
        self.assertEqual(identity, sha256(canonical_bytes(doc)).hexdigest())
        self.assertTrue(built.content.endswith(b"\n"))
        self.assertFalse(built.content.endswith(b"\n\n"))

    def test_real_api_rejects_miniature_without_override(self):
        with self.assertRaises(BindingError):
            bind_development(self.inputs, candle_id=dataset_id)
        with self.assertRaises(BindingError):
            load_binding(self.binding.content, candle_id=dataset_id)
        self.assertEqual((_FROZEN.start, _FROZEN.end, _FROZEN.days, _FROZEN.minutes),
                         (START, END, 639, 920160))
        self.assertEqual(2 * _FROZEN.days, 1278)

    def test_candle_id_content_and_physical_hash_are_distinct(self):
        doc = json.loads(self.binding.content)
        for index, item in enumerate(doc["candle_bindings"]):
            sequence = self.inputs.candles[index]
            self.assertEqual(item["dataset_id"], dataset_id(sequence.identity))
            self.assertEqual(item["content_sha256"], canonical_candle_content_sha256(sequence))
            self.assertEqual(item["parquet_byte_sha256"], sha256(self.paths[index].read_bytes()).hexdigest())
            self.assertNotEqual(item["dataset_id"], item["content_sha256"])

    def test_alignment_and_exact_minute_identity(self):
        doc = json.loads(self.binding.content)
        self.assertEqual(doc["alignment"]["common_minute_count"], MINI.minutes)
        for index, item in enumerate(doc["de1_minute_bindings"]):
            dataset = self.inputs.minutes[index]
            self.assertEqual(item["minute_state_dataset_id"], dataset.dataset_id)
            self.assertEqual(item["minute_state_content_sha256"], dataset.content_sha256)
            self.assertEqual(item["identity"], dataset.identity.as_canonical_dict())

    def test_role_is_not_inferred_from_dates(self):
        changed = replace(self.sources[0][0], research_role=ResearchDatasetRole.SCREENING_VALIDATION)
        bad = replace(self.inputs, source_manifests=((changed, self.sources[0][1]), self.sources[1]))
        with self.assertRaises(BindingError):
            _build(bad, MINI, dataset_id)

    def test_pair_missing_duplicate_reordered_and_extra(self):
        for attr in ("candles", "ranges", "minutes", "source_manifests"):
            items = getattr(self.inputs, attr)
            for replacement in ((items[0],), (items[0], items[0]), items[::-1], (*items, items[0])):
                with self.subTest(collection=attr, length=len(replacement)), self.assertRaises(BindingError):
                    _build(replace(self.inputs, **{attr: replacement}), MINI, dataset_id)

    def test_gap_duplicate_reorder_truncation_and_extra_candles(self):
        sequence = self.inputs.candles[0]
        for rows in (sequence.candles[:-1], sequence.candles[1:], sequence.candles[::-1],
                     sequence.candles[:1] + sequence.candles, sequence.candles[:8] + sequence.candles[9:]):
            bad = deepcopy(sequence)
            object.__setattr__(bad, "candles", rows)
            with self.assertRaises(BindingError):
                _build(replace(self.inputs, candles=(bad, self.inputs.candles[1])), MINI, dataset_id)
        longer = sample_sequence(start=MINI.start, count=MINI.minutes + 1)
        with self.assertRaises(BindingError):
            _build(replace(self.inputs, candles=(longer, self.inputs.candles[1])), MINI, dataset_id)

    def test_wrong_timeframe_and_symbol(self):
        for field, value in (("timeframe", "5m"), ("symbol", "ETHUSDT")):
            bad = deepcopy(self.inputs.candles[0])
            object.__setattr__(bad.identity, field, value)
            with self.assertRaises(BindingError):
                _build(replace(self.inputs, candles=(bad, self.inputs.candles[1])), MINI, dataset_id)

    def test_minute_mutations_fail(self):
        dataset = self.inputs.minutes[0]
        for rows in (dataset.states[:-1], dataset.states[::-1], dataset.states[:1] + dataset.states):
            bad = deepcopy(dataset)
            object.__setattr__(bad, "states", rows)
            with self.assertRaises(BindingError):
                _build(replace(self.inputs, minutes=(bad, self.inputs.minutes[1])), MINI, dataset_id)
        bad = deepcopy(dataset)
        object.__setattr__(bad.identity, "source_range_id", "0" * 64)
        with self.assertRaises(BindingError):
            _build(replace(self.inputs, minutes=(bad, self.inputs.minutes[1])), MINI, dataset_id)

    def test_range_lineage_mutation(self):
        bad = deepcopy(self.inputs.ranges[0])
        object.__setattr__(bad.partitions[0], "canonical_sequence_sha256", "0" * 64)
        with self.assertRaises(BindingError):
            _build(replace(self.inputs, ranges=(bad, self.inputs.ranges[1])), MINI, dataset_id)

    def test_manifest_mutations_even_after_outer_rehash(self):
        changes = [
            (("scientific_lineage", "catalog_id"), "0" * 64),
            (("scientific_lineage", "fdr_family_id"), "0" * 64),
            (("scientific_lineage", "evaluator_commit"), "0" * 40),
            (("development_interval", "end_exclusive"), "2025-10-01T00:00:00.000000Z"),
            (("development_interval", "day_count"), True),
            (("candle_bindings", 0, "symbol"), "ETHUSDT"),
            (("candle_bindings", 0, "candle_count"), MINI.minutes - 1),
            (("candle_bindings", 0, "dataset_id"), "0" * 64),
            (("candle_bindings", 0, "content_sha256"), "0" * 64),
            (("candle_bindings", 0, "parquet_byte_sha256"), "X" * 64),
            (("de1_range_bindings", 0, "manifest", "selected_partition_count"), 1),
            (("de1_range_bindings", 0, "research_role"), "sealed_oos"),
            (("de1_minute_bindings", 0, "minute_count"), MINI.minutes - 1),
            (("de1_minute_bindings", 0, "minute_state_content_sha256"), "0" * 64),
            (("de1_minute_bindings", 0, "identity", "source_range_id"), "0" * 64),
        ]
        for route, value in changes:
            doc = json.loads(self.binding.content)
            target = doc
            for key in route[:-1]:
                target = target[key]
            target[route[-1]] = value
            with self.subTest(route=route), self.assertRaises(BindingError):
                _load(rehash(doc), MINI, dataset_id)

    def test_duplicate_noncanonical_and_wrong_binding_id(self):
        content = self.binding.content
        doc = json.loads(content)
        doc["binding_id"] = "0" * 64
        for bad in (b" " + content, content.rstrip(), content.replace(b"{", b'{"binding_id":"duplicate",', 1),
                    canonical_bytes(doc), content.replace(b'"day_count":2', b'"day_count":2.0')):
            with self.assertRaises(BindingError):
                _load(bad, MINI, dataset_id)

    def test_all_nested_objects_reject_unknown_fields(self):
        def routes(value, route=()):
            if type(value) is dict:
                yield route
                for key, child in value.items():
                    yield from routes(child, (*route, key))
            elif type(value) is list:
                for index, child in enumerate(value):
                    yield from routes(child, (*route, index))
        original = json.loads(self.binding.content)
        for route in routes(original):
            doc = deepcopy(original)
            target = doc
            for key in route:
                target = target[key]
            target["price"] = "REDACTED_SENTINEL"
            with self.subTest(route=route), self.assertRaises(BindingError):
                _load(rehash(doc), MINI, dataset_id)

    def test_blindness_and_handoff(self):
        for value in (json.loads(self.binding.content), json.loads(self.receipt), self.binding.to_evaluator_identity()):
            self.assertFalse(set(nested_keys(value)) & FORBIDDEN_KEYS)
        metadata = self.binding.to_evaluator_identity()
        self.assertEqual(metadata["binding_id"], self.binding.binding_id)
        self.assertEqual(len(metadata["candles"]), 2)
        self.assertEqual(len(metadata["minute_states"]), 2)
        self.assertEqual(metadata["scientific_lineage"], dict(LINEAGE))
        self.assertNotIn("synthetic", repr(self.binding))

    def test_receipt_closed_schema_types_and_no_predictive_keys(self):
        for route in ((), ("catalog",), ("symbols", 0), ("symbols", 1)):
            for key in FORBIDDEN_KEYS:
                doc = json.loads(self.receipt)
                target = doc
                for part in route:
                    target = target[part]
                target[key] = 1
                with self.assertRaises(BindingError):
                    load_receipt(canonical_bytes(doc))
        doc = json.loads(self.receipt)
        doc["symbols"][0]["raw_events_consumed"] = True
        with self.assertRaises(BindingError):
            load_receipt(canonical_bytes(doc))

    def test_constructor_cannot_bypass_validated_factory(self):
        with self.assertRaises(BindingError):
            DevelopmentBinding(b'{"price":1}\n')

    def test_frozen_real_manifest_identity_only_validator(self):
        # No observations and no filesystem artifact: exercise 639 identity refs
        # using invented synthetic digests, never claiming verified real inputs.
        doc = json.loads(self.binding.content)
        doc["development_interval"] = {"start": "2024-01-01T00:00:00.000000Z",
            "end_exclusive": "2025-10-01T00:00:00.000000Z", "timeframe": "1m", "day_count": 639}
        doc["alignment"].update(common_minute_count=920160, total_source_partition_count=1278)
        for index, symbol in enumerate(SYMBOLS):
            candle = doc["candle_bindings"][index]
            sequence_identity = replace(self.inputs.candles[index].identity, start_time=START,
                                        end_time=END - timedelta(minutes=1))._validated_copy()
            candle.update(start_open_time=doc["development_interval"]["start"],
                          end_open_time="2025-09-30T23:59:00.000000Z", candle_count=920160,
                          dataset_id=dataset_id(sequence_identity))
            candle["identity_sha256"] = sha256(canonical_bytes({k: v for k, v in candle.items()
                if k not in ("identity_sha256", "parquet_byte_sha256")})).hexdigest()
            manifest = doc["de1_range_bindings"][index]["manifest"]
            original_part = deepcopy(manifest["partitions"][0])
            original_boundary = deepcopy(manifest["boundaries"][0])
            parts, boundaries = [], []
            for day in range(639):
                now = START + timedelta(days=day)
                part = deepcopy(original_part)
                part["logical_partition"]["source_date"] = now.date().isoformat()
                part["observed_first_event_time"] = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
                part["observed_last_event_time"] = (now + timedelta(microseconds=1)).isoformat(timespec="microseconds").replace("+00:00", "Z")
                part["source_timestamp_unit"] = "millisecond" if now.year == 2024 else "microsecond"
                parts.append(part)
                if day:
                    boundary = deepcopy(original_boundary)
                    boundary.update(left_source_date=parts[-2]["logical_partition"]["source_date"],
                        right_source_date=part["logical_partition"]["source_date"],
                        left_last_event_time=parts[-2]["observed_last_event_time"],
                        right_first_event_time=part["observed_first_event_time"])
                    boundaries.append(boundary)
            manifest.update(partitions=parts, boundaries=boundaries, expected_partition_count=639,
                selected_partition_count=639, total_accepted_event_count=sum(p["accepted_row_count"] for p in parts),
                requested_start_date=START.date().isoformat(), requested_end_date_exclusive=END.date().isoformat(),
                observed_first_event_time=parts[0]["observed_first_event_time"], observed_last_event_time=parts[-1]["observed_last_event_time"])
            manifest["range_id"] = sha256(canonical_bytes({k: v for k, v in manifest.items() if k != "range_id"})).hexdigest()
            minute = doc["de1_minute_bindings"][index]
            mi = minute["identity"]
            mi.update(source_range_id=manifest["range_id"], requested_start_time=doc["development_interval"]["start"],
                      requested_end_time_exclusive=doc["development_interval"]["end_exclusive"],
                      source_partitions=[{"source_date": p["logical_partition"]["source_date"],
                          **{k: p[k] for k in ("manifest_id", "source_revision_id", "dataset_id", "canonical_sequence_sha256", "source_timestamp_unit")}}
                          for p in parts])
            minute.update(minute_count=920160, source_partition_count=639,
                minute_state_dataset_id=aggregate_trade_minute_dataset_id(_minute_identity(mi), minute["minute_state_content_sha256"]))
        valid = load_binding(rehash(doc), candle_id=dataset_id)
        self.assertEqual(json.loads(valid.content)["alignment"]["common_minute_count"], 920160)
        for key, value in (("common_minute_count", 920159), ("total_source_partition_count", 1277)):
            changed = deepcopy(doc)
            changed["alignment"][key] = value
            with self.assertRaises(BindingError):
                load_binding(rehash(changed), candle_id=dataset_id)
