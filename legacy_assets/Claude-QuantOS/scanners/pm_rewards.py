"""S3: Polymarket maker liquidity-rewards scanner. Public data only, no keys, no orders. Python 3.9, stdlib only.

Sources (verified 2026-10-19):
  CLOB /sampling-markets          -> markets in the rewards program: tokens, rewards{rates[].rewards_daily_rate,
                                     min_size (shares), max_spread (cents)}, market_slug, end_date_iso, neg_risk
  CLOB /books (POST)              -> current book of the YES token (NO book is its mirror)
  Gamma /markets?condition_ids=   -> volume24hr
  CLOB /prices-history            -> 1 day of 5-minute prices for a volatility proxy

Reward model (simplified from Polymarket's liquidity-rewards docs):
  order score  = size * ((v - s) / v) ** 2   for an order s cents from the mid, v = max_spread; 0 if s > v
  two-sided requirement: mid in [0.10, 0.90] lets single-sided orders count at 1/3; outside, only two-sided counts.
  our share    = our_score / (competitor_score + our_score); reward/day = daily_rate * our share.
We assume $100 is quoted as $50 bid + $50 ask at the current best bid/ask (joining the touch). Simplifications:
book levels aggregate orders (cannot drop competitor orders below min_size), scoring is sampled each minute
(we use one snapshot), the per-epoch min(bid score, ask score) pairing is approximated by quoting both sides equally,
and fills/adverse selection are NOT costed. Treat output as a ranking, not a PnL forecast.
Usage: python3 pm_rewards.py --once --min-rate 20 --top 15
"""
import argparse, json, math, os, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
WORKERS = 8
QUOTE_USD = 100.0


def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json",
                                                          "user-agent": "claude-quantos-scanner"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def reward_markets(min_rate):
    out, cursor = [], ""
    while True:
        page = http(f"{CLOB}/sampling-markets" + (f"?next_cursor={cursor}" if cursor else ""))
        for m in page.get("data") or []:
            rw = m.get("rewards") or {}
            rate = sum(float(r.get("rewards_daily_rate") or 0) for r in rw.get("rates") or [])
            toks = m.get("tokens") or []
            if (rate >= min_rate and m.get("accepting_orders") and not m.get("closed") and len(toks) == 2
                    and rw.get("max_spread")):
                out.append({"condition_id": m["condition_id"], "slug": m.get("market_slug"), "rate": rate,
                            "min_size": float(rw.get("min_size") or 0), "max_spread": float(rw["max_spread"]),
                            "yes": toks[0]["token_id"], "end": m.get("end_date_iso"), "neg_risk": m.get("neg_risk"),
                            "tick": float(m.get("minimum_tick_size") or 0.01)})
        cursor = page.get("next_cursor")
        if not cursor or cursor == "LTE=":
            return out


def parallel(fn, items):
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(fn, items))


def fetch_books(token_ids):
    def one(chunk):
        try:
            return http(f"{CLOB}/books", [{"token_id": t} for t in chunk])
        except Exception as e:
            print("books error:", e)
            return []
    books = {}
    for res in parallel(one, [token_ids[i:i + 200] for i in range(0, len(token_ids), 200)]):
        for b in res:
            books[b["asset_id"]] = b
    return books


def score(levels, mid, v_cents, side):
    """Quadratic score of resting levels within v cents of mid; returns (score in shares, depth in $)."""
    sc, usd = 0.0, 0.0
    for l in levels:
        p, size = float(l["price"]), float(l["size"])
        s = abs(mid - p) * 100
        if s > v_cents or (side == "bid" and p > mid) or (side == "ask" and p < mid):
            continue
        sc += size * ((v_cents - s) / v_cents) ** 2
        usd += size * (p if side == "bid" else 1 - p)
    return sc, usd


def evaluate(m, book):
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = max(float(l["price"]) for l in bids)
    best_ask = min(float(l["price"]) for l in asks)
    mid, v = (best_bid + best_ask) / 2, m["max_spread"]
    spread_c = (best_ask - best_bid) * 100
    bid_sc, bid_usd = score(bids, mid, v, "bid")
    ask_sc, ask_usd = score(asks, mid, v, "ask")
    s_c = spread_c / 2  # we join the touch on both sides
    if s_c > v:
        our = 0.0
    else:
        w = ((v - s_c) / v) ** 2
        our_bid = max(QUOTE_USD / 2 / best_bid, m["min_size"]) * w
        our_ask = max(QUOTE_USD / 2 / (1 - best_ask), m["min_size"]) * w
        our = our_bid + our_ask
    # min-size requirement may force more than $100 of quotes; report actual $ needed
    need_usd = max(QUOTE_USD / 2, m["min_size"] * best_bid) + max(QUOTE_USD / 2, m["min_size"] * (1 - best_ask))
    share = our / (bid_sc + ask_sc + our) if our else 0.0
    simple = m["rate"] * QUOTE_USD / (bid_usd + ask_usd + QUOTE_USD)
    return dict(m, mid=mid, spread_c=spread_c, comp_depth_usd=bid_usd + ask_usd, need_usd=need_usd,
                reward_day_scored=m["rate"] * share, reward_day_simple=simple,
                last_trade=float(book.get("last_trade_price") or mid), two_sided_only=not (0.10 <= mid <= 0.90))


def volatility(token):
    try:
        h = http(f"{CLOB}/prices-history?market={token}&interval=1d&fidelity=5").get("history") or []
    except Exception:
        return None
    ps = [float(x["p"]) for x in h]
    d = [b - a for a, b in zip(ps, ps[1:])]
    if len(d) < 10:
        return None
    mu = sum(d) / len(d)
    return math.sqrt(sum((x - mu) ** 2 for x in d) / (len(d) - 1))


def volumes(cids):
    def one(chunk):
        try:
            return http(f"{GAMMA}/markets?" + "&".join("condition_ids=" + c for c in chunk))
        except Exception as e:
            print("gamma error:", e)
            return []
    out = {}
    for res in parallel(one, [cids[i:i + 20] for i in range(0, len(cids), 20)]):
        for g in res:
            out[g.get("conditionId")] = float(g.get("volume24hr") or 0)
    return out


def scan(min_rate, top):
    t0 = time.time()
    ms = reward_markets(min_rate)
    books = fetch_books([m["yes"] for m in ms])
    rows = [r for r in (evaluate(m, books[m["yes"]]) for m in ms if m["yes"] in books) if r]
    rows.sort(key=lambda r: -r["reward_day_scored"])
    short = rows[:max(top * 4, 60)]
    for r, vol in zip(short, parallel(volatility, [r["yes"] for r in short])):
        r["vol_5m"] = vol
    vols = volumes([r["condition_id"] for r in short])
    ts = datetime.now(timezone.utc).isoformat()
    for r in short:
        r["ts"] = ts
        r["vol24h"] = vols.get(r["condition_id"])
        v = r["vol_5m"] if r["vol_5m"] is not None else abs(r["last_trade"] - r["mid"])
        r["rank_score"] = r["reward_day_scored"] / max(v, 0.002)
    short.sort(key=lambda r: -r["rank_score"])
    print(f"reward markets>=${min_rate}/day: {len(ms)}  with books: {len(rows)}  "
          f"total pool ${sum(r['rate'] for r in rows):,.0f}/day  scan {time.time() - t0:.0f}s")
    return short


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-rate", type=float, default=20.0, help="min daily reward pool ($)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--interval", type=int, default=600)
    p.add_argument("--out", default="data/pm_rewards.jsonl")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    while True:
        try:
            rows = scan(a.min_rate, a.top)
            with open(a.out, "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            print(f"{'market':<48} {'pool/d':>7} {'minSz':>5} {'maxSp':>5} {'mid':>5} {'sprd':>5} {'compDepth$':>10} "
                  f"{'need$':>6} {'$/d@100':>7} {'simple':>7} {'vol5m':>6} {'vol24h':>9}")
            for r in rows[:a.top]:
                vol = "-" if r["vol_5m"] is None else f"{r['vol_5m']:.4f}"
                print(f"{(r['slug'] or '')[:48]:<48} {r['rate']:7.0f} {r['min_size']:5.0f} {r['max_spread']:5.1f} "
                      f"{r['mid']:5.3f} {r['spread_c']:5.1f} {r['comp_depth_usd']:10,.0f} {r['need_usd']:6.0f} "
                      f"{r['reward_day_scored']:7.2f} {r['reward_day_simple']:7.2f} {vol:>6} {r['vol24h'] or 0:9,.0f}"
                      f"{'  2-sided-only' if r['two_sided_only'] else ''}")
        except Exception as e:
            print("scan error:", e)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
