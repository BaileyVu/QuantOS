import json, os, stat, tempfile, unittest
from pathlib import Path

from quantos.live.client import (SignedClient, sign, ModeError, UnknownOrder, TimestampError, MarginInsufficient,
                                 MinNotional, RateLimited, AmbiguousOrderState, CONFIRM_LINE)
from quantos.live.keys import load_keys, KeyError_, Keys
from tests.unit.live_fakes import Fake, KEYS

SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
Q = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"


class SignTests(unittest.TestCase):
    def test_known_vector(self):
        self.assertEqual(sign(SECRET, Q), "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71")

    def test_request_signature_and_header(self):
        f = Fake(lambda m, p, q: (200, {"orderId": 1}))
        c = SignedClient("testnet", KEYS, transport=f, clock_ms=lambda: 5000)
        c.new_order({"symbol": "SOLUSDC", "side": "BUY"})
        m, path, params, req = f.requests[-1]
        self.assertEqual(req.full_url.split("?")[1].rsplit("&signature=", 1)[0],
                         "symbol=SOLUSDC&side=BUY&recvWindow=5000&timestamp=1000")
        self.assertEqual(params["signature"], sign(SECRET, "symbol=SOLUSDC&side=BUY&recvWindow=5000&timestamp=1000"))
        self.assertEqual(req.get_header("X-mbx-apikey"), "AK")
        self.assertTrue(req.full_url.startswith("https://testnet.binancefuture.com/"))

    def test_server_time_offset(self):
        f = Fake(lambda m, p, q: (200, {}))
        c = SignedClient("testnet", KEYS, transport=f, clock_ms=lambda: 400)
        c.open_orders()
        self.assertEqual(f.requests[-1][2]["timestamp"], "1000")  # server 1000, local 400 -> offset 600

    def test_allowlist(self):
        c = SignedClient("testnet", KEYS, transport=Fake(lambda *a: (200, {})))
        with self.assertRaises(ModeError): c.request("POST", "/fapi/v1/batchOrders", {})
        with self.assertRaises(ModeError): c.request("GET", "/sapi/v1/capital/withdraw/apply", {})


class ErrorTests(unittest.TestCase):
    def run_err(self, status, body, method="GET"):
        c = SignedClient("testnet", KEYS, transport=Fake(lambda *a: (status, body)), clock_ms=lambda: 1)
        return c.request(method, "/fapi/v1/openOrders" if method == "GET" else "/fapi/v1/order", {})

    def test_mapping(self):
        for code, exc in [(-2011, UnknownOrder), (-1021, TimestampError), (-2019, MarginInsufficient), (-4164, MinNotional)]:
            with self.assertRaises(exc): self.run_err(400, {"code": code, "msg": "x"})
        with self.assertRaises(RateLimited): self.run_err(429, {})
        with self.assertRaises(RateLimited): self.run_err(418, {})

    def test_post_timeout_is_ambiguous(self):
        def boom(req, t): raise TimeoutError()
        c = SignedClient("testnet", KEYS, transport=boom, clock_ms=lambda: 1); c._offset = 0
        with self.assertRaises(AmbiguousOrderState): c.new_order({"symbol": "X"})

    def test_5xx_post_ambiguous(self):
        with self.assertRaises(AmbiguousOrderState): self.run_err(503, {"msg": "?"}, "POST")


class DryRunTests(unittest.TestCase):
    def test_nothing_sent_and_redacted(self):
        def boom(req, t): raise AssertionError("network")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "dry.jsonl"
            c = SignedClient("dry-run", KEYS, transport=boom, log_path=log, clock_ms=lambda: 7)
            ack = c.new_order({"symbol": "SOLUSDC", "side": "BUY", "type": "MARKET", "quantity": "1", "newClientOrderId": "a"})
            self.assertTrue(ack["dryRun"])
            text = log.read_text()
            self.assertIn("REDACTED", text)
            self.assertNotIn(SECRET, text); self.assertNotIn("AK\"", text)
            self.assertEqual(json.loads(text)["signature"], "REDACTED")


class MainnetTests(unittest.TestCase):
    def test_refused_without_flag_and_file(self):
        with tempfile.TemporaryDirectory() as d:
            ok, bad = Path(d) / "ok", Path(d) / "bad"
            ok.write_text(CONFIRM_LINE + "\n"); bad.write_text("yes")
            with self.assertRaises(ModeError): SignedClient("mainnet", KEYS)
            with self.assertRaises(ModeError): SignedClient("mainnet", KEYS, allow_mainnet=True)
            with self.assertRaises(ModeError): SignedClient("mainnet", KEYS, confirm_file=ok)
            with self.assertRaises(ModeError): SignedClient("mainnet", KEYS, allow_mainnet=True, confirm_file=bad)
            with self.assertRaises(ModeError): SignedClient("mainnet", KEYS, allow_mainnet=True, confirm_file=Path(d) / "nope")
            c = SignedClient("mainnet", KEYS, allow_mainnet=True, confirm_file=ok, transport=Fake(lambda *a: (200, {})))
            self.assertEqual(c.mode, "mainnet")


class KeyTests(unittest.TestCase):
    def test_load_and_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "k.json"
            p.write_text(json.dumps({"api_key": "AAA", "api_secret": "SSS"}))
            os.chmod(p, 0o644)
            with self.assertRaises(KeyError_): load_keys(p)
            os.chmod(p, 0o600)
            k = load_keys(p)
            self.assertEqual((repr(k), str(k)), ("Keys(****)", "Keys(****)"))
            self.assertNotIn("SSS", f"{k!r}{k}")
            os.environ["QUANTOS_KEY_FILE"] = str(p)
            try: self.assertEqual(load_keys().api_key, "AAA")
            finally: del os.environ["QUANTOS_KEY_FILE"]
            with self.assertRaises(KeyError_): load_keys(Path(d) / "missing")

    def test_inside_repo_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            p = Path(d) / "k.json"
            p.write_text(json.dumps({"api_key": "A", "api_secret": "S"})); os.chmod(p, 0o600)
            with self.assertRaises(KeyError_): load_keys(p)


if __name__ == "__main__":
    unittest.main()
