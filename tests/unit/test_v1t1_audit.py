"""Narrow final V1-T1 boundary and safety regressions."""
import ast
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.application.risk_execution import TradingStep, build_order_intent
from quantos.domain.alpha import AlphaAction
from quantos.domain.execution import OrderType
from quantos.domain.execution.core import ExecutionEngine, ExecutionError, PaperFillProvider
from quantos.domain.risk.engine import RiskEngine
from quantos.domain.runtime_contracts import AccountSnapshot, Position
from quantos.infrastructure.configuration.paper import load_paper_config
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from tests.unit.test_v1t1 import COSTS, POLICY, T, account, alpha, context, market


class AuditTests(unittest.TestCase):
    def test_risk_has_no_execution_dependency(self):
        root = Path(__file__).resolve().parents[2]/'src/quantos/domain'
        for path in (*sorted((root/'risk').glob('*.py')), root/'runtime_contracts.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                         else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    self.assertFalse(name.startswith('quantos.domain.execution'), (path, name))

    def test_snapshot_types_are_shared_with_compatible_execution_exports(self):
        from quantos.domain.execution import AccountSnapshot as ExportedAccount, Position as ExportedPosition
        self.assertIs(AccountSnapshot, ExportedAccount)
        self.assertIs(Position, ExportedPosition)

    def test_sell_allowed_at_daily_loss_stop(self):
        ctx = context(account(cash=D('8'), quantity=D('.1')))
        result = RiskEngine(POLICY).evaluate(alpha(AlphaAction.SELL), ctx)
        self.assertTrue(result.approved)
        self.assertEqual(result.approved_quantity, D('.1'))

    def test_sell_allowed_at_drawdown_stop(self):
        ctx = replace(context(account(cash=D('6'), quantity=D('.1'))), day_start_equity=D('16'))
        self.assertTrue(RiskEngine(POLICY).evaluate(alpha(AlphaAction.SELL), ctx).approved)

    def test_sell_allowed_with_nonpositive_entry_edge(self):
        for edge in (D('-1'), D('0'), D('.003002')):
            ctx = replace(context(account(cash=D('10'), quantity=D('.1'))), gross_edge_rate=edge)
            self.assertTrue(RiskEngine(POLICY).evaluate(alpha(AlphaAction.SELL), ctx).approved)

    def test_sell_still_rejects_invalid_or_stale_state(self):
        ctx = replace(context(account(cash=D('8'), quantity=D('.1'))), gross_edge_rate=D('-1'))
        for changes in ({'markets': ()}, {'markets': (replace(market(), valid=False),)},
                        {'markets': (market(T-timedelta(minutes=2)),)},
                        {'gross_edge_rate': D('NaN')}, {'account': account()},
                        {'account': AccountSnapshot(T, {'USDT': D('8'), 'BTC': D('.2')}, ctx.account.positions)}):
            self.assertFalse(RiskEngine(POLICY).evaluate(alpha(AlphaAction.SELL), replace(ctx, **changes)).approved)

    def test_marked_holdings_are_included_in_equity_for_buy(self):
        ctx = context(account(cash=D('10'), quantity=D('.1')))
        policy = replace(POLICY, order_notional=D('5'))
        self.assertTrue(RiskEngine(policy).evaluate(alpha(), ctx).approved)
        marked_loss = replace(ctx, markets=(market(price=D('50')),))
        self.assertFalse(RiskEngine(policy).evaluate(alpha(), marked_loss).approved)

    def test_exact_zero_edge_including_safety_margin_rejects_buy(self):
        policy = replace(POLICY, safety_margin_rate=D('.005'))
        ctx = replace(context(), gross_edge_rate=D('.008002'))
        self.assertFalse(RiskEngine(policy).evaluate(alpha(), ctx).approved)

    def test_enabled_volatility_scaling_is_bounded_and_deterministic(self):
        policy = replace(POLICY, volatility_target=D('.01'))
        for volatility, quantity in ((D('.02'), D('.05')), (D('.01'), D('.1')),
                                     (D('.005'), D('.1')), (D('0'), D('.1'))):
            ctx = replace(context(), volatility=volatility)
            result = RiskEngine(policy).evaluate(alpha(), ctx)
            self.assertEqual(result.approved_quantity, quantity)
            with localcontext() as ambient:
                ambient.prec = 3
                self.assertEqual(RiskEngine(policy).evaluate(alpha(), ctx), result)

    def test_enabled_volatility_scaling_rejects_unavailable_or_invalid_input(self):
        policy = replace(POLICY, volatility_target=D('.01'))
        for volatility in (None, D('NaN'), D('-1'), 0.1):
            self.assertFalse(RiskEngine(policy).evaluate(alpha(), replace(context(), volatility=volatility)).approved)

    def test_quantity_rounding_cannot_cross_notional_boundary(self):
        policy = replace(POLICY, order_notional=D('2.99999999999999999999999999999999999'), quantity_step=D('1'))
        result = RiskEngine(policy).evaluate(alpha(), context(price=D('3')))
        self.assertFalse(result.approved)
        self.assertIn('quantity', result.rejection_reason)

    def test_request_identity_binds_risk_and_order_policy(self):
        decision = alpha()
        ctx = context()
        risk = RiskEngine(POLICY).evaluate(decision, ctx)
        intent = build_order_intent(decision, risk, ctx, POLICY)
        self.assertEqual(build_order_intent(decision, risk, ctx, POLICY), intent)
        smaller_policy = replace(POLICY, order_notional=D('5'))
        smaller = build_order_intent(decision, RiskEngine(smaller_policy).evaluate(decision, ctx), ctx, smaller_policy)
        limit = build_order_intent(decision, risk, ctx, POLICY, order_type=OrderType.LIMIT, limit_price=D('100.2'))
        self.assertEqual(len({intent.request.request_id, smaller.request.request_id, limit.request.request_id}), 3)
        with self.assertRaises(ExecutionError):
            replace(intent, request=replace(intent.request, quantity=D('.2'))).validate()

    def test_legitimate_loss_exit_executes_and_unknown_state_blocks_exit(self):
        initial = account(cash=D('6'), quantity=D('.1'))
        ctx = replace(context(initial), gross_edge_rate=D('-1'), day_start_equity=D('16'))
        with TemporaryDirectory() as directory:
            engine = ExecutionEngine(initial, COSTS, JsonlExecutionLedger(Path(directory)/'ledger.jsonl'))
            step = TradingStep(RiskEngine(POLICY), engine)
            result = step.run(alpha(AlphaAction.SELL), ctx)
            self.assertTrue(result.risk.approved)
            self.assertEqual(engine.snapshot.positions, ())
            self.assertEqual(engine.snapshot.balances['USDT'], D('15.97002'))
            self.assertEqual(engine.reconcile(), engine.snapshot)
            with self.assertRaises(ExecutionError):
                engine.reconcile(initial)
            with self.assertRaises(ExecutionError):
                step.run(alpha(AlphaAction.SELL), ctx)

    def test_replay_does_not_resubmit_provider_orders(self):
        class CountingProvider(PaperFillProvider):
            def __init__(self):
                self.submissions = 0
            def fill(self, intent):
                self.submissions += 1
                return super().fill(intent)
        with TemporaryDirectory() as directory:
            ledger = JsonlExecutionLedger(Path(directory)/'ledger.jsonl')
            provider = CountingProvider()
            engine = ExecutionEngine(account(), COSTS, ledger, provider)
            TradingStep(RiskEngine(POLICY), engine).run(alpha(), context())
            self.assertEqual(provider.submissions, 1)
            engine.reconcile()
            ExecutionEngine(account(), COSTS, ledger, provider)
            self.assertEqual(provider.submissions, 1)

    def test_optional_volatility_configuration(self):
        config_path = Path(__file__).resolve().parents[2]/'configs/paper.toml'
        with TemporaryDirectory() as directory:
            path = Path(directory)/'config.toml'
            path.write_text(config_path.read_text()+'\nvolatility_target = "0.01"\n')
            self.assertEqual(load_paper_config(path).risk.volatility_target, D('.01'))
