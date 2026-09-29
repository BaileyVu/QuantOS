"""Sequential V1 historical evaluation and walk-forward orchestration."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Callable

from quantos.application.risk_execution import TradingStep
from quantos.domain.alpha.context import AlphaDecisionContext
from quantos.domain.features.daily import DailyFeatureState, TSMOM_FEATURE_VERSION
from quantos.domain.evaluation.contracts import (
    AlphaDecisionFunction,
    AlphaEvaluation,
    BacktestConfig,
    BacktestReport,
    BuiltAlpha,
    CompletedTrade,
    DecisionTrace,
    EquityPoint,
    FoldBoundary,
    TrainingWindow,
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardReport,
)
from quantos.domain.evaluation.metrics import calculate_metrics, equity_returns
from quantos.domain.execution.contracts import ExecutionStatus, OrderSide
from quantos.domain.execution.core import ExecutionEngine, ExecutionLedger
from quantos.domain.features import FEATURE_VERSION, MIN_HISTORY, compute_feature_vector
from quantos.domain.market_data import Candle, DatasetIdentity, ValidatedCandleSequence
from quantos.domain.risk.engine import MarketState, RiskContext, RiskEngine, RiskPolicy
from quantos.domain.runtime_contracts import AccountSnapshot, arithmetic, identity, validate_account


SYMBOL_ORDER = {"BTCUSDT": 0, "ETHUSDT": 1}
ZERO = Decimal("0")


class EvaluationError(ValueError):
    """Historical evidence is invalid, ambiguous, or cannot be evaluated safely."""


def _validate_datasets(
    datasets: tuple[ValidatedCandleSequence, ...], *, minute_end_clock: bool = False,
) -> tuple[ValidatedCandleSequence, ...]:
    if type(datasets) is not tuple or not datasets:
        raise EvaluationError("datasets must be a non-empty built-in tuple")
    symbols: set[str] = set()
    completion_reference = None
    for dataset in datasets:
        if type(dataset) is not ValidatedCandleSequence:
            raise EvaluationError("datasets must contain validated candle sequences")
        try:
            ValidatedCandleSequence.__post_init__(dataset)
            DatasetIdentity.__post_init__(dataset.identity)
        except ValueError as error:
            raise EvaluationError(f"invalid canonical dataset: {error}") from error
        if dataset.identity.symbol in symbols:
            raise EvaluationError("duplicate symbol dataset")
        symbols.add(dataset.identity.symbol)
        for candle in dataset.candles:
            try:
                Candle.__post_init__(candle)
            except ValueError as error:
                raise EvaluationError(f"invalid canonical candle: {error}") from error
            end = candle.open_time + timedelta(minutes=1)
            if candle.open_time.second or candle.open_time.microsecond:
                raise EvaluationError("candle opens must align to UTC minutes")
            if not end - timedelta(milliseconds=1) <= candle.close_time <= end:
                raise EvaluationError("invalid one-minute candle boundary")
            if completion_reference is None:
                completion_reference = end if minute_end_clock else candle.close_time
            if ((end if minute_end_clock else candle.close_time) - completion_reference) % timedelta(minutes=1):
                raise EvaluationError("evaluation requires a common one-minute completion grid")
    times = sorted({candle.open_time+timedelta(minutes=1) if minute_end_clock else candle.close_time
                    for dataset in datasets for candle in dataset.candles})
    if any(right - left != timedelta(minutes=1) for left, right in zip(times, times[1:])):
        raise EvaluationError("evaluation clock has a missing minute")
    return tuple(sorted(datasets, key=lambda item: SYMBOL_ORDER[item.identity.symbol]))


def _marked_state(account: AccountSnapshot, marks: dict[str, Decimal]) -> tuple[Decimal, Decimal]:
    validate_account(account)
    exposure = ZERO
    with localcontext(arithmetic()):
        for position in account.positions:
            try:
                mark = marks[position.symbol]
            except KeyError as error:
                raise EvaluationError(f"missing mark for owned position {position.symbol}") from error
            if mark <= ZERO:
                raise EvaluationError("position mark must be positive")
            exposure += position.quantity * mark
        equity = account.balances["USDT"] + exposure
    if equity <= ZERO:
        raise EvaluationError("marked equity must be positive")
    return equity, exposure


class _TradeBook:
    """Evaluation evidence derived from Execution fills; never an account-state owner."""

    def __init__(self) -> None:
        self._entry_fees: dict[str, Decimal] = defaultdict(lambda: ZERO)
        self.completed: list[CompletedTrade] = []

    def observe(self, *, before: AccountSnapshot, result, side: OrderSide, symbol: str) -> None:
        report = result.report
        if report.status is not ExecutionStatus.FILLED:
            return
        quantity = report.filled_quantity
        price = report.fill_price
        if price is None:
            raise EvaluationError("filled execution is missing fill price")
        if side is OrderSide.BUY:
            with localcontext(arithmetic()):
                self._entry_fees[symbol] += result.fee
            return
        position = next((item for item in before.positions if item.symbol == symbol), None)
        if position is None or position.average_entry_price is None or position.quantity < quantity:
            raise EvaluationError("SELL fill lacks an owned cost basis")
        with localcontext(arithmetic()):
            if quantity == position.quantity:
                allocated = self._entry_fees.pop(symbol, ZERO)
            else:
                allocated = self._entry_fees[symbol] * quantity / position.quantity
                self._entry_fees[symbol] -= allocated
            gross = quantity * (price - position.average_entry_price)
            net = gross - allocated - result.fee
        self.completed.append(
            CompletedTrade(
                timestamp=report.timestamp,
                symbol=symbol,
                quantity=quantity,
                entry_price=position.average_entry_price,
                exit_price=price,
                allocated_entry_fee=allocated,
                exit_fee=result.fee,
                gross_pnl=gross,
                net_pnl=net,
            )
        )


def run_backtest(
    *,
    datasets: tuple[ValidatedCandleSequence, ...],
    initial_account: AccountSnapshot,
    risk_policy: RiskPolicy,
    alpha_decider: AlphaDecisionFunction,
    config: BacktestConfig,
    ledger: ExecutionLedger,
) -> BacktestReport:
    """Run the production Feature -> Alpha -> Risk -> Execution path sequentially."""
    ordered = _validate_datasets(datasets, minute_end_clock=config.feature_version == TSMOM_FEATURE_VERSION)
    validate_account(initial_account)
    if initial_account.positions:
        raise EvaluationError("historical evaluation requires a flat initial account with complete fee basis")
    risk_policy.__post_init__()
    config.__post_init__()
    def decision_time(candle):
        return candle.open_time+timedelta(minutes=1) if config.feature_version == TSMOM_FEATURE_VERSION else candle.close_time

    first_time = min(
        (decision_time(candle) for dataset in ordered for candle in dataset.candles
         if (config.decision_start is None or decision_time(candle) >= config.decision_start)
         and (config.decision_end_exclusive is None or decision_time(candle) < config.decision_end_exclusive)),
        default=None,
    )
    if first_time is None:
        raise EvaluationError("decision window contains no historical events")
    if initial_account.timestamp > first_time:
        raise EvaluationError("initial account is later than the evaluation clock")
    if not callable(alpha_decider):
        raise EvaluationError("alpha_decider must be callable")
    if ledger.read():
        raise EvaluationError("backtest requires a fresh execution ledger")

    execution = ExecutionEngine(initial_account, risk_policy.costs, ledger)
    step = TradingStep(RiskEngine(risk_policy), execution)
    events: dict[datetime, list[Candle]] = defaultdict(list)
    content_ids: list[str] = []
    for dataset in ordered:
        content_ids.append(identity((dataset.identity, dataset.candles)))
        for candle in dataset.candles:
            events[decision_time(candle)].append(candle)

    histories: dict[str, list[Candle]] = defaultdict(list)
    daily_states = {d.identity.symbol: DailyFeatureState(d.identity.symbol) for d in ordered}
    current: dict[str, Candle] = {}
    curve: list[EquityPoint] = []
    traces: list[DecisionTrace] = []
    trade_book = _TradeBook()
    fees = ZERO
    slippage = ZERO
    initial_equity: Decimal | None = None
    day_start_equity: Decimal | None = None
    day = None
    peak: Decimal | None = None
    strategy_version: str | None = None
    model_version: str | None = None

    for timestamp in sorted(events):
        candles = sorted(events[timestamp], key=lambda item: SYMBOL_ORDER[item.symbol])
        for candle in candles:
            histories[candle.symbol].append(candle)
            histories[candle.symbol] = histories[candle.symbol][-MIN_HISTORY:]
            if config.feature_version == TSMOM_FEATURE_VERSION:
                daily_states[candle.symbol] = daily_states[candle.symbol].advance(candle, decision_time=timestamp)
            current[candle.symbol] = candle
        if config.decision_start is not None and timestamp < config.decision_start:
            continue
        if config.decision_end_exclusive is not None and timestamp >= config.decision_end_exclusive:
            break

        if any(timestamp - current[position.symbol].close_time > timedelta(seconds=risk_policy.stale_seconds)
               for position in execution.snapshot.positions):
            raise EvaluationError("stale mark for owned position")

        pre_equity, _ = _marked_state(execution.snapshot, {key: value.close for key, value in current.items()})
        if initial_equity is None:
            initial_equity = pre_equity
            day_start_equity = pre_equity
            peak = pre_equity
            day = timestamp.date()
        elif timestamp.date() != day:
            day_start_equity = pre_equity
            day = timestamp.date()
        peak = max(peak, pre_equity)

        for candle in candles:
            feature = (daily_states[candle.symbol].feature() if config.feature_version == TSMOM_FEATURE_VERSION
                       else compute_feature_vector(tuple(histories[candle.symbol]), decision_time=timestamp))
            if feature is None:
                continue
            alpha_input = (alpha_decider(feature, AlphaDecisionContext(timestamp, execution.snapshot))
                           if config.feature_version == TSMOM_FEATURE_VERSION else alpha_decider(feature))
            if type(alpha_input) is not AlphaEvaluation:
                raise EvaluationError("alpha callback must return AlphaEvaluation")
            alpha_input.__post_init__()
            alpha = alpha_input.decision
            if (
                alpha.timestamp != timestamp
                or alpha.symbol != candle.symbol
                or alpha.feature_version != feature.feature_version
            ):
                raise EvaluationError("alpha decision is inconsistent with its causal feature vector")
            if strategy_version is None:
                strategy_version = alpha.strategy_version
                model_version = alpha.model_version
            elif (alpha.strategy_version, alpha.model_version) != (strategy_version, model_version):
                raise EvaluationError("strategy/model version changed within one run")

            before = execution.snapshot
            marked, _ = _marked_state(before, {key: value.close for key, value in current.items()})
            peak = max(peak, marked)
            required = {alpha.symbol, *(position.symbol for position in before.positions)}
            try:
                markets = tuple(
                    MarketState(current[symbol], True)
                    for symbol in sorted(required, key=lambda item: SYMBOL_ORDER[item])
                )
            except KeyError as error:
                raise EvaluationError(f"missing causal market state for {error.args[0]}") from error
            context = RiskContext(
                timestamp=timestamp,
                markets=markets,
                account=before,
                gross_edge_rate=alpha_input.gross_edge_rate,
                day_start_timestamp=timestamp.replace(hour=0, minute=0, second=0, microsecond=0),
                day_start_equity=day_start_equity,
                peak_equity=peak,
                volatility=alpha_input.volatility,
            )
            trading = step.run(alpha, context)
            execution_result = trading.execution
            if execution_result is not None:
                side = OrderSide(alpha.action.value)
                trade_book.observe(before=before, result=execution_result, side=side, symbol=alpha.symbol)
                with localcontext(arithmetic()):
                    fees += execution_result.fee
                    slippage += execution_result.slippage_cost
                status = execution_result.report.status.value
                request_id = execution_result.report.request_id
            else:
                status = None
                request_id = None
            traces.append(
                DecisionTrace(
                    timestamp=timestamp,
                    symbol=alpha.symbol,
                    action=alpha.action.value,
                    alpha_id=identity(alpha),
                    risk_id=trading.risk.decision_id,
                    risk_approved=trading.risk.approved,
                    rejection_reason=trading.risk.rejection_reason,
                    request_id=request_id,
                    execution_status=status,
                    fee=execution_result.fee if execution_result is not None else ZERO,
                    slippage=execution_result.slippage_cost if execution_result is not None else ZERO,
                )
            )
            post_equity, _ = _marked_state(
                execution.snapshot, {key: value.close for key, value in current.items()}
            )
            peak = max(peak, post_equity)

        equity, exposure = _marked_state(
            execution.snapshot, {key: value.close for key, value in current.items()}
        )
        peak = max(peak, equity)
        curve.append(
            EquityPoint(
                timestamp=timestamp,
                equity=equity,
                cash=execution.snapshot.balances["USDT"],
                gross_exposure=exposure,
                day_start_equity=day_start_equity,
                running_peak_equity=peak,
            )
        )

    if not curve or initial_equity is None:
        raise EvaluationError("decision window contains no historical events")
    if not traces or strategy_version is None or model_version is None:
        raise EvaluationError("decision window contains no feature-ready Alpha decisions")
    final_equity = curve[-1].equity
    returns = equity_returns(initial_equity, tuple(curve))
    ledger_records = ledger.read()
    ledger_id = identity(ledger_records)
    run_id = identity(
        (
            tuple(content_ids),
            config,
            risk_policy,
            initial_account,
            strategy_version,
            model_version,
            config.feature_version if config.feature_version == TSMOM_FEATURE_VERSION else FEATURE_VERSION,
            tuple(curve),
            tuple(trade_book.completed),
            tuple(traces),
            ledger_id,
        )
    )
    result = calculate_metrics(
        run_id=run_id,
        timestamp=curve[-1].timestamp,
        initial_equity=initial_equity,
        final_equity=final_equity,
        equity_curve=tuple(curve),
        period_returns=returns,
        completed_trades=tuple(trade_book.completed),
        fees=fees,
        slippage=slippage,
        annualization_periods=config.annualization_periods,
    )
    execution.reconcile()
    return BacktestReport(
        run_id=run_id,
        result=result,
        datasets=tuple(dataset.identity for dataset in ordered),
        config=config,
        strategy_version=strategy_version,
        model_version=model_version,
        feature_version=config.feature_version if config.feature_version == TSMOM_FEATURE_VERSION else FEATURE_VERSION,
        evaluation_start=curve[0].timestamp,
        evaluation_end=curve[-1].timestamp,
        initial_equity=initial_equity,
        final_equity=final_equity,
        equity_curve=tuple(curve),
        period_returns=returns,
        completed_trades=tuple(trade_book.completed),
        decisions=tuple(traces),
        ledger_identity=ledger_id,
    )


AlphaBuilder = Callable[[TrainingWindow], BuiltAlpha]
LedgerFactory = Callable[[int], ExecutionLedger]


def run_walk_forward(
    *,
    datasets: tuple[ValidatedCandleSequence, ...],
    initial_account: AccountSnapshot,
    risk_policy: RiskPolicy,
    base_config: BacktestConfig,
    walk_config: WalkForwardConfig,
    alpha_builder: AlphaBuilder,
    ledger_factory: LedgerFactory,
) -> WalkForwardReport:
    """Build on past-only windows and validate each fold through ``run_backtest``."""
    ordered = _validate_datasets(datasets, minute_end_clock=base_config.feature_version == TSMOM_FEATURE_VERSION)
    walk_config.__post_init__()
    base_config.__post_init__()
    if base_config.decision_start is not None or base_config.decision_end_exclusive is not None:
        raise EvaluationError("walk-forward boundaries must be supplied through walk_config")
    common_start = max(dataset.candles[0].close_time for dataset in ordered)
    common_end_exclusive = min(dataset.candles[-1].close_time for dataset in ordered) + timedelta(minutes=1)
    cursor = common_start
    folds: list[WalkForwardFold] = []
    while True:
        train_end = cursor + timedelta(minutes=walk_config.train_minutes)
        validation_start = train_end
        validation_end = validation_start + timedelta(minutes=walk_config.validation_minutes)
        if validation_end > common_end_exclusive:
            break
        boundary = FoldBoundary(cursor, train_end, validation_start, validation_end)
        training = {
            dataset.identity.symbol: tuple(
                candle
                for candle in dataset.candles
                if boundary.train_start <= candle.close_time < boundary.train_end_exclusive
            )
            for dataset in ordered
        }
        window = TrainingWindow(boundary, training)
        built = alpha_builder(window)
        if type(built) is not BuiltAlpha:
            raise EvaluationError("alpha_builder must return BuiltAlpha")
        built.__post_init__()
        fold_config = replace(
            base_config,
            alpha_implementation_id=built.implementation_id,
            decision_start=validation_start,
            decision_end_exclusive=validation_end,
        )
        report = run_backtest(
            datasets=ordered,
            initial_account=initial_account,
            risk_policy=risk_policy,
            alpha_decider=built.decide,
            config=fold_config,
            ledger=ledger_factory(len(folds)),
        )
        folds.append(
            WalkForwardFold(
                index=len(folds),
                boundary=boundary,
                training_identity=identity(window),
                alpha_implementation_id=built.implementation_id,
                report=report,
            )
        )
        cursor += timedelta(minutes=walk_config.step_minutes)
    if not folds:
        raise EvaluationError("walk-forward configuration produces no complete folds")

    fold_tuple = tuple(folds)
    run_id = identity((walk_config, tuple((fold.boundary, fold.training_identity, fold.report.run_id) for fold in fold_tuple)))
    trades = tuple(trade for fold in fold_tuple for trade in fold.report.completed_trades)
    returns = tuple(value for fold in fold_tuple for value in fold.report.period_returns)
    curves = tuple(point for fold in fold_tuple for point in fold.report.equity_curve)
    with localcontext(arithmetic()):
        net_profit = sum((fold.report.result.net_profit for fold in fold_tuple), ZERO)
        fees = sum((fold.report.result.fees for fold in fold_tuple), ZERO)
        slippage = sum((fold.report.result.slippage for fold in fold_tuple), ZERO)
    drawdown = max(fold.report.result.maximum_drawdown for fold in fold_tuple)
    initial_equity = fold_tuple[0].report.initial_equity
    aggregate = calculate_metrics(
        run_id=run_id,
        timestamp=max(fold.report.result.timestamp for fold in fold_tuple),
        initial_equity=initial_equity,
        final_equity=fold_tuple[-1].report.final_equity,
        equity_curve=curves,
        period_returns=returns,
        completed_trades=trades,
        fees=fees,
        slippage=slippage,
        annualization_periods=base_config.annualization_periods,
        net_profit=net_profit,
        maximum_drawdown_value=drawdown,
    )
    return WalkForwardReport(run_id=run_id, config=walk_config, folds=fold_tuple, aggregate=aggregate)
