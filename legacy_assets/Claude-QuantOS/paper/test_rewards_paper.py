import json, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rewards_paper as rp


class Sizing(unittest.TestCase):
    def test_cap_is_50_shares(self):
        self.assertEqual(rp.side_size(5), 50.0)
        self.assertEqual(rp.side_size(50), 50.0)

    def test_min_size_above_cap_skips(self):
        self.assertIsNone(rp.side_size(100))
        self.assertIsNone(rp.side_size(200))

    def test_full_fill_plus_10c_move_loses_at_most_5(self):
        sh = rp.side_size(20)
        self.assertLessEqual(sh * 0.10, 5.0 + 1e-9)


class Keywords(unittest.TestCase):
    def test_stem_match(self):
        self.assertIn("tariff", rp.keyword_hit("us-canada-diplomatic-agreement-to-lower-tariffs"))
        self.assertIn("iran", rp.keyword_hit("us-x-iran-ceasefire"))
        self.assertEqual(rp.keyword_hit("will-cashmere-launch-a-token-by-december-31-2026"), [])


class Fills(unittest.TestCase):
    def test_book_cross(self):
        self.assertEqual(rp.detect_fills((0.40, 50, 0.42, 50), 0.38, 0.40, []), [("bid", 0.40, 50)])
        self.assertEqual(rp.detect_fills((0.40, 50, 0.42, 50), 0.42, 0.44, []), [("ask", 0.42, 50)])

    def test_trade_print_at_level(self):
        self.assertEqual(rp.detect_fills((0.40, 50, 0.42, 50), 0.40, 0.42, [(0.40, "bid")]), [("bid", 0.40, 50)])
        self.assertEqual(rp.detect_fills((0.40, 50, 0.42, 50), 0.40, 0.42, [(0.41, "bid")]), [])
        self.assertEqual(rp.detect_fills((0.40, 0, 0.42, 50), 0.38, 0.40, []), [])  # no bid resting

    def test_accounting_round_trip(self):
        pos = {"yes": 0.0, "no": 0.0, "cash": 0.0}
        pos = rp.apply_fill(pos, "bid", 0.40, 50)
        self.assertAlmostEqual(pos["cash"], -20.0); self.assertEqual(pos["yes"], 50)
        pos = rp.apply_fill(pos, "ask", 0.42, 50)
        self.assertAlmostEqual(pos["cash"], 1.0); self.assertEqual(pos["yes"], 0); self.assertEqual(pos["no"], 0)

    def test_ask_fill_when_flat_buys_no(self):
        pos = rp.apply_fill({"yes": 0.0, "no": 0.0, "cash": 0.0}, "ask", 0.60, 50)
        self.assertAlmostEqual(pos["cash"], -20.0); self.assertEqual(pos["no"], 50)
        pos = rp.apply_fill(pos, "bid", 0.58, 50)  # sell NO at 0.42
        self.assertAlmostEqual(pos["cash"], 1.0); self.assertEqual(pos["no"], 0)

    def test_inventory_only_reducing_side(self):
        self.assertEqual(rp.desired_quotes(50, 50, 200, 0.40, 0.42), (0.0, 50))
        self.assertEqual(rp.desired_quotes(-30, 50, 200, 0.40, 0.42), (30, 0.0))
        self.assertEqual(rp.desired_quotes(0, 50, 200, 0.40, 0.42), (50, 50))
        self.assertEqual(rp.desired_quotes(0, 50, 10, 0.40, 0.42), (0.0, 0.0))  # cash can't cover a fill


class DollarCapV4(unittest.TestCase):
    """Rule v4: a fill that goes to zero loses at most $5 per side."""

    def setUp(self):
        rp.load_rule(os.path.join(rp.ROOT, "notes", "rewards_rule_v4.json"))

    def tearDown(self):
        rp.load_rule(os.path.join(rp.ROOT, "notes", "rewards_rule_v2.json"))

    def test_cost_never_exceeds_5(self):
        for bb, ba in [(0.50, 0.52), (0.10, 0.12), (0.84, 0.86), (0.33, 0.35)]:
            b, a = rp.desired_quotes(0, rp.side_size(5), 200, bb, ba, min_size=5)
            self.assertLessEqual(b * bb, 5.0 + 1e-9)
            self.assertLessEqual(a * (1 - ba), 5.0 + 1e-9)

    def test_gta_case_capped(self):
        # v3 bought 50 @0.50 ($25 at risk); v4 rests at most 10 shares there
        self.assertEqual(rp.desired_quotes(0, 50.0, 200, 0.50, 0.52, min_size=5), (10.0, 10.0))

    def test_min_size_breaks_cap_side_skipped(self):
        b, a = rp.desired_quotes(0, 50.0, 200, 0.50, 0.90, min_size=20)
        self.assertEqual(b, 0.0)          # 10 shares < min 20
        self.assertEqual(a, 50.0)         # 50 x 0.10 = $5 ok

    def test_cheap_side_keeps_share_cap(self):
        self.assertEqual(rp.dollar_capped(50.0, 0.05, 5), 50.0)

    def test_event_exclusions(self):
        self.assertTrue(rp.keyword_hit("another-gta-vi-trailer-released-by-november-30-20261019"))
        self.assertTrue(rp.keyword_hit("will-perplexity-launch-a-muse-like-personal-agent-before-2027"))
        self.assertTrue(rp.keyword_hit("will-ukmto-publish-fewer-than-8-notices"))
        self.assertEqual(rp.keyword_hit("dow-quarterly-earnings-nongaap-eps-10-22-2026-0pt65"), [])

    def test_v2_has_no_dollar_cap(self):
        rp.load_rule(os.path.join(rp.ROOT, "notes", "rewards_rule_v2.json"))
        self.assertEqual(rp.desired_quotes(0, 50.0, 200, 0.50, 0.52, min_size=5), (50.0, 50.0))


class Stops(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(rp.stop_state(-4.99, 0), (False, False))
        self.assertEqual(rp.stop_state(-5.0, 0), (True, False))
        self.assertEqual(rp.stop_state(0, -10.0), (False, True))


def book(bb, ba, size=1000):
    return {"bids": [{"price": str(bb), "size": str(size)}], "asks": [{"price": str(ba), "size": str(size)}]}


class EngineTick(unittest.TestCase):
    """Drives tick() with fake market data: fill, stop, restart idempotency."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.led = rp.Ledger(os.path.join(self.tmp, "t.db"))
        meta = {"cid": "c1", "slug": "test-market", "yes": "Y1", "rate": 100, "min_size": 20, "v": 3.5, "size": 50.0}
        self.led.db.execute("INSERT INTO markets (cid, meta, status, day) VALUES ('c1', ?, 'active', ?)",
                            (json.dumps(meta), rp.utc_day()))
        self.led.db.commit()
        self.books = {"Y1": book(0.40, 0.42)}
        self.trades = []
        self.p = [mock.patch.object(rp, "fetch_books", lambda toks: self.books),
                  mock.patch.object(rp, "recent_trades", lambda cid, since: self.trades),
                  mock.patch.object(rp, "resolution", lambda cid: None)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_fill_then_reduce_only_and_stop(self):
        rp.tick(self.led, 1)                      # quote 0.40 / 0.42, both sides 50
        m = self.led.markets()[0]
        self.assertEqual(json.loads(m["quote"]), [0.40, 50.0, 0.42, 50.0])
        self.books = {"Y1": book(0.37, 0.39)}     # crosses our bid -> buy 50 YES @0.40
        rp.tick(self.led, 2)
        m = self.led.markets()[0]
        self.assertEqual(m["yes"], 50)
        self.assertAlmostEqual(self.led.get("cash"), 200 - 20)
        q = json.loads(m["quote"])
        self.assertEqual((q[1], q[3]), (0.0, 50.0))   # only the reducing ask
        self.assertAlmostEqual(rp.value(m), 50 * 0.38 - 20)  # marked to mid 0.38
        self.books = {"Y1": book(0.29, 0.31)}     # mid 0.30 -> -5.0: market stop
        rp.tick(self.led, 3)
        m = self.led.markets()[0]
        self.assertEqual(m["status"], "stopped")
        self.assertIsNone(m["quote"])

    def test_restart_idempotent(self):
        rp.tick(self.led, 1)
        self.books = {"Y1": book(0.37, 0.39)}
        rp.tick(self.led, 2)
        led2 = rp.Ledger(os.path.join(self.tmp, "t.db"))   # "restart"
        self.assertIsNone(rp.tick(led2, 2))                # same tick again: no double fill
        self.assertEqual(led2.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 1)
        self.assertAlmostEqual(led2.get("cash"), 180.0)

    def test_settlement(self):
        rp.tick(self.led, 1)
        self.books = {"Y1": book(0.37, 0.39)}
        rp.tick(self.led, 2)
        self.books = {}
        with mock.patch.object(rp, "resolution", lambda cid: 1.0):
            rp.tick(self.led, 3)
        m = self.led.markets()[0]
        self.assertEqual(m["status"], "settled")
        self.assertAlmostEqual(self.led.get("cash"), 180 + 50)
        self.assertAlmostEqual(rp.value(m), 30.0)


if __name__ == "__main__":
    unittest.main()
