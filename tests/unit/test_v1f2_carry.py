import unittest
import contextlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
from research.v1f1.factor import Series, HOUR
from research.v1f2.carry import Signal, observations, choose_side, plan, fill_cost, simulate, signals, replacement_economic
from research.v1f2 import evaluate


def fixture(name, n=850):
    p = np.tile([100., 101., 99., 100., 1., 100., 50.], (n, 1))
    f = np.array([[h*HOUR, 8., 0.] for h in range(0, n, 8)])
    return Series(name, 0, p, p.copy(), p.copy(), f)


def scores():
    return {f'S{i:02}': Signal(i/19, .0001*(10-i)) for i in range(20)}


class CarryTests(unittest.TestCase):
    def test_funding_and_basis_observations_are_causal(self):
        s = fixture('A'); s.funding[:, 2] = .0001
        original = observations(s, 720)
        s.perp[720:] = 999; s.index[720:] = 999; s.mark[720:] = 999
        s.funding[s.funding[:, 0] >= 720*HOUR, 2] = 999
        now = observations(s, 720)
        np.testing.assert_array_equal(original[0], now[0])
        self.assertEqual(original[1], now[1])
        self.assertAlmostEqual(now[0][0], -.0001)

    def test_missing_history_and_settlement_gap_exclude(self):
        s = fixture('A'); s.mark[700] = np.nan
        self.assertIsNone(observations(s, 720))
        s = fixture('A'); s.funding = np.delete(s.funding, 80, axis=0)
        self.assertIsNone(observations(s, 720))

    def test_hysteresis_retains_in_band_and_rejects_uneconomic_switch(self):
        x = scores()
        self.assertEqual(choose_side(x, ['S15', 'S16'], 1), ['S15', 'S16'])
        flat = {s: Signal(v.score, 0.) for s, v in x.items()}
        self.assertEqual(choose_side(flat, ['S00', 'S01'], 1), ['S00', 'S01'])
        self.assertEqual(choose_side(x, ['S00', 'S01'], 1), ['S19', 'S18'])

    def test_replacement_threshold_is_strict(self):
        x = {s: Signal(v.score, 0.) for s, v in scores().items()}
        x['S00'] = Signal(0., .006/21)
        self.assertEqual(choose_side(x, ['S00', 'S18'], 1), ['S00', 'S18'])

    def test_beta_hedges_and_gross_one(self):
        x = scores(); beta = {s: 2. for s in x}
        for variant in ('A', 'B', 'C'):
            longs, shorts, weights, why = plan(x, beta, [], [], variant)
            self.assertEqual(len(longs), 2)
            self.assertAlmostEqual(sum(abs(v) for v in weights.values()), 1.)
            if variant == 'B': self.assertAlmostEqual(weights['BTCUSDT'], -2/3)
            if variant == 'C':
                self.assertAlmostEqual(weights['BTCUSDT'], -1/3)
                self.assertAlmostEqual(weights['ETHUSDT'], -1/3)

    def test_missing_held_eligibility_flattens_coordinated_basket(self):
        self.assertEqual(plan(scores(), {}, ['MISSING', 'S19'], ['S00', 'S01'], 'A')[2], {})

    def test_delta_costs_and_no_forced_reopening(self):
        panel = {s: fixture(s) for s in scores()}
        cache = {h: (scores(), {}) for h in range(720, 800, 8)}
        r = simulate(panel, cache, 'A', 720, 800, 5, 2)
        self.assertEqual(len(r['fills']), 8)  # four entries plus four final exits
        self.assertEqual(r['economic_changes'], 2)
        self.assertEqual(r['holding_hours'], [78]*4)
        self.assertLess(r['totals']['turnover'], 2.01)
        t = r['totals']
        self.assertAlmostEqual(r['equity'][-1]-1, t['price']+t['funding']-t['fees']-t['slippage'])
        self.assertAlmostEqual(r['equity'][-1]-1, t['long']+t['short'])

    def test_funding_sign_and_entry_exclusion(self):
        panel = {s: fixture(s) for s in scores()}
        panel['S19'].funding[:, 2] = .001
        cache = {720: (scores(), {})}
        r = simulate(panel, cache, 'A', 720, 730, 0, 0)
        self.assertAlmostEqual(r['totals']['funding'], -.00025)  # event728 only

    def test_data_loss_closes_every_leg(self):
        panel = {s: fixture(s) for s in scores()}; panel['S19'].mark[725] = np.nan
        r = simulate(panel, {720: (scores(), {})}, 'A', 720, 730, 5, 2)
        self.assertEqual(r['data_loss_exits'], 1)
        self.assertEqual(sum(f['reason']=='data_loss' for f in r['fills']), 4)
        self.assertGreater(r['totals']['slippage'], .001)

    def test_failed_leg_entry_leaves_whole_basket_flat(self):
        panel = {s: fixture(s) for s in scores()}; panel['S19'].perp[721] = np.nan
        r = simulate(panel, {720: (scores(), {})}, 'A', 720, 730, 5, 2)
        self.assertEqual(r['failed_baskets'], 1)
        self.assertEqual(r['fills'], [])
        np.testing.assert_array_equal(r['equity'], 1.)

    def test_costs_use_signed_adverse_fill_notional(self):
        fee, slip = fill_cost(-2, 100, 5, 10)
        self.assertAlmostEqual(fee, 199.8*.0005)
        self.assertAlmostEqual(slip, .2)

    def test_beta_unavailable_rejects_hedged_portfolio(self):
        panel = {s: fixture(s) for s in list(scores())+['BTCUSDT','ETHUSDT']}
        self.assertEqual(signals(panel, list(scores()), 720, 'B'), ({}, {}))

    def test_beta_estimation_is_trailing_and_not_future_dependent(self):
        panel = {s: fixture(s) for s in list(scores())+['BTCUSDT','ETHUSDT']}
        moves = np.sin(np.arange(850)/5)*.001
        for i, (symbol, s) in enumerate(panel.items()):
            beta = 1. if symbol in ('BTCUSDT','ETHUSDT') else .5+i*.05
            prices = 100*np.exp(np.cumsum(moves)*beta)
            for a in (s.perp, s.index, s.mark):
                a[:, 0] = a[:, 3] = prices; a[:, 1] = prices*1.01; a[:, 2] = prices*.99
        before = signals(panel, list(scores()), 720, 'B')
        self.assertEqual(len(before[0]), 20)
        self.assertAlmostEqual(before[1]['S00'], .5)
        for s in panel.values(): s.perp[720:, 3] = 1e8
        self.assertEqual(before, signals(panel, list(scores()), 720, 'B'))

    def test_gross_increase_reduces_without_full_close_reopen(self):
        panel = {s: fixture(s) for s in scores()}
        # One short suffers a jump; prospective risk reduction affects deltas.
        panel['S00'].perp[725:, 0:4] *= 1.2
        panel['S00'].mark[725:, 0:4] *= 1.2
        cache = {h: (scores(), {}) for h in range(720, 760, 8)}
        r = simulate(panel, cache, 'A', 720, 760, 5, 2)
        cuts = [x for x in r['fills'] if x['reason']=='gross_cap']
        self.assertTrue(cuts)
        self.assertTrue(all(x['notional'] < .1 for x in cuts))
        self.assertEqual(len(r['holding_hours']), 4)

    def test_flat_score_cannot_initiate(self):
        x = {s: Signal(.5, 0.) for s in scores()}
        self.assertEqual(plan(x, {}, [], [], 'A')[2], {})

    def test_whole_portfolio_switch_accounts_for_hedge_funding(self):
        panel = {s: fixture(s) for s in ['OLD','NEW','BTCUSDT']}
        x = {'OLD':Signal(0., 0.), 'NEW':Signal(1., -.001)}
        q = {'OLD':.005, 'BTCUSDT':-.005}
        weights = {'NEW':.5, 'BTCUSDT':-.5}
        self.assertTrue(replacement_economic(panel, x, 720, q, weights, 1.))
        # Doubling an expensive negative-funding hedge erases the long advantage.
        panel['BTCUSDT'].funding[:, 2] = -.01
        weights = {'NEW':.25, 'BTCUSDT':-.75}
        self.assertFalse(replacement_economic(panel, x, 720, q, weights, 1.))

    def test_one_shot_and_holdout_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = {'test_years':[2021,2022,2023,2024], 'variants':['A','B','C']}
            evaluate.authorize(root, config)
            with self.assertRaises(ValueError):
                evaluate.authorize(root, {**config, 'test_years':[2025]})
            with self.assertRaises(ValueError):
                evaluate.authorize(root, {**config, 'variants':['A','B','C','D']})
            (root/'economic.started.json').write_text('{}')
            with self.assertRaises(ValueError): evaluate.authorize(root, config)

    def test_complete_synthetic_report_export_and_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'out'; root.mkdir(); data = Path(tmp)/'data'; data.mkdir()
            (root/'preregistration.lock.json').write_text('{}')
            config = json.loads(Path('configs/v1f2_preregistration.json').read_text())
            benchmark = data/config['benchmark_report']; benchmark.parent.mkdir()
            benchmark.write_text(json.dumps({'results':[{'configuration':'basis-24h-2x2',
                'fee_bps':fee, 'slippage_bps':slip, 'turnover_starting_equity_units':100.}
                for _, fee, slip in config['costs']]}))
            panel = {s: fixture(s, 1201) for s in list(scores())+['BTCUSDT','ETHUSDT']}
            cache = {t:(scores(), {s:1. for s in scores()}) for t in range(720,1200,8)}
            with patch.object(evaluate, 'inputs', return_value=(panel,{},{})), \
                 patch.object(evaluate, 'verify', return_value={}), \
                 patch.object(evaluate, 'hour', side_effect=lambda y:720+(y-2021)*120), \
                 patch.object(evaluate, 'cache_signals', return_value=cache), \
                 patch('sys.argv', ['evaluate','--root',str(root),'--data',str(data)]), \
                 contextlib.redirect_stdout(io.StringIO()):
                evaluate.main()
                with self.assertRaises(ValueError): evaluate.main()
            report = json.loads(next((root/'evaluation').glob('development-*.json')).read_text())
            self.assertEqual(len(report['results']), 9)
            self.assertEqual(report['status'], 'NO_GO_ALPHA')
            self.assertFalse(report['holdouts_accessed'])
            for row in report['results']:
                self.assertEqual(row['fills'], 8*4 if row['variant']!='B' else 6*4)
                self.assertEqual(len(row['folds']), 4)


if __name__ == '__main__': unittest.main()
