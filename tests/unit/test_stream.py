import json
import socket
import struct
import threading
import unittest

from quantos.paper.stream import QuoteStream, WebSocket


def frame(op, data=b"", fin=True):
    n = len(data)
    head = bytes([(0x80 if fin else 0) | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n))
    return head + data


class FakeSock:
    def __init__(self, chunks): self.chunks, self.sent = list(chunks), []
    def recv(self, n):
        if not self.chunks: raise socket.timeout()
        c = self.chunks.pop(0)
        if c is None: raise socket.timeout()
        return c
    def sendall(self, b): self.sent.append(b)


def ws_with(chunks):
    ws = WebSocket.__new__(WebSocket)
    ws.sock, ws.buf, ws.parts = FakeSock(chunks), b"", []
    return ws


def msg(kind, s, **f):
    return json.dumps({"stream": "x", "data": {"e": kind, "s": s, **f}})


BOOK = dict(b="4.10", B="10", a="4.11", A="12", T=1_000)
MARK = dict(p="4.105", E=1_000)


class WebSocketTests(unittest.TestCase):
    def test_timeout_mid_frame_loses_nothing_and_pings_are_answered(self):
        data = b'{"a":1}' * 30  # 210 bytes: 16-bit length
        f = frame(0x1, data)
        ws = ws_with([frame(0x9, b"hi") + f[:5], None, f[5:]])
        with self.assertRaises(socket.timeout):
            ws.recv()
        self.assertEqual(ws.recv(), data.decode())
        self.assertEqual(ws.sock.sent[0][0], 0x8A)  # pong

    def test_fragmented_message_and_close(self):
        ws = ws_with([frame(0x1, b"ab", fin=False) + frame(0x0, b"cd"), frame(0x8)])
        self.assertEqual(ws.recv(), "abcd")
        with self.assertRaises(ConnectionError):
            ws.recv()


class QuoteStreamTests(unittest.TestCase):
    def stream(self, now):
        q = QuoteStream(lambda: {"NEARUSDC"}, clock_ms=lambda: now[0])
        q.subscribed = {c: frozenset({"NEARUSDC"}) for c in q.CHANNELS}
        q.up = {c: True for c in q.CHANNELS}
        return q

    def test_quotes_validated_like_rest(self):
        now = [1_500]
        q = self.stream(now)
        self.assertIsNone(q.quotes(["NEARUSDC"], 1_500))  # nothing received yet -> REST
        q.handle(msg("bookTicker", "NEARUSDC", **BOOK)); q.handle(msg("markPriceUpdate", "NEARUSDC", **MARK))
        quote = q.quotes(["NEARUSDC"], 1_500)["NEARUSDC"]
        self.assertEqual((str(quote.bid), str(quote.ask), str(quote.mark)), ("4.10", "4.11", "4.105"))
        self.assertIsNone(q.quotes(["NEARUSDC", "BTCUSDC"], 1_500))  # unsubscribed symbol -> REST

    def test_silent_or_disconnected_stream_falls_back(self):
        now = [1_500]
        q = self.stream(now)
        q.handle(msg("bookTicker", "NEARUSDC", **BOOK)); q.handle(msg("markPriceUpdate", "NEARUSDC", **MARK))
        self.assertIsNone(q.quotes(["NEARUSDC"], 1_500 + 6_000))  # marks silent > 5s
        q.up["mark"] = False
        self.assertIsNone(q.quotes(["NEARUSDC"], 1_500))

    def test_stale_mark_is_skipped_not_served(self):
        now = [10_000]
        q = self.stream(now)
        q.handle(msg("bookTicker", "NEARUSDC", **BOOK)); q.handle(msg("markPriceUpdate", "NEARUSDC", **MARK))
        self.assertIsNone(q.quotes(["NEARUSDC"], 10_000))  # mark 9s old: invalid -> nothing -> REST

    def test_book_and_mark_use_separate_endpoints_and_reconnect(self):
        wanted, opened = [{"NEARUSDC"}], []
        stop = threading.Event()

        class Conn:
            def __init__(self, host, path):
                opened.append(path)
                if path.startswith("/public") and sum(p.startswith("/public") for p in opened) == 1:
                    raise OSError("refused")
            def settimeout(self, s): pass
            def recv(self):
                books = [p for p in opened if p.startswith("/public")]
                if len(books) == 2: wanted[0] = {"NEARUSDC", "BTCUSDC"}
                elif len(books) >= 3: stop.set()
                return msg("markPriceUpdate", "NEARUSDC", **MARK)
            def close(self): pass

        q = QuoteStream(lambda: wanted[0], connect=Conn, clock_ms=lambda: 0)
        stop.wait = lambda t=None: None  # no real backoff sleep
        q._loop(stop, "book")
        books = [p for p in opened if p.startswith("/public")]
        self.assertEqual(len(books), 3)
        self.assertIn("btcusdc@bookTicker", books[2])
        stop.clear()
        marks = []
        q2 = QuoteStream(lambda: {"NEARUSDC"}, connect=lambda h, p: (marks.append(p), stop.set(), Conn(h, "/x"))[2],
                         clock_ms=lambda: 0)
        q2._loop(stop, "mark")
        self.assertEqual(marks, ["/market/stream?streams=nearusdc@markPrice@1s"])


if __name__ == "__main__":
    unittest.main()


class UserStreamTests(unittest.TestCase):
    def test_keeps_key_alive_reconnects_on_expiry_and_hands_events_to_the_main_loop(self):
        from quantos.paper.stream import UserStream
        clock = [0]
        stop = threading.Event()

        class Client:
            ws_host, keys, keepalives = "demo-fstream.binance.com", 0, 0
            def listen_key(self): self.keys += 1; return f"k{self.keys}"
            def keepalive_listen_key(self): self.keepalives += 1

        paths = []

        class Conn:
            def __init__(self, host, path):
                paths.append((host, path))
                self.script = [json.dumps({"e": "ACCOUNT_UPDATE", "a": {"P": []}}),
                               json.dumps({"e": "ORDER_TRADE_UPDATE", "o": {"X": "FILLED"}}), None,
                               json.dumps({"e": "listenKeyExpired"})] if len(paths) == 1 else [None]
            def settimeout(self, s): pass
            def close(self): pass
            def recv(self):
                clock[0] += 31 * 60_000          # each read jumps past the keepalive interval
                if len(paths) > 1: stop.set()
                item = self.script.pop(0) if self.script else None
                if item is None: raise socket.timeout()
                return item

        client = Client()
        u = UserStream(client, connect=Conn, clock_ms=lambda: clock[0])
        u.run(stop)
        self.assertEqual(paths[0], ("demo-fstream.binance.com", "/private/ws/k1"))
        self.assertEqual(paths[1][1], "/private/ws/k2")    # new key after listenKeyExpired
        self.assertGreaterEqual(client.keepalives, 1)
        self.assertEqual([e["e"] for e in u.drain()], ["ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE"])
        self.assertEqual(u.drain(), [])
        self.assertFalse(u.up)
