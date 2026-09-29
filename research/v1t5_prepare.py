"""Integrity-only manifest and numeric caches; never computes strategy outcomes."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
from quantos.infrastructure.storage.v1t5_data import load_canonical_segment

UTC = timezone.utc
def dt(x): return datetime.fromisoformat(x)
def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def add_months(d, n):
    m = d.year*12+d.month-1+n
    return datetime(m//12, m%12+1, 1, tzinfo=UTC)
def first_month(d):
    first = d.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    return first if first == d else add_months(first,1)

def prepare(root, canonical):
    root = Path(root)
    output = root/'dataset_manifest.json'
    if output.exists():
        raise ValueError('Manifest already frozen; do not replace')
    parts = []
    for path in sorted((root/'data_integrity').glob('archive-*.json')):
        report = json.loads(path.read_text())
        if report['status'] != 'validated': continue
        for p in report['partitions']:
            # Existing long canonical source owns the post-gap interval.
            if dt(p['start']) < dt('2023-03-24T14:00:00+00:00') or dt(p['start']) >= dt('2026-09-28T00:00:00+00:00'):
                parts.append(p)
    choices = []
    for p in Path(canonical).rglob('*.parquet'):
        m = pq.read_metadata(p).metadata
        choices.append((m[b'quantos.start_time'].decode(), p, m))
    _, p, m = min(choices)
    parts.append(dict(path=str(p),dataset_id=m[b'quantos.dataset_id'].decode(),
                      start=m[b'quantos.start_time'].decode(),end=m[b'quantos.end_time'].decode()))
    parts.sort(key=lambda p:p['start'])
    segments, group, quarantined = [], [], []
    previous = None
    arrays = []
    cache = root/'cache'; cache.mkdir(exist_ok=True)
    def publish():
        if not group: return
        source_id = hashlib.sha256(json.dumps(group,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path = cache/(source_id+'.npy')
        values = np.concatenate(arrays)
        if len(values)>1 and not np.all(np.diff(values[:,0]) == 60):
            raise ValueError('Partition-chain continuity violated')
        if path.exists():
            if not np.array_equal(np.load(path,allow_pickle=False),values):
                raise ValueError('Existing cache differs from canonical data')
        else:
            with path.open('xb') as target: np.save(target,values,allow_pickle=False)
        segments.append(dict(id=source_id,start=group[0]['start'],end=group[-1]['end'],
                             rows=len(values),cache=str(path),cache_sha256=digest(path),partitions=list(group)))
        print('validated segment',segments[-1]['start'],segments[-1]['end'],len(values),flush=True)
    for index, p in enumerate(parts):
        start,end = dt(p['start']),dt(p['end'])
        if previous is not None:
            if start <= previous: raise ValueError('Overlapping canonical partitions')
            if start != previous+timedelta(minutes=1):
                publish(); group=[]; arrays=[]
        try:
            loaded = load_canonical_segment(Path(p['path']))
        except ValueError as error:
            if str(error) != 'unexpected canonical close-time convention': raise
            quarantined.append(dict(partition=p,reason=str(error)))
            print('quarantined incomplete-candle partition',p['start'],p['end'],flush=True)
            publish(); group=[]; arrays=[]; previous=None
            continue
        if loaded.dataset_id != p['dataset_id']: raise ValueError('Identity mismatch')
        p['file_sha256']=loaded.file_sha256
        p['rows']=len(loaded.minutes)
        group.append(p); arrays.append(loaded.minutes)
        previous=end
        if index%200==0: print('validated partitions',index,flush=True)
    publish()
    config=json.loads(Path('configs/v1t5_preregistration.json').read_text())
    folds={f:[] for f in ('A','B')}
    for segment in segments:
        start=first_month(dt(segment['start']))
        end=min(dt(segment['end'])+timedelta(minutes=1),dt(config['development_end_exclusive']))
        for family in ('A','B'):
            c=config[family]
            minimum=c.get('minimum_train_months',c['train_months'])
            test=add_months(start,minimum+c['validation_months'])
            if family=='B':
                while test.month not in (1,4,7,10): test=add_months(test,1)
            while add_months(test,c['test_months'])<=end:
                val=add_months(test,-c['validation_months'])
                train=max(start,add_months(val,-c['train_months']))
                folds[family].append(dict(segment=segment['id'],train_start=train.isoformat(),
                    validation_start=val.isoformat(),test_start=test.isoformat(),
                    test_end=add_months(test,c['test_months']).isoformat()))
                test=add_months(test,c['test_months'])
    manifest=dict(segments=segments,folds=folds,gap_repaired=False,quarantined=quarantined,
                  protocol_sha256=digest('configs/v1t5_preregistration.json'))
    with output.open('x') as target: json.dump(manifest,target,indent=2)
    print('MANIFEST',digest(output),'FOLDS',{k:len(v) for k,v in folds.items()},flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--canonical',required=True)
    a=p.parse_args();prepare(a.root,a.canonical)
