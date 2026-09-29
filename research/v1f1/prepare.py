"""Build strictly trailing liquidity candidates; no forward returns or PnL."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from research.v1f1.dataset import HOUR,digest,write_once

START=int(datetime(2019,9,1,tzinfo=timezone.utc).timestamp()*1000)
END=int(datetime(2025,1,1,tzinfo=timezone.utc).timestamp()*1000)
N=(END-START)//HOUR

def trailing_sum(values,window):
    ok=np.isfinite(values)
    sums=np.r_[0.,np.cumsum(np.where(ok,values,0.))]
    counts=np.r_[0,np.cumsum(ok)]
    result=np.full(len(values)+1,np.nan)
    result[window:]=np.where(counts[window:]-counts[:-window]==window,
                             sums[window:]-sums[:-window],np.nan)
    return result

def load_records(root,stage):
    paths=sorted((root/'manifests').glob(stage+'-*.json'))
    if len(paths)!=1:raise ValueError('select exactly one immutable '+stage+' manifest')
    raw=paths[0].read_bytes()
    if digest(raw)!=paths[0].stem.split('-',1)[1]:raise ValueError('manifest hash')
    return json.loads(raw)['records'],digest(raw)

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    root=p.parse_args().root
    records,identity=load_records(root,'perp')
    groups=defaultdict(list)
    for r in records:
        if r['ok']:groups[r['key'].split('/')[5]].append(r)
    symbols=sorted(groups)
    decisions=np.arange((int(datetime(2020,1,1,tzinfo=timezone.utc).timestamp()*1000)-START)//HOUR,N,8)
    liquid=np.full((len(decisions),len(symbols)),np.nan)
    for j,symbol in enumerate(symbols):
        q=np.full(N,np.nan)
        for r in groups[symbol]:
            raw=Path(r['parquet']).read_bytes()
            if digest(raw)!=r['parquet_sha256']:raise ValueError('parquet hash')
            table=pq.read_table(pa.BufferReader(raw),columns=['timestamp','quote_volume'])
            ix=(table['timestamp'].to_numpy()-START)//HOUR
            vals=table['quote_volume'].to_numpy()
            keep=(ix>=0)&(ix<N)
            if np.any(np.isfinite(q[ix[keep]])):raise ValueError('overlapping source partitions')
            q[ix[keep]]=vals[keep]
        liquid[:,j]=trailing_sum(q,720)[decisions]/30
    output=[];requests=set();eligible_counts=[]
    for row,t in enumerate(decisions):
        valid=np.flatnonzero(np.isfinite(liquid[row])&(liquid[row]>=10_000_000))
        order=sorted(valid,key=lambda j:(-liquid[row,j],symbols[j]))[:30]
        eligible_counts.append(len(order))
        for rank,j in enumerate(order,1):
            timestamp=START+int(t)*HOUR
            output.append({'timestamp':timestamp,'symbol':symbols[j],'liquidity_rank':rank,'trailing_daily_quote':float(liquid[row,j])})
            dt=datetime.fromtimestamp(timestamp/1000,timezone.utc)
            month_index=dt.year*12+dt.month-1
            for shift in (-1,0,1):
                year,month=divmod(month_index+shift,12)
                period=f'{year:04}-{month+1:02}'
                if '2019-09'<=period<='2024-12':requests.add((symbols[j],period))
    sink=pa.BufferOutputStream();pq.write_table(pa.Table.from_pylist(output),sink,compression='zstd')
    data=sink.getvalue().to_pybytes();write_once(root/'candidates.parquet',data)
    write_once(root/'context_requests.json',json.dumps(sorted(requests)).encode())
    report={'perp_manifest_sha256':identity,'candidate_sha256':digest(data),'historical_symbols':len(symbols),
            'decision_count':len(decisions),'at_least_15_candidates':sum(x>=15 for x in eligible_counts),
            'context_symbol_months':len(requests),'context_symbols':len({x[0] for x in requests}),
            'rule':'top30 trailing 720 complete hours quote-volume, >=10M USDT/day, no current symbol list'}
    write_once(root/'coverage.json',json.dumps(report,sort_keys=True).encode());print(json.dumps(report))

if __name__=='__main__':main()
