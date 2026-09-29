"""Frozen T5 long/cash decisions; model fitting stays outside the domain."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext

from quantos.domain.alpha.contracts import AlphaAction, AlphaDecision
from quantos.domain.common import require_decimal, require_non_empty, require_utc
from quantos.domain.runtime_contracts import arithmetic, validate_account

A = 'sae-tbl-30m-longcash-v1'
B = 'cost-aware-hourly-xgb-v1'
ZERO = Decimal('0')


@dataclass(frozen=True, slots=True)
class T5Prediction:
    timestamp: datetime
    training_end: datetime
    model_version: str
    feature_version: str
    forecast: Decimal | None = None
    probabilities: tuple[Decimal, Decimal, Decimal] | None = None
    training_class_means: tuple[Decimal, Decimal, Decimal] | None = None

    def __post_init__(self):
        require_utc(self.timestamp, 'prediction time')
        require_utc(self.training_end, 'training end')
        if self.training_end > self.timestamp:
            raise ValueError('prediction precedes training completion')
        require_non_empty(self.model_version, 'model version')
        require_non_empty(self.feature_version, 'feature version')
        if self.forecast is not None:
            require_decimal(self.forecast, 'forecast')
            if self.probabilities is not None or self.training_class_means is not None:
                raise ValueError('ambiguous prediction')
        else:
            for values in (self.probabilities, self.training_class_means):
                if type(values) is not tuple or len(values) != 3:
                    raise ValueError('three immutable class values required')
                for value in values:
                    require_decimal(value, 'class value')
            if any(p < 0 or p > 1 for p in self.probabilities):
                raise ValueError('invalid probability')
            with localcontext(arithmetic()):
                if abs(sum(self.probabilities, ZERO)-1) > Decimal('0.00000001'):
                    raise ValueError('probabilities do not sum to one')


def decide(prediction, *, family, timestamp, account, cost_rate):
    """Return decision and gross entry edge with a conservative exit reserve."""
    prediction.__post_init__()
    require_utc(timestamp, 'execution decision time')
    validate_account(account)
    require_decimal(cost_rate, 'cost rate', non_negative=True)
    if timestamp <= prediction.timestamp or account.timestamp > timestamp:
        raise ValueError('prediction/account unavailable at execution decision')
    held = account.balances.get('BTC', ZERO) > 0
    with localcontext(arithmetic()):
        if family == A and prediction.probabilities is not None:
            probs = prediction.probabilities
            desired = probs[2] >= Decimal('.6') and probs[2] > max(probs[:2])
            score = probs[2]
            gross = sum((p*r for p, r in zip(probs, prediction.training_class_means)), ZERO)-cost_rate
            permitted = True
        elif family == B and prediction.forecast is not None:
            score = prediction.forecast
            desired = score > 0
            gross = score-cost_rate
            permitted = abs(score) > 2*cost_rate*abs(int(desired)-int(held))
        else:
            raise ValueError('unregistered family or incompatible prediction')
    action, reason = AlphaAction.HOLD, 'already positioned; no pyramiding'
    if desired != held:
        if not permitted:
            reason = 'cost-aware position-change threshold'
        elif desired and account.balances.get('ETH', ZERO) > 0:
            reason = 'other spot position blocks entry'
        else:
            action = AlphaAction.BUY if desired else AlphaAction.SELL
            reason = 'preregistered model target LONG' if desired else 'preregistered model target CASH'
    decision = AlphaDecision(timestamp, 'BTCUSDT', family, prediction.model_version,
                             prediction.feature_version, action, reason,
                             'LONG' if desired else 'CASH', score)
    return decision, gross
