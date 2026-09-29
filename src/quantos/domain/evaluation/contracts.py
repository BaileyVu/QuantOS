"""Immutable V1 evaluation inputs, evidence, metrics, and robustness reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from types import MappingProxyType
from typing import Callable, Mapping

from quantos.domain.alpha import AlphaDecision
from quantos.domain.common import require_decimal, require_non_empty, require_utc, require_v1_symbol
from quantos.domain.features import FeatureVector
from quantos.domain.market_data import Candle, DatasetIdentity
from quantos.domain.runtime_contracts import arithmetic


@dataclass(frozen=True, slots=True)
class AlphaEvaluation:
    """One caller-supplied Alpha decision plus explicit Risk inputs."""

    decision: AlphaDecision
    gross_edge_rate: Decimal
    volatility: Decimal | None = None

    def __post_init__(self) -> None:
        if type(self.decision) is not AlphaDecision:
            raise ValueError("decision must be an AlphaDecision")
        AlphaDecision.__post_init__(self.decision)
        require_decimal(self.gross_edge_rate, "gross_edge_rate")
        if self.volatility is not None:
            require_decimal(self.volatility, "volatility", non_negative=True)


AlphaDecisionFunction = Callable[[FeatureVector], AlphaEvaluation]


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Reproducible configuration for one use of the historical clock."""

    code_version: str
    alpha_implementation_id: str
    decision_start: datetime | None = None
    decision_end_exclusive: datetime | None = None
    annualization_periods: int = 365 * 24 * 60
    feature_version: str = "candidate-v1"

    def __post_init__(self) -> None:
        require_non_empty(self.code_version, "code_version")
        if self.feature_version not in ("candidate-v1", "tsmom-daily-v1", "trb-daily-v1"):
            raise ValueError("unsupported evaluation feature version")
        require_non_empty(self.alpha_implementation_id, "alpha_implementation_id")
        if self.decision_start is not None:
            require_utc(self.decision_start, "decision_start")
        if self.decision_end_exclusive is not None:
            require_utc(self.decision_end_exclusive, "decision_end_exclusive")
        if (
            self.decision_start is not None
            and self.decision_end_exclusive is not None
            and self.decision_end_exclusive <= self.decision_start
        ):
            raise ValueError("decision_end_exclusive must follow decision_start")
        if type(self.annualization_periods) is not int or self.annualization_periods <= 0:
            raise ValueError("annualization_periods must be a positive integer")


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    gross_exposure: Decimal
    day_start_equity: Decimal
    running_peak_equity: Decimal

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        for name in (
            "equity", "cash", "gross_exposure", "day_start_equity", "running_peak_equity",
        ):
            require_decimal(getattr(self, name), name, non_negative=True)
        if self.running_peak_equity < self.equity:
            raise ValueError("running peak must not be below current equity")


@dataclass(frozen=True, slots=True)
class CompletedTrade:
    timestamp: datetime
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    allocated_entry_fee: Decimal
    exit_fee: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        require_v1_symbol(self.symbol)
        for name in ("quantity", "entry_price", "exit_price"):
            value = require_decimal(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("allocated_entry_fee", "exit_fee"):
            require_decimal(getattr(self, name), name, non_negative=True)
        require_decimal(self.gross_pnl, "gross_pnl")
        require_decimal(self.net_pnl, "net_pnl")
        with localcontext(arithmetic()):
            if self.net_pnl != self.gross_pnl - self.allocated_entry_fee - self.exit_fee:
                raise ValueError("net_pnl must equal gross_pnl less entry and exit fees")


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    timestamp: datetime
    symbol: str
    action: str
    alpha_id: str
    risk_id: str | None
    risk_approved: bool
    rejection_reason: str | None
    request_id: str | None
    execution_status: str | None
    fee: Decimal
    slippage: Decimal

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        require_v1_symbol(self.symbol)
        require_non_empty(self.action, "action")
        if self.action not in {"BUY", "SELL", "HOLD"}:
            raise ValueError("action must be BUY, SELL, or HOLD")
        require_non_empty(self.alpha_id, "alpha_id")
        if self.risk_id is not None:
            require_non_empty(self.risk_id, "risk_id")
        if type(self.risk_approved) is not bool:
            raise ValueError("risk_approved must be a boolean")
        if self.rejection_reason is not None:
            require_non_empty(self.rejection_reason, "rejection_reason")
        if self.request_id is not None:
            require_non_empty(self.request_id, "request_id")
        if self.execution_status is not None:
            require_non_empty(self.execution_status, "execution_status")
            if self.execution_status not in {
                "ACKNOWLEDGED", "REJECTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "UNKNOWN",
            }:
                raise ValueError("unsupported execution status")
        require_decimal(self.fee, "fee", non_negative=True)
        require_decimal(self.slippage, "slippage", non_negative=True)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Frozen V1 metrics with explicit status for undefined ratios."""

    run_id: str
    timestamp: datetime
    expected_value: Decimal
    net_profit: Decimal
    sharpe: Decimal
    sortino: Decimal
    maximum_drawdown: Decimal
    profit_factor: Decimal
    win_rate: Decimal
    trade_count: int
    average_trade: Decimal
    exposure: Decimal
    fees: Decimal
    slippage: Decimal
    undefined_metrics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty(self.run_id, "run_id")
        require_utc(self.timestamp, "timestamp")
        for field_name in (
            "expected_value", "net_profit", "sharpe", "sortino", "maximum_drawdown",
            "profit_factor", "win_rate", "average_trade", "exposure", "fees", "slippage",
        ):
            require_decimal(getattr(self, field_name), field_name)
        if not Decimal("0") <= self.maximum_drawdown <= Decimal("1"):
            raise ValueError("maximum_drawdown must be between zero and one")
        if self.profit_factor < Decimal("0"):
            raise ValueError("profit_factor must not be negative")
        if not Decimal("0") <= self.win_rate <= Decimal("1"):
            raise ValueError("win_rate must be between zero and one")
        if self.exposure < Decimal("0") or self.fees < Decimal("0") or self.slippage < Decimal("0"):
            raise ValueError("exposure, fees, and slippage must not be negative")
        if isinstance(self.trade_count, bool) or not isinstance(self.trade_count, int):
            raise ValueError("trade_count must be an integer")
        if self.trade_count < 0:
            raise ValueError("trade_count must not be negative")
        undefined = tuple(self.undefined_metrics)
        if any(type(name) is not str or not name for name in undefined):
            raise ValueError("undefined_metrics must contain non-empty strings")
        if len(set(undefined)) != len(undefined):
            raise ValueError("undefined_metrics must not contain duplicates")
        object.__setattr__(self, "undefined_metrics", undefined)


@dataclass(frozen=True, slots=True)
class BacktestReport:
    run_id: str
    result: EvaluationResult
    datasets: tuple[DatasetIdentity, ...]
    config: BacktestConfig
    strategy_version: str
    model_version: str
    feature_version: str
    evaluation_start: datetime
    evaluation_end: datetime
    initial_equity: Decimal
    final_equity: Decimal
    equity_curve: tuple[EquityPoint, ...]
    period_returns: tuple[Decimal, ...]
    completed_trades: tuple[CompletedTrade, ...]
    decisions: tuple[DecisionTrace, ...]
    ledger_identity: str

    def __post_init__(self) -> None:
        require_non_empty(self.run_id, "run_id")
        if self.result.run_id != self.run_id:
            raise ValueError("result run identity mismatch")
        datasets = tuple(self.datasets)
        if not datasets:
            raise ValueError("at least one dataset identity is required")
        if len({item.symbol for item in datasets}) != len(datasets):
            raise ValueError("dataset identities must have unique symbols")
        object.__setattr__(self, "datasets", datasets)
        self.config.__post_init__()
        require_non_empty(self.strategy_version, "strategy_version")
        require_non_empty(self.model_version, "model_version")
        require_non_empty(self.feature_version, "feature_version")
        require_utc(self.evaluation_start, "evaluation_start")
        require_utc(self.evaluation_end, "evaluation_end")
        if self.evaluation_end < self.evaluation_start:
            raise ValueError("evaluation end must not precede start")
        require_decimal(self.initial_equity, "initial_equity")
        require_decimal(self.final_equity, "final_equity")
        if self.initial_equity <= Decimal("0") or self.final_equity <= Decimal("0"):
            raise ValueError("initial and final equity must be positive")
        curve = tuple(self.equity_curve)
        returns = tuple(self.period_returns)
        trades = tuple(self.completed_trades)
        decisions = tuple(self.decisions)
        if not curve or len(returns) != len(curve) - 1:
            raise ValueError("returns must align with consecutive equity observations")
        for point in curve:
            EquityPoint.__post_init__(point)
            if point.equity <= Decimal("0"):
                raise ValueError("equity observations must be positive")
        if any(current.timestamp <= previous.timestamp for previous, current in zip(curve, curve[1:])):
            raise ValueError("equity curve must be strictly chronological")
        if (
            curve[0].timestamp != self.evaluation_start
            or curve[-1].timestamp != self.evaluation_end
            or curve[-1].equity != self.final_equity
            or self.result.timestamp != self.evaluation_end
        ):
            raise ValueError("report boundaries or final equity do not match evidence")
        if any(current.timestamp < previous.timestamp for previous, current in zip(decisions, decisions[1:])):
            raise ValueError("decision trace must be chronological")
        if any(current.timestamp < previous.timestamp for previous, current in zip(trades, trades[1:])):
            raise ValueError("completed trades must be chronological")
        for value in returns:
            require_decimal(value, "period return")
        with localcontext(arithmetic()):
            for previous, current, value in zip(curve, curve[1:], returns):
                if current.timestamp - previous.timestamp != timedelta(minutes=1):
                    raise ValueError("equity curve must use consecutive one-minute observations")
                if previous.equity <= 0 or current.equity <= 0:
                    raise ValueError("equity observations must be positive")
                if value != current.equity / previous.equity - Decimal("1"):
                    raise ValueError("period return differs from equity evidence")
        object.__setattr__(self, "equity_curve", curve)
        object.__setattr__(self, "period_returns", returns)
        object.__setattr__(self, "completed_trades", trades)
        object.__setattr__(self, "decisions", decisions)
        require_non_empty(self.ledger_identity, "ledger_identity")


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    train_minutes: int
    validation_minutes: int
    step_minutes: int

    def __post_init__(self) -> None:
        for name in ("train_minutes", "validation_minutes", "step_minutes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.step_minutes < self.validation_minutes:
            raise ValueError("validation windows must not overlap")


@dataclass(frozen=True, slots=True)
class FoldBoundary:
    train_start: datetime
    train_end_exclusive: datetime
    validation_start: datetime
    validation_end_exclusive: datetime

    def __post_init__(self) -> None:
        for name in (
            "train_start", "train_end_exclusive", "validation_start", "validation_end_exclusive",
        ):
            require_utc(getattr(self, name), name)
        if not self.train_start < self.train_end_exclusive <= self.validation_start:
            raise ValueError("training must strictly precede validation")
        if self.validation_end_exclusive <= self.validation_start:
            raise ValueError("validation window must be positive")


@dataclass(frozen=True, slots=True)
class TrainingWindow:
    boundary: FoldBoundary
    candles_by_symbol: Mapping[str, tuple[Candle, ...]]

    def __post_init__(self) -> None:
        copied: dict[str, tuple[Candle, ...]] = {}
        for symbol, candles in self.candles_by_symbol.items():
            require_v1_symbol(symbol)
            values = tuple(candles)
            if not values:
                raise ValueError("every training symbol must have candles")
            if any(candle.symbol != symbol for candle in values):
                raise ValueError("training candle symbol mismatch")
            if any(
                not self.boundary.train_start <= candle.close_time < self.boundary.train_end_exclusive
                for candle in values
            ):
                raise ValueError("training window contains an out-of-bound candle")
            copied[symbol] = values
        if not copied:
            raise ValueError("training window must not be empty")
        object.__setattr__(self, "candles_by_symbol", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class BuiltAlpha:
    implementation_id: str
    decide: AlphaDecisionFunction

    def __post_init__(self) -> None:
        require_non_empty(self.implementation_id, "implementation_id")
        if not callable(self.decide):
            raise ValueError("decide must be callable")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    index: int
    boundary: FoldBoundary
    training_identity: str
    alpha_implementation_id: str
    report: BacktestReport

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("fold index must be a non-negative integer")
        require_non_empty(self.training_identity, "training_identity")
        require_non_empty(self.alpha_implementation_id, "alpha_implementation_id")
        if self.report.config.alpha_implementation_id != self.alpha_implementation_id:
            raise ValueError("fold alpha implementation identity mismatch")
        if (
            self.report.evaluation_start < self.boundary.validation_start
            or self.report.evaluation_end >= self.boundary.validation_end_exclusive
        ):
            raise ValueError("fold report lies outside validation boundary")


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    run_id: str
    config: WalkForwardConfig
    folds: tuple[WalkForwardFold, ...]
    aggregate: EvaluationResult

    @property
    def aggregation_semantics(self) -> str:
        return "independent flat accounts; pooled within-fold returns and SELL PnLs; summed profit/costs; worst-fold drawdown; observation-weighted exposure"

    def __post_init__(self) -> None:
        require_non_empty(self.run_id, "run_id")
        self.config.__post_init__()
        folds = tuple(self.folds)
        if not folds or tuple(fold.index for fold in folds) != tuple(range(len(folds))):
            raise ValueError("walk-forward folds must be non-empty and sequential")
        if self.aggregate.run_id != self.run_id:
            raise ValueError("walk-forward aggregate identity mismatch")
        object.__setattr__(self, "folds", folds)


@dataclass(frozen=True, slots=True)
class MonteCarloConfig:
    simulations: int
    seed: int
    mean_block_length: int
    lower_tail_fraction: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        if type(self.simulations) is not int or self.simulations <= 0:
            raise ValueError("simulations must be a positive integer")
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        if type(self.mean_block_length) is not int or self.mean_block_length <= 0:
            raise ValueError("mean_block_length must be a positive integer")
        require_decimal(self.lower_tail_fraction, "lower_tail_fraction", non_negative=True)
        if self.lower_tail_fraction >= Decimal("1"):
            raise ValueError("lower_tail_fraction must be below one")


@dataclass(frozen=True, slots=True)
class MonteCarloOutcome:
    simulation: int
    sampled_indices: tuple[int, ...]
    terminal_pnl: Decimal
    terminal_return: Decimal
    maximum_drawdown: Decimal

    def __post_init__(self) -> None:
        if type(self.simulation) is not int or self.simulation < 0:
            raise ValueError("simulation must be a non-negative integer")
        indices = tuple(self.sampled_indices)
        if not indices or any(type(index) is not int or index < 0 for index in indices):
            raise ValueError("sampled_indices must contain non-negative integers")
        object.__setattr__(self, "sampled_indices", indices)
        require_decimal(self.terminal_pnl, "terminal_pnl")
        require_decimal(self.terminal_return, "terminal_return")
        require_decimal(self.maximum_drawdown, "maximum_drawdown", non_negative=True)
        if self.maximum_drawdown > Decimal("1"):
            raise ValueError("maximum_drawdown must not exceed one")


@dataclass(frozen=True, slots=True)
class MonteCarloReport:
    run_id: str
    source_run_id: str
    config: MonteCarloConfig
    outcomes: tuple[MonteCarloOutcome, ...]
    lower_tail_terminal_pnl: Decimal
    positive_outcome_fraction: Decimal

    def __post_init__(self) -> None:
        require_non_empty(self.run_id, "run_id")
        require_non_empty(self.source_run_id, "source_run_id")
        self.config.__post_init__()
        outcomes = tuple(self.outcomes)
        if len(outcomes) != self.config.simulations:
            raise ValueError("outcome count must equal configured simulations")
        if tuple(item.simulation for item in outcomes) != tuple(range(len(outcomes))):
            raise ValueError("simulation indices must be sequential")
        object.__setattr__(self, "outcomes", outcomes)
        require_decimal(self.lower_tail_terminal_pnl, "lower_tail_terminal_pnl")
        require_decimal(self.positive_outcome_fraction, "positive_outcome_fraction")
        if not Decimal("0") <= self.positive_outcome_fraction <= Decimal("1"):
            raise ValueError("positive_outcome_fraction must be between zero and one")
