"""Fixed allowlist of unauthenticated GETs and public market subscriptions only."""
import asyncio
import json
import re
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from ..domain.common import D, iso, utc_ms
from ..config import load, local_path
from ..storage import canonical, capture_parquet

HOSTS = {'gamma': 'https://gamma-api.polymarket.com', 'clob': 'https://clob.polymarket.com',
         'data': 'https://data-api.polymarket.com'}
WS_URL = 'wss://ws-subscriptions-clob.polymarket.com/ws/market'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('redirect rejected by public-data allowlist')


class PublicData:
    def __init__(self):
        self.evidence = []
        # Ignore environment proxy/auth settings; never inspect credentials.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, service, path, params=None):
        allowed = ((service == 'gamma' and path == '/markets') or
                   (service == 'clob' and (path in ('/time', '/book') or re.fullmatch(r'/clob-markets/0x[0-9a-fA-F]{64}', path))) or
                   (service == 'data' and path in ('/v2/activity', '/v2/trades', '/v2/positions',
                                                   '/v2/user-pnl', '/v2/user-stats', '/v2/status')))
        if not allowed:
            raise ValueError('not an allowed public read endpoint')
        url = HOSTS[service]+path
        if params:
            url += '?'+urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'User-Agent': 'QuantOS-PM/0.1 public-research'}, method='GET')
        with self.opener.open(request, timeout=20) as response:
            raw = response.read(16*1024*1024+1)
        if len(raw) > 16*1024*1024:
            raise ValueError('public response exceeds safety bound')
        data = json.loads(raw, parse_float=D)
        self.evidence.append(dict(url=url, receipt_time=iso(time.time_ns()//1000000), payload=data))
        return data


def discovery(api, now):
    """Resolve the rolling BTC five-minute slug, then cross-check CLOB identities."""
    slot = now//1000//300*300
    for start in ((slot, slot+300) if (slot+300)*1000-now > 75000 else (slot+300,)):
        candidates = api.get('gamma', '/markets', {'slug': f'btc-updown-5m-{start}'})
        if not isinstance(candidates, list):
            raise ValueError('unexpected Gamma envelope')
        for g in candidates:
            if g.get('slug') != f'btc-updown-5m-{start}' or g.get('closed') is not False:
                continue
            labels = json.loads(g['outcomes']) if isinstance(g['outcomes'], str) else g['outcomes']
            ids = json.loads(g['clobTokenIds']) if isinstance(g['clobTokenIds'], str) else g['clobTokenIds']
            if len(labels) != 2 or {x.upper() for x in labels} != {'UP', 'DOWN'}:
                continue
            tokens = dict(zip([x.upper() for x in labels], ids))
            cid = g['conditionId']
            details = api.get('clob', f'/clob-markets/{cid}')
            actual = {t['o'].upper(): t['t'] for t in details['t']}
            if actual != tokens:
                raise ValueError('Gamma/CLOB token mismatch')
            end = utc_ms(g['endDate'])
            if end != (start+300)*1000:
                raise ValueError('market expiry does not match five-minute slug')
            fd = details.get('fd') or {}
            fee_known = fd.get('r') is not None and D(fd.get('e', '-1')) == 1 and fd.get('to') is True and details.get('mbf') == 0
            return dict(type='market', market=cid, tokens=tokens, start=iso(start*1000), expiry=iso(end),
                        symbol='BTC', tick=str(details['mts']), minimum=str(details['mos']),
                        fee_rate=str(fd['r']) if fee_known else '0', fee_verified=fee_known,
                        rules=g.get('description', ''), rules_verified=False,
                        active=g.get('active') is True and g.get('acceptingOrders') is True,
                        validation_note='Rules require independent review; unsequenced public stream cannot authorize quotes')
    raise ValueError('no discoverable current/next BTC five-minute market')


class Normalizer:
    def __init__(self, market):
        self.market = market
        self.mapping = {v: k for k, v in market['tokens'].items()}
        self.counter = 0

    def envelope(self, payload, receipt, event_ms=None):
        self.counter += 1
        return dict(payload, id=f'public-{self.counter:09d}',
                    event_time=iso(receipt if event_ms is None else event_ms), receipt_time=iso(receipt))

    def messages(self, message, receipt):
        kind = message.get('event_type')
        if kind == 'new_market':
            return []  # discovery of the next window happens in the session coordinator
        if message.get('market') != self.market['market']:
            raise ValueError('stream condition mismatch')
        ts = int(message['timestamp'])
        base = dict(market=self.market['market'])
        if kind == 'book':
            payload = dict(base, type='snapshot', outcome=self.mapping[message['asset_id']],
                           bids=[[str(x['price']), str(x['size'])] for x in message['bids']],
                           asks=[[str(x['price']), str(x['size'])] for x in message['asks']],
                           sequence_verified=False)
            return [self.envelope(payload, receipt, ts)]
        if kind == 'price_change':
            grouped = {}
            for change in message['price_changes']:
                outcome = self.mapping[change['asset_id']]
                grouped.setdefault(outcome, []).append([change['side'], str(change['price']), str(change['size'])])
            return [self.envelope(dict(base, type='delta', outcome=o, changes=changes, sequence_verified=False), receipt, ts)
                    for o, changes in grouped.items()]
        if kind == 'last_trade_price':
            # Public side semantics alone do not prove maker queue execution.
            return [self.envelope(dict(base, type='trade', outcome=self.mapping[message['asset_id']],
                                      size=str(message['size']), price=str(message['price']),
                                      aggressor=message.get('side', 'UNKNOWN'), aggressor_verified=False), receipt, ts)]
        if kind == 'tick_size_change':
            return [self.envelope(dict(base, type='tick', outcome=self.mapping[message['asset_id']], tick=str(message['new_tick_size'])), receipt, ts)]
        if kind == 'market_resolved':
            winner = self.mapping[message['winning_asset_id']]
            return [self.envelope(dict(base, type='resolution', winner=winner), receipt, ts)]
        if kind == 'best_bid_ask':
            return []  # not an L2 update or a fill
        raise ValueError(f'unsupported market event {kind}')


async def capture(api, market, seconds, sink, raw):
    from websockets.asyncio.client import connect
    norm = Normalizer(market)
    now = time.time_ns()//1000000
    sink(norm.envelope(market, now))
    # REST snapshots are captured as evidence; stream snapshots start canonical L2.
    # No pretending that an unsequenced REST/WS splice is verified.
    for token in market['tokens'].values():
        api.get('clob', '/book', {'token_id': token})
    deadline = time.monotonic()+seconds
    async with connect(WS_URL, open_timeout=20, ping_interval=None, proxy=None, max_size=8*1024*1024) as ws:
        await ws.send(json.dumps({'assets_ids': list(market['tokens'].values()), 'type': 'market', 'custom_feature_enabled': True}))
        next_ping = time.monotonic()
        while time.monotonic() < deadline:
            if time.monotonic() >= next_ping:
                await ws.send('PING')
                next_ping = time.monotonic()+10
            try:
                payload = await asyncio.wait_for(ws.recv(), timeout=min(1, max(.01, deadline-time.monotonic())))
            except asyncio.TimeoutError:
                sink(norm.envelope({'type': 'timer'}, time.time_ns()//1000000))
                continue
            receipt = time.time_ns()//1000000
            if payload == 'PONG':
                continue
            data = json.loads(payload, parse_float=D)
            raw.append(dict(receipt_time=iso(receipt), payload=data))
            for msg in data if isinstance(data, list) else [data]:
                for event in norm.messages(msg, receipt):
                    sink(event)
        sink(norm.envelope({'type': 'status', 'market': market['market'], 'active': False}, time.time_ns()//1000000))
        sink(norm.envelope({'type': 'disconnect', 'reason': 'bounded shadow session ended; no reconnect inference'}, time.time_ns()//1000000))


def shadow_run(config_path, seconds=60):
    from ..engine import Engine
    from ..application import run
    c = load(config_path)
    if c.mode != 'shadow' or not 1 <= seconds <= 3600:
        raise ValueError('shadow mode and duration 1..3600 seconds required')
    directory = local_path(f'artifacts/captures/shadow-{time.time_ns()}')
    directory.mkdir(parents=True)
    api, raw, events = PublicData(), [], []
    engine = Engine(c)  # same six-boundary engine; synthetic calibration never crosses into shadow
    feed_path = local_path(directory/'events.jsonl')
    status = dict(public_feed_worked=False, virtual_only=True, capture=str(directory), errors=[])
    with feed_path.open('x') as output:
        def sink(e):
            events.append(e)
            output.write(canonical(e)+'\n')
            output.flush()
            engine.process(e)
        try:
            before = time.time_ns()//1000000
            server = api.get('clob', '/time')
            after = time.time_ns()//1000000
            server_ms = int(server)*1000
            if server_ms < before-2000 or server_ms > after+2000 or after-before > 5000:
                raise ValueError('server clock offset or round-trip uncertain')
            market = discovery(api, after)
            # Stop at expiry; bounded sessions are deliberately restarted for new windows.
            duration = min(seconds, max(1, (utc_ms(market['expiry'])-after)//1000))
            asyncio.run(capture(api, market, duration, sink, raw))
            status['public_feed_worked'] = any(e['type'] == 'snapshot' for e in events)
        except Exception as error:
            status['errors'].append(f'{type(error).__name__}: {error}')
            if events:
                now = time.time_ns()//1000000
                sink(dict(id='capture-failure', type='disconnect', event_time=iso(now), receipt_time=iso(now)))
    status['canonical_events'] = len(events)
    status['raw_messages'] = len(raw)
    status['event_types'] = {kind: sum(e['type'] == kind for e in events) for kind in sorted({e['type'] for e in events})}
    evidence = api.evidence+raw
    if evidence:
        status['parquet_sha256'] = capture_parquet(directory/'public.parquet', evidence)
    feed_path.chmod(0o444)
    if events:
        r = run(config_path, str(feed_path))
        status.update(run_id=r['run_id'], simulated_pnl=r['pnl_excluding_incentives'],
                      simulated_orders=r['orders']['quoted'], skip_reasons=r['skip_reasons'])
    status['limitations'] = ['bounded single-market session; rerun for next window',
                            'no independently validated live calibration or settlement-rule approval',
                            'public book stream lacks exchange sequence numbers; quotes fail closed',
                            'public trade aggressor/queue attribution unverified; no awarded live fills']
    local_path(directory/'status.json').write_text(canonical(status)+'\n')
    return status
