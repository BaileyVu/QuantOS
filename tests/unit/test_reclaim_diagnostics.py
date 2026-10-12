from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from quantos.domain.evaluation.reclaim_diagnostics import ReclaimDiagnostics
from quantos.interfaces.retail_edge import replay_capture
from tests.unit.test_reclaim import pipeline


class ReclaimDiagnosticsTests(unittest.TestCase):
    def test_window_range_and_timing_diagnostics_do_not_change_thresholds(self):
        d = ReclaimDiagnostics()
        d.observe_timing(D('0.9'), D('1652.4'))
        d.observe_timing(D('30.7'), D('20'))
        d.observe_timing(D('-1'), D('20'))
        d.observe_feature(1_000_000_000, None)
        d.observe_feature(2_000_000_000, D('59.999'))
        d.observe_feature(3_000_000_000, D('60'))
        d.observe_feature(7_000_000_000, D('80'))
        d.close_window()
        report = d.report()
        self.assertEqual(report['valid_window_count'], 2)
        self.assertEqual(report['usable_coverage_seconds'], 2)
        self.assertEqual(report['range_valid_evaluations'], 3)
        self.assertEqual(report['range_at_least_60bps'], 2)
        self.assertEqual(report['maximum_feed_latency_ms'], D('1652.4'))
        self.assertEqual(report['invalid_queue_clock'], 1)

    def test_qos06_timing_metrics_and_frozen_signal_replay_exactly(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'capture.jsonl'
            original, _ = pipeline(path, reliability=True)
            restored = replay_capture(path)
            self.assertTrue(restored['matches_capture'])
            self.assertEqual(restored['metrics']['decision_digest'], original['decision_digest'])
            reliability = original['market_reliability']
            self.assertGreater(reliability['valid_window_count'], 0)
            self.assertGreater(reliability['range_valid_evaluations'], 0)
            self.assertEqual(reliability['invalid_queue_clock'], 0)

    def test_qos06_observability_preserves_qos05_economic_outcome(self):
        with tempfile.TemporaryDirectory() as root:
            old,_ = pipeline(Path(root)/'qos05.jsonl')
            new,_ = pipeline(Path(root)/'qos06.jsonl', reliability=True)
            for key in ('candidate_funnel','orders_submitted','entry_fills','exit_fills',
                        'fees','realized_net_pnl','balance','ledger_digest'):
                self.assertEqual(new[key],old[key],key)
            self.assertIn('market_reliability',new)
            self.assertNotIn('market_reliability',old)
