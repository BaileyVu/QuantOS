"""Fetch public Binance USD-M data for the 2026-10-29 crash replay. Public GET only, stdlib only.

Everything is cached under ../data; re-running only fetches what is missing.
"""
import io
import json
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BASE = "https://fapi.binance.com"


def ms(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


END = ms("2026-10-30 06:00")
ALT = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
       "DOTUSDT", "LTCUSDT", "BCHUSDT", "TRXUSDT", "ATOMUSDT", "NEARUSDT", "UNIUSDT", "FILUSDT", "ETCUSDT",
       "AAVEUSDT", "XLMUSDT", "ALGOUSDT", "SANDUSDT", "MANAUSDT", "AXSUSDT", "CRVUSDT", "RUNEUSDT",
       "THETAUSDT", "VETUSDT", "GALAUSDT")
MAJORS = ALT[:9]
WIDTH = {"1m": 60000, "5m": 300000, "1h": 3600000, "4h": 14400000, "1d": 86400000}


def get(url, tries=6):
    delay = 1.0
    for attempt in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "crash-replay/1"}), timeout=20) as r:
                return r.read()
        except HTTPError as e:
            if e.code in (418, 429):
                time.sleep(float(e.headers.get("Retry-After", 60)))
            elif e.code == 404 or (e.code == 400 and attempt > 0):
                raise
            else:
                time.sleep(delay)
        except Exception:
            time.sleep(delay)
        delay = min(16, delay * 2)
    raise RuntimeError(f"failed {url}")


def klines(path, symbol, interval, start, end=END):
    out = DATA / path / interval / f"{symbol}.json"
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, cursor = [], start
    while cursor < end:
        body = get(f"{BASE}/fapi/v1/{path}?symbol={symbol}&interval={interval}&startTime={cursor}&endTime={end - 1}&limit=1500")
        page = json.loads(body)
        if not page:
            break
        rows.extend(page)
        cursor = page[-1][0] + WIDTH[interval]
        time.sleep(0.15)
    out.write_text(json.dumps(rows))


def funding(symbol, start):
    out = DATA / "funding" / f"{symbol}.json"
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(get(f"{BASE}/fapi/v1/fundingRate?symbol={symbol}&startTime={start}&endTime={END}&limit=1000").decode())


def oi_api(symbol):
    out = DATA / "oi_api" / f"{symbol}.json"
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, cursor = {}, END - 29 * 86400000
    while cursor < END:
        try:
            page = json.loads(get(f"{BASE}/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=500&startTime={cursor}"))
        except HTTPError:
            cursor += 86400000
            continue
        if not page:
            break
        for r in page:
            rows[r["timestamp"]] = r
        cursor = page[-1]["timestamp"] + 3600000
        if len(page) < 500:
            break
        time.sleep(0.3)
    out.write_text(json.dumps([rows[k] for k in sorted(rows)]))


def metrics(symbol, day):
    out = DATA / "metrics" / symbol / f"{day}.csv"
    if out.exists() or (out.parent / f"{day}.missing").exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        blob = get(f"https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip")
    except HTTPError:
        (out.parent / f"{day}.missing").write_text("404")
        return
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        out.write_bytes(z.read(z.namelist()[0]))


def main():
    info = json.loads((DATA / "exchangeInfo.json").read_text())
    usdc = [s["symbol"] for s in info["symbols"] if s["marginAsset"] == "USDC" and s["quoteAsset"] == "USDC"
            and s["contractType"] == "PERPETUAL"]
    jobs = []
    for s in usdc:
        jobs += [("klines", s, "1d", ms("2026-04-20 00:00")), ("klines", s, "4h", ms("2026-09-01 00:00")),
                 ("klines", s, "1h", ms("2026-10-10 00:00")), ("klines", s, "5m", ms("2026-10-28 12:00")),
                 ("klines", s, "1m", ms("2026-10-28 22:00")), ("markPriceKlines", s, "1m", ms("2026-10-28 22:00"))]
        jobs.append(("funding", s, ms("2026-10-28 00:00")))
    for s in ALT:
        jobs += [("klines", s, "1d", ms("2026-04-20 00:00")), ("klines", s, "1h", ms("2026-06-25 00:00")),
                 ("klines", s, "1m", ms("2026-10-28 22:00")), ("markPriceKlines", s, "1m", ms("2026-10-28 22:00"))]
        jobs.append(("funding", s, ms("2026-10-28 00:00")))
    for s in MAJORS:
        jobs.append(("oi_api", s))
        day = datetime(2026, 9, 25, tzinfo=timezone.utc)
        while day < datetime(2026, 10, 30, tzinfo=timezone.utc):
            jobs.append(("metrics", s, day.strftime("%Y-%m-%d")))
            day += timedelta(days=1)

    def run(job):
        kind, *args = job
        try:
            if kind in ("klines", "markPriceKlines"):
                klines(kind, *args)
            else:
                globals()[kind](*args)
        except Exception as e:
            print("FAIL", job, e, file=sys.stderr)

    with ThreadPoolExecutor(4) as pool:
        for i, _ in enumerate(pool.map(run, jobs)):
            if i % 50 == 0:
                print(i, "/", len(jobs), flush=True)
    # Current top-of-book sizes are used only as a depth proxy (no historical book is public via REST).
    (DATA / "bookTicker_now.json").write_bytes(get(f"{BASE}/fapi/v1/ticker/bookTicker"))


if __name__ == "__main__":
    main()
