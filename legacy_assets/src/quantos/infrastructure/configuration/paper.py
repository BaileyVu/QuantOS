"""Strict explicit V1-T1 paper/risk configuration, independent of Phase 1 startup."""
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tomllib

from quantos.domain.execution.policy import ExecutionCosts
from quantos.domain.risk.engine import RiskPolicy
from quantos.infrastructure.configuration.config import ConfigurationError


@dataclass(frozen=True, slots=True)
class PaperConfig:
    mode: str
    initial_capital: Decimal
    ledger_path: Path
    risk: RiskPolicy


def load_paper_config(path: str | Path) -> PaperConfig:
    try:
        with Path(path).open('rb') as stream:
            document = tomllib.load(stream)
        if set(document) != {'paper', 'risk'}:
            raise ValueError('expected only [paper] and [risk]')
        paper = document['paper']
        if set(paper) != {'mode', 'initial_capital', 'ledger_path', 'fee_rate', 'slippage_rate'}:
            raise ValueError('invalid [paper] keys')
        if paper['mode'] != 'paper':
            raise ValueError('only paper mode is enabled')
        def decimal(value):
            if type(value) is not str:
                raise ValueError('money, quantities and rates must be Decimal strings')
            result = Decimal(value)
            if not result.is_finite():
                raise ValueError('non-finite configuration')
            return result
        capital = decimal(paper['initial_capital'])
        if capital <= 0:
            raise ValueError('initial_capital must be positive')
        if type(paper['ledger_path']) is not str or not paper['ledger_path'].strip():
            raise ValueError('ledger_path must be explicit')
        costs = ExecutionCosts(decimal(paper['fee_rate']), decimal(paper['slippage_rate']))
        risk = document['risk']
        required = {f.name for f in fields(RiskPolicy)} - {'costs', 'volatility_target'}
        if not required <= set(risk) or set(risk) - required - {'volatility_target'}:
            raise ValueError('invalid [risk] keys')
        values = {k: v if k == 'stale_seconds' else decimal(v) for k, v in risk.items()}
        return PaperConfig('paper', capital, Path(paper['ledger_path']), RiskPolicy(**values, costs=costs))
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation) as error:
        raise ConfigurationError(f'invalid paper configuration: {error}') from error
