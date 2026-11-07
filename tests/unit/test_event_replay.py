from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.event_replay import event_replay
from quantos.paper.journal import Journal
from quantos.paper.risk import Policy
from quantos.paper.runtime import Runtime, dispatch
from quantos.paper.strategies import DonchianAtrStopStrategy, EmaAtrLimitStrategy
from quantos.paper.types import D, Quote, Trade, dumps
from quantos.paper.types import Candle, DelistEvent
from tests.unit.test_v3_strategies import make_snapshot


class EventReplayTests(unittest.TestCase):
    def _quote(self, source, bid, ask, mark, now):
        return replace(source, time_ms=now, received_ms=now, bid=D(bid), ask=D(ask), mark=D(mark),
                       bid_qty=D("100"), ask_qty=D("100"))

    def _run_case(self, strategy):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture, original = root / "capture", root / "original.db"
            capture.mkdir()
            base = [D(100) + D(i) * D(".05") for i in range(80)]
            if isinstance(strategy, DonchianAtrStopStrategy):
                base[-1] += D(5)
            snap = make_snapshot(base)
            bars = tuple(replace(c, high=max(c.open, c.close) + D(3),
                                 low=min(c.open, c.close) - D(3)) for c in snap.candles_5m)
            now = snap.at_ms + 1
            close = snap.candles_5m[-1].close
            initial = self._quote(snap.quote, close - D(".05"), close + D(".05"), close, now)
            one_minute = tuple(Candle(now - (3-i)*60_000, now - (2-i)*60_000 - 1,
                                      close, close + D(".1"), close - D(".1"), close, D(10))
                               for i in range(3))
            announcement = DelistEvent("BTCUSDC", now + 86_400_000, now, "synthetic-1",
                                       "synthetic announcement", "https://example.test/event")
            snap = replace(snap, at_ms=now, quote=initial, candles_1m=one_minute,
                           candles_5m=bars, delist_events=(announcement,))
            proposal = strategy.evaluate(snap)
            self.assertIsNotNone(proposal)
            if proposal.entry_type == "LIMIT":
                fill_events = [("feed", [("quote", self._quote(initial, proposal.entry_price,
                               proposal.entry_price + D(".01"), proposal.entry_price, now + 1000)),
                               ("trade", Trade("BTCUSDC", 1, now + 1000,
                                                proposal.entry_price - D(".01"), D("1000"), True))])]
                exit_start = now + 2000
            else:
                trigger = self._quote(initial, proposal.entry_price - D(".01"),
                                      proposal.entry_price, proposal.entry_price - D(".01"), now + 1000)
                fill = self._quote(initial, proposal.entry_price, proposal.entry_price + D(".01"),
                                   proposal.entry_price, now + 2000)
                fill_events = [("feed", [("quote", trigger)]), ("feed", [("quote", fill)])]
                exit_start = now + 3000
            target = proposal.take_profit
            take_profit_quote = self._quote(initial, target, target + D(".01"), target, exit_start)
            final_quote = replace(take_profit_quote, time_ms=exit_start + 1000,
                                  received_ms=exit_start + 1000)
            rows = [(now, "restart", {}), (now, "feed", [("quote", initial)]),
                    (now, "scan", [snap]), *[(now + i * 1000, kind, payload)
                                             for i, (kind, payload) in enumerate(fill_events, 1)],
                    (exit_start, "feed", [("quote", take_profit_quote)]),
                    (exit_start + 1000, "feed", [("quote", final_quote)])]
            runtime = Runtime(capture)
            j = Journal(original, "p1", "natural")
            engine = PaperEngine(j, Policy(risk_fraction=D(".01")))
            engine.recover(now)
            try:
                for at, kind, payload in rows:
                    if kind == "restart":
                        runtime.record(kind, payload, at)
                        continue
                    if kind == "scan":
                        class FixedSentiment:
                            @staticmethod
                            def judge(signal, _now):
                                return {"signal_id": signal.signal_id, "verdict": "WOULD_ALLOW",
                                        "source": "synthetic"}
                        trace = {}
                        dispatch([engine], kind, payload, at, strategies=(strategy,),
                                 sentiment=FixedSentiment(), sentiment_trace=trace)
                        context = {"sentiment_enabled": True, "sentiment_fail_closed": False,
                                   "sentiment_verdicts": trace}
                        runtime.record(kind, payload, at, context=context)
                    else:
                        runtime.record(kind, payload, at)
                        dispatch([engine], kind, payload, at, strategies=(strategy,))
                orders = j.orders()
                self.assertEqual(len(orders), 1)
                self.assertEqual(orders[0]["signal"]["entry_type"], proposal.entry_type)
                self.assertEqual(orders[0]["state"], "CLOSED")
                expected_fills = [tuple(r) for r in j.db.execute(
                    "SELECT kind,quantity,price,fee,pnl FROM fills ORDER BY rowid")]
                self.assertEqual(len(expected_fills), 2)
                result = event_replay(sorted((capture / "market").glob("*.gz")), root / "out",
                                      profile="legacy", strategy=strategy.strategy_id,
                                      expected_journal=original)
                self.assertTrue(result["run"]["sentiment_trace_complete"])
                self.assertTrue(result["run"]["journal_parity"]["exact"],
                                result["run"]["journal_parity"])
                with sqlite3.connect(root / "out" / "journal_p1.db") as replay_db:
                    actual_fills = replay_db.execute(
                        "SELECT kind,quantity,price,fee,pnl FROM fills ORDER BY rowid").fetchall()
                    self.assertEqual(actual_fills, expected_fills)
                    self.assertEqual(replay_db.execute(
                        "SELECT payload FROM orders").fetchone()[0],
                        j.db.execute("SELECT payload FROM orders").fetchone()[0])
                self.assertTrue(result["run"]["profiles"] == ["p1", "p5", "pA"])
            finally:
                j.close()

    def test_replay_parity_for_limit_entry_depth_impact_and_exit(self):
        self._run_case(EmaAtrLimitStrategy())

    def test_replay_parity_for_stop_entry_depth_impact_and_exit(self):
        self._run_case(DonchianAtrStopStrategy())

    def test_rejects_non_runtime_recording_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "record.jsonl"
            path.write_text(dumps({"schema": 1, "received_ms": 1, "kind": "ignored"}) + "\n")
            with self.assertRaisesRegex(ValueError, "schema-2"):
                event_replay([path], root / "out", profile="legacy")
            self.assertTrue((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
