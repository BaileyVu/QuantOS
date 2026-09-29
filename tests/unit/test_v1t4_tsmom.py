"""Daily feature, deterministic Alpha, calibration, and runtime recovery proofs."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.domain.features.daily import DailyFeatureState, TSMOM_FEATURE_VERSION
from quantos.domain.alpha.context import AlphaDecisionContext
from quantos.domain.alpha.tsmom import EntryEpisode, EdgeCalibration, TsmomAlpha, calibrate
from quantos.domain.alpha import AlphaAction
from quantos.domain.market_data import Candle, DatasetIdentity, validate_candle_sequence, MarketEvent
from quantos.domain.runtime_contracts import AccountSnapshot, Position, canonical, arithmetic
from quantos.application.tsmom import TsmomDecisionFunction
from quantos.application.paper_runtime import PaperRuntime, PaperRuntimePolicy, PaperRuntimeError
from quantos.application.evaluation import run_backtest
from quantos.domain.evaluation import BacktestConfig
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from quantos.infrastructure.storage.paper_runtime import LocalPaperRuntimeStore
from tests.unit.test_v1t2_evaluation import POLICY
from tests.unit.test_v1t3_runtime import Clock, Feed

UTC=timezone.utc
START=datetime(2025,12,10,tzinfo=UTC)


def minute(opened,price=D('100'),millisecond=False):
    end=opened+timedelta(minutes=1)
    return Candle('BTCUSDT','1m',opened,end-timedelta(milliseconds=int(millisecond)),
                  price,price,price,price,D(1),price,1)


def sequence(count,start=START):
    candles=tuple(minute(start+timedelta(minutes=i),D(100+i//1440)) for i in range(count))
    return validate_candle_sequence(DatasetIdentity('BTCUSDT','1m',start,candles[-1].open_time,
                                    'test-only','v1','t4-fixture'),candles)


def calibration(horizon=7):
    start=datetime(2020,1,1,tzinfo=UTC)
    episodes=tuple(EntryEpisode(start+timedelta(days=i*2),start+timedelta(days=i*2+1),D(100),D(110),D('.1')) for i in range(30))
    with localcontext(arithmetic()):
        edge=D('1.1')*(1-D('.005004'))-1
    return EdgeCalibration(horizon,start,start+timedelta(days=60),'test-only','a'*64,
                           episodes,D('.1'),D(0),D('.1'),D('.005004'),edge)


class DailyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=sequence(33*1440)
        state=DailyFeatureState('BTCUSDT'); cls.ends={}
        for i,c in enumerate(cls.data.candles):
            state=state.advance(c,decision_time=c.close_time)
            if (i+1)%1440==0:
                cls.ends[(i+1)//1440]=state

    def test_horizon_warmup_and_bounded_state(self):
        for h in (7,14,30):
            self.assertEqual(self.ends[h].feature().values[f'ready_{h}d'],0)
            self.assertEqual(self.ends[h+1].feature().values[f'ready_{h}d'],1)
            with localcontext(arithmetic()):
                expected=D(100+h)/100-1
            self.assertEqual(self.ends[h+1].feature().values[f'momentum_{h}d'],expected)
        self.assertEqual(len(self.ends[33].daily),31)
        self.assertLess(len(canonical(self.ends[33])),6000)

    def test_duplicate_gap_conflict_and_incomplete(self):
        state=self.ends[8]; last=state.last
        self.assertIs(state.advance(last,decision_time=last.close_time),state)
        for c in (replace(last,volume=D(2)),minute(last.open_time+timedelta(minutes=2))):
            with self.assertRaises(ValueError): state.advance(c,decision_time=c.close_time)
        nxt=minute(last.open_time+timedelta(minutes=1))
        with self.assertRaises(ValueError): state.advance(nxt,decision_time=nxt.open_time)
        with self.assertRaises(ValueError): state.advance(replace(nxt,open_time=nxt.open_time+timedelta(seconds=1)),decision_time=nxt.close_time)

    def test_restart_midday_midnight_serialization_and_no_lookahead(self):
        state=self.ends[8]
        for i in range(1441):
            candle=self.data.candles[8*1440+i]
            restored=DailyFeatureState.restore(json.loads(canonical(state)))
            direct=state.advance(candle,decision_time=candle.close_time)
            resumed=restored.advance(candle,decision_time=candle.close_time)
            self.assertEqual(canonical(direct),canonical(resumed))
            self.assertEqual(direct.feature(),resumed.feature())
            if i<1439:
                self.assertEqual(direct.daily,state.daily)
                self.assertEqual(direct.feature().values['decision_day_close'],0)
            state=direct

    def test_leap_month_year_boundaries_both_close_grids(self):
        for start in (datetime(2024,2,28,tzinfo=UTC),datetime(2025,12,31,tzinfo=UTC)):
            for milli in (False,True):
                state=DailyFeatureState('BTCUSDT')
                for i in range(3*1440):
                    c=minute(start+timedelta(minutes=i),millisecond=milli)
                    state=state.advance(c,decision_time=c.close_time)
                self.assertEqual([d.day for d in state.daily],[start+timedelta(days=i) for i in range(3)])

    def test_partial_initial_day_excluded_and_corrupt_state_rejected(self):
        state=DailyFeatureState('BTCUSDT')
        for i in range(1439):
            c=minute(START+timedelta(minutes=i+1))
            state=state.advance(c,decision_time=c.close_time)
        self.assertEqual(state.daily,())
        self.assertEqual(state.feature().values['decision_day_close'],0)
        raw=self.ends[8].evidence(); raw['minute_count']=1
        with self.assertRaises(ValueError): DailyFeatureState.restore(raw)

    def test_alpha_position_and_edge_semantics(self):
        alpha=TsmomAlpha(calibration()); feature=self.ends[8].feature()
        flat=AccountSnapshot(feature.timestamp,{'USDT':D(20),'BTC':D(0)},())
        decision=alpha.decide(feature,AlphaDecisionContext(feature.timestamp,flat))
        self.assertEqual(decision.action,AlphaAction.BUY)
        self.assertEqual(decision.model_version,'deterministic-tsmom-no-ml-v1')
        self.assertNotEqual(alpha.calibration.entry_edge,decision.model_score)
        owned=AccountSnapshot(feature.timestamp,{'USDT':D(10),'BTC':D('.1')},
                              (Position('BTCUSDT',D('.1'),D(100),feature.timestamp),))
        self.assertEqual(alpha.decide(feature,AlphaDecisionContext(feature.timestamp,owned)).action,AlphaAction.HOLD)
        vals=dict(feature.values); vals['momentum_7d']=D(0)
        cash=replace(feature,values=vals)
        self.assertEqual(alpha.decide(cash,AlphaDecisionContext(feature.timestamp,owned)).action,AlphaAction.SELL)
        self.assertEqual(alpha.decide(cash,AlphaDecisionContext(feature.timestamp,flat)).action,AlphaAction.HOLD)
        vals['decision_day_close']=D(0)
        self.assertEqual(alpha.decide(replace(feature,values=vals),AlphaDecisionContext(feature.timestamp,owned)).action,AlphaAction.HOLD)

    def test_training_censors_open_trades_rejects_future_and_missing_input(self):
        data=self.data.candles[:10*1440]
        result=calibrate(data,horizon=7,training_start=data[0].open_time,
                         training_end_exclusive=data[-1].open_time+timedelta(minutes=1),source_identity='fixture')
        self.assertEqual(result.episodes,())
        self.assertFalse(result.eligible)
        with self.assertRaises(ValueError):
            calibrate(data,horizon=7,training_start=data[0].open_time,
                      training_end_exclusive=data[-1].open_time,source_identity='fixture')
        with self.assertRaises(ValueError):
            replace(calibration(),entry_edge=D(1))


class RuntimeDailyTests(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_restart_duplicate_boundary_and_evaluator_parity(self):
        data=sequence(8*1440+2,start=datetime(2024,12,24,tzinfo=UTC))
        mixed=tuple(replace(c,close_time=c.close_time-timedelta(microseconds=1000 if c.open_time.year==2024 else 1)) for c in data.candles)
        data=validate_candle_sequence(replace(data.identity),mixed)
        boot_candles=data.candles[:-3]
        bootstrap=validate_candle_sequence(DatasetIdentity('BTCUSDT','1m',data.identity.start_time,boot_candles[-1].open_time,
                                                         'test-only','v1','t4-fixture'),boot_candles)
        clock=Clock(); clock.now=boot_candles[-1].close_time
        policy=PaperRuntimePolicy(('BTCUSDT',),D(20),POLICY,50,'test-tsmom',1000,feature_version=TSMOM_FEATURE_VERSION)
        alpha=TsmomDecisionFunction(TsmomAlpha(calibration()))
        with TemporaryDirectory() as temp:
            root=Path(temp)
            def runtime():
                return PaperRuntime(policy=policy,alpha=alpha,ledger=JsonlExecutionLedger(root/'orders'),
                    store=LocalPaperRuntimeStore(root/'state',root/'minutes',root/'orders'),clock=clock,bootstrap=(bootstrap,))
            first=runtime()
            await first.run(Feed([MarketEvent(data.candles[-3].close_time,data.candles[-3])],clock))
            second=runtime()
            await second.run(Feed([MarketEvent(c.close_time,c) for c in data.candles[-3:]],clock))
            self.assertEqual(second.evidence_count,3)
            self.assertEqual(len(second.ledger.read()),2) # initial + single economic entry
            report=run_backtest(datasets=(data,),initial_account=AccountSnapshot(data.identity.start_time,{'USDT':D(20),'BTC':D(0)},()),
                risk_policy=POLICY,alpha_decider=alpha,config=BacktestConfig('fixture','test-tsmom',
                decision_start=data.candles[-3].close_time,feature_version=TSMOM_FEATURE_VERSION),
                ledger=JsonlExecutionLedger(root/'backtest'))
            self.assertEqual(report.final_equity,D(second.last_equity['equity']))
            self.assertEqual(report.feature_version,TSMOM_FEATURE_VERSION)
            self.assertEqual([x.action for x in report.decisions],['BUY','HOLD','HOLD'])
            with self.assertRaises(PaperRuntimeError):
                await runtime().run(Feed([MarketEvent(data.candles[-1].open_time+timedelta(minutes=3),
                    minute(data.candles[-1].open_time+timedelta(minutes=2)))],clock))

class CalibrationSafetyTests(unittest.TestCase):
    def test_completed_entry_labels_and_roundtrip_cost_reserve(self):
        # Known synthetic episode: day 7 enters 120; day 8 exits 90 => -25% gross.
        raw=[]
        for i in range(9*1440):
            day=i//1440
            price=D(120) if day==7 else D(90) if day==8 else D(100)
            raw.append(minute(START+timedelta(minutes=i),price))
        result=calibrate(tuple(raw),horizon=7,training_start=START,
            training_end_exclusive=START+timedelta(days=9),source_identity='test-episode')
        self.assertEqual(len(result.episodes),1)
        self.assertEqual(result.episodes[0].gross_return,D('-.25'))
        self.assertEqual(result.mean,D('-.25'))
        self.assertFalse(result.eligible)
        with localcontext(arithmetic()):
            self.assertEqual(result.entry_edge,D('.75')*(1-D('.005004'))-1)
        # Calibration cannot cross the requested training interval.
        with self.assertRaisesRegex(ValueError,'outside training'):
            calibrate(tuple(raw),horizon=7,training_start=START,
                training_end_exclusive=START+timedelta(days=8),source_identity='test-episode')

    def test_warmup_no_ml_and_future_calibration_rejected(self):
        state=DailyFeatureState('BTCUSDT')
        c=minute(START)
        feature=state.advance(c,decision_time=c.close_time).feature()
        context=AlphaDecisionContext(feature.timestamp,AccountSnapshot(START,{'USDT':D(20)},()))
        alpha=TsmomAlpha(calibration())
        self.assertEqual(alpha.decide(feature,context).action,AlphaAction.HOLD)
        future=replace(feature,timestamp=datetime(2019,1,1,tzinfo=UTC))
        old_context=AlphaDecisionContext(future.timestamp,AccountSnapshot(future.timestamp,{'USDT':D(20)},()))
        with self.assertRaisesRegex(ValueError,'unavailable'):
            alpha.decide(future,old_context)
        with self.assertRaises(ValueError):
            replace(calibration(),horizon=8)

    def test_daily_clock_uses_minute_end_without_rewriting_candle(self):
        state=DailyFeatureState('BTCUSDT')
        for delta in (1000,1,0):
            opened=START if state.last is None else state.last.open_time+timedelta(minutes=1)
            c=replace(minute(opened),close_time=opened+timedelta(minutes=1)-timedelta(microseconds=delta))
            state=state.advance(c,decision_time=c.close_time)
            self.assertEqual(state.last,c)
            self.assertEqual(state.feature().timestamp,opened+timedelta(minutes=1))

    def test_evidence_publish_never_overwrites(self):
        from quantos.interfaces.tsmom_screen import publish
        with TemporaryDirectory() as temp:
            path=Path(temp)/'immutable.json'
            publish(path,{'status':'test-only'})
            publish(path,{'status':'test-only'})
            with self.assertRaisesRegex(ValueError,'collision'):
                publish(path,{'status':'PASS'})

class AcquisitionTests(unittest.TestCase):
    def test_existing_archive_pipeline_publishes_continuous_successor_and_reuses_verified_raw(self):
        from quantos.interfaces.tsmom_data import acquire
        from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore
        from tests.unit.test_binance_daily_archive import archive_row,csv_payload,valid_zip,checksum_payload
        from unittest.mock import Mock
        start=datetime(2025,1,1,tzinfo=UTC)
        base=sequence(1440,start=start)
        base=validate_candle_sequence(replace(base.identity,source='binance-spot-daily-archive'),base.candles)
        archive_date=(start+timedelta(days=1)).date()
        payload=valid_zip(archive_date=archive_date,csv_bytes=csv_payload([archive_row(archive_date,minute=i) for i in range(1440)]))
        checksum=checksum_payload(payload,archive_date=archive_date)
        transport=Mock(side_effect=lambda url,timeout: checksum if url.endswith('.CHECKSUM') else payload)
        with TemporaryDirectory() as temp:
            root=Path(temp); store=ParquetCandleDatasetStore(root)
            source=store.write(base); original=source.read_bytes()
            output=acquire(base_path=source,root=root,end_exclusive=start+timedelta(days=2),
                           ingestion_version='t4-test',http_get=transport)
            self.assertEqual(len(store.read(output).candles),2880)
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(transport.call_count,2)
            again=acquire(base_path=source,root=root,end_exclusive=start+timedelta(days=2),
                          ingestion_version='t4-test',http_get=Mock(side_effect=AssertionError('network forbidden')))
            self.assertEqual(again,output)
            raw=next((root/'t4'/'ingestion'/'raw').glob('*.zip'))
            raw.write_bytes(b'corrupt test-only cache')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                acquire(base_path=source,root=root,end_exclusive=start+timedelta(days=2),
                        ingestion_version='t4-test',http_get=Mock(side_effect=AssertionError('network forbidden')))
