"""Execution-owned orchestration, Spot accounting, idempotency and replay."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
import json
import logging
from typing import Protocol

from quantos.domain.common import require_decimal
from quantos.domain.execution.contracts import (
    AccountSnapshot, ExecutionReport, ExecutionStatus, OrderRequest, OrderSide, OrderType, Position,
)
from quantos.domain.execution.evidence import canonical, identity, primitive
from quantos.domain.execution.policy import ExecutionCosts, arithmetic
from quantos.domain.execution.state import validate_account


class ExecutionError(ValueError):
    """Unsafe or unreconciled execution; no further submission is allowed."""


@dataclass(frozen=True, slots=True)
class OrderIntent:
    request: OrderRequest
    alpha_id: str
    risk_id: str
    account_id: str
    reference_price: Decimal
    costs: ExecutionCosts
    evidence: str

    def validate(self) -> None:
        OrderRequest.__post_init__(self.request)
        require_decimal(self.reference_price, 'reference_price')
        if self.reference_price <= 0:
            raise ExecutionError('invalid execution reference')
        self.costs.__post_init__()
        data = json.loads(self.evidence)
        if canonical(data) != self.evidence:
            raise ExecutionError('noncanonical approval evidence')
        risk = data['risk']
        unsigned = dict(risk, decision_id=None)
        request = self.request
        # Check delegated authority and binding, never reevaluate or override Risk.
        if (risk['approved'] is not True or risk['rejection_reason'] is not None
                or risk['approved_quantity'] != primitive(request.quantity)
                or risk['symbol'] != request.symbol or risk['timestamp'] != primitive(request.timestamp)
                or identity(unsigned) != self.risk_id or risk['decision_id'] != self.risk_id
                or identity(data['alpha']) != self.alpha_id or risk['alpha_id'] != self.alpha_id
                or request.request_id != identity((self.alpha_id, self.risk_id,
                                                   request.order_type, request.limit_price))
                or data['alpha']['action'] != request.side.value
                or identity((data['context'], data['policy'])) != risk['context_id']
                or identity(data['context']['account']) != self.account_id
                or data['policy']['costs'] != primitive(self.costs)):
            raise ExecutionError('unapproved or inconsistent order intent')
        markets = data['context']['markets']
        prices = {m['candle']['symbol']: m['candle']['close'] for m in markets}
        if prices.get(request.symbol) != primitive(self.reference_price):
            raise ExecutionError('reference differs from approved price')


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    report: ExecutionReport
    fee: Decimal
    slippage_cost: Decimal
    fee_asset: str = 'USDT'


class ExecutionLedger(Protocol):
    def read(self) -> tuple[str, ...]: ...
    def append(self, record: str, expected: tuple[str, ...]) -> None: ...


class FillProvider(Protocol):
    def fill(self, intent: OrderIntent) -> ExecutionResult: ...
    def verify(self, intent: OrderIntent, result: ExecutionResult) -> None: ...


class PaperFillProvider:
    """Adverse configured price movement, quote fees, market/marketable LIMIT IOC."""
    def verify(self, intent: OrderIntent, result: ExecutionResult) -> None:
        if result != PaperFillProvider._simulate(intent):
            raise ExecutionError('paper provider returned inconsistent execution evidence')

    def fill(self, intent: OrderIntent) -> ExecutionResult:
        return PaperFillProvider._simulate(intent)

    @staticmethod
    def _simulate(intent: OrderIntent) -> ExecutionResult:
        with localcontext(arithmetic()):
            request = intent.request
            direction = Decimal('1') if request.side is OrderSide.BUY else Decimal('-1')
            price = intent.reference_price * (1+direction*intent.costs.slippage_rate)
            if request.order_type is OrderType.LIMIT:
                marketable = (price <= request.limit_price if request.side is OrderSide.BUY
                              else price >= request.limit_price)
                if not marketable:
                    return rejected(request, 'LIMIT IOC not executable within limit')
            return ExecutionResult(
                ExecutionReport(request.request_id, request.timestamp, ExecutionStatus.FILLED,
                                request.quantity, request.quantity, price),
                request.quantity*price*intent.costs.fee_rate,
                request.quantity*abs(price-intent.reference_price),
            )


def rejected(request: OrderRequest, reason: str) -> ExecutionResult:
    return ExecutionResult(ExecutionReport(request.request_id, request.timestamp,
                           ExecutionStatus.REJECTED, request.quantity, reason=reason),
                           Decimal('0'), Decimal('0'))


def apply_result(before: AccountSnapshot, intent: OrderIntent,
                 result: ExecutionResult) -> AccountSnapshot:
    """Authoritative accounting shared by submission and ledger replay."""
    with localcontext(arithmetic()):
        validate_account(before)
        report = result.report
        ExecutionReport.__post_init__(report)
        request = intent.request
        if (report.request_id != request.request_id or report.timestamp != request.timestamp
                or report.requested_quantity != request.quantity or result.fee_asset != 'USDT'):
            raise ExecutionError('inconsistent execution result')
        for name in ('fee', 'slippage_cost'):
            require_decimal(getattr(result, name), name, non_negative=True)
        if report.status is ExecutionStatus.REJECTED:
            if result.fee or result.slippage_cost:
                raise ExecutionError('rejected order has costs')
            return before
        if report.status is not ExecutionStatus.FILLED:
            raise ExecutionError('uncertain or unsupported fill status')
        if result.fee != report.filled_quantity*report.fill_price*intent.costs.fee_rate:
            raise ExecutionError('incorrect fill fee')
        if result.slippage_cost != report.filled_quantity*abs(report.fill_price-intent.reference_price):
            raise ExecutionError('incorrect slippage evidence')
        balances = dict(before.balances)
        positions = {p.symbol: p for p in before.positions}
        base = request.symbol[:-4]
        owned = balances.get(base, Decimal('0'))
        old = positions.get(request.symbol)
        quantity = report.filled_quantity
        notional = quantity*report.fill_price
        if request.side is OrderSide.BUY:
            if notional+result.fee > balances['USDT']:
                raise ExecutionError('insufficient BUY cash')
            balances['USDT'] -= notional+result.fee
            new_quantity = owned+quantity
            basis = owned*old.average_entry_price if old is not None else Decimal('0')
            average = (basis+notional)/new_quantity
        else:
            if quantity > owned:
                raise ExecutionError('insufficient owned position; short prohibited')
            balances['USDT'] += notional-result.fee
            new_quantity = owned-quantity
            average = old.average_entry_price if new_quantity else None
        balances[base] = new_quantity
        if new_quantity:
            positions[request.symbol] = Position(request.symbol, new_quantity, average, request.timestamp)
        else:
            positions.pop(request.symbol, None)
        after = AccountSnapshot(request.timestamp, balances, tuple(positions[s] for s in sorted(positions)))
        validate_account(after)
        return after


def decode_intent(data: dict) -> OrderIntent:
    request = dict(data['request'])
    request['timestamp'] = datetime.fromisoformat(request['timestamp'])
    request['side'] = OrderSide(request['side'])
    request['order_type'] = OrderType(request['order_type'])
    request['quantity'] = Decimal(request['quantity'])
    request['limit_price'] = None if request['limit_price'] is None else Decimal(request['limit_price'])
    return OrderIntent(OrderRequest(**request), data['alpha_id'], data['risk_id'], data['account_id'],
                       Decimal(data['reference_price']),
                       ExecutionCosts(**{k: Decimal(v) for k, v in data['costs'].items()}), data['evidence'])


def decode_result(data: dict) -> ExecutionResult:
    report = dict(data['report'])
    report['timestamp'] = datetime.fromisoformat(report['timestamp'])
    report['status'] = ExecutionStatus(report['status'])
    for name in ('requested_quantity', 'filled_quantity', 'fill_price'):
        report[name] = None if report[name] is None else Decimal(report[name])
    return ExecutionResult(ExecutionReport(**report), Decimal(data['fee']),
                           Decimal(data['slippage_cost']), data['fee_asset'])


class ExecutionEngine:
    """One synchronous execution owner; durable append precedes visible state change.

    Any persistence/reconciliation uncertainty latches this instance closed. Recovery
    creates a new instance only after the complete durable ledger validates.
    """
    def __init__(self, initial: AccountSnapshot, costs: ExecutionCosts,
                 ledger: ExecutionLedger, provider: FillProvider | None = None, logger: logging.Logger | None = None):
        validate_account(initial)
        costs.__post_init__()
        self._initial = initial
        self._snapshot = initial
        self.costs = costs
        self.ledger = ledger
        self.provider = provider if provider is not None else PaperFillProvider()
        self.logger = logger or logging.getLogger('quantos')
        self._blocked = False
        self._records: tuple[str, ...] = ()
        self._known: dict[str, tuple[OrderIntent, ExecutionResult]] = {}
        try:
            genesis = canonical({'schema': 'paper-execution-v1', 'initial': initial, 'costs': costs})
            records = ledger.read()
            if not records:
                ledger.append(genesis, ())
                records = (genesis,)
            if records[0] != genesis:
                raise ExecutionError('initial state or costs mismatch')
            self._snapshot, self._known = self._replay(records)
            self._records = records
            self._log('reconciliation_result', {'status': 'recovered', 'state_id': identity(self.snapshot)})
        except Exception as error:
            self._fail(error)

    @property
    def snapshot(self) -> AccountSnapshot:
        return self._snapshot

    def _log(self, event: str, context) -> None:
        self.logger.info(event, extra={'event': event, 'context': primitive(context)})

    def _fail(self, error: Exception):
        self._blocked = True
        self._log('reconciliation_result', {'status': 'failed', 'reason': str(error)})
        raise ExecutionError(str(error)) from error

    def _check(self):
        if self._blocked:
            raise ExecutionError('execution blocked; authoritative reconciliation required')

    def _record(self, intent, result, before, after, records):
        return canonical({'sequence': len(records), 'previous': identity(records[-1]),
                          'intent': intent, 'result': result,
                          'before_state_id': identity(before), 'after_state_id': identity(after),
                          'after': after})

    def _transition(self, before, intent, provider):
        intent.validate()
        if intent.costs != self.costs:
            raise ExecutionError('cost configuration differs from Risk approval')
        if intent.account_id != identity(before) or intent.request.timestamp < before.timestamp:
            raise ExecutionError('obsolete or inconsistent approved account state')
        result = provider.fill(intent)
        self.provider.verify(intent, result)
        # Execution rechecks spend/ownership constraints even with a valid Risk approval.
        after = apply_result(before, intent, result)
        return result, after

    def _replay(self, records):
        state = self._initial
        known = {}
        if not records or records[0] != canonical({'schema': 'paper-execution-v1',
                                                   'initial': self._initial, 'costs': self.costs}):
            raise ExecutionError('missing or corrupt ledger genesis')
        for index, line in enumerate(records[1:], 1):
            data = json.loads(line)
            if canonical(data) != line:
                raise ExecutionError('noncanonical ledger')
            intent = decode_intent(data['intent'])
            if intent.request.request_id in known:
                raise ExecutionError('duplicate ledger request identity')
            if any(prior.alpha_id == intent.alpha_id for prior, _ in known.values()):
                raise ExecutionError('duplicate ledger Alpha decision')
            # Verify recorded fills without resubmitting through the provider.
            intent.validate()
            if intent.costs != self.costs or intent.account_id != identity(state):
                raise ExecutionError('ledger approval state mismatch')
            if intent.request.timestamp < state.timestamp:
                raise ExecutionError('ledger time moved backwards')
            result = decode_result(data['result'])
            self.provider.verify(intent, result)
            after = apply_result(state, intent, result)
            expected = self._record(intent, result, state, after, records[:index])
            if expected != line:
                raise ExecutionError('ledger evidence or state linkage mismatch')
            known[intent.request.request_id] = (intent, result)
            state = after
        return state, known

    def reconcile(self, expected: AccountSnapshot | None = None) -> AccountSnapshot:
        self._check()
        try:
            records = self.ledger.read()
            state, known = self._replay(records)
            if records != self._records or state != self.snapshot or (expected is not None and state != expected):
                raise ExecutionError('reconciliation state mismatch')
            self._known = known
            self._log('reconciliation_result', {'status': 'matched', 'state_id': identity(state)})
            return state
        except Exception as error:
            self._fail(error)

    def submit(self, intent: OrderIntent) -> ExecutionResult:
        self._check()
        try:
            self.reconcile()
            intent.validate()
            previous = self._known.get(intent.request.request_id)
            if previous is not None:
                if previous[0] != intent:
                    raise ExecutionError('duplicate identity with changed order payload')
                self._log('execution_duplicate', {'request_id': intent.request.request_id})
                return previous[1]
            if any(known.alpha_id == intent.alpha_id for known, _ in self._known.values()):
                raise ExecutionError('Alpha decision already executed with a different approved payload')
            self._log('order_request', intent)
            result, after = self._transition(self.snapshot, intent, self.provider)
            record = self._record(intent, result, self.snapshot, after, self._records)
            self.ledger.append(record, self._records)
            self._records += (record,)
            self._snapshot = after
            self._known[intent.request.request_id] = (intent, result)
            self._log('execution_result', result)
            if result.report.status is ExecutionStatus.FILLED:
                self._log('fill', {'result': result, 'state_id': identity(after)})
            return result
        except Exception as error:
            self._fail(error)
