"""Pre-PnL entry-evidence gate, not an alternative backtester or promotion path."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from quantos.domain.alpha.tsmom import calibrate_candidates
from quantos.domain.features.daily import HORIZONS
from quantos.domain.runtime_contracts import identity


@dataclass(frozen=True, slots=True)
class CalibrationScreen:
    dataset_identity: str
    protocol_identity: str
    windows: tuple
    eligible_horizons: tuple[int, ...]
    verdict: str
    holdout_opened: bool = False

    @property
    def artifact_id(self):
        return identity(self)


def screen_entry_evidence(*, development, protocol_identity, train_days=365,
                          validation_days=90, step_days=90, progress=None):
    """Require an eligible calibration in every predeclared fold and final training.

    Caller must pass only the permitted development interval. No held-out market
    data or strategy-performance output is accepted through this API.
    """
    from quantos.application.evaluation import _validate_datasets
    from datetime import datetime, timezone
    _validate_datasets((development,), minute_end_clock=True)
    if development.identity.symbol != 'BTCUSDT':
        raise ValueError('initial production selection is BTCUSDT only')
    candles=development.candles
    if candles[0].open_time != datetime(2024,1,1,tzinfo=timezone.utc):
        raise ValueError('development start differs from preregistration')
    if candles[-1].open_time != datetime(2025,12,31,23,59,tzinfo=timezone.utc):
        raise ValueError('development end differs from preregistration or contains holdout')
    if (train_days,validation_days,step_days) != (365,90,90):
        raise ValueError('unregistered walk-forward boundaries')
    windows=[]
    train_count=train_days*1440; validation_count=validation_days*1440; step_count=step_days*1440
    source=identity(development.identity)
    for cursor in range(0,len(candles)-train_count-validation_count+1,step_count):
        training=candles[cursor:cursor+train_count]
        values=calibrate_candidates(training,training_start=training[0].open_time,
                                   training_end_exclusive=training[-1].open_time+timedelta(minutes=1),
                                   source_identity=source)
        windows.append(tuple(values[h] for h in HORIZONS))
        if progress is not None:
            progress('walk-forward-training', len(windows)-1, windows[-1])
    if len(windows)<3:
        raise ValueError('insufficient complete walk-forward folds')
    values=calibrate_candidates(candles,training_start=candles[0].open_time,
                               training_end_exclusive=candles[-1].open_time+timedelta(minutes=1),
                               source_identity=source)
    windows.append(tuple(values[h] for h in HORIZONS))
    if progress is not None:
        progress('final-pre-holdout-training',len(windows)-1,windows[-1])
    # Stress entry cost remains unpaid; calibrated edge already reserves stress exit cost.
    entry_cost=Decimal('0.005004')
    eligible=tuple(h for i,h in enumerate(HORIZONS)
                   if all(w[i].eligible and w[i].entry_edge>entry_cost for w in windows))
    return CalibrationScreen(source,protocol_identity,tuple(windows),eligible,
                             'ELIGIBLE_FOR_DEVELOPMENT_EVALUATION' if eligible else 'NO_GO_FOR_LIVE')
