"""Sentiment observations fed by sentibot's signal log.

The reader only returns verdicts; runtime applies the profile's optional gate.
The newest line of sentibot's `paper_perp_signals.jsonl`
holds per-symbol scores (`signals[SYM].scores.news`, `sentiment_raw.news[SYM]`,
keyed by USDT symbols). Stale or unreadable input is reported as unavailable.
"""
from __future__ import annotations

from datetime import datetime
from decimal import InvalidOperation
import json
from pathlib import Path

from .types import D

MAX_AGE_MS = 30 * 60_000
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
    def __init__(self, path: Path | None, input_observer=None):
        self.path = Path(path).expanduser() if path else None
        self.input_observer, self.observation_error = input_observer, None

    def read(self, now_ms: int) -> dict:
        if self.path is None:
            return {"available": False, "reason": "NOT_CONFIGURED"}
        try:
            text = _last_line(self.path)
            if self.input_observer is not None:
                try: self.input_observer("sentiment", (text or "").encode(), now_ms)
                except Exception as exc: self.observation_error = type(exc).__name__
            row = json.loads(text or "null")
            if not isinstance(row, dict): raise ValueError("invalid sentiment record")
            parsed = datetime.fromisoformat(row["time"])
            if parsed.tzinfo is None: raise ValueError("sentiment time needs an explicit timezone")
            at = int(parsed.timestamp() * 1000)
        except PermissionError:
            return {"available": False, "reason": "PERMISSION_DENIED"}
        except (OSError, ValueError, KeyError, TypeError):
            return {"available": False, "reason": "UNREADABLE"}
        if not 0 <= now_ms - at <= MAX_AGE_MS:
            return {"available": False, "reason": "STALE", "age_s": (now_ms - at) // 1000}
        raw, signals = row.get("sentiment_raw") or {}, row.get("signals") or {}
        if not isinstance(raw, dict) or not isinstance(signals, dict) or not isinstance(raw.get("news", {}), dict):
            return {"available": False, "reason": "UNREADABLE"}
        return {"available": True, "time_ms": at, "news": raw.get("news") or {},
                "composite": {k: v.get("composite") for k, v in signals.items()
                              if isinstance(v, dict)}}

    def judge(self, signal, now_ms: int) -> dict:
        """Verdict for one signal; runtime owns whether the profile blocks it."""
        data = self.read(now_ms)
        if not data["available"]:
            return {"signal_id": signal.signal_id, "verdict": "UNAVAILABLE", "reason": data["reason"]}
        twin = signal.symbol[:-4] + "USDT" if signal.symbol.endswith("USDC") else signal.symbol
        raw = data["news"].get(twin, data["news"].get("_market"))
        if raw is None:
            return {"signal_id": signal.signal_id, "verdict": "NO_SCORE", "symbol": twin}
        try:
            score = D(str(raw))
            if not score.is_finite(): raise ValueError("nonfinite sentiment")
        except (ValueError, InvalidOperation):
            return {"signal_id": signal.signal_id, "verdict": "UNAVAILABLE", "reason": "INVALID_SCORE"}
        block = score < LONG_BLOCK_BELOW if signal.side == "LONG" else score > SHORT_BLOCK_ABOVE
        return {"signal_id": signal.signal_id, "verdict": "WOULD_BLOCK" if block else "WOULD_ALLOW",
                "symbol": twin, "news": str(score), "composite": data["composite"].get(twin),
                "source_ms": data["time_ms"]}
