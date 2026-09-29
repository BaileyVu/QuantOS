"""One-shot, preregistered tournament using canonical QuantOS accounting."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from quantos.infrastructure.models.v1t5 import feature_matrix, fit_fold, triple_barrier_labels, purged_indices
from quantos.infrastructure.storage.v1t5_data import CanonicalSegment,aggregate_segment
from quantos.application.v1t5_evaluation import evaluate
from quantos.domain.alpha.v1t5 import A,B,T5Prediction
from quantos.domain.market_data import Candle,DatasetIdentity

UTC=timezone.utc
CONFIG=Path('configs/v1t5_preregistration.json')
PROTOCOL=Path('docs/V1_T5_ALPHA_PREREGISTRATION.md')
def dt(x):return datetime.fromisoformat(x)
def digest(p):return sha256(Path(p).read_bytes()).hexdigest()
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':'),default=str,allow_nan=False)
def save(path,obj):
    with Path(path).open('x',encoding='utf8') as f:f.write(json.dumps(obj,indent=2,default=str,allow_nan=False))

def candles_in(segment,start,end):
    """Exact Decimal source records, not binary-float reconstructed candles."""
    epoch=datetime(1970,1,1,tzinfo=UTC)
    previous=None
    for part in segment['partitions']:
        if dt(part['end'])<start or dt(part['start'])>=end:continue
        if digest(part['path'])!=part['file_sha256']:raise ValueError('Canonical content changed')
        f=pq.ParquetFile(part['path'],page_checksum_verification=True)
        for batch in f.iter_batches(batch_size=32768,use_threads=False):
            d={n:batch.column(n).to_pylist() for n in batch.schema.names if n not in ('open_time','close_time')}
            for n in ('open_time','close_time'):
                d[n]=[epoch+timedelta(microseconds=x) for x in batch.column(n).cast(pa.int64()).to_pylist()]
            for i,t in enumerate(d['open_time']):
                if not start<=t<end:continue
                if previous is not None and t-previous!=timedelta(minutes=1):raise ValueError('Fold gap')
                previous=t
                yield Candle(**{n:d[n][i] for n in d})

def verify_seal(root):
    seal=json.loads((root/'preregistration_seal.json').read_text())
    if digest(CONFIG)!=seal['config'] or digest(PROTOCOL)!=seal['protocol']:raise ValueError('Protocol drift')
    if digest(root/'dataset_manifest.json')!=seal['dataset_manifest']:raise ValueError('Dataset drift')
    for name,h in seal['sources'].items():
        if digest(name)!=h:raise ValueError('Evaluated source drift: '+name)
    return seal

def guard_window(start,end,stage,*,earned=False):
    """A protected period requires an explicit preceding evidence gate."""
    if start.tzinfo is None or end.tzinfo is None or end<=start:
        raise ValueError('Invalid UTC evaluation interval')
    if stage=='development':
        if end>dt('2025-01-01T00:00:00+00:00'):raise ValueError('Prefinal/holdout sealed')
    elif stage=='prefinal':
        if not earned or start!=dt('2025-01-01T00:00:00+00:00') or end!=dt('2026-01-01T00:00:00+00:00'):
            raise ValueError('2025 access not earned')
    elif stage=='holdout':
        if not earned or start!=dt('2026-01-01T00:00:00+00:00') or end!=dt('2026-09-28T00:00:00+00:00'):
            raise ValueError('2026 access not earned')
    else:raise ValueError('Unknown evaluation role')

def prepare_model(segment,fold,family,root):
    config=json.loads(CONFIG.read_text());cadence=config[family]['minutes']
    raw=np.load(segment['cache'],allow_pickle=False,mmap_mode='r')
    if digest(segment['cache'])!=segment['cache_sha256']:raise ValueError('Cache content drift')
    # Never expose prefinal/holdout labels to fitting. The array is truncated at test start.
    limit=dt(fold['test_start'])
    identity=DatasetIdentity('BTCUSDT','1m',dt(segment['start']),dt(segment['end']),
                             'validated-partition-chain','candle-v1',segment['id'])
    allbars=aggregate_segment(CanonicalSegment(segment['id'],segment['cache_sha256'],identity,raw),
                             cadence,end_exclusive=dt(fold['test_end'])).bars
    times=allbars[:,0]+cadence*60
    # Include the label/feature bar ending exactly at split boundary only in the earlier split.
    train=(times>dt(fold['train_start']).timestamp())&(times<=dt(fold['validation_start']).timestamp())
    val=(times>dt(fold['validation_start']).timestamp())&(times<=limit.timestamp())
    test=(times>limit.timestamp())&(times<dt(fold['test_end']).timestamp())
    fit_end=np.flatnonzero(times<=limit.timestamp())[-1]+1
    features=feature_matrix(allbars)
    model=fit_fold(allbars[:fit_end],train[:fit_end],val[:fit_end],family,
                   features=(features[0][:fit_end],features[1]))
    means=None
    if family=='A':
        labels,ends=triple_barrier_labels(allbars[:fit_end],model.config['horizon'],model.config['barrier'])
        ix=purged_indices(train[:fit_end],ends,features[0][:fit_end],labels)
        horizon=model.config['horizon']
        outcomes=allbars[ix+horizon,4]/allbars[ix,4]-1
        means=[float(outcomes[labels[ix]==k].mean()) if np.any(labels[ix]==k) else 0.0 for k in range(3)]
    metadata=dict(model.metadata,configuration=model.config,validation_loss=model.validation_loss,
                  class_means=means,fold=fold,family=family)
    version=sha256(canonical(metadata).encode()).hexdigest()
    out=root/'models'/version;out.mkdir(parents=True,exist_ok=False)
    if family=='B':
        (out/'model.ubj').write_bytes(bytes(model.model.save_raw(raw_format='ubj')))
    else:
        np.savez(out/'weights.npz',**{k:v.detach().numpy() for k,v in model.model.state_dict().items()})
    metadata['model_file_sha256']={p.name:digest(p) for p in out.iterdir()}
    save(out/'metadata.json',metadata)
    forecast=model.predict(features[0][test]);predictions={}
    for stamp,result in zip(times[test],forecast):
        timestamp=datetime.fromtimestamp(stamp,UTC)
        kw=dict(timestamp=timestamp,training_end=limit,model_version=version,feature_version='v1t5-finite39-v1')
        if family=='A':
            probs=[Decimal(str(float(v))) for v in result];total=sum(probs)
            probs=tuple(v/total for v in probs)
            prediction=T5Prediction(**kw,probabilities=probs,training_class_means=tuple(Decimal(str(v)) for v in means))
        else:prediction=T5Prediction(**kw,forecast=Decimal(str(float(result))))
        predictions[timestamp]=prediction
    save(out/'predictions.json',[asdict(p) for p in predictions.values()])
    return predictions,metadata

def summarize(evidence):
    metrics=evidence.metrics
    return dict(metrics=asdict(metrics),daily_returns=[float(r) for r in evidence.daily_returns],
        daily_equities=[(str(day),str(value)) for day,value in evidence.daily_equities],
        trades=[asdict(t) for t in evidence.completed_trades],fill_count=len(evidence.fills),
        rejections=evidence.rejection_counts,final_account=asdict_safe_account(evidence.final_account),
        fills=[asdict(f) for f in evidence.fills])

def asdict_safe_account(a):
    return dict(timestamp=a.timestamp,balances=dict(a.balances),positions=[asdict(p) for p in a.positions])

def aggregate(results,cost):
    runs=[r['costs'][str(cost)] for r in results]
    pnl=[float(r['metrics']['net_profit']) for r in runs]
    trades=[float(t['net_pnl']) for r in runs for t in r['trades']]
    returns=np.array([v for r in runs for v in r['daily_returns']])
    winners=sum(v for v in trades if v>0);losses=-sum(v for v in trades if v<0)
    positive=sum(v for v in pnl if v>0)
    events=sum(r['fill_count'] for r in runs)
    std=float(returns.std()) if len(returns) else 0
    downside=float(np.sqrt(np.mean(np.minimum(returns,0)**2))) if len(returns) else 0
    return dict(folds=len(runs),net_profit=sum(pnl),aggregate_return=sum(pnl)/(20*len(runs)) if runs else 0,
        linked_return=float(np.prod([1+p/20 for p in pnl])-1),
        profit_factor=winners/losses if losses else None,
        sharpe=float(returns.mean()/std*np.sqrt(365)) if std else None,
        sortino=float(returns.mean()/downside*np.sqrt(365)) if downside else None,
        expectancy=float(np.mean(trades)) if trades else None,
        change_expectancy=sum(pnl)/events if events else None,
        win_rate=sum(v>0 for v in trades)/len(trades) if trades else None,
        completed_trades=len(trades),events=events,events_per_day=events/len(returns) if len(returns) else 0,
        profitable_fold_fraction=sum(v>0 for v in pnl)/len(pnl) if pnl else 0,
        max_positive_fold_share=max([v for v in pnl if v>0],default=0)/positive if positive else None,
        max_positive_trade_share=max([v for v in trades if v>0],default=0)/winners if winners else None,
        max_drawdown=max([float(r['metrics']['maximum_drawdown']) for r in runs],default=0),
        fees=sum(float(r['metrics']['fees']) for r in runs),slippage=sum(float(r['metrics']['slippage']) for r in runs),
        exposure=float(np.mean([float(r['metrics']['exposure']) for r in runs])) if runs else 0)

def economic_gates(base,stress):
    def gt(x,n):return x is not None and x>n
    def le(x,n):return x is not None and x<=n
    return dict(baseline_positive=gt(base['aggregate_return'],0),stress_positive=gt(stress['aggregate_return'],0),
        profit_factor=gt(base['profit_factor'],1.1),sharpe=gt(base['sharpe'],.5),stress_sharpe=gt(stress['sharpe'],0),
        expectancy=gt(base['expectancy'],0) and gt(base['change_expectancy'],0),
        stress_expectancy=gt(stress['expectancy'],0) and gt(stress['change_expectancy'],0),
        fold_stability=gt(base['profitable_fold_fraction'],.5),fold_concentration=le(base['max_positive_fold_share'],.4),
        trade_concentration=le(base['max_positive_trade_share'],.2),drawdown=le(base['max_drawdown'],.2) and le(stress['max_drawdown'],.2))

def stationary_bootstrap(results,cost,replications=10000):
    rng=np.random.default_rng(1729)
    equity=np.ones(replications);peak=equity.copy();mdd=np.zeros(replications)
    totals=np.zeros(replications);longest=np.zeros(replications,dtype=int);streak=longest.copy();count=0
    for fold in results:
        values=np.asarray(fold['costs'][str(cost)]['daily_returns']);n=len(values)
        if not n:continue
        cursor=rng.integers(n,size=replications)
        for i in range(n):
            if i:cursor=np.where(rng.random(replications)<.1,rng.integers(n,size=replications),(cursor+1)%n)
            r=values[cursor];totals+=r;equity*=1+r;peak=np.maximum(peak,equity);mdd=np.maximum(mdd,1-equity/peak)
            streak=np.where(r<0,streak+1,0);longest=np.maximum(longest,streak);count+=1
    q=lambda v:np.quantile(v,[.01,.05,.5,.95,.99]).tolist()
    terminal=equity-1;tail=terminal[terminal<=np.quantile(terminal,.05)]
    return dict(mean_return_quantiles=q(totals/max(1,count)),terminal_equity_quantiles=q(20*equity),
        maximum_drawdown_quantiles=q(mdd),longest_daily_loss_streak_quantiles=q(longest),
        terminal_expected_shortfall_5pct=float(tail.mean()),
        severe_impairment_probability={str(t):float(np.mean(mdd>=t)) for t in (.2,.35,.5,.7)},
        replications=replications,mean_block_days=10,seed=1729)

def run_family(root,family):
    root=Path(root);verify_seal(root)
    manifest=json.loads((root/'dataset_manifest.json').read_text());segments={s['id']:s for s in manifest['segments']}
    config=json.loads(CONFIG.read_text());out=root/'development'/family;out.mkdir(parents=True,exist_ok=True)
    if list(out.glob('fold-*.json')):raise ValueError('Development outcomes already exist; no reevaluation')
    results=[]
    for i,fold in enumerate(manifest['folds'][family]):
        guard_window(dt(fold['test_start']),dt(fold['test_end']),'development')
        print(f'{family} fitting fold {i+1}/{len(manifest["folds"][family])} {fold["test_start"]}',flush=True)
        segment=segments[fold['segment']]
        predictions,metadata=prepare_model(segment,fold,family,root)
        candles=tuple(candles_in(segment,dt(fold['test_start']),dt(fold['test_end'])))
        result=dict(fold=fold,model=metadata,costs={})
        for cost in config['cost_bps']:
            report=evaluate(candles,predictions,family=A if family=='A' else B,run_id=f'{family}-{i}-{cost}',cost_bps=cost)
            result['costs'][str(cost)]=summarize(report)
        save(out/f'fold-{i:03}.json',result);results.append(result)
        print(f'{family} fold {i+1} sealed',flush=True)
    summary=dict(family=family,costs={str(c):aggregate(results,c) for c in config['cost_bps']})
    gates=economic_gates(summary['costs']['15'],summary['costs']['30']);summary['gates']=gates
    if all(gates.values()):
        summary['bootstrap']={str(c):stationary_bootstrap(results,c) for c in (15,30)}
        percentile=0 if summary['costs']['15']['events']<100 else 1
        gates['bootstrap']=all(v['mean_return_quantiles'][percentile]>0 for v in summary['bootstrap'].values())
    summary['development_pass']=all(gates.values());save(out/'summary.json',summary)
    return summary

def main(root):
    root=Path(root);verify_seal(root)
    with ProcessPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run_family,[root,root],['A','B']))
    save(root/'development_summary.json',results)
    if not any(r['development_pass'] for r in results):
        save(root/'verdict.json',dict(verdict='NO_GO_ALPHA',holdout_opened=False,prefinal_opened=False,
             mainnet='LOCKED / NOT_APPROVED',reason='Both candidates failed frozen development gates'))
    else:
        print('Development qualified; proceed with frozen prefinal and bootstrap artifacts.',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();main(a.root)
