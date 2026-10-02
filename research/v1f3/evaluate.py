"""One-shot sealed development evaluation for frozen Alpha plus bounded Risk."""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import subprocess
import numpy as np
from research.v1f1.dataset import digest, write_once
from research.v1f1.evaluate import hour, metrics, bootstrap_lower, encode_report
from research.v1f2.evaluate import inputs, cache_signals
from research.v1f2.carry import simulate
from research.v1f3.risk import IntentTape, snapshot, run_overlay

BASE='350c3d5f5cc190bb7f5fd86f2bc05c9e62b3e202'
FROZEN=['research/v1f2/carry.py','configs/v1f2_preregistration.json',
        'docs/V1_F2_PERSISTENT_CARRY_PREREGISTRATION.md']
SOURCES=FROZEN+['research/v1f3/risk.py','research/v1f3/recovery.py',
        'research/v1f3/evaluate.py','configs/v1f3_preregistration.json',
        'docs/V1_F3_CARRY_RISK_PREREGISTRATION.md','research/v1f2/evaluate.py',
        'research/v1f1/evaluate.py','research/v1f1/factor.py',
        'research/v1f1/prepare.py','research/v1f1/dataset.py']


def candidate_grid(config):
    if config['development_years']!=[2021,2022,2023,2024] or config['variant']!='A':
        raise ValueError('development/Alpha boundary changed')
    grid=[dict(id=f'v{int(v*100)}-{d}-c{int(c*100)}',vol_target=v,throttle=d,carry_threshold=c)
          for v,d,c in itertools.product(config['vol_targets'],config['throttles'],config['carry_thresholds'])]
    if len(grid)!=12 or len({x['id'] for x in grid})!=12:raise ValueError('unregistered search size')
    return grid


def frozen_alpha(repo):
    for name in FROZEN:
        original=subprocess.check_output(['git','show',BASE+':'+name],cwd=repo)
        # Git normalizes text; account for checkout CRLF without changing semantics.
        if (repo/name).read_bytes().replace(b'\r\n',b'\n')!=original.replace(b'\r\n',b'\n'):
            raise ValueError('F2 Alpha mutated: '+name)


def seal(root,data,f2,repo):
    frozen_alpha(repo)
    config=json.loads((repo/'configs/v1f3_preregistration.json').read_text());candidate_grid(config)
    if (root/'economic.started.json').exists():raise ValueError('economic access already consumed')
    data_files={str(p.resolve()):digest(p.read_bytes()) for p in
                [data/'candidates.parquet',data/'context_requests.json',root/'universe-recovery-audit.json',
                 *sorted((data/'manifests').glob('*.json')),*sorted((f2/'evaluation').glob('development-*.json'))]}
    result=dict(utc=datetime.now(timezone.utc).isoformat(),base_commit=BASE,
                source_files={n:digest((repo/n).read_bytes()) for n in SOURCES},
                external_files=data_files,python_hash_seed='0',mainnet='LOCKED / NOT_APPROVED')
    write_once(root/'preregistration.lock.json',encode_report(result))
    return result


def verify(root,repo):
    value=json.loads((root/'preregistration.lock.json').read_text())
    frozen_alpha(repo)
    for n,sha in value['source_files'].items():
        if digest((repo/n).read_bytes())!=sha:raise ValueError('sealed source changed: '+n)
    for n,sha in value['external_files'].items():
        if digest(Path(n).read_bytes())!=sha:raise ValueError('sealed evidence changed: '+n)
    return value


def recover(panel,root):
    """Apply verified exact observations only; never rewrite the immutable sources."""
    audit=json.loads((root/'universe-recovery-audit.json').read_text())
    updated={};count=0
    for r in audit['records']:
        row=r['recovered']
        if row is None:continue
        successful=[a for a in r['attempts'] if 'sha256' in a]
        if len(successful)!=2:raise ValueError('recovery lacks two authoritative sources')
        for a in successful:
            if digest((root/'recovery-raw'/a['sha256']).read_bytes())!=a['sha256']:
                raise ValueError('recovery raw changed')
        s=r['symbol'];h=r['hour']
        if np.isfinite(panel[s].mark[h,0]):raise ValueError('attempt to overwrite original row')
        if s not in updated:updated[s]=panel[s].mark.copy()
        updated[s][h]=[row[k] for k in ['open','high','low','close','volume','quote_volume','taker_quote']]
        count+=1
    if count!=audit['recovered_mark_rows']:raise ValueError('recovery count mismatch')
    result=panel.copy()
    for s,mark in updated.items():
        mark.flags.writeable=False;result[s]=replace(panel[s],mark=mark)
    return result


def stats(daily,equity):
    m=metrics(daily,daily,equity)
    m['expectancy_per_day']=m.pop('expectancy_per_episode');m['days']=m.pop('episodes')
    return m


def summarize(run,candidate,label,config,original,f1_turnover):
    m=stats(run['daily'],run['equity'])
    m.update(candidate=candidate,cost=label,folds=[dict(year=f['year'],**stats(f['daily'],f['equity']),
                    components_per_starting_equity=f['components']) for f in run['folds']],
             components_usdt={k:20*v for k,v in run['totals'].items()},economic_changes=run['changes'],
             fills=len(run['fills']),data_loss_exits=run['data_loss'],active_fraction=run['active_fraction'],
             max_drawdown=run['max_drawdown'],drawdown_locked=run['locked'],risk_rule_violations=run['rule_violations'],
             max_hourly_gross=run['max_gross'],max_hourly_abs_net=run['max_net'],
             average_holding_hours=float(np.mean(run['holding_hours'])) if run['holding_hours'] else None,
             actual_traded_notional_usdt=20*sum(f['notional'] for f in run['fills']),
             turnover_equity_units=run['turnover'],turnover_reduction_vs_f1=1-run['turnover']/f1_turnover,
             turnover_reduction_vs_f2=1-run['turnover']/original['turnover_equity_units'],
             retained_f2_return=m['net_return']/original['net_return'])
    denominator=max(1,min(run['changes'],config['f2_original_change_denominators'][label]))
    m['data_loss_gate_denominator']=denominator;m['data_loss_fraction']=run['data_loss']/denominator
    if label=='stress':
        m['mean_daily_lower_bound']=bootstrap_lower(run['daily'],config['seed'],
            config['bootstrap_draws'],config['bootstrap_block_days'],config['bootstrap_alpha'])
    c=run['totals'];net=c['price']+c['funding']-c['fees']-c['slippage']
    if not np.isclose(net,m['net_return'],atol=1e-9,rtol=1e-9):raise ValueError('cash ledger failed reconciliation')
    if not np.isclose(c['long']+c['short'],net,atol=1e-9,rtol=1e-9):raise ValueError('directional ledger failed reconciliation')
    return m


def gate(group,config):
    s=group['stress']
    checks=dict(positive_all_costs=all(m['net_return']>0 and m['expectancy_per_day']>0 for m in group.values()),
                stress_confidence=s['mean_daily_lower_bound']>0,stress_pf=(s['profit_factor'] or 0)>1,
                stable_folds=sum(f['net_return']>0 and f['expectancy_per_day']>0 for f in s['folds'])>=config['min_positive_folds'],
                drawdown=s['max_drawdown']<=config['max_drawdown'],
                active_coverage=s['active_fraction']>=config['min_active_fraction'],
                turnover_reduced=s['turnover_reduction_vs_f1']>=config['minimum_turnover_reduction_vs_f1'],
                no_risk_violations=all(m['risk_rule_violations']==0 for m in group.values()),
                data_loss=all(m['data_loss_fraction']<=config['max_data_loss_fraction'] for m in group.values()))
    return dict(candidate=s['candidate'],passed=all(checks.values()),checks=checks)


def selection_key(s):
    c=s['candidate']
    return (s['max_drawdown'],-s['cvar_5pct_daily'],
            -sum(f['net_return']>0 and f['expectancy_per_day']>0 for f in s['folds']),
            -s['retained_f2_return'],c['throttle']!='moderate',c['carry_threshold'],c['vol_target'])


def main():
    parser=argparse.ArgumentParser()
    for n in ['root','data','f2']:parser.add_argument('--'+n,type=Path,required=True)
    parser.add_argument('--seal',action='store_true');a=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    if a.seal:
        seal(a.root,a.data,a.f2,repo);print('PREREGISTRATION_SEALED');return
    sealed=verify(a.root,repo)
    if os.environ.get('PYTHONHASHSEED')!='0':raise ValueError('deterministic process seed required')
    config=json.loads((repo/'configs/v1f3_preregistration.json').read_text());grid=candidate_grid(config)
    if (a.root/'economic.started.json').exists():raise ValueError('development already consumed; no rerun')
    panel,candidates,identities=inputs(a.data);panel=recover(panel,a.root)
    cache=cache_signals(panel,candidates,'A')
    prior=json.loads(next((a.f2/'evaluation').glob('development-*.json')).read_text())
    originals={r['cost']:r for r in prior['results'] if r['variant']=='A'}
    for label,m in originals.items():
        if m['economic_changes']!=config['f2_original_change_denominators'][label]:raise ValueError('original gate denominator mismatch')
    write_once(a.root/'economic.started.json',encode_report(dict(utc=datetime.now(timezone.utc).isoformat(),
               seal_sha256=digest((a.root/'preregistration.lock.json').read_bytes()))))
    results=[];references_out=[]
    for label,fee,slip in config['costs']:
        references=[];states={};daily=[];eq=[];capital=1.;changes=losses=0
        for y in config['development_years']:
            first,last=hour(y),hour(y+1);reference=simulate(panel,cache,'A',first,last,fee,slip)
            references.append((y,first,last,reference));tape=IntentTape(reference['fills'])
            for h in range(first,last):
                q=tape.at(h)
                if h%8==1 and q:states[h]=snapshot(panel,q,h,reference['equity'][h-first])
            daily.extend(reference['daily']);eq.extend(capital*reference['equity'][1:]);capital*=reference['equity'][-1]
            changes+=reference['economic_changes'];losses+=reference['data_loss_exits']
        references_out.append(dict(cost=label,**stats(daily,np.asarray(eq)),economic_changes=changes,data_loss_exits=losses))
        print(json.dumps({'recovered_reference_ready':label}),flush=True)
        for c in grid:
            run=run_overlay(panel,references,states,c,fee,slip)
            original=originals[label]
            m=summarize(run,c,label,config,original,original['v1f1_turnover_equity_units'])
            artifact=dict(summary=m,hourly_equity=run['equity'].tolist(),daily_returns=run['daily'].tolist(),
                          fills=run['fills'],holding_hours=run['holding_hours'],risk_decisions=run['risk_rows'])
            raw=encode_report(artifact);sha=digest(raw)
            write_once(a.root/'evaluation'/f'{c["id"]}-{label}-{sha}.json',raw)
            m['artifact_sha256']=sha;results.append(m)
            print(json.dumps({'completed':c['id'],'cost':label}),flush=True)
    gates=[gate({r['cost']:r for r in results if r['candidate']['id']==c['id']},config) for c in grid]
    passed={g['candidate']['id'] for g in gates if g['passed']}
    eligible=[r for r in results if r['cost']=='stress' and r['candidate']['id'] in passed]
    selected=min(eligible,key=selection_key)['candidate'] if eligible else None
    report=dict(status='DEVELOPMENT_RISK_PASS' if selected else 'NO_GO_ALPHA',selected=selected,
                results=results,gates=gates,recovered_f2_reference=references_out,seal=sealed,
                data_identities=identities,holdouts_accessed=False,mainnet='LOCKED / NOT_APPROVED')
    raw=encode_report(report);sha=digest(raw);path=a.root/'evaluation'/('development-'+sha+'.json')
    write_once(path,raw)
    if selected:write_once(a.root/'selected.lock.json',encode_report(dict(candidate=selected,development_sha256=sha)))
    print(json.dumps({'report':str(path),'sha256':sha,'status':report['status']}),flush=True)


if __name__=='__main__':main()
