from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.cli import parser
from quantos.paper.demo import run_demo
from quantos.paper.runtime import arbitrate
from quantos.paper.types import D
from tests.unit.test_v3_engine import signal
from quantos.paper.runtime import Runtime, dispatch
from quantos.paper.types import Funding
import json
import time
from dataclasses import replace
from quantos.paper.types import Quote, Trade, dumps
from quantos.paper.strategies import EmaAtrLimitStrategy, DonchianAtrStopStrategy
from quantos.paper.journal import Journal
from quantos.paper.engine import PaperEngine
from quantos.paper.risk import Policy
from tests.unit.test_v3_strategies import make_snapshot


class RuntimeTests(unittest.TestCase):
    def test_restart_clears_old_finish_time_without_extending_deadline(self):
        from quantos.paper.report import write_reports
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            now = int(time.time()*1000)
            previous = dict(started_ms=now-10000, deadline_ms=now+10000,
                            finished_ms=now-8000, evidence='natural', status='stopped',
                            restarts=0, hours=72, reported_days=[])
            (state/'run.json').write_text(json.dumps(previous))
            runtime = Runtime(state)
            runtime.scanner = lambda: None
            runtime.feed = lambda *_: None
            runtime.stop_requested = True
            reports = []
            def capture_report(path, run, **kwargs):
                result = write_reports(path, dict(run), **kwargs)
                reports.append(result)
                return result
            with (patch('quantos.paper.runtime.subprocess.Popen'),
                  patch('quantos.paper.runtime.write_reports', side_effect=capture_report),
                  patch('urllib.request.urlopen', side_effect=AssertionError('no network'))):
                self.assertEqual(runtime.run(), 0)
            first = reports[0]
            self.assertNotIn('finished_ms', first['run'])
            self.assertEqual(first['run']['started_ms'], previous['started_ms'])
            self.assertEqual(first['run']['deadline_ms'], previous['deadline_ms'])
            self.assertEqual(first['run']['restarts'], 1)
            self.assertGreaterEqual(first['wall_seconds'], 10)

    def test_full_no_key_supervisor_start_uses_only_injected_public_feed(self):
        from tests.unit.test_v3_engine import INS, quote
        with tempfile.TemporaryDirectory() as temp:
            class Market:
                def funding(self,*args): return ()
            runtime = Runtime(Path(temp),hours=.00002,market_factory=Market)
            runtime.scanner = lambda: runtime.instruments.update({'BTCUSDC':INS})
            runtime.feed = lambda *_: runtime.emit('feed',[('quote',quote(time=int(time.time()*1000)))])
            with (patch.dict('os.environ',{},clear=True), patch('urllib.request.urlopen',side_effect=AssertionError('unexpected HTTP')),
                  patch('quantos.paper.runtime.subprocess.Popen')):
                self.assertEqual(runtime.run(),0)
            self.assertEqual(json.loads((Path(temp)/'run.json').read_text())['status'],'completed')
            for profile in ('p1','p5'):
                j=Journal(Path(temp)/f'journal_{profile}.db',profile)
                try: self.assertEqual(j.cash(),D(200))
                finally: j.close()

    def test_funding_is_emitted_even_when_subsequent_quote_fails(self):
        from tests.unit.test_v3_engine import INS, T
        with tempfile.TemporaryDirectory() as temp:
            runtime = Runtime(Path(temp))
            runtime.instruments = {'BTCUSDC': INS}
            funding = Funding('BTCUSDC',T+1500,D('.001'),D(100))
            class Market:
                def trades(self,*args,**kwargs): return ()
                def funding(self,*args): return (funding,)
                def quote(self,*args):
                    runtime.shutdown.set()
                    raise ValueError('stale quote')
            runtime.market_factory = Market
            with patch('quantos.paper.runtime.milliseconds',return_value=T+3000):
                runtime.feed(T,'BTCUSDC')
            self.assertEqual(runtime.messages.get_nowait(),('funding',(funding,)))
            self.assertEqual(runtime.messages.get_nowait()[0],'disconnected')

    def test_versioned_capture_replays_identical_fills_and_cash(self):
        from quantos.paper.replay import replay
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)/'capture'; state.mkdir()
            runtime = Runtime(state)
            strategies = (EmaAtrLimitStrategy(),)
            snap = make_snapshot([D(100)+D(i)*D('.05') for i in range(80)])
            bars = tuple(replace(c,high=max(c.open,c.close)+3,low=min(c.open,c.close)-3) for c in snap.candles_5m)
            now = snap.at_ms+1
            q = Quote('BTCUSDC',now,now,D('103.90'),D('104'),D(100),D(100),D('103.95'))
            snap = replace(snap,at_ms=now,quote=q,candles_5m=bars)
            proposal = strategies[0].evaluate(snap)
            j = Journal(state/'original.db','p1','natural')
            try:
                engine = PaperEngine(j,Policy())
                def quote_at(price,ms): return replace(q,bid=price,ask=price+D('.01'),mark=price,time_ms=ms,received_ms=ms)
                records = [(now,'restart',{}),(now,'feed',[('quote',q)]),(now,'scan',[snap]),
                    (now+1000,'feed',[('quote',quote_at(proposal.entry_price,now+1000)),
                        ('trade',Trade('BTCUSDC',1,now+1000,proposal.entry_price-D('.01'),D(1000),True))]),
                    (now+2000,'feed',[('quote',quote_at(proposal.take_profit,now+2000))]),
                    (now+3000,'feed',[('quote',quote_at(proposal.take_profit,now+3000))])]
                for ts,kind,payload in records:
                    runtime.record(kind,payload,ts)
                    dispatch([engine],kind,payload,ts,strategies=strategies)
                expected = list(j.db.execute('SELECT kind,quantity,price,fee,pnl FROM fills ORDER BY rowid'))
                expected = [tuple(row) for row in expected]
                self.assertEqual(len(expected),2)
                result = replay(sorted((state/'market').glob('*.gz')),Path(temp)/'replay',strategies[0].strategy_id)
                self.assertEqual(D(result['profiles']['p1']['cash']),j.cash())
                other = sqlite3.connect(Path(temp)/'replay/journal_p1.db')
                try: self.assertEqual(other.execute('SELECT kind,quantity,price,fee,pnl FROM fills ORDER BY rowid').fetchall(),expected)
                finally: other.close()
            finally: j.close()
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
