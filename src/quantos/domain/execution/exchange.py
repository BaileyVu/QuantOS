"""Execution-owned durable exchange state; uncertain requests are never resubmitted."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
import json

from quantos.domain.execution.core import ExecutionError, decode_intent
from quantos.domain.execution.contracts import OrderSide
from quantos.domain.runtime_contracts import AccountSnapshot, Position, arithmetic, canonical, identity, validate_account

ZERO=Decimal(0)
TERMINAL=frozenset(('FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH','REJECTED'))
STATUSES=TERMINAL | {'NEW','PARTIALLY_FILLED','PENDING_NEW','PENDING_CANCEL'}


@dataclass(frozen=True, slots=True)
class ActualFill:
    trade_id: str
    quantity: Decimal
    price: Decimal
    quote_quantity: Decimal
    commission: Decimal
    commission_asset: str

    def __post_init__(self):
        if not self.trade_id or not self.commission_asset:raise ExecutionError('missing fill identity')
        for v in (self.quantity,self.price,self.quote_quantity,self.commission):
            if type(v) is not Decimal or not v.is_finite() or v<0:raise ExecutionError('invalid actual fill')
        if min(self.quantity,self.price,self.quote_quantity)<=0:raise ExecutionError('nonpositive fill')


@dataclass(frozen=True, slots=True)
class ActualOrder:
    order_id: str
    client_id: str
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    quote_quantity: Decimal
    fills: tuple[ActualFill,...]

    def __post_init__(self):
        if not self.order_id or not self.client_id or self.status not in STATUSES:
            raise ExecutionError('unsupported exchange order')
        for v in (self.quantity,self.filled_quantity,self.quote_quantity):
            if type(v) is not Decimal or not v.is_finite() or v<0:raise ExecutionError('invalid order totals')
        if self.quantity<=0 or self.filled_quantity>self.quantity:raise ExecutionError('invalid filled quantity')
        if self.status=='FILLED' and self.filled_quantity!=self.quantity:raise ExecutionError('incomplete terminal fill')
        if self.status in ('NEW','PENDING_NEW','REJECTED') and self.filled_quantity:raise ExecutionError('invalid unfilled status')
        if self.status=='PARTIALLY_FILLED' and not 0<self.filled_quantity<self.quantity:raise ExecutionError('invalid partial status')
        if type(self.fills) is not tuple or len({f.trade_id for f in self.fills})!=len(self.fills):raise ExecutionError('duplicate fills')
        for f in self.fills:f.__post_init__()
        with localcontext(arithmetic()):
            if sum((f.quantity for f in self.fills),ZERO)!=self.filled_quantity or sum((f.quote_quantity for f in self.fills),ZERO)!=self.quote_quantity:
                raise ExecutionError('order/fill totals differ; incomplete trade history')

    @property
    def average_price(self):
        with localcontext(arithmetic()):
            return self.quote_quantity/self.filled_quantity if self.filled_quantity else None


def decode_order(data):
    fills=tuple(ActualFill(f['trade_id'],*(Decimal(f[k]) for k in ('quantity','price','quote_quantity','commission')),f['commission_asset']) for f in data['fills'])
    return ActualOrder(data['order_id'],data['client_id'],data['status'],*(Decimal(data[k]) for k in ('quantity','filled_quantity','quote_quantity')),fills)


def apply_actual(before,intent,order):
    """Recompute cumulative effects from the pre-order state, applying each fill once."""
    intent.validate();order.__post_init__();validate_account(before)
    if order.quantity!=intent.request.quantity:raise ExecutionError('order quantity differs from Risk approval')
    base=intent.request.symbol[:-4];balances=dict(before.balances);positions={p.symbol:p for p in before.positions}
    old=positions.get(intent.request.symbol);owned=balances.get(base,ZERO)
    with localcontext(arithmetic()):
        basis=owned*old.average_entry_price if old else ZERO
        for f in order.fills:
            limit = intent.request.limit_price
            if limit is not None:
                buy = intent.request.side is OrderSide.BUY
                if (buy and (f.price > limit or f.quote_quantity > f.quantity*limit)) or (not buy and (f.price < limit or f.quote_quantity < f.quantity*limit)):
                    raise ExecutionError('actual fill violates approved limit price')
            if f.commission_asset not in (base,'USDT'):raise ExecutionError('unsupported third-asset commission')
            bfee=f.commission if f.commission_asset==base else ZERO
            qfee=f.commission if f.commission_asset=='USDT' else ZERO
            if intent.request.side is OrderSide.BUY:
                acquired=f.quantity-bfee
                if acquired<=0:raise ExecutionError('commission consumes fill')
                balances['USDT']-=f.quote_quantity+qfee;owned+=acquired;basis+=f.quote_quantity+qfee
            else:
                sold=f.quantity+bfee
                if sold>owned:raise ExecutionError('sell exceeds owned asset')
                basis=basis*(owned-sold)/owned;owned-=sold
                balances['USDT']+=f.quote_quantity-qfee
            if balances['USDT']<0:raise ExecutionError('fill exceeds allocated cash')
        balances[base]=owned
        if owned:positions[intent.request.symbol]=Position(intent.request.symbol,owned,basis/owned,intent.request.timestamp)
        else:positions.pop(intent.request.symbol,None)
    result=AccountSnapshot(intent.request.timestamp,balances,tuple(positions[k] for k in sorted(positions)))
    validate_account(result);return result


def actual_cost_evidence(intent, order):
    """Actual commissions remain in their charged assets; no guessed FX conversion."""
    with localcontext(arithmetic()):
        commissions = {}
        for fill in order.fills:
            commissions[fill.commission_asset] = commissions.get(fill.commission_asset, ZERO) + fill.commission
        direction = Decimal(1) if intent.request.side is OrderSide.BUY else Decimal(-1)
        slippage = direction * (order.quote_quantity - order.filled_quantity*intent.reference_price)
        return {'commission_by_asset': commissions, 'signed_slippage_quote': slippage,
                'adverse_slippage_quote': max(ZERO, slippage), 'average_price': order.average_price}


class ExchangeExecutionEngine:
    """Single writer with durable prepare, cumulative fills and remote recovery.

    Port owns provider I/O and normalization. Only this Execution owner invokes
    economic submission. A pending or ambiguous request blocks all new intent.
    """
    def __init__(self,initial,costs,ledger,port,*,recover_pending=False):
        validate_account(initial);costs.__post_init__()
        if initial.positions or initial.balances.get('BTC',ZERO) or initial.balances.get('ETH',ZERO):raise ExecutionError('new allocation must start flat')
        if not port.testnet:raise ExecutionError('economic runtime is testnet only')
        self.initial,self.costs,self.ledger,self.port=initial,costs,ledger,port
        self._scope=port.scope
        self._blocked=False;self._records=ledger.read();self._orders={};self._snapshot=initial
        try:
            if not self._records:
                if port.open_orders():raise ExecutionError('unowned exchange open orders')
                baseline=port.balances()
                if any(locked for free,locked in baseline.values()) or baseline.get('USDT',(ZERO,ZERO))[0]<initial.balances['USDT']:
                    raise ExecutionError('unavailable or locked starting allocation')
                self._append({'kind':'genesis','schema':'spot-execution-v1','initial':initial,'costs':costs,'scope':port.scope,'baseline':baseline})
            self._restore()
            self.reconcile(allow_pending=recover_pending)
        except Exception:
            self._blocked=True
            raise

    @property
    def snapshot(self):return self._snapshot

    @property
    def pending(self):return tuple(k for k,v in self._orders.items() if v['order'] is None or v['order'].status not in TERMINAL)

    def _append(self,event):
        value=canonical({'sequence':len(self._records),'previous':identity(self._records[-1]) if self._records else None,'event':event})
        self.ledger.append(value,self._records);self._records+=(value,)

    def _restore(self):
        self._orders={};self._snapshot=self.initial
        for i,line in enumerate(self._records):
            raw=json.loads(line)
            if canonical(raw)!=line or raw['sequence']!=i or raw['previous']!=(identity(self._records[i-1]) if i else None):raise ExecutionError('exchange ledger chain mismatch')
            event=raw['event']
            if i==0:
                if event['schema']!='spot-execution-v1' or event['scope']!=self.port.scope or canonical(event['initial'])!=canonical(self.initial) or canonical(event['costs'])!=canonical(self.costs):raise ExecutionError('exchange ledger genesis mismatch')
                self.baseline={k:tuple(Decimal(v) for v in values) for k,values in event['baseline'].items()}
            elif event['kind']=='prepared':
                intent=decode_intent(event['intent']);self._validate_new(intent)
                cid=self.port.client_id(intent.request.request_id)
                if event['client_id']!=cid:raise ExecutionError('client identity mismatch')
                self._orders[intent.request.request_id]={'intent':intent,'before':self.snapshot,'order':None,'cid':cid}
            elif event['kind']=='observation':
                observed=decode_order(event['order'])
                intent=self._orders[event['request_id']]['intent']
                if canonical(event['actual_costs']) != canonical(actual_cost_evidence(intent,observed)):
                    raise ExecutionError('actual cost evidence mismatch')
                self._apply(event['request_id'],observed)
                if event['state_id']!=identity(self.snapshot):raise ExecutionError('exchange accounting mismatch')
            elif event['kind'] in ('unknown','cancel_requested'):
                if event['request_id'] not in self._orders:raise ExecutionError('unknown request absent')
            else:raise ExecutionError('invalid exchange ledger event')

    def _validate_new(self,intent):
        intent.validate()
        if self.pending:raise ExecutionError('pending/UNKNOWN execution blocks new decisions')
        if intent.account_id!=identity(self.snapshot) or intent.costs!=self.costs or intent.request.timestamp<self.snapshot.timestamp:
            raise ExecutionError('obsolete approval or costs')
        if intent.request.request_id in self._orders or any(v['intent'].alpha_id==intent.alpha_id for v in self._orders.values()):raise ExecutionError('duplicate economic decision')
        if intent.request.side is OrderSide.BUY and self.snapshot.positions:raise ExecutionError('one position; no pyramiding')

    def _apply(self,rid,order):
        item=self._orders[rid];prior=item['order']
        if order.client_id!=item['cid']:raise ExecutionError('remote client identity mismatch')
        if prior:
            if prior.order_id!=order.order_id or (prior.status in TERMINAL and prior!=order):raise ExecutionError('terminal order changed')
            if order.fills[:len(prior.fills)]!=prior.fills:raise ExecutionError('fill history changed/regressed')
        self._snapshot=apply_actual(item['before'],item['intent'],order);item['order']=order

    def _observe(self,rid):
        item=self._orders[rid]
        order=self.port.observe(item['intent'],item['cid'])
        if item['order']==order:return
        # Preserve before-state until append succeeds; failure latches closed.
        after=apply_actual(item['before'],item['intent'],order)
        prior=item['order']
        if prior and (prior.order_id!=order.order_id or prior.status in TERMINAL or order.fills[:len(prior.fills)]!=prior.fills):raise ExecutionError('invalid exchange status transition')
        if order.client_id!=item['cid']:raise ExecutionError('wrong client order')
        self._append({'kind':'observation','request_id':rid,'order':order,'state_id':identity(after),'actual_costs':actual_cost_evidence(item['intent'],order)})
        self._apply(rid,order)

    def reconcile(self,*,allow_pending=False):
        if self._blocked:raise ExecutionError('execution blocked; restart and reconcile')
        try:
            if self.port.scope != self._scope:raise ExecutionError('exchange account scope changed')
            if self.ledger.read()!=self._records:raise ExecutionError('concurrent ledger writer')
            # Re-observe only pending. Terminal orders are immutable local evidence;
            # remote balances and all open orders must still match on every call.
            for rid in self.pending:self._observe(rid)
            open_orders=self.port.open_orders()
            expected={v['cid'] for v in self._orders.values() if v['order'] and v['order'].status not in TERMINAL}
            actual=[r['clientOrderId'] for r in open_orders]
            if len(actual)!=len(set(actual)) or set(actual)!=expected:raise ExecutionError('unowned/missing open order')
            remote=self.port.balances()
            assets=set(self.baseline)|set(remote)|set(self.snapshot.balances)
            for asset in assets:
                with localcontext(arithmetic()):
                    initial=sum(self.baseline.get(asset,(ZERO,ZERO)),ZERO)
                    expected_total=initial+self.snapshot.balances.get(asset,ZERO)-self.initial.balances.get(asset,ZERO)
                    free,locked=remote.get(asset,(ZERO,ZERO))
                    if free+locked!=expected_total or (not self.pending and locked):raise ExecutionError('exchange balance reconciliation mismatch')
            if self.pending and not allow_pending:raise ExecutionError('pending execution requires recovery; no new orders')
            return self.snapshot
        except Exception:
            self._blocked=True;raise

    def submit(self,intent):
        if self._blocked:raise ExecutionError('execution blocked; restart and reconcile')
        try:
            self.reconcile()
            prior=self._orders.get(intent.request.request_id)
            if prior:
                if prior['intent']!=intent:raise ExecutionError('request collision')
                return prior['order']
            self._validate_new(intent)
            self.port.validate_filters(intent)
            self.port.test_order(intent)
            cid=self.port.client_id(intent.request.request_id)
            self._append({'kind':'prepared','intent':intent,'client_id':cid})
            self._orders[intent.request.request_id]={'intent':intent,'before':self.snapshot,'order':None,'cid':cid}
            try:self.port.new_order(intent)
            except Exception:
                self._append({'kind':'unknown','request_id':intent.request.request_id})
            # Query even on timeout/rejection. Never assume an economic request failed.
            self._observe(intent.request.request_id)
            self.reconcile()
            return self._orders[intent.request.request_id]['order']
        except Exception:
            self._blocked=True;raise


    def cancel_pending(self):
        """Explicit recovery action; cancellation uncertainty also requires query."""
        if self._blocked:raise ExecutionError('restart in explicit recovery mode')
        try:
            self.reconcile(allow_pending=True)
            for rid in self.pending:
                item=self._orders[rid]
                self._append({'kind':'cancel_requested','request_id':rid})
                try:self.port.cancel_order(item['intent'].request.symbol,item['cid'])
                except Exception:
                    self._append({'kind':'unknown','request_id':rid})
                self._observe(rid)
            return self.reconcile()
        except Exception:
            self._blocked=True;raise
