from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Account, Policy, evaluate
from quantos.paper.types import Candle, D, Snapshot
from tests.unit.test_v3_engine import INS, T, quote, signal


def trailing(**kwargs):
    return signal(strategy_id='tsmom_4h', strategy_version='1.0.0', entry_type='MARKET',
                  take_profit=None, maximum_hold_seconds=259200, **kwargs)


class ManifestRiskTests(unittest.TestCase):
    def decide(self, s):
        return evaluate(s, INS, quote(), Account(D(200),D(200),D(200)), Policy(), T)

    def test_only_registered_version_and_four_hour_strategy_can_hold_72h(self):
        self.assertTrue(self.decide(trailing()).approved)
        for s in (signal(maximum_hold_seconds=259200), replace(trailing(),strategy_version='unknown'),
                  replace(trailing(), strategy_id='delist_short_e17', side='SHORT', stop_loss=D(125))):
            self.assertEqual(self.decide(s).reason, 'INVALID_HOLD')
        self.assertEqual(self.decide(replace(trailing(), maximum_hold_seconds=259201)).reason,'INVALID_HOLD')

    def test_optional_target_requires_trusted_exit_manifest(self):
        self.assertEqual(self.decide(signal(take_profit=None)).reason,'INVALID_BRACKET')
        self.assertEqual(self.decide(replace(trailing(),take_profit=D(110))).reason,'INVALID_BRACKET')
        self.assertEqual(self.decide(replace(trailing(),entry_type='LIMIT')).reason,'INVALID_SIGNAL')
        self.assertEqual(self.decide(replace(trailing(),stop_loss=D(101))).reason,'INVALID_BRACKET')

    def test_no_target_cost_guard_boundary_and_fixed_target_rr(self):
        self.assertEqual(self.decide(trailing(stop_loss=D('98.32'))).reason,'NET_RR_NOT_APPLICABLE')
        self.assertEqual(self.decide(trailing(stop_loss=D('98.33'))).reason,'COSTS_TOO_HIGH_FOR_STOP')
        self.assertEqual(self.decide(signal(take_profit=D(101))).reason,'NET_REWARD_RISK')

    def test_other_risk_guards_remain(self):
        a=Account(D(200),D(200),D(200),paused=True)
        self.assertEqual(evaluate(trailing(),INS,quote(),a,Policy(),T).reason,'UNRECONCILED_OR_PAUSED')
        self.assertEqual(self.decide(trailing(stop_loss=D(1))).reason,'LIQUIDATION_BUFFER')


class MovingStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.j=Journal(Path(self.tmp.name)/'j.db','p1','synthetic'); self.addCleanup(self.j.close)
        self.e=PaperEngine(self.j,Policy()); self.e.recover(T); self.e.connection(True,T)

    def enter(self,s):
        order_id=self.e.propose(s,INS,quote(),T)
        self.assertIsNotNone(order_id)
        self.e.quote(quote(time=T+1000),T+1000)
        return self.j.orders()[0]

    def test_trailing_activates_at_one_r_and_never_loosens(self):
        o=self.enter(trailing()); self.assertGreater(D(o['filled']),0)
        self.assertFalse(o['target_active'])
        self.e.quote(quote('101',T+2000),T+2000)
        self.assertEqual(D(self.j.orders()[0]['stop_price']),D(98))
        self.e.quote(quote('110',T+3000),T+3000)
        stop=D(self.j.orders()[0]['stop_price']); self.assertGreater(stop,D(100))
        self.e.quote(quote('109',T+4000),T+4000)
        self.assertEqual(D(self.j.orders()[0]['stop_price']),stop)
        recovered=PaperEngine(self.j,Policy()); self.assertTrue(recovered.recover(T+4500))
        self.assertEqual(D(self.j.orders()[0]['stop_price']),stop)
        recovered.connection(True,T+4500)
        recovered.quote(quote('103',T+5000),T+5000)
        recovered.quote(quote('103',T+6000),T+6000)
        self.assertEqual(self.j.orders()[0]['state'],'CLOSED')
        self.assertEqual(self.j.orders()[0]['close_reason'],'SL')
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM events WHERE kind='NET_RR_NOT_APPLICABLE'").fetchone()[0],1)

    def test_short_stop_moves_down_only(self):
        self.enter(trailing(side='SHORT',stop_loss=D(102)))
        self.e.quote(quote('90',T+2000),T+2000)
        stop=D(self.j.orders()[0]['stop_price']); self.assertLess(stop,D(100))
        self.e.quote(quote('91',T+3000),T+3000)
        self.assertEqual(D(self.j.orders()[0]['stop_price']),stop)

    def test_corrupt_or_unjournaled_stop_blocks_recovery(self):
        self.enter(trailing())
        o=self.j.orders()[0]; o['stop_price']='99'
        with self.j.transaction(): self.j.save(o)
        self.assertFalse(PaperEngine(self.j,Policy()).recover(T+2000))
        self.assertIn('STOP_STATE_MISMATCH',self.j.get('paused'))

    def test_closed_channel_tightens_partial_fill_and_survives_recovery(self):
        s=replace(trailing(),strategy_id='turtle_55',entry_type='STOP',entry_price=D(101))
        self.assertIsNotNone(self.e.propose(s,INS,quote(),T))
        o=self.j.orders()[0]
        with self.j.transaction():
            self.e._entry_fill(o,D(o['quantity'])/2,D(101),T+1000,'partial-test',False)
        bar_ms=14400000
        bars=tuple(Candle(T+(i-19)*bar_ms,T+(i-18)*bar_ms-1,D(101),D(103),D(99),D(101),D(10)) for i in range(20))
        now=bars[-1].close_ms+1
        snap=Snapshot(now,INS,quote(time=now),(),(),candles_4h=bars)
        self.e.channel_stops(replace(snap,at_ms=now-2),now-2)
        self.assertEqual(D(self.j.orders()[0]['stop_price']),D(98))
        self.e.channel_stops(snap,now)
        self.assertEqual(D(self.j.orders()[0]['stop_price']),D(99))
        self.e.channel_stops(snap,now)
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM events WHERE kind='STOP_UPDATED'").fetchone()[0],1)
        self.assertTrue(PaperEngine(self.j,Policy()).recover(now))
        self.assertEqual(D(self.j.orders()[0]['remaining']),0)
        self.assertEqual(D(self.j.orders()[0]['stop_price']),D(99))
