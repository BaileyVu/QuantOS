"""Strict runtime settings referencing the existing paper/risk configuration."""
from dataclasses import dataclass
from pathlib import Path
import tomllib

from quantos.application.paper_runtime import PaperRuntimePolicy
from quantos.infrastructure.configuration.config import ConfigurationError
from quantos.infrastructure.configuration.paper import load_paper_config


@dataclass(frozen=True, slots=True)
class PaperRuntimeConfig:
    policy: PaperRuntimePolicy
    ledger_path: Path
    state_path: Path
    evidence_path: Path


def load_paper_runtime_config(path: str | Path, *, alpha_implementation_id: str) -> PaperRuntimeConfig:
    try:
        source = Path(path)
        with source.open("rb") as stream:
            document = tomllib.load(stream)
        keys = {"mode", "symbols", "interval", "paper_config", "state_path", "evidence_path", "synchronization_timeout_ms", "provider_clock_skew_tolerance_ms"}
        if set(document) != {"runtime"} or set(document["runtime"]) != keys:
            raise ValueError("expected exact [runtime] keys")
        value = document["runtime"]
        if value["mode"] != "paper" or value["interval"] != "1m":
            raise ValueError("runtime supports paper mode and 1m only")
        if type(value["symbols"]) is not list:
            raise ValueError("symbols must be an array")
        for key in ("paper_config", "state_path", "evidence_path"):
            if type(value[key]) is not str or not value[key].strip():
                raise ValueError(f"{key} must be an explicit path")
        paper = load_paper_config(source.parent / value["paper_config"])
        policy = PaperRuntimePolicy(tuple(value["symbols"]), paper.initial_capital, paper.risk,
                                   value["synchronization_timeout_ms"], alpha_implementation_id,
                                   provider_clock_skew_tolerance_ms=value["provider_clock_skew_tolerance_ms"])
        state, evidence, ledger = Path(value["state_path"]), Path(value["evidence_path"]), paper.ledger_path
        paths = [state, evidence, state.with_name(state.name+".lock"), state.with_name(state.name+".tmp"),
                 ledger, *(ledger.with_name(ledger.name+suffix) for suffix in (".head", ".head.tmp", ".lock", ".runtime.lock"))]
        resolved = [p.resolve() for p in paths]
        if len(set(resolved)) != len(resolved):
            raise ValueError("runtime and execution persistence paths overlap")
        return PaperRuntimeConfig(policy, ledger, state, evidence)
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise ConfigurationError(f"invalid paper runtime configuration: {error}") from error
