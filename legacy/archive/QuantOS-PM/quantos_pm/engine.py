from decimal import localcontext
from .domain.common import CONTEXT, utc_ms
from .domain.market_data import MarketData
from .domain.feature import Features
from .domain.alpha import HedgedMaker
from .domain.risk import Risk
from .domain.execution import Execution
from .domain.evaluation import Evaluation


class Engine:
    def __init__(self, config, calibration=None):
        self.config = config
        self.md, self.features, self.alpha = MarketData(), Features(calibration), HedgedMaker()
        self.risk, self.execution, self.evaluation = Risk(config.capital), Execution(config), Evaluation(config.capital)
        self.seen = {}
        self.used_markets = set()  # one entry episode per five-minute market; no averaging
        self.now = 0

    def process(self, event):
        with localcontext(CONTEXT):
            return self._process(event)

    def _process(self, event):
        import json
        eid = event['id']
        blob = json.dumps(event, sort_keys=True, separators=(',', ':'))
        if eid in self.seen:
            if self.seen[eid] != blob:
                raise ValueError('conflicting duplicate event')
            return
        self.seen[eid] = blob
        self.now = utc_ms(event['receipt_time'])
        x, md, ev = self.execution, self.md, self.evaluation
        try:
            previous_bids = None
            if event['type'] in ('snapshot', 'delta'):
                previous_bids = dict(md.books[event['market'], event['outcome']].bids)
            md.apply(event)
            kind = event['type']
            if previous_bids is not None:
                x.depth_change(event, previous_bids, md)
            if kind in ('disconnect', 'cancel_failure'):
                x.uncertain = True
                for o in x.orders.values():
                    if o.pending:
                        o.cancel_failed = kind == 'cancel_failure'
                x.cancel(self.now, kind)
                ev.stops.append(dict(time=self.now, reason=kind))
            if kind == 'reconcile':
                x.reconcile(event, self.now)
                md.healthy, md.reason = True, ''
            if kind in ('tick', 'status'):
                x.cancel(self.now, 'market_metadata_change', event['market'])
            x.advance(self.now, md)
            if kind == 'trade':
                x.trade(event, self.now, md)
            if kind == 'resolution':
                x.settle(event['market'], event['winner'], self.now)
            for mid in md.markets:
                x.merge(mid, self.now)
            eq = x.equity(md, self.now)
            halt = self.risk.observe(self.now, eq)
            if halt:
                if not ev.stops or ev.stops[-1]['reason'] != halt:
                    ev.stops.append(dict(time=self.now, reason=halt))
                x.cancel(self.now, halt)
            for mid in md.markets:
                reason = md.eligible(mid, self.now, self.config.stale_ms, shadow=self.config.mode == 'shadow')
                if reason:
                    x.cancel(self.now, reason, mid)
                if kind not in ('snapshot', 'delta', 'timer') or not md.markets[mid].active:
                    continue
                ev.decisions += 1
                reason = reason or halt or ('execution_uncertain' if x.uncertain else '')
                if not reason and mid in self.used_markets:
                    reason = 'single_entry_episode'
                if not reason:
                    intents, reason, estimate = self.alpha.propose(mid, md, self.features, self.config, self.now, eq)
                    if estimate is not None:
                        x.record('edge_estimate', self.now, market=mid, **estimate)
                    if not reason:
                        reason = self.risk.approve(intents, x, md, eq)
                    if not reason:
                        x.submit(intents, self.now)
                        self.used_markets.add(mid)
                        ev.accepted += 1
                if reason:
                    ev.skips[reason] += 1
        except (ValueError, KeyError, ArithmeticError) as error:
            md.healthy, md.reason, x.uncertain = False, str(error), True
            self.risk.halted = 'unreconciled_state'
            x.cancel(self.now, str(error))
            ev.stops.append(dict(time=self.now, reason=f'invalid_event: {error}'))
        ev.observe(self.now, x, md, event)

    def finish(self):
        with localcontext(CONTEXT):
            # No invented future cancellation acknowledgement at EOF.
            self.execution.cancel(self.now, 'end_of_capture')
            return self.evaluation.report(self.execution, self.md, self.config.model, self.config.mode)
