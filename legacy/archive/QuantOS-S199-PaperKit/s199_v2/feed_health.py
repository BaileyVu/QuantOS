from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BookState:
    connected: bool = False
    snapshot: bool = False
    sequence: int | None = None
    updated_ns: int = 0
    bid: float | None = None
    ask: float | None = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    depth: int = 0
    generation: int = 0
    fault: str | None = "not_connected"


@dataclass
class FeedHealth:
    max_age_ns: int
    books: dict[str, BookState] = field(default_factory=dict)

    def _book(self, instrument_id: str) -> BookState:
        return self.books.setdefault(instrument_id, BookState())

    def connect(self, instrument_id: str) -> None:
        book = self._book(instrument_id)
        book.connected = True
        book.snapshot = False
        book.sequence = None
        book.generation += 1
        book.fault = "awaiting_snapshot"

    def disconnect(self, instrument_id: str) -> None:
        book = self._book(instrument_id)
        book.connected = False
        book.snapshot = False
        book.fault = "disconnected"

    def snapshot(
        self, instrument_id: str, *, sequence: int, ts_ns: int,
        bid: float, ask: float, bid_size: float, ask_size: float, depth: int,
    ) -> None:
        book = self._book(instrument_id)
        if not book.connected:
            raise ValueError("snapshot received while disconnected")
        self._apply(book, sequence, ts_ns, bid, ask, bid_size, ask_size, depth)
        book.snapshot = book.fault is None

    def delta(
        self, instrument_id: str, *, sequence: int, ts_ns: int,
        bid: float, ask: float, bid_size: float, ask_size: float, depth: int,
    ) -> None:
        book = self._book(instrument_id)
        if not book.connected or not book.snapshot:
            book.fault = "delta_without_snapshot"
            return
        if book.sequence is None or sequence != book.sequence + 1:
            book.snapshot = False
            book.fault = "sequence_gap"
            return
        self._apply(book, sequence, ts_ns, bid, ask, bid_size, ask_size, depth)

    @staticmethod
    def _apply(book: BookState, sequence: int, ts_ns: int, bid: float, ask: float,
               bid_size: float, ask_size: float, depth: int) -> None:
        book.sequence, book.updated_ns = sequence, ts_ns
        book.bid, book.ask = bid, ask
        book.bid_size, book.ask_size, book.depth = bid_size, ask_size, depth
        if bid <= 0 or ask <= 0 or bid_size <= 0 or ask_size <= 0 or depth <= 0:
            book.fault = "empty_book"
        elif bid >= ask:
            book.fault = "crossed_book"
        else:
            book.fault = None

    def tradeable(self, instrument_ids: tuple[str, ...], *, now_ns: int) -> tuple[bool, str | None]:
        for instrument_id in instrument_ids:
            book = self.books.get(instrument_id)
            if book is None:
                return False, f"{instrument_id}:missing"
            if not book.connected or not book.snapshot or book.fault:
                return False, f"{instrument_id}:{book.fault or 'unhealthy'}"
            age = now_ns - book.updated_ns
            if age < 0 or age > self.max_age_ns:
                return False, f"{instrument_id}:stale"
        return True, None


def bounded_backoff(attempt: int, *, base: float = 0.5, cap: float = 30.0) -> float:
    if attempt < 0 or base <= 0 or cap <= 0:
        raise ValueError("invalid reconnect backoff")
    return min(cap, base * (2 ** min(attempt, 16)))
