"""Exclusive, bounded hash-chained event journal; no historical mutation."""
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import os
from pathlib import Path

from quantos.domain.market_data.hft import DepthSnapshot, DepthDelta, AggregateTrade, BookTicker, MarkPriceEvent
from quantos.domain.market_data.intelligence import IntelligenceEvent
from quantos.infrastructure.storage.retail_paper import _encode, _decode

_TYPES = {cls.__name__: cls for cls in (DepthSnapshot, DepthDelta, AggregateTrade, BookTicker, MarkPriceEvent, IntelligenceEvent)}


def encode(value):
    if is_dataclass(value):
        return {"__event__": type(value).__name__, "fields": asdict(value)}
    return _encode(value)


def decode(value):
    if "__event__" in value:
        cls = _TYPES[value["__event__"]]
        fields = value["fields"]
        for side in ("bids", "asks"):
            if side in fields:
                fields[side] = tuple(tuple(level) for level in fields[side])
        return cls(**fields)
    return _decode(value)


def canonical(value):
    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"), allow_nan=False)


class RetailEventJournal:
    def __init__(self, path: Path, maximum_bytes: int = 256 * 1024 * 1024):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("x", encoding="ascii")
        self.maximum_bytes, self.bytes_written = maximum_bytes, 0
        self.failed = False
        self.sequence, self.digest = 0, "0" * 64

    def append(self, kind: str, **payload):
        if self.failed:
            raise RuntimeError("journal failed; refusing further records")
        row = canonical({"sequence": self.sequence, "previous": self.digest,
                         "kind": kind, "payload": payload})
        digest = sha256(row.encode("ascii")).hexdigest()
        line = canonical({"record": row, "digest": digest}) + "\n"
        if self.bytes_written + len(line) > self.maximum_bytes:
            self.failed = True
            raise RuntimeError("retail capture capacity reached; stop rather than discard evidence")
        try:
            self.stream.write(line)
            self.stream.flush()
            if kind in {"manifest", "complete", "aborted"}:
                os.fsync(self.stream.fileno())
        except BaseException:
            self.failed = True
            if kind == "complete":
                # A failed terminal write/fsync must not leave a success record.
                try:
                    self.stream.seek(self.bytes_written)
                    self.stream.truncate()
                    self.stream.flush()
                    os.fsync(self.stream.fileno())
                except Exception:
                    pass  # unresolved managed marker still prohibits reuse
            raise
        self.bytes_written += len(line)
        self.sequence += 1
        self.digest = digest

    def close(self):
        self.stream.close()


def read_journal(path: Path):
    previous = "0" * 64
    with path.open(encoding="ascii") as stream:
        for sequence, line in enumerate(stream):
            if not line.endswith("\n"):
                raise ValueError("truncated retail capture")
            wrapper = json.loads(line)
            row = wrapper["record"]
            digest = sha256(row.encode("ascii")).hexdigest()
            if digest != wrapper["digest"]:
                raise ValueError("retail capture digest mismatch")
            record = json.loads(row, object_hook=decode)
            if record["sequence"] != sequence or record["previous"] != previous:
                raise ValueError("retail capture chain broken")
            previous = digest
            yield record
