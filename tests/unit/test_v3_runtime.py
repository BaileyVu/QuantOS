from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.cli import parser
from quantos.paper.demo import run_demo
from quantos.paper.runtime import arbitrate
from quantos.paper.types import D
from tests.unit.test_v3_engine import signal
from dataclasses import replace
from quantos.paper.types import Quote, Trade, dumps
from quantos.paper.strategies import EmaAtrLimitStrategy, DonchianAtrStopStrategy
from quantos.paper.journal import Journal
from quantos.paper.engine import PaperEngine
from quantos.paper.risk import Policy
from tests.unit.test_v3_strategies import make_snapshot


class RuntimeTests(unittest.TestCase):
    def test_each_reference_strategy_traverses_risk_entry_and_target(self):
        for strategy in (EmaAtrLimitStrategy(), DonchianAtrStopStrategy()):
            with self.subTest(strategy=strategy.strategy_id), tempfile.TemporaryDirectory() as temp:
                closes = [D(100)+D(i)*D('.05') for i in range(80)]
                if isinstance(strategy, DonchianAtrStopStrategy): closes[-1] += 5
                snap = make_snapshot(closes)
                bars = tuple(replace(c, high=max(c.open,c.close)+3, low=min(c.open,c.close)-3) for c in snap.candles_5m)
                now = snap.at_ms+1
                q = Quote('BTCUSDC',now,now,closes[-1]-D('.05'),closes[-1]+D('.05'),D(100),D(100),closes[-1])
                snap = replace(snap, at_ms=now, quote=q, candles_5m=bars)
                proposal = strategy.evaluate(snap)
                self.assertIsNotNone(proposal)
                j = Journal(Path(temp)/'j.db','p1','synthetic')
                try:
                    engine = PaperEngine(j,Policy()); engine.recover(now); engine.connection(True,now)
                    identity = engine.propose(proposal,snap.instrument,q,now)
                    self.assertIsNotNone(identity, j.db.execute("select payload from events where kind='REJECTED_RISK'").fetchall())
                    def at(price, delta): return replace(q,bid=price,ask=price+D('.01'),mark=price,time_ms=now+delta,received_ms=now+delta)
                    if proposal.entry_type=='LIMIT':
                        engine.trade(Trade('BTCUSDC',1,now+1000,proposal.entry_price-D('.01'),D(1000),True),now+1000)
                    else:
                        engine.quote(at(proposal.entry_price,1000),now+1000)
                        engine.quote(at(proposal.entry_price,2000),now+2000)
                    engine.quote(at(proposal.take_profit,3000),now+3000)
                    engine.quote(at(proposal.take_profit,4000),now+4000)
                    self.assertEqual(j.orders()[0]['state'],'CLOSED')
                    self.assertEqual(j.orders()[0]['close_reason'],'TP')
                finally: j.close()

    def test_recorded_inputs_replay_deterministically_without_network(self):
        from quantos.paper.replay import replay
        from tests.unit.test_v3_engine import quote, T
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'input.jsonl'
            source.write_text(dumps({'received_ms':T,'records':[['quote',quote()]]})+'\n')
            with patch('urllib.request.urlopen',side_effect=AssertionError('replay must be offline')):
                a = replay([source],Path(temp)/'a')
                b = replay([source],Path(temp)/'b')
            self.assertEqual(a['profiles'],b['profiles'])
    def test_no_live_mode_or_authenticated_order_command(self):
        for argv in (["start", "--mode", "live"], ["live"], ["testnet"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                parser().parse_args(argv)

    def test_conflicts_block_all_same_symbol_proposals(self):
        eligible, conflicts = arbitrate([signal(), signal(signal_id="short", side="SHORT")])
        self.assertEqual(eligible, [])
        self.assertEqual(conflicts, {"BTCUSDC"})

    def test_seeded_demo_both_profiles_and_accounting(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)/"demo"
            with patch("urllib.request.urlopen", side_effect=AssertionError("no network allowed")):
                report = run_demo(state)
            for profile in ("p1", "p5"):
                p = report["profiles"][profile]
                self.assertEqual(len(p["closed_trades"]), 5)
                self.assertEqual(p["open_orders"], [])
                self.assertEqual(D(p["cash"])-200, D(p["net_cash_change"]))
                self.assertTrue(all(t["R"] is not None for t in p["closed_trades"]))
                self.assertEqual(p["identity"]["evidence"], "synthetic")
            self.assertEqual(len(report["run"]["scenarios"]), 12)
            with self.assertRaises(FileExistsError): run_demo(state)

    def test_risk_profiles_do_not_scale_pnl(self):
        with tempfile.TemporaryDirectory() as temp:
            report = run_demo(Path(temp)/"demo")
            one, five = (report["profiles"][p] for p in ("p1", "p5"))
            self.assertNotEqual(D(one["net_cash_change"])*5, D(five["net_cash_change"]))
