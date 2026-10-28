"""Immutable preregistration and one-way outcome access for portfolio research."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _exclusive_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        out.write(canonical(value).decode() + "\n")
        out.flush()
        import os
        os.fsync(out.fileno())


def seal(path: Path, plan: dict, training_selection: dict, *,
         code_sha: str, training_data_sha: str) -> dict:
    """Never overwrite a seal, even if a new outcome would favor another pick."""
    if plan.get("training_end_exclusive") != "2025-01-01T00:00:00Z":
        raise ValueError("training must end before 2025")
    if plan.get("evaluation_start") != "2025-01-01T00:00:00Z":
        raise ValueError("evaluation starts in 2025")
    if not code_sha or len(training_data_sha) != 64 or not training_selection:
        raise ValueError("missing provenance or training selection")
    payload = {"schema": 1, "plan": plan, "selection": training_selection,
               "code_sha": code_sha, "training_data_sha": training_data_sha}
    result = {"payload": payload, "sha256": fingerprint(payload)}
    _exclusive_json(path, result)
    return result


def read_seal(path: Path) -> dict:
    value = json.loads(path.read_text())
    if fingerprint(value["payload"]) != value["sha256"]:
        raise ValueError("seal modified")
    return value


def claim_evaluation(directory: Path, sealed: dict, evaluation_data_sha: str) -> dict:
    """Claim one evaluation wave; crash recovery must resume its own cell ledger.

    Sensitivities and the complete exploratory grid belong to this same wave,
    not a new choice after seeing results. A second claim is an explicit error.
    """
    if fingerprint(sealed["payload"]) != sealed["sha256"] or len(evaluation_data_sha) != 64:
        raise ValueError("invalid provenance")
    value = {"schema": 1, "seal_sha256": sealed["sha256"],
             "evaluation_data_sha256": evaluation_data_sha, "status": "claimed"}
    _exclusive_json(directory / "evaluation_claim.json", value)
    return value


def complete_cell(directory: Path, cell: dict, result: dict) -> str:
    """Immutable, uniquely keyed result for safe restart without rerunning cells."""
    if not (directory / "evaluation_claim.json").is_file():
        raise ValueError("evaluation not claimed")
    key = fingerprint(cell)
    _exclusive_json(directory / "cells" / f"{key}.json", {"cell": cell, "result": result})
    return key
