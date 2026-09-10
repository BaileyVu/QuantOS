"""Verified, immutable publication of complete native model directories."""

import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

from quantos.infrastructure.models.config import MODEL_ARTIFACT_SCHEMA_VERSION, ModelArtifactError
from quantos.infrastructure.models.model import ModelArtifact, VerifiedModel

_LOG = logging.getLogger("quantos")


def _plain_path(path: Path) -> None:
    attributes = path.lstat()
    if path.is_symlink() or getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ModelArtifactError("artifact paths must not be links or reparse points")


def _read(path: Path, *, canonical: bool = True) -> ModelArtifact:
    try:
        _plain_path(path)
        if not path.is_dir() or {child.name for child in path.iterdir()} != {"model.txt", "metadata.json"}:
            raise ModelArtifactError("artifact must contain exactly model.txt and metadata.json")
        for name in ("model.txt", "metadata.json"):
            child = path / name
            _plain_path(child)
            if not child.is_file():
                raise ModelArtifactError("artifact contents must be regular files")
        artifact = ModelArtifact((path / "model.txt").read_bytes(), (path / "metadata.json").read_bytes())
        if canonical and (path.name != artifact.artifact_id or path.parent.name != MODEL_ARTIFACT_SCHEMA_VERSION or path.parent.parent.name != "models"):
            raise ModelArtifactError("artifact directory does not match canonical identity path")
        artifact.load()
        return artifact
    except ModelArtifactError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise ModelArtifactError(f"cannot read model artifact {path}: {error}") from error


def load_model(path: Path) -> VerifiedModel:
    """Load exactly one caller-selected artifact after full integrity checks."""
    artifact = _read(Path(path))
    model = artifact.load()
    _LOG.info("model_loaded", extra={"event": "model_loaded", "context": {"model_version": model.model_version}})
    return model


def _publish_no_replace(temporary: Path, destination: Path) -> None:
    # Windows rename never replaces an existing destination, including an empty
    # directory. POSIX rename may replace empty directories, so fail closed there.
    if os.name != "nt":
        raise ModelArtifactError("atomic no-overwrite directory publication currently requires Windows")
    os.rename(temporary, destination)


class ModelArtifactStore:
    """Caller supplies a local artifact root outside source control."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def artifact_path(self, artifact: ModelArtifact) -> Path:
        if type(artifact) is not ModelArtifact:
            raise ModelArtifactError("publication requires a ModelArtifact")
        return self._root / "models" / MODEL_ARTIFACT_SCHEMA_VERSION / artifact.artifact_id

    def _existing(self, destination: Path, artifact: ModelArtifact) -> Path:
        if _read(destination) != artifact:
            raise ModelArtifactError("existing immutable artifact differs from requested bytes")
        return destination

    def write(self, artifact: ModelArtifact) -> Path:
        temporary = None
        try:
            destination = self.artifact_path(artifact)
            artifact.load()
            if destination.exists():
                return self._existing(destination, artifact)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=".publishing-", dir=destination.parent))
            for name, payload in (("model.txt", artifact.model_bytes), ("metadata.json", artifact.metadata_bytes)):
                with (temporary / name).open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            if _read(temporary, canonical=False) != artifact:
                raise ModelArtifactError("temporary artifact differs from training result")
            try:
                _publish_no_replace(temporary, destination)
            except OSError:
                if destination.exists():
                    return self._existing(destination, artifact)
                raise
            temporary = None
            _LOG.info("model_published", extra={"event": "model_published", "context": {"model_version": artifact.model_version}})
            return destination
        except ModelArtifactError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise ModelArtifactError(f"cannot publish model artifact: {error}") from error
        finally:
            if temporary is not None:
                active_error = sys.exception()
                try:
                    # Only this call's mkdtemp directory can reach cleanup.
                    shutil.rmtree(temporary)
                except OSError as error:
                    if active_error is not None:
                        active_error.add_note(f"temporary artifact cleanup failed: {error}")
                    else:
                        raise ModelArtifactError(f"temporary artifact cleanup failed: {error}") from error
