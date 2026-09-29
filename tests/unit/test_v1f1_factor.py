"""Synthetic fixtures exercise accounting/causality; never economic evidence."""
import io
import unittest
import zipfile
import json
from pathlib import Path
import tempfile
import numpy as np
from research.v1f1.factor import Series,features,select,rank01,funding_pnl,leg_account,simulate,HOUR
from research.v1f1.prepare import trailing_sum
from research.v1f1.dataset import parse
from research.v1f1.dataset import digest
from research.v1f1.evaluate import metrics,bootstrap_lower,verify_seal,authorize_development,encode_report

def fixture(symbol='TESTUSDT',n=800):
    p=np.tile([100.,101.,99.,100.,1.,100.,50.],(n,1))
    fund=np.array([[i*HOUR,8.,.001] for i in range(0,n,8)])
    return Series(symbol,0,p,p.copy(),p.copy(),fund)

class FactorTests(unittest.TestCase):
    def test_trailing_liquidity_excludes_decision_and_future(self):
        q=np.arange(10,dtype=float)
        before=trailing_sum(q,3)[5]
        q[5:]=1e9
        self.assertEqual(before,trailing_sum(q,3)[5])
        self.assertEqual(before,9.)
        q[3]=np.nan
        self.assertTrue(np.isnan(trailing_sum(q,3)[5]))

    def test_feature_basis_causal_funding_and_future_independence(self):
        s=fixture();s.index[719,3]=102
        expected=features(s,720).copy()
        self.assertAlmostEqual(expected[0],.02)
        s.perp[720:]=999999;s.index[720:]=999999;s.mark[720:]=999999
        s.funding[s.funding[:,0]>=720*HOUR,2]=100
        np.testing.assert_array_equal(expected,features(s,720))
        self.assertAlmostEqual(expected[1],-.001)

    def test_missing_history_excludes_only_affected_symbol(self):
        s=fixture();s.perp[710,0]=np.nan
        self.assertIsNone(features(s,720))
        self.assertIsNotNone(features(fixture(),720))

    def test_long_short_funding_and_transaction_costs(self):
        self.assertAlmostEqual(funding_pnl(2,.001,100),-.2)
        self.assertAlmostEqual(funding_pnl(-2,.001,100),.2)
        p,f,s,t=leg_account(2,100,110,5,2,2)
        self.assertEqual(p,20)
        self.assertAlmostEqual(s,.084)
        self.assertAlmostEqual(f,(100.02+109.978)*2*.0005)
        self.assertEqual(t,420)
        self.assertEqual(leg_account(-2,100,110,5,2,2)[0],-20)

    def test_funding_events_count_once_at_actual_timestamp(self):
        a,b=fixture('A'),fixture('B');b.funding[:,2]=0
        result=simulate({'A':a,'B':b},[('A',.5),('B',-.5)],719,8,0,0)
        # Entry at hour720: funding at720 excluded; event728 included at exit.
        self.assertAlmostEqual(result['funding'],-.0005)
        self.assertAlmostEqual(result['net'],-.0005)

    def test_coordinated_data_loss_and_partial_entry(self):
        a,b=fixture('A'),fixture('B');a.funding[:,2]=0;b.funding[:,2]=0
        b.mark[724]=np.nan
        result=simulate({'A':a,'B':b},[('A',.5),('B',-.5)],719,8,5,2)
        self.assertTrue(result['data_loss'])
        self.assertAlmostEqual(result['slippage'],.0012)
        self.assertEqual(result['price'],0)
        self.assertEqual(len(result['legs']),2)
        b.perp[720,0]=np.nan
        result=simulate({'A':a,'B':b},[('A',.5),('B',-.5)],719,8,5,2)
        self.assertTrue(result['entry_failed']);self.assertEqual(result['net'],0)

    def test_rank_ties_and_neutral_selection(self):
        np.testing.assert_allclose(rank01([1,1,3]),[.25,.25,1])
        series={f'S{i:02}':fixture(f'S{i:02}') for i in range(16)}
        for i,s in enumerate(series.values()):s.index[:,3]+=i*.01
        portfolio,count=select(list(series),series,720,'basis',2)
        self.assertEqual(count,16);self.assertEqual(len(portfolio),4)
        self.assertEqual(sum(w for _,w in portfolio),0)
        self.assertEqual(sum(abs(w) for _,w in portfolio),1)
        self.assertEqual(portfolio[0][0],'S15')

    def test_replay_is_identical_and_does_not_mutate_inputs(self):
        a,b=fixture('A'),fixture('B');original=a.perp.copy()
        kwargs=({'A':a,'B':b},[('A',.5),('B',-.5)],719,8,7,10)
        x,y=simulate(*kwargs),simulate(*kwargs)
        np.testing.assert_array_equal(x.pop('curve'),y.pop('curve'))
        self.assertEqual(x,y);np.testing.assert_array_equal(original,a.perp)

    def test_bad_hour_is_quarantined_without_filling(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('fixture.csv','1577836800000,10,11,9,10,2,1577840399999,20,4,1,10,0\n1577840400000,10,8,9,10,2,1577843999999,20,4,1,10,0\n')
        table,bad=parse(data.getvalue(),'klines')
        self.assertEqual(len(table),1);self.assertEqual(bad,1)

    def test_components_reconcile_to_equity_and_direction(self):
        a,b=fixture('A'),fixture('B');a.perp[728,0]=110;b.perp[728,0]=95
        r=simulate({'A':a,'B':b},[('A',.5),('B',-.5)],719,8,7,10)
        self.assertAlmostEqual(r['price'],.075)
        self.assertAlmostEqual(r['net'],r['price']+r['funding']-r['fees']-r['slippage'])
        self.assertAlmostEqual(r['net'],r['long']+r['short'])
        self.assertAlmostEqual(r['curve'][-1],r['net'])

    def test_metrics_include_cash_days_and_intraday_drawdown(self):
        m=metrics([0.,.1,-.1],[.1,-.1],[1.,.7,1.1,.99])
        self.assertAlmostEqual(m['max_drawdown'],.3)
        self.assertAlmostEqual(m['net_return'],-.01)
        self.assertEqual(m['profit_factor'],1)
        self.assertAlmostEqual(m['mean_daily_return'],0)

    def test_block_resampling_is_deterministic_and_negative_stays_negative(self):
        returns=np.tile([-.01,-.02,0.],30)
        self.assertEqual(bootstrap_lower(returns,123,draws=100),bootstrap_lower(returns,123,draws=100))
        self.assertLess(bootstrap_lower(returns,123,draws=100),0)

    def test_seal_rejects_code_and_dataset_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir()
            (repo/'code').write_bytes(b'original');(root/'data').write_bytes(b'data')
            seal={'files':{'code':digest(b'original')},'data_files':{'data':digest(b'data')}}
            (root/'preregistration.lock.json').write_text(json.dumps(seal))
            verify_seal(root,repo)
            (repo/'code').write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_seal(root,repo)
            (repo/'code').write_bytes(b'original');(root/'data').write_bytes(b'changed')
            with self.assertRaises(ValueError):verify_seal(root,repo)

    def test_economic_holdout_and_repeat_access_are_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);valid={'development_test_years':[2021,2022,2023,2024]}
            authorize_development(root,valid)
            for year in (2025,2026):
                with self.assertRaisesRegex(ValueError,'holdout'):
                    authorize_development(root,{'development_test_years':[year]})
            (root/'development.started.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'consumed'):authorize_development(root,valid)

    def test_report_serializes_numpy_gates_and_rejects_invalid_values(self):
        decoded=json.loads(encode_report({'pass':np.bool_(False),'count':np.int64(4),'mean':np.float64(.1)}))
        self.assertEqual(decoded,{'pass':False,'count':4,'mean':.1})
        with self.assertRaises(ValueError):encode_report({'mean':np.float64(np.nan)})
        with self.assertRaises(TypeError):encode_report({'unexpected':object()})

if __name__=='__main__':unittest.main()
