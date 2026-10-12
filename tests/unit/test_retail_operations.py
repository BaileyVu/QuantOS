import asyncio
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from quantos.application.retail_edge_session import run_edge_session
from quantos.application.retail_replay import RetailCausalProcessor
from quantos.application.retail_trader import RetailPaperEngine
from quantos.domain.evaluation.retail import RetailEvaluation
from quantos.domain.execution.retail_paper import RetailPaperAccount
from quantos.domain.market_data.hft import AggregateTrade
from quantos.domain.risk.retail import RetailRiskPolicy
from quantos.infrastructure.configuration.hft import load_hft_config
from quantos.infrastructure.storage.retail_events import RetailEventJournal
from quantos.infrastructure.storage.retail_paper import RetailPaperStateStore, account_payload, restore_account, RetailPaperStateError
from quantos.interfaces.retail_edge import edge_command
from tests.unit.test_hft_paper import at, book, fees, rules
from tests.unit.test_retail_paper import signal, depth

def paper_fixture():
    from tests.unit.test_retail_paper import RetailPaperTests
    fixture = RetailPaperTests()
    fixture.setUp()
    return fixture


class RetailOperationsTests(unittest.TestCase):
    def setUp(self):
        self.config=load_hft_config(Path("configs/futures_hft_200_paper.toml"))
        self.policy=RetailRiskPolicy()
        self.account=RetailPaperAccount(fees(),self.policy)
        self.engine=RetailPaperEngine(self.config,rules(),self.account,RetailEvaluation(),self.policy,unified=True)
        self.engine.book=book()
        self.engine.disarmed=False

    def test_duplicate_trade_never_double_counts_flow(self):
        item=AggregateTrade("BTCUSDC",1,D("100"),D("1"),False,at(1),at(1),received_monotonic_ns=1_000_000_000)
        # Fresh synthetic book receive clock for each trade.
        self.engine.book.last_received_monotonic_ns=1_000_000_000
        self.engine.on_event(item,feed_latency_ms=D("1"),clock_healthy=True)
        self.engine.on_event(replace(item,received_monotonic_ns=1_100_000_000),feed_latency_ms=D("1"),clock_healthy=True)
        self.assertEqual(len(self.engine.features._trades),1)
        self.assertEqual(self.engine.evaluation.rejections["duplicate_or_regressed_trade"],1)

    def test_stalled_pipeline_and_kill_cancel_without_fabricated_exit(self):
        fixture=paper_fixture();fixture._entry(visible=".01")
        self.engine.account=fixture.account
        self.engine.book=fixture.book
        processor=RetailCausalProcessor(self.engine)
        processor.apply("tick",{"processing_ns":5_000_000_000,"timestamp":at(5),"kill":False})
        self.assertTrue(self.engine.disarmed)
        self.assertIsNone(fixture.account.pending)
        self.assertIsNotNone(fixture.account.position)
        processor.apply("tick",{"processing_ns":5_100_000_000,"timestamp":at(5),"kill":True})
        self.assertTrue(fixture.account.risk_off)
        self.assertEqual(len(fixture.account.fills),1)

    def test_invalid_features_prevent_pending_fill(self):
        fixture=paper_fixture()
        from quantos.domain.risk.retail import RetailRiskDecision
        fixture.account.submit_entry(RetailRiskDecision(True,"approved",D(".05"),D("13"),D(".3")),signal(),at(2),2_000_000_000,D("101"))
        engine=RetailPaperEngine(self.config,rules(),fixture.account,RetailEvaluation(),self.policy,processing_clock_ns=lambda:2_200_000_000)
        engine.book=fixture.book;engine.disarmed=False
        # Only two levels while configuration requires five: must disarm before matching.
        from tests.unit.test_hft_paper import delta
        event=replace(delta(first=102,final=102,previous=101),received_monotonic_ns=2_200_000_000)
        engine.on_event(event,feed_latency_ms=D("1"),clock_healthy=True)
        self.assertEqual(fixture.account.fills,[])
        self.assertIsNone(fixture.account.pending)

    def test_external_failure_cancels_entry_before_fill(self):
        from quantos.domain.risk.retail import RetailRiskDecision
        from quantos.domain.market_data.hft import MarkPriceEvent
        from tests.unit.test_hft_paper import delta
        config = replace(self.config, feature=replace(self.config.feature, depth_levels=1))
        engine = RetailPaperEngine(config, rules(), self.account, RetailEvaluation(), self.policy,
                                   processing_clock_ns=lambda: 2_200_000_000, unified=True)
        engine.book = book(); engine.disarmed = False
        engine.funding_received_ns = 2_000_000_000
        self.account.observe_funding(MarkPriceEvent("BTCUSDC",D("100"),D(".0001"),at(100),at(2),at(2),2_000_000_000))
        intent = replace(signal(), strategy_id="Q04-LL-v1")
        self.account.submit_entry(RetailRiskDecision(True,"approved",D(".05"),D("13"),D(".3")), intent, at(2),2_000_000_000,D("101"))
        RetailCausalProcessor(engine).apply("source_failure", {"provider":"fixture", "source_kind":"leader_quote", "reason":"disconnected"})
        item = replace(delta(first=102,final=102,previous=101),received_monotonic_ns=2_200_000_000)
        engine.on_event(item,feed_latency_ms=D("1"),clock_healthy=True)
        self.assertEqual(self.account.fills,[])
        self.assertIsNone(self.account.pending)
        self.assertEqual(self.account.lifecycle[-1]["reason"],"alpha_or_risk_invalidated_before_fill")

    def test_restart_exposure_funding_and_risk_off_are_durable(self):
        fixture=paper_fixture();fixture._entry()
        fixture.account.risk_off=True
        with tempfile.TemporaryDirectory() as root:
            store=RetailPaperStateStore(Path(root)/"account.json")
            store.reset("identity",fees(),self.policy)
            store.save("identity",fixture.account,{})
            restored,_=store.load("identity",fees(),self.policy)
            self.assertTrue(restored.risk_off)
            self.assertEqual(restored.position.opened_ns,0)
            recovered=RetailPaperEngine(self.config,rules(),restored,RetailEvaluation(),self.policy,unified=True)
            self.assertTrue(recovered.force_exit_on_recovery)
            self.assertEqual(restored.balance,fixture.account.balance)

    def test_offline_status_kill_and_config_rejection_never_construct_gateway(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"account.json"
            RetailPaperStateStore(path).reset("identity",fees(),self.policy)
            with patch("quantos.interfaces.retail_edge.BinanceHftPublicClient",side_effect=AssertionError("network")):
                self.assertEqual(edge_command(Namespace(futures_operation="retail-edge-status",state_store=path)),0)
                self.assertEqual(edge_command(Namespace(futures_operation="retail-edge-kill",state_store=path)),0)
                with self.assertRaisesRegex(ValueError,"kill marker"):
                    edge_command(Namespace(futures_operation="retail-edge-paper",state_store=path))
                with self.assertRaisesRegex(ValueError,"200"):
                    edge_command(Namespace(futures_operation="retail-edge-reset",state_store=Path(root)/"bad.json",confirm_new_paper_account=True,market_config=Path("configs/futures_hft_paper.toml")))

    def test_no_authenticated_dependencies_in_unified_entry_points(self):
        import ast
        paths=["src/quantos/interfaces/retail_edge.py","src/quantos/application/retail_edge_session.py","src/quantos/infrastructure/public_data/retail.py"]
        for path in paths:
            for node in ast.walk(ast.parse(Path(path).read_text())):
                if isinstance(node,ast.ImportFrom):
                    self.assertNotIn("authenticated",node.module or "")

    def test_bad_capital_rejected_in_replay_restore(self):
        payload=account_payload(self.account);payload["starting_equity"]=D("100")
        with self.assertRaisesRegex(RetailPaperStateError,"200"):
            restore_account(payload,fees(),self.policy)


class BoundedSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_kill_is_bounded_and_records_no_network_orders(self):
        config=load_hft_config(Path("configs/futures_hft_200_paper.toml"))
        policy=RetailRiskPolicy()
        class PublicClock:
            def server_time(self):
                return datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as root:
            store=RetailPaperStateStore(Path(root)/"account.json")
            account=store.reset("identity",fees(),policy)
            journal=RetailEventJournal(Path(root)/"events.jsonl")
            report=await run_edge_session(config=config,rules=rules(),account=account,
                evaluation=RetailEvaluation(),policy=policy,store=store,identity="identity",
                duration_seconds=1,client=PublicClock(),stream_factory=lambda: self.fail("stream should not start"),
                external_factories=[],journal=journal,emit=lambda e: None,kill_requested=lambda: True)
            journal.close()
            self.assertEqual(report["shutdown_reason"],"operator_kill")
            self.assertEqual(report["orders_submitted"],0)
            self.assertTrue(account.risk_off)
