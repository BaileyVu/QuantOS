"""Research-only receipt reads. Fixed RPC methods; no transaction submission."""
import json
import re
import time
import urllib.request
from collections import defaultdict
from decimal import localcontext
from ..config import local_path
from ..domain.common import D, CONTEXT, iso
from ..storage import canonical, digest
from .public import NoRedirect

RPC = 'https://polygon-bor-rpc.publicnode.com'
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'


class PublicPolygon:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.evidence = []

    def read(self, method, params):
        valid = ((method == 'eth_chainId' and params == []) or
                 (method == 'eth_getTransactionReceipt' and len(params) == 1 and re.fullmatch(r'0x[0-9a-fA-F]{64}', params[0])) or
                 (method == 'eth_getBlockByNumber' and len(params) == 2 and re.fullmatch(r'0x[0-9a-fA-F]+', params[0]) and params[1] is False))
        if not valid:
            raise ValueError('RPC method/parameters outside read-only research allowlist')
        payload = dict(jsonrpc='2.0', id=1, method=method, params=params)
        req = urllib.request.Request(RPC, data=canonical(payload).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'QuantOS-PM-public-research'})
        with self.opener.open(req, timeout=20) as response:
            data = response.read(8*1024*1024+1)
        if len(data) > 8*1024*1024:
            raise ValueError('RPC response size limit')
        result = json.loads(data)
        if result.get('id') != 1 or 'error' in result or 'result' not in result:
            raise ValueError('invalid or failed RPC read: '+str(result.get('error', 'envelope')))
        self.evidence.append(dict(request=payload, response=result, received_at=iso(time.time_ns()//1000000)))
        return result['result']


def inspect_receipt(wallet, row, receipt, block):
    if receipt is None or block is None:
        return dict(verified=False, reason='receipt or canonical block unavailable')
    if receipt['transactionHash'].lower() != row['transaction_hash'].lower() or receipt['blockHash'] != block['hash']:
        raise ValueError('transaction/canonical-block identity mismatch')
    if int(receipt['status'],16) != 1:
        return dict(verified=False, reason='chain transaction failed')
    stamp = int(block['timestamp'],16)
    if stamp != int(row['timestamp']):
        return dict(verified=False, reason='API and canonical-block timestamps differ', block_timestamp=stamp)
    word = '0x'+'0'*24+wallet[2:].lower()
    flows, wallet_logs = defaultdict(int), 0
    for log in receipt['logs']:
        topics = [s.lower() for s in log.get('topics', [])]
        wallet_logs += word in topics
        if len(topics) == 3 and topics[0] == TRANSFER and re.fullmatch(r'0x[0-9a-fA-F]{64}', log['data']):
            q = int(log['data'],16)
            if topics[1] == word:
                flows[log['address'].lower()] -= q
            if topics[2] == word:
                flows[log['address'].lower()] += q
    involved = wallet_logs > 0 or receipt.get('from','').lower() == wallet.lower() or receipt.get('to','').lower() == wallet.lower()
    return dict(verified=involved, reason='successful canonical receipt with wallet involvement' if involved else 'wallet involvement not established',
                block_number=int(receipt['blockNumber'],16), block_timestamp=stamp,
                transaction_hash=row['transaction_hash'], activity_type=row['type'],
                api_usdc_size=row['usdc_size'], wallet_log_count=wallet_logs,
                erc20_net_raw_units={a:str(q) for a,q in flows.items()},
                interpretation='raw token units only; no assumed decimals/USD identity or full fee attribution',
                receipt_sha256=digest(canonical(receipt).encode()))


def verify_samples(directory, limit=12):
    directory = local_path(directory)
    if not 1 <= limit <= 50:
        raise ValueError('sample limit must be 1..50')
    report = json.loads(local_path(directory/'report.json',must_exist=True).read_text())
    wallet = report['wallet']
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}',wallet):
        raise ValueError('invalid research wallet')
    rows = json.loads(local_path(directory/'activity.json',must_exist=True).read_text())
    selected, seen, covered = [], set(), set()
    # First cover distinct action types, then fill the sample chronologically.
    for row in rows:
        tx = row.get('transaction_hash','')
        if row['type'] not in covered and re.fullmatch(r'0x[0-9a-fA-F]{64}',tx) and tx not in seen:
            selected.append(row);seen.add(tx);covered.add(row['type'])
            if len(selected)==limit:break
    if len(selected)<limit:
        for row in rows:
            tx=row.get('transaction_hash','')
            if tx not in seen and re.fullmatch(r'0x[0-9a-fA-F]{64}',tx):
                selected.append(row);seen.add(tx)
                if len(selected)==limit:break
    rpc, results, errors = PublicPolygon(), [], []
    try:
        if int(rpc.read('eth_chainId',[]),16)!=137:
            raise ValueError('unexpected chain id')
        for row in selected:
            try:
                receipt=rpc.read('eth_getTransactionReceipt',[row['transaction_hash']])
                block=rpc.read('eth_getBlockByNumber',[receipt['blockNumber'],False]) if receipt else None
                results.append(inspect_receipt(wallet,row,receipt,block))
            except Exception as error:
                errors.append(f"{row['transaction_hash']}: {type(error).__name__}: {error}")
    except Exception as error:
        errors.append(f'{type(error).__name__}: {error}')
    result=dict(wallet=wallet,endpoint=RPC,selected=len(selected),verified=sum(r['verified'] for r in results),
                results=results,errors=errors,full_reconciliation=False,
                limitations=['purposive sample, not statistical coverage','no token-burn/fee/maker ABI reconstruction',
                             'canonical block as returned by one public RPC; no independent finality proof'])
    destination=local_path(directory/f'chain-sample-{time.time_ns()}.json')
    destination.write_text(canonical(dict(result,evidence=rpc.evidence))+'\n')
    result['evidence_file']=str(destination)
    return result
