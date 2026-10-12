"""Offline immutable publication, collision, and filesystem failure tests."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from quantos.domain.market_data import ValidatedCandleSequence
from quantos.infrastructure.storage import (
    DatasetCollisionError,
    ParquetCandleDatasetStore,
    ParquetStorageError,
)
from tests.unit.test_parquet_store import sample_sequence

MODULE = "quantos.infrastructure.storage.parquet"


class ParquetPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ParquetCandleDatasetStore(self.root)
        self.sequence = sample_sequence()
        self.destination = self.store.dataset_path(self.sequence.identity)

    def files(self) -> list[Path]:
        return [path for path in self.root.rglob("*") if path.is_file()]

    def different_sequence(self) -> ValidatedCandleSequence:
        return ValidatedCandleSequence(
            self.sequence.identity,
            (replace(self.sequence.candles[0], volume=Decimal("2")),)
            + self.sequence.candles[1:],
        )

    def test_first_publication_links_only_a_verified_complete_temporary_file(self) -> None:
        real_link = os.link
        real_fsync = os.fsync
        events = []

        def sync(fd: int) -> None:
            real_fsync(fd)
            events.append("sync")

        def publish(source: Path, destination: Path) -> None:
            self.assertEqual(events, ["sync"])
            self.assertEqual(source.parent, destination.parent)
            self.assertEqual(source.suffix, ".tmp")
            self.assertNotIn(source, list(self.root.rglob("*.parquet")))
            self.assertFalse(destination.exists())
            self.assertEqual(self.store.read(source), self.sequence)
            real_link(source, destination)
            self.assertEqual(source.stat().st_ino, destination.stat().st_ino)
            events.append("publish")

        with patch(f"{MODULE}.os.fsync", side_effect=sync):
            with patch(f"{MODULE}.os.link", side_effect=publish) as publication:
                path = self.store.write(self.sequence)

        publication.assert_called_once()
        self.assertEqual(events, ["sync", "publish"])
        self.assertEqual(path, self.destination)
        self.assertEqual(self.store.read(path), self.sequence)
        self.assertEqual(self.files(), [path])

    def test_identical_second_write_does_not_rewrite_or_change_mtime(self) -> None:
        path = self.store.write(self.sequence)
        before = path.read_bytes(), path.stat().st_mtime_ns

        with patch(f"{MODULE}.pq.write_table", side_effect=AssertionError("unexpected rewrite")):
            with patch(f"{MODULE}.os.link", side_effect=AssertionError("unexpected publication")):
                self.assertEqual(self.store.write(self.sequence), path)

        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(self.files(), [path])

    def test_different_content_is_a_specific_collision_and_preserves_existing_file(self) -> None:
        path = self.store.write(self.sequence)
        before = path.read_bytes(), path.stat().st_mtime_ns

        with self.assertRaises(DatasetCollisionError):
            self.store.write(self.different_sequence())

        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(self.store.read(path), self.sequence)
        self.assertEqual(self.files(), [path])

    def test_corrupt_existing_destination_is_never_replaced(self) -> None:
        self.destination.parent.mkdir(parents=True)
        self.destination.write_bytes(b"PAR1 broken existing dataset PAR1")
        before = self.destination.read_bytes(), self.destination.stat().st_mtime_ns

        with self.assertRaises(ParquetStorageError):
            self.store.write(self.sequence)

        self.assertEqual((self.destination.read_bytes(), self.destination.stat().st_mtime_ns), before)
        self.assertEqual(self.files(), [self.destination])

    def test_unvalidated_inputs_are_rejected_without_files(self) -> None:
        for value in (None, self.sequence.identity, self.sequence.candles, list(self.sequence.candles)):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ParquetStorageError):
                    self.store.write(value)
                self.assertEqual(self.files(), [])

    def test_write_rechecks_forged_identity_boundaries_before_filesystem_writes(self) -> None:
        forged = replace(self.sequence)
        object.__setattr__(forged, "identity", sample_sequence(count=4).identity)

        with self.assertRaisesRegex(ParquetStorageError, "end_time"):
            self.store.write(forged)

        self.assertEqual(self.files(), [])

    def test_partial_parquet_write_failure_cleans_temporary_file(self) -> None:
        def partial_write(table: object, target: object, **kwargs: object) -> None:
            target.write(b"partial unpublished content")
            raise OSError("injected write failure")

        with patch(f"{MODULE}.pq.write_table", side_effect=partial_write):
            with self.assertRaisesRegex(ParquetStorageError, "injected write failure"):
                self.store.write(self.sequence)

        self.assertEqual(self.files(), [])

    def test_flush_failure_cleans_temporary_file_without_publication(self) -> None:
        with patch(f"{MODULE}.os.fsync", side_effect=OSError("injected flush failure")):
            with self.assertRaisesRegex(ParquetStorageError, "flush failure"):
                self.store.write(self.sequence)

        self.assertEqual(self.files(), [])

    def test_read_back_validation_failure_prevents_publication_and_cleans_temp(self) -> None:
        with patch.object(self.store, "read", side_effect=ParquetStorageError("read-back invalid")):
            with patch(f"{MODULE}.os.link") as publication:
                with self.assertRaisesRegex(ParquetStorageError, "read-back invalid"):
                    self.store.write(self.sequence)

        publication.assert_not_called()
        self.assertEqual(self.files(), [])

    def test_read_back_logical_mismatch_prevents_publication_and_cleans_temp(self) -> None:
        with patch.object(self.store, "read", return_value=self.different_sequence()):
            with patch(f"{MODULE}.os.link") as publication:
                with self.assertRaisesRegex(ParquetStorageError, "read-back differs"):
                    self.store.write(self.sequence)

        publication.assert_not_called()
        self.assertEqual(self.files(), [])

    def test_hard_link_failure_has_no_fallback_or_canonical_file(self) -> None:
        with patch(f"{MODULE}.os.link", side_effect=OSError("hard links unsupported")):
            with patch(f"{MODULE}.os.replace", side_effect=AssertionError("overwrite forbidden")):
                with self.assertRaisesRegex(ParquetStorageError, "hard links unsupported"):
                    self.store.write(self.sequence)

        self.assertEqual(self.files(), [])

    def test_race_with_identical_published_destination_is_idempotent(self) -> None:
        real_link = os.link
        published = []

        def competing_publication(source: Path, destination: Path) -> None:
            real_link(source, destination)
            published.append((destination.read_bytes(), destination.stat().st_mtime_ns))
            raise FileExistsError("another writer published first")

        with patch(f"{MODULE}.os.link", side_effect=competing_publication):
            path = self.store.write(self.sequence)

        self.assertEqual(path, self.destination)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), published[0])
        self.assertEqual(self.files(), [path])

    def test_race_with_different_content_preserves_winner_and_raises_collision(self) -> None:
        other_store = ParquetCandleDatasetStore(self.root / "other-writer")
        winner = other_store.write(self.different_sequence())
        winner_bytes = winner.read_bytes()
        real_link = os.link

        def competing_publication(source: Path, destination: Path) -> None:
            real_link(winner, destination)
            raise FileExistsError("another writer published first")

        with patch(f"{MODULE}.os.link", side_effect=competing_publication):
            with self.assertRaises(DatasetCollisionError):
                self.store.write(self.sequence)

        self.assertEqual(self.destination.read_bytes(), winner_bytes)
        self.assertEqual(self.store.read(self.destination), self.different_sequence())
        self.assertEqual(set(self.files()), {winner, self.destination})

    def test_race_with_corrupt_destination_never_replaces_it(self) -> None:
        def competing_publication(source: Path, destination: Path) -> None:
            destination.write_bytes(b"corrupt competing file")
            raise FileExistsError("another writer published first")

        with patch(f"{MODULE}.os.link", side_effect=competing_publication):
            with self.assertRaises(ParquetStorageError):
                self.store.write(self.sequence)

        self.assertEqual(self.destination.read_bytes(), b"corrupt competing file")
        self.assertEqual(self.files(), [self.destination])

    def test_two_actual_concurrent_writers_publish_one_complete_identical_dataset(self) -> None:
        barrier = Barrier(2)
        real_link = os.link

        def synchronized_link(source: Path, destination: Path) -> None:
            barrier.wait(timeout=10)
            real_link(source, destination)

        with patch(f"{MODULE}.os.link", side_effect=synchronized_link):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(ParquetCandleDatasetStore(self.root).write, self.sequence)
                    for _ in range(2)
                ]
                results = [future.result(timeout=15) for future in futures]

        self.assertEqual(results, [self.destination, self.destination])
        self.assertEqual(self.store.read(self.destination), self.sequence)
        self.assertEqual(self.files(), [self.destination])

    def test_cleanup_failure_after_publication_is_reported_without_removing_canonical_file(self) -> None:
        real_unlink = Path.unlink

        def fail_temp_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path.suffix == ".tmp":
                raise PermissionError("injected temporary cleanup failure")
            real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=fail_temp_unlink):
            with self.assertRaisesRegex(ParquetStorageError, "cannot remove temporary"):
                self.store.write(self.sequence)

        self.assertEqual(self.store.read(self.destination), self.sequence)
        temporary_paths = list(self.root.rglob("*.tmp"))
        self.assertEqual(len(temporary_paths), 1)
        for path in temporary_paths:
            path.unlink()
        self.assertEqual(self.files(), [self.destination])

    def test_cleanup_failure_does_not_mask_original_publication_failure(self) -> None:
        with patch(f"{MODULE}.os.link", side_effect=OSError("primary publication failure")):
            with patch.object(Path, "unlink", side_effect=PermissionError("cleanup also failed")):
                with self.assertRaisesRegex(ParquetStorageError, "primary publication failure") as captured:
                    self.store.write(self.sequence)

        self.assertIn("temporary cleanup also failed", captured.exception.__notes__[0])
        self.assertFalse(self.destination.exists())
        for path in self.root.rglob("*.tmp"):
            path.unlink()
        self.assertEqual(self.files(), [])
