"""Autonomous multi-timeframe BTCUSDT Futures paper trader and replay."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Iterable

from quantos.domain.alpha.futures import (
    Direction, MarketRegime, classify_regime, evaluate_strategies,
    select_candidate,
)
from quantos.domain.execution.futures_paper import (
    FuturesPaperExecution, FuturesPaperPolicy, PaperTrade,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.market_data.timeframes import (
    MultiTimeframeAggregator, SUPPORTED_SIGNAL_TIMEFRAMES, timeframe_minutes,
)
from quantos.domain.risk.futures import (
    ConsecutiveLossBreakerState, FuturesAccountState, FuturesRiskPolicy,
    advance_consecutive_loss_breaker, breaker_disabled_duration,
    evaluate_futures_risk, record_breaker_blocked_candidate,
    record_closed_trade,
)


@dataclass(frozen=True, slots=True)
class FuturesTraderConfig:
    symbol: str
    starting_equity: Decimal
    lookback: int
    timeframes: tuple[str, ...]
    risk: FuturesRiskPolicy
    execution: FuturesPaperPolicy

    def __post_init__(self) -> None:
        if self.symbol != "BTCUSDT":
            raise ValueError("V1 supports BTCUSDT only")
        if self.starting_equity <= 0 or self.lookback < 30:
            raise ValueError("invalid Futures trader configuration")
        if not self.timeframes or len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("enabled timeframes must be non-empty and unique")
        if set(self.timeframes) - set(SUPPORTED_SIGNAL_TIMEFRAMES):
            raise ValueError("unsupported signal timeframe")
        if self.execution.slippage_rate > self.risk.maximum_slippage_rate:
            raise ValueError("paper slippage exceeds Risk bound")
        if self.execution.taker_fee_rate > self.risk.maximum_fee_rate:
            raise ValueError("paper fee exceeds Risk bound")


class FuturesTraderError(RuntimeError):
    pass


def _value(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _value(v) for k, v in value.items()}
    return value


class AutonomousFuturesPaperTrader:
    def __init__(self, config: FuturesTraderConfig, rules: FuturesSymbolRules) -> None:
        self.config = config
        self.rules = rules
        self.execution = FuturesPaperExecution(config.starting_equity, config.execution)
        self.aggregator = MultiTimeframeAggregator(config.timeframes)
        self.histories = {timeframe: [] for timeframe in config.timeframes}
        self.latest_regimes = {}
        self.equity_curve: list[Decimal] = [config.starting_equity]
        self.decisions: list[dict] = []
        self.rejections: dict[str, int] = defaultdict(int)
        self.breaker_state = ConsecutiveLossBreakerState()
        self.day = None
        self.day_start_equity = config.starting_equity
        self.daily_loss_breaker_trigger_count = 0
        self.daily_loss_active_day = None
        self.last_candle: Candle | None = None
        self.no_valid_signal_count = 0
        self.risk_rejection_count = 0
        self.exchange_rule_rejection_count = 0
        self.position_already_open_rejections = 0
        self.candidate_count = {timeframe: 0 for timeframe in config.timeframes}
        self.selected_trade_count = {timeframe: 0 for timeframe in config.timeframes}
        self.rejected_candidate_count = {timeframe: 0 for timeframe in config.timeframes}

    @property
    def consecutive_losses(self) -> int:
        return self.breaker_state.consecutive_losses

    def _roll_utc_day(self, candle: Candle) -> None:
        current_day = candle.close_time.date()
        if self.day != current_day:
            self.day = current_day
            self.day_start_equity = self.execution.marked_equity(candle.close)
            self.daily_loss_active_day = None

    def _daily_loss_active(self, equity: Decimal) -> bool:
        loss = max(Decimal(0), self.day_start_equity - equity)
        return loss >= self.day_start_equity * self.config.risk.daily_loss_fraction

    def _observe_daily_loss(self, equity: Decimal) -> None:
        if self._daily_loss_active(equity) and self.daily_loss_active_day != self.day:
            self.daily_loss_active_day = self.day
            self.daily_loss_breaker_trigger_count += 1

    def _record_closed_trade(self, trade: PaperTrade) -> None:
        self.breaker_state = record_closed_trade(
            self.breaker_state, trade.pnl, trade.closed_at, self.config.risk
        )
        self._observe_daily_loss(self.execution.balance)

    def _higher_context(self, timeframe: str):
        base_minutes = timeframe_minutes(timeframe)
        return tuple(
            (context_timeframe, self.latest_regimes[context_timeframe].regime)
            for context_timeframe in sorted(self.latest_regimes, key=timeframe_minutes)
            if timeframe_minutes(context_timeframe) > base_minutes
        )

    def _classify_rejection(self, reason: str) -> None:
        self.risk_rejection_count += 1
        if reason == "one-position rule":
            self.position_already_open_rejections += 1
        if any(fragment in reason for fragment in (
            "not TRADING", "LOT_SIZE", "PRICE_FILTER", "minimum notional",
            "rounds to zero", "representable",
        )):
            self.exchange_rule_rejection_count += 1

    def on_candle(self, candle: Candle) -> dict:
        if candle.symbol != self.config.symbol or candle.interval != "1m":
            raise FuturesTraderError("unexpected market")
        if self.last_candle and candle.open_time <= self.last_candle.open_time:
            raise FuturesTraderError("candles must be strictly chronological")
        self.breaker_state = advance_consecutive_loss_breaker(
            self.breaker_state, candle.close_time, self.config.risk
        )
        self._roll_utc_day(candle)

        closed = self.execution.process_candle(candle)
        if closed is not None:
            self._record_closed_trade(closed)
        self.last_candle = candle
        completed = self.aggregator.update(candle)

        record = {
            "timestamp": candle.close_time.isoformat(),
            "direction": "HOLD",
            "reason": "no completed signal timeframe",
            "completed_timeframes": list(completed),
        }

        ready = []
        for timeframe, signal_candle in completed.items():
            history = self.histories[timeframe]
            history.append(signal_candle)
            if len(history) > self.config.lookback:
                history.pop(0)
            if len(history) >= 30:
                regime = classify_regime(history)
                self.latest_regimes[timeframe] = regime
                ready.append((timeframe, regime))

        signals = []
        for timeframe, regime in ready:
            timeframe_signals = evaluate_strategies(
                self.histories[timeframe],
                regime,
                timeframe=timeframe,
                higher_context=self._higher_context(timeframe),
            )
            self.candidate_count[timeframe] += len(timeframe_signals)
            signals.extend(timeframe_signals)

        if ready:
            selection = select_candidate(signals)
            record.update(
                direction=selection.direction.value,
                reason=selection.reason,
                regimes={
                    timeframe: self.latest_regimes[timeframe].regime.value
                    for timeframe, _ in ready
                },
                signals=[_value(asdict(item)) for item in signals],
            )
            selected_id = (
                selection.candidate.signal.signal_id
                if selection.candidate is not None else None
            )
            for item in signals:
                if item.signal_id != selected_id:
                    self.rejected_candidate_count[item.timeframe] += 1
            if not signals:
                self.no_valid_signal_count += 1
            if selection.candidate is not None:
                signal_timeframe = selection.candidate.signal.timeframe
                if closed is not None:
                    record["reason"] = "same-candle exit prevents causal re-entry"
                    self.rejected_candidate_count[signal_timeframe] += 1
                else:
                    equity = self.execution.marked_equity(candle.close)
                    self._observe_daily_loss(equity)
                    account = FuturesAccountState(
                        equity=equity,
                        day_start_equity=self.day_start_equity,
                        consecutive_losses=self.breaker_state.consecutive_losses,
                        consecutive_loss_breaker_active=self.breaker_state.active,
                        has_position=self.execution.position is not None,
                        reconciled=True,
                    )
                    risk = evaluate_futures_risk(
                        selection.candidate, account, self.rules, self.config.risk
                    )
                    record["risk"] = _value(asdict(risk))
                    if risk.approved:
                        position = self.execution.open(selection.candidate, risk)
                        self.selected_trade_count[signal_timeframe] += 1
                        record["execution"] = _value(asdict(position))
                    else:
                        self.rejections[risk.reason] += 1
                        self.rejected_candidate_count[signal_timeframe] += 1
                        self._classify_rejection(risk.reason)
                        if risk.reason == "consecutive-loss circuit breaker":
                            self.breaker_state = record_breaker_blocked_candidate(
                                self.breaker_state
                            )
        if closed is not None:
            record["closed_trade"] = _value(asdict(closed))
        self.equity_curve.append(self.execution.marked_equity(candle.close))
        self.decisions.append(record)
        return record

    def finish(self) -> dict:
        if self.last_candle:
            closed = self.execution.close_at(
                self.last_candle.close, self.last_candle.close_time
            )
            if closed is not None:
                self._record_closed_trade(closed)
                self.equity_curve.append(self.execution.balance)
        return self.metrics()

    def metrics(self) -> dict:
        trades = self.execution.trades
        wins = [trade for trade in trades if trade.pnl > 0]
        losses = [trade for trade in trades if trade.pnl < 0]
        gross_wins = sum((trade.pnl for trade in wins), Decimal(0))
        gross_losses = sum((-trade.pnl for trade in losses), Decimal(0))
        peak = self.equity_curve[0]
        drawdown = Decimal(0)
        for equity in self.equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                drawdown = max(drawdown, (peak - equity) / peak)

        strategy = defaultdict(lambda: {"trades": 0, "pnl": Decimal(0)})
        regime = defaultdict(lambda: {"trades": 0, "pnl": Decimal(0)})
        timeframe = {
            value: {"trades": 0, "pnl": Decimal(0), "wins": 0, "losses": 0}
            for value in self.config.timeframes
        }
        for trade in trades:
            for bucket, key in (
                (strategy, trade.strategy_id),
                (regime, trade.regime.value),
            ):
                bucket[key]["trades"] += 1
                bucket[key]["pnl"] += trade.pnl
            attribution = timeframe[trade.timeframe]
            attribution["trades"] += 1
            attribution["pnl"] += trade.pnl
            if trade.pnl > 0:
                attribution["wins"] += 1
            elif trade.pnl < 0:
                attribution["losses"] += 1

        count = len(trades)
        result = {
            "starting_equity": self.config.starting_equity,
            "ending_equity": self.execution.balance,
            "net_pnl": self.execution.balance - self.config.starting_equity,
            "number_of_trades": count,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": Decimal(len(wins)) / Decimal(count) if count else Decimal(0),
            "average_win": gross_wins / Decimal(len(wins)) if wins else Decimal(0),
            "average_loss": -(gross_losses / Decimal(len(losses))) if losses else Decimal(0),
            "expectancy": (
                sum((trade.pnl for trade in trades), Decimal(0)) / Decimal(count)
                if count else Decimal(0)
            ),
            "profit_factor": gross_wins / gross_losses if gross_losses else None,
            "max_drawdown": drawdown,
            "fees": self.execution.total_fees,
            "funding": None,
            "funding_limitation": "funding is not modeled in the MVP replay",
            "strategy_attribution": dict(strategy),
            "regime_attribution": dict(regime),
            "timeframe_attribution": timeframe,
            "candidate_count_per_timeframe": self.candidate_count,
            "selected_trades_per_timeframe": self.selected_trade_count,
            "rejected_candidates_per_timeframe": self.rejected_candidate_count,
            "no_valid_signal_count": self.no_valid_signal_count,
            "risk_rejection_count": self.risk_rejection_count,
            "position_already_open_rejections": self.position_already_open_rejections,
            "exchange_rule_rejection_count": self.exchange_rule_rejection_count,
            "consecutive_loss_breaker_trigger_count": self.breaker_state.trigger_count,
            "consecutive_loss_breaker_reset_count": self.breaker_state.reset_count,
            "consecutive_loss_breaker_blocked_candidates": (
                self.breaker_state.blocked_candidates
            ),
            "consecutive_loss_breaker_disabled_duration": breaker_disabled_duration(
                self.breaker_state,
                self.last_candle.close_time if self.last_candle else None,
            ),
            "consecutive_loss_breaker_disabled_duration_unit": "seconds",
            "daily_loss_breaker_trigger_count": self.daily_loss_breaker_trigger_count,
            "breaker_state": _value(asdict(self.breaker_state)),
            "rejections": dict(self.rejections),
            "incomplete_aggregation_bucket_count": (
                self.aggregator.incomplete_bucket_count
            ),
        }
        return _value(result)


def run_futures_replay(
    candles: Iterable[Candle],
    config: FuturesTraderConfig,
    rules: FuturesSymbolRules,
) -> tuple[dict, AutonomousFuturesPaperTrader]:
    trader = AutonomousFuturesPaperTrader(config, rules)
    for candle in candles:
        trader.on_candle(candle)
    return trader.finish(), trader
