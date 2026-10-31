"""Fixed, audit-only synthetic sensitivities; no original evidence is overwritten.

Import ``run_experiments`` for in-memory journal-equivalent rows. Running this
module writes only ``evidence/pnl_audit/candidate_experiments.json``. The supplied
fixture was inspected before this protocol: none of these results is held out.
"""
import json
from dataclasses import replace
from decimal import localcontext

from .config import ROOT, load, local_path
from .domain.alpha import HedgedMaker
from .domain.common import CONTEXT, D, ZERO, utc_ms
from .domain.execution import Execution
from .domain.market_data import MarketData
from .domain.risk import Risk
from .engine import Engine
from .storage import canonical, digest


def read_report(run_id):
    # The auditor refuses nonempty WAL and reads immutable SQLite, avoiding
    # creation of sidecars while inspecting preserved historical evidence.
    from .pnl_audit import read_rows
    return next(row['body'] for row in read_rows(run_id) if row['kind'] == 'report')


POLICIES = (
    'control', 'quote_5', 'inventory_75pct', 'combined_097',
    'liquidity_queue', 'exposure_deadline_5s', 'expiry_120s', 'cancel_dominant',
)
MODELS = ('optimistic', 'baseline', 'pessimistic')
CAPITALS = (200, 350, 500)
POLICY_DEFINITIONS = {
    'control': 'Original policy, original event schedule.',
    'quote_5': 'Quote ceiling 5; no minimum-size upward rounding.',
    'inventory_75pct': 'Unmatched budgets 1.5% market / 2.25% global, with consistent 1.5% size sizing; matched budgets unchanged.',
    'combined_097': 'Reject combined bid price above 0.97; do not change quoted prices.',
    'liquidity_queue': 'Both best bid/ask depths >= 2 * venue minimum; raw queue <= 2 * quote size; original calibration queue limit remains.',
    'exposure_deadline_5s': 'At the first real event at/after 5s unmatched, request market-wide cancellation; retain inventory to genuine settlement.',
    'expiry_120s': 'Stop entry and request cancellation at <= 120s remaining; retain original acknowledgement latency.',
    'cancel_dominant': 'After event/merge, request cancellation only for orders on the currently dominant inventory side; no replacement or sale.',
}


class ExperimentAlpha(HedgedMaker):
    def __init__(self, policy):
        self.policy = policy

    def propose(self, mid, md, features, config, now, equity):
        prices = [max(md.books[mid, side].bids) for side in ('UP', 'DOWN')]
        if self.policy == 'combined_097' and sum(prices, ZERO) > D('.97'):
            return (), 'audit_combined_097', None
        if self.policy == 'inventory_75pct':
            size = min(config.quote_size, equity * D('.015') / max(prices))
            config = replace(config, quote_size=size)
        intents, reason, estimate = super().propose(mid, md, features, config, now, equity)
        if intents and not reason and self.policy == 'liquidity_queue':
            minimum_depth = 2 * md.markets[mid].minimum
            for intent in intents:
                book = md.books[mid, intent.outcome]
                if book.bids[max(book.bids)] < minimum_depth or book.asks[min(book.asks)] < minimum_depth:
                    return (), 'audit_minimum_liquidity', estimate
                if book.queue(intent.price) > 2 * intent.size:
                    return (), 'audit_queue_quality', estimate
        return intents, reason, estimate


class TighterUnmatchedRisk(Risk):
    def approve(self, intents, execution, md, equity):
        reason = super().approve(intents, execution, md, equity)
        if reason:
            return reason
        # Repeat the original conservative scenario calculation with only the
        # unmatched bounds tightened. Never net pending orders across markets.
        reserves = [order for order in execution.orders.values() if order.pending]
        projected = []
        for mid in md.markets:
            pos = execution.positions.get(mid)
            qty = {side: pos.qty[side] if pos else ZERO for side in ('UP', 'DOWN')}
            costs = {side: pos.cost[side] if pos else ZERO for side in qty}
            pending_qty = dict.fromkeys(qty, ZERO)
            pending_cost = dict.fromkeys(qty, ZERO)
            for order in reserves:
                if order.market == mid:
                    pending_qty[order.outcome] += order.remaining
                    pending_cost[order.outcome] += order.remaining * order.price
            for intent in intents:
                if intent.market == mid:
                    pending_qty[intent.outcome] += intent.size
                    pending_cost[intent.outcome] += intent.size * intent.price
            unmatched = max(
                costs[side] + pending_cost[side] if qty[side] + pending_qty[side] > qty[other] else ZERO
                for side, other in (('UP', 'DOWN'), ('DOWN', 'UP')))
            projected.append(unmatched)
            if unmatched > equity * D('.015'):
                return 'audit_market_unmatched_limit'
        if sum(projected, ZERO) > equity * D('.0225'):
            return 'audit_global_unmatched_limit'
        return ''


class EarlierExpiryMarketData(MarketData):
    def eligible(self, mid, now, stale, *, shadow=False):
        reason = super().eligible(mid, now, stale, shadow=shadow)
        if not reason and now >= self.markets[mid].expiry - 120000:
            return 'audit_expiry_120s'
        return reason


class ExperimentExecution(Execution):
    def cancel_one(self, order, now, reason, policy):
        """Original cancellation semantics scoped to one order, without ack."""
        if not order.pending or order.cancel_at is not None:
            return False
        lag = self.config.cancel_ms * (3 if self.config.model == 'pessimistic' else 1)
        order.cancel_at = now + lag
        if order.status != 'SUBMITTING':
            order.status = 'CANCEL_PENDING'
        self.record('cancel_requested', now, order=order.id, reason=reason,
                    due=order.cancel_at, policy=policy)
        return True


class ExposureTracker:
    """Observe real event boundaries; never add a timer or infer an exit."""
    def __init__(self, policy):
        self.policy = policy
        self.active = {}
        self.episodes = []
        self.reactions = []

    def before_event(self, engine, event):
        if self.policy != 'exposure_deadline_5s':
            return
        now = utc_ms(event['receipt_time'])
        for mid, episode in self.active.items():
            if episode.get('reaction_time') is not None or now < episode['start'] + 5000:
                continue
            requested = []
            for order in engine.execution.orders.values():
                if order.market == mid and engine.execution.cancel_one(
                        order, now, 'audit_unmatched_deadline', self.policy):
                    requested.append(order.id)
            episode['reaction_time'] = now
            self.reactions.append(dict(
                market=mid, exposure_start=episode['start'], deadline=episode['start'] + 5000,
                reaction_time=now, reaction_lateness_ms=now - episode['start'] - 5000,
                orders_requested=requested, retained_inventory=True))

    def after_event(self, engine, event):
        now, execution = engine.now, engine.execution
        current = {mid: position for mid, position in execution.positions.items()
                   if position.unmatched_cost > 0}
        for mid in tuple(self.active):
            if mid not in current:
                episode = self.active.pop(mid)
                episode.update(end=now, duration_ms=now - episode['start'],
                               duration_over_5s=now - episode['start'] > 5000,
                               end_reason='resolution' if event['type'] == 'resolution' and event['market'] == mid else 'matched')
                self.episodes.append(episode)
        for mid, position in current.items():
            if mid not in self.active:
                self.active[mid] = dict(market=mid, start=now, peak_cost=position.unmatched_cost,
                                        reaction_time=None)
            episode = self.active[mid]
            episode['peak_cost'] = max(episode['peak_cost'], position.unmatched_cost)
            if self.policy == 'cancel_dominant':
                dominant = 'UP' if position.qty['UP'] > position.qty['DOWN'] else 'DOWN'
                requested = []
                for order in execution.orders.values():
                    if order.market == mid and order.outcome == dominant and execution.cancel_one(
                            order, now, 'audit_cancel_dominant', self.policy):
                        requested.append(order.id)
                if requested:
                    self.reactions.append(dict(market=mid, exposure_start=episode['start'],
                        reaction_time=now, dominant=dominant, orders_requested=requested,
                        retained_inventory=True))

    def report(self, now):
        episodes = self.episodes + [dict(
            episode, end=None, observed_through=now, duration_ms=now - episode['start'],
            duration_over_5s=now - episode['start'] > 5000, end_reason='still_open_at_capture_end')
            for episode in self.active.values()]
        return dict(exposure_episodes=episodes, policy_reactions=self.reactions,
                    max_observed_exposure_duration_ms=max((row['duration_ms'] for row in episodes), default=0),
                    episodes_exceeding_5s=sum(row['duration_over_5s'] for row in episodes),
                    guaranteed_holding_limit=False,
                    timing_note='Observed receipt-time boundaries only; no inserted timers, sales or invented acknowledgements.')


def _primitive(value):
    return json.loads(canonical(value))


def _make_engine(config, calibration, policy):
    if policy not in POLICIES:
        raise ValueError('unknown fixed experiment policy')
    if policy == 'quote_5':
        config = replace(config, quote_size=D('5'))
    engine = Engine(config, calibration)
    if policy != 'control':
        engine.alpha = ExperimentAlpha(policy)
        engine.execution = ExperimentExecution(config)
    if policy == 'inventory_75pct':
        engine.risk = TighterUnmatchedRisk(config.capital)
    if policy == 'expiry_120s':
        engine.md = EarlierExpiryMarketData()
    return engine


def _run_one(config, calibration, events, policy, original_report, base_manifest):
    engine = _make_engine(config, calibration, policy)
    tracker = ExposureTracker(policy)
    manifest = dict(base_manifest, capital=config.capital, model=config.model, policy=policy,
                    definition=POLICY_DEFINITIONS[policy])
    run_id = 'audit-' + digest(canonical(manifest).encode())[:20]
    rows = [dict(kind='manifest', body=manifest)]
    for event in events:
        start = len(engine.execution.records)
        tracker.before_event(engine, event)
        engine.process(event)
        tracker.after_event(engine, event)
        execution = engine.execution
        rows.append(dict(kind='event', body=dict(
            input=event, effects=execution.records[start:], cash=execution.cash,
            reserved=execution.reserved, realized=execution.realized,
            equity=execution.equity(engine.md, engine.now), uncertain=execution.uncertain,
            stop_count=len(engine.evaluation.stops))))
    start = len(engine.execution.records)
    report = engine.finish()
    rows.append(dict(kind='end_of_capture', body=engine.execution.records[start:]))
    historical_equal = None
    if policy == 'control':
        comparable = {key: original_report[key] for key in report}
        historical_equal = _primitive(report) == comparable
        if not historical_equal:
            differences = [key for key in report if _primitive(report[key]) != comparable[key]]
            raise AssertionError(f'original-control report mismatch: {config.capital}/{config.model}: {differences}')
    report.update(run_id=run_id, manifest=manifest,
                  results_under_pessimistic_assumptions=config.model == 'pessimistic')
    rows.append(dict(kind='report', body=report))
    diagnostics = tracker.report(engine.now)
    entered = sorted(engine.used_markets)
    all_markets = sorted(engine.md.markets)
    diagnostics.update(
        historical_control_equal=historical_equal, original_run_id=original_report['run_id'],
        evaluation_data='inspected synthetic fixture; not held out', heldout_available=False,
        entered_markets=entered, skipped_markets=[mid for mid in all_markets if mid not in engine.used_markets],
        skipped_markets_count=len(all_markets) - len(entered), total_markets=len(all_markets),
        markets_skipped_percent=D(100) * (len(all_markets) - len(entered)) / len(all_markets),
        net_per_supplied_market=report['pnl_excluding_incentives'] / len(all_markets),
        net_per_entered_market=report['pnl_excluding_incentives'] / len(entered) if entered else None,
        all_skipped_has_edge_evidence=False,
    )
    return _primitive(dict(capital=int(config.capital), model=config.model, policy=policy,
                           report=report, rows=rows, diagnostics=diagnostics))


def run_experiments():
    """Return all 72 fixed diagnostic runs without any filesystem mutation.

    Each record contains ``capital``, ``model``, ``policy``, ``report``, ``rows``
    and ``diagnostics``. Rows are canonical JSON primitives in the same kind/body
    form as the original Journal, with complete original inputs and all effects.
    Control reports are required to equal the historical journal report on every
    engine evaluation field. A mismatch is raised, never suppressed.
    """
    with localcontext(CONTEXT):
        feed_path = local_path('fixtures/btc_5m.jsonl', must_exist=True)
        feed_bytes = feed_path.read_bytes()
        events = [json.loads(line) for line in feed_bytes.splitlines() if line.strip()]
        originals = json.loads(local_path('evidence/comparison.json', must_exist=True).read_text())
        lookup = {(int(row['starting_equity']), row['model']): read_report(row['run_id']) for row in originals}
        runner_hash = digest(local_path('quantos_pm/research_experiments.py', must_exist=True).read_bytes())
        results = []
        for capital in CAPITALS:
            config_path = local_path(f'configs/paper_{capital}.toml', must_exist=True)
            config = load(config_path)
            calibration_bytes = local_path(config.calibration, must_exist=True).read_bytes()
            calibration = json.loads(calibration_bytes)
            base_manifest = dict(
                schema='quantos-pm-fixed-diagnostics-v1', virtual_only=True, mode='paper',
                data_provenance='inspected synthetic mechanics fixture; no held-out data',
                heldout_available=False, policy_manifest=POLICY_DEFINITIONS,
                feed_sha256=digest(feed_bytes), calibration_sha256=digest(calibration_bytes),
                original_config_sha256=digest(config_path.read_bytes()), runner_sha256=runner_hash,
                original_config=config.raw, no_inserted_timers=True,
            )
            for model in MODELS:
                model_config = replace(config, model=model)
                for policy in POLICIES:
                    results.append(_run_one(model_config, calibration, events, policy,
                                            lookup[capital, model], base_manifest))
        for capital in CAPITALS:
            for model in MODELS:
                group = [row for row in results if row['capital'] == capital and row['model'] == model]
                control = next(row for row in group if row['policy'] == 'control')
                eligible = set(control['diagnostics']['entered_markets'])
                for row in group:
                    skipped = eligible - set(row['diagnostics']['entered_markets'])
                    row['diagnostics'].update(
                        control_entered_markets_count=len(eligible),
                        control_entered_markets_skipped_count=len(skipped),
                        control_entered_markets_skipped_percent=str(D(100) * len(skipped) / len(eligible)) if eligible else None)
        return results


def main():
    results = run_experiments()
    output = local_path('evidence/pnl_audit/candidate_experiments.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical(results) + '\n')
    print(canonical(dict(output=str(output), runs=len(results), historical_controls_equal=9,
                         heldout_available=False, edge_demonstrated=False)))


if __name__ == '__main__':
    main()
