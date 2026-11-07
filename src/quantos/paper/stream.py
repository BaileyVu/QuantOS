"""Live Binance USD-M quotes over WebSocket (bookTicker + markPrice@1s), stdlib only.

The REST poll stays as the fallback: `quotes()` returns None whenever the stream is not healthy, and
the feed then polls exactly as before. Rows are reshaped to the REST fields and validated by the same
`PublicMarket._make_quote`, so the engine sees identical Quote objects, just ~2s fresher.
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
from typing import Callable

from .market import PublicMarket
from .types import Quote

HOST, PORT = "fstream.binance.com", 443
SILENT_MS = 5_000          # no frame for 5s (marks arrive every 1s) -> unhealthy, reconnect
ROTATE_MS = 23 * 3600_000  # Binance closes connections at 24h; rotate before that


class WebSocket:
    """Minimal RFC 6455 client: text frames in, pong/close out. Enough for Binance market streams."""
    def __init__(self, host: str, path: str, timeout: float = 10):
        raw = socket.create_connection((host, PORT), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk: raise ConnectionError("handshake closed")
            head += chunk
        status, _, self.buf = head.partition(b"\r\n\r\n")
        self.parts = []
        line = status.split(b"\r\n")[0]
        if b" 101 " not in line + b" ":
            raise ConnectionError(f"handshake refused: {line[:80]!r}")

    def _frame(self):
        """(fin, opcode, payload) of the first complete frame in the buffer, else None. Nothing is
        consumed until a frame is whole, so a read timeout mid-frame loses no bytes."""
        b = self.buf
        if len(b) < 2: return None
        n, at = b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4: return None
            n, at = struct.unpack(">H", b[2:4])[0], 4
        elif n == 127:
            if len(b) < 10: return None
            n, at = struct.unpack(">Q", b[2:10])[0], 10
        mask = None
        if b[1] & 0x80:
            if len(b) < at + 4: return None
            mask, at = b[at:at + 4], at + 4
        if len(b) < at + n: return None
        data, self.buf = b[at:at + n], b[at + n:]
        if mask: data = bytes(x ^ mask[i % 4] for i, x in enumerate(data))
        return b[0] & 0x80, b[0] & 0x0F, data

    def _send(self, opcode: int, payload: bytes = b""):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self) -> str:
        """Next text message. Answers pings; raises ConnectionError on close; socket.timeout on silence."""
        while True:
            frame = self._frame()
            if frame is None:
                chunk = self.sock.recv(65536)
                if not chunk: raise ConnectionError("socket closed")
                self.buf += chunk
                continue
            fin, op, data = frame
            if op == 0x9: self._send(0xA, data); continue        # ping -> pong
            if op == 0xA: continue
            if op == 0x8: raise ConnectionError("server closed stream")
            self.parts.append(data)
            if fin:
                text, self.parts = b"".join(self.parts).decode(), []
                return text

    def settimeout(self, seconds: float): self.sock.settimeout(seconds)

    def close(self):
        try: self._send(0x8)
        except OSError: pass
        try: self.sock.close()
        except OSError: pass


class QuoteStream:
    """Background threads holding the latest book and mark per symbol. Binance serves high-frequency
    book streams on /public and mark-price streams on /market (a bookTicker+markPrice subscription on
    plain /stream delivered books only, 11-07), so each runs on its own socket. `symbols()` is read every
    second; a changed set reconnects with the new subscriptions."""
    CHANNELS = {"book": ("/public/stream?streams=", "{}@bookTicker", 60_000),
                "mark": ("/market/stream?streams=", "{}@markPrice@1s", SILENT_MS)}

    def __init__(self, symbols: Callable[[], set], connect: Callable = WebSocket,
                 clock_ms: Callable[[], int] = lambda: int(time.time() * 1000), on_event: Callable = None):
        self.symbols, self.connect, self.clock_ms = symbols, connect, clock_ms
        self.on_event = on_event or (lambda kind, detail: None)
        self.lock = threading.Lock()
        self.books, self.marks = {}, {}
        self.subscribed = {c: frozenset() for c in self.CHANNELS}
        self.last = {c: 0 for c in self.CHANNELS}
        self.up = {c: False for c in self.CHANNELS}
        self.messages = self.reconnects = 0

    @property
    def connected(self) -> bool: return all(self.up.values())

    def handle(self, text: str, channel: str | None = None):
        msg = json.loads(text).get("data") or {}
        s, kind = msg.get("s"), msg.get("e")
        with self.lock:
            if kind == "bookTicker":
                self.books[s] = {"symbol": s, "bidPrice": msg["b"], "bidQty": msg["B"], "askPrice": msg["a"],
                                 "askQty": msg["A"], "time": msg["T"]}
                self.last["book"] = self.clock_ms()
            elif kind == "markPriceUpdate":
                self.marks[s] = {"symbol": s, "markPrice": msg["p"], "time": msg["E"]}
                self.last["mark"] = self.clock_ms()
            self.messages += 1

    def seed_books(self, quotes: dict):
        """Books from a REST batch for symbols the stream has not seen since connecting. bookTicker only
        pushes on change, so a quiet book never arrives and every batch fell back to REST (share fell to
        57% on a quiet Saturday, 11-07). A book that later changes overwrites its seed."""
        with self.lock:
            for s, q in quotes.items():
                if s in self.subscribed["book"] and s not in self.books:
                    self.books[s] = {"symbol": s, "bidPrice": str(q.bid), "bidQty": str(q.bid_qty),
                                     "askPrice": str(q.ask), "askQty": str(q.ask_qty), "time": q.time_ms}

    def healthy(self, now_ms: int) -> bool:
        """Both sockets up and each heard within its silence limit (marks tick every 1s; a quiet book may not)."""
        return all(self.up[c] and now_ms - self.last[c] <= limit for c, (_, _, limit) in self.CHANNELS.items())

    def quotes(self, symbols, now_ms: int) -> dict[str, Quote] | None:
        """Validated quotes for every requested symbol, or None so the caller polls REST instead."""
        if not self.healthy(now_ms) or not all(set(symbols) <= sub for sub in self.subscribed.values()):
            return None
        out = {}
        with self.lock:
            for s in symbols:
                b, m = self.books.get(s), self.marks.get(s)
                if b is None or m is None: return None  # REST bookTicker covers a book quiet since connect
                try: out[s] = PublicMarket._make_quote(s, b, m, now_ms)
                except ValueError: pass  # stale/invalid: same skip_invalid rule as the REST batch
        return out or None

    def run(self, shutdown: threading.Event):
        marks = threading.Thread(target=self._loop, args=(shutdown, "mark"), daemon=True)
        marks.start()
        self._loop(shutdown, "book")
        marks.join(timeout=5)

    def _loop(self, shutdown: threading.Event, channel: str):
        prefix, stream, silent_ms = self.CHANNELS[channel]
        backoff = 1
        while not shutdown.is_set():
            wanted = frozenset(s for s in self.symbols() if s)
            if not wanted:
                shutdown.wait(1); continue
            ws = None
            try:
                ws = self.connect(HOST, prefix + "/".join(stream.format(s.lower()) for s in sorted(wanted)))
                ws.settimeout(1)
                with self.lock:
                    table = self.books if channel == "book" else self.marks
                    for k in [k for k in table if k not in wanted]: del table[k]
                    self.subscribed[channel], self.up[channel] = wanted, True
                opened = self.last[channel] = self.clock_ms()
                self.on_event("connected", {"channel": channel, "symbols": len(wanted)})
                backoff = 1
                while not shutdown.is_set():
                    try:
                        self.handle(ws.recv(), channel)
                    except socket.timeout:
                        pass
                    now = self.clock_ms()
                    if now - self.last[channel] > silent_ms:
                        raise ConnectionError(f"{channel} stream silent")
                    if now - opened > ROTATE_MS or frozenset(s for s in self.symbols() if s) != wanted:
                        break  # reconnect with fresh subscriptions
            except Exception as error:
                self.on_event("disconnected", {"channel": channel, "error": f"{type(error).__name__}: {error}"[:200]})
                shutdown.wait(backoff)
                backoff = min(30, backoff * 2)
            finally:
                self.up[channel] = False
                if ws is not None: ws.close()
                self.reconnects += 1


class UserStream:
    """Account events (ORDER_TRADE_UPDATE, ACCOUNT_UPDATE) on the private user-data stream. A background
    thread keeps the listenKey alive (PUT every 30 min; Binance expires it after 60) and reconnects with
    backoff; the main loop takes events with `drain()`. Nothing depends on it being up: the mirror still
    reconciles every 30s by REST, the stream only makes it react within ~2s."""
    PATH = "/private/ws/{}"   # fstream routes account data under /private (books /public, marks /market)
    KEEPALIVE_MS = 30 * 60_000

    def __init__(self, client, host: str | None = None, connect: Callable = WebSocket,
                 clock_ms: Callable[[], int] = lambda: int(time.time() * 1000), on_event: Callable = None):
        self.client, self.host, self.connect, self.clock_ms = client, host or client.ws_host, connect, clock_ms
        self.on_event = on_event or (lambda kind, detail: None)
        self.lock = threading.Lock()
        self.events, self.up, self.down_since = [], False, None
        self.messages = self.reconnects = 0

    def drain(self) -> list:
        with self.lock:
            out, self.events = self.events, []
        return out

    def handle(self, text: str):
        msg = json.loads(text)
        msg = msg.get("data", msg)
        if msg.get("e") == "listenKeyExpired":
            raise ConnectionError("listenKey expired")
        if msg.get("e") in ("ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE"):
            with self.lock:
                self.events.append(msg)
                del self.events[:-1000]  # bounded if nobody drains
        self.messages += 1

    def run(self, shutdown: threading.Event):
        backoff = 1
        while not shutdown.is_set():
            ws = None
            try:
                key = self.client.listen_key()
                ws = self.connect(self.host, self.PATH.format(key))
                ws.settimeout(1)
                self.up, self.down_since = True, None
                opened = refreshed = self.clock_ms()
                self.on_event("connected", {"channel": "user"})
                backoff = 1
                while not shutdown.is_set():
                    try:
                        self.handle(ws.recv())
                    except socket.timeout:
                        pass
                    now = self.clock_ms()
                    if now - refreshed > self.KEEPALIVE_MS:
                        self.client.keepalive_listen_key()
                        refreshed = now
                    if now - opened > ROTATE_MS:
                        break
            except Exception as error:
                self.on_event("disconnected", {"channel": "user", "error": f"{type(error).__name__}: {error}"[:200]})
                shutdown.wait(backoff)
                backoff = min(60, backoff * 2)
            finally:
                if self.up or self.down_since is None:
                    self.down_since = self.clock_ms()
                self.up = False
                if ws is not None: ws.close()
                self.reconnects += 1
