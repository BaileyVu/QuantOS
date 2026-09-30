"""Autonomous BTCUSDT Futures paper trader and accelerated replay."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Iterable

from quantos.domain.alpha.futures import (
    Direction, classify_regime, evaluate_strategies, select_candidate,
)
from quantos.domain.execution.futures_paper import (
    FuturesPaperExecution, FuturesPaperPolicy,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.risk.futures import (
    FuturesAccountState, FuturesRiskPolicy, evaluate_futures_risk,
)


@dataclass(frozen=True, slots=True)
class FuturesTraderConfig:
    symbol: str
    starting_equity: Decimal
    lookback: int
    risk: FuturesRiskPolicy
    execution: FuturesPaperPolicy

    def __post_init__(self) -> None:
        if self.symbol != "BTCUSDT":
            raise ValueError("V1 supports BTCUSDT only")
        if self.starting_equity <= 0 or self.lookback < 30:
            raise ValueError("invalid Futures trader configuration")
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
        self.history: list[Candle] = []
        self.equity_curve: list[Decimal] = [config.starting_equity]
        self.decisions: list[dict] = []
        self.rejections: dict[str, int] = defaultdict(int)
        self.consecutive_losses = 0
        self.day = None
        self.day_start_equity = config.starting_equity

    def on_candle(self, candle: Candle) -> dict:
        if candle.symbol != self.config.symbol or candle.interval != "1m":
            raise FuturesTraderError("unexpected market")
        if self.history and candle.open_time <= self.history[-1].open_time:
            raise FuturesTraderError("candles must be strictly chronological")
        if candle.close_time <= candle.open_time:
            raise FuturesTraderError("incomplete candle")
        current_day = candle.close_time.date()
        if self.day != current_day:
            self.day = current_day
            self.day_start_equity = self.execution.marked_equity(candle.close)

        closed = self.execution.process_candle(candle)
        if closed is not None:
            self.consecutive_losses = self.consecutive_losses + 1 if closed.pnl < 0 else 0
        self.history.append(candle)
        if len(self.history) > self.config.lookback:
            self.history.pop(0)

        record = {"timestamp": candle.close_time.isoformat(), "direction": "HOLD",
                  "reason": "position active" if self.execution.position else "insufficient lookback"}
        # A candle that closed a position cannot also open another: without
        # sub-minute data the causal ordering of exit and new signal is unknown.
        if closed is None and self.execution.position is None and len(self.history) >= 30:
            regime = classify_regime(self.history)
            signals = evaluate_strategies(self.history, regime)
            selection = select_candidate(signals)
            record.update(regime=regime.regime.value, direction=selection.direction.value,
                          reason=selection.reason,
                          signals=[_value(asdict(signal)) for signal in selection.signals])
            if selection.candidate is not None:
                account = FuturesAccountState(
                    equity=self.execution.marked_equity(candle.close),
                    day_start_equity=self.day_start_equity,
                    consecutive_losses=self.consecutive_losses,
                    has_position=False,
                    reconciled=True,
                )
                risk = evaluate_futures_risk(selection.candidate, account, self.rules,
                                             self.config.risk)
                record["risk"] = _value(asdict(risk))
                if risk.approved:
                    position = self.execution.open(selection.candidate, risk)
                    record["execution"] = _value(asdict(position))
                else:
                    self.rejections[risk.reason] += 1
        if closed is not None:
            record["closed_trade"] = _value(asdict(closed))
        self.equity_curve.append(self.execution.marked_equity(candle.close))
        self.decisions.append(record)
        return record

    def finish(self) -> dict:
        if self.history:
            closed = self.execution.close_at(self.history[-1].close,
                                             self.history[-1].close_time)
            if closed is not None:
                self.equity_curve.append(self.execution.balance)
        return self.metrics()

    def metrics(self) -> dict:
        trades = self.execution.trades
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]
        gross_wins = sum((t.pnl for t in wins), Decimal(0))
        gross_losses = sum((-t.pnl for t in losses), Decimal(0))
        peak = self.equity_curve[0]
        drawdown = Decimal(0)
        for equity in self.equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                drawdown = max(drawdown, (peak - equity) / peak)
        strategy = defaultdict(lambda: {"trades": 0, "pnl": Decimal(0)})
        regime = defaultdict(lambda: {"trades": 0, "pnl": Decimal(0)})
        for trade in trades:
            for bucket, key in ((strategy, trade.strategy_id), (regime, trade.regime.value)):
                bucket[key]["trades"] += 1
                bucket[key]["pnl"] += trade.pnl
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
            "expectancy": sum((t.pnl for t in trades), Decimal(0)) / Decimal(count) if count else Decimal(0),
            "profit_factor": gross_wins / gross_losses if gross_losses else None,
            "max_drawdown": drawdown,
            "fees": self.execution.total_fees,
            "funding": None,
            "funding_limitation": "funding is not modeled in the MVP replay",
            "strategy_attribution": dict(strategy),
            "regime_attribution": dict(regime),
            "rejections": dict(self.rejections),
        }
        return _value(result)


def run_futures_replay(candles: Iterable[Candle], config: FuturesTraderConfig,
                       rules: FuturesSymbolRules) -> tuple[dict, AutonomousFuturesPaperTrader]:
    trader = AutonomousFuturesPaperTrader(config, rules)
    for candle in candles:
        trader.on_candle(candle)
    return trader.finish(), trader

