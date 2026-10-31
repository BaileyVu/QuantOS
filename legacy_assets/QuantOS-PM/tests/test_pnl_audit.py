"""Independent audit failure detection and time-integral evidence checks."""
import copy
from decimal import Decimal as D
import unittest

from quantos_pm.pnl_audit import audit_rows, read_rows


class PnlAuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = read_rows('43684903b25ad4ac9e49')

    def test_exact_cash_and_pnl_attribution_with_partial_pair_residual(self):
        result = audit_rows(self.rows, '500')
        s = result['summary']
        self.assertEqual(s['ending_equity'], D('500') - D('61.25') + D('52'))
        self.assertEqual(s['total_pnl'], D('1.04') - D('9.80') - D('.49'))
        self.assertEqual(s['orders_ever_partially_filled'], 7)
        self.assertEqual(s['partial_fill_events'], 9)
        self.assertEqual(s['orders_partially_filled_at_close'], 2)
        self.assertEqual(s['active_observation_seconds'], D('1806'))
        self.assertEqual(s['wall_observation_seconds'], D('432301'))
        self.assertEqual(s['seconds_with_unmatched_inventory'], D('602'))
        self.assertEqual(s['unrealized_pnl'], 0)
        self.assertEqual(s['combined_slippage_ops_admission_allowance'], D('.10'))

    def test_mutated_cash_checkpoint_fails_loudly(self):
        rows = copy.deepcopy(self.rows)
        row = next(r for r in rows if r['kind'] == 'event')
        row['body']['cash'] = '499.99'
        with self.assertRaisesRegex(AssertionError, 'cash'):
            audit_rows(rows, '500')

    def test_extra_fee_or_deleted_settlement_fails_loudly(self):
        rows = copy.deepcopy(self.rows)
        effects = [r['body']['effects'] for r in rows if r['kind'] == 'event']
        fill = next(e for group in effects for e in group if e['kind'] == 'virtual_fill')
        fill['fee'] = '.01'
        with self.assertRaisesRegex(AssertionError, 'cash'):
            audit_rows(rows, '500')
        rows = copy.deepcopy(self.rows)
        for r in rows:
            if r['kind'] == 'event':
                r['body']['effects'] = [e for e in r['body']['effects']
                    if not (e['kind'] == 'virtual_redemption' and D(e['pnl']) < 0)]
        with self.assertRaisesRegex(AssertionError, 'realized'):
            audit_rows(rows, '500')

    def test_missing_merge_cannot_be_hidden_by_final_report(self):
        rows = copy.deepcopy(self.rows)
        for r in rows:
            if r['kind'] == 'event':
                r['body']['effects'] = [e for e in r['body']['effects'] if e['kind'] != 'virtual_merge']
        with self.assertRaisesRegex(AssertionError, 'cash'):
            audit_rows(rows, '500')


if __name__ == '__main__':
    unittest.main()
