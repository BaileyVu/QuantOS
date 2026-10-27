"""Evaluation Engine contracts, metrics, and seeded robustness analysis."""

from quantos.domain.evaluation.contracts import (
    AlphaDecisionFunction,
    AlphaEvaluation,
    BacktestConfig,
    BacktestReport,
    BuiltAlpha,
    CompletedTrade,
    DecisionTrace,
    EquityPoint,
    EvaluationResult,
    FoldBoundary,
    MonteCarloConfig,
    MonteCarloOutcome,
    MonteCarloReport,
    TrainingWindow,
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardReport,
)
from quantos.domain.evaluation.metrics import calculate_metrics, equity_returns, maximum_drawdown
from quantos.domain.evaluation.monte_carlo import run_monte_carlo

__all__ = [
    "AlphaDecisionFunction", "AlphaEvaluation", "BacktestConfig", "BacktestReport",
    "BuiltAlpha", "CompletedTrade", "DecisionTrace", "EquityPoint", "EvaluationResult",
    "FoldBoundary", "MonteCarloConfig", "MonteCarloOutcome", "MonteCarloReport",
    "TrainingWindow", "WalkForwardConfig", "WalkForwardFold", "WalkForwardReport",
    "calculate_metrics", "equity_returns", "maximum_drawdown", "run_monte_carlo",
]
