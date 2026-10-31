"""S4: Polymarket crypto Up/Down snapshot. Public data only, no keys, no orders. Python 3.9, stdlib only.

Lists currently-live crypto Up/Down markets (5m / 15m / hourly / 4h) with time left, the start reference,
the current Binance price, the move since start, and best bid/ask on both outcomes.

Resolution sources (from Gamma market `resolutionSource` / `description`, verified 2026-10-19):
  5m, 15m, 4h : Chainlink <asset>/USD 60s TWAP over the window vs the price at window start. Gamma does not
                publish the start price; we use the Binance 1m kline open at eventStartTime as a PROXY.
  hourly      : Binance <ASSET>/USDT 1h candle close >= open (HYPE: Binance futures HYPEUSDT). The start
                price is EXACTLY the Binance 1h kline open, so this reference is authoritative.
Usage: python3 pm_crypto_lastsecs.py --once [--horizon-min 70]
"""
import argparse, json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

GAMMA = "https://gamma-api.polymarket.com/events/keyset"
CLOB_BOOKS = "https://clob.polymarket.com/books"
BN_SPOT = "https://api.binance.com/api/v3"
BN_FUT = "https://fapi.binance.com/fapi/v1"
ASSETS = {"btc": "BTC", "bitcoin": "BTC", "eth": "ETH", "ethereum": "ETH", "sol": "SOL", "solana": "SOL",
          "xrp": "XRP", "doge": "DOGE", "dogecoin": "DOGE", "bnb": "BNB", "hype": "HYPE", "zec": "ZEC"}
FUTURES_ONLY = {"HYPE"}


def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json",
                                                          "user-agent": "claude-quantos-scanner"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def live_markets(horizon_min):
    now = datetime.now(timezone.utc)
    base = (f"{GAMMA}?tag_slug=up-or-down&closed=false&limit=100&end_date_min={iso(now)}"
            f"&end_date_max={iso(now + timedelta(minutes=horizon_min))}")
    out, cursor = [], None
    while True:
        page = http(base + (f"&after_cursor={cursor}" if cursor else ""))
        for ev in page.get("events") or []:
            series = ev.get("seriesSlug") or ""
            asset = ASSETS.get(series.split("-")[0])
            if not asset:
                continue  # non-crypto up/down (FX, stocks, commodities)
            for m in ev.get("markets") or []:
                start, end = m.get("eventStartTime"), m.get("endDate")
                if not start or not end or not (parse(start) <= now < parse(end)) or m.get("closed"):
                    continue
                toks = m.get("clobTokenIds")
                toks = json.loads(toks) if isinstance(toks, str) else toks
                out.append({"slug": ev["slug"], "series": series, "asset": asset, "start": parse(start),
                            "end": parse(end), "up": toks[0], "down": toks[1],
                            "binance_resolved": "binance.com" in (m.get("resolutionSource") or ""),
                            "res_src": m.get("resolutionSource")})
        cursor = page.get("next_cursor")
        if not page.get("events") or not cursor:
            return out


def binance_price(asset):
    if asset in FUTURES_ONLY:
        return float(http(f"{BN_FUT}/ticker/price?symbol={asset}USDT")["price"])
    return float(http(f"{BN_SPOT}/ticker/price?symbol={asset}USDT")["price"])


def binance_open(asset, start, hourly):
    root = BN_FUT if asset in FUTURES_ONLY else BN_SPOT
    ms = int(start.timestamp() * 1000)
    k = http(f"{root}/klines?symbol={asset}USDT&interval={'1h' if hourly else '1m'}&startTime={ms}&limit=1")
    return float(k[0][1]) if k and int(k[0][0]) == ms else None


def top(book, side):
    lv = (book or {}).get(side) or []
    if not lv:
        return None, 0.0
    best = (max if side == "bids" else min)(lv, key=lambda l: float(l["price"]))
    return float(best["price"]), float(best["size"])


def snapshot(horizon_min):
    t0 = time.time()
    ms = live_markets(horizon_min)
    toks = [t for m in ms for t in (m["up"], m["down"])]
    books = {}
    for i in range(0, len(toks), 200):
        for b in http(CLOB_BOOKS, [{"token_id": t} for t in toks[i:i + 200]]):
            books[b["asset_id"]] = b
    assets = sorted({m["asset"] for m in ms})
    with ThreadPoolExecutor(8) as ex:
        prices = dict(zip(assets, ex.map(binance_price, assets)))
        opens = list(ex.map(lambda m: binance_open(m["asset"], m["start"], m["binance_resolved"]), ms))
    now = datetime.now(timezone.utc)
    rows = []
    for m, ref in zip(ms, opens):
        ub, ubs = top(books.get(m["up"]), "bids")
        ua, uas = top(books.get(m["up"]), "asks")
        db, _ = top(books.get(m["down"]), "bids")
        da, _ = top(books.get(m["down"]), "asks")
        px = prices.get(m["asset"])
        rows.append(dict(m, start=m["start"].isoformat(), end=m["end"].isoformat(),
                         secs_left=(m["end"] - now).total_seconds(), ref=ref,
                         ref_kind="exact_binance_1h_open" if m["binance_resolved"] else "proxy_binance_1m_open",
                         price=px, move_bp=(px / ref - 1) * 1e4 if ref and px else None,
                         up_bid=ub, up_ask=ua, up_ask_size=uas, down_bid=db, down_ask=da))
    rows.sort(key=lambda r: r["secs_left"])
    print(f"{now.isoformat()[:19]} live crypto up/down markets: {len(rows)}  ({time.time() - t0:.1f}s)")
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--horizon-min", type=int, default=250, help="only markets ending within this many minutes")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=int, default=5)
    a = p.parse_args()
    while True:
        try:
            rows = snapshot(a.horizon_min)
            print(f"{'market':<44} {'left':>6} {'ref':>11} {'binance':>11} {'move_bp':>7}  {'up b/a':>11} {'down b/a':>11} ref_kind")
            for r in rows:
                mv = "-" if r["move_bp"] is None else f"{r['move_bp']:+7.1f}"
                fmt = lambda x: "-" if x is None else f"{x:.3f}"
                print(f"{r['slug'][:44]:<44} {r['secs_left']:6.0f} {r['ref'] or 0:11.4f} {r['price'] or 0:11.4f} {mv:>7}  "
                      f"{fmt(r['up_bid'])}/{fmt(r['up_ask'])} {fmt(r['down_bid'])}/{fmt(r['down_ask'])} {r['ref_kind']}")
        except Exception as e:
            print("scan error:", e)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
