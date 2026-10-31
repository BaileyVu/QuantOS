"""Shadow-only sentiment gate fed by sentibot's signal log.

It never blocks: it records what a gate *would* have done so its value can be
measured later. The newest line of sentibot's `paper_perp_signals.jsonl`
holds per-symbol scores (`signals[SYM].scores.news`, `sentiment_raw.news[SYM]`,
keyed by USDT symbols). Stale or unreadable input is reported as unavailable.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from .types import D

MAX_AGE_MS = 3 * 3600_000
LONG_BLOCK_BELOW = D("-0.05")   # raw news sentiment; a LONG would be blocked below this
SHORT_BLOCK_ABOVE = D("0.05")


def _last_line(path: Path) -> str | None:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 65536))
        lines = [x for x in f.read().splitlines() if x.strip()]
    return lines[-1].decode() if lines else None


class SentimentShadow:
    def __init__(self, path: Path | None):
        self.path = Path(path).expanduser() if path else None

    def read(self, now_ms: int) -> dict:
        if self.path is None:
            return {"available": False, "reason": "NOT_CONFIGURED"}
        try:
            row = json.loads(_last_line(self.path) or "null")
            at = int(datetime.fromisoformat(row["time"]).timestamp() * 1000)
        except PermissionError:
            return {"available": False, "reason": "PERMISSION_DENIED"}
        except (OSError, ValueError, KeyError, TypeError):
            return {"available": False, "reason": "UNREADABLE"}
        if not 0 <= now_ms - at <= MAX_AGE_MS:
            return {"available": False, "reason": "STALE", "age_s": (now_ms - at) // 1000}
        return {"available": True, "time_ms": at, "news": (row.get("sentiment_raw") or {}).get("news") or {},
                "composite": {k: v.get("composite") for k, v in (row.get("signals") or {}).items()
                              if isinstance(v, dict)}}

    def judge(self, signal, now_ms: int) -> dict:
        """Would-block verdict for one signal; never used to block."""
        data = self.read(now_ms)
        if not data["available"]:
            return {"signal_id": signal.signal_id, "verdict": "UNAVAILABLE", "reason": data["reason"]}
        twin = signal.symbol[:-4] + "USDT" if signal.symbol.endswith("USDC") else signal.symbol
        raw = data["news"].get(twin, data["news"].get("_market"))
        if raw is None:
            return {"signal_id": signal.signal_id, "verdict": "NO_SCORE", "symbol": twin}
        score = D(str(raw))
        block = score < LONG_BLOCK_BELOW if signal.side == "LONG" else score > SHORT_BLOCK_ABOVE
        return {"signal_id": signal.signal_id, "verdict": "WOULD_BLOCK" if block else "WOULD_ALLOW",
                "symbol": twin, "news": str(score), "composite": data["composite"].get(twin),
                "source_ms": data["time_ms"]}
