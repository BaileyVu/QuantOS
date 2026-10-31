"""S1: Binance USD-M vs Hyperliquid funding-spread scanner. Public data only, no keys, no orders.

Logs, per shared symbol, the hourly funding difference and whether it beats round-trip costs
for a delta-neutral pair (long perp on the venue that pays, short on the one that charges).
Usage: python3 funding_spread.py --notional 100 --interval 60 --out data/funding_spread.jsonl
"""
import argparse, json, time, urllib.request
from datetime import datetime, timezone

BINANCE_PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_INFO = "https://fapi.binance.com/fapi/v1/fundingInfo"
HL_INFO = "https://api.hyperliquid.xyz/info"

# Assumed taker fees per leg (verify against your own tiers before trusting results).
BINANCE_TAKER = 0.0005
HL_TAKER = 0.00045
SLIPPAGE_PER_LEG = 0.0003


def get(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def binance_hourly_funding():
    intervals = {x["symbol"]: int(x["fundingIntervalHours"]) for x in get(BINANCE_INFO)}
    out = {}
    for x in get(BINANCE_PREMIUM):
        sym = x["symbol"]
        if not sym.endswith("USDT"):
            continue
        hours = intervals.get(sym, 8)
        out[sym[:-4]] = {"rate_h": float(x["lastFundingRate"]) / hours, "mark": float(x["markPrice"]),
                         "next_ms": int(x["nextFundingTime"]), "interval_h": hours}
    return out


def hyperliquid_hourly_funding():
    meta, ctxs = get(HL_INFO, {"type": "metaAndAssetCtxs"})
    out = {}
    for asset, ctx in zip(meta["universe"], ctxs):
        if asset.get("isDelisted"):
            continue
        out[asset["name"]] = {"rate_h": float(ctx["funding"]), "mark": float(ctx["markPx"]),
                              "oi": float(ctx["openInterest"]), "vol24h": float(ctx["dayNtlVlm"])}
    return out


def scan(notional, hold_hours):
    bn, hl = binance_hourly_funding(), hyperliquid_hourly_funding()
    ts = datetime.now(timezone.utc).isoformat()
    roundtrip_cost = 2 * (BINANCE_TAKER + HL_TAKER + 2 * SLIPPAGE_PER_LEG)
    rows = []
    for coin in sorted(set(bn) & set(hl)):
        b, h = bn[coin], hl[coin]
        diff_h = b["rate_h"] - h["rate_h"]  # >0: short Binance, long HL collects the spread
        gross = abs(diff_h) * hold_hours
        rows.append({"ts": ts, "coin": coin, "bn_rate_h": b["rate_h"], "hl_rate_h": h["rate_h"],
                     "diff_h": diff_h, "apr": diff_h * 24 * 365,
                     "basis": (b["mark"] - h["mark"]) / h["mark"],
                     "short_venue": "binance" if diff_h > 0 else "hyperliquid",
                     "net_frac_hold": gross - roundtrip_cost,
                     "net_usd_hold": (gross - roundtrip_cost) * notional,
                     "hl_vol24h": h["vol24h"], "bn_interval_h": b["interval_h"]})
    return sorted(rows, key=lambda r: -abs(r["diff_h"]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--notional", type=float, default=100.0)
    p.add_argument("--hold-hours", type=float, default=24.0)
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--out", default="data/funding_spread.jsonl")
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    import os
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    while True:
        try:
            rows = scan(a.notional, a.hold_hours)
            with open(a.out, "a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            for r in rows[:10]:
                print(f"{r['ts'][:19]} {r['coin']:>8} APR {r['apr']*100:7.1f}%  short {r['short_venue']:<11} "
                      f"net/{a.hold_hours:.0f}h ${r['net_usd_hold']:+.3f}  hlvol ${r['hl_vol24h']/1e6:.1f}M")
            print("-" * 60)
        except Exception as e:  # keep scanning through transient API errors
            print("scan error:", e)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
