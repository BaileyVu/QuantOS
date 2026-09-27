"""Offline public-stream composition through real Risk and Paper Execution."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.application.paper_runtime import StopSignal
from quantos.domain.alpha import AlphaAction
from quantos.interfaces.paper_runtime import run_paper
from tests.unit.test_binance_live_stream import FakeConnector
from tests.unit.test_binance_live_klines import combined_kline, encode
from tests.unit.test_v1t2_evaluation import FixtureAlpha
from tests.unit.test_v1t3_runtime import Clock

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def messages(minutes):
    result = []
    for minute in minutes:
        for symbol in ("ETHUSDT", "BTCUSDT"):
            payload = combined_kline(symbol, minute)
            price = D("100") + D(minute)/2
            payload["data"]["k"].update(o=str(price), h=str(price+2), l=str(price-2),
                c=str(price+D("0.1") if minute % 2 else price+D("0.2")),
                v=str(10+minute), q=str((10+minute)*price))
            result.append(encode(payload))
    return result


class LivePaperVerticalTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_adapter_buy_stop_restart_duplicate_sell(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paper = Path("configs/paper.toml").read_text()
            paper = paper.replace('ledger_path = "artifacts/paper/execution.jsonl"',
                                  f'ledger_path = "{root.as_posix()}/orders.jsonl"')
            (root/"paper.toml").write_text(paper)
            config = Path("configs/paper_runtime.toml").read_text()
            config = config.replace("artifacts/paper/runtime.json", f"{root.as_posix()}/runtime.json")
            config = config.replace("artifacts/paper/minutes.jsonl", f"{root.as_posix()}/minutes.jsonl")
            (root/"runtime.toml").write_text(config)
            clock = Clock()
            clock.now = START+timedelta(minutes=1, microseconds=123456)
            buy_time = START+timedelta(minutes=21, microseconds=-1)
            sell_time = START+timedelta(minutes=22, microseconds=-1)
            alpha = FixtureAlpha({("BTCUSDT", buy_time): AlphaAction.BUY,
                                  ("BTCUSDT", sell_time): AlphaAction.SELL})

            async def session(minutes):
                connector = FakeConnector(messages(minutes)+[None])
                connection = connector.connections[0]
                original = connection.recv
                async def receive():
                    raw = await original()
                    clock.now = max(clock.now, EPOCH+timedelta(microseconds=json.loads(raw)["data"]["E"]))
                    return raw
                connection.recv = receive
                stop = StopSignal()
                task = asyncio.create_task(run_paper(root/"runtime.toml", alpha=alpha,
                    alpha_implementation_id="offline-fixture-only", logger=logging.getLogger("quantos"),
                    stop=stop, clock=clock, connector=connector))
                try:
                    await asyncio.wait_for(connection.receiving.wait(), 15)
                    stop.request("offline test stop")
                    await asyncio.wait_for(task, 15)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(connection.closed)
                self.assertEqual(connector.active, 0)
                return json.loads((root/"runtime.json").read_bytes())["state"]

            opened = await session(range(21))
            self.assertEqual(len(alpha.seen), 2)
            self.assertEqual(len((root/"orders.jsonl").read_bytes().splitlines()), 2)
            closed = await session((20, 21))
            self.assertEqual(len(alpha.seen), 4)  # persisted minute 20 never calls Alpha again
            self.assertEqual(len((root/"orders.jsonl").read_bytes().splitlines()), 3)
            self.assertEqual(closed["evidence_count"], 22)
            self.assertEqual(closed["status"], "ready")
            self.assertEqual(closed["last_equity"]["gross_exposure"], "0")
            evidence = [json.loads(line)["evidence"] for line in (root/"minutes.jsonl").read_bytes().splitlines()]
            buy = evidence[20]["decisions"][0]["execution"]
            sell = evidence[21]["decisions"][0]["execution"]
            for fill, sign, reference in ((buy, D("1"), D("110.2")), (sell, D("-1"), D("110.6"))):
                price = reference*(1+sign*D("0.002"))
                self.assertEqual(D(fill["report"]["fill_price"]), price)
                quantity = D(fill["report"]["filled_quantity"])
                self.assertEqual(D(fill["fee"]), quantity*price*D("0.001"))
                self.assertEqual(D(fill["slippage_cost"]), quantity*reference*D("0.002"))
            self.assertEqual(closed["totals"]["trade_count"], 1)
            self.assertEqual(len(evidence[-1]["completed_trades"]), 1)
            self.assertNotEqual(opened["execution_id"], closed["execution_id"])
            self.assertEqual(D(closed["last_equity"]["equity"]),
                             D("20")+D(closed["totals"]["realized_pnl"]))
