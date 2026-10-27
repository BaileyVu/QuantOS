import random
import unittest

from quantos.paper.engine import record_path
from quantos.paper.exits import ExitPolicy, choose_exit, exit_r
from quantos.paper.types import D, Quote


def walk(seed, n, drift=0.0):
    rnd, out = random.Random(seed), []
    for _ in range(n):
        p, path = 100.0, []
        for m in range(240):
            p *= 1 + rnd.gauss(drift, 0.002)
            path.append([m, p * 1.001, p * 0.999, p])
        out.append((path, 100.0, 99.0, 1))
    return out


class ExitTests(unittest.TestCase):
    def test_stop_wins_same_bar(self):
        self.assertEqual(exit_r([[0, 102, 98, 100]], 100, 99, 1, ExitPolicy(tp_r=1), 0), -1)

    def test_breakeven_and_short_target(self):
        p = ExitPolicy(tp_r=3, be_at_r=1)
        self.assertEqual(exit_r([[0, 101.2, 100.2, 101], [1, 100.5, 99.5, 100]], 100, 99, 1, p, 0), 0)
        self.assertEqual(exit_r([[0, 99.5, 97.5, 98]], 100, 101, -1, ExitPolicy(tp_r=2), 0), 2)

    def test_no_fake_improvement_on_random_walk(self):
        self.assertEqual(choose_exit(walk(0, 200))[1]["reason"], "KEEP")

    def test_finds_trend_benefit(self):
        policy, rep = choose_exit(walk(2, 300, 0.0006), ExitPolicy(tp_r=1.0))
        self.assertEqual(rep["reason"], "SWITCH", rep)
        self.assertGreater(policy.tp_r, 1.0)

    def test_record_path_buckets_minutes(self):
        o = {}
        for t, px in ((0, "100"), (30000, "101"), (59000, "99"), (60000, "100.5")):
            record_path(o, Quote("X", t, t, D(px), D(px), D(1), D(1), D(px)))
        self.assertEqual(o["path"], [[0, "101", "99", "99"], [1, "100.5", "100.5", "100.5"]])
