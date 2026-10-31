"""Market data (public Binance klines, no keys) and the daily Fear & Greed index. Cached to CSV."""
import json, os, time, urllib.request
import pandas as pd

HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UA = {"User-Agent": "Mozilla/5.0 (sentibot paper research)"}


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _binance(path):
    last = None
    for host in HOSTS:
        try:
            return get_json(host + path)
        except Exception as e:  # try the next host
            last = e
    raise last


def klines(symbol, interval="1h", start="2021-01-01", refresh_after=1800):
    """Closed candles as a DataFrame indexed by open time (UTC): open high low close volume."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{symbol}_{interval}.csv")
    df = None
    if os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if time.time() - os.path.getmtime(path) < refresh_after:
            return df
        start_ms = int(df.index[-1].timestamp() * 1000) + 1
    else:
        start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    rows, now_ms = [], int(time.time() * 1000)
    while True:
        batch = _binance(f"/api/v3/klines?symbol={symbol}&interval={interval}&startTime={start_ms}&limit=1000")
        if not batch:
            break
        rows += [k for k in batch if k[6] < now_ms]  # closed candles only
        if len(batch) < 1000:
            break
        start_ms = batch[-1][0] + 1
    if rows:
        new = pd.DataFrame([[float(x) for x in k[1:6]] for k in rows], columns=["open", "high", "low", "close", "volume"],
                           index=pd.to_datetime([k[0] for k in rows], unit="ms", utc=True))
        df = new if df is None else pd.concat([df, new])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_csv(path)
    elif df is not None:
        os.utime(path)
    return df


def fear_greed(refresh_after=3 * 3600):
    """Daily Crypto Fear & Greed index (alternative.me), Series 0..100 indexed by UTC date. None if unreachable."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "fear_greed.csv")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < refresh_after:
        return pd.read_csv(path, index_col=0, parse_dates=True)["value"]
    try:
        d = get_json("https://api.alternative.me/fng/?limit=0&format=json")["data"]
    except Exception:
        return pd.read_csv(path, index_col=0, parse_dates=True)["value"] if os.path.exists(path) else None
    s = pd.Series({pd.to_datetime(int(x["timestamp"]), unit="s", utc=True): float(x["value"]) for x in d}).sort_index()
    s.name = "value"
    s.to_csv(path)
    return s
