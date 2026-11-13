"""Dashboard Close button: request file -> bot closes via its normal exit, mirror cancels stop/TP."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from quantos.live.bridge import ExchangeMirror
from quantos.paper import webdash
from quantos.paper.runtime import Runtime
from tests.unit import test_live_bridge as lb
Engine, FakeExchange, order = lb.Engine, lb.FakeExchange, lb.order
from tests.unit import test_v3_engine as v3
from tests.unit.test_v3_engine import T


class _Base(unittest.TestCase):
    setUp, submit, fill, close_at = v3.EngineTests.setUp, v3.EngineTests.submit, v3.EngineTests.fill, v3.EngineTests.close_at


class EngineManualClose(_Base):
    def test_manual_close_moves_filled_position_to_closing_and_tags_it(self):
        o = self.submit(); self.fill()
        self.assertEqual(self.e.manual_close(o["id"] if isinstance(o, dict) else self.e.active()[0]["id"], T + 1500),
                         "BTCUSDC")
        a = self.e.active()[0]
        self.assertEqual((a["state"], a["close_reason"], a["exclude_from_edge"]), ("CLOSING", "MANUAL_CLOSE", True))
        self.assertIsNone(self.e.manual_close(a["id"], T + 1600))  # idempotent: already closing
        self.close_at("100", T + 3000)
        self.assertEqual(self.e.active(), [])

    def test_manual_close_unknown_order_is_noop(self):
        self.assertIsNone(self.e.manual_close("nope", T))


class MirrorClosesWithProtection(unittest.TestCase):
    def test_closing_order_is_flattened_and_stop_tp_cancelled(self):
        with tempfile.TemporaryDirectory() as d:
            ex, e = FakeExchange(), Engine()
            m = ExchangeMirror(ex, "run1", Path(d), lambda *a: None)
            o = order(); o["signal"]["take_profit"] = "90.00"
            e.orders = [o]
            m.sync(e, 1)
            self.assertEqual(len(ex.algo), 2)  # stop + take-profit
            e.orders = [{**o, "state": "CLOSING"}]
            m.sync(e, 2)
            self.assertEqual(ex.pos["SOLUSDC"], 0)
            self.assertEqual(ex.algo, {})


class RuntimeConsumesRequests(unittest.TestCase):
    def test_request_file_is_recorded_applied_and_removed(self):
        class E:
            class journal: profile = "pD"
            def __init__(self): self.calls = []
            def manual_close(self, oid, now): self.calls.append(oid); return "SOLUSDC"
        class A:
            def __init__(self): self.alerts = []
            def alert(self, *a): self.alerts.append(a)
        with tempfile.TemporaryDirectory() as d:
            rt = Runtime.__new__(Runtime); rt.state = Path(d)
            recorded = []; rt.record = lambda kind, payload, now: recorded.append((kind, payload))
            (rt.state / "close_requests").mkdir()
            (rt.state / "close_requests" / "1.json").write_text(json.dumps({"profile": "pD", "order_id": "x1"}))
            (rt.state / "close_requests" / "2.json").write_text("garbage")
            (rt.state / "close_requests" / "3.json").write_text(json.dumps({"profile": "other", "order_id": "x2"}))
            e, a = E(), A()
            rt._manual_closes([e], a, T)
            self.assertEqual(e.calls, ["x1"])
            self.assertEqual(recorded, [("control", {"manual_close": "x1", "profile": "pD"})])
            self.assertEqual(list((rt.state / "close_requests").glob("*.json")), [])
            self.assertEqual(len(a.alerts), 1)


class WebdashCloseEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        (self.state / "dashboard").mkdir()
        (self.state / "dashboard" / "status.json").write_text(json.dumps({"engines": [{"profile": "pD", "open_positions": [
            {"id": "oid1", "symbol": "SOLUSDC", "state": "PROTECTED"},
            {"id": "oid2", "symbol": "ETHUSDC", "state": "CLOSING"}]}]}))
        self.runs = [("demo", self.state, "pD")]

    def test_validates_and_writes_request(self):
        self.assertEqual(webdash.request_close(self.runs, {"run": "demo", "order_id": "zz"})[0], 409)
        self.assertEqual(webdash.request_close(self.runs, {"run": "demo", "order_id": "oid2"})[0], 409)
        self.assertEqual(webdash.request_close(self.runs, {"run": "x", "order_id": "oid1"})[0], 404)
        self.assertEqual(webdash.request_close(self.runs, {"run": "demo"})[0], 400)
        code, body = webdash.request_close(self.runs, {"run": "demo", "order_id": "oid1"})
        self.assertEqual((code, body["symbol"]), (202, "SOLUSDC"))
        files = list((self.state / "close_requests").glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text())["order_id"], "oid1")

    def test_http_requires_token_and_local_origin(self):
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        threading.Thread(target=webdash.serve, args=(self.runs, None, port), daemon=True).start()
        import time
        for _ in range(50):
            try: page = urlopen(f"http://127.0.0.1:{port}/").read().decode(); break
            except OSError: time.sleep(0.05)
        token = page.split('name="qos-token" content="')[1].split('"')[0]
        def post(headers):
            r = Request(f"http://127.0.0.1:{port}/api/close", json.dumps({"run": "demo", "order_id": "oid1"}).encode(),
                        {"Content-Type": "application/json", **headers}, method="POST")
            try: return urlopen(r).status
            except HTTPError as e: return e.code
        self.assertEqual(post({}), 403)
        self.assertEqual(post({"X-QuantOS-Token": token, "Origin": "https://evil.example"}), 403)
        self.assertEqual(post({"X-QuantOS-Token": token, "Origin": f"http://127.0.0.1:{port}"}), 202)


class LibraryEndpoint(unittest.TestCase):
    def test_reads_local_file_or_reports_missing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lib.json"
            self.assertFalse(webdash.library_view(path)["available"])
            path.write_text(json.dumps({"strategies": {"crash_flush_e2": {"name": "Crash"}}}))
            out = webdash.library_view(path)
            self.assertTrue(out["available"]); self.assertEqual(out["strategies"]["crash_flush_e2"]["name"], "Crash")
            path.write_text("not json")
            self.assertFalse(webdash.library_view(path)["available"])


if __name__ == "__main__":
    unittest.main()
