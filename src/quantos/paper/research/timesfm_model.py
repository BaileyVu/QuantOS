"""Explicit loader for the pinned CPU TimesFM 2.5 research model.

Importing this module does not import torch/TimesFM or download anything. The
loader requires a caller-owned cache path, disables Hub telemetry, uses no Hub
token, and verifies the pinned checkpoint bytes before model initialization.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import os
from pathlib import Path

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = ""

MODEL_ID = "google/timesfm-2.5-200m-pytorch"
MODEL_REVISION = "2d1b32066b7f3950c45913d4a3849f3c75cb0312"
WEIGHTS_SHA256 = "2f776efe6245e42b24bc4153ffdf61810140210e4bd3b01fb21f7aa779ab6ce8"
THREAD_COUNT = 2


@dataclass(frozen=True)
class LoadedTimesFM25:
    model: object
    metadata: dict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_timesfm25(*, cache_dir: str | Path,
                   local_files_only: bool = False) -> LoadedTimesFM25:
    """Download or reuse the pinned 2.5 checkpoint and load it on CPU.

    `cache_dir` is mandatory so no default shared Hugging Face cache is used.
    This call performs checkpoint loading, but not a forecast/inference call.
    """
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    cache = Path(cache_dir).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)

    import torch
    from huggingface_hub import hf_hub_download

    torch.set_num_threads(THREAD_COUNT)
    weights_path = Path(hf_hub_download(
        repo_id=MODEL_ID, filename="model.safetensors", revision=MODEL_REVISION,
        cache_dir=str(cache), token=False, local_files_only=local_files_only,
    ))
    actual_hash = _sha256(weights_path)
    if actual_hash != WEIGHTS_SHA256:
        raise ValueError("pinned TimesFM checkpoint SHA-256 mismatch")

    from timesfm import ForecastConfig, TimesFM_2p5_200M_torch

    model = TimesFM_2p5_200M_torch.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, cache_dir=str(cache), token=False,
        torch_compile=False, local_files_only=local_files_only,
    )
    device = getattr(getattr(model, "model", None), "device", None)
    if getattr(device, "type", None) != "cpu":
        raise RuntimeError("TimesFM model did not select CPU")
    model.compile(ForecastConfig(
        max_context=512,
        max_horizon=128,
        normalize_inputs=True,
        use_continuous_quantile_head=True,
        fix_quantile_crossing=True,
        per_core_batch_size=1,
    ))
    package_names = ("timesfm", "torch", "numpy", "huggingface-hub", "safetensors")
    versions = {name: importlib.metadata.version(name) for name in package_names}
    metadata = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "weights_sha256": actual_hash,
        "cache_dir": str(cache),
        "device": "cpu",
        "torch_num_threads": THREAD_COUNT,
        "dependencies": versions,
    }
    return LoadedTimesFM25(model=model, metadata=metadata)


def load_model(*, cache_dir: str | Path,
               local_files_only: bool = False) -> tuple[object, dict]:
    """Public adapter expected by offline experiment runners."""
    loaded = load_timesfm25(cache_dir=cache_dir,
                            local_files_only=local_files_only)
    return loaded.model, loaded.metadata
