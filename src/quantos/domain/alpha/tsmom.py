"""Preregistered deterministic long/cash scoring and training-only entry evidence."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from quantos.domain.alpha.contracts import AlphaAction, AlphaDecision
from quantos.domain.alpha.context import AlphaDecisionContext
from quantos.domain.common import require_utc, require_non_empty, require_decimal
from quantos.domain.features.daily import DailyFeatureState, HORIZONS, TSMOM_FEATURE_VERSION
from quantos.domain.runtime_contracts import arithmetic, identity

MODEL_VERSION = "deterministic-tsmom-no-ml-v1"
STRATEGY_VERSION = "published-tsmom-v1"
ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class EntryEpisode:
    entry_time: datetime
    exit_time: datetime
    entry_close: Decimal
    exit_close: Decimal
    gross_return: Decimal


@dataclass(frozen=True, slots=True)
class EdgeCalibration:
    horizon: int
    training_start: datetime
    training_end_exclusive: datetime
    source_identity: str
    input_chain: str
    episodes: tuple[EntryEpisode, ...]
    mean: Decimal
    dispersion: Decimal
    lower_estimate: Decimal
    exit_cost_reserve: Decimal
    entry_edge: Decimal
    minimum_samples: int = 30
    version: str = "tsmom-entry-calibration-v1"

    @property
    def artifact_id(self):
        return identity(self)

    @property
    def eligible(self):
        return len(self.episodes) >= self.minimum_samples and self.entry_edge > 0

    def __post_init__(self):
        if type(self.horizon) is not int or self.horizon not in HORIZONS:
            raise ValueError("unregistered momentum horizon")
        require_utc(self.training_start, "training start")
        require_utc(self.training_end_exclusive, "training end")
        if self.training_start >= self.training_end_exclusive:
            raise ValueError("invalid calibration window")
        require_non_empty(self.source_identity, "source identity")
        require_non_empty(self.input_chain, "input chain")
        if self.minimum_samples != 30 or self.version != "tsmom-entry-calibration-v1":
            raise ValueError("incompatible calibration policy")
        if type(self.episodes) is not tuple:
            raise ValueError("immutable episodes required")
        previous = None
        with localcontext(arithmetic()):
            for item in self.episodes:
                for name in ("entry_close", "exit_close", "gross_return"):
                    require_decimal(getattr(item, name), name)
                require_utc(item.entry_time, "entry time")
                require_utc(item.exit_time, "exit time")
                if not self.training_start <= item.entry_time < item.exit_time <= self.training_end_exclusive:
                    raise ValueError("calibration episode outside training")
                if previous is not None and item.entry_time <= previous:
                    raise ValueError("overlapping calibration episodes")
                if item.entry_close <= 0 or item.exit_close <= 0 or item.gross_return != item.exit_close/item.entry_close-1:
                    raise ValueError("invalid gross entry return")
                previous = item.exit_time
            mean, sd, lower = _statistics(tuple(item.gross_return for item in self.episodes))
            if (self.mean, self.dispersion, self.lower_estimate) != (mean, sd, lower):
                raise ValueError("calibration statistics differ from completed episodes")
            if self.exit_cost_reserve != Decimal("0.005004"):
                raise ValueError("calibration requires frozen stressed exit costs")
            if self.entry_edge != (1+lower)*(1-self.exit_cost_reserve)-1:
                raise ValueError("per-entry edge does not reserve exit costs")


def _statistics(returns):
    with localcontext(arithmetic()):
        n = len(returns)
        if not n:
            return ZERO, ZERO, ZERO
        mean = sum(returns,ZERO)/n
        sd = (sum(((r-mean)**2 for r in returns),ZERO)/(n-1)).sqrt() if n>1 else ZERO
        lower = mean-Decimal(19).sqrt()*sd/Decimal(n).sqrt()
        return mean, sd, lower


def calibrate_candidates(candles, *, training_start, training_end_exclusive, source_identity):
    """One causal minute pass for all preregistered labels; never an account simulator."""
    state = DailyFeatureState("BTCUSDT")
    entries = {h: None for h in HORIZONS}
    episodes = {h: [] for h in HORIZONS}
    first = last = None
    for candle in candles:
        if not training_start <= candle.open_time < training_end_exclusive:
            raise ValueError("calibration received a candle outside training")
        if first is None:
            first = candle
        last = candle
        state = state.advance(candle, decision_time=candle.close_time)
        if state.minutes_in_day != 1440:
            continue
        feature = state.feature()
        for horizon in HORIZONS:
            if len(state.daily) <= horizon:
                continue
            score = feature.values[f"momentum_{horizon}d"]
            entry = entries[horizon]
            if score > 0 and entry is None:
                entries[horizon] = candle
            elif score <= 0 and entry is not None:
                with localcontext(arithmetic()):
                    episodes[horizon].append(EntryEpisode(entry.close_time,candle.close_time,entry.close,candle.close,
                                                         candle.close/entry.close-1))
                entries[horizon] = None
    if first is None:
        raise ValueError("empty training input")
    from datetime import timedelta
    if first.open_time != training_start or last.open_time+timedelta(minutes=1) != training_end_exclusive:
        raise ValueError("incomplete calibration interval")
    reserve = Decimal("0.005004")
    result = {}
    for horizon in HORIZONS:
        mean, sd, lower = _statistics(tuple(e.gross_return for e in episodes[horizon]))
        with localcontext(arithmetic()):
            edge = (1+lower)*(1-reserve)-1
        result[horizon] = EdgeCalibration(horizon,training_start,training_end_exclusive,source_identity,state.chain,
                                          tuple(episodes[horizon]),mean,sd,lower,reserve,edge)
    return result


def calibrate(candles, *, horizon, training_start, training_end_exclusive, source_identity):
    if type(horizon) is not int or horizon not in HORIZONS:
        raise ValueError("unregistered momentum horizon")
    return calibrate_candidates(candles,training_start=training_start,
        training_end_exclusive=training_end_exclusive,source_identity=source_identity)[horizon]


@dataclass(frozen=True, slots=True)
class TsmomAlpha:
    calibration: EdgeCalibration

    def __post_init__(self):
        if type(self.calibration) is not EdgeCalibration:
            raise ValueError("explicit calibration artifact required")
        self.calibration.__post_init__()

    def decide(self, feature, context: AlphaDecisionContext):
        context.__post_init__()
        if feature.feature_version != TSMOM_FEATURE_VERSION or feature.timestamp != context.timestamp:
            raise ValueError("incompatible TSMOM feature/context")
        if feature.timestamp < self.calibration.training_end_exclusive:
            raise ValueError("calibration is unavailable at decision time")
        horizon=self.calibration.horizon
        keys={"decision_day_close", *(f"ready_{h}d" for h in HORIZONS), *(f"momentum_{h}d" for h in HORIZONS)}
        if set(feature.values) != keys:
            raise ValueError("invalid TSMOM feature schema")
        for key in ("decision_day_close", *(f"ready_{h}d" for h in HORIZONS)):
            if feature.values[key] not in (ZERO, Decimal(1)):
                raise ValueError("invalid TSMOM readiness flag")
        score=feature.values[f"momentum_{horizon}d"]
        ready=feature.values[f"ready_{horizon}d"] == 1
        desired="LONG" if ready and score>0 else "CASH" if ready else "WARMUP"
        action=AlphaAction.HOLD
        reason="non-decision minute"
        held=context.account.balances.get("BTC",ZERO)>0
        if feature.symbol != "BTCUSDT":
            reason="initial strategy deployment is BTCUSDT only"
        elif not ready:
            reason="insufficient completed UTC-day history"
        elif feature.values["decision_day_close"] == 1:
            if desired=="CASH" and held:
                action,reason=AlphaAction.SELL,"nonpositive momentum: reduce owned BTC exposure"
            elif desired=="LONG" and not held:
                if context.account.balances.get("ETH",ZERO)>0:
                    reason="existing ETH exposure blocks BTC entry"
                elif not self.calibration.eligible:
                    reason="NO_GO_FOR_LIVE: insufficient calibrated entry edge/sample"
                else:
                    action,reason=AlphaAction.BUY,"positive momentum and frozen calibrated entry edge"
            else:
                reason="already in desired position; no pyramiding"
        decision=AlphaDecision(feature.timestamp,feature.symbol,STRATEGY_VERSION,
            MODEL_VERSION,feature.feature_version,action,
            reason+"; calibration="+self.calibration.artifact_id,desired,score)
        return decision
