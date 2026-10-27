"""V1-T1 safety and deterministic paper accounting acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.domain.alpha import AlphaAction, AlphaDecision
from quantos.domain.execution import AccountSnapshot, Position, ExecutionStatus, OrderType
from quantos.domain.execution.core import ExecutionEngine, ExecutionError, PaperFillProvider
from quantos.domain.execution.evidence import canonical
from quantos.domain.execution.policy import ExecutionCosts
from quantos.domain.market_data import Candle
from quantos.domain.risk.engine import RiskContext, RiskEngine, RiskPolicy, MarketState
from quantos.application.risk_execution import TradingStep, build_order_intent
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from quantos.infrastructure.configuration.paper import load_paper_config

T = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
COSTS = ExecutionCosts(D('0.001'), D('0.002'))
POLICY = RiskPolicy(D('10'), D('0.00001'), D('1'), D('1'), D('18'),
                    D('0.9'), D('0.1'), D('0.2'), 60, D('0'), COSTS)


def alpha(action=AlphaAction.BUY, timestamp=T):
    return AlphaDecision(timestamp, 'BTCUSDT', 'strategy-fixture', 'model-fixture',
                         'features-fixture', action, 'test', 'test', D('0.1'))


def market(timestamp=T, price=D('100'), symbol='BTCUSDT'):
    return MarketState(Candle(symbol, '1m', timestamp - timedelta(minutes=1), timestamp,
                              price, price, price, price, D('1'), price, 1), True)


def account(timestamp=T, cash=D('20'), quantity=D('0')):
    positions = () if not quantity else (Position('BTCUSDT', quantity, D('100'), timestamp),)
    return AccountSnapshot(timestamp, {'USDT': cash, 'BTC': quantity, 'ETH': D('0')}, positions)


def context(snapshot=None, timestamp=T, price=D('100')):
    return RiskContext(timestamp, (market(timestamp, price),), snapshot or account(),
                       D('0.02'), T.replace(hour=0, minute=0), D('20'), D('20'))


class RiskTests(unittest.TestCase):
    def reject(self, ctx=None, policy=POLICY, action=AlphaAction.BUY, reason=None):
        result = RiskEngine(policy).evaluate(alpha(action), ctx or context())
        self.assertFalse(result.approved)
        if reason:
            self.assertIn(reason, result.rejection_reason)
        return result

    def test_buy(self):
        risk = RiskEngine(POLICY).evaluate(alpha(), context())
        self.assertTrue(risk.approved)
        self.assertEqual(risk.approved_quantity, D('0.1'))
        self.assertEqual(risk.net_edge_rate, D('.016998'))

    def test_sell(self):
        risk = RiskEngine(POLICY).evaluate(alpha(AlphaAction.SELL), context(account(cash=D('10'), quantity=D('.1'))))
        self.assertEqual(risk.approved_quantity, D('.1'))

    def test_hold(self):
        self.reject(action=AlphaAction.HOLD, reason='HOLD')

    def test_stale(self):
        self.reject(replace(context(), markets=(market(T-timedelta(seconds=120)),)), reason='stale')

    def test_invalid_market(self):
        self.reject(replace(context(), markets=(replace(market(), valid=False),)), reason='invalid')

    def test_future_open_missing_and_duplicate_market(self):
        for markets in ((), (market(), market()), (market(T+timedelta(seconds=1)),),
                        (replace(market(), candle=replace(market().candle, open_time=T-timedelta(seconds=30))),)):
            with self.subTest(markets=markets):
                self.reject(replace(context(), markets=markets))

    def test_nonpositive_edge(self):
        for edge in (D('0'), D('-1'), D('.003002')):
            self.reject(replace(context(), gross_edge_rate=edge), reason='edge')

    def test_exposure(self):
        self.reject(policy=replace(POLICY, max_exposure_fraction=D('.4')), reason='exposure')

    def test_position_notional(self):
        self.reject(policy=replace(POLICY, max_position_notional=D('9')), reason='position')

    def test_position_quantity(self):
        self.reject(policy=replace(POLICY, max_position_quantity=D('.09')), reason='position')

    def test_daily_loss(self):
        self.reject(replace(context(), day_start_equity=D('25'), peak_equity=D('25')), reason='daily')

    def test_drawdown(self):
        self.reject(replace(context(), peak_equity=D('26')), reason='drawdown')

    def test_insufficient_cash(self):
        self.reject(replace(context(account(cash=D('10'))), day_start_equity=D('10'), peak_equity=D('10')),
                    policy=replace(POLICY, max_exposure_fraction=D('1')), reason='cash')

    def test_insufficient_sell(self):
        self.reject(action=AlphaAction.SELL, reason='owned')

    def test_zero_sized_order(self):
        self.reject(policy=replace(POLICY, quantity_step=D('1')), reason='quantity')

    def test_inconsistent_account(self):
        invalid = AccountSnapshot(T, {'USDT': D('20'), 'BTC': D('.1')}, ())
        self.reject(context(invalid), reason='balance')

    def test_missing_cross_asset_mark(self):
        snapshot = AccountSnapshot(T, {'USDT': D('10'), 'ETH': D('1')},
                                   (Position('ETHUSDT', D('1'), D('10'), T),))
        self.reject(context(snapshot), reason='market')

    def test_equity_and_day_state(self):
        for changes in ({'peak_equity': D('1')}, {'day_start_equity': D('0')},
                        {'day_start_timestamp': T-timedelta(days=1)}):
            self.reject(replace(context(), **changes))

    def test_bad_decimal_and_configuration(self):
        for value in (0.1, D('NaN'), D('-1')):
            with self.assertRaises(ValueError):
                replace(POLICY, order_notional=value)
        self.reject(replace(context(), gross_edge_rate=D('NaN')))

    def test_immutable_and_ambient_decimal_independence(self):
        expected = RiskEngine(POLICY).evaluate(alpha(), context())
        with localcontext() as ctx:
            ctx.prec = 3
            self.assertEqual(RiskEngine(POLICY).evaluate(alpha(), context()), expected)
        with self.assertRaises(TypeError):
            context().account.balances['USDT'] = D('999')


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'orders.jsonl'
        self.ledger = JsonlExecutionLedger(self.path)
        self.engine = ExecutionEngine(account(), COSTS, self.ledger, PaperFillProvider())
        self.step = TradingStep(RiskEngine(POLICY), self.engine)

    def buy(self):
        return self.step.run(alpha(), context(self.engine.snapshot))

    def sell(self, fraction=D('1')):
        timestamp = T+timedelta(minutes=1)
        step = TradingStep(RiskEngine(replace(POLICY, sell_fraction=fraction)), self.engine)
        return step.run(alpha(AlphaAction.SELL, timestamp), context(self.engine.snapshot, timestamp))

    def test_buy_exact_accounting(self):
        result = self.buy()
        self.assertEqual(result.execution.report.fill_price, D('100.2'))
        self.assertEqual(result.execution.fee, D('.01002'))
        self.assertEqual(self.engine.snapshot.balances['USDT'], D('9.96998'))
        self.assertEqual(self.engine.snapshot.balances['BTC'], D('.1'))
        self.assertEqual(self.engine.snapshot.positions[0].average_entry_price, D('100.2'))

    def test_sell_full_close_exact_accounting(self):
        self.buy()
        result = self.sell()
        self.assertEqual(result.execution.report.fill_price, D('99.8'))
        self.assertEqual(result.execution.fee, D('.00998'))
        self.assertEqual(self.engine.snapshot.balances['USDT'], D('19.94'))
        self.assertEqual(self.engine.snapshot.balances['BTC'], D('0'))
        self.assertEqual(self.engine.snapshot.positions, ())

    def test_partial_reduction(self):
        self.buy()
        self.sell(D('.5'))
        self.assertEqual(self.engine.snapshot.balances['BTC'], D('.05'))
        self.assertEqual(self.engine.snapshot.positions[0].average_entry_price, D('100.2'))
        self.assertEqual(self.engine.snapshot.balances['USDT'], D('14.95499'))

    def test_hold_and_rejection_never_reach_execution(self):
        before = self.path.read_bytes()
        for action in (AlphaAction.HOLD, AlphaAction.SELL):
            result = self.step.run(alpha(action), context())
            self.assertIsNone(result.execution)
        self.assertEqual(self.path.read_bytes(), before)

    def test_rejected_or_mismatched_risk_cannot_become_intent(self):
        risk = RiskEngine(POLICY).evaluate(alpha(AlphaAction.HOLD), context())
        with self.assertRaises(ValueError):
            build_order_intent(alpha(), risk, context(), POLICY)
        risk = RiskEngine(POLICY).evaluate(alpha(), context())
        with self.assertRaises(ValueError):
            build_order_intent(alpha(AlphaAction.SELL), risk, context(), POLICY)
        with self.assertRaises(ValueError):
            build_order_intent(alpha(), replace(risk, approved_quantity=D('1')), context(), POLICY)

    def test_stable_identity_and_duplicate_replay(self):
        first = self.buy()
        before = self.path.read_bytes()
        second = self.step.run(alpha(), context())
        self.assertEqual(first, second)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.engine.snapshot.balances['BTC'], D('.1'))

    def test_changed_payload_same_identity_fails_closed(self):
        self.buy()
        with self.assertRaises(ExecutionError):
            TradingStep(RiskEngine(replace(POLICY, order_notional=D('5'))), self.engine).run(alpha(), context())

    def test_obsolete_account_fails_closed(self):
        self.buy()
        later = T+timedelta(minutes=1)
        with self.assertRaises(ExecutionError):
            self.step.run(alpha(timestamp=later), context(timestamp=later))

    def test_replay_and_restart(self):
        first = self.buy()
        restarted = ExecutionEngine(account(), COSTS, JsonlExecutionLedger(self.path), PaperFillProvider())
        self.assertEqual(restarted.snapshot, self.engine.snapshot)
        self.assertEqual(restarted.reconcile(), self.engine.snapshot)
        self.assertEqual(TradingStep(RiskEngine(POLICY), restarted).run(alpha(), context()), first)

    def test_mismatch_latches_execution_closed(self):
        self.buy()
        with self.assertRaises(ExecutionError):
            self.engine.reconcile(account())
        with self.assertRaises(ExecutionError):
            self.sell()

    def test_corrupt_ledger(self):
        self.buy()
        with self.path.open('ab') as stream:
            stream.write(b'{broken')
        with self.assertRaises(ExecutionError):
            self.engine.reconcile()
        with self.assertRaises(ExecutionError):
            ExecutionEngine(account(), COSTS, self.ledger, PaperFillProvider())

    def test_ledger_is_canonical_and_deterministic(self):
        self.buy()
        for line in self.path.read_text().splitlines():
            self.assertEqual(canonical(json.loads(line)), line)
        other = JsonlExecutionLedger(Path(self.temp.name)/'other.jsonl')
        engine = ExecutionEngine(account(), COSTS, other, PaperFillProvider())
        TradingStep(RiskEngine(POLICY), engine).run(alpha(), context())
        self.assertEqual(self.path.read_bytes(), other.path.read_bytes())

    def test_altered_fee_and_state_are_detected(self):
        self.buy()
        lines = self.path.read_text().splitlines()
        record = json.loads(lines[-1])
        record['result']['fee'] = '0'
        lines[-1] = canonical(record)
        self.path.write_text('\n'.join(lines)+'\n')
        self.ledger.head_path.write_bytes(self.ledger._head(tuple(lines)))
        with self.assertRaises(ExecutionError):
            self.engine.reconcile()

    def test_truncation_and_wrong_initial_state(self):
        self.buy()
        with self.assertRaises(ExecutionError):
            ExecutionEngine(account(cash=D('21')), COSTS, self.ledger, PaperFillProvider())
        self.path.write_bytes(self.path.read_bytes().splitlines(keepends=True)[0])
        with self.assertRaises(ExecutionError):
            self.engine.reconcile()

    def test_append_failure_prevents_state_change_and_retry(self):
        initial = self.engine.snapshot
        def fail(*args):
            raise OSError('uncertain persistence')
        self.ledger.append = fail
        with self.assertRaises(ExecutionError):
            self.buy()
        self.assertEqual(self.engine.snapshot, initial)
        with self.assertRaises(ExecutionError):
            self.buy()

    def test_concurrent_stale_writer_fails_closed(self):
        second = ExecutionEngine(account(), COSTS, JsonlExecutionLedger(self.path), PaperFillProvider())
        self.buy()
        with self.assertRaises(ExecutionError):
            TradingStep(RiskEngine(POLICY), second).run(alpha(), context())

    def test_limit_marketability_buy_and_sell(self):
        result = self.step.run(alpha(), context(), order_type=OrderType.LIMIT, limit_price=D('100.2'))
        self.assertEqual(result.execution.report.status, ExecutionStatus.FILLED)
        later = T+timedelta(minutes=1)
        result = self.step.run(alpha(AlphaAction.SELL, later), context(self.engine.snapshot, later),
                               order_type=OrderType.LIMIT, limit_price=D('99.8'))
        self.assertEqual(result.execution.report.status, ExecutionStatus.FILLED)
        self.assertEqual(self.engine.snapshot.positions, ())

    def test_unmarketable_limit_is_final_rejection(self):
        initial = self.engine.snapshot
        result = self.step.run(alpha(), context(), order_type=OrderType.LIMIT, limit_price=D('100'))
        self.assertEqual(result.execution.report.status, ExecutionStatus.REJECTED)
        self.assertEqual(self.engine.snapshot, initial)
        self.assertEqual(self.engine.reconcile(), initial)
        self.assertEqual(self.step.run(alpha(), context(), order_type=OrderType.LIMIT,
                                       limit_price=D('100')), result)
        with self.assertRaises(ExecutionError):
            self.buy()  # Cannot turn a rejected LIMIT identity into a new MARKET order.

    def test_no_negative_cash_or_short_even_if_called_directly(self):
        from quantos.domain.execution.core import apply_result
        risk = RiskEngine(POLICY).evaluate(alpha(), context())
        intent = build_order_intent(alpha(), risk, context(), POLICY)
        result = PaperFillProvider().fill(intent)
        with self.assertRaisesRegex(ExecutionError, 'cash'):
            apply_result(account(cash=D('1')), intent, result)
        owned = account(cash=D('10'), quantity=D('.1'))
        sell_alpha = alpha(AlphaAction.SELL)
        sell_context = context(owned)
        risk = RiskEngine(POLICY).evaluate(sell_alpha, sell_context)
        intent = build_order_intent(sell_alpha, risk, sell_context, POLICY)
        with self.assertRaisesRegex(ExecutionError, 'short'):
            apply_result(account(), intent, PaperFillProvider().fill(intent))

    def test_weighted_average_entry_price(self):
        self.buy()
        later = T+timedelta(minutes=1)
        policy = replace(POLICY, order_notional=D('5'))
        ctx = replace(context(self.engine.snapshot, later, D('110')), peak_equity=D('21'))
        result = TradingStep(RiskEngine(policy), self.engine).run(alpha(timestamp=later), ctx)
        self.assertTrue(result.risk.approved)
        quantity = result.risk.approved_quantity
        with localcontext() as arithmetic_context:
            arithmetic_context.prec = 34
            expected = (D('.1')*D('100.2')+quantity*D('110.22'))/(D('.1')+quantity)
        self.assertEqual(self.engine.snapshot.positions[0].average_entry_price, expected)

    def test_cross_symbol_exposure_counts_all_positions(self):
        initial = AccountSnapshot(T, {'USDT': D('10'), 'ETH': D('.1')},
                                  (Position('ETHUSDT', D('.1'), D('100'), T),))
        ctx = replace(context(initial), markets=(market(), market(symbol='ETHUSDT')))
        policy = replace(POLICY, order_notional=D('9'), max_position_notional=D('20'))
        result = RiskEngine(policy).evaluate(alpha(), ctx)
        self.assertFalse(result.approved)
        self.assertIn('exposure', result.rejection_reason)

    def test_stale_lock_prevents_any_new_fill(self):
        lock = self.path.with_name(self.path.name+'.lock')
        lock.write_text('')
        with self.assertRaises(ExecutionError):
            self.buy()
        self.assertEqual(self.engine.snapshot, account())

    def test_restart_detects_removed_complete_tail(self):
        self.buy()
        self.path.write_bytes(self.path.read_bytes().splitlines(keepends=True)[0])
        with self.assertRaises(ExecutionError):
            ExecutionEngine(account(), COSTS, self.ledger, PaperFillProvider())

    def test_restart_detects_missing_ledger(self):
        self.buy()
        self.path.unlink()
        with self.assertRaises(ExecutionError):
            ExecutionEngine(account(), COSTS, self.ledger, PaperFillProvider())

    def test_missing_head_fails_closed(self):
        self.buy()
        self.ledger.head_path.unlink()
        with self.assertRaises(ExecutionError):
            self.engine.reconcile()

    def test_wrong_sequence_fails_replay(self):
        self.buy()
        lines = self.ledger.read()
        data = json.loads(lines[-1])
        bad = lines[:-1]+(canonical(dict(data, sequence=99)),)
        # Even internally consistent framing/head cannot bless invalid evidence.
        self.path.write_text('\n'.join(bad)+'\n', encoding='ascii')
        self.ledger.head_path.write_bytes(self.ledger._head(bad))
        with self.assertRaises(ExecutionError):
            self.engine.reconcile()

    def test_io_failure_after_durable_append_recovers_once(self):
        real_append = self.ledger.append
        def uncertain(record, expected):
            real_append(record, expected)
            raise OSError('caller did not receive durable acknowledgment')
        self.ledger.append = uncertain
        with self.assertRaises(ExecutionError):
            self.buy()
        self.assertEqual(self.engine.snapshot, account())
        restarted = ExecutionEngine(account(), COSTS, JsonlExecutionLedger(self.path), PaperFillProvider())
        result = TradingStep(RiskEngine(POLICY), restarted).run(alpha(), context())
        self.assertEqual(result.execution.report.status, ExecutionStatus.FILLED)
        self.assertEqual(restarted.snapshot.balances['BTC'], D('.1'))
        self.assertEqual(len(self.path.read_text().splitlines()), 2)

    def test_unknown_provider_result_fails_closed(self):
        from quantos.domain.execution.core import ExecutionResult
        from quantos.domain.execution import ExecutionReport
        class UncertainProvider(PaperFillProvider):
            def fill(self, intent):
                return ExecutionResult(ExecutionReport(intent.request.request_id, T,
                    ExecutionStatus.UNKNOWN, intent.request.quantity, reason='unknown'), D('0'), D('0'))
        self.engine.provider = UncertainProvider()
        with self.assertRaises(ExecutionError):
            self.buy()
        self.assertEqual(self.engine.snapshot, account())

    def test_flight_recorder(self):
        import logging
        with self.assertLogs('quantos', logging.INFO) as captured:
            self.buy()
            self.sell()
            self.engine.reconcile()
        events = {record.event for record in captured.records}
        self.assertTrue({'alpha_decision', 'risk_input', 'risk_decision', 'order_intent',
                         'order_request', 'execution_result', 'fill', 'reconciliation_result'} <= events)

    def test_ambient_decimal_independence(self):
        with localcontext() as ctx:
            ctx.prec = 3
            self.buy()
            self.sell()
        self.assertEqual(self.engine.snapshot.balances['USDT'], D('19.94'))

    def test_config_and_live_disabled(self):
        config = load_paper_config(Path(__file__).resolve().parents[2]/'configs/paper.toml')
        self.assertEqual(config.initial_capital, D('20'))
        self.assertEqual(config.mode, 'paper')
        text = (Path(__file__).resolve().parents[2]/'configs/paper.toml').read_text()
        bad = Path(self.temp.name)/'bad.toml'
        bad.write_text(text.replace('mode = "paper"', 'mode = "live"'))
        with self.assertRaises(ValueError):
            load_paper_config(bad)


class ConfigurationAndBoundaryTests(unittest.TestCase):
    def test_strict_config_rejects_unsafe_values(self):
        path = Path(__file__).resolve().parents[2]/'configs/paper.toml'
        text = path.read_text()
        mutations = (
            ('fee_rate = "0.001"', 'fee_rate = 0.001'),
            ('fee_rate = "0.001"', 'fee_rate = "NaN"'),
            ('fee_rate = "0.001"', 'fee_rate = "1"'),
            ('slippage_rate = "0.002"', 'slippage_rate = "-1"'),
            ('sell_fraction = "1"', 'sell_fraction = "1.1"'),
            ('stale_seconds = 60', 'stale_seconds = true'),
            ('initial_capital = "20"', 'initial_capital = "0"'),
            ('ledger_path = "artifacts/paper/execution.jsonl"', 'ledger_path = ""'),
            ('order_notional = "10"', 'unknown = "10"'),
        )
        with TemporaryDirectory() as directory:
            target = Path(directory)/'config.toml'
            for old, new in mutations:
                with self.subTest(new=new):
                    target.write_text(text.replace(old, new))
                    with self.assertRaises(ValueError):
                        load_paper_config(target)

    def test_stops_at_exact_loss_boundaries(self):
        daily = replace(context(account(cash=D('18'))), peak_equity=D('20'))
        drawdown = replace(context(account(cash=D('16'))), day_start_equity=D('16'))
        for ctx, reason in ((daily, 'daily'), (drawdown, 'drawdown')):
            result = RiskEngine(POLICY).evaluate(alpha(), ctx)
            self.assertFalse(result.approved)
            self.assertIn(reason, result.rejection_reason)

    def test_decisions_and_config_reject_float_quantities(self):
        for value in (0.1, D('NaN'), D('Infinity'), D('0'), D('-1')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    replace(POLICY, quantity_step=value)

    def test_sell_is_rounded_down_without_short_or_forced_dust_fill(self):
        snapshot = account(cash=D('10'), quantity=D('.100001'))
        ctx = replace(context(snapshot), peak_equity=D('20.1'))
        result = RiskEngine(POLICY).evaluate(alpha(AlphaAction.SELL), ctx)
        self.assertEqual(result.approved_quantity, D('.1'))

    def test_risk_evaluation_does_not_replace_snapshot_members(self):
        ctx = context()
        balances = ctx.account.balances
        positions = ctx.account.positions
        RiskEngine(POLICY).evaluate(alpha(), ctx)
        self.assertIs(ctx.account.balances, balances)
        self.assertIs(ctx.account.positions, positions)
