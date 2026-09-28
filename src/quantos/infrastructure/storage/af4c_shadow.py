"""Write synthetic sealed evidence and an allowlisted operational receipt.

Sealing separates disclosure surfaces; it is not encryption or an OS access
control. No result discovery, unseal operation, or execution CLI is provided.
"""
from dataclasses import dataclass
from pathlib import Path

from quantos.domain.evaluation.af4c import (
    CATALOG_ID, EVALUATOR_ID, FDR_FAMILY_ID, SyntheticResult,
)
from quantos.domain.evaluation.alpha_funnel import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class PublicReceipt:
    payload: bytes


def public_receipt(result: SyntheticResult) -> PublicReceipt:
    if type(result) is not SyntheticResult:
        raise ValueError("synthetic result required")
    document = result.validate()
    return PublicReceipt(canonical_json_bytes({
        "schema": "af4c-shadow-receipt-v1", "completed_successfully": True,
        "data_kind": "SYNTHETIC", "catalog_id": CATALOG_ID,
        "fdr_family_id": FDR_FAMILY_ID, "evaluator_version": EVALUATOR_ID,
        "registered_evaluation_count": 9,
        "sealed_payload_sha256": result.payload_sha256,
        "input_identity_sha256": document["input_identity_sha256"],
        "integrity_status": "verified", "shadow": True, "sealed": True,
    }))


def publish_synthetic_shadow(result: SyntheticResult, directory: Path) -> PublicReceipt:
    """Create a fresh caller-selected directory; tests use temporary directories.

    Exclusive creation prevents overwriting evidence. The receipt is written last:
    an interrupted publication has no successful public completion receipt.
    """
    receipt = public_receipt(result)
    directory = Path(directory)
    if directory.parent.is_symlink() or directory.parent.is_junction():
        raise ValueError("publication parent must not redirect")
    directory.mkdir(exist_ok=False)
    sealed = directory / "sealed"
    sealed.mkdir()
    with (sealed / "payload.json").open("xb") as stream:
        stream.write(result.payload)
    if (sealed / "payload.json").read_bytes() != result.payload:
        raise ValueError("sealed publication verification failed")
    with (directory / "receipt.json").open("xb") as stream:
        stream.write(receipt.payload)
    return receipt
