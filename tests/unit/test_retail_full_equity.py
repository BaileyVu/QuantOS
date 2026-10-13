from argparse import Namespace
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from quantos.domain.alpha.hft import HftSide
from quantos.domain.alpha.retail import ProtectedRetailSignal
from quantos.domain.risk.retail import FullEquityRiskPolicy, RetailRiskPolicy, RetailRiskEngine
from quantos.domain.execution.retail_paper import RetailPaperAccount
from quantos.infrastructure.storage.retail_paper import RetailPaperStateStore, RetailPaperStateError, exclusive_account, account_payload, restore_account
from quantos.interfaces.retail_runtime import managed_run, session_status, BoundedLog
from tests.unit.test_hft_paper import feature, fees, rules, book, at
from tests.unit.test_retail_paper import depth


def intent(**changes):
    return replace(ProtectedRetailSignal("synthetic-only", HftSide.BUY, D(300), D(300), 101,
        2_000_000_000, 4_000_000_000, protective_stop_price=D(99), take_profit_price=D(103)), **changes)


class FullEquityTests(unittest.TestCase):
    def setUp(self):
        self.policy = FullEquityRiskPolicy()
        self.risk = RetailRiskEngine(self.policy, fees(), rules())
        self.account = RetailPaperAccount(fees(), self.policy)
        self.book = book()

    def decision(self, signal=None, equity=D(200), peak=D(200), daily=D(200)):
        return self.risk.evaluate(signal or intent(), feature(), equity, peak, False, daily_start_equity=daily)

    def enter(self, visible="5"):
        decision = self.decision()
        self.account.submit_entry(decision, intent(), at(2), 2_000_000_000, D(101))
        item = depth(self.book, final=102, ns=2_100_000_000, asks=((D(101),D(visible)),))
        self.account.on_depth(self.book,item.received_at,item.received_monotonic_ns)
        return decision

    def change_book(self, bid, ask, ns=3_000_000_000):
        item = depth(self.book,final=self.book.last_update_id+1,ns=ns,
            bids=tuple((p,D(0)) for p in self.book.bids if p != D(bid))+((D(bid),D(10)),),
            asks=tuple((p,D(0)) for p in self.book.asks if p != D(ask))+((D(ask),D(10)),))
        return item

    def exit_at(self,bid,ask):
        item=self.change_book(bid,ask)
        self.account.manage_protection(self.book,item.received_at,item.received_monotonic_ns,item.received_monotonic_ns)
        self.assertIsNone(self.account.on_depth(self.book,item.received_at,item.received_monotonic_ns))
        item=depth(self.book,final=self.book.last_update_id+1,ns=3_100_000_000)
        return self.account.on_depth(self.book,item.received_at,item.received_monotonic_ns)

    def test_full_equity_reserves_and_no_standalone_one_percent_cap(self):
        d=self.decision()
        self.assertTrue(d.approved)
        self.assertGreater(d.quantity*101,D(198))
        self.assertLess(d.quantity*101,D(200))
        self.assertGreater(d.quantity*d.risk_per_unit,D(2))
        self.assertLessEqual(d.quantity*d.risk_per_unit,D(6))
        legacy=RetailRiskEngine(RetailRiskPolicy(),fees(),rules()).evaluate(intent(),feature(),D(200),D(200),False)
        self.assertTrue(legacy.approved)

    def test_loss_headroom_sizes_down_and_breaker_rejects(self):
        smaller=self.decision(equity=D(195))
        self.assertTrue(smaller.approved)
        self.assertLess(smaller.quantity,self.decision().quantity)
        self.assertLessEqual(smaller.quantity*smaller.risk_per_unit,D(1))
        self.assertFalse(self.decision(equity=D(194)).approved)
        self.assertFalse(self.decision(daily=D(210)).approved)

    def test_venue_liquidity_limit_and_invalid_stops(self):
        r=RetailRiskEngine(self.policy,fees(),replace(rules(),maximum_quantity=D('.1')))
        self.assertEqual(r.evaluate(intent(),feature(),D(200),D(200),False).quantity,D('.1'))
        for stop in (D(0),D(101),D(102),D('NaN')):
            self.assertFalse(self.decision(intent(protective_stop_price=stop)).approved)
        self.assertFalse(self.decision(intent(expected_move_bps=D(1))).approved)
        d=self.risk.evaluate(None,feature(),D(200),D(200),False)
        self.assertFalse(d.approved)
        with self.assertRaises(ValueError):
            self.account.submit_entry(d,intent(),at(2),2_000_000_000,D(101))
        self.assertEqual(self.account.orders_submitted,0)

    def test_profit_compounds_and_protective_exit_accounts_once(self):
        initial=self.enter()
        fill=self.exit_at('103','104')
        self.assertEqual(fill.reason,'take_profit')
        self.assertGreater(self.account.balance,200)
        next_d=self.decision(equity=self.account.balance,peak=self.account.peak_equity,daily=self.account.daily_start_equity)
        self.assertGreater(next_d.quantity,initial.quantity)
        total=self.account.balance
        self.account.on_depth(self.book,at(4),3_100_000_000)
        self.assertEqual(self.account.balance,total)
        self.assertEqual(self.account.balance-200,self.account.realized_net_pnl)

    def test_gap_through_stop_exceeds_intended_level_and_reduces_capital(self):
        initial=self.enter()
        fill=self.exit_at('98.5','99.5')
        self.assertEqual(fill.reason,'protective_stop')
        self.assertLess(fill.price,D(99))
        self.assertLess(self.account.balance,200)
        d=self.decision(equity=self.account.balance)
        self.assertLess(d.quantity,initial.quantity)

    def test_partial_entry_stop_cancels_remaining_and_tracks_quantity(self):
        self.enter('.1')
        self.assertEqual(self.account.position.quantity,D('.1'))
        self.assertIsNotNone(self.account.pending)
        fill=self.exit_at('98','99')
        self.assertEqual(fill.quantity,D('.1'))
        self.assertIsNone(self.account.position)
        self.assertEqual(self.account.orders_cancelled,1)

    def test_exit_liquidity_partial_and_uncertain_data_do_not_fabricate_fill(self):
        self.enter()
        item=self.change_book('98','99')
        self.account.manage_protection(self.book,item.received_at,3_000_000_000,3_000_000_000)
        item=depth(self.book,final=104,ns=3_100_000_000,bids=((D(98),D('.1')),))
        fill=self.account.on_depth(self.book,item.received_at,3_100_000_000)
        self.assertEqual(fill.quantity,D('.1'))
        self.assertGreater(self.account.position.quantity,0)
        before=self.account.balance
        self.book.valid=False
        self.assertIsNone(self.account.on_depth(self.book,at(4),4_000_000_000))
        self.assertEqual(before,self.account.balance)
        self.assertIsNotNone(self.account.position)

    def test_restart_open_stop_and_corrupt_state_rejection(self):
        self.enter('.1')
        with tempfile.TemporaryDirectory() as root:
            store=RetailPaperStateStore(Path(root)/'account.json')
            store.reset('full',fees(),self.policy)
            store.save('full',self.account,{})
            restored,_=store.load('full',fees(),self.policy)
            self.assertEqual(restored.position.stop_price,D(99))
            self.assertEqual(restored.position.quantity,D('.1'))
            self.assertIsNone(restored.pending)
            self.assertEqual(restored.position.opened_ns,0)
            with self.assertRaises(RetailPaperStateError):
                store.load('wrong',fees(),self.policy)
            raw=account_payload(restored);raw['position']['stop_price']=D(0)
            with self.assertRaises(RetailPaperStateError):
                restore_account(raw,fees(),self.policy)

    def test_synthetic_alpha_uses_engine_risk_execution_and_compounds(self):
        from quantos.application.retail_trader import RetailPaperEngine
        from quantos.domain.evaluation.retail import RetailEvaluation
        from quantos.infrastructure.configuration.hft import load_hft_config
        from tests.unit.test_hft_paper import delta
        class SyntheticAlpha:
            def reset(self): pass
            def observe(self, f, ns):
                return intent(signal_id=f"synthetic-{ns}", observed_ns=ns, expires_ns=ns+2_000_000_000,
                    book_update_id=f.book_update_id, protective_stop_price=f.best_ask-D(2), take_profit_price=f.best_ask+D(2))
        config=load_hft_config(Path('configs/futures_hft_200_paper.toml'))
        engine=RetailPaperEngine(config,rules(),self.account,RetailEvaluation(),self.policy,unified=True,alpha_provider=SyntheticAlpha())
        engine.book=self.book;engine.disarmed=False
        engine.funding_received_ns=2_000_000_000
        self.account.funding_due=at(100)
        def event(ns,bid=None,ask=None):
            f=self.book.last_update_id+1
            bids=tuple((p,D(0)) for p in self.book.bids if p != D(bid))+((D(bid),D(10)),) if bid else ()
            asks=tuple((p,D(0)) for p in self.book.asks if p != D(ask))+((D(ask),D(10)),) if ask else ()
            item=replace(delta(first=f,final=f,previous=f-1,bids=bids,asks=asks),received_monotonic_ns=ns,received_at=at(ns/1e9),exchange_time=at(ns/1e9))
            engine.processing_clock_ns=lambda:ns
            def features(*a,**kw):
                return replace(feature(),book_update_id=f,best_bid=engine.book.best_bid,best_ask=engine.book.best_ask)
            with patch.object(engine.features,'compute',side_effect=features):
                return engine.on_event(item,feed_latency_ms=D(1),clock_healthy=True)
        event(2_000_000_000)
        original=self.account.pending.quantity*self.account.pending.reference_price
        event(2_200_000_000)
        self.assertIsNotNone(self.account.position)
        event(3_000_000_000,'103','104')
        event(3_200_000_000)
        self.assertIsNone(self.account.position)
        self.assertGreater(self.account.realized_net_pnl,0)
        event(3_400_000_000)
        self.assertGreater(self.account.pending.quantity*self.account.pending.reference_price,original)
        self.assertEqual(engine.evaluation.risk_approved,2)

    def test_full_equity_captured_execution_replays_exactly(self):
        from tests.unit.test_reclaim import pipeline
        from quantos.interfaces.retail_edge import replay_capture
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'events.jsonl'
            report,account=pipeline(path,full_equity=True)
            replay=replay_capture(path)
            self.assertTrue(replay['matches_capture'])
            self.assertEqual(report['ledger_digest'],replay['metrics']['ledger_digest'])
            self.assertGreater(account.realized_net_pnl,0)

    def test_trade_trigger_latches_and_exit_retries_cannot_reuse_liquidity(self):
        self.enter()
        self.account.observe_protective_price(D('98.5'),at(3))
        self.assertEqual(self.account.position.exit_reason,'protective_stop')
        item=self.change_book('100','101')
        self.account.manage_protection(self.book,item.received_at,3_000_000_000,3_000_000_000)
        self.assertEqual(self.account.pending.reason,'protective_stop')
        item=depth(self.book,final=104,ns=3_100_000_000,bids=((D(100),D('.1')),))
        self.account.on_depth(self.book,item.received_at,3_100_000_000)
        quantity=self.account.position.quantity
        item=depth(self.book,final=105,ns=5_100_000_000)
        self.account.manage_protection(self.book,item.received_at,5_100_000_000,5_100_000_000)
        item=depth(self.book,final=106,ns=5_200_000_000)
        self.assertIsNone(self.account.on_depth(self.book,item.received_at,5_200_000_000))
        self.assertEqual(self.account.position.quantity,quantity)


class ManagedRuntimeTests(unittest.TestCase):
    def args(self,root):
        return Namespace(session=Path(root)/'session',state_store=Path(root)/'account.json',
            market_config=Path('configs/futures_hft_200_paper.toml'),capital_profile=Path('configs/retail_full_equity_paper.toml'),duration_seconds=1)

    def test_startup_failure_has_durable_manifest_and_exit(self):
        with tempfile.TemporaryDirectory() as root:
            args=self.args(root)
            with patch('quantos.interfaces.retail_runtime.preflight',side_effect=ValueError('missing account')):
                self.assertEqual(managed_run(args),1)
            result=session_status(args.session)
            self.assertEqual(result['terminal']['exit_code'],1)
            self.assertEqual(result['terminal']['error']['message'],'missing account')
            self.assertFalse((args.session/'events.jsonl').exists())
            with self.assertRaises(FileExistsError): managed_run(args)

    def test_managed_exit_and_signal_evidence(self):
        import os, signal
        with tempfile.TemporaryDirectory() as root:
            args=self.args(root)
            def operation(namespace):
                self.assertTrue((args.session/'startup.json').exists())
                from tests.unit.test_reclaim import pipeline
                report, account = pipeline(args.session/'events.jsonl', full_equity=True)
                store = RetailPaperStateStore(args.state_store)
                store.reset('fixture', account.fees, account.policy)
                store.save('fixture', account, {'metrics': report})
                os.kill(os.getpid(),signal.SIGTERM)
                return 0
            with patch('quantos.interfaces.retail_runtime.preflight',return_value={}):
                self.assertEqual(managed_run(args,operation),0)
            data=session_status(args.session)['terminal']
            self.assertEqual(data['reason'],'operator_signal')
            self.assertEqual(data['signals'],[signal.SIGTERM])
            self.assertTrue(args.state_store.with_suffix('.json.kill').exists())

    def test_failure_preserves_claim_and_preflight_rejects_stale_state(self):
        from quantos.interfaces.retail_runtime import preflight
        with tempfile.TemporaryDirectory() as root:
            args=self.args(root)
            with patch('quantos.interfaces.retail_runtime.preflight',return_value={}):
                self.assertEqual(managed_run(args,lambda ns: (_ for _ in ()).throw(RuntimeError('crash'))),1)
            self.assertTrue(args.state_store.with_suffix('.json.session.json').exists())
            with self.assertRaisesRegex(ValueError,'unreconciled'):
                preflight(args)

    def test_crash_and_concurrent_account_detection_and_bounded_log(self):
        with tempfile.TemporaryDirectory() as root:
            args=self.args(root);args.session.mkdir()
            (args.session/'startup.json').write_text('{}')
            self.assertIn('terminated_without_exit',session_status(args.session)['status'])
            with exclusive_account(args.state_store):
                with self.assertRaises(RetailPaperStateError):
                    with exclusive_account(args.state_store): pass
            log=BoundedLog(args.session/'log',maximum=3)
            log.write('abc')
            with self.assertRaises(RuntimeError):log.write('d')
            log.close()
