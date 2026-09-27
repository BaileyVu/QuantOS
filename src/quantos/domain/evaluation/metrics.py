"""Deterministic Decimal performance metrics for V1 evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from quantos.domain.evaluation.contracts import CompletedTrade, EquityPoint, EvaluationResult
from quantos.domain.runtime_contracts import arithmetic


ZERO = Decimal("0")
ONE = Decimal("1")


def equity_returns(initial_equity: Decimal, curve: tuple[EquityPoint, ...]) -> tuple[Decimal, ...]:
    """One-minute close-to-close equity returns; omit the first undefined return."""
    if initial_equity <= ZERO:
        raise ValueError("initial equity must be positive")
    values: list[Decimal] = []
    with localcontext(arithmetic()):
        for previous, point in zip(curve, curve[1:]):
            if point.timestamp - previous.timestamp != timedelta(minutes=1):
                raise ValueError("equity observations must be exactly one minute apart")
            if previous.equity <= ZERO:
                raise ValueError("cannot calculate a return from non-positive equity")
            values.append(point.equity / previous.equity - ONE)
    return tuple(values)


def maximum_drawdown(initial_equity: Decimal, equities: tuple[Decimal, ...]) -> Decimal:
    """Maximum peak-to-trough fractional drawdown, including initial equity."""
    if initial_equity <= ZERO:
        raise ValueError("initial equity must be positive")
    peak = initial_equity
    result = ZERO
    with localcontext(arithmetic()):
        for equity in equities:
            if equity < ZERO:
                raise ValueError("equity must not be negative")
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak
            result = max(result, drawdown)
    return result


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def _ratios(returns: tuple[Decimal, ...], annualization_periods: int) -> tuple[Decimal, Decimal, tuple[str, ...]]:
    undefined: list[str] = []
    if len(returns) < 2:
        return ZERO, ZERO, ("sharpe", "sortino")
    with localcontext(arithmetic()):
        mean = _mean(returns)
        variance = _mean(tuple((value - mean) ** 2 for value in returns))
        deviation = variance.sqrt()
        scale = Decimal(annualization_periods).sqrt()
        if deviation == ZERO:
            sharpe = ZERO
            undefined.append("sharpe")
        else:
            sharpe = mean / deviation * scale
        downside = _mean(tuple(min(value, ZERO) ** 2 for value in returns)).sqrt()
        if downside == ZERO:
            sortino = ZERO
            undefined.append("sortino")
        else:
            sortino = mean / downside * scale
    return sharpe, sortino, tuple(undefined)


def calculate_metrics(
    *,
    run_id: str,
    timestamp: datetime,
    initial_equity: Decimal,
    final_equity: Decimal,
    equity_curve: tuple[EquityPoint, ...],
    period_returns: tuple[Decimal, ...],
    completed_trades: tuple[CompletedTrade, ...],
    fees: Decimal,
    slippage: Decimal,
    annualization_periods: int,
    net_profit: Decimal | None = None,
    maximum_drawdown_value: Decimal | None = None,
) -> EvaluationResult:
    """Calculate the frozen V1 metric set without binary floating point."""
    if not equity_curve:
        raise ValueError("an equity curve is required")
    if len(period_returns) >= len(equity_curve):
        raise ValueError("returns must omit the first observation of each independent curve")
    if type(annualization_periods) is not int or annualization_periods <= 0:
        raise ValueError("annualization_periods must be a positive integer")
    with localcontext(arithmetic()):
        trade_pnls = tuple(trade.net_pnl for trade in completed_trades)
        count = len(trade_pnls)
        average = _mean(trade_pnls) if trade_pnls else ZERO
        winners = tuple(value for value in trade_pnls if value > ZERO)
        losers = tuple(value for value in trade_pnls if value < ZERO)
        if losers:
            profit_factor = sum(winners, ZERO) / abs(sum(losers, ZERO))
            undefined: list[str] = []
        else:
            profit_factor = ZERO
            undefined = ["profit_factor"]
        win_rate = Decimal(len(winners)) / Decimal(count) if count else ZERO
        if not count:
            undefined.extend(("expected_value", "win_rate", "average_trade"))
        ratios = _ratios(period_returns, annualization_periods)
        undefined.extend(ratios[2])
        exposure_ratios = tuple(
            point.gross_exposure / point.equity if point.equity > ZERO else ZERO
            for point in equity_curve
        )
        exposure = _mean(exposure_ratios)
        drawdown = (
            maximum_drawdown_value
            if maximum_drawdown_value is not None
            else maximum_drawdown(initial_equity, tuple(point.equity for point in equity_curve))
        )
        profit = final_equity - initial_equity if net_profit is None else net_profit
    return EvaluationResult(
        run_id=run_id,
        timestamp=timestamp,
        expected_value=average,
        net_profit=profit,
        sharpe=ratios[0],
        sortino=ratios[1],
        maximum_drawdown=drawdown,
        profit_factor=profit_factor,
        win_rate=win_rate,
        trade_count=count,
        average_trade=average,
        exposure=exposure,
        fees=fees,
        slippage=slippage,
        undefined_metrics=tuple(dict.fromkeys(undefined)),
    )
