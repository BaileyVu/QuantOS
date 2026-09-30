"""Sealed three-variant persistent-carry experiment; no holdout acquisition."""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from research.v1f1.dataset import digest, write_once, HOUR
from research.v1f1.prepare import START
from research.v1f1.evaluate import load_panel, hour, metrics, bootstrap_lower, encode_report
from research.v1f2.carry import signals, simulate


def inputs(data):
    panel, identities = load_panel(data)
    candidates = defaultdict(list)
    for r in pq.read_table(data/'candidates.parquet').to_pylist():
        candidates[(r['timestamp']-START)//HOUR].append(r['symbol'])
    return panel, candidates, identities


def verify(root, data, repo):
    seal = json.loads((root/'preregistration.lock.json').read_text())
    for name, sha in seal['source_files'].items():
        if digest((repo/name).read_bytes()) != sha:
            raise ValueError('sealed source changed: '+name)
    for name, sha in seal['data_files'].items():
        if digest((data/name).read_bytes()) != sha:
            raise ValueError('sealed data changed: '+name)
    return seal


def authorize(root, config):
    if config['test_years'] != [2021, 2022, 2023, 2024] or config['variants'] != ['A', 'B', 'C']:
        raise ValueError('unregistered search or holdout access')
    if (root/'economic.started.json').exists():
        raise ValueError('economic periods consumed; no automatic rerun')


def cache_signals(panel, candidates, variant):
    return {t: signals(panel, candidates.get(t, []), t, variant)
            for year in (2021, 2022, 2023, 2024) for t in range(hour(year), hour(year+1), 8)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--coverage-only', action='store_true')
    args = p.parse_args(); root, data = args.root, args.data
    repo = Path(__file__).resolve().parents[2]
    config = json.loads((repo/'configs/v1f2_preregistration.json').read_text())
    if not args.coverage_only:
        seal = verify(root, data, repo)
        authorize(root, config)
    panel, candidates, identities = inputs(data)
    caches = {}; coverage = {}
    for variant in config['variants']:
        caches[variant] = cache_signals(panel, candidates, variant)
        coverage[variant] = {str(y): sum(len(x[0]) >= 15 for t, x in caches[variant].items()
                                       if hour(y) <= t < hour(y+1)) for y in config['test_years']}
        print(json.dumps({'causal_signals_ready': variant, 'coverage': coverage[variant]}), flush=True)
    if args.coverage_only:
        write_once(root/'coverage.json', encode_report({'data': identities, 'valid_decisions': coverage}))
        return
    with (root/'economic.started.json').open('x') as f:
        json.dump({'seal': digest((root/'preregistration.lock.json').read_bytes()),
                   'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}, f)
    benchmark = json.loads((data/config['benchmark_report']).read_text())
    results = []
    for vi, variant in enumerate(config['variants']):
        for label, fee, slip in config['costs']:
            equity_all = []; daily_all = []; fills_all = []; duration_all = []
            totals = defaultdict(float); folds = []; scale = 1.; active = 0; changes = 0
            data_loss = 0; failed = 0; max_gross = 0.; max_net = 0.
            for year in config['test_years']:
                first, last = hour(year), hour(year+1)
                run = simulate(panel, caches[variant], variant, first, last, fee, slip)
                # Daily PF is used for persistent portfolios without artificial trade episodes.
                m = metrics(run['daily'], run['daily'], run['equity'])
                m['expectancy_per_day'] = m.pop('expectancy_per_episode')
                m['days'] = m.pop('episodes'); m['year'] = year
                m['components_usdt'] = {k: 20*v for k, v in run['totals'].items() if k != 'turnover'}
                m['turnover_equity_units'] = run['totals']['turnover']
                m['actual_traded_notional_usdt'] = 20*sum(f['notional'] for f in run['fills'])
                m['average_holding_hours'] = float(np.mean(run['holding_hours'])) if run['holding_hours'] else None
                m['economic_changes'] = run['economic_changes']; m['data_loss_exits'] = run['data_loss_exits']
                folds.append(m)
                for k, v in run['totals'].items():
                    totals[k] += v if k == 'turnover' else 20*scale*v
                for fill in run['fills']:
                    fills_all.append({'year': year, **fill, 'notional_usdt':20*scale*fill['notional']})
                duration_all.extend(run['holding_hours'])
                equity_all.extend(run['equity'][1:]*scale); daily_all.extend(run['daily'])
                active += run['active_hours']; changes += run['economic_changes']
                data_loss += run['data_loss_exits']; failed += run['failed_baskets']
                max_gross = max(max_gross, run['max_gross']); max_net = max(max_net, run['max_abs_net'])
                scale *= run['equity'][-1]
            m = metrics(daily_all, daily_all, np.asarray(equity_all))
            m['expectancy_per_day'] = m.pop('expectancy_per_episode'); m['days'] = m.pop('episodes')
            m.update(variant=variant, cost=label, fee_bps=fee, slippage_bps=slip, folds=folds,
                     components_usdt=dict(totals), economic_changes=changes, fills=len(fills_all),
                     average_holding_hours=float(np.mean(duration_all)) if duration_all else None,
                     closed_position_spells=len(duration_all), data_loss_exits=data_loss, failed_baskets=failed,
                     active_fraction=active/len(equity_all), max_hourly_gross=max_gross, max_hourly_abs_net=max_net)
            m['actual_traded_notional_usdt'] = sum(f['notional_usdt'] for f in fills_all)
            comparable = next(x for x in benchmark['results'] if x['configuration']=='basis-24h-2x2'
                              and x['fee_bps']==fee and x['slippage_bps']==slip)
            m['v1f1_comparison_configuration'] = 'basis-24h-2x2'
            m['v1f1_turnover_equity_units'] = comparable['turnover_starting_equity_units']
            m['turnover_reduction'] = 1-totals['turnover']/m['v1f1_turnover_equity_units']
            m['turnover_equity_units'] = m['components_usdt'].pop('turnover')
            if label == 'stress':
                m['mean_daily_lower_bound'] = bootstrap_lower(daily_all, config['seed']+vi,
                    draws=10000, block=7, alpha=.05/3)
            components = m['components_usdt']
            if not np.isclose(sum(components[k] for k in ('price','funding'))-sum(components[k] for k in ('fees','slippage')),
                              20*m['net_return'], atol=1e-9, rtol=1e-9):
                raise ValueError('account reconciliation failed')
            if not np.isclose(components['long']+components['short'],20*m['net_return'],atol=1e-9,rtol=1e-9):
                raise ValueError('directional attribution failed')
            artifact = dict(summary=m, hourly_equity=equity_all, daily_returns=daily_all,
                            fills=fills_all, holding_hours=duration_all)
            raw = encode_report(artifact); sha = digest(raw)
            write_once(root/'evaluation'/f'{variant}-{label}-{sha}.json', raw)
            m['artifact_sha256'] = sha; results.append(m)
            print(json.dumps({'completed': variant, 'cost': label}), flush=True)
    gates = []
    for variant in config['variants']:
        group = {x['cost']: x for x in results if x['variant']==variant}
        stress = group['stress']
        checks = {'positive_all_costs': all(x['mean_daily_return']>0 and x['net_return']>0 for x in group.values()),
                  'stress_confidence': stress['mean_daily_lower_bound']>0,
                  'stress_pf': (stress['profit_factor'] or 0)>1,
                  'stable_folds': sum(f['mean_daily_return']>0 and f['net_return']>0 for f in stress['folds'])>=3,
                  'drawdown': stress['max_drawdown']<=.35,
                  'active_coverage': stress['active_fraction']>=.8,
                  'turnover_reduced': stress['turnover_reduction']>=.5,
                  'data_loss': stress['data_loss_exits']/max(1,stress['economic_changes'])<=.01}
        gates.append({'variant': variant, 'pass': all(checks.values()), 'checks': checks})
    report = dict(status='DEVELOPMENT_ALPHA_PASS' if any(g['pass'] for g in gates) else 'NO_GO_ALPHA',
                  layer='economic factor simulation', results=results, gates=gates, seal=seal,
                  coverage=coverage, data_identities=identities, holdouts_accessed=False,
                  mainnet='LOCKED / NOT_APPROVED')
    raw = encode_report(report); path = root/'evaluation'/('development-'+digest(raw)+'.json')
    write_once(path, raw)
    print(json.dumps({'report': str(path), 'sha256': digest(raw), 'status': report['status']}), flush=True)


if __name__ == '__main__': main()
