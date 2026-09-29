"""One provider-independent Alpha -> Risk -> Execution application step."""
from dataclasses import dataclass
from decimal import Decimal
import logging

from quantos.domain.alpha import AlphaAction, AlphaDecision
from quantos.domain.execution.contracts import OrderRequest, OrderSide, OrderType
from quantos.domain.execution.core import ExecutionEngine, ExecutionResult, OrderIntent
from quantos.domain.execution.exchange import ActualOrder, ExchangeExecutionEngine
from quantos.domain.execution.evidence import canonical, identity, primitive
from quantos.domain.risk.contracts import RiskDecision
from quantos.domain.risk.engine import RiskContext, RiskEngine, RiskPolicy, decision_identity


def build_order_intent(alpha: AlphaDecision, risk: RiskDecision, context: RiskContext,
                       policy: RiskPolicy, *, order_type: OrderType = OrderType.MARKET,
                       limit_price: Decimal | None = None) -> OrderIntent:
    if (alpha.action is AlphaAction.HOLD or not risk.approved
            or risk.alpha_id != identity(alpha) or risk.context_id != identity((context, policy))
            or risk.decision_id != decision_identity(risk)
            or risk.symbol != alpha.symbol or risk.timestamp != context.timestamp):
        raise ValueError('Risk rejection or mismatched approval terminates order construction')
    request_id = identity((identity(alpha), risk.decision_id, order_type, limit_price))
    request = OrderRequest(request_id, context.timestamp, alpha.symbol,
                           OrderSide(alpha.action.value), order_type, risk.approved_quantity, limit_price)
    price = next(m.candle.close for m in context.markets if m.candle.symbol == alpha.symbol)
    intent = OrderIntent(request, identity(alpha), risk.decision_id, identity(context.account),
                         price, policy.costs, canonical({'alpha': alpha, 'risk': risk,
                                                        'context': context, 'policy': policy}))
    intent.validate()
    return intent


@dataclass(frozen=True, slots=True)
class TradingResult:
    risk: RiskDecision
    execution: ExecutionResult | ActualOrder | None


class TradingStep:
    def __init__(self, risk: RiskEngine, execution: ExecutionEngine | ExchangeExecutionEngine, logger: logging.Logger | None = None):
        self.risk = risk
        self.execution = execution
        self.logger = logger or logging.getLogger('quantos')

    def _log(self, event, value):
        self.logger.info(event, extra={'event': event, 'context': primitive(value)})

    def run(self, alpha: AlphaDecision, context: RiskContext, *,
            order_type: OrderType = OrderType.MARKET, limit_price: Decimal | None = None) -> TradingResult:
        self._log('alpha_decision', {'alpha_id': identity(alpha), 'alpha': alpha})
        decision = self.risk.evaluate(alpha, context)
        try:
            self._log('risk_input', {'context': context, 'policy': self.risk.policy})
        except ValueError:
            self._log('risk_input', {'invalid_input': repr(context)})
        self._log('risk_decision', decision)
        if not decision.approved:
            return TradingResult(decision, None)
        intent = build_order_intent(alpha, decision, context, self.risk.policy,
                                    order_type=order_type, limit_price=limit_price)
        self._log('order_intent', intent)
        return TradingResult(decision, self.execution.submit(intent))
