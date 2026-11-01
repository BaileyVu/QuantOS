"""Immutable filesystem publication and durable verification of AF1 artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from quantos.domain.evaluation.alpha_funnel import (
    AlphaFunnelError,
    ScreeningRun,
    VerifiedScreeningArtifact,
    _verify_persisted_screening_artifact,
)


_ARTIFACT_NAMES = frozenset({"manifest.json", "results.json"})
_HEX_DIGITS = frozenset("0123456789abcdef")


class AlphaFunnelArtifactStore:
    """Publish evaluator runs and independently verify persisted AF1 output."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve(strict=False)

    def _runs_root(self) -> Path:
        runs_root = (self._root / "runs").resolve(strict=False)
        if runs_root == self._root or not runs_root.is_relative_to(self._root):
            raise AlphaFunnelError("AF1 runs directory escapes the configured artifact root")
        return runs_root

    def _run_directory(self, run_id: str) -> Path:
        if (
            type(run_id) is not str
            or len(run_id) != 64
            or any(character not in _HEX_DIGITS for character in run_id)
        ):
            raise AlphaFunnelError("AF1 run_id must be a canonical SHA-256")
        runs_root = self._runs_root()
        destination = runs_root / run_id
        if destination.parent != runs_root:
            raise AlphaFunnelError("AF1 run path escapes the configured artifact root")
        return destination

    def run_path(self, run: ScreeningRun) -> Path:
        if type(run) is not ScreeningRun:
            raise AlphaFunnelError("artifact publication requires ScreeningRun")
        run.verify_integrity()
        return self._run_directory(run.run_id)

    @staticmethod
    def _expected(run: ScreeningRun) -> dict[str, bytes]:
        run.verify_integrity()
        return {
            "manifest.json": run.manifest_bytes(),
            "results.json": run.results_bytes(),
        }

    def _read_existing(self, destination: Path) -> dict[str, bytes]:
        if destination.is_symlink():
            raise AlphaFunnelError("existing AF1 run path must not be a symbolic link")
        if (
            not destination.is_dir()
            or {item.name for item in destination.iterdir()} != _ARTIFACT_NAMES
        ):
            raise AlphaFunnelError("existing AF1 run directory has unexpected contents")
        canonical_destination = destination.resolve(strict=True)
        runs_root = self._runs_root()
        if (
            canonical_destination.parent != runs_root
            or not canonical_destination.is_relative_to(self._root)
        ):
            raise AlphaFunnelError("existing AF1 run path escapes the configured artifact root")

        payloads: dict[str, bytes] = {}
        for name in sorted(_ARTIFACT_NAMES):
            child = destination / name
            if child.is_symlink():
                raise AlphaFunnelError(
                    "existing AF1 artifact child must not be a symbolic link"
                )
            resolved_child = child.resolve(strict=True)
            if (
                resolved_child.parent != canonical_destination
                or not resolved_child.is_file()
            ):
                raise AlphaFunnelError(
                    "existing AF1 artifact child escapes its canonical run directory"
                )
            payloads[name] = resolved_child.read_bytes()
        return payloads

    def load_verified(self, run_id: str) -> VerifiedScreeningArtifact:
        """Load and independently verify one persisted AF1 research artifact."""
        try:
            destination = self._run_directory(run_id)
            payloads = self._read_existing(destination)
            return _verify_persisted_screening_artifact(
                run_id,
                payloads["manifest.json"],
                payloads["results.json"],
            )
        except AlphaFunnelError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise AlphaFunnelError(f"cannot load AF1 artifact: {error}") from error

    def _verify_existing(self, destination: Path, expected: dict[str, bytes]) -> Path:
        payloads = self._read_existing(destination)
        try:
            verified = _verify_persisted_screening_artifact(
                destination.name,
                payloads["manifest.json"],
                payloads["results.json"],
            )
        except AlphaFunnelError as error:
            raise AlphaFunnelError(
                "existing AF1 run differs from deterministic output"
            ) from error
        actual = {
            "manifest.json": verified.manifest_bytes(),
            "results.json": verified.results_bytes(),
        }
        if actual != expected:
            raise AlphaFunnelError(
                "existing AF1 run differs from deterministic output"
            )
        return destination

    def write(self, run: ScreeningRun) -> Path:
        destination = self.run_path(run)
        expected = self._expected(run)
        temporary: Path | None = None
        try:
            if destination.exists() or destination.is_symlink():
                return self._verify_existing(destination, expected)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(
                prefix=".publishing-af1-",
                dir=destination.parent,
            ))
            for name, payload in expected.items():
                with (temporary / name).open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            try:
                os.rename(temporary, destination)
                temporary = None
            except OSError:
                if destination.exists() or destination.is_symlink():
                    return self._verify_existing(destination, expected)
                raise
            return destination
        except AlphaFunnelError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise AlphaFunnelError(f"cannot publish AF1 artifact: {error}") from error
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary)
