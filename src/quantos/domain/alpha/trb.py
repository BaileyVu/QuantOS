"""The sole T4R breakout family: strict prior-range breaks with owned-position persistence."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from quantos.domain.alpha import AlphaAction, AlphaDecision
from quantos.domain.alpha.context import AlphaDecisionContext
from quantos.domain.common import require_utc
from quantos.domain.features.daily import TRB_FEATURE_VERSION, TRB_HORIZONS


@dataclass(frozen=True, slots=True)
class TrbAlpha:
    horizon: int
    calibration_id: str
    available_at: datetime
    eligible: bool

    def __post_init__(self):
        if type(self.horizon) is not int or self.horizon not in TRB_HORIZONS:raise ValueError('unregistered range')
        require_utc(self.available_at,'calibration availability')
        if len(self.calibration_id)!=64 or type(self.eligible) is not bool:raise ValueError('invalid calibration binding')

    def decide(self,feature,context: AlphaDecisionContext):
        self.__post_init__();context.__post_init__()
        if feature.feature_version!=TRB_FEATURE_VERSION or feature.timestamp!=context.timestamp or feature.timestamp<self.available_at:
            raise ValueError('unavailable calibration or incompatible feature context')
        keys={'decision_day_close','daily_close',*(f'{name}_{h}d' for h in TRB_HORIZONS for name in ('ready','resistance','support'))}
        if set(feature.values)!=keys:raise ValueError('invalid TRB feature schema')
        v=feature.values
        for name in ('decision_day_close',*(f'ready_{h}d' for h in TRB_HORIZONS)):
            if v[name] not in (0,1):raise ValueError('invalid readiness')
        held=context.account.balances.get('BTC',Decimal(0))>0
        action=AlphaAction.HOLD;reason='non-decision minute'
        if feature.symbol!='BTCUSDT':reason='BTC only'
        elif not v[f'ready_{self.horizon}d']:reason='warmup'
        elif v['decision_day_close']:
            if not 0<v[f'support_{self.horizon}d']<=v[f'resistance_{self.horizon}d']:raise ValueError('invalid prior range')
            if held and v['daily_close']<v[f'support_{self.horizon}d']:
                action=AlphaAction.SELL;reason='strict downside breakout; owned BTC to cash'
            elif not held and v['daily_close']>v[f'resistance_{self.horizon}d'] and not context.account.positions:
                if self.eligible:action=AlphaAction.BUY;reason='strict upside breakout; calibrated entry'
                else:reason='NO_GO_ALPHA: calibration ineligible'
            else:reason='persist current position; no pyramiding'
        return AlphaDecision(feature.timestamp,feature.symbol,f'btc-trading-range-breakout-v1-{self.horizon}d',
            'deterministic-trb-no-ml-v1',feature.feature_version,action,reason+'; calibration='+self.calibration_id,
            'LONG' if held else 'CASH',Decimal(0))
