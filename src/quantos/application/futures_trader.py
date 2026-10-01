"""Autonomous multi-timeframe BTCUSDT Futures paper trader and replay."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Iterable

from quantos.domain.alpha.futures import (
    Direction, MarketRegime, PRODUCTION_STRATEGY_IDS, classify_regime,
    evaluate_strategies_with_diagnostics, select_candidate, TradeCandidate,
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
    assess_candidate_economics,
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
    entry_timeframes: tuple[str, ...] = ("1m", "3m", "5m", "15m")
    context_timeframes: tuple[str, ...] = ("15m", "30m", "1h")
    enabled_strategies: tuple[str, ...] = PRODUCTION_STRATEGY_IDS

    def __post_init__(self) -> None:
        if self.symbol != "BTCUSDT":
            raise ValueError("V1 supports BTCUSDT only")
        if self.starting_equity <= 0 or self.lookback < 30:
            raise ValueError("invalid Futures trader configuration")
        if not self.timeframes or len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("enabled timeframes must be non-empty and unique")
        if set(self.timeframes) - set(SUPPORTED_SIGNAL_TIMEFRAMES):
            raise ValueError("unsupported signal timeframe")
        enabled_entries = tuple(
            value for value in self.entry_timeframes if value in self.timeframes
        )
        enabled_context = tuple(
            value for value in self.context_timeframes if value in self.timeframes
        )
        if not enabled_entries:
            enabled_entries = self.timeframes
        object.__setattr__(self, "entry_timeframes", enabled_entries)
        object.__setattr__(self, "context_timeframes", enabled_context)
        if self.execution.slippage_rate > self.risk.maximum_slippage_rate:
            raise ValueError("paper slippage exceeds Risk bound")
        if self.execution.taker_fee_rate > self.risk.maximum_fee_rate:
            raise ValueError("paper fee exceeds Risk bound")
        if (not self.enabled_strategies
                or len(set(self.enabled_strategies)) != len(self.enabled_strategies)
                or set(self.enabled_strategies) - set(PRODUCTION_STRATEGY_IDS)):
            raise ValueError("enabled strategies must be a supported non-empty subset")


class FuturesTraderError(RuntimeError):
    pass


FUNNEL_STAGES = (
    "raw_signals_generated",
    "regime_compatible",
    "economic_filter_pass",
    "minimum_executable_risk_pass",
    "leverage_filter_pass",
    "decision_reached",
    "selected",
    "risk_approved",
    "trades",
)


def _funnel_bucket() -> dict:
    return {
        **{stage: 0 for stage in FUNNEL_STAGES},
        "pnl": Decimal(0),
        "rejection_reasons": defaultdict(int),
    }


def filter_economically_feasible_candidates(
    signals,
    account: FuturesAccountState,
    rules: FuturesSymbolRules,
    policy: FuturesRiskPolicy,
):
    """Return executable signals and auditable economics before selection."""
    economics = {
        item.signal_id: assess_candidate_economics(
            TradeCandidate(item), account, rules, policy
        )
        for item in signals
    }
    return (
        tuple(item for item in signals if economics[item.signal_id].approved),
        economics,
    )


def _value(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _value(v) for k, v in value.items()}
    return value


class AutonomousFuturesPaperTrader:
    def __init__(self, config: FuturesTraderConfig, rules: FuturesSymbolRules) -> None:
        self.config = config
        self.rules = rules
        self.execution = FuturesPaperExecution(
            config.starting_equity, config.execution, rules.price_tick
        )
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
        self.economic_rejections: dict[str, int] = defaultdict(int)
        self.economic_rejections_per_timeframe = {
            timeframe: defaultdict(int) for timeframe in config.timeframes
        }
        self.strategy_funnel = {
            strategy: {
                **_funnel_bucket(),
                "raw_signals_by_timeframe": {
                    timeframe: 0 for timeframe in config.entry_timeframes
                },
                "raw_signals_by_regime": {
                    regime.value: 0 for regime in MarketRegime
                },
            }
            for strategy in PRODUCTION_STRATEGY_IDS
        }
        self.timeframe_funnel = {
            timeframe: _funnel_bucket()
            for timeframe in config.entry_timeframes
        }
        self.regime_funnel = {
            regime.value: _funnel_bucket() for regime in MarketRegime
        }
        self.regime_evaluation_periods = {
            regime.value: 0 for regime in MarketRegime
        }
        self.regime_evaluation_periods_by_timeframe = {
            timeframe: {regime.value: 0 for regime in MarketRegime}
            for timeframe in config.timeframes
        }
        self.funnel_rejection_matrix = defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(lambda: defaultdict(int))
            )
        )
        self.projected_opportunities = {
            strategy: {
                "gross_projected_r": [],
                "net_projected_r": [],
                "first_structure_r": [],
                "continuation_objective_r": [],
                "reward_to_cost": [],
            }
            for strategy in PRODUCTION_STRATEGY_IDS
        }

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
        self._increment_funnel(
            trade.strategy_id, trade.timeframe, trade.regime, "pnl", trade.pnl
        )
        self.breaker_state = record_closed_trade(
            self.breaker_state, trade.pnl, trade.closed_at, self.config.risk
        )
        self._observe_daily_loss(self.execution.balance)

    def _higher_context(self, timeframe: str):
        base_minutes = timeframe_minutes(timeframe)
        return tuple(
            (context_timeframe, self.latest_regimes[context_timeframe].regime)
            for context_timeframe in sorted(self.latest_regimes, key=timeframe_minutes)
            if (context_timeframe in self.config.context_timeframes
                and timeframe_minutes(context_timeframe) > base_minutes)
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

    def _increment_funnel(
        self,
        strategy: str,
        timeframe: str,
        regime: MarketRegime,
        field: str,
        amount=1,
    ) -> None:
        if strategy not in self.strategy_funnel:
            self.strategy_funnel[strategy] = {
                **_funnel_bucket(),
                "raw_signals_by_timeframe": {
                    value: 0 for value in self.config.entry_timeframes
                },
                "raw_signals_by_regime": {
                    value.value: 0 for value in MarketRegime
                },
            }
        strategy_bucket = self.strategy_funnel[strategy]
        timeframe_bucket = self.timeframe_funnel[timeframe]
        regime_bucket = self.regime_funnel[regime.value]
        for bucket in (strategy_bucket, timeframe_bucket, regime_bucket):
            bucket[field] += amount
        if field == "raw_signals_generated":
            strategy_bucket["raw_signals_by_timeframe"][timeframe] += amount
            strategy_bucket["raw_signals_by_regime"][regime.value] += amount

    def _record_funnel_rejection(
        self,
        strategy: str,
        timeframe: str,
        regime: MarketRegime,
        reason: str,
    ) -> None:
        if strategy not in self.strategy_funnel:
            self._increment_funnel(
                strategy, timeframe, regime, "raw_signals_generated", 0
            )
        for bucket in (
            self.strategy_funnel[strategy],
            self.timeframe_funnel[timeframe],
            self.regime_funnel[regime.value],
        ):
            bucket["rejection_reasons"][reason] += 1
        self.funnel_rejection_matrix[strategy][timeframe][
            regime.value
        ][reason] += 1

    def _account_state(self, price: Decimal) -> FuturesAccountState:
        equity = self.execution.marked_equity(price)
        self._observe_daily_loss(equity)
        return FuturesAccountState(
            equity=equity,
            day_start_equity=self.day_start_equity,
            consecutive_losses=self.breaker_state.consecutive_losses,
            consecutive_loss_breaker_active=self.breaker_state.active,
            has_position=self.execution.position is not None,
            reconciled=True,
        )

    def on_candle(self, candle: Candle, *, allow_new_entries: bool = True) -> dict:
        if candle.symbol != self.config.symbol or candle.interval != "1m":
            raise FuturesTraderError("unexpected market")
        if self.last_candle and candle.open_time == self.last_candle.open_time:
            return {
                "timestamp": candle.close_time.isoformat(),
                "direction": "HOLD",
                "reason": "duplicate_candle_ignored",
                "completed_timeframes": [],
            }
        if self.last_candle and candle.open_time < self.last_candle.open_time:
            raise FuturesTraderError("candles must be strictly chronological")
        self.breaker_state = advance_consecutive_loss_breaker(
            self.breaker_state, candle.close_time, self.config.risk
        )
        self._roll_utc_day(candle)
        audit_start = len(self.execution.audit_events)

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
                self.regime_evaluation_periods[regime.regime.value] += 1
                self.regime_evaluation_periods_by_timeframe[
                    timeframe
                ][regime.regime.value] += 1

        if closed is None and self.execution.position is not None:
            managed_timeframe = self.execution.position.timeframe
            if (managed_timeframe in completed
                    and managed_timeframe in self.latest_regimes):
                state = self.latest_regimes[managed_timeframe]
                closed = self.execution.manage_completed_candle(
                    completed[managed_timeframe],
                    state.atr,
                    state.regime,
                    self._higher_context(managed_timeframe),
                    tuple(self.histories[managed_timeframe]),
                )
                if closed is not None:
                    self._record_closed_trade(closed)

        signals = []
        strategy_evaluations = []
        for timeframe, regime in ready:
            if timeframe not in self.config.entry_timeframes:
                continue
            timeframe_signals, evaluations = evaluate_strategies_with_diagnostics(
                self.histories[timeframe],
                regime,
                timeframe=timeframe,
                higher_context=self._higher_context(timeframe),
            )
            for evaluation in evaluations:
                self._increment_funnel(
                    evaluation.strategy_id,
                    evaluation.timeframe,
                    evaluation.detected_regime,
                    "raw_signals_generated",
                )
                if evaluation.regime_compatible:
                    self._increment_funnel(
                        evaluation.strategy_id,
                        evaluation.timeframe,
                        evaluation.detected_regime,
                        "regime_compatible",
                    )
                if evaluation.rejection_reason is not None:
                    self._record_funnel_rejection(
                        evaluation.strategy_id,
                        evaluation.timeframe,
                        evaluation.detected_regime,
                        evaluation.rejection_reason,
                    )
            strategy_evaluations.extend(evaluations)
            self.candidate_count[timeframe] += len(timeframe_signals)
            signals.extend(timeframe_signals)

        if ready:
            enabled_signals = []
            for item in signals:
                if (item.strategy_id not in PRODUCTION_STRATEGY_IDS
                        or item.strategy_id in self.config.enabled_strategies):
                    enabled_signals.append(item)
                    continue
                self.rejections["strategy_disabled"] += 1
                self.rejected_candidate_count[item.timeframe] += 1
                self._record_funnel_rejection(
                    item.strategy_id, item.timeframe, item.regime,
                    "strategy_disabled",
                )
            account = self._account_state(candle.close)
            feasible_signals, economics = filter_economically_feasible_candidates(
                enabled_signals, account, self.rules, self.config.risk
            )
            for item in enabled_signals:
                result = economics[item.signal_id]
                dimensions = (item.strategy_id, item.timeframe, item.regime)
                opportunity = self.projected_opportunities.setdefault(
                    item.strategy_id,
                    {
                        "gross_projected_r": [],
                        "net_projected_r": [],
                        "first_structure_r": [],
                        "continuation_objective_r": [],
                        "reward_to_cost": [],
                    },
                )
                opportunity["gross_projected_r"].append(
                    result.gross_projected_r
                )
                opportunity["net_projected_r"].append(
                    result.net_projected_r
                )
                opportunity["first_structure_r"].append(
                    result.first_structure_r
                )
                opportunity["continuation_objective_r"].append(
                    result.continuation_objective_r
                )
                opportunity["reward_to_cost"].append(
                    result.reward_to_execution_cost_multiple
                )
                failed_reasons = set()
                economic_pass = (
                    result.passes_exchange_filter
                    and result.passes_cost_filter
                    and result.passes_expected_movement_filter
                    and result.passes_net_reward_risk_filter
                )
                if economic_pass:
                    self._increment_funnel(
                        *dimensions, "economic_filter_pass"
                    )
                elif result.passes_exchange_filter:
                    if not result.passes_cost_filter:
                        failed_reasons.add("cost")
                    if not result.passes_expected_movement_filter:
                        failed_reasons.add("expected_movement_cost")
                    if not result.passes_net_reward_risk_filter:
                        failed_reasons.add("net_reward_risk")
                if (
                    economic_pass
                    and result.passes_minimum_executable_risk_filter
                ):
                    self._increment_funnel(
                        *dimensions, "minimum_executable_risk_pass"
                    )
                elif not result.passes_minimum_executable_risk_filter:
                    failed_reasons.add("minimum_executable_risk")
                if (
                    economic_pass
                    and result.passes_minimum_executable_risk_filter
                    and result.passes_leverage_filter
                ):
                    self._increment_funnel(
                        *dimensions, "leverage_filter_pass"
                    )
                elif not result.passes_leverage_filter:
                    failed_reasons.add("leverage")
                if not result.passes_exchange_filter:
                    failed_reasons.add("exchange_filters")
                if result.approved:
                    self._increment_funnel(*dimensions, "decision_reached")
                for reason in failed_reasons:
                    self._record_funnel_rejection(*dimensions, reason)
                if not result.approved:
                    category = result.rejection_category or "unknown"
                    self.economic_rejections[category] += 1
                    self.economic_rejections_per_timeframe[item.timeframe][category] += 1
                    self.rejected_candidate_count[item.timeframe] += 1
                    self.rejections[result.reason] += 1
            if not allow_new_entries:
                for item in feasible_signals:
                    self.rejected_candidate_count[item.timeframe] += 1
                    self._record_funnel_rejection(
                        item.strategy_id, item.timeframe, item.regime,
                        "runtime_entry_disabled",
                    )
                feasible_signals = ()
            selection = select_candidate(feasible_signals)
            record.update(
                direction=selection.direction.value,
                reason=selection.reason,
                regimes={
                    timeframe: self.latest_regimes[timeframe].regime.value
                    for timeframe, _ in ready
                },
                signals=[_value(asdict(item)) for item in signals],
                enabled_signals=[_value(asdict(item)) for item in enabled_signals],
                strategy_evaluations=[
                    _value(asdict(item)) for item in strategy_evaluations
                ],
                candidate_economics={
                    signal_id: _value(asdict(result))
                    for signal_id, result in economics.items()
                },
            )
            selected_id = (
                selection.candidate.signal.signal_id
                if selection.candidate is not None else None
            )
            for item in feasible_signals:
                if item.signal_id != selected_id:
                    self.rejected_candidate_count[item.timeframe] += 1
                    reason = (
                        "directional_conflict"
                        if selection.candidate is None
                        else "outranked_by_selected_candidate"
                    )
                    self._record_funnel_rejection(
                        item.strategy_id, item.timeframe, item.regime, reason
                    )
            if not signals:
                self.no_valid_signal_count += 1
            if not allow_new_entries:
                record["reason"] = "runtime_entry_disabled"
            if selection.candidate is not None:
                selected_signal = selection.candidate.signal
                signal_timeframe = selected_signal.timeframe
                selected_dimensions = (
                    selected_signal.strategy_id,
                    selected_signal.timeframe,
                    selected_signal.regime,
                )
                self._increment_funnel(*selected_dimensions, "selected")
                if closed is not None:
                    record["reason"] = "same-candle exit prevents causal re-entry"
                    self.rejected_candidate_count[signal_timeframe] += 1
                    self._record_funnel_rejection(
                        *selected_dimensions, "same_candle_exit"
                    )
                else:
                    account = self._account_state(candle.close)
                    risk = evaluate_futures_risk(
                        selection.candidate, account, self.rules, self.config.risk
                    )
                    record["risk"] = _value(asdict(risk))
                    if risk.approved:
                        self._increment_funnel(
                            *selected_dimensions, "risk_approved"
                        )
                        position = self.execution.open(selection.candidate, risk)
                        self._increment_funnel(*selected_dimensions, "trades")
                        self.selected_trade_count[signal_timeframe] += 1
                        record["execution"] = _value(asdict(position))
                    else:
                        self.rejections[risk.reason] += 1
                        self.rejected_candidate_count[signal_timeframe] += 1
                        self._record_funnel_rejection(
                            *selected_dimensions, risk.reason
                        )
                        self._classify_rejection(risk.reason)
                        if risk.reason == "consecutive-loss circuit breaker":
                            self.breaker_state = record_breaker_blocked_candidate(
                                self.breaker_state
                            )
        if closed is not None:
            record["closed_trade"] = _value(asdict(closed))
        audit = self.execution.audit_events[audit_start:]
        if audit:
            record["position_management_events"] = _value(audit)
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
        net_wins = sum((trade.pnl for trade in wins), Decimal(0))
        net_losses = sum((-trade.pnl for trade in losses), Decimal(0))
        gross_profit = sum(
            (trade.gross_pnl for trade in trades if trade.gross_pnl > 0),
            Decimal(0),
        )
        gross_loss = sum(
            (-trade.gross_pnl for trade in trades if trade.gross_pnl < 0),
            Decimal(0),
        )
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
        exit_reason = defaultdict(lambda: {
            "trades": 0, "gross_pnl": Decimal(0), "net_pnl": Decimal(0)
        })
        leverage_distribution = defaultdict(int)
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
            exit_bucket = exit_reason[trade.exit_reason.value]
            exit_bucket["trades"] += 1
            exit_bucket["gross_pnl"] += trade.gross_pnl
            exit_bucket["net_pnl"] += trade.pnl
            leverage_distribution[str(trade.leverage)] += 1

        count = len(trades)
        gross_pnl = sum((trade.gross_pnl for trade in trades), Decimal(0))
        realized_net_pnl = sum((trade.pnl for trade in trades), Decimal(0))
        ending_equity = (
            self.execution.marked_equity(self.last_candle.close)
            if self.last_candle is not None else self.execution.balance
        )
        total_costs = self.execution.total_fees + self.execution.total_slippage_cost
        average_r_winner = (
            sum((trade.realized_r for trade in wins), Decimal(0)) / Decimal(len(wins))
            if wins else Decimal(0)
        )
        average_r_loser = (
            sum((trade.realized_r for trade in losses), Decimal(0)) / Decimal(len(losses))
            if losses else Decimal(0)
        )
        mfe_values = [trade.mfe_r for trade in trades]
        mae_values = [trade.mae_r for trade in trades]
        strategy_diagnostics = {}

        def percentiles(values) -> dict:
            ordered = sorted(values)
            if not ordered:
                return {
                    key: None for key in (
                        "min", "p25", "median", "p75", "p90", "max"
                    )
                }

            def percentile(fraction: Decimal) -> Decimal:
                position = Decimal(len(ordered) - 1) * fraction
                lower = int(position)
                upper = min(lower + 1, len(ordered) - 1)
                weight = position - Decimal(lower)
                return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

            return {
                "min": ordered[0],
                "p25": percentile(Decimal(".25")),
                "median": percentile(Decimal(".5")),
                "p75": percentile(Decimal(".75")),
                "p90": percentile(Decimal(".9")),
                "max": ordered[-1],
            }

        projected_opportunity_percentiles = {
            strategy: {
                metric: percentiles(values)
                for metric, values in metrics.items()
            }
            for strategy, metrics in self.projected_opportunities.items()
        }
        for strategy_id in PRODUCTION_STRATEGY_IDS:
            strategy_trades = [
                trade for trade in trades if trade.strategy_id == strategy_id
            ]
            strategy_count = len(strategy_trades)
            denominator = Decimal(strategy_count) if strategy_count else Decimal(1)
            strategy_diagnostics[strategy_id] = {
                "trades": strategy_count,
                "average_mfe_r": (
                    sum(
                        (trade.mfe_r for trade in strategy_trades), Decimal(0)
                    ) / denominator
                ),
                "average_mfe_price": (
                    sum(
                        (trade.mfe_price for trade in strategy_trades),
                        Decimal(0),
                    ) / denominator
                ),
                "average_mae_r": (
                    sum(
                        (trade.mae_r for trade in strategy_trades), Decimal(0)
                    ) / denominator
                ),
                "average_mae_price": (
                    sum(
                        (trade.mae_price for trade in strategy_trades),
                        Decimal(0),
                    ) / denominator
                ),
                "initial_stop_percentage": (
                    Decimal(sum(
                        trade.exit_reason.value == "INITIAL_STOP"
                        for trade in strategy_trades
                    )) / denominator
                ),
                "reached_0_5r_percentage": (
                    Decimal(sum(
                        trade.mfe_r >= Decimal(".5")
                        for trade in strategy_trades
                    )) / denominator
                ),
                "reached_1r_percentage": (
                    Decimal(sum(
                        trade.mfe_r >= Decimal(1)
                        for trade in strategy_trades
                    )) / denominator
                ),
                "reached_2r_percentage": (
                    Decimal(sum(
                        trade.mfe_r >= Decimal(2)
                        for trade in strategy_trades
                    )) / denominator
                ),
                "gross_expectancy_before_costs": (
                    sum(
                        (trade.gross_pnl for trade in strategy_trades), Decimal(0)
                    ) / denominator
                ),
                "net_expectancy": (
                    sum(
                        (trade.pnl for trade in strategy_trades), Decimal(0)
                    ) / denominator
                ),
                "average_holding_bars": (
                    sum(
                        (Decimal(trade.holding_bars) for trade in strategy_trades),
                        Decimal(0),
                    ) / denominator
                ),
                "execution_cost_per_trade": (
                    sum(
                        (
                            trade.total_execution_costs
                            for trade in strategy_trades
                        ),
                        Decimal(0),
                    ) / denominator
                ),
            }
        result = {
            "starting_equity": self.config.starting_equity,
            "ending_equity": ending_equity,
            "gross_pnl_before_execution_costs": gross_pnl,
            "net_pnl": ending_equity - self.config.starting_equity,
            "realized_net_pnl": realized_net_pnl,
            "number_of_trades": count,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": Decimal(len(wins)) / Decimal(count) if count else Decimal(0),
            "average_win": net_wins / Decimal(len(wins)) if wins else Decimal(0),
            "average_loss": -(net_losses / Decimal(len(losses))) if losses else Decimal(0),
            "expectancy": (
                sum((trade.pnl for trade in trades), Decimal(0)) / Decimal(count)
                if count else Decimal(0)
            ),
            "profit_factor": net_wins / net_losses if net_losses else None,
            "gross_profit_factor": gross_profit / gross_loss if gross_loss else None,
            "net_profit_factor": net_wins / net_losses if net_losses else None,
            "max_drawdown": drawdown,
            "fees": self.execution.total_fees,
            "maker_fills": self.execution.maker_fills,
            "taker_fills": self.execution.taker_fills,
            "maker_attempts_not_filled": (
                self.execution.maker_attempts_not_filled
            ),
            "fees_by_liquidity_role": {
                "maker": self.execution.maker_fees,
                "taker": self.execution.taker_fees,
            },
            "estimated_slippage_cost": self.execution.total_slippage_cost,
            "spread_cost": None,
            "spread_cost_limitation": (
                "top-of-book spread is unavailable from candle-only inputs"
            ),
            "total_execution_costs": total_costs,
            "execution_costs_as_percent_of_gross_profit": (
                total_costs / gross_profit * Decimal(100)
                if gross_profit else None
            ),
            "average_notional": (
                sum((trade.notional for trade in trades), Decimal(0)) / Decimal(count)
                if count else Decimal(0)
            ),
            "average_leverage": (
                sum((Decimal(trade.leverage) for trade in trades), Decimal(0))
                / Decimal(count) if count else Decimal(0)
            ),
            "leverage_distribution": dict(leverage_distribution),
            "average_r_winner": average_r_winner,
            "average_r_loser": average_r_loser,
            "expectancy_r": (
                sum((trade.realized_r for trade in trades), Decimal(0))
                / Decimal(count) if count else Decimal(0)
            ),
            "mfe_r_summary": {
                "values": mfe_values,
                "average": (
                    sum(mfe_values, Decimal(0)) / Decimal(count)
                    if count else Decimal(0)
                ),
                "maximum": max(mfe_values, default=Decimal(0)),
            },
            "mae_r_summary": {
                "values": mae_values,
                "average": (
                    sum(mae_values, Decimal(0)) / Decimal(count)
                    if count else Decimal(0)
                ),
                "maximum": max(mae_values, default=Decimal(0)),
            },
            "exit_reason_attribution": dict(exit_reason),
            "strategy_diagnostics": strategy_diagnostics,
            "projected_opportunity_percentiles": (
                projected_opportunity_percentiles
            ),
            "candidate_funnel_stage_semantics": {
                "raw_signals_generated": (
                    "technical trigger fired before regime compatibility"
                ),
                "regime_compatible": (
                    "raw trigger matched the strategy's required regime"
                ),
                "economic_filter_pass": (
                    "cost, expected-movement, and net reward:risk filters passed"
                ),
                "minimum_executable_risk_pass": (
                    "cumulative pass through the minimum executable loss budget"
                ),
                "leverage_filter_pass": (
                    "cumulative pass through configured leverage"
                ),
                "decision_reached": (
                    "all entry-economics filters passed and candidate reached selection"
                ),
                "selected": "candidate won deterministic account-level selection",
                "risk_approved": "selected candidate passed final authoritative Risk",
                "trades": "paper position was opened",
                "pnl": "net closed-trade PnL",
            },
            "strategy_candidate_funnel": self.strategy_funnel,
            "timeframe_candidate_funnel": self.timeframe_funnel,
            "regime_candidate_funnel": self.regime_funnel,
            "candidate_funnel_rejections_by_strategy_timeframe_regime": (
                self.funnel_rejection_matrix
            ),
            "regime_evaluation_periods": self.regime_evaluation_periods,
            "regime_evaluation_periods_by_timeframe": (
                self.regime_evaluation_periods_by_timeframe
            ),
            "funding": self.execution.total_funding,
            "funding_limitation": (
                "historical replay requires explicit funding observations; "
                "live paper applies validated public funding history"
            ),
            "strategy_attribution": dict(strategy),
            "regime_attribution": dict(regime),
            "timeframe_attribution": timeframe,
            "candidate_count_per_timeframe": self.candidate_count,
            "selected_trades_per_timeframe": self.selected_trade_count,
            "rejected_candidates_per_timeframe": self.rejected_candidate_count,
            "economic_rejections": dict(self.economic_rejections),
            "economic_rejections_per_timeframe": {
                key: dict(value)
                for key, value in self.economic_rejections_per_timeframe.items()
            },
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
