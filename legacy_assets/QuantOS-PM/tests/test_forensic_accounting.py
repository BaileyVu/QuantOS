"""Forensic checks against the nine *existing* paper journals.

These tests never call application.run or open a journal for writing.  The
independent ledger below starts from journaled fills, not engine P&L fields.
Only the frozen synthetic fixture and project-local evidence are used.
"""
import copy
import hashlib
import json
import sqlite3
import unittest
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from decimal import localcontext

from quantos_pm.config import ROOT, load
from quantos_pm.domain.common import CONTEXT, D, ZERO, utc_ms
from quantos_pm.domain.execution import Execution, Position
from quantos_pm.engine import Engine
from quantos_pm.storage import canonical


FIXTURE = [json.loads(line) for line in (ROOT / 'fixtures/btc_5m.jsonl').read_text().splitlines()]
CALIBRATION = json.loads((ROOT / 'fixtures/calibration.json').read_text())
COMPARISON = json.loads((ROOT / 'evidence/comparison.json').read_text())
MID = FIXTURE[0]['market']


def existing_journal(run_id):
    path = ROOT / 'artifacts/runs' / run_id / 'journal.sqlite'
    wal = path.with_name(path.name + '-wal')
    # immutable=1 cannot see uncheckpointed WAL; do not silently ignore one.
    if wal.exists() and wal.stat().st_size:
        raise AssertionError('existing journal has uncheckpointed WAL')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        rows = list(db.execute('SELECT seq,kind,body,previous,hash FROM journal ORDER BY seq'))
    return rows


def decoded_journal(run_id):
    return [(kind, json.loads(body)) for _, kind, body, _, _ in existing_journal(run_id)]


def ready_engine():
    engine = Engine(load('configs/paper_200.toml'), CALIBRATION)
    for row in FIXTURE[:4]:
        engine.process(copy.deepcopy(row))
    return engine


class ForensicAccountingInvariants(unittest.TestCase):
    def setUp(self):
        self.context = localcontext(CONTEXT)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def bought(self, up, down, up_price='.49', down_price='.49'):
        x = Execution(load('configs/paper_200.toml'))
        position = x.positions.setdefault(MID, Position())
        for outcome, quantity, price in [('UP', up, up_price), ('DOWN', down, down_price)]:
            if D(quantity):
                position.buy(outcome, D(quantity), D(price))
        x.cash -= position.total_cost
        return x

    def test_one_pair_at_098_yields_exact_002_for_either_winner(self):
        for winner in ('UP', 'DOWN'):
            with self.subTest(winner=winner):
                x = self.bought('1', '1')
                x.settle(MID, winner, 1)
                self.assertEqual(x.matched_pnl, D('.02'))
                self.assertEqual(x.realized, D('.02'))
                self.assertEqual(x.cash, D('200.02'))
                self.assertEqual(x.directional_pnl, ZERO)

    def test_one_pair_at_100_yields_exact_zero_for_either_winner(self):
        for winner in ('UP', 'DOWN'):
            with self.subTest(winner=winner):
                x = self.bought('1', '1', '.45', '.55')
                x.settle(MID, winner, 1)
                self.assertEqual(x.realized, ZERO)
                self.assertEqual(x.cash, D('200'))

    def test_either_unmatched_leg_can_lose_its_entire_cost(self):
        for held, winner in [('UP', 'DOWN'), ('DOWN', 'UP')]:
            with self.subTest(held=held):
                x = self.bought('7' if held == 'UP' else '0', '7' if held == 'DOWN' else '0')
                x.settle(MID, winner, 1)
                self.assertEqual(x.realized, D('-3.43'))
                self.assertEqual(x.directional_pnl, D('-3.43'))
                self.assertEqual(x.cash, D('196.57'))
                self.assertEqual(x.positions[MID].total_cost, ZERO)

    def test_repeated_merge_and_redemption_never_repeat_payout(self):
        x = self.bought('10', '10')
        x.merge(MID, 1)
        expected = (x.cash, x.realized, x.pairs)
        for now in range(2, 8):
            x.merge(MID, now)
            x.settle(MID, 'UP', now)
            self.assertEqual((x.cash, x.realized, x.pairs), expected)
        self.assertEqual(expected, (D('200.20'), D('.20'), D('10')))
        self.assertEqual(sum((r['payout'] for r in x.records if 'payout' in r), ZERO), D('10'))

    def test_staged_unequal_fills_keep_cost_basis_and_final_cash(self):
        x = self.bought('10', '4', '.47', '.49')
        x.merge(MID, 1)
        p = x.positions[MID]
        self.assertEqual(p.qty['UP'], D('6'))
        self.assertEqual(p.cost['UP'], D('2.82'))
        p.buy('DOWN', D('3'), D('.51'))
        x.cash -= D('1.53')
        x.merge(MID, 2)
        self.assertEqual(x.matched_pnl, D('.22'))
        self.assertEqual(p.cost['UP'], D('1.41'))
        x.settle(MID, 'DOWN', 3)
        self.assertEqual(x.directional_pnl, D('-1.41'))
        self.assertEqual(x.cash, D('200') - D('4.70') - D('1.96') - D('1.53') + D('7'))
        self.assertEqual(x.realized, D('-1.19'))

    def test_pair_value_becomes_cash_at_merge_without_equity_jump(self):
        engine = ready_engine()
        x = self.bought('8', '8')
        before = x.equity(engine.md, engine.now)
        self.assertEqual(before, D('200.16'))
        x.merge(MID, engine.now)
        self.assertEqual(x.equity(engine.md, engine.now), before)
        self.assertEqual(x.cash, before)

    def test_fresh_then_stale_mark_does_not_realize_or_spend_cash(self):
        engine = ready_engine()
        x = self.bought('7', '0')
        self.assertEqual(x.equity(engine.md, engine.now), D('200'))
        cash = x.cash
        self.assertEqual(x.equity(engine.md, engine.now + 11000), D('196.57'))
        self.assertEqual(x.realized, ZERO)
        self.assertEqual(x.cash, cash)
        x.settle(MID, 'UP', engine.now + 300000)
        self.assertEqual(x.realized, D('3.57'))
        self.assertEqual(x.cash, D('203.57'))

    def test_unrealized_report_loss_becomes_realized_once_at_resolution(self):
        engine = ready_engine()
        for row in FIXTURE[4:8]:
            # Market 1 first UP fill alone, before any matching DOWN fill.
            if row['outcome'] == 'UP':
                engine.process(copy.deepcopy(row))
                break
        cash = engine.execution.cash
        engine.process(dict(FIXTURE[8], id='forensic-stale-timer'))
        report = engine.finish()
        self.assertEqual(report['realized_pnl'], ZERO)
        self.assertEqual(report['unrealized_pnl'], D('-.98'))
        self.assertEqual(report['ending_equity'], cash)
        resolution = dict(FIXTURE[11], id='forensic-losing-resolution', winner='DOWN')
        engine.process(resolution)
        report = engine.finish()
        self.assertEqual(report['realized_pnl'], D('-.98'))
        self.assertEqual(report['unrealized_pnl'], ZERO)
        self.assertEqual(report['pnl_excluding_incentives'], D('-.98'))

    def test_reservation_moves_no_cash_and_fill_replaces_reserved_cost(self):
        engine = ready_engine()
        x = engine.execution
        before = (x.cash, x.reserved)
        self.assertEqual(before[0], D('200'))
        engine.process(copy.deepcopy(FIXTURE[4]))
        spent = before[0] - x.cash
        self.assertEqual(spent, D('.98'))
        self.assertEqual(before[1] - x.reserved, spent)
        self.assertEqual(x.positions[MID].total_cost, spent)
        self.assertEqual(x.realized, ZERO)
        self.assertEqual(x.fees, ZERO)
        self.assertLessEqual(x.reserved, x.cash)

    def test_cancellation_releases_reserve_without_crediting_cash(self):
        engine = ready_engine()
        x = engine.execution
        x.cancel(engine.now, 'forensic-test')
        cash, reserved = x.cash, x.reserved
        self.assertGreater(reserved, ZERO)
        x.advance(engine.now + 751, engine.md)
        self.assertEqual(x.reserved, ZERO)
        self.assertEqual(x.cash, cash)
        self.assertEqual(x.realized, ZERO)

    def test_expiry_releases_only_reserve_and_resolution_pays_held_winner(self):
        engine = ready_engine()
        x = engine.execution
        engine.process(copy.deepcopy(FIXTURE[4]))
        cash = x.cash
        self.assertGreater(x.reserved, ZERO)
        x.advance(engine.md.markets[MID].expiry, engine.md)
        self.assertEqual(x.reserved, ZERO)
        self.assertEqual(x.cash, cash)
        x.settle(MID, 'UP', engine.md.markets[MID].expiry + 1)
        self.assertEqual(x.cash, cash + D('2'))
        self.assertEqual(x.realized, D('1.02'))


class ExistingNineRunJournalChecks(unittest.TestCase):
    def setUp(self):
        self.context = localcontext(CONTEXT)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_all_original_hash_chains_and_fixture_hashes_verify(self):
        self.assertEqual(len(COMPARISON), 9)
        for summary in COMPARISON:
            with self.subTest(run=summary['run_id']):
                previous = '0' * 64
                rows = existing_journal(summary['run_id'])
                for expected, (seq, kind, body, prev, digest) in enumerate(rows):
                    self.assertEqual(seq, expected)
                    self.assertEqual(prev, previous)
                    self.assertEqual(digest, hashlib.sha256((prev + kind + body).encode()).hexdigest())
                    previous = digest
                manifest = json.loads(rows[0][2])
                self.assertEqual(manifest['feed_sha256'], hashlib.sha256((ROOT / 'fixtures/btc_5m.jsonl').read_bytes()).hexdigest())
                self.assertEqual(manifest['calibration_sha256'], hashlib.sha256((ROOT / 'fixtures/calibration.json').read_bytes()).hexdigest())

    def test_independent_ledger_reconciles_every_event_and_final_report(self):
        for summary in COMPARISON:
            with self.subTest(run=summary['run_id']):
                cash = D(summary['starting_equity'])
                realized = pairs = matched = directional = fees = turnover = ZERO
                quantity = defaultdict(lambda: {'UP': ZERO, 'DOWN': ZERO})
                costs = defaultdict(lambda: {'UP': ZERO, 'DOWN': ZERO})
                orders, books, resolutions, redemptions = {}, {}, set(), set()
                decoded = decoded_journal(summary['run_id'])
                stale_ms = decoded[0][1]['config']['stale_ms']
                for kind, body in decoded:
                    if kind != 'event':
                        continue
                    event = body['input']
                    now = utc_ms(event['receipt_time'])
                    if event['type'] == 'snapshot':
                        books[event['market'], event['outcome']] = {
                            'bids': {D(p): D(q) for p, q in event['bids']},
                            'event': utc_ms(event['event_time']), 'receipt': now,
                        }
                    elif event['type'] == 'delta':
                        book = books[event['market'], event['outcome']]
                        for side, price, size in event['changes']:
                            if side == 'BUY':
                                if D(size):
                                    book['bids'][D(price)] = D(size)
                                else:
                                    book['bids'].pop(D(price), None)
                        book.update(event=utc_ms(event['event_time']), receipt=now)
                    elif event['type'] == 'resolution':
                        resolutions.add(event['market'])
                    for effect in body['effects']:
                        action = effect['kind']
                        if action == 'virtual_submit':
                            self.assertNotIn(effect['order'], orders)
                            orders[effect['order']] = dict(price=D(effect['price']), remaining=D(effect['size']), pending=True)
                        elif action == 'virtual_fill':
                            mid, outcome = effect['market'], effect['outcome']
                            size, price, charged_fee = D(effect['size']), D(effect['price']), D(effect['fee'])
                            self.assertEqual(charged_fee, ZERO)
                            cost = size * price
                            cash -= cost + charged_fee
                            fees += charged_fee
                            turnover += cost
                            quantity[mid][outcome] += size
                            costs[mid][outcome] += cost
                            order = orders[effect['order']]
                            order['remaining'] -= size
                            self.assertGreaterEqual(order['remaining'], ZERO)
                            order['pending'] = order['remaining'] != ZERO
                        elif action == 'virtual_merge':
                            mid, size = effect['market'], D(effect['pairs'])
                            self.assertEqual(size, min(quantity[mid].values()))
                            removed_cost = ZERO
                            for outcome in ('UP', 'DOWN'):
                                removed = costs[mid][outcome] * size / quantity[mid][outcome]
                                costs[mid][outcome] -= removed
                                quantity[mid][outcome] -= size
                                removed_cost += removed
                            self.assertEqual(removed_cost, D(effect['cost']))
                            self.assertEqual(D(effect['payout']), size)
                            self.assertEqual(D(effect['pnl']), size - removed_cost)
                            cash += size
                            realized += size - removed_cost
                            matched += size - removed_cost
                            pairs += size
                        elif action == 'virtual_redemption':
                            mid = effect['market']
                            self.assertIn(mid, resolutions)
                            self.assertNotIn(mid, redemptions)
                            redemptions.add(mid)
                            payout = quantity[mid][effect['winner']]
                            cost = sum(costs[mid].values(), ZERO)
                            self.assertEqual(payout, D(effect['payout']))
                            self.assertEqual(cost, D(effect['cost']))
                            self.assertEqual(payout - cost, D(effect['pnl']))
                            cash += payout
                            realized += payout - cost
                            directional += payout - cost
                            quantity[mid] = {'UP': ZERO, 'DOWN': ZERO}
                            costs[mid] = {'UP': ZERO, 'DOWN': ZERO}
                        elif action in ('cancel_ack_reconciled', 'expiry_reconciled', 'post_only_or_metadata_reject', 'explicit_virtual_reconciliation'):
                            orders[effect['order']]['pending'] = False
                    reserved = sum((o['price'] * o['remaining'] for o in orders.values() if o['pending']), ZERO)
                    # Value unmatched holdings from the recorded book freshness rule;
                    # this is a mark, never a liquidation credit to cash.
                    equity = cash
                    for mid, holdings in quantity.items():
                        paired = min(holdings.values())
                        equity += paired
                        for outcome, size in holdings.items():
                            if size == paired:
                                continue
                            book = books[mid, outcome]
                            mark = max(book['bids']) if 0 <= now - book['event'] <= stale_ms and now - book['receipt'] <= stale_ms else ZERO
                            equity += (size - paired) * mark
                    self.assertEqual(cash, D(body['cash']), event['id'])
                    self.assertEqual(reserved, D(body['reserved']), event['id'])
                    self.assertEqual(realized, D(body['realized']), event['id'])
                    self.assertEqual(equity, D(body['equity']), event['id'])
                    self.assertLessEqual(reserved, cash, event['id'])
                    self.assertGreaterEqual(cash, ZERO, event['id'])
                report = next(body for kind, body in decoded if kind == 'report')
                self.assertEqual(len(resolutions), 6)
                self.assertEqual(redemptions, set(quantity))
                self.assertTrue(all(sum(q.values(), ZERO) == ZERO for q in quantity.values()))
                for field, expected in [('cash', cash), ('ending_equity', cash), ('realized_pnl', realized),
                                        ('complete_pairs', pairs), ('matched_set_pnl', matched),
                                        ('directional_pnl', directional), ('fees', fees), ('turnover', turnover)]:
                    self.assertEqual(D(report[field]), expected, field)
                self.assertEqual(D(report['unrealized_pnl']), ZERO)
                self.assertEqual(D(report['reserved_collateral']), ZERO)
                self.assertEqual(cash - D(report['starting_equity']), matched + directional - fees)
                self.assertEqual(cash - D(report['starting_equity']), D(summary['pnl_excluding_incentives']))

    def test_current_engine_reproduces_all_recorded_event_effects_in_memory(self):
        for summary in COMPARISON:
            with self.subTest(run=summary['run_id']):
                config = replace(load(f"configs/paper_{summary['starting_equity']}.toml"), model=summary['model'])
                engine = Engine(config, CALIBRATION)
                for kind, body in decoded_journal(summary['run_id']):
                    if kind == 'event':
                        offset = len(engine.execution.records)
                        engine.process(copy.deepcopy(body['input']))
                        self.assertEqual(canonical(engine.execution.records[offset:]), canonical(body['effects']))
                        self.assertEqual(engine.execution.cash, D(body['cash']))
                        self.assertEqual(engine.execution.equity(engine.md, engine.now), D(body['equity']))
                    elif kind == 'report':
                        report = engine.finish()
                        for key in ('ending_equity', 'realized_pnl', 'unrealized_pnl', 'pnl_excluding_incentives',
                                    'matched_set_pnl', 'directional_pnl', 'complete_pairs', 'fees', 'slippage', 'reserved_collateral'):
                            self.assertEqual(report[key], D(body[key]), key)

    def test_ops_allowance_is_admission_estimate_not_a_duplicated_cash_charge(self):
        # Records distinguish an expected screening allowance from actual charges.
        # The report excludes this estimate; an economic stress report must show it
        # separately rather than relabeling it as an already-paid expense.
        for summary in COMPARISON:
            with self.subTest(run=summary['run_id']):
                decoded = decoded_journal(summary['run_id'])
                estimates = [effect for kind, body in decoded if kind == 'event'
                             for effect in body['effects'] if effect['kind'] == 'edge_estimate']
                report = next(body for kind, body in decoded if kind == 'report')
                self.assertTrue(estimates)
                self.assertTrue(all(D(e['costs']) > ZERO for e in estimates))
                self.assertEqual(D(report['fees']), ZERO)
                self.assertEqual(D(report['slippage']), ZERO)
                self.assertEqual(D(report['pnl_excluding_incentives']),
                                 D(report['matched_set_pnl']) + D(report['directional_pnl']))


if __name__ == '__main__':
    unittest.main()
