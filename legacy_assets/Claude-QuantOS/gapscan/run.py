"""Gap scanner loop. Read-only: public data, no keys, no orders.

  python3 run.py --once            # one pass of every scanner, prints the top rows
  python3 run.py                   # loop: CEX every 60s, funding + DEX every 10 min
Rows go to data/gaps_YYYY-MM-DD.jsonl; errors to data/errors.jsonl. Summarise with report.py.
"""
import argparse, json, os, time
import cex, dex, funding
from common import now, append_jsonl

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ADDR_CACHE = os.path.join(DATA, "cg_addresses.json")


def out_path():
    return os.path.join(DATA, f"gaps_{time.strftime('%Y-%m-%d', time.gmtime())}.jsonl")


def log_errors(ts, scanner, errors):
    if errors:
        append_jsonl(os.path.join(DATA, "errors.jsonl"), [{"ts": ts, "scanner": scanner, "errors": errors}])


def addresses(symbols):
    if os.path.exists(ADDR_CACHE) and time.time() - os.path.getmtime(ADDR_CACHE) < 86400:
        return json.load(open(ADDR_CACHE))
    m = dex.coingecko_addresses(symbols)
    os.makedirs(DATA, exist_ok=True)
    json.dump(m, open(ADDR_CACHE, "w"))
    return m


def show(rows, n=8):
    for r in rows[:n]:
        if r["kind"] == "cex":
            print(f"  CEX  {r['base']:>10} buy {r['buy']:<7} sell {r['sell']:<7} net {r['net']*100:6.2f}%  "
                  f"top size ${r['top_size_usd']:.0f}")
        elif r["kind"] == "dex":
            print(f"  DEX  {r['base']:>10} {r['route']:<40} net {r['net']*100:6.2f}%  pool ${r['pool_liq']/1e3:.0f}k")
        else:
            print(f"  FUND {r['base']:>10} short {r['short']:<11} long {r['long']:<11} APR {r['apr']*100:6.1f}%  "
                  f"net/{r['hold_h']:.0f}h ${r['net_usd_hold']:+.2f} per $100")


def cycle(i, size_usd):
    ts = now()
    books, errs = cex.fetch_all()
    log_errors(ts, "cex", errs)
    rows = cex.gaps(books, ts)
    print(f"{ts} cex venues ok: {sorted(books)} errors: {sorted(errs)}  gaps>=0.2% net: {len(rows)}")
    show(rows)
    if i % 10 == 0:
        rates, ferrs = {}, {}
        for name, fn in funding.VENUES.items():
            try:
                rates[name] = fn()
            except Exception as e:
                ferrs[name] = f"{type(e).__name__}: {e}"
        log_errors(ts, "funding", ferrs)
        frows = funding.gaps(rates, ts, size_usd=size_usd)
        print(f"{ts} funding venues ok: {sorted(rates)}  positive after costs: {len(frows)}")
        show(frows)
        rows += frows
        try:
            syms = sorted(set().union(*[set(b) for b in books.values()]))
            amap = addresses(syms)
            prices = dex.dex_prices(amap)
            drows = dex.gaps(books, prices, ts, size_usd=size_usd)
            print(f"{ts} dex tokens priced: {len(prices)}  gaps>=0.3% net: {len(drows)}")
            show(drows)
            rows += drows
        except Exception as e:
            log_errors(ts, "dex", {"dex": f"{type(e).__name__}: {e}"})
            print("dex error:", e)
    append_jsonl(out_path(), rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--size", type=float, default=100.0, help="trade size in USD for DEX impact and $ figures")
    ap.add_argument("--hours", type=float, default=72.0)
    a = ap.parse_args()
    i = 0
    start = time.time()
    while True:
        try:
            cycle(i, a.size)
        except Exception as e:
            print("cycle error:", e)
        if a.once:
            break
        if time.time() - start > a.hours * 3600:
            break
        i += 1
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
