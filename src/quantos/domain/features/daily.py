"""Compact canonical-minute -> completed UTC-day state, independent of Alpha."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from quantos.domain.common import require_utc, require_v1_symbol
from quantos.domain.features.contracts import FeatureVector
from quantos.domain.market_data import Candle
from quantos.domain.runtime_contracts import arithmetic, identity, primitive

TSMOM_FEATURE_VERSION = "tsmom-daily-v1"
HORIZONS = (7, 14, 30)
ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class DailyClose:
    day: datetime
    close: Decimal


@dataclass(frozen=True, slots=True)
class DailyFeatureState:
    symbol: str
    last: Candle | None = None
    minutes_in_day: int = 0
    daily: tuple[DailyClose, ...] = ()
    minute_count: int = 0
    chain: str | None = None
    version: str = TSMOM_FEATURE_VERSION

    def __post_init__(self):
        require_v1_symbol(self.symbol)
        if self.version != TSMOM_FEATURE_VERSION:
            raise ValueError("incompatible daily feature schema")
        if type(self.daily) is not tuple or len(self.daily) > 31:
            raise ValueError("daily state must contain at most 31 closes")
        if type(self.minutes_in_day) is not int or not 0 <= self.minutes_in_day <= 1440:
            raise ValueError("invalid day coverage")
        if type(self.minute_count) is not int or self.minute_count < 0:
            raise ValueError("invalid minute count")
        if self.last is None:
            if self.daily or self.minutes_in_day or self.minute_count or self.chain is not None:
                raise ValueError("state without minute evidence")
            return
        Candle.__post_init__(self.last)
        end = self.last.open_time+timedelta(minutes=1)
        if (self.last.open_time.second or self.last.open_time.microsecond
                or not end-timedelta(milliseconds=1) <= self.last.close_time <= end
                or self.last.close <= 0):
            raise ValueError("invalid checkpoint minute boundary")
        if self.last.symbol != self.symbol or self.minute_count < 1:
            raise ValueError("invalid last minute")
        if type(self.chain) is not str or len(self.chain) != 64 or any(c not in '0123456789abcdef' for c in self.chain):
            raise ValueError("invalid minute evidence hash")
        day = self.last.open_time.replace(hour=0, minute=0, second=0, microsecond=0)
        expected = self.last.open_time.hour * 60 + self.last.open_time.minute + 1
        if self.minutes_in_day not in (0, expected) or self.minute_count < self.minutes_in_day:
            raise ValueError("inconsistent daily coverage")
        for index, item in enumerate(self.daily):
            require_utc(item.day, "daily close day")
            if item.day != item.day.replace(hour=0, minute=0, second=0, microsecond=0):
                raise ValueError("daily close must identify UTC midnight")
            if type(item.close) is not Decimal or not item.close.is_finite() or item.close <= 0:
                raise ValueError("invalid daily close")
            if index and item.day - self.daily[index-1].day != timedelta(days=1):
                raise ValueError("nonconsecutive daily closes")
            if item.day + timedelta(days=1) > self.last.open_time + timedelta(minutes=1):
                raise ValueError("future daily close")
        if self.daily:
            expected_day = day if self.minutes_in_day == 1440 else day-timedelta(days=1)
            if self.daily[-1].day != expected_day:
                raise ValueError("daily state does not meet last minute")
            if self.minutes_in_day == 1440 and self.daily[-1].close != self.last.close:
                raise ValueError("daily/minute close mismatch")

    def advance(self, candle: Candle, *, decision_time: datetime):
        require_utc(decision_time, "decision_time")
        Candle.__post_init__(candle)
        end = candle.open_time + timedelta(minutes=1)
        if (candle.symbol != self.symbol or candle.open_time.second or candle.open_time.microsecond
                or not end-timedelta(milliseconds=1) <= candle.close_time <= end
                or candle.close_time > decision_time or candle.close <= 0):
            raise ValueError("invalid or incomplete canonical daily-feature minute")
        if self.last is not None:
            if candle == self.last:
                return self  # exact last-minute replay is idempotent
            if candle.open_time-self.last.open_time != timedelta(minutes=1):
                raise ValueError("missing, duplicate, conflicting or backward daily-feature minute")
        midnight = candle.open_time.hour == candle.open_time.minute == 0
        count = 1 if midnight else (self.minutes_in_day+1 if self.minutes_in_day else 0)
        daily = self.daily
        if count == 1440:
            daily = (daily+(DailyClose(candle.open_time.replace(hour=0, minute=0), candle.close),))[-31:]
        return replace(self, last=candle, minutes_in_day=count, daily=daily,
                       minute_count=self.minute_count+1, chain=identity((self.chain, candle)))

    def feature(self):
        if self.last is None:
            return None
        values = {"decision_day_close": Decimal(int(self.minutes_in_day == 1440))}
        with localcontext(arithmetic()):
            for horizon in HORIZONS:
                ready = len(self.daily) > horizon
                values[f"ready_{horizon}d"] = Decimal(int(ready))
                values[f"momentum_{horizon}d"] = self.daily[-1].close/self.daily[-horizon-1].close-1 if ready else ZERO
        return FeatureVector(self.last.open_time+timedelta(minutes=1), self.symbol, self.version, values)

    def evidence(self):
        return primitive(self)

    @classmethod
    def restore(cls, value):
        if set(value) != {"symbol", "last", "minutes_in_day", "daily", "minute_count", "chain", "version"}:
            raise ValueError("invalid daily checkpoint fields")
        raw = dict(value)
        if raw["last"] is not None:
            c = dict(raw["last"])
            for name in ("open_time", "close_time"):
                c[name] = datetime.fromisoformat(c[name])
            for name in ("open", "high", "low", "close", "volume", "quote_volume"):
                c[name] = Decimal(c[name])
            raw["last"] = Candle(**c)
        raw["daily"] = tuple(DailyClose(datetime.fromisoformat(d["day"]), Decimal(d["close"])) for d in raw["daily"])
        return cls(**raw)
