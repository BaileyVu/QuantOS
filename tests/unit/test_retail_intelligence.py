from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import unittest

from quantos.domain.market_data.intelligence import IntelligenceEvent
from quantos.domain.features.intelligence import IntelligenceFeatureEngine
from quantos.infrastructure.public_data.retail import coinbase_ticker, liquidation_event
from tests.unit.test_hft_paper import at


def quote(ns=1_000_000_000, sequence=1, bid="100", ask="100.02"):
    return IntelligenceEvent("fixture", "BTC-USD", "BTC/USD:spot", "leader_quote",
        at(1), at(1), ns, ns, sequence, "synthetic://qos04", "monotone_snapshot", True,
        bid=D(bid), ask=D(ask))


class IntelligenceTests(unittest.TestCase):
    def test_causal_warmup_return_and_expiry(self):
        engine = IntelligenceFeatureEngine()
        self.assertEqual(engine.compute(0).leader_reason, "missing_leader")
        engine.ingest(quote())
        self.assertEqual(engine.compute(1_000_000_000).leader_reason, "leader_warmup_5s")
        engine.ingest(quote(6_000_000_000, 2, "101", "101.02"))
        self.assertGreater(engine.compute(6_000_000_000).leader_return_bps, 0)
        self.assertEqual(engine.compute(5_999_999_999).leader_reason, "not_yet_available")
        self.assertEqual(engine.compute(8_000_000_001).leader_reason, "stale_observation")

    def test_duplicate_regression_delayed_and_future_fail_closed(self):
        engine = IntelligenceFeatureEngine()
        self.assertEqual(engine.ingest(quote()), "valid")
        self.assertEqual(engine.ingest(quote()), "duplicate")
        self.assertEqual(engine.ingest(quote(2_000_000_000, 0)), "out_of_order_sequence")
        self.assertEqual(engine.compute(2_000_000_000).leader_reason, "missing_leader")
        delayed = replace(quote(3_000_000_000, 3), received_at=at(4))
        self.assertEqual(engine.ingest(delayed), "stale_observation")
        self.assertEqual(engine.ingest(replace(quote(4_000_000_000, 4), source_time=at(2))), "ambiguous_source_clock")

    def test_unavailable_and_unknown_integrity(self):
        self.assertEqual(replace(quote(), valid=False).freshness_reason(1_000_000_000), "provider_unavailable_or_invalid")
        self.assertEqual(replace(quote(), integrity="unknown").freshness_reason(1_000_000_000), "unverified_integrity")
        with self.assertRaises(ValueError):
            replace(quote(), available_ns=0)
        with self.assertRaises(ValueError):
            replace(quote(), bid=D("NaN"))

    def test_public_protocol_samples(self):
        item = coinbase_ticker({"type":"ticker", "product_id":"BTC-USD", "time":at(1).isoformat(),
            "sequence":12, "price":"100", "best_bid":"99", "best_ask":"101"}, at(1), 1)
        self.assertEqual(item.market, "BTC/USD:spot")
        row = {"e":"forceOrder", "E":1000, "o":{"s":"BTCUSDC", "S":"SELL", "T":1000, "ap":"100", "z":"2"}}
        item = liquidation_event(row, "BTCUSDC", at(1), 1)
        self.assertEqual(item.direction, -1)
        self.assertEqual(item.quantity, D("2"))
        self.assertEqual(item.integrity, "sampled_not_complete")
        with self.assertRaises(ValueError):
            liquidation_event(row, "BTCUSDT", at(1), 1)

class JournalTests(unittest.TestCase):
    def test_roundtrip_and_tamper_rejection(self):
        import tempfile
        from pathlib import Path
        from quantos.infrastructure.storage.retail_events import RetailEventJournal, read_journal
        from tests.unit.test_hft_paper import delta
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "capture.jsonl"
            journal = RetailEventJournal(path)
            journal.append("external", event=quote())
            journal.append("event", event=delta())
            journal.close()
            rows = list(read_journal(path))
            self.assertEqual(rows[0]["payload"]["event"], quote())
            self.assertEqual(rows[1]["payload"]["event"], delta())
            with self.assertRaises(FileExistsError):
                RetailEventJournal(path)
            path.write_text(path.read_text().replace('external', 'tampered'))
            with self.assertRaisesRegex(ValueError, 'digest'):
                list(read_journal(path))

    def test_capture_capacity_fails_closed(self):
        import tempfile
        from pathlib import Path
        from quantos.infrastructure.storage.retail_events import RetailEventJournal
        with tempfile.TemporaryDirectory() as root:
            journal = RetailEventJournal(Path(root) / "capture.jsonl", maximum_bytes=1)
            with self.assertRaisesRegex(RuntimeError, 'capacity'):
                journal.append("event", event=quote())
            journal.close()
