"""Signed Binance USD-M futures client. transport(request, timeout) -> (status, body, headers)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASES = {"testnet": "https://testnet.binancefuture.com", "mainnet": "https://fapi.binance.com"}
CONFIRM_LINE = "I CONFIRM MAINNET QUANTOS"
ALLOWED = {
    ("POST", "/fapi/v1/order"), ("GET", "/fapi/v1/order"), ("DELETE", "/fapi/v1/order"),
    ("DELETE", "/fapi/v1/allOpenOrders"), ("GET", "/fapi/v1/openOrders"),
    ("GET", "/fapi/v2/positionRisk"), ("GET", "/fapi/v2/account"),
    ("POST", "/fapi/v1/leverage"), ("POST", "/fapi/v1/marginType"),
    ("GET", "/fapi/v1/exchangeInfo"), ("GET", "/fapi/v1/time"),
    # Conditional (STOP/TP) orders moved to the Algo Order API; verify on testnet.
    ("POST", "/fapi/v1/algoOrder"), ("GET", "/fapi/v1/algoOrder"), ("DELETE", "/fapi/v1/algoOrder"),
    ("GET", "/fapi/v1/openAlgoOrders"), ("DELETE", "/fapi/v1/algoOpenOrders"),
}
UNSIGNED = {"/fapi/v1/exchangeInfo", "/fapi/v1/time"}


class BinanceError(RuntimeError):
    def __init__(self, code: int, msg: str, status: int = 0):
        self.code, self.msg, self.status = code, msg, status
        super().__init__(f"binance error {code}: {msg} (http {status})")
class UnknownOrder(BinanceError): pass
class TimestampError(BinanceError): pass
class MarginInsufficient(BinanceError): pass
class MinNotional(BinanceError): pass
class RateLimited(BinanceError):
    def __init__(self, code, msg, status=0, retry_after: float = 60.0):
        super().__init__(code, msg, status); self.retry_after = max(60.0, retry_after)
class AmbiguousOrderState(RuntimeError):
    """Outcome of a mutating request is unknown; caller must query by clientOrderId."""
class ModeError(RuntimeError): pass

CODES = {-2011: UnknownOrder, -2013: UnknownOrder, -1021: TimestampError,
         -2019: MarginInsufficient, -4164: MinNotional}


def sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _fmt(v) -> str:
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, Decimal): return format(v, "f")
    return str(v)


def check_mainnet(allow_mainnet: bool, confirm_file) -> None:
    if not allow_mainnet or not confirm_file:
        raise ModeError("mainnet requires allow_mainnet=True and a confirm file")
    try:
        text = Path(confirm_file).read_text()
    except OSError:
        raise ModeError("mainnet confirm file missing") from None
    if text not in (CONFIRM_LINE, CONFIRM_LINE + "\n"):
        raise ModeError("mainnet confirm file content mismatch")


class SignedClient:
    def __init__(self, mode, keys, transport: Callable | None = None, clock_ms: Callable[[], int] | None = None,
                 recv_window: int = 5000, *, allow_mainnet: bool = False, confirm_file=None, log_path=None,
                 conditional_api: str = "algo"):
        if conditional_api not in ("algo", "legacy"): raise ModeError("conditional_api must be algo or legacy")
        self.conditional_api = conditional_api
        if mode not in ("dry-run", "testnet", "mainnet"):
            raise ModeError("mode must be dry-run, testnet or mainnet")
        if mode == "mainnet": check_mainnet(allow_mainnet, confirm_file)
        if mode != "dry-run" and keys is None: raise ModeError("keys required")
        self.mode, self._keys, self.recv_window = mode, keys, int(recv_window)
        self._transport = transport or self._urlopen
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._offset: int | None = 0 if mode == "dry-run" else None
        self._log_path = Path(log_path) if log_path else None
        self._dry_orders: dict[tuple, dict] = {}
        self.dry_log: list[dict] = []

    @staticmethod
    def _urlopen(request, timeout):
        try:
            with urlopen(request, timeout=timeout) as r:
                return r.status, r.read(), r.headers
        except HTTPError as e:
            return e.code, e.read(), e.headers

    # -- core -------------------------------------------------------------
    def _ts(self) -> int:
        if self._offset is None:
            body = self._send("GET", "/fapi/v1/time", {}, signed=False)
            self._offset = int(body["serverTime"]) - self._clock_ms()
        return self._clock_ms() + self._offset

    def request(self, method: str, path: str, params: dict | None = None):
        if (method, path) not in ALLOWED: raise ModeError("endpoint not allowlisted")
        p = {k: _fmt(v) for k, v in (params or {}).items() if v is not None}
        if self.mode == "dry-run": return self._dry(method, path, p)
        return self._send(method, path, p, signed=path not in UNSIGNED)

    def _send(self, method, path, p, signed):
        mutating = method != "GET"
        if signed:
            q = urlencode({**p, "recvWindow": self.recv_window, "timestamp": self._ts()})
            q += "&signature=" + sign(self._keys.api_secret, q)
        else:
            q = urlencode(p)
        req = Request(BASES[self.mode] + path + ("?" + q if q else ""), method=method,
                      headers={"Accept": "application/json", "User-Agent": "QuantOS-live/1"})
        if signed: req.add_header("X-MBX-APIKEY", self._keys.api_key)
        try:
            status, body, headers = self._transport(req, 5)
        except Exception as e:
            if mutating: raise AmbiguousOrderState(f"{method} {path}: transport failure {type(e).__name__}") from None
            raise ConnectionError(f"{method} {path}: transport failure {type(e).__name__}") from None
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            data = None
        if status in (418, 429):
            try: ra = float((headers or {}).get("Retry-After", 60))
            except (TypeError, ValueError): ra = 60.0
            raise RateLimited(-1003, "rate limited", status, ra)
        code = data.get("code") if isinstance(data, dict) else None
        if status >= 500 and mutating:
            raise AmbiguousOrderState(f"{method} {path}: http {status}")
        if isinstance(code, int) and code < 0 or status != 200:
            code = code if isinstance(code, int) else 0
            if code == -1007 and mutating: raise AmbiguousOrderState("execution status unknown (-1007)")
            if code == -1021: self._offset = None
            msg = str(data.get("msg", ""))[:200] if isinstance(data, dict) else ""
            raise CODES.get(code, BinanceError)(code, msg, status)
        if data is None:
            if mutating: raise AmbiguousOrderState(f"{method} {path}: unparseable response")
            raise ValueError("unparseable response")
        return data

    # -- dry run ----------------------------------------------------------
    def _dry(self, method, path, p):
        rec = {"ts": self._clock_ms(), "method": method, "path": path,
               "params": {**p, "recvWindow": self.recv_window},
               "signature": "REDACTED", "api_key": "REDACTED"}
        if method != "GET" or path == "/fapi/v1/order": self._log(rec)
        key = (p.get("symbol"), p.get("newClientOrderId") or p.get("origClientOrderId") or p.get("clientAlgoId"))
        if path == "/fapi/v1/algoOrder":
            if method == "POST":
                ack = {"dryRun": True, "symbol": p.get("symbol"), "clientAlgoId": key[1], "side": p.get("side"),
                       "orderType": p.get("type"), "algoStatus": "NEW", "triggerPrice": p.get("triggerPrice"),
                       "reduceOnly": p.get("reduceOnly") == "true", "closePosition": p.get("closePosition") == "true"}
                self._dry_orders[key] = ack
                return dict(ack)
            if key not in self._dry_orders: raise UnknownOrder(-2013, "dry-run: algo order does not exist", 400)
            return dict(self._dry_orders.pop(key) if method == "DELETE" else self._dry_orders[key])
        if path == "/fapi/v1/openAlgoOrders":
            return [dict(o) for o in self._dry_orders.values() if "clientAlgoId" in o]
        if (method, path) == ("POST", "/fapi/v1/order"):
            mkt = p.get("type") == "MARKET"
            ack = {"dryRun": True, "symbol": p.get("symbol"), "clientOrderId": key[1], "side": p.get("side"),
                   "type": p.get("type"), "status": "FILLED" if mkt else "NEW", "origQty": p.get("quantity", "0"),
                   "executedQty": p.get("quantity", "0") if mkt else "0", "avgPrice": "0",
                   "reduceOnly": p.get("reduceOnly") == "true", "closePosition": p.get("closePosition") == "true"}
            self._dry_orders[key] = ack
            return dict(ack)
        if (method, path) == ("GET", "/fapi/v1/order"):
            if key not in self._dry_orders: raise UnknownOrder(-2013, "dry-run: order does not exist", 400)
            return dict(self._dry_orders[key])
        if (method, path) == ("DELETE", "/fapi/v1/order"):
            o = self._dry_orders.pop(key, None)
            if o is None: raise UnknownOrder(-2011, "dry-run: unknown order", 400)
            return {**o, "status": "CANCELED"}
        if path == "/fapi/v1/time": return {"serverTime": self._clock_ms()}
        if path == "/fapi/v1/exchangeInfo": return {"symbols": []}
        if path in ("/fapi/v1/openOrders", "/fapi/v2/positionRisk"): return []
        if path == "/fapi/v2/account": return {"dryRun": True, "assets": [], "positions": []}
        return {"dryRun": True, "code": 200, "msg": "dry-run ack"}

    def _log(self, rec):
        self.dry_log.append(rec)
        if self._log_path:
            fd = os.open(self._log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as f: f.write(json.dumps(rec, sort_keys=True) + "\n")

    # -- endpoints --------------------------------------------------------
    def new_order(self, params): return self.request("POST", "/fapi/v1/order", params)
    def new_algo_order(self, params): return self.request("POST", "/fapi/v1/algoOrder", params)
    def get_algo_order(self, client_algo_id): return self.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": client_algo_id})
    def cancel_algo_order(self, client_algo_id): return self.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": client_algo_id})
    def open_algo_orders(self, symbol=None): return self.request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})
    def cancel_all_algo(self, symbol): return self.request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})
    def get_order(self, symbol, client_id): return self.request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id})
    def cancel_order(self, symbol, client_id): return self.request("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id})
    def cancel_all(self, symbol): return self.request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
    def open_orders(self, symbol=None): return self.request("GET", "/fapi/v1/openOrders", {"symbol": symbol})
    def position_risk(self, symbol=None): return self.request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})
    def account(self): return self.request("GET", "/fapi/v2/account")
    def exchange_info(self): return self.request("GET", "/fapi/v1/exchangeInfo")
    def server_time(self): return self.request("GET", "/fapi/v1/time")
    def set_leverage(self, symbol, leverage): return self.request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)})
    def set_margin_type(self, symbol, margin_type="ISOLATED"):
        try: return self.request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type})
        except BinanceError as e:
            if e.code == -4046: return {"code": 200, "msg": "no change"}
            raise
