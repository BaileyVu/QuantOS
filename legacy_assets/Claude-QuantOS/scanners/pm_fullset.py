"""S2: Polymarket full-set scanner. Public data only, no keys, no orders. Python 3.9, stdlib only.

For every liquid active event it checks, at real book depth for a target number of sets:
  binary  buy-both : cost(YES asks) + cost(NO asks) + fees < 1   -> buy both, merge for $1
  binary  split-sell: proceeds(YES bids) + proceeds(NO bids) - fees > 1 -> split $1, sell both
  negRisk buy-all : sum over outcomes of cost(YES asks) + fees < 1 -> exactly one YES pays $1
Usage: python3 pm_fullset.py --sets 10 --interval 120 --out data/pm_fullset.jsonl
"""
import argparse, json, os, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com/events/keyset"
CLOB_BOOKS = "https://clob.polymarket.com/books"
DEFAULT_FEE_RATE = 0.07  # fallback when a market has no feeSchedule
WORKERS = 8


def http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json",
                                                          "user-agent": "claude-quantos-scanner"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def active_events(min_liquidity):
    # Gamma caps limit at 100 and rejects large offsets (422); page with keyset + after_cursor.
    events, cursor = [], None
    while True:
        url = f"{GAMMA}?active=true&closed=false&archived=false&liquidity_min={min_liquidity}&limit=100"
        page = http(url + (f"&after_cursor={cursor}" if cursor else ""))
        events.extend(page.get("events") or [])
        cursor = page.get("next_cursor")
        if not page.get("events") or not cursor or len(events) > 20000:
            return events


def as_list(v):
    return json.loads(v) if isinstance(v, str) else (v or [])


def tradable(m):
    return (not m.get("closed") and m.get("active") is not False and m.get("enableOrderBook", True)
            and m.get("acceptingOrders", True))


def fee_params(m):
    """(rate, exponent) for taker fee/share = rate * (p*(1-p))**exponent; (0, 1) when fees are off."""
    if m.get("feesEnabled") is False:
        return 0.0, 1.0
    fs = m.get("feeSchedule") or {}
    if "rate" not in fs:
        return DEFAULT_FEE_RATE, 1.0
    return float(fs["rate"]), float(fs.get("exponent") or 1)


def fetch_books(token_ids):
    chunks = [token_ids[i:i + 200] for i in range(0, len(token_ids), 200)]

    def one(chunk):
        try:
            return http(CLOB_BOOKS, [{"token_id": t} for t in chunk])
        except Exception as e:
            print("books error:", e)
            return []

    books = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for result in ex.map(one, chunks):
            for b in result:
                books[b["asset_id"]] = b
    return books


def walk(levels, shares, best_first_key, fee):
    """Cost (or proceeds) of `shares` against levels; None if depth is insufficient."""
    rate, expo = fee
    lv = sorted(((float(l["price"]), float(l["size"])) for l in levels), key=best_first_key)
    left, total, fees = shares, 0.0, 0.0
    for price, size in lv:
        take = min(left, size)
        total += take * price
        fees += take * rate * (price * (1 - price)) ** expo
        left -= take
        if left <= 1e-9:
            return total, fees
    return None


def buy(book, n, fee):
    return walk(book.get("asks", []), n, lambda x: x[0], fee) if book else None


def sell(book, n, fee):
    return walk(book.get("bids", []), n, lambda x: -x[0], fee) if book else None


def scan(n_sets, min_liquidity):
    t0 = time.time()
    events = active_events(min_liquidity)
    t_events = time.time() - t0
    markets = []
    for ev in events:
        for m in ev.get("markets", []):
            toks = as_list(m.get("clobTokenIds"))
            if tradable(m) and len(toks) == 2:
                markets.append((ev, m, toks))
    books = fetch_books([t for _, _, toks in markets for t in toks])
    ts = datetime.now(timezone.utc).isoformat()
    rows = []

    for ev, m, (yes, no) in markets:
        fee = fee_params(m)
        b = (buy(books.get(yes), n_sets, fee), buy(books.get(no), n_sets, fee))
        s = (sell(books.get(yes), n_sets, fee), sell(books.get(no), n_sets, fee))
        base = {"ts": ts, "event": ev.get("slug"), "market": m.get("slug"), "condition_id": m.get("conditionId"),
                "neg_risk": bool(m.get("negRisk")), "sets": n_sets, "fee_rate": fee[0]}
        if all(b):
            edge = n_sets - sum(c for c, _ in b) - sum(f for _, f in b)
            rows.append(dict(base, kind="binary_buy_both", edge_usd=edge, edge_per_set=edge / n_sets))
        if all(s):
            edge = sum(p for p, _ in s) - sum(f for _, f in s) - n_sets
            rows.append(dict(base, kind="binary_split_sell", edge_usd=edge, edge_per_set=edge / n_sets))

    skipped_augmented = 0
    for ev in events:
        ev_markets = ev.get("markets", [])
        if not ev.get("negRisk") or len(ev_markets) < 2:
            continue
        if ev.get("negRiskAugmented") or any(m.get("negRiskAugmented") for m in ev_markets):
            skipped_augmented += 1  # outcome set can change (placeholders/"other"): no guaranteed $1
            continue
        if not all(tradable(m) for m in ev_markets):
            continue  # an outcome resolved/closed/halted: remaining legs are not a full set
        legs = []
        for m in ev_markets:
            toks = as_list(m.get("clobTokenIds"))
            legs.append(buy(books.get(toks[0]), n_sets, fee_params(m)) if len(toks) == 2 else None)
        if all(legs):
            edge = n_sets - sum(c for c, _ in legs) - sum(f for _, f in legs)
            rows.append({"ts": ts, "event": ev.get("slug"), "kind": "negrisk_buy_all_yes", "legs": len(legs),
                         "sets": n_sets, "end": ev.get("endDate"), "edge_usd": edge, "edge_per_set": edge / n_sets})
    print(f"fetched events={len(events)} ({t_events:.0f}s) markets={len(markets)} books={len(books)} "
          f"negrisk_augmented_skipped={skipped_augmented} total={time.time() - t0:.0f}s")
    return len(markets), sorted(rows, key=lambda r: -r["edge_per_set"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sets", type=float, default=10.0)
    p.add_argument("--min-liquidity", type=float, default=5000.0)
    p.add_argument("--interval", type=int, default=120)
    p.add_argument("--out", default="data/pm_fullset.jsonl")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    while True:
        try:
            n_markets, rows = scan(a.sets, a.min_liquidity)
            positive = [r for r in rows if r["edge_usd"] > 0]
            with open(a.out, "a") as f:
                for r in positive + [r for r in rows[:20] if r["edge_usd"] <= 0]:  # positives + closest non-positive
                    f.write(json.dumps(r) + "\n")
            print(f"{datetime.now(timezone.utc).isoformat()[:19]} markets={n_markets} checks={len(rows)} positive={len(positive)}")
            for r in rows[:10]:
                print(f"  {r['kind']:<22} {r['edge_per_set']:+.4f}/set  ${r['edge_usd']:+.3f}  {r.get('market') or r['event']}")
        except Exception as e:
            print("scan error:", e)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
