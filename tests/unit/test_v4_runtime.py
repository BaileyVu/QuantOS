from dataclasses import asdict, replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from quantos.paper.adaptive import AdaptiveSizer
from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.regime import regime_tags
from quantos.paper.replay import snapshot as decode_snapshot
from quantos.paper.risk import Policy
from quantos.paper.runtime import arbitrate, dispatch
from quantos.paper.types import D, DelistEvent, Snapshot
from tests.unit.test_v3_engine import INS, T, quote, signal
from tests.unit.test_v4_strategies import snapshot


class RuntimeIntegrationTests(unittest.TestCase):
    def test_pA_chooses_higher_edge_and_p1_keeps_deterministic_first(self):
        with tempfile.TemporaryDirectory() as folder:
            p1=Journal(Path(folder)/'p1.db','p1','synthetic')
            pa=Journal(Path(folder)/'pa.db','pA','synthetic')
            try:
                adaptive=AdaptiveSizer(pa,p1,priors={'a':{'prior_mu':.1},'b':{'prior_mu':1.0}})
                engines=[PaperEngine(p1,Policy()),PaperEngine(pa,Policy(risk_fraction=D('.05')),adaptive=adaptive)]
                for e in engines: e.recover(T); e.connection(True,T)
                signals=[signal(strategy_id=k,signal_id=k) for k in ('a','b')]
                strategies=[SimpleNamespace(strategy_id=s.strategy_id,evaluate=lambda _,s=s:s) for s in signals]
                snap=Snapshot(T,INS,quote(),(),())
                dispatch(engines,'scan',[snap],T,strategies=strategies)
                self.assertEqual(p1.orders()[0]['signal']['strategy_id'],'a')
                self.assertEqual(pa.orders()[0]['signal']['strategy_id'],'b')
                self.assertEqual(pa.orders()[0]['signal']['trend_regime'],'UNKNOWN')
            finally: pa.close(); p1.close()

    def test_pA_ties_shorter_hold_then_id_and_opposing_conflicts_remain(self):
        edge=SimpleNamespace(mu=lambda:1,m2=lambda:1)
        adaptive=SimpleNamespace(edge=lambda _:edge)
        signals=[signal(strategy_id='b',maximum_hold_seconds=100),signal(strategy_id='a',maximum_hold_seconds=200)]
        self.assertEqual(arbitrate(signals,adaptive)[0][0].strategy_id,'b')
        signals[1]=replace(signals[1],maximum_hold_seconds=100)
        self.assertEqual(arbitrate(signals,adaptive)[0][0].strategy_id,'a')
        signals[1]=replace(signals[1],side='SHORT')
        self.assertEqual(arbitrate(signals,adaptive),([],{'BTCUSDC'}))

    def test_regime_tags_are_closed_only_and_not_required(self):
        snap=snapshot('1d',[str(100+i) for i in range(110)])
        tags=regime_tags(snap)
        self.assertEqual(tags['trend_regime'],'UP')
        self.assertTrue(D(0)<=D(tags['vol_regime'])<=100)
        future=replace(snap,at_ms=snap.candles_1d[-1].close_ms-1)
        self.assertEqual(regime_tags(future),{'trend_regime':'UNKNOWN','vol_regime':'UNKNOWN'})

    def test_capture_decodes_extended_bars_events_and_old_snapshot(self):
        snap=snapshot('4h',[str(100+i) for i in range(181)])
        event=DelistEvent('BTCUSDC',T-1000,T,'code','Binance Will Delist BTC','https://www.binance.com/en/support/announcement/code')
        snap=replace(snap,delist_events=(event,),recent_trade_ms=T,recent_trade_volume=D(2))
        self.assertEqual(decode_snapshot(asdict(snap)),snap)
        old=asdict(Snapshot(T,INS,quote(),(),()))
        for key in ('candles_1h','candles_4h','candles_1d','delist_events','recent_trade_ms','recent_trade_volume'):
            old.pop(key)
        self.assertEqual(decode_snapshot(old).candles_4h,())
