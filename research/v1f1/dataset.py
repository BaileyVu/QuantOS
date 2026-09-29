"""Checksum-bound hourly Binance USD-M research acquisition, no economic metrics."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

BUCKET = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
BASE = 'https://data.binance.vision/'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
STABLES = {'USDC','BUSD','USDT','TUSD','USDP','DAI','FDUSD','USD1','PAX','UST','USTC'}
HOUR = 3600000

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as f:
            f.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f'immutable collision: {path}')

def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403,404):
                raise
            if attempt == 3:
                raise
        except (OSError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)

def catalog(root, prefix, delimiter=None):
    cache = root/'catalog'/ (digest(prefix.encode()) + ('.dirs.json' if delimiter else '.keys.json'))
    if cache.exists():
        return json.loads(cache.read_text())
    items, marker = [], None
    while True:
        params = {'prefix':prefix}
        if delimiter:
            params['delimiter'] = delimiter
        if marker:
            params['marker'] = marker
        raw = get(BUCKET+'?'+urllib.parse.urlencode(params))
        write_once(root/'catalog-raw'/digest(raw), raw)
        tree = ET.fromstring(raw)
        items.extend(n.text for n in tree.findall('s:CommonPrefixes/s:Prefix' if delimiter else 's:Contents/s:Key',NS))
        if tree.findtext('s:IsTruncated',namespaces=NS) == 'false':
            break
        new = tree.findtext('s:NextMarker',namespaces=NS) or items[-1]
        if new == marker:
            raise ValueError('nonadvancing listing')
        marker = new
    write_once(cache, json.dumps(sorted(set(items))).encode())
    return sorted(set(items))

def parse(raw, family):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist()) != 1:
            raise ValueError('unexpected ZIP members')
        with z.open(z.namelist()[0]) as f:
            rows = list(csv.reader(io.TextIOWrapper(f,encoding='utf-8-sig')))
    if rows and not rows[0][0].isdigit():
        rows = rows[1:]
    if not rows:
        raise ValueError('empty archive')
    width = 3 if family == 'fundingRate' else 12
    if any(len(r)!=width for r in rows):
        raise ValueError('bad schema')
    a = np.asarray(rows,dtype=np.float64)
    timestamps = a[:,0].astype(np.int64)
    if np.any(np.diff(timestamps)<=0) or np.any(timestamps<1500000000000) or np.any(timestamps>1900000000000):
        raise ValueError('time units, duplicates or ordering')
    if family == 'fundingRate':
        valid = np.all(np.isfinite(a),axis=1)&(a[:,1]>0)
        names = ['timestamp','interval_hours','rate']
        columns = [timestamps,a[:,1],a[:,2]]
    else:
        valid = (np.all(np.isfinite(a[:,:11]),axis=1)&(timestamps%HOUR==0)
                 &(a[:,6]==timestamps+HOUR-1)&np.all(a[:,1:5]>0,axis=1)
                 &(a[:,3]<=np.minimum(a[:,1],a[:,4]))&(a[:,2]>=np.maximum(a[:,1],a[:,4])))
        if family=='klines':
            valid &= ((a[:,5]>=0)&(a[:,7]>=0)&(a[:,8]>=0)&(a[:,9]>=0)&(a[:,9]<=a[:,5])&(a[:,10]>=0)&(a[:,10]<=a[:,7]))
        names=['timestamp','open','high','low','close','volume','quote_volume','taker_quote']
        columns=[timestamps,*[a[:,i] for i in [1,2,3,4,5,7,10]]]
    table=pa.table({n:col[valid] for n,col in zip(names,columns)})
    return table, int((~valid).sum())

def acquire(root,key,family):
    cache=root/'records'/(digest(key.encode())+'.json')
    if cache.exists():
        record=json.loads(cache.read_text())
        if record.get('ok'):
            return record
    try:
        checksum=get(BASE+key+'.CHECKSUM')
        fields=checksum.decode().split()
        if len(fields)!=2 or fields[1].lstrip('*')!=key.rsplit('/',1)[1]:
            raise ValueError('checksum filename')
        raw_path=root/'raw'/fields[0]
        raw=raw_path.read_bytes() if raw_path.exists() else get(BASE+key)
        if digest(raw)!=fields[0]:
            raise ValueError('checksum mismatch')
        write_once(raw_path,raw)
        write_once(root/'checksums'/digest(checksum),checksum)
        table,bad=parse(raw,family)
        metadata={b'source_url':(BASE+key).encode(),b'raw_sha256':fields[0].encode(),b'schema':b'v1f1-hourly-v1'}
        table=table.replace_schema_metadata(metadata)
        sink=pa.BufferOutputStream()
        pq.write_table(table,sink,compression='zstd')
        encoded=sink.getvalue().to_pybytes()
        parquet=root/'parquet'/(digest(encoded)+'.parquet')
        write_once(parquet,encoded)
        record={'ok':True,'key':key,'family':family,'raw_sha256':fields[0],
                'parquet_sha256':digest(encoded),'parquet':str(parquet),'rows':len(table),'quarantined_rows':bad,
                'first':int(table['timestamp'][0].as_py()) if len(table) else None,
                'last':int(table['timestamp'][-1].as_py()) if len(table) else None}
        write_once(cache,json.dumps(record,sort_keys=True).encode())
        return record
    except Exception as e:
        return {'ok':False,'key':key,'family':family,'error':str(e)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--stage',choices=['perp','context'],required=True)
    args=p.parse_args(); root=args.root
    if args.stage=='perp':
        prefixes=catalog(root,'data/futures/um/monthly/klines/','/')
        symbols=[x.split('/')[-2] for x in prefixes if re.fullmatch('[A-Z0-9]+USDT',x.split('/')[-2]) and x.split('/')[-2][:-4] not in STABLES]
        def keys(symbol):
            entries=catalog(root,f'data/futures/um/monthly/klines/{symbol}/1h/')
            return [k for k in entries if k.endswith('.zip') and '2019-09'<=k[-11:-4]<='2024-12']
        tasks=[]
        with ThreadPoolExecutor(max_workers=12) as pool:
            for result in pool.map(keys,symbols):tasks.extend((k,'klines') for k in result)
        print(json.dumps({'symbols_discovered':len(symbols),'perp_partitions':len(tasks)}),flush=True)
    else:
        pairs=json.loads((root/'context_requests.json').read_text())
        tasks=[]
        for symbol,month in pairs:
            for family in ['indexPriceKlines','markPriceKlines','fundingRate']:
                folder='' if family=='fundingRate' else '1h/'
                suffix='fundingRate' if family=='fundingRate' else '1h'
                tasks.append((f'data/futures/um/monthly/{family}/{symbol}/{folder}{symbol}-{suffix}-{month}.zip',family))
    records=[]
    with ThreadPoolExecutor(max_workers=12) as pool:
        for i,record in enumerate(pool.map(lambda task:acquire(root,*task),tasks),1):
            records.append(record)
            if i%250==0:print(json.dumps({'stage':args.stage,'completed':i,'total':len(tasks),'errors':sum(not r['ok'] for r in records)}),flush=True)
    result=json.dumps({'stage':args.stage,'records':records},sort_keys=True).encode()
    write_once(root/'manifests'/(args.stage+'-'+digest(result)+'.json'),result)
    print(json.dumps({'stage':args.stage,'manifest_sha256':digest(result),'partitions':len(records),'errors':sum(not r['ok'] for r in records)}),flush=True)

if __name__=='__main__':main()
