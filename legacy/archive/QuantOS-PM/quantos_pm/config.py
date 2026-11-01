from dataclasses import dataclass
from pathlib import Path
import tomllib
from .domain.common import D

ROOT = Path(__file__).resolve().parent.parent
FLAGS = ('enable_taker_complete_set_arb', 'enable_early_loser_exit',
         'enable_directional_inventory_alpha', 'enable_live_orders')


def local_path(value, *, must_exist=False):
    """All user-controlled file paths are confined to this independent project."""
    p = Path(value)
    if not p.is_absolute():
        p = ROOT / p
    resolved = p.resolve()
    if not resolved.is_relative_to(ROOT) or '.git' in resolved.parts or '.venv' in resolved.parts:
        raise ValueError('path escapes QuantOS-PM data/source boundary')
    if must_exist and not resolved.is_file():
        raise ValueError(f'missing project file: {resolved.name}')
    return resolved


@dataclass(frozen=True)
class Config:
    capital: object
    model: str
    mode: str
    quote_size: object
    stale_ms: int
    submit_ms: int
    cancel_ms: int
    quote_ttl_ms: int
    calibration: str
    raw: dict


def load(path):
    raw = tomllib.loads(local_path(path, must_exist=True).read_text())
    allowed = {'capital', 'model', 'mode', 'quote_size', 'stale_ms', 'submit_ms',
               'cancel_ms', 'quote_ttl_ms', 'calibration', *FLAGS}
    if set(raw) - allowed:
        raise ValueError('unknown config keys')
    if any(raw.get(f, False) is not False for f in FLAGS):
        raise ValueError('all research/live flags are structurally disabled in V0')
    c = Config(D(raw['capital']), raw['model'], raw['mode'], D(raw['quote_size']),
               raw['stale_ms'], raw['submit_ms'], raw['cancel_ms'], raw['quote_ttl_ms'],
               raw.get('calibration', ''), raw)
    if c.capital <= 0 or c.quote_size <= 0 or c.mode not in ('paper', 'shadow') or c.model not in ('optimistic', 'baseline', 'pessimistic'):
        raise ValueError('invalid configuration')
    for x in (c.stale_ms, c.submit_ms, c.cancel_ms, c.quote_ttl_ms):
        if type(x) is not int or x <= 0:
            raise ValueError('latencies must be positive integer milliseconds')
    return c
