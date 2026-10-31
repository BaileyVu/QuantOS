"""Shared helpers: read-only public HTTP, Binance symbol mapping. stdlib only."""
import json, math, re, time, urllib.request, urllib.error, urllib.parse

HL_INFO = "https://api.hyperliquid.xyz/info"
BN = "https://fapi.binance.com"
UA = {"User-Agent": "hl-copy-paper/1.0", "Content-Type": "application/json"}


ALLOWED_HOSTS = {"api.hyperliquid.xyz", "stats-data.hyperliquid.xyz", "fapi.binance.com"}
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
COIN_RE = re.compile(r"^[A-Z0-9]{1,15}$")
# explicit HL -> Binance base-asset table for k-prefixed (x1000) HL coins
K_MAP = {"kPEPE": "1000PEPE", "kSHIB": "1000SHIB", "kBONK": "1000BONK",
         "kFLOKI": "1000FLOKI", "kLUNC": "1000LUNC", "kDOGS": "1000DOGS",
         "kNEIRO": "1000NEIRO"}


def check_host(url):
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS:
        raise ValueError("host not allowed")
    return url


def valid_addr(a):
    return isinstance(a, str) and bool(ADDR_RE.match(a))


def valid_coin(c):
    return isinstance(c, str) and (c in K_MAP or bool(COIN_RE.match(c)))


def num(x):
    """Parse untrusted number; raise ValueError if not finite."""
    v = float(x)
    if not math.isfinite(v):
        raise ValueError("non-finite")
    return v


def http_json(url, payload=None, timeout=20):
    """GET (payload None) or POST JSON. Returns parsed JSON. Raises on error."""
    check_host(url)
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=UA,
                                 method="GET" if data is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def binance_perps(exinfo):
    """exchangeInfo -> {base: {'USDC': sym, 'USDT': sym}} and {sym: info}, TRADING perps only."""
    bases, info = {}, {}
    for s in exinfo.get("symbols", []):
        if s.get("status") != "TRADING" or s.get("contractType") != "PERPETUAL":
            continue
        q = s.get("quoteAsset")
        if q not in ("USDC", "USDT"):
            continue
        mn, step, minq = 5.0, 0.001, 0.0
        for f in s.get("filters", []):
            if f["filterType"] == "MIN_NOTIONAL":
                mn = float(f.get("notional", mn))
            elif f["filterType"] in ("LOT_SIZE", "MARKET_LOT_SIZE") and f["filterType"] == "LOT_SIZE":
                step, minq = float(f["stepSize"]), float(f["minQty"])
        bases.setdefault(s["baseAsset"], {})[q] = s["symbol"]
        info[s["symbol"]] = {"minNotional": mn, "step": step, "minQty": minq}
    return bases, info


def map_coin(coin, bases):
    """HL coin ticker -> Binance perp symbol (prefer USDC, else USDT) or None."""
    if not valid_coin(coin):
        return None
    base = K_MAP.get(coin, coin)
    d = bases.get(base)
    if d:
        return d.get("USDC") or d.get("USDT")
    return None
