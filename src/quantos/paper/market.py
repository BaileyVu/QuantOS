"""Fail-closed, public-only Binance USD-M market data adapter."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .types import Candle, D, Funding, Instrument, Quote, Trade

BASE = "https://fapi.binance.com"
ENDPOINTS = {
    "/fapi/v1/exchangeInfo", "/fapi/v1/time", "/fapi/v1/klines",
    "/fapi/v1/ticker/bookTicker", "/fapi/v1/premiumIndex",
    "/fapi/v1/aggTrades", "/fapi/v1/fundingRate",
}


class RateLimited(RuntimeError):
    """The caller must defer another request for at least this many seconds."""
    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = max(60.0, retry_after_seconds)
        super().__init__(f"public API rate limited; retry after {self.retry_after_seconds:g}s")


def _decimal(value, name: str) -> Decimal:
    try:
        out = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"invalid {name}") from None
    if not out.is_finite():
        raise ValueError(f"non-finite {name}")
    return out


def _int(value, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid {name}")
    try:
        out = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"invalid {name}") from None
    if str(out) != str(value):
        raise ValueError(f"invalid {name}")
    return out


class PublicMarket:
    """Public endpoints only. `transport` receives a fixed-host Request and timeout."""
    def __init__(self, *, transport: Callable | None = None,
                 clock_ms: Callable[[], int] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._transport = transport or self._urlopen
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep

    @staticmethod
    def _urlopen(request, timeout):
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), response.headers

    def _get(self, path: str, params: dict | None = None):
        if path not in ENDPOINTS:
            raise ValueError("endpoint not allowlisted")
        query = urlencode(params or {})
        url = BASE + path + ("?" + query if query else "")
        delay = 0.25
        for attempt in range(4):
            req = Request(url, headers={"Accept": "application/json", "User-Agent": "QuantOS-paper/1"}, method="GET")
            try:
                result = self._transport(req, 5)
                status, body, headers = result
                if status in (418, 429) or status >= 500:
                    if status in (418, 429):
                        retry = headers.get("Retry-After") if headers else None
                        try: wait = float(retry) if retry is not None else 60.0
                        except (TypeError, ValueError): wait = 60.0
                        raise RateLimited(wait)
                    if attempt == 3:
                        raise RuntimeError(f"public API HTTP {status}")
                    retry = (headers.get("Retry-After") if headers else None)
                    try:
                        wait = min(4.0, max(delay, float(retry))) if retry is not None else delay
                    except (TypeError, ValueError):
                        wait = delay
                    self._sleep(wait)
                    delay = min(4.0, delay * 2)
                    continue
                if status < 200 or status >= 300:
                    raise RuntimeError(f"public API HTTP {status}")
                return json.loads(body)
            except HTTPError as exc:
                if exc.code in (418, 429):
                    retry = exc.headers.get("Retry-After") if exc.headers else None
                    try: wait = float(retry) if retry is not None else 60.0
                    except (TypeError, ValueError): wait = 60.0
                    raise RateLimited(wait) from None
                if exc.code not in (418, 429) and exc.code < 500:
                    raise RuntimeError(f"public API HTTP {exc.code}") from None
                if attempt == 3:
                    raise RuntimeError(f"public API HTTP {exc.code}") from None
                retry = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = min(4.0, max(delay, float(retry))) if retry is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                self._sleep(wait)
                delay = min(4.0, delay * 2)
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if isinstance(exc, json.JSONDecodeError) or attempt == 3:
                    raise RuntimeError("public API request failed") from None
                self._sleep(delay)
                delay = min(4.0, delay * 2)
        raise RuntimeError("public API request failed")

    def exchange_info(self) -> dict[str, Instrument]:
        data = self._get("/fapi/v1/exchangeInfo")
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise ValueError("malformed exchangeInfo")
        found = {}
        for item in data["symbols"]:
            if not isinstance(item, dict):
                raise ValueError("malformed instrument")
            if (item.get("quoteAsset") != "USDC" or item.get("marginAsset") != "USDC"
                    or item.get("contractType") != "PERPETUAL"):
                continue
            sym = item.get("symbol")
            if not isinstance(sym, str) or not sym.endswith("USDC"):
                continue
            filters = item.get("filters")
            if not isinstance(filters, list):
                continue
            by = {f.get("filterType"): f for f in filters if isinstance(f, dict)}
            try:
                p, l, ml, mn, pp = (by[x] for x in ("PRICE_FILTER", "LOT_SIZE", "MARKET_LOT_SIZE", "MIN_NOTIONAL", "PERCENT_PRICE"))
                instrument = Instrument(sym, item["marginAsset"], item["status"],
                    _decimal(p["tickSize"], "tick"), _decimal(l["stepSize"], "step"),
                    _decimal(l["minQty"], "minQty"), _decimal(l["maxQty"], "maxQty"),
                    _decimal(mn.get("notional", mn.get("minNotional")), "minNotional"),
                    _decimal(p["minPrice"], "minPrice"), _decimal(p["maxPrice"], "maxPrice"),
                    _decimal(ml["stepSize"], "marketStep"), _decimal(ml["minQty"], "marketMinQty"),
                    _decimal(ml["maxQty"], "marketMaxQty"), _decimal(pp["multiplierUp"], "multiplierUp"),
                    _decimal(pp["multiplierDown"], "multiplierDown"))
                if any(v <= 0 for v in (instrument.tick, instrument.step, instrument.min_qty,
                    instrument.max_qty, instrument.min_notional, instrument.market_step,
                    instrument.market_min_qty, instrument.market_max_qty, instrument.multiplier_up,
                    instrument.multiplier_down)) or instrument.min_price < 0 or instrument.max_price <= instrument.min_price:
                    continue
                found[sym] = instrument
            except (KeyError, ValueError, TypeError):
                continue
        return {s: found[s] for s in sorted(found, key=lambda x: (x not in {"BTCUSDC", "ETHUSDC"}, x))}

    def server_time(self) -> int:
        data = self._get("/fapi/v1/time")
        return _int(data["serverTime"], "serverTime")

    def candles(self, symbol: str, interval: str, now_ms: int, limit: int = 100) -> tuple[Candle, ...]:
        if interval not in {"1m", "5m"} or not 1 <= limit <= 1500:
            raise ValueError("unsupported candle request")
        rows = self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not isinstance(rows, list): raise ValueError("malformed klines")
        width = 60000 if interval == "1m" else 300000
        out = []
        now_open = now_ms - (now_ms % width)
        for r in rows:
            if not isinstance(r, list) or len(r) < 7: raise ValueError("malformed kline")
            o, c = _int(r[0], "openTime"), _int(r[6], "closeTime")
            op, hi, lo, cl, vol = (_decimal(r[i], "kline") for i in (1, 2, 3, 4, 5))
            if o == now_open and c >= now_ms:
                continue
            if c != o + width - 1 or c >= now_ms or min(op, hi, lo, cl) <= 0 or vol < 0 or hi < max(op, cl, lo) or lo > min(op, cl, hi):
                raise ValueError("malformed closed candle")
            out.append(Candle(o, c, op, hi, lo, cl, vol))
        out.sort(key=lambda x: x.open_ms)
        if not out or len(out) != len({x.open_ms for x in out}) or any(b.open_ms-a.open_ms != width for a,b in zip(out,out[1:])):
            raise ValueError("missing, duplicate, or non-contiguous closed candles")
        if out[-1].close_ms != now_open - 1:
            raise ValueError("latest completed candle missing or stale")
        return tuple(out)

    def quote(self, symbol: str) -> Quote:
        b = self._get("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        m = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        received = self._clock_ms()
        if b.get("symbol") != symbol or m.get("symbol") != symbol:
            raise ValueError("quote symbol mismatch")
        bid, ask = _decimal(b["bidPrice"], "bid"), _decimal(b["askPrice"], "ask")
        bq, aq = _decimal(b["bidQty"], "bidQty"), _decimal(b["askQty"], "askQty")
        mark = _decimal(m["markPrice"], "mark")
        ts = _int(b["time"], "bookTime")
        mark_ts = _int(m["time"], "markTime")
        if (any(v <= 0 for v in (bid, ask, bq, aq, mark)) or bid > ask
                or not 0 <= received-ts <= 5000 or not 0 <= received-mark_ts <= 5000):
            raise ValueError("invalid or stale quote")
        return Quote(symbol, ts, received, bid, ask, bq, aq, mark, mark_ts)

    def trades(self, symbol: str, from_id: int | None = None, start_ms: int | None = None) -> tuple[Trade, ...]:
        if (from_id is None) == (start_ms is None): raise ValueError("specify exactly one trade cursor")
        params = {"symbol": symbol, "limit": 1000}
        params["fromId" if from_id is not None else "startTime"] = from_id if from_id is not None else start_ms
        rows = self._get("/fapi/v1/aggTrades", params)
        if not isinstance(rows, list): raise ValueError("malformed aggTrades")
        out = []
        for index, r in enumerate(rows):
            if not isinstance(r, dict) or not isinstance(r.get("m"), bool): raise ValueError("malformed aggTrade")
            if r.get("s", symbol) != symbol: raise ValueError("trade symbol mismatch")
            # Binance RPI aggregate quantity is excluded; use it only when supplied.
            qty = _decimal(r.get("nq", r["q"]), "quantity")
            t = Trade(symbol, _int(r["a"], "tradeId"), _int(r["T"], "tradeTime"), _decimal(r["p"], "price"), qty, r["m"])
            if t.trade_id < 0 or t.time_ms < 0 or t.price <= 0 or t.quantity <= 0: raise ValueError("invalid aggTrade")
            out.append(t)
        if any(b.trade_id <= a.trade_id or b.time_ms < a.time_ms for a,b in zip(out,out[1:])): raise ValueError("unordered aggTrades")
        if from_id is not None and out and out[0].trade_id != from_id: raise ValueError("aggregate trade cursor gap")
        if any(b.trade_id != a.trade_id + 1 for a,b in zip(out,out[1:])): raise ValueError("aggregate trade ID gap")
        return tuple(out)

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> tuple[Funding, ...]:
        if start_ms < 0 or end_ms < start_ms: raise ValueError("invalid funding range")
        out, cursor = [], start_ms
        while cursor <= end_ms:
            rows = self._get("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000})
            if not isinstance(rows, list): raise ValueError("malformed funding history")
            if not rows: break
            page = []
            for r in rows:
                if not isinstance(r, dict) or r.get("symbol") != symbol: raise ValueError("funding symbol mismatch")
                f = Funding(symbol, _int(r["fundingTime"], "fundingTime"), _decimal(r["fundingRate"], "fundingRate"), _decimal(r["markPrice"], "markPrice"))
                if not start_ms <= f.time_ms <= end_ms or f.mark <= 0: raise ValueError("invalid funding record")
                page.append(f)
            page.sort(key=lambda x:x.time_ms)
            if any(b.time_ms <= a.time_ms for a,b in zip(page,page[1:])): raise ValueError("duplicate funding time")
            if out and page and page[0].time_ms <= out[-1].time_ms: raise ValueError("funding cursor stalled")
            out.extend(page)
            if len(rows) < 1000: break
            nxt = page[-1].time_ms + 1
            if nxt <= cursor: raise ValueError("funding pagination stalled")
            cursor = nxt
        return tuple(sorted(out, key=lambda x:x.time_ms))
