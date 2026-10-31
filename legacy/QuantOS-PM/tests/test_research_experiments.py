"""Evidence-preserving policy diagnostics, not performance-selection tests."""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from quantos_pm.config import ROOT, load
from quantos_pm.domain.common import D, iso, utc_ms
from quantos_pm.research_experiments import (
    POLICIES, _make_engine, run_experiments,
)
from quantos_pm.storage import digest, read_report


class ResearchExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = [json.loads(line) for line in (ROOT / 'fixtures/btc_5m.jsonl').read_text().splitlines()]
        cls.calibration = json.loads((ROOT / 'fixtures/calibration.json').read_text())
        paths = [ROOT / 'fixtures/btc_5m.jsonl', ROOT / 'fixtures/calibration.json',
                 ROOT / 'evidence/comparison.json']
        for capital in (200, 350, 500):
            paths.append(ROOT / f'configs/paper_{capital}.toml')
        comparisons = json.loads((ROOT / 'evidence/comparison.json').read_text())
        for row in comparisons:
            paths.append(ROOT / 'artifacts/runs' / row['run_id'] / 'journal.sqlite')
            paths.append(ROOT / 'artifacts/runs' / row['run_id'] / 'report.json')
        cls.paths = paths
        cls.before = {str(path): digest(path.read_bytes()) for path in paths}
        # An importable experiment run must not write an output or alter inputs.
        with patch('pathlib.Path.write_text', side_effect=AssertionError('unexpected write')):
            cls.runs = run_experiments()

    def test_all_nine_historical_controls_match_every_original_report_field(self):
        controls = [row for row in self.runs if row['policy'] == 'control']
        self.assertEqual(len(controls), 9)
        self.assertTrue(all(row['diagnostics']['historical_control_equal'] for row in controls))
        self.assertEqual({row['policy'] for row in self.runs}, set(POLICIES))
        self.assertEqual(len(self.runs), 72)

    def test_original_reports_journals_configs_and_fixtures_unchanged(self):
        self.assertEqual(self.before, {str(path): digest(path.read_bytes()) for path in self.paths})

    def test_every_candidate_uses_identical_real_events_without_added_timers(self):
        for row in self.runs:
            with self.subTest(capital=row['capital'], model=row['model'], policy=row['policy']):
                events = [entry['body']['input'] for entry in row['rows'] if entry['kind'] == 'event']
                self.assertEqual(events, self.fixture)
                self.assertFalse(row['diagnostics']['heldout_available'])
                self.assertFalse(row['report']['edge_demonstrated'])
                self.assertEqual(row['rows'][-2]['kind'], 'end_of_capture')
                self.assertEqual(row['rows'][-1]['body'], row['report'])
                self.assertEqual(D(row['report']['ending_equity']),
                                 D(row['report']['starting_equity']) + D(row['report']['pnl_excluding_incentives']))

    def test_selective_cancel_retains_reservation_latency_and_racing_fill(self):
        engine = _make_engine(load('configs/paper_200.toml'), self.calibration, 'cancel_dominant')
        for event in self.fixture[:4]:
            engine.process(event)
        execution = engine.execution
        up = next(order for order in execution.orders.values() if order.outcome == 'UP')
        down = next(order for order in execution.orders.values() if order.outcome == 'DOWN')
        reserved = execution.reserved
        now = engine.now
        self.assertTrue(execution.cancel_one(up, now, 'unit_cancel', 'cancel_dominant'))
        self.assertEqual(up.cancel_at, now + 750)
        self.assertIsNone(down.cancel_at)
        self.assertEqual(execution.reserved, reserved)
        # Verified opposing trade 100ms into the original pessimistic delay.
        trade = dict(self.fixture[4], id='audit-unit-racing-fill',
                     event_time=iso(now + 90), receipt_time=iso(now + 100))
        engine.process(trade)
        self.assertEqual(execution.positions[up.market].qty['UP'], D('2'))
        self.assertEqual(up.status, 'CANCEL_PENDING')
        self.assertGreater(execution.reserved, 0)
        engine.process(dict(id='audit-unit-cancel-ack', type='timer',
                            event_time=iso(now + 990), receipt_time=iso(now + 1000)))
        self.assertEqual(up.status, 'CANCELED')
        self.assertTrue(down.pending)
        self.assertEqual(execution.reserved, down.remaining * down.price)

    def test_unmatched_deadline_never_invents_liquidation_or_holding_limit(self):
        for row in self.runs:
            if row['policy'] != 'exposure_deadline_5s':
                continue
            with self.subTest(capital=row['capital'], model=row['model']):
                diag = row['diagnostics']
                self.assertFalse(diag['guaranteed_holding_limit'])
                self.assertEqual(diag['max_observed_exposure_duration_ms'], 298000)
                self.assertGreater(diag['episodes_exceeding_5s'], 0)
                self.assertTrue(all(reaction['retained_inventory'] for reaction in diag['policy_reactions']))
                self.assertTrue(all(reaction['reaction_lateness_ms'] >= 0 for reaction in diag['policy_reactions']))
                effects = [effect for entry in row['rows'] if entry['kind'] == 'event'
                           for effect in entry['body']['effects']]
                losing_settlements = [effect for effect in effects
                                      if effect['kind'] == 'virtual_redemption' and D(effect['pnl']) < 0]
                self.assertTrue(losing_settlements)
                self.assertTrue(all(effect['kind'] != 'virtual_sell' for effect in effects))

    def test_all_skipped_threshold_is_explicitly_no_edge_evidence(self):
        for row in self.runs:
            if row['policy'] == 'combined_097':
                self.assertEqual(D(row['diagnostics']['markets_skipped_percent']), D(100))
                self.assertEqual(row['report']['orders']['quoted'], 0)
                self.assertIsNone(row['diagnostics']['net_per_entered_market'])
                self.assertFalse(row['diagnostics']['all_skipped_has_edge_evidence'])

    def test_historical_mismatch_is_raised_not_suppressed(self):
        def corrupted(run_id):
            report = read_report(run_id)
            report['ending_equity'] = '999999'
            return report
        with patch('quantos_pm.research_experiments.read_report', side_effect=corrupted):
            with self.assertRaisesRegex(AssertionError, 'original-control report mismatch'):
                run_experiments()


if __name__ == '__main__':
    unittest.main()
