from pathlib import Path
import tempfile
import unittest

from quantos.paper.runtime import Runtime


class ConflationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Runtime(Path(self.tmp.name))

    def put(self, *items):
        for item in items: self.r.messages.put(item)

    def test_only_newest_quote_batch_survives_a_backlog(self):
        self.put(*[("feed", [("quote", i)]) for i in range(5)])
        self.assertEqual(self.r.next_message(), ("feed", [("quote", 4)]))
        self.assertEqual(self.r.next_message(0), ("tick", None))

    def test_other_messages_and_trade_batches_are_kept_in_order(self):
        self.put(("scan", 1), ("feed", [("quote", 1)]), ("feed", [("trade", 2), ("quote", 2)]),
                 ("funding", 3), ("feed", [("quote", 4)]))
        got = [self.r.next_message(0) for _ in range(4)]
        self.assertEqual(got, [("scan", 1), ("feed", [("trade", 2), ("quote", 2)]), ("funding", 3),
                               ("feed", [("quote", 4)])])

    def test_empty_queue_is_a_tick(self):
        self.assertEqual(self.r.next_message(0), ("tick", None))
