"""Complete fixture Alpha -> Risk -> Execution -> ledger -> replay path."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.application.risk_execution import TradingStep
from quantos.domain.execution import ExecutionStatus
from quantos.domain.execution.core import ExecutionEngine, PaperFillProvider
from quantos.domain.risk.engine import RiskEngine
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from tests.unit.test_v1t1 import account, alpha, context, COSTS, POLICY, T
from quantos.domain.alpha import AlphaAction
from datetime import timedelta


class VerticalLifecycleTests(unittest.TestCase):
    def test_buy_sell_reconcile(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'execution.jsonl'
            ledger = JsonlExecutionLedger(path)
            engine = ExecutionEngine(account(), COSTS, ledger, PaperFillProvider())
            step = TradingStep(RiskEngine(POLICY), engine)
            buy = step.run(alpha(), context(engine.snapshot))
            self.assertTrue(buy.risk.approved)
            self.assertEqual(buy.execution.report.status, ExecutionStatus.FILLED)
            self.assertEqual(len(engine.snapshot.positions), 1)
            later = T+timedelta(minutes=1)
            sell = step.run(alpha(AlphaAction.SELL, later), context(engine.snapshot, later))
            self.assertTrue(sell.risk.approved)
            self.assertEqual(sell.execution.report.status, ExecutionStatus.FILLED)
            self.assertEqual(engine.snapshot.positions, ())
            self.assertEqual(engine.snapshot.balances['USDT'], Decimal('19.94'))
            self.assertEqual(len(path.read_text().splitlines()), 3)
            self.assertEqual(engine.reconcile(), engine.snapshot)
            recovered = ExecutionEngine(account(), COSTS, ledger, PaperFillProvider())
            self.assertEqual(recovered.snapshot, engine.snapshot)
