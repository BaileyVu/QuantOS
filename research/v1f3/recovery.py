"""Targeted authoritative recovery audit; no new strategy PnL or source rewriting."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import numpy as np
import pyarrow.parquet as pq
from research.v1f1.dataset import get, digest, write_once, parse, HOUR
from research.v1f1.evaluate import load_panel, encode_report
from research.v1f1.prepare import START
from research.v1f2.carry import valid_open


def main():
    p = argparse.ArgumentParser(); p.add_argument('--root', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True); p.add_argument('--f2', type=Path, required=True)
    p.add_argument('--include-candidates', action='store_true')
    a = p.parse_args(); panel, _ = load_panel(a.data)
    report = json.loads(next((a.f2/'evaluation').glob('development-*.json')).read_text())
    stress = next(r for r in report['results'] if r['variant']=='A' and r['cost']=='stress')
    path = a.f2/'evaluation'/('A-stress-'+stress['artifact_sha256']+'.json')
    assert digest(path.read_bytes()) == stress['artifact_sha256']
    fills = json.loads(path.read_text())['fills']
    requests = sorted({(f['symbol'], f['hour']) for f in fills
                       if f['reason']=='data_loss' and not valid_open(panel[f['symbol']], f['hour'])})
    prior = json.loads((a.root/'recovery-audit.json').read_text()) if a.include_candidates else None
    cached = {x['url']:x['sha256'] for r in prior['records'] for x in r['attempts'] if 'sha256' in x} if prior else {}
    if a.include_candidates:
        affected = {h for s,h in requests if not np.isfinite(panel[s].mark[h,0])}
        extended = set(requests)
        for row in pq.read_table(a.data/'candidates.parquet').to_pylist():
            h=(row['timestamp']-START)//HOUR; s=row['symbol']
            if h in affected and not np.isfinite(panel[s].mark[h,0]):extended.add((s,h))
        requests=sorted(extended)
    def fetch(url):
        if url in cached:
            raw=(a.root/'recovery-raw'/cached[url]).read_bytes();assert digest(raw)==cached[url];return raw
        raw = get(url); write_once(a.root/'recovery-raw'/digest(raw), raw)
        return raw
    def probe(task):
        symbol, h = task; s = panel[symbol]; timestamp = START+h*HOUR
        date = datetime.fromtimestamp(timestamp/1000, timezone.utc).strftime('%Y-%m-%d')
        result = {'symbol':symbol, 'hour':h, 'timestamp':timestamp, 'attempts':[], 'recovered':None}
        if not np.isfinite(s.mark[h, 0]):
            key = f'data/futures/um/daily/markPriceKlines/{symbol}/1h/{symbol}-1h-{date}.zip'
            url = 'https://data.binance.vision/'+key
            try:
                check = fetch(url+'.CHECKSUM').decode().split()
                assert check[1].lstrip('*') == key.rsplit('/',1)[1]
                raw = fetch(url); assert digest(raw) == check[0]
                table, bad = parse(raw, 'markPriceKlines')
                rows = [r for r in table.to_pylist() if r['timestamp']==timestamp]
                result['attempts'].append({'url':url, 'sha256':digest(raw), 'quarantined':bad, 'matching_rows':len(rows)})
                if len(rows)==1: result['recovered']=rows[0]
            except Exception as e: result['attempts'].append({'url':url, 'error':str(e)})
            url = 'https://fapi.binance.com/fapi/v1/markPriceKlines?'+urllib.parse.urlencode(
                {'symbol':symbol,'interval':'1h','startTime':timestamp,'endTime':timestamp+HOUR-1,'limit':2})
            try:
                raw = fetch(url); rows = json.loads(raw)
                match = [r for r in rows if isinstance(r,list) and r[0]==timestamp]
                result['attempts'].append({'url':url,'sha256':digest(raw),'matching_rows':len(match),'raw_rows':match})
                if len(match)==1:
                    r=match[0]; o,high,low,c=map(float,r[1:5])
                    assert int(r[6])==timestamp+HOUR-1 and 0<low<=min(o,c)<=max(o,c)<=high
                    recovered=dict(timestamp=timestamp,open=o,high=high,low=low,close=c,volume=float(r[5]),quote_volume=float(r[7]),taker_quote=float(r[10]))
                    if result['recovered'] is not None:
                        assert all(result['recovered'][k]==recovered[k] for k in ['open','high','low','close'])
                    result['recovered']=recovered
            except Exception as e:
                if isinstance(e, AssertionError):result['recovered']=None
                result['attempts'].append({'url':url,'error':str(e)})
        else:
            j=s.funding_before(timestamp); result['prior_funding']=s.funding[j].tolist()
            result['following_funding']=s.funding[j+1].tolist() if j+1<len(s.funding) else None
            url='https://fapi.binance.com/fapi/v1/fundingRate?'+urllib.parse.urlencode(
                {'symbol':symbol,'startTime':int(s.funding[j,0])+1,'endTime':timestamp,'limit':100})
            try:
                raw=fetch(url); result['attempts'].append({'url':url,'sha256':digest(raw),'rows':json.loads(raw)})
            except Exception as e: result['attempts'].append({'url':url,'error':str(e)})
            result['note']='Preserve F2 staleness semantics; never fabricate settlements or infer future interval changes.'
        return result
    with ThreadPoolExecutor(max_workers=4) as pool: records=list(pool.map(probe,requests))
    output={'f2_stress_artifact_sha256':stress['artifact_sha256'],'records':records,
            'recovered_mark_rows':sum(r['recovered'] is not None for r in records)}
    filename='universe-recovery-audit.json' if a.include_candidates else 'recovery-audit.json'
    write_once(a.root/filename,encode_report(output))
    print(json.dumps({'file':filename,'requests':len(records),'recovered_mark_rows':output['recovered_mark_rows']}))


if __name__=='__main__':main()
