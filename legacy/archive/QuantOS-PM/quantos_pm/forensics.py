"""Read-only wallet research. API coverage is not an exact strategy reconstruction."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import localcontext
import re
import time
from .adapters.public import PublicData
from .domain.common import D, ZERO, CONTEXT, iso
from .config import local_path
from .storage import canonical, digest


def pages(api, path, params, max_pages, evidence):
    rows, cursors = [], set()
    params = dict(params, limit=1000)
    for index in range(max_pages):
        cached = next((p for p in evidence if p['path'] == path and p['params'] == params), None)
        response = cached['response'] if cached else api.get('data', path, params)
        if cached is None:
            evidence.append(dict(path=path, params=dict(params), response=response))
        if not isinstance(response.get('data'), list) or 'next_cursor' not in response.get('pagination', {}):
            raise ValueError('invalid v2 envelope/pagination')
        rows.extend(response['data'])
        cursor = response['pagination']['next_cursor']
        if cursor is None:
            return rows, True
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError('repeated or invalid opaque cursor')
        cursors.add(cursor)
        params['cursor'] = cursor
    return rows, False


def trade_key(row):
    return tuple(str(row.get(k, '')) for k in ('transaction_hash', 'proxy_wallet', 'condition_id', 'token_id', 'side', 'size', 'price', 'timestamp'))


def classify(trades, takers, takers_complete):
    taker_counts = Counter(trade_key(r) for r in takers)
    all_counts = Counter(trade_key(r) for r in trades)
    result = []
    for row in trades:
        key = trade_key(row)
        role = 'UNKNOWN'
        if all_counts[key] == 1 and taker_counts[key] == 1:
            role = 'CONFIRMED_TAKER'
        elif takers_complete and all_counts[key] == 1 and taker_counts[key] == 0:
            # Evidence is the documented v2 taker-only filter with identical window,
            # not the execution price. All-stream completeness is checked by caller.
            role = 'CONFIRMED_MAKER'
        result.append(dict(row, execution_class=role))
    return result


def reconstruct(activity, positions):
    """Cost-basis reconstruction of supported binary actions; gaps stay explicit."""
    inventory = defaultdict(lambda: {'qty': ZERO, 'cost': ZERO})
    tokens = defaultdict(set)
    for r in positions+activity:
        if r.get('token_id') and r.get('condition_id'):
            tokens[r['condition_id']].add(r['token_id'])
        if r.get('opposite_token_id'):
            tokens[r['condition_id']].add(r['opposite_token_id'])
    pnl = dict(matched_sets=ZERO, early_exits=ZERO, directional_inventory=None,
               spread_capture=None, rebates=None, rewards=None, fees=None)
    cash = defaultdict(lambda: ZERO)
    gaps, receipts = [], []
    # Preserve API stable ordering for equal timestamps; no invented intra-block order.
    for row in activity:
        kind, cid = row['type'].upper(), row.get('condition_id', '')
        q, amount = D(row.get('size', '0')), D(row.get('usdc_size', '0'))
        key = (cid, row.get('token_id', ''))
        p = inventory[key]
        tx = row.get('transaction_hash')
        if tx:
            receipts.append(dict(transaction_hash=tx, polygon_url=f'https://polygonscan.com/tx/{tx}', verified=False))
        if kind == 'TRADE':
            if row['side'] == 'BUY':
                p['qty'] += q
                p['cost'] += amount
                cash['buys'] -= amount
            elif row['side'] == 'SELL':
                cash['sells'] += amount
                if p['qty'] < q:
                    gaps.append(dict(reason='sell exceeds reconstructed inventory', transaction=tx))
                    continue
                cost = p['cost']*q/p['qty'] if p['qty'] else ZERO
                p['qty'] -= q
                p['cost'] -= cost
                pnl['early_exits'] += amount-cost
            else:
                gaps.append(dict(reason='unknown trade side', transaction=tx))
        elif kind == 'SPLIT':
            cash['splits'] -= amount
            if len(tokens[cid]) != 2:
                gaps.append(dict(reason='split token set unresolved', transaction=tx))
                continue
            for token in tokens[cid]:
                inventory[cid, token]['qty'] += q
                inventory[cid, token]['cost'] += amount/2
        elif kind == 'MERGE':
            cash['merges'] += amount
            if len(tokens[cid]) != 2 or any(inventory[cid, t]['qty'] < q for t in tokens[cid]):
                gaps.append(dict(reason='merge inventory unresolved', transaction=tx))
                continue
            cost = ZERO
            for token in tokens[cid]:
                p = inventory[cid, token]
                removed = p['cost']*q/p['qty'] if p['qty'] else ZERO
                p['qty'] -= q
                p['cost'] -= removed
                cost += removed
            pnl['matched_sets'] += amount-cost
        elif kind == 'REDEEM':
            cash['redemptions'] += amount
            # Aggregate redeems can burn multiple outcomes. Without per-token chain
            # evidence, don't assign gross proceeds to directional profit.
            gaps.append(dict(reason='redemption token burn/cost requires transaction receipt', transaction=tx))
        elif kind in ('DEPOSIT', 'WITHDRAWAL', 'WITHDRAW'):
            cash[kind.lower()] += amount if kind == 'DEPOSIT' else -amount
        elif kind in ('MAKER_REBATE', 'TAKER_REBATE', 'REWARD'):
            cash[kind.lower()] += amount
            bucket = 'rewards' if kind == 'REWARD' else 'rebates'
            pnl[bucket] = (pnl[bucket] or ZERO)+amount
        else:
            cash['unclassified_'+kind] += amount
            gaps.append(dict(reason='unattributed activity '+kind, transaction=tx))
    position_total = sum((D(p['total_pnl']) for p in positions), ZERO)
    known = pnl['matched_sets']+pnl['early_exits']
    return dict(attribution=pnl, cash_flows=dict(cash), api_position_total_pnl=position_total,
                unexplained_difference=position_total-known, gaps=gaps,
                signed_observed_cash_change=sum(cash.values(), ZERO),
                polygon_evidence=receipts, exact_reconstruction=False,
                notes=['Early exits means realized sells; causality/spread versus directional motive remains unknown.',
                       'Position snapshots and activity are not atomic; fees/incentives must not be double counted.',
                       'No maker intent, queue rank, offchain cancels or reversal model can be recovered from prices alone.'])


def research(wallet, max_pages=10000, resume=None):
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', wallet) or not 1 <= max_pages <= 10000:
        raise ValueError('invalid wallet or page bound')
    import json
    now = int(time.time())
    directory = local_path(resume) if resume else local_path(f'artifacts/research/{wallet.lower()}-{time.time_ns()}')
    raw = []
    if resume:
        old = json.loads(local_path(directory/'report.json', must_exist=True).read_text())
        if old['wallet'] != wallet:
            raise ValueError('resume wallet mismatch')
        now = int(datetime.fromisoformat(old['retrieved_at'].replace('Z', '+00:00')).timestamp())
        raw = json.loads(local_path(directory/'raw-pages.json', must_exist=True).read_text(), parse_float=D)
    else:
        directory.mkdir(parents=True)
    api, datasets, coverage, errors = PublicData(), {}, {}, []
    base = dict(user=wallet, start=1, end=now)
    specs = [
        ('activity', '/v2/activity', dict(base, sort_direction='ASC', exclude_deposits_withdrawals='false')),
        ('tips', '/v2/activity', dict(base, type='TIP', sort_direction='ASC', exclude_deposits_withdrawals='false')),
        ('trades', '/v2/trades', dict(base, taker_only='false', filter_amount='0.00000001')),
        ('taker_trades', '/v2/trades', dict(base, taker_only='true', filter_amount='0.00000001')),
        ('open_positions', '/v2/positions', dict(user=wallet, status='OPEN', filter_amount='0', include_archived='true')),
        ('closed_positions', '/v2/positions', dict(user=wallet, status='CLOSED', filter_amount='0')),
    ]
    for name, path, params in specs:
        try:
            rows, complete = pages(api, path, params, max_pages, raw)
            datasets[name] = rows
            coverage[name] = dict(rows=len(rows), complete=complete)
        except Exception as error:
            datasets[name] = []
            coverage[name] = dict(rows=0, complete=False)
            errors.append(f'{name}: {type(error).__name__}: {error}')
        # Save each acquired endpoint, including partial-page raw evidence.
        local_path(directory/f'{name}.json').write_text(canonical(datasets[name])+'\n')
        local_path(directory/'raw-pages.json').write_text(canonical(raw)+'\n')
    for name, path, params in [('profile', '/v2/user-stats', {'user': wallet}),
                               ('pnl_series', '/v2/user-pnl', {'user': wallet, 'interval': 'all', 'fidelity': '1d'})]:
        try:
            datasets[name] = api.get('data', path, params)['data']
        except Exception as error:
            datasets[name] = None
            errors.append(f'{name}: {type(error).__name__}: {error}')
    # Explicit monthly queries make April's zero-state testable even when the full
    # activity walk exceeds the safety cap. No blank history is fabricated.
    months = {}
    for month in (4, 5, 6):
        start = int(datetime(2026, month, 1, tzinfo=timezone.utc).timestamp())
        end = int(datetime(2026, month+1, 1, tzinfo=timezone.utc).timestamp())-1
        try:
            rows, complete = pages(api, '/v2/activity', dict(user=wallet, start=start, end=end,
                                   sort_direction='ASC', exclude_deposits_withdrawals='false'), max_pages, raw)
            months[f'2026-{month:02}'] = dict(activity_rows=len(rows), complete=complete,
                conclusion='no wallet activity returned for this month' if complete and not rows else 'activity observed' if rows else 'unknown')
            local_path(directory/f'2026-{month:02}.json').write_text(canonical(rows)+'\n')
        except Exception as error:
            months[f'2026-{month:02}'] = dict(activity_rows=None, complete=False, conclusion='unknown; retrieval failed')
            errors.append(f'month {month}: {error}')
    classified = classify(datasets['trades'], datasets['taker_trades'],
                          coverage['taker_trades']['complete'] and coverage['trades']['complete'])
    with localcontext(CONTEXT):
        accounting = reconstruct(datasets['activity'], datasets['open_positions']+datasets['closed_positions'])
    profile = datasets['profile'] or {}
    atoms = profile.get('all_time_pnl') or {}
    # These are API-reported credited atoms, not inferred rebates or a second
    # adjustment to the API's already-composed position/economic PnL.
    accounting['credited_api_atoms'] = {k: atoms.get(k) for k in
        ('maker_rebate', 'taker_rebate', 'reward_income', 'fees', 'fees_paid',
         'deposits', 'withdrawals', 'position_pnl', 'economic_pnl', 'timestamp')}
    accounting['incentive_discrepancies'] = {}
    for atom, flow in [('maker_rebate', 'maker_rebate'), ('taker_rebate', 'taker_rebate'), ('reward_income', 'reward')]:
        if atoms.get(atom) is not None and flow in accounting['cash_flows']:
            accounting['incentive_discrepancies'][atom] = accounting['cash_flows'][flow]-D(atoms[atom])
    roles = dict(Counter(r['execution_class'] for r in classified))
    # Hypotheses are ranked only by collected evidence; unsupported ties stay ties.
    hypotheses = [dict(rank=1 if roles.get('CONFIRMED_MAKER', 0) else None, hypothesis='maker liquidity provision',
                       evidence=f"{roles.get('CONFIRMED_MAKER', 0)} uniquely classified maker rows", confidence='limited'),
                  dict(rank=None, hypothesis='two-sided inventory balancing and spread capture',
                       evidence='requires matched trade chronology and queue/order evidence', confidence='unverified'),
                  dict(rank=None, hypothesis='incentive harvesting', evidence='inspect credited PnL atoms; not inferred from trading volume', confidence='unverified'),
                  dict(rank=None, hypothesis='directional exposure / early loser exits',
                       evidence='no validated counterfactual reversal/oracle model', confidence='unverified')]
    report = dict(wallet=wallet, retrieved_at=iso(now*1000), status='INCOMPLETE_ATTRIBUTION',
                  full_public_api_walk=all(c['complete'] for c in coverage.values()), coverage=coverage,
                  months=months, profile=datasets['profile'], pnl_series=datasets['pnl_series'],
                  execution_classification=roles, hypotheses=hypotheses, accounting=accounting,
                  errors=errors, exact_wallet_replica=False,
                  limitations=['inactive positions may be excluded by the documented API',
                               'Polygon receipts linked but not fetched/decoded',
                               'API stable cursors do not establish complete chain accounting',
                               'credited_api_atoms are reported separately and are not independently chain-reconciled',
                               'matched settlement and directional attribution blocked by unresolved redemption burns'],
                  evidence_directory=str(directory))
    local_path(directory/'classified-trades.json').write_text(canonical(classified)+'\n')
    local_path(directory/'raw-pages.json').write_text(canonical(raw)+'\n')
    local_path(directory/'report.json').write_text(canonical(report)+'\n')
    brief = (f"# Wallet evidence report\n\nWallet: `{wallet}`\n\nStatus: INCOMPLETE_ATTRIBUTION. Exact replica: false.\n\n"
             + '\n'.join(f"- {m}: {v['conclusion']}; rows={v['activity_rows']}; complete={v['complete']}" for m,v in months.items())
             + '\n\nExecution classifications: '+canonical(roles)+'\n\n'
             + '\n'.join('- '+x for x in report['limitations'])+'\n\nErrors: '+canonical(errors)+'\n')
    local_path(directory/'report.md').write_text(brief)
    return {k: report[k] for k in ('wallet', 'status', 'full_public_api_walk', 'coverage', 'months',
                                 'execution_classification', 'errors', 'evidence_directory')}
