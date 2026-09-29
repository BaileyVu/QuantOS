"""T5 delayed-minute evaluation using canonical Risk and Execution accounting."""
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext

from quantos.application.evaluation import _TradeBook, _marked_state
from quantos.application.risk_execution import TradingStep
from quantos.domain.alpha.contracts import AlphaAction
from quantos.domain.alpha.v1t5 import A, B, decide
from quantos.domain.evaluation.contracts import EquityPoint
from quantos.domain.evaluation.metrics import calculate_metrics
from quantos.domain.execution.contracts import OrderSide
from quantos.domain.execution.core import ExecutionEngine
from quantos.domain.market_data import Candle
from quantos.domain.risk.engine import RiskEngine, RiskPolicy, RiskContext, MarketState
from quantos.domain.runtime_contracts import AccountSnapshot, TransactionCosts, arithmetic

D = Decimal
ZERO = D('0')
MINUTE = timedelta(minutes=1)


class MemoryLedger:
    def __init__(self):
        self.records = []

    def read(self):
        return tuple(self.records)

    def append(self, record, expected):
        if tuple(self.records) != expected:
            raise ValueError('execution ledger changed')
        self.records.append(record)


class T5RiskEngine(RiskEngine):
    """Add the frozen exchange minimum; never relax canonical Risk checks."""
    def _quantity(self, alpha, context):
        quantity = super()._quantity(alpha, context)
        price = next(m.candle.close for m in context.markets if m.candle.symbol == alpha.symbol)
        if quantity*price < D('5'):
            raise ValueError('minimum notional 5 USDT; dust remains owned')
        return quantity


@dataclass(frozen=True)
class T5Evaluation:
    metrics: object
    daily_returns: tuple
    daily_equities: tuple
    completed_trades: tuple
    rejection_counts: dict
    final_account: AccountSnapshot
    fills: tuple


def evaluate(candles, predictions, *, family, run_id, cost_bps=15, initial_equity=D('20')):
    if family not in (A, B) or cost_bps not in (10, 15, 20, 25, 30):
        raise ValueError('unregistered family/cost scenario')
    costs = TransactionCosts(D('.001'), D(cost_bps-10)/D('10000'))
    policy = RiskPolicy(D('10'), D('.00001'), D('1'), D('1'), D('1000000'),
                        D('.60'), D('.05'), D('.20'), 60, ZERO, costs)
    execution = step = None
    book = _TradeBook()
    curve, fills = [], []
    daily = {}
    rejections = Counter()
    fees = slip = ZERO
    peak = day_equity = initial_equity
    previous = day = None
    consumed = set()
    with localcontext(arithmetic()):
        for candle in candles:
            Candle.__post_init__(candle)
            timestamp = candle.open_time+MINUTE
            if (candle.symbol != 'BTCUSDT' or candle.open_time.second or candle.open_time.microsecond
                    or not timestamp-timedelta(milliseconds=1) <= candle.close_time <= timestamp):
                raise ValueError('canonical BTC completed one-minute candle required')
            if previous is not None and timestamp-previous != MINUTE:
                raise ValueError('missing or unordered evaluation minute')
            if execution is None:
                account = AccountSnapshot(candle.open_time, {'USDT': initial_equity}, ())
                execution = ExecutionEngine(account, costs, MemoryLedger())
                step = TradingStep(T5RiskEngine(policy), execution)
            equity, exposure = _marked_state(execution.snapshot, {'BTCUSDT': candle.close})
            if day != timestamp.date():
                day, day_equity = timestamp.date(), equity
            peak = max(peak, equity)
            prediction = predictions.get(timestamp-MINUTE)
            if prediction is not None:
                if prediction.timestamp != timestamp-MINUTE:
                    raise ValueError('prediction mapping timestamp mismatch')
                consumed.add(prediction.timestamp)
                alpha, edge = decide(prediction, family=family, timestamp=timestamp,
                                     account=execution.snapshot, cost_rate=costs.conservative_cost_rate)
                if alpha.action is not AlphaAction.HOLD:
                    before = execution.snapshot
                    context = RiskContext(timestamp, (MarketState(candle, True),), before, edge,
                        timestamp.replace(hour=0, minute=0, second=0, microsecond=0), day_equity, peak)
                    trading = step.run(alpha, context)
                    if not trading.risk.approved:
                        rejections[trading.risk.rejection_reason] += 1
                    elif trading.execution is not None:
                        result = trading.execution
                        book.observe(before=before, result=result, side=OrderSide(alpha.action.value), symbol='BTCUSDT')
                        fees += result.fee
                        slip += result.slippage_cost
                        fills.append(result)
            equity, exposure = _marked_state(execution.snapshot, {'BTCUSDT': candle.close})
            peak = max(peak, equity)
            curve.append(EquityPoint(timestamp, equity, execution.snapshot.balances['USDT'], exposure, day_equity, peak))
            daily[candle.open_time.date()] = equity
            previous = timestamp
        if not curve:
            raise ValueError('empty evaluation candles')
        if set(predictions)-consumed:
            raise ValueError('prediction lacks a subsequent completed execution minute')
        daily_items = tuple(daily.items())
        # Test windows begin at midnight: include the first day's costs/return.
        returns = tuple(value / (initial_equity if i == 0 else daily_items[i-1][1])-1
                        for i, (_, value) in enumerate(daily_items))
        if len(returns) >= len(curve):
            returns = ()  # A one-minute fixture has no estimable daily ratio.
        metrics = calculate_metrics(run_id=run_id, timestamp=curve[-1].timestamp,
            initial_equity=initial_equity, final_equity=curve[-1].equity, equity_curve=tuple(curve),
            period_returns=returns, completed_trades=tuple(book.completed), fees=fees,
            slippage=slip, annualization_periods=365)
    return T5Evaluation(metrics, returns, daily_items, tuple(book.completed), dict(rejections), execution.snapshot, tuple(fills))
