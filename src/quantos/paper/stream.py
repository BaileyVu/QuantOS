"""Live prices over Binance's public WebSockets, standard library only (the service Python has no websockets).

Binance serves bookTicker on /public and mark price on /market, so two sockets run side by side, each reconnecting by
itself. `quotes` returns only what is fresh, so the feed falls back to REST whenever a stream is down or silent.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlsplit

from .market import PublicMarket
from .types import BOOK_MAX_AGE_MS, Quote

# (url, stream suffix): checked live 2026-11-07; /stream and /market carry no bookTicker, /public no mark price.
WS_CHANNELS = (("wss://fstream.binance.com/public/stream", "@bookTicker"),
               ("wss://fstream.binance.com/market/stream", "@markPrice@1s"))
MAX_FRAME = 1 << 20
SILENCE_S = 5.0   # markPrice ticks every second, so this long without a frame means the socket is dead
MARK_MAX_AGE_MS = 5000


class WebSocket:
    """Minimal RFC 6455 client: text frames, ping/pong, close. Client frames are masked as the RFC requires."""
    def __init__(self, url, timeout=SILENCE_S):
        u = urlsplit(url)
        raw = socket.create_connection((u.hostname, u.port or 443), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk or len(head) > 8192: raise ConnectionError("websocket handshake failed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket refused: {head.split(bytes([13]), 1)[0][:60]!r}")
        self.buffer = b""

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk: raise ConnectionError("websocket closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def send(self, payload: bytes, opcode=1):
        mask = os.urandom(4)
        n = len(payload)
        size = bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
        self.sock.sendall(bytes([0x80 | opcode]) + size + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self) -> str:
        message = b""
        while True:
            first, second = self._read(2)
            opcode, n = first & 0x0F, second & 0x7F
            if second & 0x80: raise ConnectionError("masked server frame")
            if n == 126: n = struct.unpack(">H", self._read(2))[0]
            elif n == 127: n = struct.unpack(">Q", self._read(8))[0]
            if n > MAX_FRAME: raise ConnectionError("websocket frame too large")
            payload = self._read(n)
            if opcode == 8: raise ConnectionError("websocket closed by server")
            if opcode == 9: self.send(payload, 10)
            elif opcode in (0, 1, 2):
                message += payload
                if first & 0x80: return message.decode()

    def close(self):
        try: self.sock.close()
        except OSError: pass


class QuoteStream:
    def __init__(self, channels=WS_CHANNELS, clock_ms=None, connect=WebSocket):
        self.channels, self.connect = channels, connect
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.books, self.marks, self.wanted = {}, {}, set()
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.up, self.reconnects, self.threads = set(), 0, []

    def watch(self, symbols):
        with self.lock: self.wanted = set(symbols)
        if not self.threads:
            self.threads = [threading.Thread(target=self.run, args=c, daemon=True, name=f"quote-stream{c[1]}")
                            for c in self.channels]
            for t in self.threads: t.start()

    def apply(self, frame: str):
        msg = json.loads(frame)
        d = msg.get("data", msg)
        if not isinstance(d, dict) or "s" not in d: return
        now = self.clock_ms()
        with self.lock:
            if d.get("e") == "markPriceUpdate": self.marks[d["s"]] = (d["p"], int(d["E"]))
            elif "b" in d and "a" in d:
                self.books[d["s"]] = (d["b"], d["a"], d["B"], d["A"], int(d.get("T") or d.get("E") or now), now)

    def quotes(self, symbols) -> dict[str, Quote]:
        """Fresh quotes only, validated like REST: a quiet book may be up to BOOK_MAX_AGE_MS old, the mark 5s."""
        now, out = self.clock_ms(), {}
        with self.lock:
            connected = len(self.up) == len(self.channels)
            for s in symbols:
                book, mark = self.books.get(s), self.marks.get(s)
                if not connected or book is None or mark is None: continue
                try:
                    out[s] = PublicMarket._make_quote(
                        s, {"symbol": s, "bidPrice": book[0], "askPrice": book[1], "bidQty": book[2], "askQty": book[3],
                            "time": min(book[4], now)},
                        {"symbol": s, "markPrice": mark[0], "time": min(mark[1], now)}, now)
                except ValueError:
                    pass
        return out

    def run(self, url, suffix):
        backoff = 1
        while not self.stop.is_set():
            ws = None
            try:
                ws, subscribed = self.connect(url), set()
                with self.lock: self.up.add(url)
                while not self.stop.is_set():
                    with self.lock: new = self.wanted - subscribed
                    if new:
                        ws.send(json.dumps({"method": "SUBSCRIBE", "id": int(time.time()),
                                            "params": sorted(f"{x.lower()}{suffix}" for x in new)}).encode())
                        subscribed |= new
                    self.apply(ws.recv())
                    backoff = 1
            except (OSError, ValueError, ConnectionError):  # includes ssl errors and the silence timeout
                pass
            finally:
                with self.lock:
                    self.up.discard(url)
                    self.reconnects += 1
                if ws is not None: ws.close()
            self.stop.wait(backoff)
            backoff = min(30, backoff * 2)
