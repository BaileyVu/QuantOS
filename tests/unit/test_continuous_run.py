from pathlib import Path
import tempfile
import unittest

from quantos.paper.runtime import Runtime


class FakeMarket: pass


class DurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_live50_may_run_until_stopped(self):
        self.assertEqual(Runtime(Path(self.tmp.name), 87600, FakeMarket, portfolio="live50").hours, 87600)
        with self.assertRaises(ValueError): Runtime(Path(self.tmp.name), 87601, FakeMarket, portfolio="live50")

    def test_other_portfolios_stay_at_72_hours(self):
        with self.assertRaises(ValueError): Runtime(Path(self.tmp.name), 73, FakeMarket)
        self.assertEqual(Runtime(Path(self.tmp.name), 72, FakeMarket).hours, 72)
