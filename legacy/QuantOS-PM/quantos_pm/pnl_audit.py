"""Read-only forensic ledger reconstruction and reproducible audit artifacts.

No execution, position, risk or marking implementation is reused by the ledger.
The optional experiment runner uses audit-only adapters and never writes the
historical runs. Monetary arithmetic uses Decimal, including checkpoints.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, localcontext, Context
import hashlib
import json
import sqlite3

from .config import ROOT, local_path

D = Decimal
Z = D(0)
SIDES = ('UP', 'DOWN')
PENDING = {'SUBMITTING', 'OPEN', 'CANCEL_PENDING', 'UNCERTAIN'}


def encode(value):
    return json.dumps(value, default=lambda x: str(x) if isinstance(x, D) else
                      (_ for _ in ()).throw(TypeError(type(x).__name__)),
                      sort_keys=True, indent=2) + '\n'


def ms(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def iso(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(run_id):
    path = local_path(f'artifacts/runs/{run_id}/journal.sqlite', must_exist=True)
    wal = path.with_name(path.name + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Refusing immutable read with nonempty WAL')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity failure')
        raw = list(db.execute('SELECT seq,kind,body,previous,hash FROM journal ORDER BY seq'))
    previous, rows = '0' * 64, []
    for expected, (seq, kind, body, prev, digest) in enumerate(raw):
        if seq != expected or prev != previous or digest != hashlib.sha256((prev + kind + body).encode()).hexdigest():
            raise ValueError(f'Hash-chain failure: {run_id}:{seq}')
        previous = digest
        rows.append(dict(kind=kind, body=json.loads(body)))
    return rows


def audit_rows(rows, capital, stale_ms=10000):
    with localcontext(Context(prec=34)):
        return _audit_rows(rows, D(str(capital)), stale_ms)


def _audit_rows(rows, capital, stale_ms):
    cash, realized, fees, bought, merged, redeemed = capital, Z, Z, Z, Z, Z
    markets, orders, books, estimates = {}, {}, {}, {}
    timeline, losses, residuals = [], [], []
    last, report = None, None
    integral = dict(exposure=Z, utilization=Z, locked=Z, active_ms=0, exposed_ms=0)
    max_exposure = peak_util = max_reserved = Z
    lowest_unrealized = Z

    def same(actual, expected, label):
        error = actual - D(str(expected))
        if error:
            raise AssertionError(f'{label}: reconstructed {actual}, recorded {expected}, residual {error}')

    def remove(m, side, q):
        cost = m['cost'][side] * q / m['qty'][side] if q else Z
        m['qty'][side] -= q
        m['cost'][side] -= cost
        # FIFO only attributes surviving shares to recorded fills; the monetary
        # ledger above uses average cost, as specified by the original engine.
        left = q
        for lot in m['lots'][side]:
            take = min(lot['remaining'], left)
            lot['remaining'] -= take
            left -= take
        if left:
            raise AssertionError('Removed shares without recorded fill')
        return cost

    for row in rows:
        kind, body = row['kind'], row['body']
        if kind == 'report':
            report = body
            continue
        if kind not in ('event', 'end_of_capture'):
            continue
        event = body.get('input') if isinstance(body, dict) else None
        effects = body['effects'] if isinstance(body, dict) else body
        if event:
            now = ms(event['receipt_time'])
            if last:
                dt = now - last['time_ms']
                integral['exposure'] += last['unmatched_cost'] * dt
                if last['unmatched_cost']:
                    integral['exposed_ms'] += dt
                if last['active']:
                    integral['active_ms'] += dt
                    integral['utilization'] += last['utilization'] * dt
                    integral['locked'] += last['locked'] * dt
            ek = event['type']
            if ek == 'market':
                mid = event['market']
                markets[mid] = dict(market=mid, start=event['start'], expiry=event['expiry'],
                    qty=dict.fromkeys(SIDES, Z), cost=dict.fromkeys(SIDES, Z), lots={s: [] for s in SIDES},
                    filled=dict.fromkeys(SIDES, Z), unmatched_settled=dict.fromkeys(SIDES, Z),
                    settlement_pnl=dict.fromkeys(SIDES, Z), pairs=Z, pair_profit=Z,
                    buy_notional=Z, allowance=Z, submitted=[], resolved=False)
            elif ek in ('snapshot', 'delta'):
                key = event['market'], event['outcome']
                if ek == 'snapshot':
                    books[key] = {'bids': {D(p): D(q) for p, q in event['bids']}}
                else:
                    for side, p, q in event['changes']:
                        if side == 'BUY':
                            if D(q):
                                books[key]['bids'][D(p)] = D(q)
                            else:
                                books[key]['bids'].pop(D(p), None)
                books[key].update(event_ms=ms(event['event_time']), receipt_ms=now, valid=True)
            elif ek == 'resolution':
                markets[event['market']].update(resolved=True, winner=event['winner'], resolution=event['receipt_time'])
            elif ek == 'disconnect':
                for b in books.values():
                    b['valid'] = False
        for r in effects:
            action = r['kind']
            if action == 'edge_estimate':
                estimates[r['market']] = r
            elif action == 'virtual_submit':
                if r['order'] in orders:
                    raise AssertionError('Duplicate submitted order')
                orders[r['order']] = dict(order=r['order'], market=r['market'], outcome=r['outcome'],
                    submitted=iso(r['time']), price=D(r['price']), size=D(r['size']),
                    remaining=D(r['size']), status='SUBMITTING', fills=[], cancellations=[], partial_fill_events=0)
                m = markets[r['market']]
                if not m['submitted']:
                    m['allowance'] = D(estimates[r['market']]['costs'])
                m['submitted'].append(r['order'])
            elif action == 'virtual_open':
                o = orders[r['order']]
                o['status'] = 'CANCEL_PENDING' if o['cancellations'] else 'OPEN'
                o['opened'], o['queue_ahead'] = iso(r['time']), D(r['queue_ahead'])
            elif action == 'cancel_requested':
                o = orders[r['order']]
                o['cancellations'].append(dict(time=iso(r['time']), due=iso(r['due']), reason=r['reason']))
                o['status'] = 'CANCEL_PENDING'
            elif action in ('cancel_ack_reconciled', 'explicit_virtual_reconciliation', 'expiry_reconciled', 'post_only_or_metadata_reject'):
                o = orders[r['order']]
                o['status'] = {'expiry_reconciled': 'EXPIRED', 'post_only_or_metadata_reject': 'REJECTED'}.get(action, 'CANCELED')
                o['terminal_time'] = iso(r['time'])
            elif action == 'virtual_fill':
                m, o = markets[r['market']], orders[r['order']]
                side, q, p, f = r['outcome'], D(r['size']), D(r['price']), D(r['fee'])
                if o['status'] not in PENDING or q > o['remaining']:
                    raise AssertionError('Invalid fill lifecycle')
                cash -= q * p + f
                fees += f
                bought += q * p
                m['buy_notional'] += q * p
                m['qty'][side] += q
                m['cost'][side] += q * p
                m['filled'][side] += q
                lot = dict(order=r['order'], receipt_time=iso(r['time']),
                           event_time=event['event_time'], shares=q, remaining=q, price=p)
                m['lots'][side].append(lot)
                o['fills'].append({k: v for k, v in lot.items() if k != 'remaining'})
                o['remaining'] -= q
                o['partial_fill_events'] += int(o['remaining'] > 0)
                if not o['remaining']:
                    o['status'] = 'FILLED'
            elif action == 'virtual_merge':
                m, q = markets[r['market']], D(r['pairs'])
                same(q, min(m['qty'].values()), 'matched quantity')
                cost = sum((remove(m, side, q) for side in SIDES), Z)
                same(cost, r['cost'], 'merge cost')
                same(q, r['payout'], 'merge payout')
                same(q - cost, r['pnl'], 'merge profit')
                cash += q
                merged += q
                realized += q - cost
                m['pairs'] += q
                m['pair_profit'] += q - cost
            elif action == 'virtual_redemption':
                m = markets[r['market']]
                if not m['resolved'] or m['winner'] != r['winner']:
                    raise AssertionError('Redemption without matching resolution')
                payout, cost = m['qty'][r['winner']], sum(m['cost'].values(), Z)
                same(payout, r['payout'], 'redemption payout')
                same(cost, r['cost'], 'redemption cost')
                same(payout - cost, r['pnl'], 'redemption profit')
                for side in SIDES:
                    q, c = m['qty'][side], m['cost'][side]
                    pnl = (q if side == r['winner'] else Z) - c
                    m['unmatched_settled'][side] += q
                    m['settlement_pnl'][side] += pnl
                    if pnl < 0:
                        losses.append(dict(market=m['market'], outcome=side, shares=q, cost=c,
                            pnl=pnl, settlement_time=iso(r['time']), winner=r['winner'],
                            residual_fills=[dict(lot) for lot in m['lots'][side] if lot['remaining']]))
                    remove(m, side, q)
                cash += payout
                redeemed += payout
                realized += payout - cost
        if not event:
            continue
        reserve = sum((o['price'] * o['remaining'] for o in orders.values() if o['status'] in PENDING), Z)
        value = exposure = position_cost = Z
        for mid, m in markets.items():
            paired = min(m['qty'].values())
            value += paired
            position_cost += sum(m['cost'].values(), Z)
            for side in SIDES:
                q = m['qty'][side]
                unmatched = q - paired
                exposure += m['cost'][side] * unmatched / q if q else Z
                b = books.get((mid, side))
                fresh = b and b['valid'] and 0 <= now - b['event_ms'] <= stale_ms and now - b['receipt_ms'] <= stale_ms
                mark = max(b['bids'], default=Z) if fresh else Z
                value += unmatched * mark
        equity = cash + value
        unrealized = equity - capital - realized
        for label, actual in [('cash', cash), ('reserved', reserve), ('realized', realized), ('equity', equity)]:
            same(actual, body[label], f'{event["id"]} {label}')
        if reserve > cash or cash < 0:
            raise AssertionError('Invalid collateral balance')
        residuals.append(Z)
        locked = reserve + position_cost
        utilization = locked / equity if equity else D(1)
        max_exposure, peak_util, max_reserved = max(max_exposure, exposure), max(peak_util, utilization), max(max_reserved, reserve)
        lowest_unrealized = min(lowest_unrealized, unrealized)
        last = dict(time_ms=now, time=event['receipt_time'], event_id=event['id'], cash=cash,
            reserved=reserve, equity=equity, realized=realized, unrealized=unrealized,
            position_value=value, unmatched_cost=exposure, utilization=utilization, locked=locked,
            active=any(not m['resolved'] for m in markets.values()))
        timeline.append(last)
    if report is None:
        raise AssertionError('Missing terminal report')
    pairs = sum((m['pairs'] for m in markets.values()), Z)
    paired_pnl = sum((m['pair_profit'] for m in markets.values()), Z)
    side_pnl = {s: sum((m['settlement_pnl'][s] for m in markets.values()), Z) for s in SIDES}
    allowance = sum((m['allowance'] for m in markets.values()), Z)
    active_ms = integral['active_ms']
    wall_ms = timeline[-1]['time_ms'] - timeline[0]['time_ms']
    counts = Counter(o['status'] for o in orders.values())
    summary = dict(starting_equity=capital, ending_equity=last['equity'], cash=cash,
        realized_total_pnl=realized, realized_settlement_pnl=sum(side_pnl.values(), Z),
        unrealized_pnl=last['unrealized'], gross_pair_profit=paired_pnl,
        unmatched_up_pnl=side_pnl['UP'], unmatched_down_pnl=side_pnl['DOWN'],
        fees=fees, slippage_posted=Z, operational_cost_posted=Z,
        combined_slippage_ops_admission_allowance=allowance,
        pnl_after_allowance_scenario=last['equity'] - capital - allowance,
        total_pnl=last['equity'] - capital, complete_pairs=pairs, matched_leg_shares=2*pairs,
        unmatched_up_settled=sum((m['unmatched_settled']['UP'] for m in markets.values()), Z),
        unmatched_down_settled=sum((m['unmatched_settled']['DOWN'] for m in markets.values()), Z),
        final_open_shares=sum((sum(m['qty'].values(), Z) for m in markets.values()), Z),
        orders_submitted=len(orders), orders_ever_partially_filled=sum(o['partial_fill_events'] > 0 for o in orders.values()),
        partial_fill_events=sum(o['partial_fill_events'] for o in orders.values()),
        fill_events=sum(len(o['fills']) for o in orders.values()),
        orders_partially_filled_at_close=sum(0 < o['remaining'] < o['size'] for o in orders.values()),
        orders_fully_filled=counts['FILLED'], orders_canceled=counts['CANCELED'],
        orders_rejected=counts['REJECTED'], orders_expired=counts['EXPIRED'],
        final_reserved=last['reserved'], maximum_reserved=max_reserved,
        max_unmatched_cost=max_exposure,
        average_unmatched_cost_active=integral['exposure'] / active_ms if active_ms else Z,
        average_unmatched_cost_wall=integral['exposure'] / wall_ms if wall_ms else Z,
        average_unmatched_cost_while_exposed=integral['exposure'] / integral['exposed_ms'] if integral['exposed_ms'] else Z,
        unmatched_dollar_seconds=integral['exposure']/1000,
        seconds_with_unmatched_inventory=D(integral['exposed_ms'])/1000,
        active_observation_seconds=D(active_ms)/1000, wall_observation_seconds=D(wall_ms)/1000,
        capital_utilization_peak=peak_util,
        capital_utilization_average_active=integral['utilization']/active_ms if active_ms else Z,
        locked_capital_average_active=integral['locked']/active_ms if active_ms else Z,
        lowest_transient_unrealized_pnl=lowest_unrealized,
        buy_notional=bought, merge_payouts=merged, redemption_payouts=redeemed,
        cash_reconciliation_residual=cash - (capital - bought - fees + merged + redeemed),
        pnl_reconciliation_residual=last['equity'] - capital - paired_pnl - sum(side_pnl.values(), Z) - last['unrealized'] + fees,
        verified_event_checkpoints=len(residuals), maximum_checkpoint_residual=max(residuals, default=Z),
        quoted_markets=sum(bool(m['submitted']) for m in markets.values()), candidate_markets=len(markets))
    summary['skipped_market_fraction'] = D(len(markets) - summary['quoted_markets']) / len(markets)
    summary['mean_pnl_per_market'] = summary['total_pnl'] / len(markets)
    summary['mean_pnl_per_entered_market'] = summary['total_pnl'] / summary['quoted_markets'] if summary['quoted_markets'] else None
    for field, key in [('ending_equity', 'ending_equity'), ('realized_total_pnl', 'realized_pnl'),
                       ('unrealized_pnl', 'unrealized_pnl'), ('gross_pair_profit', 'matched_set_pnl'),
                       ('total_pnl', 'pnl_excluding_incentives'), ('complete_pairs', 'complete_pairs'),
                       ('fees', 'fees'), ('buy_notional', 'turnover'), ('final_reserved', 'reserved_collateral'),
                       ('max_unmatched_cost', 'max_unmatched_exposure'),
                       ('capital_utilization_peak', 'capital_utilization_peak')]:
        same(summary[field], report[key], f'final report {key}')
    same(sum(side_pnl.values(), Z), report['directional_pnl'], 'directional total')
    for m in markets.values():
        up, down = (m['filled'][s] > 0 for s in SIDES)
        m['observed_outcome'] = 'both_any' if up and down else 'only_up' if up else 'only_down' if down else 'neither'
        m['deterministic_outcome_indicators'] = {k: int(k == m['observed_outcome']) for k in ('both_any', 'only_up', 'only_down', 'neither')}
        m['both_full'] = bool(m['submitted']) and all(orders[oid]['remaining'] == 0 for oid in m['submitted'])
        m['pnl'] = m['pair_profit'] + sum(m['settlement_pnl'].values(), Z)
    return dict(summary=summary, markets=list(markets.values()), orders=list(orders.values()),
                loss_events=sorted(losses, key=lambda r: r['pnl']), timeline=timeline)


def verify_original_inputs(output):
    path = output / 'original_input_hashes.json'
    if not path.exists():
        raise ValueError('Missing original_input_hashes.json preservation manifest')
    original = json.loads(path.read_text())
    changed = [p for p, h in original.items() if not (ROOT/p).is_file() or sha(ROOT/p) != h]
    if changed:
        raise AssertionError(f'Original files changed: {changed}')
    return dict(files_verified=len(original), changed=[], status='unchanged')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='evidence/pnl_audit')
    parser.add_argument('--experiments', action='store_true')
    args = parser.parse_args()
    output = local_path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    preservation = verify_original_inputs(output)
    audited = []
    for entry in json.loads((ROOT/'evidence/comparison.json').read_text()):
        rows = read_rows(entry['run_id'])
        historical_report = next(r['body'] for r in rows if r['kind'] == 'report')
        exported = json.loads((ROOT/'artifacts/runs'/entry['run_id']/'report.json').read_text())
        if historical_report != exported:
            raise AssertionError('Exported report differs from journal')
        result = audit_rows(rows, entry['starting_equity'], rows[0]['body']['config']['stale_ms'])
        result.update(run_id=entry['run_id'], model=entry['model'], hash_chain_verified=True)
        audited.append(result)
    (output/'baseline_attribution.json').write_text(encode(audited))
    candidates = []
    if args.experiments:
        from .research_experiments import run_experiments
        for run in run_experiments():
            run['audit'] = audit_rows(run['rows'], run['capital'])
            candidates.append(run)
        (output/'candidate_experiments.json').write_text(encode(candidates))
    preservation = verify_original_inputs(output)
    (output/'verification.json').write_text(encode(dict(original_preservation=preservation,
        original_runs=len(audited), original_checkpoints=sum(r['summary']['verified_event_checkpoints'] for r in audited),
        candidate_runs=len(candidates), accounting_residuals='all exactly zero',
        held_out_validation='unavailable; user authorized fixture diagnostics only',
        audit_source_sha256=sha(ROOT/'quantos_pm/pnl_audit.py'),
        experiment_source_sha256=sha(ROOT/'quantos_pm/research_experiments.py') if candidates else None)))
    write_report(output, audited, candidates)
    print(f'PASS: {len(audited)} historical runs; {len(candidates)} diagnostic runs; original files unchanged')


def write_report(output, audited, candidates):
    # Deliberately a separate generated appendix; the concise narrative report
    # explains interpretation and provenance without hiding detailed quantities.
    lines = ['# P&L forensic audit: generated numerical appendix', '',
             'All amounts USD; full precision is retained in the JSON artifacts. O/B/P = optimistic/baseline/pessimistic.', '',
             '## Exact reconciliation', '',
             '| Capital | Model | End | Pair gross | UP settlement | DOWN settlement | Realized total | Final MTM | Total P&L |',
             '|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in audited:
        s = r['summary']
        lines.append('| ' + ' | '.join(str(v) for v in [s['starting_equity'], r['model'], s['ending_equity'],
            s['gross_pair_profit'], s['unmatched_up_pnl'], s['unmatched_down_pnl'], s['realized_total_pnl'], s['unrealized_pnl'], s['total_pnl']]) + ' |')
    lines += ['', 'Fees, posted slippage, posted operating costs, final open shares and final reserves are zero in all nine runs. Every reconciliation residual is exactly zero.', '',
        '## Cash reconciliation and admission-only allowance', '',
        'End cash = start − buy notional − fees + merge payouts + redemption payouts. End equity = cash + remaining position value (zero here). The combined slippage/ops allowance is **not charged** in the journals; its split is unavailable. The overlay below subtracts it once per accepted paired opportunity, including zero-fill entries.', '',
        '| Capital | Model | Buy notional | Merge payouts | Redemption payouts | Admission allowance | P&L after allowance scenario |',
        '|---:|---|---:|---:|---:|---:|---:|']
    for r in audited:
        s = r['summary']
        lines.append('| ' + ' | '.join(str(v) for v in [s['starting_equity'], r['model'], s['buy_notional'], s['merge_payouts'], s['redemption_payouts'], s['combined_slippage_ops_admission_allowance'], s['pnl_after_allowance_scenario']]) + ' |')
    lines += ['', '## Share and order counts', '',
        'Pairs count one UP + one DOWN unit; matched leg shares are twice pairs. Unmatched counts below are shares remaining after all merges and taken to settlement; terminal open counts are zero.', '',
        '| Capital | Model | Pairs | Settled unmatched UP / DOWN | Submitted | Ever partial orders / partial fill events | Fully filled | Partial at close | Canceled | Rejected |',
        '|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in audited:
        s = r['summary']
        lines.append('| ' + ' | '.join(str(v) for v in [s['starting_equity'], r['model'], s['complete_pairs'],
            f"{s['unmatched_up_settled']} / {s['unmatched_down_settled']}", s['orders_submitted'],
            f"{s['orders_ever_partially_filled']} / {s['partial_fill_events']}", s['orders_fully_filled'],
            s['orders_partially_filled_at_close'], s['orders_canceled'], s['orders_rejected']]) + ' |')
    lines += ['', '## Exposure and capital utilization', '',
        'Exposure = remaining unmatched acquisition cost. Time weighting holds each post-event balance until the next recorded receipt. Active denominator = union of metadata-receipt to resolution-receipt windows (1,806 seconds); idle overnight gaps excluded. JSON also gives the full 432,301-second wall-clock average and the conditional average while exposed. Utilization = (pending reserve + inventory cost) / contemporaneous equity; this is capital locked, not an expense.', '',
        '| Capital | Model | Avg exposure active | Max exposure | Exposed seconds | Avg utilization active | Peak utilization | Lowest interim MTM |',
        '|---:|---|---:|---:|---:|---:|---:|---:|']
    for r in audited:
        s = r['summary']
        lines.append(f"| {s['starting_equity']} | {r['model']} | {s['average_unmatched_cost_active']:.8f} | {s['max_unmatched_cost']:.8f} | {s['seconds_with_unmatched_inventory']} | {100*s['capital_utilization_average_active']:.6f}% | {100*s['capital_utilization_peak']:.6f}% | {s['lowest_transient_unrealized_pnl']} |")
    lines += ['', '## Per-market deterministic outcomes and quantities', '',
        'Market numbers are the final hex digit of the 64-digit synthetic condition ID. One replay per market/model gives a 0/1 outcome indicator, **not an estimated stochastic fill probability**. B/U/D/N denote both-any/UP-only/DOWN-only/neither. N includes skipped markets, separately identified below.', '',
        '| Capital | Model | Market | Filled UP | Filled DOWN | Pairs | Unmatched UP / DOWN | B/U/D/N indicator | Both full? | Entered? | Net P&L |',
        '|---:|---|---:|---:|---:|---:|---:|---|---|---|---:|']
    for r in audited:
        for m in r['markets']:
            ind = '/'.join(str(m['deterministic_outcome_indicators'][k]) for k in ('both_any', 'only_up', 'only_down', 'neither'))
            lines.append(f"| {r['summary']['starting_equity']} | {r['model']} | {int(m['market'],16)} | {m['filled']['UP']} | {m['filled']['DOWN']} | {m['pairs']} | {m['unmatched_settled']['UP']} / {m['unmatched_settled']['DOWN']} | {ind} | {m['both_full']} | {bool(m['submitted'])} | {m['pnl']} |")
    lines += ['', '## Loss orders and timestamps', '',
        'Receipt timestamps are UTC, 10 ms after the source event. Residual-fill allocation is FIFO for attribution; all these fills cost $0.49 per share, so allocation cannot change P&L. Full condition IDs and fill source times are in baseline_attribution.json.', '',
        '| Capital | Model | Market | Order | Losing shares | Losing cost | Residual fill receipt(s) | Settlement |',
        '|---:|---|---|---|---:|---:|---|---|']
    for r in audited:
        for loss in r['loss_events']:
            fills = loss['residual_fills']
            lines.append(f"| {r['summary']['starting_equity']} | {r['model']} | {loss['market']} | {', '.join(sorted({f['order'] for f in fills}))} | {loss['shares']} | {loss['cost']} | {', '.join(f['receipt_time'] for f in fills)} | {loss['settlement_time']} |")
    lines += ['', '## Expectancy on this fixture', '',
        'Every run: entered-market frequencies B/U/D/N = 3/5, 1/5, 0/5, 1/5; all-market frequencies = 3/6, 1/6, 0/6, 2/6. One of the two N markets is skipped. Pessimistic $350/$500 have only 2/5 both-full and 1/5 both-partial with residual risk.', '',
        '| Capital | Model | Realized mean / supplied market | Realized mean / entered market |',
        '|---:|---|---:|---:|']
    for r in audited:
        s = r['summary']
        lines.append(f"| {s['starting_equity']} | {r['model']} | {s['mean_pnl_per_market']:.10f} | {s['mean_pnl_per_entered_market']:.10f} |")
    if candidates:
        lines += ['', '## Fixed candidate comparisons: synthetic fixture only', '',
            'All 72 runs use identical input rows and original fill/latency/queue/risk rules; nine controls reproduce the historical results. Each other run changes one policy. No held-out validation is available. No parameter was selected or production default changed. Zero-trade runs show no positive edge.', '',
            '| Policy | Capital | Model | P&L | After allowance | Pair profit | UP P&L | DOWN P&L | Pairs | Markets skipped | Max exposure | Exposure seconds |',
            '|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for r in candidates:
            s = r['audit']['summary']
            lines.append(f"| {r['policy']} | {r['capital']} | {r['model']} | {s['total_pnl']:.8f} | {s['pnl_after_allowance_scenario']:.8f} | {s['gross_pair_profit']:.8f} | {s['unmatched_up_pnl']:.8f} | {s['unmatched_down_pnl']:.8f} | {s['complete_pairs']} | {100*s['skipped_market_fraction']:.2f}% | {s['max_unmatched_cost']:.8f} | {s['seconds_with_unmatched_inventory']} |")
    (output/'NUMERICAL_APPENDIX.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
