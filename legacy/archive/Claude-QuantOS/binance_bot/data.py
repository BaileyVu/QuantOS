"""Public Binance spot daily klines, cached to CSV. No keys needed."""
import csv, json, os, time, urllib.request
from datetime import datetime, timezone

HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _get(path):
    last = None
    for host in HOSTS:
        try:
            with urllib.request.urlopen(host + path, timeout=20) as r:
                return json.loads(r.read())
        except Exception as e:  # try the next host
            last = e
    raise last


def fetch_daily(symbol, start="2019-01-01", refresh=False):
    """Return list of (date 'YYYY-MM-DD', open, high, low, close, quote_volume), closed candles only."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{symbol}_1d.csv")
    if os.path.exists(path) and not refresh and time.time() - os.path.getmtime(path) < 6 * 3600:
        return load_csv(path)
    start_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    rows, now_ms = [], int(time.time() * 1000)
    while True:
        batch = _get(f"/api/v3/klines?symbol={symbol}&interval=1d&startTime={start_ms}&limit=1000")
        if not batch:
            break
        for k in batch:
            if k[6] >= now_ms:  # candle not closed yet
                continue
            d = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            rows.append((d, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])))
        if len(batch) < 1000:
            break
        start_ms = batch[-1][0] + 1
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "quote_volume"])
        w.writerows(rows)
    return rows


def load_csv(path):
    with open(path) as f:
        r = csv.reader(f)
        next(r)
        return [(x[0], *map(float, x[1:])) for x in r]


def last_price(symbol):
    return float(_get(f"/api/v3/ticker/price?symbol={symbol}")["price"])
