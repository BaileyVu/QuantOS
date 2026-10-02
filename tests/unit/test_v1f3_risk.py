import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from research.v1f1.factor import HOUR
from research.v1f2.carry import simulate, fill_cost
from research.v1f3.risk import Account, IntentTape, throttle, clusters, snapshot, scale_for, run_overlay
from research.v1f3.evaluate import candidate_grid, gate, selection_key, frozen_alpha, verify
from tests.unit.test_v1f2_carry import fixture, scores


def risk_state():
    return dict(names=['S19','S18','S00','S01'],weights=np.array([.25,.25,-.25,-.25]),
                covariance=np.eye(4),vol=.5,contribution=np.ones(4)*.125,
                cluster_gross=.25,beta=0.,carry=.2,carry_risk=.4)


class RiskTests(unittest.TestCase):
    def test_throttle_boundaries_and_lock_band(self):
        self.assertEqual([throttle(x,'moderate') for x in [0,.1,.2,.3,.35,.8]],[1,.75,.5,.25,0,0])
        self.assertEqual([throttle(x,'defensive') for x in [0,.1,.2,.3,.35]],[1,.5,.25,.1,0])

    def test_correlation_clusters_are_transitive_and_include_negative(self):
        c=np.array([[1,.81,0,0],[.81,1,-.9,0],[0,-.9,1,0],[0,0,0,1]])
        self.assertEqual(clusters(c),[{0,1,2},{3}])

    def test_uniform_scale_enforces_each_independent_cap(self):
        s=risk_state()
        self.assertAlmostEqual(scale_for(s,.2,0,0,'moderate'),.4)
        for key,value,maximum in [('vol',4.,.05),('contribution',np.ones(4)*2,.04),
                                  ('cluster_gross',4.,.125),('beta',3.,.05)]:
            x=copy.deepcopy(s);x[key]=value
            self.assertLessEqual(scale_for(x,.2,0,0,'moderate'),maximum+1e-12)
        x=copy.deepcopy(s);x['weights']=np.array([2.,.2,-.1,-.1])
        self.assertLessEqual(scale_for(x,.2,0,0,'moderate'),.05+1e-12)

    def test_carry_veto_and_missing_inputs_flatten(self):
        s=risk_state();self.assertEqual(scale_for(None,.2,0,0,'moderate'),0)
        self.assertEqual(scale_for(s,.2,.5,0,'moderate'),0)
        s['carry']=0;self.assertEqual(scale_for(s,.2,0,0,'moderate'),0)

    def test_covariance_carry_and_beta_are_strictly_trailing(self):
        panel={s:fixture(s,1000) for s in ['A','B','C','D','BTCUSDT']}
        rng=np.random.default_rng(123)
        for i,s in enumerate(panel.values()):
            prices=100*np.exp(np.cumsum(rng.normal(0,.005,1000)))
            s.perp[:,:4]=prices[:,None];s.mark[:,:4]=prices[:,None];s.index[:,:4]=prices[:,None]
            s.funding[:,2]=.0001*(i-2)
        q={'A':.002,'B':.002,'C':-.002,'D':-.002}
        before=snapshot(panel,q,801,1.)
        self.assertIsNotNone(before)
        for s in panel.values():
            s.perp[800:,3]=99999;s.mark[800:,3]=99999;s.index[800:,3]=99999
            s.funding[s.funding[:,0]>=800*HOUR,2]=123
        after=snapshot(panel,q,801,1.)
        for k in ['vol','beta','carry','carry_risk']:self.assertEqual(before[k],after[k])
        np.testing.assert_array_equal(before['covariance'],after['covariance'])

    def test_intent_tape_exposes_no_future_and_rejects_time_reversal(self):
        tape=IntentTape([dict(hour=1,symbol='A',delta=2),dict(hour=5,symbol='A',delta=-2)])
        self.assertEqual(tape.at(0),{});self.assertEqual(tape.at(1),{'A':2})
        self.assertEqual(tape.at(4),{'A':2});self.assertEqual(tape.at(5),{})
        with self.assertRaises(ValueError):tape.at(4)

    def test_account_matches_frozen_f2_on_identical_intents(self):
        panel={s:fixture(s) for s in scores()}
        panel['S19'].funding[:,2]=.0001;panel['S00'].funding[:,2]=.0002
        for s in panel.values():s.perp[750:,:4]*=1.01;s.mark[750:,:4]*=1.01
        r=simulate(panel,{h:(scores(),{}) for h in range(720,800,8)},'A',720,800,5,10)
        account=Account();tape=IntentTape(r['fills'])
        for h in range(720,800):
            account.advance(panel,h,5,10);q=tape.at(h)
            account.trade(q,{s:panel[s].perp[h,0] for s in set(q)|set(account.q)},h,5,10,'replay')
        self.assertAlmostEqual(account.cash,r['equity'][-1],places=12)
        for k in account.totals:self.assertAlmostEqual(account.totals[k],r['totals'][k],places=12)

    def test_data_loss_excludes_boundary_funding_and_uses_stress_exit(self):
        s=fixture('A');s.funding[:,2]=.01;s.mark[728]=np.nan
        a=Account();a.trade({'A':.01},{'A':100},727,5,2,'entry')
        self.assertTrue(a.advance({'A':s},728,5,2));self.assertEqual(a.data_loss,1)
        self.assertEqual(a.totals['funding'],0);self.assertEqual(a.q,{})
        self.assertAlmostEqual(a.fills[-1]['slippage'],.001)

    def test_resizes_preserve_spells_and_charge_only_delta(self):
        a=Account();a.trade({'A':.01},{'A':100},1,5,10,'entry')
        a.trade({'A':.008},{'A':100},2,5,10,'resize')
        a.trade({}, {'A':100},11,5,10,'exit')
        self.assertEqual(a.durations,[10])
        self.assertAlmostEqual(sum(f['notional'] for f in a.fills),2.)
        self.assertAlmostEqual(a.totals['fees'],sum(fill_cost(d,100,5,10)[0] for d in [.01,-.002,-.008]))

    def overlay_fixture(self):
        panel={s:fixture(s,900) for s in scores()}
        reference=simulate(panel,{h:(scores(),{}) for h in range(800,848,8)},'A',800,848,5,10)
        states={h:risk_state() for h in range(801,848,8)}
        candidate=dict(vol_target=.2,throttle='moderate',carry_threshold=0)
        return panel,[(2021,800,848,reference)],states,candidate

    def test_overlay_cost_accounting_and_caps(self):
        args=self.overlay_fixture();r=run_overlay(*args,5,10)
        t=r['totals'];self.assertEqual(r['rule_violations'],0)
        self.assertLess(r['max_gross'],1.)
        self.assertAlmostEqual(r['equity'][-1]-1,t['price']+t['funding']-t['fees']-t['slippage'])
        self.assertEqual(r['changes'],len({f['hour'] for f in r['fills']}))
        self.assertEqual(len(r['holding_hours']),4)

    def test_mark_basis_cost_reserve_does_not_breach_caps(self):
        panel,refs,states,c=self.overlay_fixture()
        panel['S19'].mark[:,0]*=.95;panel['S00'].mark[:,0]*=1.05
        r=run_overlay(panel,refs,states,c,5,10)
        self.assertEqual(r['rule_violations'],0)

    def test_price_gap_records_drawdown_and_locks(self):
        panel,refs,states,c=self.overlay_fixture()
        for s in ['S19','S18']:panel[s].perp[802:,:4]*=.01;panel[s].mark[802:,:4]*=.01
        for s in ['S00','S01']:panel[s].perp[802:,:4]*=2;panel[s].mark[802:,:4]*=2
        r=run_overlay(panel,refs,states,c,5,10)
        self.assertTrue(r['locked']);self.assertGreater(r['max_drawdown'],.35)
        self.assertTrue(all(f['hour']<=802 for f in r['fills']))

    def test_alpha_and_bounded_configuration(self):
        repo=Path(__file__).resolve().parents[2];frozen_alpha(repo)
        config=json.loads((repo/'configs/v1f3_preregistration.json').read_text())
        self.assertEqual(len(candidate_grid(config)),12)
        config['development_years'].append(2025)
        with self.assertRaises(ValueError):candidate_grid(config)

    def test_seal_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);repo=Path(__file__).resolve().parents[2]
            (root/'preregistration.lock.json').write_text(json.dumps(dict(source_files={'research/v1f3/risk.py':'wrong'},external_files={})))
            with self.assertRaisesRegex(ValueError,'sealed source changed'):verify(root,repo)


if __name__=='__main__':unittest.main()
