"""Focused AF3B split and dataset-bound universe tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import unittest

from quantos.domain.evaluation.alpha_catalog import load_and_verify_fdr_plan, load_catalog_bytes
from quantos.domain.evaluation.alpha_funnel import (
    DEFAULT_HORIZONS_MINUTES,
    AlphaFunnelError,
    DatasetRole,
    ResearchDataset,
    canonical_json_bytes,
)
from quantos.domain.evaluation.alpha_hypotheses import (
    load_and_verify_materialization_manifest,
    materialize_hypotheses,
)
from quantos.domain.evaluation.alpha_universe import (
    AlphaUniverseError,
    DEVELOPMENT_END_EXCLUSIVE,
    EXPECTED_FDR_TEST_COUNT,
    EXPECTED_SPLIT_DECLARATION_ID,
    SCREENING_VALIDATION_END_EXCLUSIVE,
    build_af3b_universe,
    build_development_datasets,
    derive_universe_artifact,
    load_and_verify_split,
    load_and_verify_universe_artifact,
    verify_af2a_to_af1_mapping,
)
from quantos.domain.market_data import Candle, DatasetIdentity, validate_candle_sequence


ROOT = Path(__file__).resolve().parents[2]
AF2A = ROOT / "research" / "alpha-funnel" / "af2a"
AF3A = ROOT / "research" / "alpha-funnel" / "af3a"
AF3B = ROOT / "research" / "alpha-funnel" / "af3b"
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


def source(symbol: str, count: int = 3):
    identity = DatasetIdentity(
        symbol=symbol,
        timeframe="1m",
        start_time=START,
        end_time=START + (count - 1) * MINUTE,
        source="unit-test",
        schema_version="candle-v1",
        ingestion_version="af3b-unit-v1",
    )
    candles = tuple(
        Candle(
            symbol=symbol,
            interval="1m",
            open_time=START + index * MINUTE,
            close_time=START + index * MINUTE + timedelta(seconds=59),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
            quote_volume=Decimal("100"),
            trade_count=10,
        )
        for index in range(count)
    )
    return validate_candle_sequence(identity, candles)


def small_split() -> dict[str, object]:
    return {
        "split_declaration_id": "unit-split",
        "development_start": START.isoformat(timespec="microseconds"),
        "development_end_exclusive": (START + 3 * MINUTE).isoformat(timespec="microseconds"),
    }


class AlphaUniverseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog_payload = (AF2A / "catalog.json").read_bytes()
        cls.plan_payload = (AF2A / "fdr_plan.json").read_bytes()
        cls.manifest_payload = (AF3A / "materialization.json").read_bytes()
        cls.catalog = load_catalog_bytes(cls.catalog_payload)
        cls.plan = load_and_verify_fdr_plan(cls.catalog_payload, cls.plan_payload)
        cls.manifest = load_and_verify_materialization_manifest(
            cls.catalog_payload, cls.plan_payload, cls.manifest_payload
        )
        cls.hypotheses = materialize_hypotheses(cls.catalog, cls.plan)

    def setUp(self) -> None:
        self.datasets = build_development_datasets(
            small_split(), (source("BTCUSDT"), source("ETHUSDT"))
        )
        self.universe = build_af3b_universe(self.datasets, self.hypotheses)

    def test_split_artifact_is_canonical_and_round_trips(self) -> None:
        payload = (AF3B / "split.json").read_bytes()
        split = load_and_verify_split(payload)
        self.assertEqual(payload, canonical_json_bytes(split))
        self.assertEqual(split["split_declaration_id"], EXPECTED_SPLIT_DECLARATION_ID)

    def test_split_ranges_are_exact_non_overlapping_and_future_only(self) -> None:
        split = load_and_verify_split((AF3B / "split.json").read_bytes())
        self.assertEqual(split["screening_validation_start"], split["development_end_exclusive"])
        self.assertEqual(split["sealed_oos_start"], split["screening_validation_end_exclusive"])
        self.assertIsNone(split["sealed_oos_end_exclusive"])
        self.assertEqual(
            SCREENING_VALIDATION_END_EXCLUSIVE - DEVELOPMENT_END_EXCLUSIVE,
            timedelta(days=60),
        )

    def test_newer_source_does_not_expand_frozen_development(self) -> None:
        datasets = build_development_datasets(
            small_split(), (source("BTCUSDT", 5), source("ETHUSDT", 5))
        )
        self.assertEqual([len(item.sequence.candles) for item in datasets], [3, 3])
        self.assertEqual(
            [item.sequence.identity.end_time for item in datasets],
            [START + 2 * MINUTE, START + 2 * MINUTE],
        )

    def test_exactly_two_development_datasets_bind(self) -> None:
        self.assertEqual(
            [item.sequence.identity.symbol for item in self.datasets],
            ["BTCUSDT", "ETHUSDT"],
        )
        self.assertTrue(all(item.role is DatasetRole.DEVELOPMENT for item in self.datasets))

    def test_wrong_content_hash_is_rejected(self) -> None:
        dataset = self.datasets[0]
        with self.assertRaisesRegex(AlphaFunnelError, "content_sha256"):
            ResearchDataset(dataset.dataset_id, "0" * 64, dataset.role, dataset.sequence)

    def test_universe_has_exact_616_keys_and_308_per_symbol(self) -> None:
        self.assertEqual(len(self.universe.tests), EXPECTED_FDR_TEST_COUNT)
        self.assertEqual(sum(item.symbol == "BTCUSDT" for item in self.universe.tests), 308)
        self.assertEqual(sum(item.symbol == "ETHUSDT" for item in self.universe.tests), 308)

    def test_universe_has_exact_fixed_horizons(self) -> None:
        self.assertEqual(
            {item.horizon_minutes for item in self.universe.tests},
            set(DEFAULT_HORIZONS_MINUTES),
        )

    def test_af2a_registrations_map_one_to_one(self) -> None:
        verify_af2a_to_af1_mapping(self.plan, self.manifest, self.universe)

    def test_universe_artifact_round_trip_and_tampering_fail(self) -> None:
        expected = derive_universe_artifact(
            small_split(), self.datasets, self.hypotheses, self.universe
        )
        payload = canonical_json_bytes(expected)
        self.assertEqual(load_and_verify_universe_artifact(payload, expected), expected)
        tampered = deepcopy(expected)
        tampered["fdr_test_count"] = 615
        identity = dict(tampered)
        identity.pop("universe_artifact_id")
        tampered["universe_artifact_id"] = sha256(canonical_json_bytes(identity)).hexdigest()
        with self.assertRaisesRegex(AlphaUniverseError, "differs"):
            load_and_verify_universe_artifact(canonical_json_bytes(tampered), expected)

    def test_noncanonical_or_digest_tampered_artifact_fails(self) -> None:
        expected = derive_universe_artifact(
            small_split(), self.datasets, self.hypotheses, self.universe
        )
        pretty = (json.dumps(expected, indent=2) + "\n").encode()
        with self.assertRaisesRegex(AlphaUniverseError, "canonical JSON"):
            load_and_verify_universe_artifact(pretty, expected)
        damaged = dict(expected)
        damaged["universe_artifact_id"] = "0" * 64
        with self.assertRaisesRegex(AlphaUniverseError, "identity mismatch"):
            load_and_verify_universe_artifact(canonical_json_bytes(damaged), expected)

    def test_no_validation_or_sealed_role_enters_universe(self) -> None:
        self.assertEqual(
            {item.dataset_role for item in self.universe.tests},
            {DatasetRole.DEVELOPMENT},
        )
        for role in (DatasetRole.SCREENING_VALIDATION, DatasetRole.SEALED_OOS):
            changed = (replace(self.datasets[0], role=role), self.datasets[1])
            with self.subTest(role=role), self.assertRaisesRegex(
                AlphaUniverseError, "DEVELOPMENT"
            ):
                build_af3b_universe(changed, self.hypotheses)


if __name__ == "__main__":
    unittest.main()
