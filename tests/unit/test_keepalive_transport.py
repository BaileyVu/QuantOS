import http.client
import unittest
from urllib.error import URLError
from urllib.request import Request

from quantos.paper.market import KeepAliveTransport


class FakeResponse:
    def __init__(self, status=200, body=b"[]", will_close=False):
        self.status, self._body, self.will_close, self.headers = status, body, will_close, {}

    def read(self): return self._body


class FakeConnection:
    def __init__(self, script):
        self.script, self.sock, self.timeout, self.closed, self.requests = script, None, None, False, []

    def request(self, method, path, headers=None):
        self.requests.append((method, path))
        step = self.script.pop(0)
        if isinstance(step, Exception): raise step
        self._next = step

    def getresponse(self): return self._next
    def close(self): self.closed = True


class KeepAliveTests(unittest.TestCase):
    def setUp(self):
        self.made, self.scripts = [], []

    def factory(self, host, timeout):
        conn = FakeConnection(self.scripts.pop(0))
        self.made.append((host, conn))
        return conn

    def req(self, path="/fapi/v1/ping?x=1"):
        return Request("https://fapi.binance.com" + path, method="GET")

    def test_reuses_one_connection_across_polls(self):
        self.scripts = [[FakeResponse(), FakeResponse(), FakeResponse()]]
        t = KeepAliveTransport(self.factory)
        for _ in range(3):
            self.assertEqual(t(self.req(), 5)[:2], (200, b"[]"))
        self.assertEqual(len(self.made), 1)
        self.assertEqual(self.made[0][1].requests[0], ("GET", "/fapi/v1/ping?x=1"))

    def test_dropped_keepalive_retries_once_on_fresh_socket(self):
        self.scripts = [[FakeResponse(), http.client.RemoteDisconnected("idle close")], [FakeResponse(body=b"{}")]]
        t = KeepAliveTransport(self.factory)
        t(self.req(), 5)
        self.assertEqual(t(self.req(), 5)[1], b"{}")
        self.assertEqual(len(self.made), 2)
        self.assertTrue(self.made[0][1].closed)

    def test_failure_on_fresh_socket_is_urlerror(self):
        self.scripts = [[ConnectionResetError("reset")]]
        with self.assertRaises(URLError):
            KeepAliveTransport(self.factory)(self.req(), 5)

    def test_timeout_propagates_and_drops_connection(self):
        self.scripts = [[TimeoutError()], [FakeResponse()]]
        t = KeepAliveTransport(self.factory)
        with self.assertRaises(TimeoutError):
            t(self.req(), 5)
        self.assertEqual(t(self.req(), 5)[0], 200)   # next poll opens a new socket
        self.assertEqual(len(self.made), 2)

    def test_server_close_header_forces_new_connection(self):
        self.scripts = [[FakeResponse(will_close=True)], [FakeResponse()]]
        t = KeepAliveTransport(self.factory)
        t(self.req(), 5); t(self.req(), 5)
        self.assertEqual(len(self.made), 2)

    def test_each_thread_gets_its_own_connection(self):
        # The scanner maps candle loads over a 4-thread pool with one shared client (11-03 segfault).
        import threading
        lock, made = threading.Lock(), []

        def factory(host, timeout):
            conn = FakeConnection([FakeResponse() for _ in range(5)])
            with lock: made.append(conn)
            return conn

        t = KeepAliveTransport(factory)
        start = threading.Barrier(4)

        def worker():
            start.wait()
            for _ in range(5): t(self.req(), 5)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for th in threads: th.start()
        for th in threads: th.join()
        self.assertEqual(len(made), 4)
        self.assertTrue(all(len(c.requests) == 5 for c in made))

    def test_https_only(self):
        with self.assertRaises(URLError):
            KeepAliveTransport(self.factory)(Request("http://fapi.binance.com/x"), 5)


if __name__ == "__main__":
    unittest.main()
