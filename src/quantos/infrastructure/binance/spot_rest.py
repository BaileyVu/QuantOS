"""Spot REST boundary. HMAC transport is Testnet-only for economic requests."""
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

from quantos.domain.execution.core import ExecutionError
from quantos.domain.execution.contracts import OrderType
from quantos.domain.runtime_contracts import identity

TESTNET = 'https://testnet.binance.vision'
MAINNET = 'https://api.binance.com'


class SpotError(ExecutionError):
    def __init__(self, category, code=None):
        self.category, self.code = category, code
        super().__init__(f'Spot request {category}; code={code}')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SpotError('redirect refused')


def transport(method, url, headers, body, timeout):
    # Never follow a redirect carrying authentication to another origin.
    try:
        with build_opener(NoRedirect).open(Request(url, data=body, headers=headers, method=method), timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except HTTPError as e:
        return e.code, dict(e.headers), e.read()


@dataclass(frozen=True)
class Credentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)

    def __post_init__(self):
        if not self.key or not self.secret or any(c.isspace() for c in self.key+self.secret):
            raise ValueError('missing or invalid credentials')


def client_id(request_id):
    # 32 digest hex characters plus prefix; all characters accepted by Spot.
    return 'qos-'+hashlib.sha256(request_id.encode('ascii')).hexdigest()[:32]


def decimal(value):
    if type(value) not in (str, int, Decimal):
        raise SpotError('invalid numeric response')
    v=Decimal(value)
    if not v.is_finite() or v<0:
        raise SpotError('invalid numeric response')
    return v


class SpotRest:
    def __init__(self, credentials=None, *, testnet=True, http=transport, clock=time.time, timeout=10):
        if type(testnet) is not bool or not 0<timeout<=30:
            raise ValueError('invalid REST configuration')
        self.credentials=credentials
        self.testnet=testnet
        self.base=TESTNET if testnet else MAINNET
        self.http,self.clock,self.timeout=http,clock,timeout
        self.offset_ms=None
        self.synced_at=None
        self.blocked_until=0

    @property
    def scope(self):
        if self.credentials is None:raise SpotError('credentials required')
        return identity((self.base, hashlib.sha256(self.credentials.key.encode()).hexdigest()))

    def _call(self, method, path, params=(), *, signed=False):
        if (signed or method != 'GET') and (not self.testnet or self.base != TESTNET):
            raise SpotError('Mainnet/non-Testnet authenticated/economic requests disabled')
        if self.clock()<self.blocked_until:raise SpotError('rate limited')
        values=dict(params)
        headers={}
        if signed:
            if self.credentials is None:raise SpotError('credentials required')
            if self.synced_at is None or not 0<=self.clock()-self.synced_at<=30:
                self.sync_time()
            values.update(timestamp=int(self.clock()*1000)+self.offset_ms,recvWindow=5000)
            headers['X-MBX-APIKEY']=self.credentials.key
        payload=urlencode(values).encode('ascii')
        if signed:
            payload+=b'&signature='+hmac.new(self.credentials.secret.encode(),payload,hashlib.sha256).hexdigest().encode()
        url=self.base+path
        body=None
        if method=='GET':
            if payload:url+='?'+payload.decode('ascii')
        else:
            body=payload;headers['Content-Type']='application/x-www-form-urlencoded'
        try:
            status,response_headers,raw=self.http(method,url,headers,body,self.timeout)
        except Exception:
            # Do not expose original exception: transports may include signed URLs.
            raise SpotError('UNKNOWN' if method!='GET' else 'unavailable') from None
        try:
            result=json.loads(raw, parse_float=Decimal)
        except Exception:
            raise SpotError('UNKNOWN' if method!='GET' else 'invalid response') from None
        code=result.get('code') if isinstance(result,dict) else None
        if status in (418,429):
            try:delay=max(1,int({k.lower(): v for k,v in response_headers.items()}.get('retry-after','60')))
            except (ValueError,TypeError):delay=60
            self.blocked_until=self.clock()+delay
        if status>=500 or code in (-1006,-1007):raise SpotError('UNKNOWN',code)
        if status not in range(200,300) or (isinstance(code,int) and code<0):
            category='not found' if code==-2013 else ('authentication' if code in (-2014,-2015,-1022) else 'rejected')
            raise SpotError(category,code)
        return result

    def sync_time(self):
        before=self.clock();data=self._call('GET','/api/v3/time');after=self.clock()
        if not isinstance(data,dict) or type(data.get('serverTime')) is not int or not 0<=after-before<=2:
            raise SpotError('unreliable server clock')
        self.offset_ms=data['serverTime']-int((before+after)*500)
        self.synced_at=after
        return data

    def exchange_info(self,symbol='BTCUSDT'):
        if symbol not in ('BTCUSDT','ETHUSDT'):raise SpotError('unsupported symbol')
        return self._call('GET','/api/v3/exchangeInfo',{'symbol':symbol})

    def account(self):
        data=self._call('GET','/api/v3/account',signed=True)
        if data.get('canTrade') is not True or (not self.testnet and data.get('canWithdraw') is not False):
            raise SpotError('account permission policy')
        # /account may omit the key-level withdrawal flag: the Testnet-only endpoint
        # has no withdrawals; Mainnet key permission validation is not implemented.
        return data

    def balances(self):
        data=self.account();values={}
        for row in data['balances']:
            asset=row['asset']
            if asset in values:raise SpotError('duplicate asset')
            values[asset]=(decimal(row['free']),decimal(row['locked']))
        if 'USDT' not in values:raise SpotError('USDT account missing')
        return values

    def open_orders(self):
        return self._call('GET','/api/v3/openOrders',signed=True)

    def query_order(self,symbol,cid):
        return self._call('GET','/api/v3/order',{'symbol':symbol,'origClientOrderId':cid},signed=True)

    def trades(self,symbol,order_id):
        # >=1000 fills is ambiguous; block rather than silently truncate.
        data=self._call('GET','/api/v3/myTrades',{'symbol':symbol,'orderId':order_id,'limit':1000},signed=True)
        if not isinstance(data,list) or len(data)>=1000:raise SpotError('incomplete fill history')
        return data

    def _params(self,intent):
        r=intent.request
        values={'symbol':r.symbol,'side':r.side.value,'type':r.order_type.value,
                'quantity':format(r.quantity,'f'),'newClientOrderId':client_id(r.request_id),'newOrderRespType':'FULL'}
        if r.order_type is OrderType.LIMIT:
            values.update(price=format(r.limit_price,'f'),timeInForce='IOC')
        return values

    def validate_filters(self,intent):
        intent.validate();r=intent.request
        self.sync_time()
        now_ms=int(self.clock()*1000)+self.offset_ms
        age_ms=now_ms-int(r.timestamp.timestamp()*1000)
        if not 0 <= age_ms <= 60000:raise SpotError('stale or future approved intent')
        info=self.exchange_info(r.symbol)
        symbols=info.get('symbols',[])
        if len(symbols)!=1 or symbols[0].get('symbol')!=r.symbol:raise SpotError('invalid exchange information')
        symbol=symbols[0]
        if symbol.get('status')!='TRADING' or symbol.get('isSpotTradingAllowed') is not True or r.order_type.value not in symbol['orderTypes']:
            raise SpotError('symbol not eligible')
        filters={f['filterType']:f for f in symbol['filters']}
        if len(filters)!=len(symbol['filters']) or 'LOT_SIZE' not in filters or 'PRICE_FILTER' not in filters:
            raise SpotError('missing/duplicate filters')
        for name in ('LOT_SIZE','MARKET_LOT_SIZE'):
            if name=='MARKET_LOT_SIZE' and r.order_type is not OrderType.MARKET:continue
            if name not in filters:continue
            f=filters[name];low,high,step=(decimal(f[k]) for k in ('minQty','maxQty','stepSize'))
            if (low and r.quantity<low) or (high and r.quantity>high) or (step and r.quantity%step):
                raise SpotError('quantity filter; no upward rounding permitted')
        price=r.limit_price or intent.reference_price
        if r.limit_price is not None:
            f=filters['PRICE_FILTER'];low,high,tick=(decimal(f[k]) for k in ('minPrice','maxPrice','tickSize'))
            if (low and price<low) or (high and price>high) or (tick and price%tick):raise SpotError('price filter')
        # Local conservative check plus the exchange's signed test validates all
        # dynamic/reference-price and account filters immediately before prepare.
        for name in ('MIN_NOTIONAL','NOTIONAL'):
            if name in filters:
                f=filters[name]
                if price*r.quantity<decimal(f['minNotional']):raise SpotError('minimum notional; do not increase order')
                if name=='NOTIONAL' and price*r.quantity>decimal(f['maxNotional']):raise SpotError('maximum notional')
        if not any(k in filters for k in ('MIN_NOTIONAL','NOTIONAL')):raise SpotError('notional filters unavailable')
        return info

    def test_order(self,intent):
        if not self.testnet:raise SpotError('Mainnet disabled')
        intent.validate();return self._call('POST','/api/v3/order/test',self._params(intent),signed=True)

    def new_order(self,intent):
        if not self.testnet:raise SpotError('Mainnet economic orders disabled')
        intent.validate();return self._call('POST','/api/v3/order',self._params(intent),signed=True)

    def cancel_order(self,symbol,cid):
        if not self.testnet:raise SpotError('Mainnet disabled')
        return self._call('DELETE','/api/v3/order',{'symbol':symbol,'origClientOrderId':cid},signed=True)

    client_id = staticmethod(client_id)

    def observe(self,intent,cid):
        from quantos.domain.execution.exchange import ActualFill, ActualOrder
        raw=self.query_order(intent.request.symbol,cid)
        r=intent.request
        if raw.get('symbol')!=r.symbol or raw.get('side')!=r.side.value or raw.get('type')!=r.order_type.value or raw.get('clientOrderId')!=cid:
            raise SpotError('order request binding mismatch')
        if r.order_type is OrderType.LIMIT and (raw.get('timeInForce')!='IOC' or decimal(raw['price'])!=r.limit_price):
            raise SpotError('limit binding mismatch')
        order_id=raw['orderId'];fills=[]
        if type(order_id) is not int or order_id < 0:raise SpotError('invalid order ID')
        for f in self.trades(r.symbol,order_id):
            if type(f.get('id')) is not int or f['id'] < 0:raise SpotError('invalid trade ID')
            if f.get('symbol')!=r.symbol or f.get('orderId')!=order_id or f.get('isBuyer')!=(r.side.value=='BUY'):
                raise SpotError('fill identity mismatch')
            fills.append(ActualFill(str(f['id']),decimal(f['qty']),decimal(f['price']),decimal(f['quoteQty']),decimal(f['commission']),f['commissionAsset']))
        fills.sort(key=lambda f:int(f.trade_id))
        return ActualOrder(str(order_id),cid,raw['status'],decimal(raw['origQty']),decimal(raw['executedQty']),decimal(raw['cummulativeQuoteQty']),tuple(fills))
