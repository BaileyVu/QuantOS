"""Versioned, preregistered paper rules. Signal payloads cannot grant exceptions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Manifest:
    strategy_id: str
    version: str
    family: str
    source: str
    timeframe: str
    entry_type: str
    exit_model: str
    maximum_hold_seconds: int
    parameters: tuple[tuple[str, str], ...]
    evidence_status: str
    aliases: tuple[str, ...] = ()


MANIFESTS = (
    Manifest('ema_atr_ref', '1.0.0', 'reference', 'QuantOS V3 EMA/ATR reference',
             '5m', 'LIMIT', 'fixed', 3600,
             (('fast_ema','20'),('slow_ema','50'),('atr_period','14'),('pullback_atr','0.25'),
              ('stop_atr','1.5'),('target_atr','2.0')), 'PAPER_REFERENCE', ('ema_atr_limit_pullback',)),
    Manifest('donchian_atr_ref', '1.0.0', 'reference', 'QuantOS V3 Donchian/ATR reference',
             '5m', 'STOP', 'fixed', 3600,
             (('channel_period','20'),('atr_period','14'),('stop_atr','1.5'),('target_atr','2.0')),
             'PAPER_REFERENCE', ('donchian_atr_stop_breakout',)),
    Manifest('tsmom_4h', '1.0.0', 'trend',
             'Moskowitz, Ooi & Pedersen 2012; task-specific 30-day/4h adaptation',
             '4h', 'MARKET', 'trailing', 259200,
             (('return_bars','180'),('ema_period','50'),('atr_period','14'),('stop_atr','2'),
              ('trail_activate_r','1'),('trail_distance_r','2'),('atr_smoothing','Wilder')),
             'UNPROVEN_REFERENCE'),
    Manifest('turtle_55', '1.0.0', 'trend', 'Original Turtle System 2; task-specific 4h adaptation',
             '4h', 'STOP', 'channel', 259200,
             (('entry_bars','55'),('exit_bars','20'),('atr_period','20'),('stop_atr','2'),
              ('atr_smoothing','Wilder')), 'UNPROVEN_REFERENCE'),
    Manifest('vol_squeeze_breakout', '1.0.0', 'volatility',
             'Carter TTM squeeze; unproven range-breakout adaptation',
             '1h', 'STOP', 'fixed', 14400,
             (('period','20'),('bb_std','2'),('kc_atr','1.5'),('kc_basis','EMA'),
              ('atr_smoothing','Wilder'),('target_r','2'),('range','consecutive squeeze bars'),
              ('trigger','first closed squeeze-off bar beyond range; stop beyond its extreme')),
             'UNPROVEN_REFERENCE'),
    Manifest('delist_short_e17', '1.0.0', 'event', 'Owner-supplied e17/LIVE_SPEC.md; Binance CMS catalog 161',
             'event', 'MARKET', 'time', 14400,
             (('maximum_latency_ms','300000'),('stop_fraction','0.25'),('poll_seconds','30'),
              ('margin_asset','USDC')), 'FRAGILE_SOURCE_ONLY'),
)


def manifest_for(strategy_id: str, version: str | None = None) -> Manifest | None:
    return next((m for m in MANIFESTS if strategy_id in (m.strategy_id, *m.aliases)
                 and (version is None or version == m.version)), None)
