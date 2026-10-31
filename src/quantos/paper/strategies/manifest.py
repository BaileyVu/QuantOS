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
    status: str = "library"


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
    Manifest('crash_flush_e2', 'research-1.0.0', 'crash_reversal',
             'Owner-specified crash/OI candidate; previous test did not beat crash-only baseline',
             '1h', 'MARKET', 'time', 259200,
             (('oi_change_hours','4'),('oi_z_window_hours','720'),('oi_z_threshold','-2'),
              ('drawdown_24h','0.08'),('stop_fraction','0.25'),('holding_hours','24'),
              ('holding_grid_hours','24,48,72'),('cooldown_hours','24'),
              ('stop_sensitivity_only','0.15'),('margin_asset','USDT')),
             'CANDIDATE_UNVERIFIED', status='candidate'),
    Manifest('alt_dip_reversal', 'research-1.0.0', 'cross_sectional_reversal',
             'Owner-supplied simple_baseline.py; spot-to-perpetual adaptation, survivor universe',
             '1h', 'MARKET', 'time', 259200,
             (('calibration_days','90'),('embargo_days','3'),('cutoff_quantile','0.01'),
              ('quantile_grid','0.005,0.01'),('holding_hours','24'),
              ('holding_grid_hours','12,24,72'),('stop_fraction','0.25'),
              ('stop_sensitivity_only','0.15'),('margin_asset','USDT')),
             'CANDIDATE_PARTIALLY_CONTAMINATED', status='candidate'),
    Manifest('ema_level_2h', 'research-1.0.0', 'support_resistance',
             'Owner-supplied causal zone logic; preregistered 12/26 adaptation distinct from source audit',
             '2h', 'MARKET', 'fixed', 259200,
             (('fast_ema','12'),('slow_ema','26'),('atr_period','14'),('atr_smoothing','EMA'),
              ('pivot_k','5'),('level_lookback','288'),('zone_tolerance_atr','0.25'),
              ('prior_distinct_touches','2'),('zone_body','false'),('flip_zones','true'),
              ('stop_buffer_atr','0.25'),('leave_atr','1'),('retest_bars','12'),
              ('near_atr','1'),('target_r','1.5'),('maximum_hold_hours','72'),
              ('major_swing_fib_filter','false'),('margin_asset','USDT')),
             'CANDIDATE_UNVERIFIED', status='candidate'),
    Manifest('crash_flush_e2', 'paper-usdc-1.1.0', 'crash_reversal',
             'crash_flush_e2 research rule mapped to USDC perps; tolerant OI repair rule',
             '1h', 'MARKET', 'time', 86400,
             (('oi_change_hours','4'),('oi_z_window_hours','720'),('oi_z_threshold','-2'),
              ('oi_mode','tolerant'),('oi_min_coverage','0.9'),
              ('drawdown_24h','0.08'),('stop_fraction','0.25'),('holding_hours','24'),
              ('cooldown_hours','24'),('signal_source','USDC candles + USDT twin OI'),
              ('margin_asset','USDC')),
             'PROMOTED_PAPER_UNVERIFIED', status='promoted'),
    Manifest('alt_dip_reversal', 'paper-usdc-1.1.0', 'cross_sectional_reversal',
             'alt_dip_reversal research rule mapped to USDC perps; USDT-calibrated quarterly cutoff',
             '1h', 'MARKET', 'time', 86400,
             (('calibration_days','90'),('embargo_days','3'),('cutoff_quantile','0.01'),
              ('holding_hours','24'),('stop_fraction','0.25'),
              ('signal_source','USDC candles + USDT-universe cutoff'),('margin_asset','USDC')),
             'PROMOTED_PAPER_UNVERIFIED', status='promoted'),
)


def manifest_for(strategy_id: str, version: str | None = None) -> Manifest | None:
    return next((m for m in MANIFESTS if strategy_id in (m.strategy_id, *m.aliases)
                 and (version is None or version == m.version)), None)
