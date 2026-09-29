"""T4R acceptance: causal breakout, real fills, durable unknown recovery and transport safety."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit,parse_qs
import hashlib,hmac,json,unittest

from quantos.application.risk_execution import build_order_intent,TradingStep
from quantos.domain.alpha import AlphaAction,AlphaDecision
from quantos.domain.alpha.context import AlphaDecisionContext
from quantos.domain.alpha.trb import TrbAlpha
from quantos.domain.features.daily import DailyClose,DailyFeatureState,TRB_FEATURE_VERSION,completed_daily_closes
from quantos.domain.execution.exchange import ActualFill,ActualOrder,ExchangeExecutionEngine,apply_actual
from quantos.domain.execution.core import ExecutionError
from quantos.domain.execution.contracts import OrderType
from quantos.domain.risk.engine import RiskContext,RiskEngine,MarketState
from quantos.infrastructure.binance.spot_rest import SpotRest,SpotError,Credentials,client_id
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from quantos.application.trb import calibrate_trb
from tests.unit.test_v1t2_evaluation import POLICY,COSTS,account,START
from tests.unit.test_v1t4_tsmom import minute,sequence


def intent(before=None,action=AlphaAction.BUY,edge=D('.05'),order_type=OrderType.MARKET,limit_price=None):
    before=before or account();t=before.timestamp+timedelta(minutes=1)
    c=minute(t-timedelta(minutes=1));alpha=AlphaDecision(t,'BTCUSDT','testnet-synthetic-v1','no-model','test-fixture',action,'operator test only','test',D(0))
    ctx=RiskContext(t,(MarketState(c,True),),before,edge,t.replace(hour=0,minute=0,second=0,microsecond=0),D(20),D(20))
    r=RiskEngine(POLICY).evaluate(alpha,ctx)
    return build_order_intent(alpha,r,ctx,POLICY,order_type=order_type,limit_price=limit_price)


class Port:
    testnet=True;scope='test-scope';client_id=staticmethod(client_id)
    def __init__(self):
        self.calls=0;self.orders={};self.initial=account();self.current=self.initial
        self.timeout=False;self.unknown=False;self.partial=False;self.commission_asset='USDT';self.foreign=False
    def open_orders(self):
        result=[{'clientOrderId':c} for c,o in self.orders.items() if o.status not in ('FILLED','CANCELED')]
        return result+([{'clientOrderId':'foreign'}] if self.foreign else [])
    def balances(self):return {k:(v,D(0)) for k,v in self.current.balances.items()}
    def validate_filters(self,i):i.validate()
    def test_order(self,i):i.validate()
    def new_order(self,i):
        self.calls+=1
        if not self.unknown:
            qty=i.request.quantity/(2 if self.partial else 1)
            f=ActualFill('1',qty,D(100),qty*100,D('.001'),self.commission_asset)
            o=ActualOrder('123',client_id(i.request.request_id),'PARTIALLY_FILLED' if self.partial else 'FILLED',i.request.quantity,qty,qty*100,(f,))
            self.orders[o.client_id]=o
            if self.commission_asset in ('BTC','USDT'):self.current=apply_actual(self.current,i,o)
        if self.timeout or self.unknown:raise TimeoutError('do not trust timeout')
    def observe(self,i,cid):
        if cid not in self.orders:raise SpotError('not found',-2013)
        return self.orders[cid]


class ExchangeTests(unittest.TestCase):
    def setUp(self):self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.ledger=JsonlExecutionLedger(Path(self.tmp.name)/'orders.jsonl');self.port=Port()
    def engine(self):return ExchangeExecutionEngine(account(),COSTS,self.ledger,self.port)
    def test_timeout_after_fill_query_no_resubmit_and_restart(self):
        e=self.engine();i=intent();self.port.timeout=True
        order=e.submit(i);self.assertEqual(order.status,'FILLED');self.assertEqual(self.port.calls,1)
        restored=self.engine();self.assertEqual(restored.snapshot,e.snapshot)
        self.assertEqual(restored.submit(i),order);self.assertEqual(self.port.calls,1)
    def test_unknown_notfound_never_retries(self):
        e=self.engine();self.port.unknown=True
        with self.assertRaises(SpotError):e.submit(intent())
        with self.assertRaises(SpotError):self.engine()
        self.assertEqual(self.port.calls,1)
        self.assertIn('prepared',self.ledger.read()[1]);self.assertIn('unknown',self.ledger.read()[2])
    def test_partial_recovery_multiple_fills_and_fee_asset(self):
        e=self.engine();i=intent();self.port.partial=True
        with self.assertRaises(ExecutionError):e.submit(i)
        partial=self.port.orders[client_id(i.request.request_id)]
        second=ActualFill('2',i.request.quantity/2,D(101),i.request.quantity/2*101,D('.000001'),'BTC')
        filled=replace(partial,status='FILLED',filled_quantity=i.request.quantity,quote_quantity=partial.quote_quantity+second.quote_quantity,fills=partial.fills+(second,))
        self.port.orders[partial.client_id]=filled;self.port.current=apply_actual(account(),i,filled)
        restored=self.engine();self.assertEqual(restored.snapshot,self.port.current)
        self.assertEqual(restored.snapshot.balances['BTC'],i.request.quantity-D('.000001'))
        self.assertEqual(filled.average_price,D('100.5'));self.assertEqual(self.port.calls,1)
    def test_third_asset_fails_closed(self):
        e=self.engine();self.port.commission_asset='BNB'
        with self.assertRaisesRegex(ExecutionError,'third-asset'):e.submit(intent())
        with self.assertRaises(ExecutionError):e.submit(intent())
        self.assertEqual(self.port.calls,1)
    def test_foreign_order_or_balance_blocks(self):
        e=self.engine();self.port.foreign=True
        with self.assertRaises(ExecutionError):e.submit(intent())
        self.assertEqual(self.port.calls,0)
    def test_prepare_persistence_failure_prevents_post(self):
        e=self.engine();self.ledger.append=lambda *a: (_ for _ in ()).throw(OSError('disk'))
        with self.assertRaises(OSError):e.submit(intent())
        self.assertEqual(self.port.calls,0)
    def test_crash_after_remote_fill_before_observation(self):
        e=self.engine();original=self.port.new_order
        def crash(i):original(i);raise KeyboardInterrupt()
        self.port.new_order=crash
        with self.assertRaises(KeyboardInterrupt):e.submit(intent())
        self.assertEqual(len(self.ledger.read()),2)
        restored=self.engine();self.assertFalse(restored.pending);self.assertEqual(self.port.calls,1)
    def test_corrupt_fill_totals_rejected(self):
        with self.assertRaises(ExecutionError):ActualOrder('1','cid','FILLED',D(1),D(1),D(100),())
    def test_mainnet_port_never_starts(self):
        self.port.testnet=False
        with self.assertRaises(ExecutionError):self.engine()
        self.assertEqual(self.port.calls,0)
    def test_risk_rejection_cannot_build_intent(self):
        with self.assertRaises(ValueError):intent(edge=D('-1'))
        self.assertEqual(self.port.calls,0)


class RestTests(unittest.TestCase):
    def test_sign_exact_encoded_bytes_and_redact_credentials(self):
        calls=[]
        def http(method,url,headers,body,timeout):
            calls.append((url,headers,body))
            return 200,{},b'{"serverTime":1000000}' if '/time' in url else b'{"canTrade":true,"canWithdraw":false,"balances":[]}'
        c=Credentials('fixture-key','fixture-secret');r=SpotRest(c,http=http,clock=lambda:1000)
        r.account();query=urlsplit(calls[-1][0]).query;payload,sig=query.rsplit('&signature=',1)
        self.assertEqual(hmac.new(c.secret.encode(),payload.encode(),hashlib.sha256).hexdigest(),sig)
        self.assertEqual(parse_qs(payload)['timestamp'],['1000000']);self.assertNotIn(c.secret,repr(c));self.assertNotIn(c.key,repr(c))
    def test_mainnet_new_and_test_orders_block_before_transport(self):
        calls=[];r=SpotRest(Credentials('x','y'),testnet=False,http=lambda *a:calls.append(a))
        for fn in (r.new_order,r.test_order):
            with self.assertRaises(SpotError):fn(intent())
        self.assertEqual(calls,[])
    def test_error_does_not_leak_signed_url(self):
        def http(*a):raise TimeoutError('secret signature full URL')
        r=SpotRest(Credentials('x','y'),http=http,clock=lambda:1);r.synced_at=1;r.offset_ms=0
        with self.assertRaises(SpotError) as e:r.new_order(intent())
        self.assertEqual(e.exception.category,'UNKNOWN');self.assertNotIn('signature',str(e.exception))
    def test_429_backoff_and_client_id(self):
        r=SpotRest(http=lambda *a:(429,{'Retry-After':'60'},b'{"code":-1003}'),clock=lambda:100)
        with self.assertRaises(SpotError):r.exchange_info()
        self.assertEqual(r.blocked_until,160)
        self.assertEqual(client_id('request'),client_id('request'));self.assertEqual(len(client_id('request')),36)
    def test_fill_trade_truncation_blocked(self):
        r=SpotRest();r._call=lambda *a,**k:[{}]*1000
        with self.assertRaises(SpotError):r.trades('BTCUSDT',1)


class TrbTests(unittest.TestCase):
    def state(self,current=D(400)):
        start=START-timedelta(days=201)
        daily=tuple(DailyClose(start+timedelta(days=i),D(100+i)) for i in range(200))+(DailyClose(START-timedelta(days=1),current),)
        return DailyFeatureState('BTCUSDT',minute(START-timedelta(minutes=1),current),1440,daily,201*1440,'a'*64,TRB_FEATURE_VERSION)
    def test_current_close_excluded_and_no_rebuy(self):
        f=self.state().feature();self.assertEqual(f.values['resistance_200d'],D(299))
        alpha=TrbAlpha(200,'b'*64,START,True)
        self.assertEqual(alpha.decide(f,AlphaDecisionContext(START,account())).action,AlphaAction.BUY)
        from quantos.domain.runtime_contracts import AccountSnapshot,Position
        held=AccountSnapshot(START,{'USDT':D(10),'BTC':D('.1')},(Position('BTCUSDT',D('.1'),D(100),START),))
        self.assertEqual(alpha.decide(f,AlphaDecisionContext(START,held)).action,AlphaAction.HOLD)
        down=self.state(D(50)).feature()
        self.assertEqual(alpha.decide(down,AlphaDecisionContext(START,held)).action,AlphaAction.SELL)
        self.assertEqual(alpha.decide(down,AlphaDecisionContext(START,account())).action,AlphaAction.HOLD)
    def test_equality_nondecision_and_restore(self):
        state=self.state(D(299));alpha=TrbAlpha(200,'b'*64,START,True)
        self.assertEqual(alpha.decide(state.feature(),AlphaDecisionContext(START,account())).action,AlphaAction.HOLD)
        self.assertEqual(DailyFeatureState.restore(state.evidence()),state)
        nextstate=state.advance(minute(START,D(500)),decision_time=START+timedelta(minutes=1))
        self.assertEqual(alpha.decide(nextstate.feature(),AlphaDecisionContext(START+timedelta(minutes=1),account())).action,AlphaAction.HOLD)
    def test_batch_daily_matches_stream_and_partial_day(self):
        data=sequence(3*1440,start=START+timedelta(hours=3));state=DailyFeatureState('BTCUSDT',version=TRB_FEATURE_VERSION)
        for c in data.candles:state=state.advance(c,decision_time=c.close_time)
        self.assertEqual(completed_daily_closes(data),state.daily);self.assertEqual(len(state.daily),2)
    def test_calibration_censor_minimum_and_lookahead(self):
        daily=tuple(DailyClose(START+timedelta(days=i),D(100+i)) for i in range(220));end=START+timedelta(days=220)
        a=calibrate_trb(daily,horizon=50,source_identity='fixture',end_exclusive=end)
        self.assertFalse(a.eligible);self.assertEqual(a.episodes,());self.assertIsNotNone(a.censored_entry);self.assertIsNone(a.entry_edge)
        with self.assertRaises(ValueError):calibrate_trb(daily,horizon=50,source_identity='fixture',end_exclusive=START)
    def test_backward_extension_conflicting_overlap(self):
        from quantos.application.historical_ingestion import prepend_and_persist_historical_range
        data=sequence(5,start=START);older=minute(START-timedelta(minutes=1))
        class Fetch:
            def fetch_open_time_range(self,**kw):return (older,replace(data.candles[0],volume=D(999)))
        class Writer:
            def write(self,s):raise AssertionError('must not publish conflict')
        with self.assertRaisesRegex(ValueError,'overlap conflicts'):
            prepend_and_persist_historical_range(data,Fetch(),Writer(),start_open_time=older.open_time,ingestion_version='new',overlap_minutes=1)


class HarnessIntegrationTests(unittest.TestCase):
    def test_real_adapter_harness_signed_risk_buy_sell_restart(self):
        from quantos.interfaces.testnet_execution import run_harness
        now=START+timedelta(minutes=2)
        key='fixture-key';secret='fixture-secret';remote={'USDT':D(10020),'BTC':D(2),'ETH':D(0)};orders={};fills={};post_count=[0]
        symbol={'symbol':'BTCUSDT','status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['MARKET','LIMIT'],
            'filters':[{'filterType':'LOT_SIZE','stepSize':'.001','minQty':'.001','maxQty':'100'},
                {'filterType':'PRICE_FILTER','tickSize':'.01','minPrice':'.01','maxPrice':'1000000'},
                {'filterType':'NOTIONAL','minNotional':'5','maxNotional':'1000000'}]}
        def http(method,url,headers,body,timeout):
            self.assertTrue(url.startswith('https://testnet.binance.vision/'))
            split=urlsplit(url);path=split.path;payload=body.decode() if body else split.query
            signed=path not in ('/api/v3/time','/api/v3/exchangeInfo','/api/v3/klines')
            if signed:
                unsigned,sig=payload.rsplit('&signature=',1)
                self.assertEqual(sig,hmac.new(secret.encode(),unsigned.encode(),hashlib.sha256).hexdigest())
                self.assertEqual(headers['X-MBX-APIKEY'],key)
            q={k:v[0] for k,v in parse_qs(payload).items()}
            if path.endswith('/time'):data={'serverTime':int(now.timestamp()*1000)}
            elif path.endswith('/exchangeInfo'):data={'symbols':[symbol]}
            elif path.endswith('/account'):data={'canTrade':True,'canWithdraw':True,'balances':[{'asset':k,'free':str(v),'locked':'0'} for k,v in remote.items()]}
            elif path.endswith('/openOrders'):data=[]
            elif path.endswith('/klines'):
                opened=now-timedelta(minutes=1);ms=int(opened.timestamp()*1000)
                data=[[ms,'100','100','100','100','1',ms+59999,'100',1,'0','0','0']]
            elif path.endswith('/order/test'):data={}
            elif path.endswith('/order') and method=='POST':
                post_count[0]+=1;qty=D(q['quantity']);cid=q['newClientOrderId'];oid=post_count[0]
                data={'symbol':'BTCUSDT','clientOrderId':cid,'orderId':oid,'status':'FILLED','origQty':str(qty),'executedQty':str(qty),'cummulativeQuoteQty':str(qty*100),'side':q['side'],'type':q['type'],'timeInForce':q.get('timeInForce'),'price':q.get('price')}
                orders[cid]=data;commission=qty*100*D('.001')
                fills[oid]=[{'symbol':'BTCUSDT','orderId':oid,'id':oid,'qty':str(qty),'price':'100','quoteQty':str(qty*100),'commission':str(commission),'commissionAsset':'USDT','isBuyer':q['side']=='BUY'}]
                if q['side']=='BUY':remote['USDT']-=qty*100+commission;remote['BTC']+=qty
                else:remote['USDT']+=qty*100-commission;remote['BTC']-=qty
            elif path.endswith('/order'):data=orders[q['origClientOrderId']]
            elif path.endswith('/myTrades'):data=fills[int(q['orderId'])]
            else:raise AssertionError(path)
            return 200,{},json.dumps(data).encode()
        with TemporaryDirectory() as tmp:
            def port():return SpotRest(Credentials(key,secret),http=http,clock=lambda:now.timestamp())
            state=Path(tmp)/'allocation'
            first=run_harness(port=port(),state_dir=state,action='buy',approved=True,clock=lambda:now)
            self.assertFalse(first['mainnet_enabled']);self.assertTrue(first['snapshot'].positions)
            # New process-equivalent engine loads durable state, never repeats BUY.
            run_harness(port=port(),state_dir=state,action='recover',clock=lambda:now)
            now+=timedelta(minutes=1)
            last=run_harness(port=port(),state_dir=state,action='sell',approved=True,clock=lambda:now)
            self.assertFalse(last['snapshot'].positions);self.assertEqual(post_count[0],2)
            self.assertEqual(last['snapshot'].balances['USDT'],D('19.980200'))
            raw=(state/'execution.jsonl').read_text();self.assertNotIn(secret,raw);self.assertNotIn(key,raw);self.assertNotIn('signature',raw)
            with self.assertRaises(ValueError):run_harness(port=port(),state_dir=state,action='buy',approved=True,clock=lambda:now)

    def test_cancel_partial_preserves_fills_and_no_resubmit(self):
        with TemporaryDirectory() as tmp:
            p=Port();ledger=JsonlExecutionLedger(Path(tmp)/'ledger');e=ExchangeExecutionEngine(account(),COSTS,ledger,p)
            p.partial=True
            with self.assertRaises(ExecutionError):e.submit(intent())
            def cancel(symbol,cid):p.orders[cid]=replace(p.orders[cid],status='CANCELED')
            p.cancel_order=cancel
            recovery=ExchangeExecutionEngine(account(),COSTS,ledger,p,recover_pending=True)
            after=recovery.cancel_pending();self.assertFalse(recovery.pending)
            self.assertEqual(after.balances['BTC'],intent().request.quantity/2);self.assertEqual(p.calls,1)

    def test_quantity_filter_rejects_without_rounding_or_post(self):
        i=intent();r=SpotRest(clock=lambda:i.request.timestamp.timestamp());r.sync_time=lambda:None;r.offset_ms=0
        r.exchange_info=lambda s:{'symbols':[{'symbol':s,'status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['MARKET'],
            'filters':[{'filterType':'LOT_SIZE','stepSize':'.03','minQty':'.03','maxQty':'1'},
                {'filterType':'PRICE_FILTER','tickSize':'.01','minPrice':'.01','maxPrice':'100000'},
                {'filterType':'NOTIONAL','minNotional':'5','maxNotional':'100'}]}]}
        with self.assertRaisesRegex(SpotError,'no upward rounding'):r.validate_filters(i)

class RecoveryAuditTests(unittest.TestCase):
    def test_changed_account_scope_and_torn_ledger_block(self):
        with TemporaryDirectory() as tmp:
            ledger=JsonlExecutionLedger(Path(tmp)/'ledger');p=Port();e=ExchangeExecutionEngine(account(),COSTS,ledger,p)
            p.scope='different-account'
            with self.assertRaisesRegex(ExecutionError,'scope changed'):e.submit(intent())
            self.assertEqual(p.calls,0)
            with ledger.path.open('ab') as stream:stream.write(b'{')
            with self.assertRaises(ExecutionError):ExchangeExecutionEngine(account(),COSTS,ledger,Port())
    def test_concurrent_engines_one_economic_order(self):
        with TemporaryDirectory() as tmp:
            ledger=JsonlExecutionLedger(Path(tmp)/'ledger');p=Port()
            a=ExchangeExecutionEngine(account(),COSTS,ledger,p);b=ExchangeExecutionEngine(account(),COSTS,ledger,p)
            a.submit(intent())
            with self.assertRaisesRegex(ExecutionError,'concurrent'):b.submit(intent())
            self.assertEqual(p.calls,1)
    def test_exchange_balance_drift_blocks_before_test_order(self):
        with TemporaryDirectory() as tmp:
            ledger=JsonlExecutionLedger(Path(tmp)/'ledger');p=Port();e=ExchangeExecutionEngine(account(),COSTS,ledger,p)
            p.current=replace(p.current,balances={'USDT':D(19),'BTC':D(0),'ETH':D(0)})
            with self.assertRaisesRegex(ExecutionError,'balance'):e.submit(intent())
            self.assertEqual(p.calls,0)
    def test_market_and_notional_filter_limits(self):
        i=intent();r=SpotRest(clock=lambda:i.request.timestamp.timestamp());r.sync_time=lambda:None;r.offset_ms=0
        info={'symbols':[{'symbol':'BTCUSDT','status':'TRADING','isSpotTradingAllowed':True,'orderTypes':['MARKET'],
            'filters':[{'filterType':'LOT_SIZE','stepSize':'.001','minQty':'.001','maxQty':'1'},
                {'filterType':'MARKET_LOT_SIZE','stepSize':'.001','minQty':'.1','maxQty':'1'},
                {'filterType':'PRICE_FILTER','tickSize':'.01','minPrice':'.01','maxPrice':'100000'},
                {'filterType':'NOTIONAL','minNotional':'5','maxNotional':'100'}]}]}
        r.exchange_info=lambda s:info
        with self.assertRaisesRegex(SpotError,'quantity filter'):r.validate_filters(i)
        info['symbols'][0]['filters'][1]['minQty']='.001'
        info['symbols'][0]['filters'][-1]['minNotional']='10'
        with self.assertRaisesRegex(SpotError,'do not increase'):r.validate_filters(i)
    def test_trb_all_horizons_warmup_and_no_leak(self):
        base=TrbTests().state();last=base.daily[-1]
        for h in (50,150,200):
            # h prior observations plus current close is the exact readiness boundary.
            f=replace(base,daily=base.daily[-h:]).feature()
            self.assertEqual(f.values[f'ready_{h}d'],0)
            f=replace(base,daily=base.daily[-h-1:]).feature()
            self.assertEqual(f.values[f'ready_{h}d'],1)
            self.assertEqual(f.values[f'resistance_{h}d'],D(299))
            changed=replace(base,daily=base.daily[:-1]+(replace(last,close=D(999)),),last=replace(base.last,open=D(999),high=D(999),low=D(999),close=D(999))).feature()
            self.assertEqual(changed.values[f'resistance_{h}d'],f.values[f'resistance_{h}d'])
    def test_backwards_extension_success_immutable_readback(self):
        from quantos.application.historical_ingestion import prepend_and_persist_historical_range
        from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore
        base=sequence(3,start=START);older=minute(START-timedelta(minutes=1))
        class Fetch:
            def fetch_open_time_range(self,**kw):return (older,base.candles[0])
        with TemporaryDirectory() as tmp:
            store=ParquetCandleDatasetStore(tmp)
            result=prepend_and_persist_historical_range(base,Fetch(),store,start_open_time=older.open_time,ingestion_version='new',overlap_minutes=1)
            self.assertEqual(store.read(result.path).candles,(older,)+base.candles)
            second=prepend_and_persist_historical_range(base,Fetch(),store,start_open_time=older.open_time,ingestion_version='new',overlap_minutes=1)
            self.assertEqual(second.path,result.path)


class FinalSafetyTests(unittest.TestCase):
    def test_actual_fill_exceeding_approved_limit_blocked(self):
        i=intent(order_type=OrderType.LIMIT,limit_price=D(100))
        f=ActualFill('1',i.request.quantity,D(101),i.request.quantity*101,D(0),'USDT')
        o=ActualOrder('1',client_id(i.request.request_id),'FILLED',f.quantity,f.quantity,f.quote_quantity,(f,))
        with self.assertRaisesRegex(ExecutionError,'approved limit'):apply_actual(account(),i,o)
    def test_mainnet_host_mutation_is_not_an_activation_route(self):
        from quantos.infrastructure.binance.spot_rest import MAINNET
        p=SpotRest(Credentials('fixture','fixture'));p.base=MAINNET
        with self.assertRaisesRegex(SpotError,'Mainnet'):p.new_order(intent())
    def test_batch_cannot_accept_candles_runtime_rejects(self):
        from quantos.domain.market_data import validate_candle_sequence
        data=sequence(2,start=START)
        malformed=(replace(data.candles[0],close_time=START+timedelta(seconds=10)),data.candles[1])
        # Generic candle sequence validation permits provider precision; daily
        # aggregation still enforces the same strict minute boundary as runtime.
        data=validate_candle_sequence(replace(data.identity),malformed)
        with self.assertRaisesRegex(ValueError,'daily-feature'):completed_daily_closes(data)


class TrbRuntimeParityTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_warmup_bootstrap_paper_restart_and_historical_parity(self):
        from quantos.domain.market_data import DatasetIdentity,validate_candle_sequence,MarketEvent
        from quantos.domain.evaluation import AlphaEvaluation,BacktestConfig
        from quantos.application.evaluation import run_backtest
        from quantos.application.paper_runtime import PaperRuntime,PaperRuntimePolicy
        from quantos.infrastructure.storage.paper_runtime import LocalPaperRuntimeStore
        from tests.unit.test_v1t3_runtime import Clock,Feed
        data=sequence(51*1440+2,start=datetime(2024,1,1,tzinfo=timezone.utc))
        boot=data.candles[:-3]
        bootstrap=validate_candle_sequence(DatasetIdentity('BTCUSDT','1m',data.identity.start_time,boot[-1].open_time,'test-only','v1','trb-fixture'),boot)
        # Synthetic fixture bypasses no actual calibration: it is not a market run
        # or a production decision function and never enters an evidence directory.
        alpha=TrbAlpha(50,'a'*64,data.identity.start_time,True)
        def fixture(feature,context):return AlphaEvaluation(alpha.decide(feature,context),D('.05'))
        clock=Clock();clock.now=boot[-1].close_time
        policy=PaperRuntimePolicy(('BTCUSDT',),D(20),POLICY,50,'trb-test-fixture',1000,feature_version=TRB_FEATURE_VERSION)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            def runtime():
                return PaperRuntime(policy=policy,alpha=fixture,ledger=JsonlExecutionLedger(root/'orders'),
                    store=LocalPaperRuntimeStore(root/'state',root/'minutes',root/'orders'),clock=clock,bootstrap=(bootstrap,))
            one=runtime();await one.run(Feed([MarketEvent(data.candles[-3].close_time,data.candles[-3])],clock))
            two=runtime();await two.run(Feed([MarketEvent(c.close_time,c) for c in data.candles[-3:]],clock))
            self.assertEqual(two.evidence_count,3);self.assertEqual(len(two.ledger.read()),2)
            report=run_backtest(datasets=(data,),initial_account=account(data.identity.start_time),risk_policy=POLICY,
                alpha_decider=fixture,config=BacktestConfig('fixture','trb-test-fixture',decision_start=data.candles[-3].close_time,
                    feature_version=TRB_FEATURE_VERSION),ledger=JsonlExecutionLedger(root/'backtest'))
            self.assertEqual(report.final_equity,D(two.last_equity['equity']))
            self.assertEqual([d.action for d in report.decisions],['BUY','HOLD','HOLD'])


class ActualCostTests(unittest.TestCase):
    def test_actual_commission_assets_and_signed_slippage_evidence(self):
        from quantos.domain.execution.exchange import actual_cost_evidence
        i=intent();q=i.request.quantity
        f=ActualFill('1',q,D(101),q*101,D('.00001'),'BTC')
        order=ActualOrder('1',client_id(i.request.request_id),'FILLED',q,q,q*101,(f,))
        values=actual_cost_evidence(i,order)
        self.assertEqual(values['commission_by_asset'],{'BTC':D('.00001')})
        self.assertEqual(values['signed_slippage_quote'],q)
        self.assertEqual(values['average_price'],D(101))

if __name__=='__main__':unittest.main()
