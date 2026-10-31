import re, unittest
from decimal import Decimal as D

from quantos.live.client import SignedClient, UnknownOrder, BinanceError
from quantos.live.orders import (client_order_id, ID_RE, market_entry, stop_market_protect, take_profit_market,
                                 reduce_only_close, place_with_protection, ProtectionFailed, submit)
from tests.unit.live_fakes import Fake, KEYS, INST


class IdTests(unittest.TestCase):
    def test_deterministic_valid_distinct(self):
        a = client_order_id("run" * 30, "sig/1:é", "entry")
        self.assertEqual(a, client_order_id("run" * 30, "sig/1:é", "entry"))
        self.assertTrue(ID_RE.match(a))
        ids = {client_order_id("r", "s", l) for l in ("entry", "sl", "tp", "close")}
        self.assertEqual(len(ids), 4)
        with self.assertRaises(ValueError): client_order_id("r", "s", "bogus")


class BuilderTests(unittest.TestCase):
    def test_rounding(self):
        self.assertEqual(market_entry("SOLUSDC", "BUY", "1.239", "c", INST)["quantity"], D("1.23"))
        s = stop_market_protect("SOLUSDC", "SELL", "99.991", "c", INST)
        self.assertEqual(s["stopPrice"], D("100.00"))
        self.assertTrue(s["closePosition"]); self.assertNotIn("quantity", s); self.assertNotIn("reduceOnly", s)
        self.assertEqual((s["workingType"], s["priceProtect"]), ("MARK_PRICE", "TRUE"))
        s2 = stop_market_protect("SOLUSDC", "BUY", "100.009", "c", INST, qty="2.005")
        self.assertEqual((s2["stopPrice"], s2["quantity"], s2["reduceOnly"]), (D("100.00"), D("2.00"), True))
        self.assertNotIn("closePosition", s2)
        t = take_profit_market("SOLUSDC", "SELL", "120.019", "1", "c", INST)
        self.assertEqual(t["stopPrice"], D("120.01")); self.assertTrue(t["reduceOnly"])
        with self.assertRaises(ValueError): market_entry("SOLUSDC", "BUY", "0.001", "c", INST)
        self.assertTrue(reduce_only_close("SOLUSDC", "SELL", "1", "c")["reduceOnly"])


def exchange(fail_sl=False, fail_close=False, store=None):
    store = {} if store is None else store
    def h(m, p, q):
        if (m, p) == ("POST", "/fapi/v1/order"):
            if q["type"] == "STOP_MARKET" and fail_sl: return 400, {"code": -2021, "msg": "would trigger"}
            if q.get("reduceOnly") == "true" and q["type"] == "MARKET" and fail_close: return 400, {"code": -2022, "msg": "rej"}
            o = {"symbol": q["symbol"], "clientOrderId": q["newClientOrderId"], "type": q["type"],
                 "status": "FILLED" if q["type"] == "MARKET" else "NEW", "executedQty": q.get("quantity", "0")}
            store[q["newClientOrderId"]] = o
            return 200, o
        if (m, p) == ("GET", "/fapi/v1/order"):
            o = store.get(q["origClientOrderId"])
            return (200, o) if o else (400, {"code": -2013, "msg": "no"})
        return 200, {}
    return h


class PlaceTests(unittest.TestCase):
    def client(self, f): return SignedClient("testnet", KEYS, transport=f, clock_ms=lambda: 1, conditional_api="legacy")

    def test_happy_path(self):
        f = Fake(exchange()); c = self.client(f)
        r = place_with_protection(c, INST, "BUY", "1", "99.5", "run", "sig", tp_price="110")
        types = [x[2]["type"] for x in f.calls("POST", "/fapi/v1/order")]
        self.assertEqual(types, ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"])
        self.assertEqual(r.sl["clientOrderId"], client_order_id("run", "sig", "sl"))
        self.assertEqual(f.calls("POST", "/fapi/v1/order")[1][2]["side"], "SELL")

    def test_sl_failure_closes(self):
        f = Fake(exchange(fail_sl=True)); c = self.client(f)
        with self.assertRaises(ProtectionFailed) as cm: place_with_protection(c, INST, "BUY", "1", "99.5", "run", "sig")
        posts = [x[2] for x in f.calls("POST", "/fapi/v1/order")]
        self.assertEqual(posts[-1]["reduceOnly"], "true"); self.assertEqual(posts[-1]["side"], "SELL")
        self.assertEqual(posts[-1]["newClientOrderId"], client_order_id("run", "sig", "close"))
        self.assertIn("closed", str(cm.exception))

    def test_sl_and_close_failure_reported(self):
        c = self.client(Fake(exchange(fail_sl=True, fail_close=True)))
        with self.assertRaises(ProtectionFailed) as cm: place_with_protection(c, INST, "SELL", "1", "100.5", "r", "s")
        self.assertIn("emergency close failed", str(cm.exception))


class AmbiguousTests(unittest.TestCase):
    def test_timeout_queries_instead_of_resubmitting(self):
        store, state = {}, {"n": 0}
        base = exchange(store=store)
        def transport(req, t):
            f(req, t)  # record
            raise TimeoutError()
        f = Fake(base)
        # first POST reaches the exchange (order stored) but response is lost
        def t2(req, timeout):
            if req.get_method() == "POST":
                f(req, timeout); raise TimeoutError()
            return f(req, timeout)
        c = SignedClient("testnet", KEYS, transport=t2, clock_ms=lambda: 1)
        out = submit(c, market_entry("SOLUSDC", "BUY", "1", "cid1", INST))
        self.assertEqual(out["clientOrderId"], "cid1")
        self.assertEqual(len(f.calls("POST", "/fapi/v1/order")), 1)
        self.assertEqual(f.calls("GET", "/fapi/v1/order")[0][2]["origClientOrderId"], "cid1")

    def test_absent_order_resubmitted_once(self):
        f = Fake(exchange()); n = {"p": 0}
        def t(req, timeout):
            if req.get_method() == "POST":
                n["p"] += 1
                if n["p"] == 1: raise TimeoutError()  # never reached exchange
            return f(req, timeout)
        c = SignedClient("testnet", KEYS, transport=t, clock_ms=lambda: 1)
        submit(c, market_entry("SOLUSDC", "BUY", "1", "cid2", INST))
        self.assertEqual(n["p"], 2)


if __name__ == "__main__":
    unittest.main()


class AlgoTests(unittest.TestCase):
    def test_stop_goes_to_algo_endpoint_with_trigger_price(self):
        seen = []
        def handler(method, path, p):
            seen.append((method, path, p))
            if path == "/fapi/v1/order" and p.get("type") == "MARKET":
                return 200, {"symbol": "SOLUSDC", "clientOrderId": p.get("newClientOrderId") or p.get("origClientOrderId"),
                             "status": "FILLED", "executedQty": "1"}
            if path == "/fapi/v1/algoOrder":
                return 200, {"clientAlgoId": p["clientAlgoId"], "orderType": p["type"], "algoStatus": "NEW"}
            return 400, {"code": -1, "msg": "unexpected"}
        c = SignedClient("testnet", KEYS, transport=Fake(handler), clock_ms=lambda: 1)
        r = place_with_protection(c, INST, "BUY", "1", "99.5", "run", "sig")
        algo = [x for x in seen if x[1] == "/fapi/v1/algoOrder"]
        self.assertEqual(len(algo), 1)
        p = algo[0][2]
        self.assertEqual((p["algoType"], p["type"], p["triggerPrice"], p["side"], p["closePosition"]),
                         ("CONDITIONAL", "STOP_MARKET", "99.5", "SELL", "true"))
        self.assertNotIn("stopPrice", p)
        self.assertEqual(r.sl["clientAlgoId"], client_order_id("run", "sig", "sl"))

    def test_dry_run_algo_and_reconcile_sees_protection(self):
        from quantos.live.reconcile import compare
        c = SignedClient("dry-run", None, clock_ms=lambda: 1)
        place_with_protection(c, INST, "BUY", "1", "99.5", "run", "sig")
        orders = c.open_algo_orders()
        self.assertEqual(len(orders), 1)
        report = compare({"SOLUSDC": "1"}, orders, {"SOLUSDC": "1"}, {client_order_id("run", "sig", "sl")})
        self.assertTrue(report.ok, report)
