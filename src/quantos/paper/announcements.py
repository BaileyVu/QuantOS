"""Public Binance CMS delist-announcement parser; no trading or order behavior."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import time
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .market import RateLimited
from .types import DelistEvent, Instrument

CMS_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    "?type=1&catalogId=161&pageNo=1&pageSize=20"
)
SOURCE_URL = "https://www.binance.com/en/support/announcement/"
_TITLE = re.compile(r"^Binance Will Delist\s+(.+?)(?:\s+on\b.*)?$")
_CODE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TICKER = re.compile(r"^[A-Z0-9]{1,24}$")


@dataclass(frozen=True)
class AnnouncementDiagnostic:
    status: str
    release_ms: int
    observed_ms: int
    article_id: str
    title: str
    ticker: str


@dataclass(frozen=True)
class AnnouncementPoll:
    events: tuple[DelistEvent, ...]
    diagnostics: tuple[AnnouncementDiagnostic, ...]


class BinanceAnnouncements:
    """Unauthenticated GET-only reader for the fixed Binance CMS catalog 161."""

    def __init__(self, *, transport: Callable | None = None,
                 clock_ms: Callable[[], int] | None = None):
        self._transport = transport or self._urlopen
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    @staticmethod
    def _urlopen(request: Request, timeout: float):
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers

    def fetch(self) -> object:
        request = Request(CMS_URL, headers={"Accept": "application/json",
                                           "User-Agent": "QuantOS-paper/1"}, method="GET")
        try:
            status, body, _headers = self._transport(request, 10)
        except HTTPError as exc:
            if exc.code in (418, 429):
                raise RateLimited(_retry_after(exc.headers)) from None
            raise RuntimeError(f"public announcement HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            raise RuntimeError("public announcement request failed") from None
        if status in (418, 429):
            raise RateLimited(_retry_after(_headers))
        if status < 200 or status >= 300:
            raise RuntimeError(f"public announcement HTTP {status}")
        try:
            return json.loads(body)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            raise ValueError("malformed announcement JSON") from None

    def parse(self, payload: object, eligible_instruments: Mapping[str, Instrument] | set[str],
              now_ms: int | None = None) -> AnnouncementPoll:
        now = _timestamp(self._clock_ms() if now_ms is None else now_ms, "now_ms")
        eligible = set(eligible_instruments)
        metadata = eligible_instruments if isinstance(eligible_instruments, Mapping) else None
        if not isinstance(payload, dict):
            raise ValueError("malformed announcement payload")
        data = payload.get("data")
        catalogs = data.get("catalogs") if isinstance(data, dict) else None
        if not isinstance(catalogs, list):
            raise ValueError("malformed announcement catalogs")

        events: list[DelistEvent] = []
        diagnostics: list[AnnouncementDiagnostic] = []
        for catalog in catalogs:
            if not isinstance(catalog, dict) or not isinstance(catalog.get("articles"), list):
                raise ValueError("malformed announcement catalog")
            for article in catalog["articles"]:
                if not isinstance(article, dict):
                    raise ValueError("malformed announcement article")
                title = article.get("title")
                if not isinstance(title, str):
                    raise ValueError("malformed announcement title")
                match = _TITLE.fullmatch(title)
                if match is None:
                    continue
                release_ms = _timestamp(article.get("releaseDate"), "releaseDate")
                code = article.get("code")
                if not isinstance(code, str) or _CODE.fullmatch(code) is None:
                    raise ValueError("invalid announcement code")
                if release_ms > now:
                    diagnostics.append(AnnouncementDiagnostic(
                        "SKIPPED_FUTURE_RELEASE", release_ms, now, code, title, ""))
                    continue
                if now - release_ms > 300_000:
                    diagnostics.append(AnnouncementDiagnostic(
                        "SKIPPED_STALE_RELEASE", release_ms, now, code, title, ""))
                    continue
                tickers = _parse_tickers(match.group(1))
                if not tickers:
                    raise ValueError("delist title contains no valid ticker")
                source_url = SOURCE_URL + code
                for ticker in tickers:
                    symbol = next((candidate for candidate in
                                   (ticker + "USDC", "1000" + ticker + "USDC")
                                   if candidate in eligible and
                                   _eligible_instrument(metadata, candidate, ticker)), None)
                    if symbol is None:
                        diagnostics.append(AnnouncementDiagnostic(
                            "SKIPPED_NO_USDC_PERP", release_ms, now, code, title, ticker))
                        continue
                    events.append(DelistEvent(symbol, release_ms, now, code, title, source_url))
        return AnnouncementPoll(tuple(events), tuple(diagnostics))

    def poll(self, eligible_instruments: Mapping[str, Instrument] | set[str],
             now_ms: int | None = None) -> AnnouncementPoll:
        return self.parse(self.fetch(), eligible_instruments, now_ms)


def _timestamp(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {name}")
    return value


def _retry_after(headers: object) -> float:
    value = headers.get("Retry-After") if headers else None
    try:
        seconds = float(value) if value is not None else 60.0
        return seconds if math.isfinite(seconds) else 60.0
    except (TypeError, ValueError, OverflowError):
        return 60.0


def _eligible_instrument(metadata: Mapping[str, Instrument] | None,
                         symbol: str, ticker: str) -> bool:
    if metadata is None:
        return True
    instrument = metadata.get(symbol)
    return (
        isinstance(instrument, Instrument)
        and instrument.symbol == symbol
        and instrument.status == "TRADING"
        and instrument.margin_asset == "USDC"
        and instrument.base_asset in {ticker, "1000" + ticker}
    )


def _parse_tickers(body: str) -> tuple[str, ...]:
    # The CMS title convention separates symbols with commas and the word "and".
    parts = re.split(r"\s*,\s*|\s+and\s+", body)
    tickers = []
    for part in parts:
        token = part.strip()
        if _TICKER.fullmatch(token) is None:
            continue
        if token not in tickers:
            tickers.append(token)
    return tuple(tickers)
