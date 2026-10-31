import ast
import copy
import json
import random
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from contextlib import closing
from decimal import localcontext
from pathlib import Path
from unittest.mock import patch

from quantos_pm.config import load, ROOT, local_path
from quantos_pm.domain.common import D, ZERO, CONTEXT, utc_ms, iso
from quantos_pm.domain.execution import Position, Execution, fee
from quantos_pm.domain.alpha import Intent
from quantos_pm.domain.market_data import Book
from quantos_pm.domain.feature import Features
from quantos_pm.domain.risk import Risk
from quantos_pm.domain.evaluation import block_bootstrap
from quantos_pm.engine import Engine
from quantos_pm.application import run
from quantos_pm.storage import Journal, read_report, canonical
from quantos_pm.adapters.public import PublicData, Normalizer
from quantos_pm.forensics import pages, classify, reconstruct

FIXTURE = [json.loads(x) for x in (ROOT/'fixtures/btc_5m.jsonl').read_text().splitlines()]
CAL = json.loads((ROOT/'fixtures/calibration.json').read_text())
MID = FIXTURE[0]['market']


def ready(model='pessimistic'):
    e = Engine(replace(load('configs/paper_200.toml'), model=model), CAL)
    for row in FIXTURE[:4]:
        e.process(copy.deepcopy(row))
    return e


def event(kind, seconds, **fields):
    ts = utc_ms(FIXTURE[0]['event_time'])+seconds*1000
    return dict(type=kind, id=f'test-{kind}-{seconds}', event_time=iso(ts), receipt_time=iso(ts+10), **fields)


class AccountingTests(unittest.TestCase):
    def pair(self, a, b):
        x = Execution(load('configs/paper_200.toml'))
        p = x.positions.setdefault(MID, Position())
        p.buy('UP', D('10'), D(a)); p.buy('DOWN', D('10'), D(b))
        x.cash -= p.total_cost
        x.settle(MID, 'UP', 1)
        return x

    def test_fifty_cent_pair_zero_profit(self):
        self.assertEqual(self.pair('.50', '.50').realized, ZERO)

    def test_forty_nine_pair_two_cents(self):
        self.assertEqual(self.pair('.49', '.49').realized, D('.20'))

    def test_single_losing_leg_entire_cost(self):
        x = Execution(load('configs/paper_200.toml'))
        p = x.positions.setdefault(MID, Position())
        p.buy('UP', D('7'), D('.49')); x.cash -= p.total_cost
        x.settle(MID, 'DOWN', 1)
        self.assertEqual(x.realized, D('-3.43'))
        self.assertEqual(x.cash, D('196.57'))

    def test_actual_weighted_fill_cost(self):
        x = Execution(load('configs/paper_200.toml'))
        p = x.positions.setdefault(MID, Position())
        p.buy('UP', D('5'), D('.45')); p.buy('UP', D('5'), D('.49'))
        p.buy('DOWN', D('10'), D('.49')); x.cash -= p.total_cost
        x.merge(MID, 1)
        self.assertEqual(x.matched_pnl, D('.40'))

    def test_documented_fees_center_extremes_and_rounding(self):
        self.assertEqual(fee('100', '.5', '.07'), D('1.75000'))
        self.assertEqual(fee('100', '.01', '.07'), D('.06930'))
        self.assertEqual(fee('100', '.99', '.07'), D('.06930'))
        self.assertEqual(fee('.00001', '.01', '.07'), ZERO)
        self.assertEqual(fee('100', '.5', '.07', maker=True), ZERO)

    def test_randomized_collateral_settlement_invariants(self):
        rng = random.Random(55)
        with localcontext(CONTEXT):
            for _ in range(500):
                x = Execution(load('configs/paper_200.toml'))
                p = x.positions.setdefault(MID, Position())
                u, d = D(rng.randrange(1, 100))/10, D(rng.randrange(1, 100))/10
                a, b = D(rng.randrange(1, 100))/100, D(rng.randrange(1, 100))/100
                p.buy('UP', u, a); p.buy('DOWN', d, b)
                cost = p.total_cost; x.cash -= cost
                winner = rng.choice(['UP', 'DOWN'])
                payout = u if winner == 'UP' else d
                x.merge(MID, 1); x.settle(MID, winner, 2)
                self.assertLess(abs(x.cash-(D('200')-cost+payout)), D('1e-28'))
                self.assertLess(abs(x.realized-(payout-cost)), D('1e-28'))
                self.assertEqual(p.total_cost, ZERO)
                self.assertEqual(sum(p.qty.values()), ZERO)


class SimulationTests(unittest.TestCase):
    def test_partial_fill_updates_inventory_and_reservations(self):
        e = ready()
        e.process(FIXTURE[4])
        self.assertEqual(e.execution.positions[MID].qty['UP'], D('2'))
        self.assertEqual(e.execution.counts['partial_fills'], 1)
        self.assertGreater(e.execution.reserved, 0)
        self.assertLessEqual(e.execution.reserved, e.execution.cash)

    def test_touch_and_depth_removal_never_award_fill(self):
        e = ready()
        e.process(event('delta', 3, market=MID, outcome='UP', seq=2, sequence_verified=True,
                        changes=[['BUY', '.49', '1']]))
        e.process(event('trade', 4, market=MID, outcome='UP', price='.49', size='100', aggressor='UNKNOWN', aggressor_verified=False))
        self.assertEqual(e.execution.counts['fills'], 0)
        self.assertGreater(e.execution.counts['unobservable_fills'], 0)
        self.assertEqual(next(iter(e.execution.orders.values())).queue, D('20'))

    def test_queue_ahead_consumed_before_fills(self):
        e = ready()
        e.process(event('trade', 3, market=MID, outcome='UP', price='.49', size='40', aggressor='SELL', aggressor_verified=True))
        self.assertEqual(e.execution.counts['fills'], 0)
        e.process(event('trade', 4, market=MID, outcome='UP', price='.49', size='2', aggressor='SELL', aggressor_verified=True))
        self.assertEqual(e.execution.positions[MID].qty['UP'], D('1'))

    def test_post_only_rejected_after_submission_latency(self):
        e = Engine(load('configs/paper_200.toml'), CAL)
        for row in FIXTURE[:3]:e.process(row)
        change = event('snapshot', 1, market=MID, outcome='UP', seq=2, sequence_verified=True,
                       bids=[['.47', '10']], asks=[['.49', '10']])
        change['event_time'] = '2026-07-01T00:00:01.200Z'
        change['receipt_time'] = '2026-07-01T00:00:01.210Z'
        e.process(change)
        e.process(event('timer', 2))
        self.assertEqual(e.execution.counts['rejected'], 1)

    def test_cancel_latency_allows_racing_fill_then_reconciles(self):
        e = ready()
        x = e.execution
        x.cancel(e.now, 'test')
        self.assertGreater(x.reserved, 0)
        # 100 ms into the 750 ms pessimistic cancellation window
        trade = event('trade', 2, market=MID, outcome='UP', price='.49', size='44', aggressor='SELL', aggressor_verified=True)
        trade['event_time']='2026-07-01T00:00:02.100Z'; trade['receipt_time']='2026-07-01T00:00:02.110Z'
        e.process(trade)
        self.assertEqual(x.counts['fills'], 1)
        e.process(event('timer', 3))
        self.assertEqual(x.reserved, ZERO)
        self.assertEqual(x.counts['canceled'], 2)

    def test_failed_cancel_halts_and_keeps_collateral_until_reconcile(self):
        e = ready()
        e.process(event('cancel_failure', 3))
        e.process(event('timer', 5))
        self.assertTrue(e.execution.uncertain)
        self.assertGreater(e.execution.reserved, 0)
        self.assertTrue(e.risk.approve((Intent(MID,'UP',D('.49'),D('5')),),e.execution,e.md,D('200')))
        ids = [o.id for o in e.execution.orders.values() if o.pending]
        e.process(event('reconcile', 6, authoritative_virtual_state=True, canceled_orders=ids))
        self.assertFalse(e.execution.uncertain)
        self.assertEqual(e.execution.reserved, ZERO)

    def test_disconnect_no_fills_no_assumed_cancels(self):
        e = ready(); e.process(event('disconnect', 3))
        e.process(event('trade', 4, market=MID, outcome='UP', price='.49', size='100', aggressor='SELL', aggressor_verified=True))
        self.assertTrue(e.execution.uncertain)
        self.assertEqual(e.execution.counts['fills'], 0)
        self.assertGreater(e.execution.reserved, 0)

    def test_stale_expiry_and_last_minute_halt(self):
        e = ready()
        self.assertEqual(e.md.eligible(MID,e.now+11000,10000),'stale_missing_or_invalid_book')
        self.assertEqual(e.md.eligible(MID,utc_ms(FIXTURE[0]['expiry'])-60000,10000),'inactive_or_expiring')
        e.process(event('timer', 301))
        self.assertEqual(e.execution.reserved,ZERO)
        self.assertTrue(all(o.status=='EXPIRED' for o in e.execution.orders.values()))

    def test_sequence_gap_and_crossed_books_fail_closed(self):
        e=ready()
        e.process(event('delta',3,market=MID,outcome='UP',seq=99,sequence_verified=True,changes=[['BUY','.49','2']]))
        self.assertTrue(e.execution.uncertain)
        self.assertEqual(e.risk.halted,'unreconciled_state')
        b=Book()
        with self.assertRaises(ValueError):
            b.update(dict(type='snapshot',event_time=FIXTURE[0]['event_time'],bids=[['.6','1']],asks=[['.5','1']]),0)

    def test_tick_change_requests_cancellation(self):
        e=ready(); e.process(event('tick',3,market=MID,outcome='UP',tick='.1'))
        self.assertTrue(all(o.cancel_at for o in e.execution.orders.values()))

    def test_duplicate_id_is_idempotent_conflict_rejected(self):
        e=ready(); e.process(FIXTURE[4]); n=e.execution.counts['fills']
        e.process(FIXTURE[4]); self.assertEqual(e.execution.counts['fills'],n)
        changed=dict(FIXTURE[4],size='999')
        with self.assertRaises(ValueError):e.process(changed)


class RiskTests(unittest.TestCase):
    def test_pending_reservations_block_simultaneous_overexposure(self):
        e=ready()
        request=(Intent(MID,'UP',D('.49'),D('5')),)
        self.assertEqual(e.risk.approve(request,e.execution,e.md,D('200')),'market_unmatched_limit')

    def test_minimum_never_rounds_up(self):
        e=Engine(load('configs/paper_200.toml'),CAL)
        rows=copy.deepcopy(FIXTURE[:3]); rows[0]['minimum']='50'
        for r in rows:e.process(r)
        self.assertEqual(e.execution.counts['quoted'],0)
        self.assertIn('minimum_exceeds_risk_budget',e.evaluation.skips)

    def test_global_reservation_two_markets(self):
        e=ready()
        new=dict(FIXTURE[0],market='0x'+'f'*64,id='market2',receipt_time=iso(e.now),event_time=iso(e.now))
        e.md.apply(new)
        proposed=(Intent(new['market'],'UP',D('.49'),D('8')),Intent(new['market'],'DOWN',D('.49'),D('8')))
        self.assertEqual(e.risk.approve(proposed,e.execution,e.md,D('200')),'global_unmatched_limit')

    def test_no_averaging(self):
        e=ready();e.process(FIXTURE[4])
        self.assertEqual(e.risk.approve((Intent(MID,'UP',D('.4'),D('5')),),e.execution,e.md,D('200')),'no_averaging_or_inventory_increase')

    def test_daily_weekly_peak_halts(self):
        now=utc_ms(FIXTURE[0]['event_time'])
        r=Risk(D('200'));r.observe(now,D('200'))
        self.assertEqual(r.observe(now+1,D('194')),'daily_loss_halt')
        r=Risk(D('200'));r.observe(now,D('200'));r.observe(now+86400000,D('195'))
        self.assertEqual(r.observe(now+86400001,D('188')),'weekly_loss_halt')
        r=Risk(D('200'));r.observe(now,D('200'));r.observe(now+8*86400000,D('190'))
        self.assertEqual(r.observe(now+8*86400000+1,D('180')),'peak_drawdown_halt')

    def test_unverified_incentives_zero_and_causal_calibration(self):
        f=Features(CAL)
        est=f.estimate(utc_ms(FIXTURE[0]['event_time']),[D('.49')]*2,[D('10')]*2,D('5'))
        self.assertEqual(est['expected_rebates'],ZERO);self.assertEqual(est['expected_rewards'],ZERO)
        self.assertIsNone(f.estimate(0,[D('.49')]*2,[D('10')]*2,D('5')))
        self.assertIsNone(f.estimate(utc_ms(FIXTURE[0]['event_time']),[D('.49')]*2,[D('10')]*2,D('5'),shadow=True))

    def test_risk_rejection_final(self):
        e=Engine(load('configs/paper_200.toml'),CAL)
        with patch.object(e.risk,'approve',return_value='forced_rejection'):
            for r in FIXTURE[:4]:e.process(r)
        self.assertEqual(e.execution.counts['quoted'],0)


class PersistenceAndIsolationTests(unittest.TestCase):
    def test_replay_restart_no_duplicate_orders_or_ledger(self):
        a=run('configs/paper_200.toml','fixtures/btc_5m.jsonl')
        path=ROOT/'artifacts/runs'/a['run_id']/'journal.sqlite'
        with closing(sqlite3.connect(path)) as db:n=db.execute('SELECT COUNT(*) FROM journal').fetchone()[0]
        b=run('configs/paper_200.toml','fixtures/btc_5m.jsonl')
        with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM journal').fetchone()[0],n)
        self.assertEqual(a,b)
        self.assertEqual(read_report(a['run_id'])['realized_pnl'],str(a['realized_pnl']))

    def test_interrupted_journal_replay_and_append_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'artifacts') as d:
            j=Journal(d,{'run':'test'});j.append('event',{'a':1});j.close()
            j=Journal(d,{'run':'test'});j.append('event',{'a':1});j.append('event',{'b':2})
            self.assertEqual(j.db.execute('SELECT COUNT(*) FROM journal').fetchone()[0],3)
            with self.assertRaises(sqlite3.DatabaseError):j.db.execute('DELETE FROM journal')
            j.close()

    def test_paths_cannot_access_sibling_or_symlink(self):
        with self.assertRaises(ValueError):local_path('../QuantOS/README.md')
        with tempfile.TemporaryDirectory(dir=ROOT/'artifacts') as d:
            p=Path(d)/'escape';p.symlink_to(ROOT.parent)
            with self.assertRaises(ValueError):local_path(p/'QuantOS/README.md')

    def test_flags_structurally_reject_live(self):
        with tempfile.NamedTemporaryFile(dir=ROOT/'artifacts',suffix='.toml',mode='w') as f:
            f.write((ROOT/'configs/paper_200.toml').read_text().replace('enable_live_orders = false','enable_live_orders = true'));f.flush()
            with self.assertRaises(ValueError):load(f.name)

    def test_no_network_in_offline_run(self):
        import socket
        with patch.object(socket,'socket',side_effect=AssertionError('offline network access')):
            r=run('configs/paper_200.toml','fixtures/btc_5m.jsonl')
        self.assertGreater(r['orders']['fills'],0)

    def test_no_signing_order_client_s199_process_access_in_source(self):
        banned={'web3','eth_account','py_clob_client','quantos','subprocess','psutil'}
        for p in (ROOT/'quantos_pm').rglob('*.py'):
            tree=ast.parse(p.read_text())
            for n in ast.walk(tree):
                if isinstance(n,ast.Import):self.assertFalse({x.name.split('.')[0] for x in n.names}&banned)
                if isinstance(n,ast.ImportFrom) and n.module:self.assertNotIn(n.module.split('.')[0],banned)
                if isinstance(n,ast.Attribute):self.assertNotIn(n.attr,{'sign_transaction','sign_message','create_order','post_order'})
        api=PublicData()
        for path in ('/order','/orders','/auth/api-key'):
            with self.assertRaises(ValueError):api.get('clob',path)
        self.assertFalse(hasattr(api,'post'))

    def test_randomized_decimal_input_rejects_nan_float(self):
        for bad in (float('nan'),0.49,'NaN','Infinity',True):
            with self.assertRaises(ValueError):D(bad)


class AdapterResearchTests(unittest.TestCase):
    def test_public_messages_snapshots_changes_ticks_resolution(self):
        m=FIXTURE[0];n=Normalizer(m);now=utc_ms(m['event_time'])
        b=dict(event_type='book',market=MID,asset_id='1',timestamp=str(now),bids=[{'price':'.49','size':'5'}],asks=[{'price':'.51','size':'5'}])
        e=n.messages(b,now)[0];self.assertEqual(e['type'],'snapshot');self.assertFalse(e['sequence_verified'])
        d=dict(event_type='price_change',market=MID,timestamp=str(now),price_changes=[dict(asset_id='1',side='BUY',price='.49',size='4')])
        self.assertEqual(n.messages(d,now)[0]['changes'],[['BUY','.49','4']])
        t=dict(event_type='tick_size_change',market=MID,timestamp=str(now),asset_id='1',new_tick_size='.001')
        self.assertEqual(n.messages(t,now)[0]['tick'],'.001')
        r=dict(event_type='market_resolved',market=MID,timestamp=str(now),winning_asset_id='2')
        self.assertEqual(n.messages(r,now)[0]['winner'],'DOWN')

    def test_cursor_pagination_retains_filters_and_exhausts(self):
        class Fake:
            calls=[]
            def get(self,service,path,params):
                self.calls.append(dict(params))
                return {'data':[{'id':len(self.calls)}],'pagination':{'next_cursor':'opaque' if len(self.calls)==1 else None}}
        f=Fake();rows,complete=pages(f,'/v2/activity',dict(user='wallet',start=1),4,[])
        self.assertTrue(complete);self.assertEqual(len(rows),2)
        self.assertEqual(f.calls[1]['cursor'],'opaque');self.assertEqual(f.calls[1]['start'],1)

    def test_repeated_cursor_fails_and_page_cap_is_incomplete(self):
        class Fake:
            def get(self,*args):return dict(data=[],pagination={'next_cursor':'same'})
        with self.assertRaises(ValueError):pages(Fake(),'/v2/activity',{},3,[])
        self.assertFalse(pages(Fake(),'/v2/activity',{},1,[])[1])

    def test_maker_not_inferred_from_price_or_partial_history(self):
        row=dict(transaction_hash='0xabc',size='5',price='.49')
        self.assertEqual(classify([row],[],False)[0]['execution_class'],'UNKNOWN')
        self.assertEqual(classify([row],[row],True)[0]['execution_class'],'CONFIRMED_TAKER')
        self.assertEqual(classify([row],[],True)[0]['execution_class'],'CONFIRMED_MAKER')
        self.assertTrue(all(x['execution_class']=='UNKNOWN' for x in classify([row,row],[],True)))

    def test_unresolved_redemption_is_not_invented_profit(self):
        r=reconstruct([dict(type='REDEEM',size='10',usdc_size='10',transaction_hash='tx')],[])
        self.assertTrue(r['gaps']);self.assertFalse(r['exact_reconstruction'])
        self.assertIsNone(r['attribution']['directional_inventory'])

    def test_block_bootstrap_uses_blocks(self):
        self.assertEqual(block_bootstrap({'one':D('1')})['status'],'insufficient_independent_blocks')
        a=block_bootstrap({'one':D('1'),'two':D('-2')})
        self.assertEqual(a,block_bootstrap({'one':D('1'),'two':D('-2')}))
        self.assertEqual(a['blocks'],2)


if __name__=='__main__':unittest.main()
