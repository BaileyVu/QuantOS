import importlib.util
from pathlib import Path
import unittest
import json
from quantos.infrastructure.models.v1t5 import A_CONFIGS,B_CONFIGS,SEED

path=Path(__file__).resolve().parents[2]/'research'/'v1t5_run.py'
spec=importlib.util.spec_from_file_location('v1t5_runner',path)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

class T5ProtocolTests(unittest.TestCase):
    def test_model_search_matches_preregistered_config(self):
        config=json.loads((path.parents[1]/'configs/v1t5_preregistration.json').read_text())
        self.assertEqual(config['A']['configurations'],list(A_CONFIGS))
        self.assertEqual(config['B']['configurations'],list(B_CONFIGS))
        self.assertEqual(config['seed'],SEED)
        self.assertEqual(config['B']['lambda'],2)
        self.assertEqual(config['cost_bps'],[10,15,20,25,30])
    def test_holdout_and_prefinal_locked(self):
        d=runner.dt
        with self.assertRaises(ValueError):runner.guard_window(d('2024-01-01T00:00:00+00:00'),d('2026-01-01T00:00:00+00:00'),'development')
        for role,start,end in [('prefinal','2025-01-01','2026-01-01'),('holdout','2026-01-01','2026-09-28')]:
            a,b=d(start+'T00:00:00+00:00'),d(end+'T00:00:00+00:00')
            with self.assertRaises(ValueError):runner.guard_window(a,b,role)
            runner.guard_window(a,b,role,earned=True)
    def test_zero_trades_cannot_pass_and_concentration_enforced(self):
        e=runner.aggregate([],15)
        self.assertFalse(all(runner.economic_gates(e,e).values()))
        base=dict(aggregate_return=.1,profit_factor=1.5,sharpe=1,expectancy=.1,change_expectancy=.05,
                  profitable_fold_fraction=.75,max_positive_fold_share=.5,max_positive_trade_share=.1,max_drawdown=.1)
        self.assertFalse(runner.economic_gates(base,base)['fold_concentration'])
    def test_stationary_bootstrap_deterministic(self):
        folds=[{'costs':{'15':{'daily_returns':[.001,-.002,.003,.004,-.001]}}}]
        a=runner.stationary_bootstrap(folds,15,100)
        self.assertEqual(a,runner.stationary_bootstrap(folds,15,100))
        self.assertEqual(set(a['severe_impairment_probability']),{'0.2','0.35','0.5','0.7'})

if __name__=='__main__':unittest.main()
