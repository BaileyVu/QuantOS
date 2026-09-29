"""One-shot preregistration-bound development evaluation; holdouts inaccessible."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from research.v1f1.dataset import digest,write_once,HOUR
from research.v1f1.prepare import START,END,N,load_records
from research.v1f1.factor import Series,select,simulate

def hour(year):return (int(datetime(year,1,1,tzinfo=timezone.utc).timestamp()*1000)-START)//HOUR

def load_panel(root):
    requests=json.loads((root/'context_requests.json').read_text())
    symbols=sorted({s for s,m in requests})
    groups=defaultdict(list)
    identities={}
    for stage in ('perp','context'):
        records,identity=load_records(root,stage);identities[stage]=identity
        for r in records:
            if r['ok'] and r['key'].split('/')[5] in symbols:
                groups[(r['key'].split('/')[5],r['family'])].append(r)
    panel={}
    for symbol in symbols:
        arrays={};fund=[]
        for family in ['klines','indexPriceKlines','markPriceKlines','fundingRate']:
            a=np.full((N,7),np.nan) if family!='fundingRate' else None
            for record in groups[(symbol,family)]:
                raw=Path(record['parquet']).read_bytes()
                if digest(raw)!=record['parquet_sha256']:raise ValueError('source hash changed')
                table=pq.read_table(pa.BufferReader(raw))
                timestamps=table['timestamp'].to_numpy()
                if family=='fundingRate':
                    fund.extend(zip(timestamps,table['interval_hours'].to_numpy(),table['rate'].to_numpy()))
                else:
                    ix=(timestamps-START)//HOUR;keep=(ix>=0)&(ix<N)
                    if np.any(np.isfinite(a[ix[keep],0])):raise ValueError('overlapping partitions')
                    a[ix[keep]]=np.column_stack([table[name].to_numpy()[keep] for name in ['open','high','low','close','volume','quote_volume','taker_quote']])
            if a is not None:a.flags.writeable=False;arrays[family]=a
        funding=np.asarray(sorted(fund),dtype=float).reshape(-1,3)
        if len(funding) and np.any(np.diff(funding[:,0])<=0):raise ValueError('duplicate funding')
        funding.flags.writeable=False
        panel[symbol]=Series(symbol,START,arrays['klines'],arrays['indexPriceKlines'],arrays['markPriceKlines'],funding)
    return panel,identities

def verify_seal(root,repo):
    seal=json.loads((root/'preregistration.lock.json').read_text())
    for path,expected in seal['files'].items():
        if digest((repo/path).read_bytes())!=expected:raise ValueError('preregistration/source changed: '+path)
    for filename,expected in seal['data_files'].items():
        if digest((root/filename).read_bytes())!=expected:raise ValueError('data identity changed: '+filename)
    return seal

def authorize_development(root,config):
    if config['development_test_years']!=[2021,2022,2023,2024]:raise ValueError('holdout access denied')
    if (root/'development.started.json').exists():raise ValueError('development periods already consumed; no automatic rerun')

def bootstrap_lower(daily,seed,draws=10000,block=7,alpha=.00625):
    daily=np.asarray(daily)
    if not len(daily):return None
    rng=np.random.default_rng(seed);means=[]
    for i in range(0,draws,100):
        starts=rng.integers(0,len(daily),size=(min(100,draws-i),int(np.ceil(len(daily)/block))))
        indices=(starts[:,:,None]+np.arange(block))%len(daily)
        samples=daily[indices.reshape(len(starts),-1)[:,:len(daily)]]
        means.extend(samples.mean(axis=1))
    return float(np.quantile(means,alpha))

def metrics(daily,episode_net,equity):
    daily=np.asarray(daily);episode_net=np.asarray(episode_net)
    gains=episode_net[episode_net>0].sum();loss=-episode_net[episode_net<0].sum()
    sd=float(np.std(daily,ddof=1)) if len(daily)>1 else 0
    downside=float(np.sqrt(np.mean(np.minimum(daily,0)**2))) if len(daily) else 0
    peaks=np.maximum.accumulate(np.r_[1.,equity])
    drawdown=1-np.r_[1.,equity]/peaks
    return {'net_return':float(equity[-1]-1),'terminal_equity_20_usdt':float(20*equity[-1]),
            'expectancy_per_episode':float(episode_net.mean()) if len(episode_net) else None,
            'mean_daily_return':float(daily.mean()) if len(daily) else None,
            'profit_factor':float(gains/loss) if loss else None,
            'sharpe':float(daily.mean()/sd*np.sqrt(365)) if sd else None,
            'sortino':float(daily.mean()/downside*np.sqrt(365)) if downside else None,
            'max_drawdown':float(drawdown.max()),'worst_day':float(daily.min()),
            'cvar_5pct_daily':float(np.sort(daily)[:max(1,int(np.ceil(.05*len(daily))))].mean()),
            'episodes':len(episode_net)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    root=p.parse_args().root;repo=Path(__file__).resolve().parents[2]
    seal=verify_seal(root,repo)
    config=json.loads((repo/'configs/v1f1_preregistration.json').read_text())
    authorize_development(root,config)
    marker=root/'development.started.json'
    panel,identities=load_panel(root)
    table=pq.read_table(root/'candidates.parquet').to_pylist();candidates=defaultdict(list)
    for r in table:candidates[(r['timestamp']-START)//HOUR].append(r['symbol'])
    marker.parent.mkdir(parents=True,exist_ok=True)
    with marker.open('x') as f:json.dump({'seal':digest((root/'preregistration.lock.json').read_bytes()),'source_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()},f)
    results=[];episode_rows=[];daily_rows=[];config_number=0
    for variant in config['variants']:
      for horizon in config['horizons_hours']:
       for legs in config['legs_per_side']:
        name=f'{variant}-{horizon}h-{legs}x{legs}';config_number+=1
        selections={}
        for year in config['development_test_years']:
            for t in range(hour(year),hour(year+1)-horizon-1,horizon):
                selections[t]=select(candidates.get(t,[]),panel,t,variant,legs)
        for fee in config['fees_bps']:
         for slip in config['slippage_bps']:
          folds=[];all_daily=[];all_btc=[];all_net=[];all_equity=[];fold_scale=1.;totals=defaultdict(float);symbol_pnl=defaultdict(float);regime_pnl=defaultdict(float)
          max_gross=0.;max_net=0.
          counts=defaultdict(int)
          for year in config['development_test_years']:
            first,last=hour(year),hour(year+1);equity=np.ones(last-first+1);capital=1.;cursor=0
            nets=[];fold_totals=defaultdict(float);fold_counts=defaultdict(int)
            for t in range(first,last-horizon-1,horizon):
                portfolio,n=selections[t];fold_counts['scheduled_decisions']+=1
                if not portfolio:fold_counts['no_trade_decisions']+=1;continue
                result=simulate(panel,portfolio,t,horizon,fee,slip)
                max_gross=max(max_gross,result['max_gross_exposure']);max_net=max(max_net,result['max_abs_net_exposure'])
                entry=t+1-first;end=entry+horizon
                equity[cursor:entry+1]=capital
                equity[entry:end+1]=capital*(1+result['curve'])
                starting=capital;capital*=1+result['net'];cursor=end
                if not result['entry_failed']:nets.append(result['net'])
                for key in ('price','funding','fees','slippage','long','short'):
                    fold_totals[key+'_usdt']+=20*starting*result[key]
                fold_totals['turnover_starting_equity_units']+=result['turnover']
                fold_counts['data_loss_exits']+=int(result['data_loss']);fold_counts['failed_entries']+=int(result['entry_failed'])
                btc=panel.get('BTCUSDT')
                regime='unknown'
                if btc is not None and np.isfinite(btc.perp[t-720,3]) and np.isfinite(btc.perp[t-1,3]):
                    regime='btc_30d_up' if btc.perp[t-1,3]>=btc.perp[t-720,3] else 'btc_30d_down'
                regime_pnl[regime]+=20*fold_scale*starting*result['net']
                for leg in result['legs']:symbol_pnl[leg['symbol']]+=20*fold_scale*starting*leg['net']
                episode_rows.append({'configuration':name,'fee_bps':fee,'slippage_bps':slip,'year':year,
                                     'decision_timestamp':START+t*HOUR,'eligible_symbols':n,
                                     **{key:result[key] for key in ('net','price','funding','fees','slippage','turnover','long','short','data_loss','entry_failed')},
                                     'symbols':','.join(s for s,w in portfolio)})
                if capital<=0:fold_counts['insolvency']+=1;break
            equity[cursor:]=capital
            daily=equity[24::24]/equity[:-24:24]-1
            btc=panel.get('BTCUSDT')
            btc_prices=btc.perp[np.arange(first-1,last,24),3] if btc is not None else np.full(len(daily)+1,np.nan)
            all_btc.extend(btc_prices[1:]/btc_prices[:-1]-1)
            if not np.all(np.isfinite(daily)):raise ValueError('nonfinite account returns')
            stats=metrics(daily,nets,equity);stats.update({'year':year,**dict(fold_totals),**dict(fold_counts)})
            folds.append(stats);all_daily.extend(daily);all_net.extend(nets)
            all_equity.extend(equity[1:]*fold_scale)
            for key,value in fold_totals.items():totals[key]+=value*(fold_scale if key.endswith('_usdt') else 1)
            for key,value in fold_counts.items():counts[key]+=value
            for i,r in enumerate(daily):daily_rows.append({'configuration':name,'fee_bps':fee,'slippage_bps':slip,'day_timestamp':START+(first+24*(i+1))*HOUR,'return':float(r)})
            fold_scale*=capital
          stats=metrics(all_daily,all_net,np.asarray(all_equity));stats.update(dict(totals));stats.update(dict(counts))
          stats.update({'configuration':name,'fee_bps':fee,'slippage_bps':slip,'folds':folds,
                        'per_symbol_net_usdt':dict(sorted(symbol_pnl.items())), 'regime_net_usdt':dict(regime_pnl),
                        'max_hourly_gross_exposure':max_gross,'max_hourly_abs_net_exposure':max_net})
          mask=np.isfinite(all_btc)&np.isfinite(all_daily)
          b=np.asarray(all_btc)[mask];d=np.asarray(all_daily)[mask]
          stats['directional_beta_to_btc']=float(np.cov(d,b,ddof=1)[0,1]/np.var(b,ddof=1)) if len(b)>1 and np.var(b)>0 else None
          absolute=sum(abs(x) for x in symbol_pnl.values())
          stats['largest_symbol_absolute_pnl_share']=max((abs(x)/absolute for x in symbol_pnl.values()),default=None) if absolute else None
          if fee==7 and slip==10:
              stats['block_bootstrap_mean_daily_lower_99_375pct']=bootstrap_lower(all_daily,config['seed']+config_number)
          results.append(stats)
        print(json.dumps({'completed_configuration':name}),flush=True)
    gates=[]
    for name in sorted({r['configuration'] for r in results}):
        base=next(r for r in results if r['configuration']==name and r['fee_bps']==5 and r['slippage_bps']==2)
        stress=next(r for r in results if r['configuration']==name and r['fee_bps']==7 and r['slippage_bps']==10)
        severe=next(r for r in results if r['configuration']==name and r['fee_bps']==10 and r['slippage_bps']==10)
        checks={'baseline_positive':base['mean_daily_return']>0,'stress_positive':stress['mean_daily_return']>0,
                'severe_positive':severe['mean_daily_return']>0,
                'stress_confidence':stress['block_bootstrap_mean_daily_lower_99_375pct']>0,
                'fold_stability':sum(f['mean_daily_return']>0 for f in stress['folds'])>=3,
                'drawdown':stress['max_drawdown']<=.35,'episodes':stress['episodes']>=300,
                'data_loss_rate':stress.get('data_loss_exits',0)/max(1,stress['episodes'])<=.01,
                'coverage':stress['episodes']/stress['scheduled_decisions']>=.8,
                'concentration':(stress['largest_symbol_absolute_pnl_share'] or 1)<=.35}
        gates.append({'configuration':name,'pass':all(checks.values()),'checks':checks})
    report={'schema':'v1f1-economic-development-v1','layer':'economic factor simulation',
            'data_identities':identities,'seal':seal,'results':results,'gates':gates,
            'alpha_qualified':any(g['pass'] for g in gates),'holdout_accessed':False,
            'status':'DEVELOPMENT_ALPHA_PASS' if any(g['pass'] for g in gates) else 'NO_GO_ALPHA',
            'mainnet':'LOCKED / NOT_APPROVED'}
    for name,rows in [('episodes',episode_rows),('daily',daily_rows)]:
        sink=pa.BufferOutputStream();pq.write_table(pa.Table.from_pylist(rows),sink,compression='zstd')
        raw=sink.getvalue().to_pybytes();write_once(root/'evaluation'/(name+'-'+digest(raw)+'.parquet'),raw)
        report[name+'_sha256']=digest(raw)
    raw=(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()
    output=root/'evaluation'/('development-'+digest(raw)+'.json');write_once(output,raw)
    print(json.dumps({'report':str(output),'sha256':digest(raw),'status':report['status']}),flush=True)

if __name__=='__main__':main()
