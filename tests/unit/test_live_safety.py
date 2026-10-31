import os, tempfile, unittest
from decimal import Decimal as D
from pathlib import Path

from quantos.live.client import SignedClient
from quantos.live.guards import DailyLossGuard, DAY_MS
from quantos.live.kill import KillSwitch, flatten
from quantos.live.reconcile import compare, reconcile
from quantos.live.__main__ import main
from tests.unit.live_fakes import Fake, KEYS

SL = {"symbol": "SOLUSDC", "type": "STOP_MARKET", "side": "SELL", "reduceOnly": True, "clientOrderId": "sl1"}


class ReconcileTests(unittest.TestCase):
    def test_ok(self):
        r = compare({"SOLUSDC": D("1")}, [SL], {"SOLUSDC": D("1")}, {"sl1"})
        self.assertTrue(r.ok)

    def test_classification(self):
        r = compare({"SOLUSDC": D("2"), "ETHUSDC": D("1"), "BTCUSDC": D("-1")},
                    [SL, {"symbol": "X", "clientOrderId": "zzz", "type": "LIMIT"}],
                    {"SOLUSDC": D("1"), "XRPUSDC": D("5"), "BTCUSDC": D("-1")}, {"sl1"})
        self.assertFalse(r.ok)
        self.assertEqual(r.unknown_positions, {"ETHUSDC": D("1")})
        self.assertEqual(r.missing_positions, {"XRPUSDC": D("5")})
        self.assertEqual(r.qty_mismatch, {"SOLUSDC": (D("1"), D("2"))})
        self.assertEqual(r.orphan_orders, ["zzz"])
        self.assertEqual(r.missing_protection, ["BTCUSDC", "ETHUSDC"])  # wrong-side/no stop

    def test_unprotected_when_stop_not_reduce_only(self):
        r = compare({"SOLUSDC": D("1")}, [{**SL, "reduceOnly": False}], {"SOLUSDC": D("1")}, {"sl1"})
        self.assertEqual(r.missing_protection, ["SOLUSDC"])

    def test_fetch_wrapper(self):
        def h(m, p, q):
            if p == "/fapi/v2/positionRisk": return 200, [{"symbol": "SOLUSDC", "positionAmt": "1.5"}, {"symbol": "A", "positionAmt": "0"}]
            return 200, [SL]
        c = SignedClient("testnet", KEYS, transport=Fake(h), clock_ms=lambda: 1)
        self.assertEqual(reconcile(c, {"SOLUSDC": D("1.5")}, {"sl1"}).ok, True)


class KillTests(unittest.TestCase):
    def test_switch(self):
        with tempfile.TemporaryDirectory() as d:
            k = KillSwitch(Path(d) / "state")
            self.assertFalse(k.active)
            k.engage("test\nreason"); self.assertTrue(k.active); self.assertEqual(k.reason(), "test reason")

    def test_flatten(self):
        def h(m, p, q):
            if p == "/fapi/v2/positionRisk":
                return 200, [{"symbol": "SOLUSDC", "positionAmt": "-2.5"}, {"symbol": "ETHUSDC", "positionAmt": "0"}]
            if p == "/fapi/v1/openOrders": return 200, [{"symbol": "SOLUSDC"}]
            if p == "/fapi/v1/order": return 200, {"clientOrderId": q["newClientOrderId"], "status": "FILLED"}
            return 200, {"code": 200}
        f = Fake(h); c = SignedClient("testnet", KEYS, transport=f, clock_ms=lambda: 1)
        res = flatten(c, None)
        self.assertEqual((res.closed, res.errors), (["SOLUSDC"], {}))
        self.assertEqual(f.calls("DELETE", "/fapi/v1/allOpenOrders")[0][2]["symbol"], "SOLUSDC")
        o = f.calls("POST", "/fapi/v1/order")[0][2]
        self.assertEqual((o["side"], o["reduceOnly"], o["quantity"], o["type"]), ("BUY", "true", "2.5", "MARKET"))

    def test_cli_kill_and_mainnet_flatten_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["kill", "--state", d]), 0)
            self.assertTrue((Path(d) / "KILL").exists())
            from quantos.live.client import ModeError
            with self.assertRaises(ModeError): main(["kill", "--state", d, "--flatten", "--mode", "mainnet"])


class GuardTests(unittest.TestCase):
    def test_trip_and_daily_reset(self):
        with tempfile.TemporaryDirectory() as d:
            k = KillSwitch(d); g = DailyLossGuard(D("1000"), kill=k)
            t0 = 10 * DAY_MS + 1000
            self.assertFalse(g.update(D("950"), t0)); self.assertFalse(k.active)
            self.assertTrue(g.update(D("900"), t0 + 5)); self.assertTrue(k.active)
            self.assertTrue(g.update(D("990"), t0 + 10))  # stays tripped within the day
            self.assertFalse(g.update(D("900"), t0 + DAY_MS))  # new UTC day: baseline reset
            self.assertTrue(g.update(D("809"), t0 + DAY_MS + 1))


if __name__ == "__main__":
    unittest.main()
