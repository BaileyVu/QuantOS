"""Real native model round trips and adversarial immutable publication."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from quantos.infrastructure.models import (
    ModelArtifact, ModelArtifactError, ModelArtifactStore, load_model, train_model,
    MODEL_ARTIFACT_SCHEMA_VERSION, MODEL_FAMILY_VERSION,
)
from quantos.infrastructure.models.model import canonical_json
from tests.model_fixtures import split, config

MODULE = "quantos.infrastructure.models.artifacts"


def reidentify(metadata):
    core = {key: value for key, value in metadata.items() if key not in ("artifact_id", "model_version")}
    artifact_id = sha256(canonical_json(core)).hexdigest()
    return {**core, "artifact_id": artifact_id, "model_version": f"{MODEL_FAMILY_VERSION}:{artifact_id}"}


class ModelArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.split = split()
        cls.artifact = train_model(cls.split, config(), code_version="artifact-tests")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quantos-model-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = ModelArtifactStore(self.root)

    def put(self, model_bytes=None, metadata=None):
        model_bytes = self.artifact.model_bytes if model_bytes is None else model_bytes
        metadata = self.artifact.metadata if metadata is None else metadata
        path = self.root / "models" / MODEL_ARTIFACT_SCHEMA_VERSION / metadata["artifact_id"]
        path.mkdir(parents=True, exist_ok=True)
        (path / "model.txt").write_bytes(model_bytes)
        (path / "metadata.json").write_bytes(canonical_json(metadata))
        return path

    def test_roundtrip_exact_predictions_for_both_symbols(self):
        before = self.artifact.load()
        path = self.store.write(self.artifact)
        after = load_model(path)
        for row in self.split.validation[:6]:
            self.assertEqual(before.predict(row.feature).as_tuple(), after.predict(row.feature).as_tuple())
        self.assertEqual(after.model_version, self.artifact.model_version)

    def test_identity_hash_and_canonical_path(self):
        metadata = self.artifact.metadata
        self.assertEqual(metadata, reidentify(metadata))
        self.assertEqual(metadata["model_sha256"], sha256(self.artifact.model_bytes).hexdigest())
        path = self.store.write(self.artifact)
        self.assertEqual(path, self.root / "models" / "lightgbm-artifact-v1" / self.artifact.artifact_id)
        self.assertEqual((path / "model.txt").read_bytes(), self.artifact.model_bytes)
        self.assertEqual((path / "metadata.json").read_bytes(), self.artifact.metadata_bytes)
        self.assertEqual({child.name for child in path.iterdir()}, {"model.txt", "metadata.json"})

    def test_idempotent_existing_artifact_does_not_rewrite(self):
        path = self.store.write(self.artifact)
        before = {child.name: (child.read_bytes(), child.stat().st_mtime_ns) for child in path.iterdir()}
        with patch(f"{MODULE}._publish_no_replace", side_effect=AssertionError("must not republish")):
            self.assertEqual(self.store.write(self.artifact), path)
        self.assertEqual(before, {child.name: (child.read_bytes(), child.stat().st_mtime_ns) for child in path.iterdir()})

    def test_missing_required_files_fail(self):
        for name in ("model.txt", "metadata.json"):
            path = self.put()
            (path / name).unlink()
            with self.subTest(name=name), self.assertRaises(ModelArtifactError):
                load_model(path)
            shutil.rmtree(path)

    def test_extra_file_or_directory_fails(self):
        for directory in (False, True):
            path = self.put()
            extra = path / "unexpected"
            extra.mkdir() if directory else extra.write_bytes(b"extra")
            with self.assertRaises(ModelArtifactError):
                load_model(path)
            shutil.rmtree(path)

    def test_model_byte_corruption_fails_hash_check(self):
        path = self.put(model_bytes=self.artifact.model_bytes+b"changed")
        with self.assertRaisesRegex(ModelArtifactError, "SHA-256"):
            load_model(path)

    def test_changed_model_sha_metadata_fails(self):
        metadata = self.artifact.metadata
        metadata["model_sha256"] = "0"*64
        path = self.put(metadata=reidentify(metadata))
        with self.assertRaisesRegex(ModelArtifactError, "SHA-256"):
            load_model(path)

    def test_changed_artifact_id_fails(self):
        metadata = self.artifact.metadata
        metadata["artifact_id"] = "0"*64
        with self.assertRaises(ModelArtifactError):
            load_model(self.put(metadata=metadata))

    def test_directory_name_must_equal_identity(self):
        path = self.put()
        altered = path.with_name("0"*64)
        path.rename(altered)
        with self.assertRaisesRegex(ModelArtifactError, "directory"):
            load_model(altered)

    def test_compatibility_tampering_rejected_even_with_updated_hash(self):
        for key, value in (
            ("feature_version", "wrong"), ("feature_names", list(reversed(self.artifact.metadata["feature_names"]))),
            ("feature_names", self.artifact.metadata["feature_names"][:-1]),
            ("feature_names", self.artifact.metadata["feature_names"]+["extra"]),
            ("target_version", "wrong"), ("target_horizon_minutes", 4), ("target_horizon_minutes", True),
            ("model_family_version", "wrong"), ("artifact_schema_version", "wrong"),
        ):
            metadata = self.artifact.metadata
            metadata[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ModelArtifactError):
                load_model(self.put(metadata=reidentify(metadata)))

    def test_invalid_metric_metadata_fails(self):
        for value in ("nan", "inf", "-inf", "-1.0", 0.1, "0.1000", None):
            metadata = self.artifact.metadata
            metadata["validation_rmse"] = value
            with self.subTest(value=value), self.assertRaises(ModelArtifactError):
                load_model(self.put(metadata=reidentify(metadata)))

    def test_malformed_noncanonical_and_duplicate_key_json_fail(self):
        for payload in (b"{invalid", b"[]", b'{"x":NaN}', self.artifact.metadata_bytes+b"\n",
                        self.artifact.metadata_bytes[:-1]+b',"artifact_id":"duplicate"}'):
            path = self.put()
            (path / "metadata.json").write_bytes(payload)
            with self.subTest(payload=payload[:20]), self.assertRaises(ModelArtifactError):
                load_model(path)

    def test_malformed_native_text_fails_even_with_matching_checksums(self):
        payload = b"invalid native model text"
        metadata = self.artifact.metadata
        metadata["model_sha256"] = sha256(payload).hexdigest()
        with self.assertRaisesRegex(ModelArtifactError, "header"):
            load_model(self.put(model_bytes=payload, metadata=reidentify(metadata)))

    def test_native_booster_feature_schema_is_verified(self):
        original = self.artifact.model_bytes
        payload = original.replace(b"feature_names=return_1m ", b"feature_names=wrong_name ", 1)
        self.assertNotEqual(payload, original)
        metadata = self.artifact.metadata
        metadata["model_sha256"] = sha256(payload).hexdigest()
        with self.assertRaisesRegex(ModelArtifactError, "Booster feature"):
            load_model(self.put(model_bytes=payload, metadata=reidentify(metadata)))

    def test_native_parser_failure_becomes_narrow_artifact_error(self):
        from lightgbm.basic import LightGBMError
        path = self.put()
        with patch("quantos.infrastructure.models.model.lgb.Booster", side_effect=LightGBMError("native parse failed")):
            with self.assertRaisesRegex(ModelArtifactError, "native parse failed"):
                load_model(path)

    def test_best_iteration_must_match_native_tree_state(self):
        metadata = self.artifact.metadata
        metadata["best_iteration"] = 1 if metadata["best_iteration"] != 1 else 2
        with self.assertRaisesRegex(ModelArtifactError, "tree state"):
            load_model(self.put(metadata=reidentify(metadata)))

    def test_metadata_config_counts_and_provenance_are_validated(self):
        for mutate in (
            lambda m: m["training_config"].update(learning_rate="invalid"),
            lambda m: m["training_config"].update(random_seed=True),
            lambda m: m["resolved_parameters"].update(device_type="gpu"),
            lambda m: m["train_counts"].update(BTCUSDT=0),
            lambda m: m["source_identities"][0].update(validation_status="unvalidated"),
            lambda m: m["source_identities"][0].update(symbol="ETHUSDT"),
            lambda m: m.update(validation_end_exclusive=m["train_start"]),
            lambda m: m["exclusions"]["purged_training_boundary"]["counts"].update(BTCUSDT=999),
        ):
            metadata = self.artifact.metadata
            mutate(metadata)
            with self.assertRaises(ModelArtifactError):
                load_model(self.put(metadata=reidentify(metadata)))

    def test_corrupt_existing_artifact_never_replaced(self):
        path = self.put(model_bytes=b"corrupt")
        with self.assertRaises(ModelArtifactError):
            self.store.write(self.artifact)
        self.assertEqual((path / "model.txt").read_bytes(), b"corrupt")

    def test_empty_existing_directory_is_not_overwritten(self):
        path = self.store.artifact_path(self.artifact)
        path.mkdir(parents=True)
        with self.assertRaises(ModelArtifactError):
            self.store.write(self.artifact)
        self.assertEqual(list(path.iterdir()), [])

    def test_flush_and_verify_happen_before_atomic_publication(self):
        from quantos.infrastructure.models.artifacts import _publish_no_replace
        calls = []
        original_fsync = os.fsync
        def sync(fd):
            calls.append("sync")
            return original_fsync(fd)
        def publish(temporary, destination):
            self.assertEqual(calls, ["sync", "sync"])
            self.assertEqual(temporary.parent, destination.parent)
            self.assertFalse(destination.exists())
            self.assertEqual({item.name for item in temporary.iterdir()}, {"model.txt", "metadata.json"})
            return _publish_no_replace(temporary, destination)
        with patch(f"{MODULE}.os.fsync", side_effect=sync), patch(f"{MODULE}._publish_no_replace", side_effect=publish):
            path = self.store.write(self.artifact)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_publication_failure_cleans_temporary_directory(self):
        with patch(f"{MODULE}._publish_no_replace", side_effect=OSError("injected rename failure")):
            with self.assertRaisesRegex(ModelArtifactError, "injected rename failure"):
                self.store.write(self.artifact)
        parent = self.store.artifact_path(self.artifact).parent
        self.assertEqual(list(parent.iterdir()), [])

    def test_write_failure_cleans_temporary_directory(self):
        with patch(f"{MODULE}.os.fsync", side_effect=OSError("injected flush failure")):
            with self.assertRaisesRegex(ModelArtifactError, "injected flush failure"):
                self.store.write(self.artifact)
        self.assertEqual(list(self.store.artifact_path(self.artifact).parent.iterdir()), [])

    def test_concurrent_identical_winner_is_idempotent(self):
        def win(temporary, destination):
            shutil.copytree(temporary, destination)
            raise FileExistsError("other publisher won")
        with patch(f"{MODULE}._publish_no_replace", side_effect=win):
            path = self.store.write(self.artifact)
        self.assertEqual(list(path.parent.iterdir()), [path])
        self.assertEqual((path / "model.txt").read_bytes(), self.artifact.model_bytes)

    def test_concurrent_corrupt_winner_is_preserved_and_rejected(self):
        def win(temporary, destination):
            destination.mkdir()
            (destination / "corrupt").write_bytes(b"do not replace")
            raise FileExistsError("other publisher won")
        with patch(f"{MODULE}._publish_no_replace", side_effect=win), self.assertRaises(ModelArtifactError):
            self.store.write(self.artifact)
        path = self.store.artifact_path(self.artifact)
        self.assertEqual((path / "corrupt").read_bytes(), b"do not replace")
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_reparse_artifact_file_is_rejected(self):
        path = self.put()
        import quantos.infrastructure.models.artifacts as module
        original = module._plain_path
        def reject(target):
            if target.name == "model.txt":
                raise ModelArtifactError("artifact paths must not be links or reparse points")
            original(target)
        with patch.object(module, "_plain_path", side_effect=reject), self.assertRaises(ModelArtifactError):
            load_model(path)

    def test_unsupported_publication_primitive_fails_closed(self):
        with patch(f"{MODULE}.os.name", "posix"), self.assertRaisesRegex(ModelArtifactError, "requires Windows"):
            from quantos.infrastructure.models.artifacts import _publish_no_replace
            _publish_no_replace(self.root / "unused-source", self.root / "unused-target")
