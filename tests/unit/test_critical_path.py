"""Synthetic critical-path regressions; no external requests or operational Alpha."""
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from quantos.application.retail_edge_session import run_edge_session
from quantos.application.retail_trader import RetailPaperEngine
from quantos.application.retail_replay import replay_commands, RetailCausalProcessor
from quantos.domain.alpha.hft import HftSide
from quantos.domain.evaluation.retail import RetailEvaluation
from quantos.domain.market_data.hft import MarkPriceEvent
from quantos.domain.execution.retail_paper import RetailPaperAccount
from quantos.domain.risk.retail import FullEquityRiskPolicy, RetailRiskEngine
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.infrastructure.storage.retail_events import RetailEventJournal, read_journal
from quantos.infrastructure.storage.retail_paper import RetailPaperStateStore, RetailPaperStateError, account_payload
from quantos.interfaces.retail_edge import replay_capture
from tests.unit.test_hft_paper import feature, fees, rules, book, at, delta, snapshot
from tests.unit.test_retail_full_equity import intent


class TargetEconomicsTests(unittest.TestCase):
    def evaluate(self, signal, f=None, policy=None):
        return RetailRiskEngine(policy or FullEquityRiskPolicy(), fees(), rules()).evaluate(
            signal, f or feature(), D(200), D(200), False)

    def test_prediction_cannot_override_close_target(self):
        self.assertEqual(self.evaluate(intent(expected_move_bps=D(10000), take_profit_price=D('101.01'))).reason,
                         'non_economic_protective_target')

    def test_long_and_short_economic_targets(self):
        self.assertTrue(self.evaluate(intent()).approved)
        self.assertTrue(self.evaluate(intent(side=HftSide.SELL, protective_stop_price=D(102), take_profit_price=D(97))).approved)

    def test_exact_cost_boundary_rejected(self):
        # Set target first, then solve the safety allowance so gross == costs.
        f=feature(); ref=f.best_ask; target=D('102'); ratio=target/ref
        gross=(target-ref)/ref*10000
        other=(fees().taker_rate*10000+D(1))*(1+ratio)+(f.best_ask-f.best_bid)/2/ref*10000+max(D(1),(f.feed_latency_ms+100)/100)
        policy=replace(FullEquityRiskPolicy(),safety_buffer_bps=gross-other)
        self.assertFalse(self.evaluate(intent(take_profit_price=target), f, policy).approved)

    def test_invalid_protection_and_prediction_rejected(self):
        for field in ('protective_stop_price','take_profit_price','expected_move_bps','uncertainty_bps'):
            for value in (D('NaN'),D('Infinity'),D('-Infinity')):
                with self.subTest(field=field,value=value):
                    self.assertFalse(self.evaluate(intent(**{field:value})).approved)
        self.assertFalse(self.evaluate(intent(take_profit_price=D(100))).approved)

    def test_costs_turn_gross_profit_into_loss(self):
        self.assertFalse(self.evaluate(intent(take_profit_price=D('101.2'))).approved)
        self.assertFalse(self.evaluate(intent(expected_move_bps=D(1))).approved)

    def test_legacy_hft_leverage_cannot_expand_full_equity(self):
        config=load_hft_config(Path('configs/futures_hft_200_paper.toml'))
        self.assertEqual(config.risk.maximum_leverage,5)
        engine=RetailPaperEngine(config,rules(),RetailPaperAccount(fees(),FullEquityRiskPolicy()),RetailEvaluation(),FullEquityRiskPolicy())
        d=engine.risk.evaluate(intent(),feature(),D(200),D(200),False)
        self.assertTrue(d.approved)
        self.assertLessEqual(d.quantity*feature().best_ask,D(200))
        self.assertIsNone(engine.policy.maximum_risk_fraction)


class Provider:
    def reset(self): pass
    def observe(self, f, ns):
        return intent(strategy_id='synthetic-provider',book_update_id=f.book_update_id,
                      observed_ns=ns,expires_ns=ns+2_000_000_000)


class ProviderIsolationTests(unittest.TestCase):
    def engine(self, provider):
        config=load_hft_config(Path('configs/futures_hft_200_paper.toml'))
        policy=FullEquityRiskPolicy();account=RetailPaperAccount(fees(),policy)
        engine=RetailPaperEngine(config,rules(),account,RetailEvaluation(),policy,alpha_provider=provider)
        engine.book=book();engine.disarmed=False;engine.funding_received_ns=2_000_000_000
        account.funding_due=at(100)
        return engine

    def event(self, engine, ns=2_000_000_000):
        seq=engine.book.last_update_id+1
        item=replace(delta(first=seq,final=seq,previous=seq-1),received_monotonic_ns=ns)
        engine.processing_clock_ns=lambda: ns
        with patch.object(engine.features,'compute',return_value=replace(feature(),book_update_id=seq)):
            return engine.on_event(item,feed_latency_ms=D(1),clock_healthy=True)

    def test_exclusive_provider_without_unified_and_real_fill(self):
        e=self.engine(Provider())
        with patch.object(e.alpha,'observe',side_effect=AssertionError('legacy Q03')), \
             patch.object(e.edge_alpha,'observe',side_effect=AssertionError('legacy Q04')), \
             patch.object(e.reclaim_alpha,'observe',side_effect=AssertionError('legacy Q05')):
            self.assertEqual(self.event(e)['event'],'retail_entry_submitted')
            self.event(e,2_200_000_000)
        self.assertEqual(len(e.account.fills),1)
        self.assertTrue(all(k.startswith('synthetic-provider:') for k in e.evaluation.candidate_funnel))
        self.assertEqual(e.evaluation.alpha_reasons,{})

    def test_none_is_no_trade(self):
        p=Provider();p.observe=lambda f,ns:None;e=self.engine(p)
        self.assertIsNone(self.event(e));self.assertEqual(e.account.orders_submitted,0)

    def test_malformed_contract_rejected_before_risk(self):
        for proposed in (object(), intent(observed_ns=3_000_000_000),intent(book_update_id=1),
                         intent(take_profit_price='bad'),intent(side='BUY'),intent(expires_ns=2_000_000_000),
                         intent(expected_move_bps=D('NaN'))):
            with self.subTest(proposed=type(proposed).__name__):
                p=Provider();p.observe=lambda f,ns:proposed;e=self.engine(p)
                with patch.object(e.risk,'evaluate',side_effect=AssertionError('Risk reached')):
                    self.assertIsNone(self.event(e))
                self.assertTrue(e.disarmed);self.assertEqual(e.account.orders_submitted,0)

    def test_observe_contract_required(self):
        with self.assertRaises(ValueError):self.engine(type('Invalid',(),{'reset':lambda self:None})())

    def test_failed_risk_never_submits(self):
        p=Provider();p.observe=lambda f,ns:intent(book_update_id=f.book_update_id,take_profit_price=D('101.01'))
        e=self.engine(p);self.assertEqual(self.event(e)['event'],'retail_rejected')
        self.assertEqual(e.account.orders_submitted,0)


class SessionFaultTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, root, *, fault=None, transport=None, kill=False):
        config=load_hft_config(Path('configs/futures_hft_200_paper.toml'))
        config=replace(config,feature=replace(config.feature,depth_levels=1))
        policy=FullEquityRiskPolicy();store=RetailPaperStateStore(root/'account.json')
        account=store.reset('fixture',fees(),policy)
        journal=RetailEventJournal(root/'capture.jsonl');events=[]
        journal.append('manifest',version='qos07-v1',config_text=Path('configs/futures_hft_200_paper.toml').read_text().replace('depth_levels = 5','depth_levels = 1'),policy=asdict(policy),rules=asdict(rules()),account=account_payload(account))
        class Clock:
            def server_time(self):return datetime.now(timezone.utc)
            def depth_snapshot(self,symbol):
                return replace(snapshot(),received_at=datetime.now(timezone.utc),received_monotonic_ns=time.monotonic_ns())
        class Stream:
            async def wait_public_ready(self):
                if transport:raise transport
            async def wait_market_ready(self):pass
            async def events(self,symbol):
                n=101
                while True:
                    if fault == 'producer':raise RuntimeError('producer programming failure')
                    now=datetime.now(timezone.utc)
                    yield replace(delta(first=n-1,final=n,previous=n-1),received_at=now,exchange_time=now,received_monotonic_ns=time.monotonic_ns())
                    n+=1
                    if fault == 'postfill':
                        now=datetime.now(timezone.utc)
                        from datetime import timedelta
                        yield MarkPriceEvent('BTCUSDC',D(100),D('.0001'),now+timedelta(hours=1),now,now,time.monotonic_ns())
                    await asyncio.sleep(.01)
        class BrokenExternal:
            async def events(self):
                raise RuntimeError('external programming failure')
                yield  # async generator interface
        external = [('fixture','leader_quote',BrokenExternal)] if fault == 'external' else []
        from quantos.infrastructure.binance.hft import BinanceHftStreamError
        original_append=journal.append
        def append(kind,**payload):
            if fault in ('journal_os','journal_hash') and kind=='command' and payload['action']=='bootstrap':
                raise OSError('disk exhausted') if fault=='journal_os' else RuntimeError('hash append failure')
            if fault == 'postfill' and account.fills and kind == 'command':
                raise OSError('fill journal write failed')
            return original_append(kind,**payload)
        journal.append=append
        if fault=='save':store.save=lambda *a,**k: (_ for _ in ()).throw(OSError('disk exhausted'))
        if fault=='account':account.balance=D(201)
        def engine_factory(*args, **kwargs):
            if fault == 'postfill':
                kwargs['alpha_provider'] = Provider()
            return RetailPaperEngine(*args, **kwargs)
        try:
            with patch('quantos.application.retail_edge_session.RetailPaperEngine',side_effect=engine_factory):
                result=await run_edge_session(config=config,rules=rules(),account=account,evaluation=RetailEvaluation(reclaim=True,reliability=True),policy=policy,store=store,identity='fixture',duration_seconds=1,client=Clock(),stream_factory=Stream,external_factories=external,journal=journal,emit=events.append,kill_requested=lambda:kill,reclaim=True,reliability=True,feed_errors=(TimeoutError,ConnectionError,BinanceHftStreamError))
        finally:journal.close()
        return result,events

    async def test_timeout_reconnects_under_duration_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            result,events=await self.run_case(Path(temp),transport=TimeoutError('timeout'))
            self.assertGreater(result['reconnects'],0)
            self.assertLessEqual(result['reconnects'],3)
            self.assertTrue(replay_capture(Path(temp)/'capture.jsonl')['matches_capture'])

    async def test_connection_disconnect_reconnects(self):
        with tempfile.TemporaryDirectory() as temp:
            result,_=await self.run_case(Path(temp),transport=ConnectionResetError('disconnected'))
            self.assertGreater(result['reconnects'],0)

    async def test_websocket_adapter_disconnect_reconnects(self):
        from quantos.infrastructure.binance.hft import BinanceHftStreamError
        with tempfile.TemporaryDirectory() as temp:
            result,_=await self.run_case(Path(temp),transport=BinanceHftStreamError('disconnect'))
            self.assertGreater(result['reconnects'],0)

    async def test_producer_programming_failure_is_not_lost_in_cleanup(self):
        await self.assert_aborted('producer',RuntimeError)

    async def test_external_worker_programming_failure_is_fatal(self):
        await self.assert_aborted('external',RuntimeError)

    async def test_journal_disk_failure_aborts_without_completion(self):
        await self.assert_aborted('journal_os',OSError)

    async def test_hash_append_failure_aborts_without_completion(self):
        await self.assert_aborted('journal_hash',RuntimeError)

    async def test_checkpoint_failure_aborts_without_completion(self):
        await self.assert_aborted('save',OSError)

    async def test_invalid_account_aborts_without_completion(self):
        await self.assert_aborted('account',RetailPaperStateError)

    async def assert_aborted(self,fault,error):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.assertRaises(error):await self.run_case(root,fault=fault)
            records=list(read_journal(root/'capture.jsonl'))
            self.assertNotIn('complete',[r['kind'] for r in records])
            self.assertEqual(records[-1]['kind'],'aborted')
            with self.assertRaises(ValueError):replay_capture(root/'capture.jsonl')

    async def test_controlled_kill_durably_completes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);report,_=await self.run_case(root,kill=True)
            self.assertEqual(report['shutdown_reason'],'operator_kill')
            self.assertTrue(replay_capture(root/'capture.jsonl')['matches_capture'])
            from quantos.interfaces.retail_edge import read_state
            account,runtime=read_state(root/'account.json')
            self.assertTrue(account.risk_off)
            self.assertEqual(runtime['metrics']['ledger_digest'],report['ledger_digest'])

    async def test_failure_after_real_fill_preserves_ledger_without_completion(self):
        from quantos.interfaces.retail_edge import read_state
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with self.assertRaises(OSError):await self.run_case(root,fault='postfill')
            account,_=read_state(root/'account.json')
            self.assertEqual(len(account.fills),1)
            self.assertGreater(account.total_fees,0)
            self.assertIsNotNone(account.position)
            self.assertNotIn('complete',[r['kind'] for r in read_journal(root/'capture.jsonl')])
            with self.assertRaises(ValueError):replay_capture(root/'capture.jsonl')

    async def test_programming_failure_is_fatal(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):await self.run_case(Path(temp),transport=ValueError('programming error'))
            self.assertNotIn('complete',[r['kind'] for r in read_journal(Path(temp)/'capture.jsonl')])


class JournalIntegrityTests(unittest.TestCase):
    def test_failed_terminal_fsync_does_not_leave_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'capture.jsonl';j=RetailEventJournal(path);j.append('manifest')
            with patch('quantos.infrastructure.storage.retail_events.os.fsync',side_effect=OSError('disk')):
                with self.assertRaises(OSError):j.append('complete',report={})
            with self.assertRaises(RuntimeError):j.append('complete',report={})
            j.close();self.assertEqual([r['kind'] for r in read_journal(path)],['manifest'])

    def test_aborted_or_nonterminal_completion_is_rejected(self):
        p=RetailCausalProcessor(RetailPaperEngine(load_hft_config(Path('configs/futures_hft_200_paper.toml')),rules(),RetailPaperAccount(fees(),FullEquityRiskPolicy()),RetailEvaluation(),FullEquityRiskPolicy()))
        for rows in ([{'kind':'aborted'}],[{'kind':'complete','payload':{'report':{}}},{'kind':'aborted'}]):
            with self.assertRaises(ValueError):replay_commands(p,rows)
