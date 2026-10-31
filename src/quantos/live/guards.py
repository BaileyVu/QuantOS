"""Daily loss guard; trips the kill switch."""
from __future__ import annotations

from ..paper.types import D

DAY_MS = 86_400_000


class DailyLossGuard:
    def __init__(self, start_equity, max_daily_loss_fraction=D("0.10"), kill=None):
        self.start_equity, self.max_loss, self.kill = D(str(start_equity)), D(str(max_daily_loss_fraction)), kill
        if self.start_equity <= 0 or not 0 < self.max_loss <= 1: raise ValueError("bad guard params")
        self.day: int | None = None
        self.tripped = False

    def update(self, equity, now_ms: int) -> bool:
        eq, day = D(str(equity)), now_ms // DAY_MS
        if self.day is None: self.day = day
        elif day != self.day:
            self.day, self.start_equity, self.tripped = day, eq, False
        if not self.tripped and (self.start_equity - eq) / self.start_equity >= self.max_loss:
            self.tripped = True
            if self.kill: self.kill.engage(f"daily loss >= {self.max_loss} (equity {eq} from {self.start_equity})")
        return self.tripped
