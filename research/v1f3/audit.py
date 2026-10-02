"""Independent artifact/ledger reconciliation; never reruns an economic period."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import numpy as np
from research.v1f1.dataset import digest, write_once
from research.v1f1.evaluate import encode_report
from research.v1f3.evaluate import verify, gate


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    repo=Path(__file__).resolve().parents[2];verify(a.root,repo)
    reports=list((a.root/'evaluation').glob('development-*.json'))
    if len(reports)!=1:raise ValueError('expected exactly one development result')
    raw=reports[0].read_bytes();sha=digest(raw)
    if reports[0].stem!='development-'+sha:raise ValueError('report hash mismatch')
    report=json.loads(raw);config=json.loads((repo/'configs/v1f3_preregistration.json').read_text())
    if len(report['results'])!=36:raise ValueError('unexpected search size')
    audited=[]
    for m in report['results']:
        path=a.root/'evaluation'/f'{m["candidate"]["id"]}-{m["cost"]}-{m["artifact_sha256"]}.json'
        raw=path.read_bytes()
        if digest(raw)!=m['artifact_sha256']:raise ValueError('artifact hash mismatch')
        artifact=json.loads(raw);fills=artifact['fills'];q=defaultdict(float)
        price=fees=slip=0.
        for f in fills:
            q[f['symbol']]+=f['delta']
            price-=np.sign(f['delta'])*f['notional'];fees+=f['fees'];slip+=f['slippage']
        if any(abs(x)>1e-9 for x in q.values()):raise ValueError('unclosed terminal position')
        c=m['components_usdt']
        for k,v in [('price',price),('fees',fees),('slippage',slip)]:
            if not np.isclose(20*v,c[k],atol=1e-8,rtol=1e-9):raise ValueError('independent ledger mismatch: '+k)
        if m['economic_changes']!=len({f['hour'] for f in fills}):raise ValueError('timestamp count mismatch')
        if m['data_loss_exits']!=len({f['hour'] for f in fills if f['reason']=='data_loss'}):raise ValueError('loss count mismatch')
        net=price+c['funding']/20-fees-slip
        if not np.isclose(net,m['net_return'],atol=1e-8,rtol=1e-9):raise ValueError('independent terminal equity mismatch')
        if len(artifact['daily_returns'])!=1461 or len(artifact['hourly_equity'])!=1461*24:
            raise ValueError('period coverage mismatch')
        audited.append(dict(candidate=m['candidate']['id'],cost=m['cost'],passed=True))
    for g in report['gates']:
        actual=gate({m['cost']:m for m in report['results'] if m['candidate']==g['candidate']},config)
        if actual!=g:raise ValueError('gate mismatch')
    result=dict(development_sha256=sha,artifact_count=len(audited),all_passed=True,checks=audited,
                checks_performed=['source/data seal','artifact hash','closed terminal quantities',
                    'independent price cashflows','fill fee/slippage totals','timestamp counts',
                    'net terminal equity','period length','qualification gates'])
    write_once(a.root/'artifact-audit.json',encode_report(result))
    print(json.dumps({'audit':'PASS','artifacts':len(audited),'development_sha256':sha}))


if __name__=='__main__':main()
