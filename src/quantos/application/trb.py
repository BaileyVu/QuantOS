"""Preregistered complete-episode TRB calibration; no holdout or exchange access."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from random import Random
from quantos.domain.features.daily import TRB_HORIZONS
from quantos.domain.runtime_contracts import arithmetic,identity
from quantos.domain.evaluation import AlphaEvaluation


@dataclass(frozen=True,slots=True)
class TrbCalibration:
    horizon: int
    source_identity: str
    available_at: object
    episodes: tuple
    censored_entry: object
    mean: Decimal | None
    lower: Decimal | None
    entry_edge: Decimal | None
    minimum_samples: int = 12
    simulations: int = 10000
    seed: int = 240929
    entry_cost: Decimal = Decimal("0.005004")

    @property
    def eligible(self):return len(self.episodes)>=self.minimum_samples and self.entry_edge is not None and self.entry_edge>self.entry_cost
    @property
    def artifact_id(self):return identity(self)


def calibrate_trb(daily,*,horizon,source_identity,end_exclusive, minimum_samples=12, simulations=10000, seed=240929, mean_block=2, quantile_denominator=60, fee=Decimal("0.001"), stress_slippage=Decimal("0.004")):
    if horizon not in TRB_HORIZONS:raise ValueError('unregistered horizon')
    if any(d.day+timedelta(days=1)>end_exclusive for d in daily):raise ValueError('future calibration label')
    entry=None;episodes=[]
    with localcontext(arithmetic()):
        for index in range(horizon,len(daily)):
            current=daily[index];prior=daily[index-horizon:index]
            if entry is None and current.close>max(d.close for d in prior):entry=current
            elif entry is not None and current.close<min(d.close for d in prior):
                episodes.append((entry.day,current.day,current.close/entry.close-1));entry=None
        values=tuple(e[2] for e in episodes)
        mean=sum(values,Decimal(0))/len(values) if values else None
        lower=None;edge=None
        if values:
            random=Random(seed);means=[];n=len(values)
            for _ in range(simulations):
                cursor=random.randrange(n);total=Decimal(0)
                for i in range(n):
                    if i and random.randrange(mean_block)==0:cursor=random.randrange(n)
                    elif i:cursor=(cursor+1)%n
                    total+=values[cursor]
                means.append(total/n)
            lower=sorted(means)[simulations//quantile_denominator]
            edge=(1+lower)*(1-stress_slippage)*(1-fee)-1
        return TrbCalibration(horizon,source_identity,end_exclusive,tuple(episodes),entry,mean,lower,edge,minimum_samples,simulations,seed,fee+stress_slippage+fee*stress_slippage)


@dataclass(frozen=True,slots=True)
class TrbDecisionFunction:
    alpha: object
    calibration: TrbCalibration

    def __call__(self,feature,context):
        if self.alpha.calibration_id!=self.calibration.artifact_id or not self.calibration.eligible:
            raise ValueError('entry evidence not eligible or changed')
        return AlphaEvaluation(self.alpha.decide(feature,context),self.calibration.entry_edge)
