"""Deterministic synthetic mechanics cases. NOT sampled Polymarket history."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).parent
base = datetime(2026, 7, 1, tzinfo=timezone.utc)
events = []


def stamp(dt):
    return dt.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def add(kind, dt, **payload):
    events.append(dict(id=f'fixture-{len(events):05}', type=kind, event_time=stamp(dt),
                       receipt_time=stamp(dt+timedelta(milliseconds=10)), **payload))


for index in range(6):
    start = base+timedelta(days=index)
    mid = '0x'+f'{index+1:064x}'
    add('market', start, market=mid, tokens={'UP': str(index*2+1), 'DOWN': str(index*2+2)},
        start=stamp(start), expiry=stamp(start+timedelta(minutes=5)), symbol='BTC',
        tick='0.01', minimum='50' if index == 5 else '5', fee_rate='0.07',
        rules='SYNTHETIC: UP if terminal reference >= initial reference; else DOWN; $1 per pair',
        rules_verified=True, fee_verified=True, active=True, synthetic=True)
    for o in ('UP', 'DOWN'):
        add('snapshot', start+timedelta(seconds=1), market=mid, outcome=o, seq=1,
            sequence_verified=True, bids=[['0.49', '10']], asks=[['0.51', '10']])
    add('timer', start+timedelta(seconds=2))
    if index in (0, 1, 2, 3):
        add('trade', start+timedelta(seconds=3), market=mid, outcome='UP', price='0.49', size='44',
            aggressor='SELL', aggressor_verified=True)
        if index != 1:
            add('trade', start+timedelta(seconds=4), market=mid, outcome='DOWN', price='0.49', size='46',
                aggressor='SELL', aggressor_verified=True)
        add('trade', start+timedelta(seconds=5), market=mid, outcome='UP', price='0.49', size='20' if index == 3 else '60',
            aggressor='SELL', aggressor_verified=True)
        if index != 1:
            add('trade', start+timedelta(seconds=6), market=mid, outcome='DOWN', price='0.49', size='20' if index == 3 else '60',
                aggressor='SELL', aggressor_verified=True)
    elif index == 4:
        add('trade', start+timedelta(seconds=3), market=mid, outcome='UP', price='0.49', size='100',
            aggressor='UNKNOWN', aggressor_verified=False)
        add('delta', start+timedelta(seconds=4), market=mid, outcome='UP', seq=2,
            sequence_verified=True, changes=[['BUY', '0.49', '1']])  # depth removal is NOT a fill
    add('timer', start+timedelta(seconds=31))
    add('timer', start+timedelta(seconds=33))
    add('timer', start+timedelta(seconds=241))
    add('resolution', start+timedelta(seconds=301), market=mid, winner='DOWN' if index == 1 else 'UP')
(root/'btc_5m.jsonl').write_text(''.join(json.dumps(e, sort_keys=True)+'\n' for e in events))
# Training observations precede the fixture; synthetic, deliberately favorable
# for exercising mechanics. They are neither wallet observations nor edge evidence.
rows = []
for i in range(2100):
    rows.append(dict(market=f'synthetic-training-{i}', day=f'2026-06-{1+i%28:02}',
                     observed_at=f'2026-06-{1+i%28:02}T00:05:01Z',
                     result='one' if i < 4 else 'neither' if i < 104 else 'both_full'))
cal = dict(source='synthetic mechanics calibration v1', synthetic=True,
           trained_through='2026-06-29T00:00:00Z', execution_model='pessimistic',
           max_size='20', max_queue='20', slippage_and_ops_per_share='0.001', observations=rows)
(root/'calibration.json').write_text(json.dumps(cal, sort_keys=True)+'\n')
