import json, tempfile, unittest
from decimal import Decimal
from pathlib import Path
from quantos.paper import dashboard
from quantos.paper.journal import Journal
from quantos.paper.risk import Account


class FakeEngine:
    def __init__(self, journal): self.journal = journal
    def account(self, now_ms): return Account(Decimal("201.5"), Decimal("200"), Decimal("205"), 1)
    def active(self):
        return [{"signal": {"symbol": "BTCUSDC", "side": "LONG", "strategy_id": "s1",
                            "stop_loss": Decimal("90"), "take_profit": Decimal("120")},
                 "filled": "1", "average": "100", "state": "OPEN", "stop_price": "90", "last_mark": "101"}]


class DashboardTests(unittest.TestCase):
    def test_events_status_and_write(self):
        with tempfile.TemporaryDirectory() as t:
            j = Journal(Path(t)/"j.db", "p1")
            with j.transaction():
                for i, k in enumerate(["SIGNAL", "OTHER", "FILLED", "CLOSED"]):
                    j.event(1000 + i, k, {"n": Decimal(i)})
            ev = dashboard.recent_events(j, ("SIGNAL", "FILLED", "CLOSED"), 2)
            self.assertEqual([e["kind"] for e in ev], ["CLOSED", "FILLED"])
            self.assertEqual(dashboard.recent_events(j, (), 5), [])
            st = dashboard.build_status("paper", {"status": "running"}, [FakeEngine(j)],
                                        {"stale": []}, False, 5000, ev[:1], ev[1:])
            e = st["engines"][0]
            self.assertEqual(e["profile"], "p1"); self.assertEqual(e["cash"], "200")
            self.assertEqual(e["equity"], "201.5"); self.assertEqual(e["day_pnl"], "1.5")
            self.assertEqual(e["open_positions"][0]["stop_loss"], "90")
            path = dashboard.write_status(t, st)
            self.assertEqual(path, Path(t)/"dashboard"/"status.json")
            self.assertEqual(json.loads(path.read_text()), st)
            j.close()


if __name__ == "__main__": unittest.main()
