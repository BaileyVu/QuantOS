"""S4 shadow: read-only recorder for Polymarket hourly crypto Up/Down markets. No keys, no orders.

These markets resolve on the Binance <ASSET>/USDT 1h candle (close >= open -> Up); HYPE uses Binance futures.
From 10 minutes before each hour close until close, every 10s we record (type="tick"): seconds left, exact
Binance 1h open, live Binance price, move in bp, realized 1-min volatility (bp) over the last 30 minutes,
Up/Down best bid/ask with sizes, and the taker fee schedule. After close we record the Binance outcome
(type="outcome") and later the official Polymarket resolution from Gamma (type="resolution").
Usage: python3 -u pm_hourly_shadow.py --hours 24 --out data/pm_hourly_shadow.jsonl
"""
import argparse, json, math, os, time, urllib.request
from datetime import datetime, timedelta, timezone

GAMMA = "https://gamma-api.polymarket.com"
CLOB_BOOKS = "https://clob.polymarket.com/books"
BN_SPOT = "https://api.binance.com/api/v3"
BN_FUT = "https://fapi.binance.com/fapi/v1"
ASSETS = {"btc": "BTC", "bitcoin": "BTC", "eth": "ETH", "ethereum": "ETH", "sol": "SOL", "solana": "SOL",
          "xrp": "XRP", "doge": "DOGE", "dogecoin": "DOGE", "bnb": "BNB", "hype": "HYPE", "zec": "ZEC"}
WINDOW_S = 600
TICK_S = 10


def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json",
                                                          "user-agent": "claude-quantos-scanner"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def hourly_markets(close):
    """Hourly crypto markets resolving on Binance whose window ends at `close`."""
    url = (f"{GAMMA}/events/keyset?tag_slug=up-or-down&limit=100&end_date_min={iso(close - timedelta(seconds=1))}"
           f"&end_date_max={iso(close + timedelta(seconds=1))}")
    out, cursor = [], None
    while True:
        page = http(url + (f"&after_cursor={cursor}" if cursor else ""))
        for ev in page.get("events") or []:
            series = ev.get("seriesSlug") or ""
            asset = ASSETS.get(series.split("-")[0])
            if not asset or "hourly" not in series:
                continue
            for m in ev.get("markets") or []:
                src = m.get("resolutionSource") or ""
                if "binance.com" not in src:
                    continue
                toks = m.get("clobTokenIds")
                toks = json.loads(toks) if isinstance(toks, str) else toks
                out.append({"slug": m.get("slug") or ev["slug"], "asset": asset, "up": toks[0], "down": toks[1],
                            "futures": "/futures/" in src, "fee": m.get("feeSchedule"),
                            "fees_enabled": m.get("feesEnabled")})
        cursor = page.get("next_cursor")
        if not page.get("events") or not cursor:
            return out


def kline(asset, futures, interval, start_ms, limit):
    base = BN_FUT if futures else BN_SPOT
    return http(f"{base}/klines?symbol={asset}USDT&interval={interval}&startTime={start_ms}&limit={limit}")


def price(asset, futures):
    base = BN_FUT if futures else BN_SPOT
    return float(http(f"{base}/ticker/price?symbol={asset}USDT")["price"])


def vol_30m_bp(asset, futures, now_ms):
    ks = kline(asset, futures, "1m", now_ms - 31 * 60_000, 31)
    closes = [float(k[4]) for k in ks]
    rets = [math.log(b / a) * 1e4 for a, b in zip(closes, closes[1:])]
    if len(rets) < 5:
        return None
    mu = sum(rets) / len(rets)
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))


def top(book, side):
    lv = (book or {}).get(side) or []
    if not lv:
        return None, None
    b = (max if side == "bids" else min)(lv, key=lambda l: float(l["price"]))
    return float(b["price"]), float(b["size"])


def write(f, rec):
    f.write(json.dumps(rec) + "\n")
    f.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--out", default="data/pm_hourly_shadow.jsonl")
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    stop_at = time.time() + a.hours * 3600
    pending = []        # markets awaiting outcome/resolution: dict(m, close=..., open=...)
    cur_close, markets, opens, vols, vol_min = None, [], {}, {}, None
    f = open(a.out, "a")
    hard_stop = stop_at + 1800  # after the window, at most 30 min to collect the last outcomes/resolutions
    while (time.time() < stop_at or pending) and time.time() < hard_stop:
        t0 = time.time()
        now = datetime.now(timezone.utc)
        try:
            close = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            left = (close - now).total_seconds()
            in_window = left <= WINDOW_S and time.time() < stop_at
            if in_window and cur_close != close:
                cur_close = close
                markets = hourly_markets(close)
                start_ms = int((close - timedelta(hours=1)).timestamp() * 1000)
                opens = {}
                for m in markets:
                    k = kline(m["asset"], m["futures"], "1h", start_ms, 1)
                    opens[m["slug"]] = float(k[0][1]) if k and int(k[0][0]) == start_ms else None
                for m in markets:
                    pending.append(dict(m, close=close.isoformat(), open=opens[m["slug"]], start_ms=start_ms,
                                        outcome_done=False, resolution_done=False))
                print(f"{now.isoformat()[:19]} window for close {close.isoformat()[:16]}: {len(markets)} markets "
                      f"{sorted({m['asset'] for m in markets})}")
            if in_window and markets:
                minute = int(now.timestamp() // 60)
                if vol_min != minute:
                    vols = {m["asset"]: vol_30m_bp(m["asset"], m["futures"], int(now.timestamp() * 1000))
                            for m in markets}
                    vol_min = minute
                prices = {(m["asset"], m["futures"]): None for m in markets}
                for key in prices:
                    prices[key] = price(*key)
                toks = [t for m in markets for t in (m["up"], m["down"])]
                books = {b["asset_id"]: b for b in http(CLOB_BOOKS, [{"token_id": t} for t in toks])}
                ts = datetime.now(timezone.utc)
                for m in markets:
                    px, op = prices[(m["asset"], m["futures"])], opens.get(m["slug"])
                    ub, ubs = top(books.get(m["up"]), "bids")
                    ua, uas = top(books.get(m["up"]), "asks")
                    db, dbs = top(books.get(m["down"]), "bids")
                    da, das = top(books.get(m["down"]), "asks")
                    write(f, {"type": "tick", "ts": ts.isoformat(), "slug": m["slug"], "asset": m["asset"],
                              "secs_left": round((close - ts).total_seconds(), 1), "open": op, "price": px,
                              "move_bp": round((px / op - 1) * 1e4, 2) if op else None,
                              "vol1m_30m_bp": round(vols.get(m["asset"]) or 0, 3),
                              "up_bid": ub, "up_bid_sz": ubs, "up_ask": ua, "up_ask_sz": uas,
                              "down_bid": db, "down_bid_sz": dbs, "down_ask": da, "down_ask_sz": das,
                              "fee": m["fee"], "fees_enabled": m["fees_enabled"]})

            for pm in pending:
                cl = datetime.fromisoformat(pm["close"])
                since = (now - cl).total_seconds()
                if not pm["outcome_done"] and since >= 15:
                    k = kline(pm["asset"], pm["futures"], "1h", pm["start_ms"], 1)
                    if k and int(k[0][0]) == pm["start_ms"] and int(k[0][6]) < int(now.timestamp() * 1000):
                        o, c = float(k[0][1]), float(k[0][4])
                        write(f, {"type": "outcome", "ts": now.isoformat(), "slug": pm["slug"], "asset": pm["asset"],
                                  "open": o, "close": c, "move_bp": round((c / o - 1) * 1e4, 2),
                                  "binance_outcome": "Up" if c >= o else "Down"})
                        pm["outcome_done"] = True
                if pm["outcome_done"] and not pm["resolution_done"] and since >= 120 and int(since) % 120 < TICK_S * 3:
                    g = http(f"{GAMMA}/markets?slug={pm['slug']}")
                    if g:
                        mk = g[0]
                        prices = json.loads(mk["outcomePrices"]) if isinstance(mk.get("outcomePrices"), str) else mk.get("outcomePrices")
                        if prices and sorted(prices) == ["0", "1"]:
                            write(f, {"type": "resolution", "ts": now.isoformat(), "slug": pm["slug"],
                                      "official": "Up" if prices[0] == "1" else "Down", "closed": mk.get("closed"),
                                      "uma_status": mk.get("umaResolutionStatus")})
                            pm["resolution_done"] = True
                if since > 4 * 3600 and not pm["resolution_done"]:
                    write(f, {"type": "resolution", "ts": now.isoformat(), "slug": pm["slug"], "official": None,
                              "note": "not resolved within 4h"})
                    pm["resolution_done"] = True
            pending = [pm for pm in pending if not (pm["outcome_done"] and pm["resolution_done"])]
        except Exception as e:
            print(f"{now.isoformat()[:19]} hourly shadow error: {e}")
        time.sleep(max(1.0, TICK_S - (time.time() - t0)))
    f.close()
    print("done: reached --hours limit")


if __name__ == "__main__":
    main()
