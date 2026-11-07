import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest

from quantos.paper.runtime import Runtime
from quantos.paper.stream import QuoteStream, WebSocket

T = 1800000000000
BOOK = json.dumps({"stream": "x@bookTicker", "data": {"s": "AAVEUSDC", "b": "100.0", "B": "2", "a": "100.1", "A": "3", "T": T}})
MARK = json.dumps({"stream": "x@markPrice@1s", "data": {"e": "markPriceUpdate", "E": T, "s": "AAVEUSDC", "p": "100.05"}})


def frame(payload: bytes, opcode=1):
    n = len(payload)
    size = bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n)
    return bytes([0x80 | opcode]) + size + payload


class FakeSocket:
    def __init__(self, data): self.data, self.sent = data, b""
    def recv(self, n):
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk
    def sendall(self, data): self.sent += data


def socket_with(data):
    ws = WebSocket.__new__(WebSocket)
    ws.sock, ws.buffer = FakeSocket(data), b""
    return ws


class FrameTests(unittest.TestCase):
    def test_reads_small_and_extended_frames(self):
        big = b"x" * 300
        ws = socket_with(frame(b"hi") + frame(big))
        self.assertEqual((ws.recv(), ws.recv()), ("hi", big.decode()))

    def test_ping_gets_a_masked_pong(self):
        ws = socket_with(frame(b"abc", 9) + frame(b"after"))
        self.assertEqual(ws.recv(), "after")
        sent = ws.sock.sent
        self.assertEqual((sent[0], sent[1]), (0x80 | 10, 0x80 | 3))
        mask, body = sent[2:6], sent[6:]
        self.assertEqual(bytes(b ^ mask[i % 4] for i, b in enumerate(body)), b"abc")

    def test_close_and_end_of_stream_raise(self):
        for data in (frame(b"", 8), b""):
            with self.assertRaises(ConnectionError): socket_with(data).recv()


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.now = T + 500
        self.s = QuoteStream(clock_ms=lambda: self.now)
        self.s.up = {c[0] for c in self.s.channels}

    def test_quote_needs_book_and_mark(self):
        self.s.apply(BOOK)
        self.assertEqual(self.s.quotes(["AAVEUSDC"]), {})
        self.s.apply(MARK)
        q = self.s.quotes(["AAVEUSDC"])["AAVEUSDC"]
        self.assertEqual((str(q.bid), str(q.ask), str(q.mark), q.time_ms, q.received_ms), ("100.0", "100.1", "100.05", T, self.now))

    def test_stale_mark_quiet_book_and_lost_socket(self):
        self.s.apply(BOOK); self.s.apply(MARK)
        self.now = T + 6000                                  # mark too old
        self.assertEqual(self.s.quotes(["AAVEUSDC"]), {})
        self.s.marks["AAVEUSDC"] = ("100.05", T + 20_000)
        self.now = T + 20_500                                # quiet book, fresh mark: still fine
        self.assertIn("AAVEUSDC", self.s.quotes(["AAVEUSDC"]))
        self.s.up = {self.s.channels[0][0]}                  # one socket down
        self.assertEqual(self.s.quotes(["AAVEUSDC"]), {})

    def test_control_messages_are_ignored(self):
        self.s.apply('{"result":null,"id":1}')
        self.assertEqual((self.s.books, self.s.marks), ({}, {}))

    def test_subscribes_and_reconnects_after_a_drop(self):
        conns = []

        class Conn:
            def __init__(self, url): self.sent, self.n = [], len(conns); conns.append(self)
            def send(self, data): self.sent.append(json.loads(data))
            def recv(self):
                if self.n == 0: raise ConnectionError("dropped")
                time.sleep(.01)
                return "{}"
            def close(self): pass

        s = QuoteStream(channels=(("wss://a", "@bookTicker"),), connect=Conn)
        s.watch(["AAVEUSDC"])
        deadline = time.time() + 5
        while len(conns) < 2 and time.time() < deadline: time.sleep(.02)
        time.sleep(.05)
        s.stop.set()
        self.assertGreaterEqual(len(conns), 2)
        self.assertEqual(conns[1].sent[0]["params"], ["aaveusdc@bookTicker"])
        self.assertEqual(conns[1].sent[0]["method"], "SUBSCRIBE")


class Market:
    calls = 0
    def quotes(self, symbols, skip_invalid=False):
        self.calls += 1
        return {s: FakeQuote(self.calls) for s in symbols}


class FakeQuote:
    def __init__(self, t): self.time_ms = self.mark_time_ms = t


class Stream:
    def __init__(self, batch): self.batch = batch
    def watch(self, symbols): pass
    def quotes(self, symbols): return dict(self.batch)


class LiveQuotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Runtime(Path(self.tmp.name), market_factory=Market)

    def test_injected_market_never_opens_a_socket(self):
        self.assertIsNone(self.r.stream_factory)

    def test_without_stream_uses_rest(self):
        self.assertEqual(list(self.r.live_quotes(None, Market(), ["A"])), ["A"])

    def test_stream_down_falls_back_to_rest_at_most_every_2s(self):
        m = Market()
        self.assertEqual(list(self.r.live_quotes(Stream({}), m, ["A"])), ["A"])
        self.assertIsNone(self.r.live_quotes(Stream({}), m, ["A"]))
        self.assertEqual(m.calls, 1)

    def test_stream_quotes_win_and_rest_fills_only_the_gap(self):
        m, q = Market(), FakeQuote(1)
        self.assertEqual(self.r.live_quotes(Stream({"A": q}), m, ["A"]), {"A": q})
        self.assertEqual(m.calls, 0)

    def test_unchanged_prices_are_not_re_emitted(self):
        m, q = Market(), FakeQuote(1)
        self.assertIsNotNone(self.r.live_quotes(Stream({"A": q}), m, ["A"]))
        self.assertIsNone(self.r.live_quotes(Stream({"A": q}), m, ["A"]))
        self.assertIsNotNone(self.r.live_quotes(Stream({"A": FakeQuote(2)}), m, ["A"]))
