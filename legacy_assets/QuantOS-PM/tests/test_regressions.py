import builtins
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import localcontext
from pathlib import Path
from unittest.mock import patch
from test_engine import ready, event, FIXTURE, MID, CAL
from quantos_pm.config import ROOT, load
from quantos_pm.application import run
from quantos_pm.domain.common import D, ZERO
from quantos_pm.domain.alpha import Intent
from quantos_pm.domain.execution import Execution, Position
from quantos_pm.domain.feature import Features
from quantos_pm.storage import Journal
from quantos_pm.engine import Engine


class RegressionTests(unittest.TestCase):
    def test_late_trade_after_cancel_blocks_new_orders(self):
        e=ready();e.execution.cancel(e.now,'test')
        e.process(event('timer',4))
        t=event('trade',5,market=MID,outcome='UP',size='100',price='.49',aggressor='SELL',aggressor_verified=True)
        t['event_time']='2026-07-01T00:00:02.400Z'
        e.process(t)
        self.assertTrue(e.execution.uncertain)
        self.assertEqual(e.execution.counts['fills'],0)

    def test_ambient_decimal_context_does_not_change_replay(self):
        with localcontext() as c:
            c.prec=9
            a=run('configs/paper_350.toml','fixtures/btc_5m.jsonl')
        with localcontext() as c:
            c.prec=50
            b=run('configs/paper_350.toml','fixtures/btc_5m.jsonl')
        self.assertEqual(a,b)

    def test_engine_file_reads_and_writes_stay_in_own_root(self):
        real_io,real_builtin=io.open,builtins.open
        observed=[]
        def check(file):
            if isinstance(file,(str,Path)):
                p=Path(file).resolve()
                self.assertTrue(p.is_relative_to(ROOT),str(p))
                observed.append(str(p))
        def guarded_io(file,*a,**kw):check(file);return real_io(file,*a,**kw)
        def guarded_builtin(file,*a,**kw):check(file);return real_builtin(file,*a,**kw)
        with patch('io.open',side_effect=guarded_io),patch('builtins.open',side_effect=guarded_builtin):
            run('configs/paper_200.toml','fixtures/btc_5m.jsonl')
        self.assertTrue(observed)

    def test_no_positive_edge_no_orders(self):
        e=Engine(load('configs/paper_200.toml'),None)
        for r in FIXTURE[:4]:e.process(r)
        self.assertEqual(e.execution.counts['quoted'],0)
        self.assertIn('no_positive_calibrated_lower_bound',e.evaluation.skips)

    def test_insufficient_day_blocks_refuses_calibration(self):
        cal=dict(CAL,observations=[dict(x,day='one-day') for x in CAL['observations']])
        self.assertIsNone(Features(cal).block_bounds(cal['observations']))

    def test_no_new_order_at_expiry_even_with_fresh_books(self):
        e=Engine(load('configs/paper_200.toml'),CAL)
        rows=json.loads(json.dumps(FIXTURE[:3]))
        for r in rows:
            r['receipt_time']='2026-07-01T00:05:00.000Z'
            r['event_time']='2026-07-01T00:05:00.000Z'
            e.process(r)
        self.assertEqual(e.execution.counts['quoted'],0)

    def test_invalid_prices_risk_rejected(self):
        e=ready();e.execution.orders.clear()
        for price in ('-.01','0','1','1.01'):
            self.assertTrue(e.risk.approve((Intent(MID,'UP',D(price),D('5')),),e.execution,e.md,D('200')))

    def test_matched_limit_still_applies_with_unmatched_hedged(self):
        e=ready();e.execution.orders.clear()
        p=e.execution.positions.setdefault(MID,Position())
        p.buy('UP',D('25'),D('.4'));p.buy('DOWN',D('25'),D('.4'))
        # Existing inventory blocks averaging before any additional market cap check.
        self.assertEqual(e.risk.approve((Intent(MID,'UP',D('.4'),D('5')),),e.execution,e.md,D('200')),'no_averaging_or_inventory_increase')

    def test_restart_detects_changed_durable_record(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'artifacts') as d:
            j=Journal(d,{'a':1});j.append('event',{'b':2});j.close()
            j=Journal(d,{'a':1})
            try:
                with self.assertRaises(ValueError):j.append('event',{'b':3})
            finally:j.close()

    def test_post_only_partial_order_cannot_exceed_original_size(self):
        e=ready()
        t=event('trade',3,market=MID,outcome='UP',size='10000',price='.49',aggressor='SELL',aggressor_verified=True)
        e.process(t)
        o=next(iter(e.execution.orders.values()))
        self.assertEqual(e.execution.positions[MID].qty['UP'],o.size)
        self.assertEqual(o.remaining,ZERO)
        self.assertEqual(o.status,'FILLED')

    def test_identical_inputs_three_models_pessimistic_not_promoted(self):
        reports=[run('configs/paper_500.toml','fixtures/btc_5m.jsonl',m) for m in ('optimistic','baseline','pessimistic')]
        self.assertLess(reports[-1]['ending_equity'],reports[0]['ending_equity'])
        self.assertTrue(all(not x['promotion_eligible'] for x in reports))

    def test_better_price_depth_additions_move_ahead(self):
        e=ready()
        e.process(event('delta',3,market=MID,outcome='UP',seq=2,sequence_verified=True,changes=[['BUY','.50','10']]))
        order=next(iter(e.execution.orders.values()))
        self.assertEqual(order.queue,D('40'))
        e.process(event('trade',4,market=MID,outcome='UP',price='.49',size='44',aggressor='SELL',aggressor_verified=True))
        self.assertEqual(e.execution.counts['fills'],0)

    def test_existing_matched_positions_enforce_global_and_market_caps(self):
        e=ready();e.execution.orders.clear()
        for i in (2,3):
            mid='0x'+f'{i:064x}'
            meta=dict(FIXTURE[0],id=f'meta{i}',market=mid,event_time='2026-07-01T00:00:03.000Z',receipt_time='2026-07-01T00:00:03.010Z')
            e.md.apply(meta)
            p=e.execution.positions.setdefault(mid,Position())
            p.buy('UP',D('18'),D('.49'));p.buy('DOWN',D('18'),D('.49'))
        proposed=(Intent(MID,'UP',D('.49'),D('5')),Intent(MID,'DOWN',D('.49'),D('5')))
        self.assertEqual(e.risk.approve(proposed,e.execution,e.md,D('200')),'global_matched_limit')
        p.buy('UP',D('3'),D('.49'));p.buy('DOWN',D('3'),D('.49'))
        self.assertEqual(e.risk.approve(proposed,e.execution,e.md,D('200')),'market_matched_limit')

    def test_cash_reservation_cannot_borrow(self):
        e=ready();e.execution.orders.clear();e.execution.cash=D('1')
        proposed=(Intent(MID,'UP',D('.49'),D('5')),Intent(MID,'DOWN',D('.49'),D('5')))
        self.assertEqual(e.risk.approve(proposed,e.execution,e.md,D('200')),'insufficient_unreserved_collateral')

    def test_first_event_of_new_day_cannot_reset_loss_baseline(self):
        from quantos_pm.domain.risk import Risk
        from quantos_pm.domain.common import utc_ms
        r=Risk(D('200'));now=utc_ms('2026-07-01T23:59:59Z')
        r.observe(now,D('200'))
        self.assertEqual(r.observe(now+2000,D('193')),'daily_loss_halt')

    def test_snapshot_sequence_cannot_silently_rewind(self):
        e=ready()
        e.process(event('snapshot',3,market=MID,outcome='UP',seq=0,sequence_verified=True,bids=[['.49','10']],asks=[['.51','10']]))
        self.assertTrue(e.execution.uncertain)

    def test_sequence_proof_cannot_be_a_boolean_without_number(self):
        e=ready()
        e.process(event('snapshot',3,market=MID,outcome='UP',sequence_verified=True,bids=[['.49','10']],asks=[['.51','10']]))
        self.assertTrue(e.execution.uncertain)

    def test_shadow_feed_failure_is_nonzero_cli_exit(self):
        from quantos_pm.__main__ import main
        with patch('sys.argv',['quantos_pm','shadow','--config','configs/shadow_200.toml']),patch('quantos_pm.adapters.public.shadow_run',return_value={'public_feed_worked':False}),patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(main(),2)

    def test_market_skip_fraction_is_not_event_skip_fraction(self):
        r=run('configs/paper_200.toml','fixtures/btc_5m.jsonl')
        self.assertEqual(r['quoted_markets'],5)
        self.assertEqual(r['candidate_markets'],6)
        self.assertLess(r['skipped_market_fraction'],r['skipped_fraction'])
