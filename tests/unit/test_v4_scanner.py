from dataclasses import replace
from decimal import Decimal
from contextlib import nullcontext
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.announcements import AnnouncementDiagnostic, AnnouncementPoll
from quantos.paper.market import RateLimited
from quantos.paper.runtime import Runtime
from quantos.paper.types import Candle, D, Quote
from tests.unit.test_v3_engine import INS


WIDTHS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000,
          "4h": 14_400_000, "1d": 86_400_000}


class FakeClock:
    def __init__(self, now=300_000):
        self.now = now


class StopAfterWaits:
    def __init__(self, clock, waits, advance_ms=0):
        self.clock, self.waits, self.advance_ms = clock, waits, advance_ms
        self.count = 0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, _timeout=None):
        self.count += 1
        if self.count == 1:
            self.clock.now += self.advance_ms
        if self.count >= self.waits:
            self.stopped = True
        return self.stopped


class ScannerMarket:
    def __init__(self, clock, symbols):
        self.clock = clock
        self.universe = {symbol: replace(INS, symbol=symbol, base_asset=symbol[:-4])
                         for symbol in symbols}
        self.universe_calls = 0
        self.candle_calls = []
        self.quote_calls = []
        self.trade_symbols = []
        self.shutdown_on_quote = None

    def server_time(self):
        return self.clock.now

    def eligible_instruments(self, _floor):
        self.universe_calls += 1
        return self.universe

    def candles(self, symbol, interval, now_ms, limit=100):
        self.candle_calls.append((symbol, interval, limit))
        width = WIDTHS[interval]
        latest_close = now_ms // width * width - 1
        first_open = latest_close - limit * width + 1
        return tuple(Candle(first_open + i * width, first_open + (i + 1) * width - 1,
                            D(100), D(101), D(99), D(100), D(1))
                     for i in range(limit))

    def quotes(self, symbols, *, skip_invalid=False):
        self.quote_calls.append((tuple(symbols), skip_invalid, self.clock.now))
        # BTC is fresh and narrow; ETH is fresh but too wide; XRP is missing as if stale.
        return {
            "BTCUSDC": Quote("BTCUSDC", self.clock.now, self.clock.now,
                             D(100), D("100.1"), D(1), D(1), D("100.05"), self.clock.now),
            "ETHUSDC": Quote("ETHUSDC", self.clock.now, self.clock.now,
                             D(100), D("100.5"), D(1), D(1), D("100.2"), self.clock.now),
            "HELDUSDC": Quote("HELDUSDC", self.clock.now, self.clock.now,
                              D(100), D("100.1"), D(1), D(1), D("100.05"), self.clock.now),
        }

    def trades(self, symbol, **_kwargs):
        self.trade_symbols.append(symbol)
        return ()

    def funding(self, *_args):
        return ()

    def quote(self, symbol):
        if self.shutdown_on_quote:
            self.shutdown_on_quote.set()
        return Quote(symbol, self.clock.now, self.clock.now, D(100), D("100.1"),
                     D(1), D(1), D("100.05"), self.clock.now)


class AnnouncementStub:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def poll(self, instruments):
        self.calls.append((self.clock.now, tuple(instruments)))
        if len(self.calls) == 1:
            raise RateLimited(240)
        diagnostic = AnnouncementDiagnostic(
            "SKIPPED_NO_USDC_PERP", self.clock.now, self.clock.now, "article-1",
            "Binance Will Delist ABSENT", "ABSENT")
        return AnnouncementPoll((), (diagnostic,))


class ScannerTests(unittest.TestCase):
    def test_shutdown_does_not_drain_queued_universe_requests(self):
        clock=FakeClock()
        market=ScannerMarket(clock,[f'COIN{i}USDC' for i in range(20)])
        with tempfile.TemporaryDirectory() as folder:
            runtime=Runtime(Path(folder),market_factory=lambda:market,announcement_factory=lambda:None)
            original=market.candles
            def stopped(*args,**kwargs):
                runtime.shutdown.set()
                return original(*args,**kwargs)
            market.candles=stopped
            with patch('quantos.paper.runtime.milliseconds',side_effect=lambda:clock.now):
                runtime.scanner()
            self.assertLessEqual(len(market.candle_calls),4)
            self.assertEqual(market.quote_calls,[])

    def messages(self, runtime):
        out = []
        while True:
            try:
                out.append(runtime.messages.get_nowait())
            except queue.Empty:
                return out

    def test_scanner_reuses_and_refreshes_bars_batches_fresh_quotes_and_defers_cms(self):
        clock = FakeClock(400_000)
        market = ScannerMarket(clock, ["BTCUSDC", "ETHUSDC", "XRPUSDC"])
        announcements = AnnouncementStub(clock)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory), market_factory=lambda: market,
                              announcement_factory=lambda: announcements)
            held = replace(INS, symbol="HELDUSDC", base_asset="HELD")
            runtime.instruments = {"HELDUSDC": held}
            runtime.watched_symbols.add("HELDUSDC")
            runtime.shutdown = StopAfterWaits(clock, waits=2, advance_ms=240_000)
            with patch("quantos.paper.runtime.milliseconds", side_effect=lambda: clock.now):
                runtime.scanner()

            self.assertEqual(market.universe_calls, 1)
            self.assertEqual([call[0] for call in announcements.calls], [400_000, 640_000])
            self.assertEqual(len(market.quote_calls), 2)
            self.assertTrue(all(set(call[0]) == {"BTCUSDC", "ETHUSDC", "XRPUSDC", "HELDUSDC"} and call[1]
                                for call in market.quote_calls))
            # 1m/5m bars cross a boundary and refresh; larger frames reuse their cached close.
            calls_by_frame = {frame: sum(call[1] == frame for call in market.candle_calls)
                              for frame in WIDTHS}
            self.assertEqual(calls_by_frame, {"1m": 8, "5m": 8, "1h": 4, "4h": 4, "1d": 4})
            self.assertIn("HELDUSDC", runtime.instruments)
            self.assertEqual(set(runtime.instruments), {"BTCUSDC", "ETHUSDC", "XRPUSDC", "HELDUSDC"})

            messages = self.messages(runtime)
            scans = [payload for kind, payload in messages if kind == "scan"]
            self.assertEqual(len(scans), 2)
            self.assertTrue(all({snapshot.instrument.symbol for snapshot in scan} ==
                                {"BTCUSDC", "ETHUSDC", "HELDUSDC"} for scan in scans))
            for scan in scans:
                eligibility = {snapshot.instrument.symbol: snapshot.entry_eligible for snapshot in scan}
                self.assertTrue(eligibility["BTCUSDC"])
                self.assertFalse(eligibility["ETHUSDC"])
                self.assertFalse(eligibility["HELDUSDC"])
            skipped = [payload for kind, payload in messages if kind == "diagnostic"
                       and payload.get("status") == "SKIPPED_MARKET_QUALITY"]
            self.assertEqual({item["symbol"] for item in skipped}, {"ETHUSDC", "XRPUSDC", "HELDUSDC"})
            cms = [payload for kind, payload in messages if kind == "diagnostic"
                   and payload.get("status") == "ANNOUNCEMENT_UNAVAILABLE"]
            self.assertEqual(len(cms), 1)
            self.assertTrue(any(kind == "diagnostic" and payload.get("status") == "SKIPPED_NO_USDC_PERP"
                                for kind, payload in messages))

            class ReadOnlyJournalStub:
                def transaction(self): return nullcontext()
                def event(self, *_args): pass

            class RecordingEngine:
                def __init__(self):
                    self.journal, self.adaptive, self.quotes = ReadOnlyJournalStub(), None, {}
                    self.maintained, self.proposed = [], []
                def channel_stops(self, snapshot, _now):
                    self.maintained.append(snapshot.instrument.symbol)
                def propose(self, proposal, *_args): self.proposed.append(proposal)

            class SignalOnHeld:
                strategy_id = "test"
                def evaluate(self, snapshot):
                    if snapshot.instrument.symbol == "HELDUSDC":
                        return object()
                    return None

            from quantos.paper.runtime import dispatch
            engine = RecordingEngine()
            dispatch([engine], "scan", scans[-1], clock.now, strategies=(SignalOnHeld(),))
            self.assertIn("HELDUSDC", engine.maintained)
            self.assertEqual(engine.proposed, [])

    def test_feed_can_subscribe_to_a_held_symbol_retained_outside_current_universe(self):
        clock = FakeClock(500_000)
        market = ScannerMarket(clock, ["BTCUSDC"])
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory), market_factory=lambda: market)
            held = replace(INS, symbol="HELDUSDC", base_asset="HELD")
            runtime.instruments = {"BTCUSDC": market.universe["BTCUSDC"], "HELDUSDC": held}
            runtime.watched_symbols = {"BTCUSDC", "ETHUSDC", "HELDUSDC"}
            stop = StopAfterWaits(clock, waits=1)
            runtime.shutdown = stop
            market.shutdown_on_quote = stop
            with patch("quantos.paper.runtime.milliseconds", side_effect=lambda: clock.now):
                runtime.feed(0, "HELDUSDC")
            self.assertEqual(market.trade_symbols, ["HELDUSDC"])
            kind, records = runtime.messages.get_nowait()
            self.assertEqual(kind, "feed")
            self.assertEqual(records[0][0], "quote")
            self.assertEqual(records[0][1].symbol, "HELDUSDC")


if __name__ == "__main__":
    unittest.main()
