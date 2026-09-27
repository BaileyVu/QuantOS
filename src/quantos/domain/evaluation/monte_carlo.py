"""Seeded stationary-bootstrap robustness over observed backtest returns."""

from __future__ import annotations

from decimal import Decimal, localcontext
from random import Random

from quantos.domain.evaluation.contracts import (
    BacktestReport,
    MonteCarloConfig,
    MonteCarloOutcome,
    MonteCarloReport,
)
from quantos.domain.evaluation.metrics import maximum_drawdown
from quantos.domain.runtime_contracts import arithmetic, identity


def run_monte_carlo(source: BacktestReport, config: MonteCarloConfig) -> MonteCarloReport:
    """Resample observed causal returns in blocks; add no invented drift."""
    if type(source) is not BacktestReport:
        raise ValueError("Monte Carlo requires a BacktestReport")
    source.__post_init__()
    config.__post_init__()
    returns = tuple(source.period_returns)
    if len(returns) < 2:
        raise ValueError("Monte Carlo requires at least two observed period returns")
    random = Random(config.seed)
    outcomes: list[MonteCarloOutcome] = []
    restart_denominator = config.mean_block_length
    for simulation in range(config.simulations):
        index = random.randrange(len(returns))
        indices: list[int] = []
        equities: list[Decimal] = []
        with localcontext(arithmetic()):
            equity = source.initial_equity
            for offset in range(len(returns)):
                if offset and random.randrange(restart_denominator) == 0:
                    index = random.randrange(len(returns))
                indices.append(index)
                equity *= Decimal("1") + returns[index]
                if equity < Decimal("0"):
                    raise ValueError("observed return produces negative simulated equity")
                equities.append(equity)
                index = (index + 1) % len(returns)
            terminal_return = equity / source.initial_equity - Decimal("1")
            outcome = MonteCarloOutcome(
                simulation=simulation,
                sampled_indices=tuple(indices),
                terminal_pnl=equity - source.initial_equity,
                terminal_return=terminal_return,
                maximum_drawdown=maximum_drawdown(source.initial_equity, tuple(equities)),
            )
        outcomes.append(outcome)
    ordered = sorted((item.terminal_pnl for item in outcomes))
    positive = sum(item.terminal_pnl > Decimal("0") for item in outcomes)
    with localcontext(arithmetic()):
        tail_index = int(Decimal(len(ordered) - 1) * config.lower_tail_fraction)
        positive_fraction = Decimal(positive) / Decimal(len(outcomes))
    report_id = identity((source.run_id, config, tuple(outcomes)))
    return MonteCarloReport(
        run_id=report_id,
        source_run_id=source.run_id,
        config=config,
        outcomes=tuple(outcomes),
        lower_tail_terminal_pnl=ordered[tail_index],
        positive_outcome_fraction=positive_fraction,
    )
