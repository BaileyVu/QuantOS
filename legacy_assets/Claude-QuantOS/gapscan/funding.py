"""Perp funding gaps across Binance, Bybit and Hyperliquid (extends scanners/funding_spread.py, which is unchanged).

Delta-neutral: short the perp on the venue with higher funding, long on the lower. Net over a hold period after
4 taker legs + slippage. No spot borrow needed.
"""
from common import http, f

TAKER = {"binance": 0.0005, "bybit": 0.00055, "hyperliquid": 0.00045}
SLIP = 0.0003


def binance():
    hours = {x["symbol"]: int(x["fundingIntervalHours"]) for x in http("https://fapi.binance.com/fapi/v1/fundingInfo")}
    live = {x["symbol"] for x in http("https://fapi.binance.com/fapi/v1/exchangeInfo")["symbols"] if x.get("status") == "TRADING"}
    out = {}
    for x in http("https://fapi.binance.com/fapi/v1/premiumIndex"):
        s = x["symbol"]
        if s.endswith("USDT") and s in live:
            out[s[:-4]] = f(x["lastFundingRate"]) / hours.get(s, 8)
    return out


def bybit():
    info = http("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")["result"]["list"]
    mins = {i["symbol"]: f(i.get("fundingInterval")) or 480.0 for i in info if i.get("status") == "Trading"}  # minutes
    out = {}
    for t in http("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]:
        s = t["symbol"]
        if s.endswith("USDT") and s in mins and t.get("fundingRate"):
            out[s[:-4]] = f(t["fundingRate"]) / (mins.get(s, 480.0) / 60)
    return out


def hyperliquid():
    meta, ctxs = http("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    return {a["name"]: f(c["funding"]) for a, c in zip(meta["universe"], ctxs) if not a.get("isDelisted")}


VENUES = {"binance": binance, "bybit": bybit, "hyperliquid": hyperliquid}


def gaps(rates, ts, hold_hours=24.0, size_usd=100.0, min_net=0.0):
    rows = []
    coins = set().union(*[set(r) for r in rates.values()]) if rates else set()
    for c in coins:
        have = {v: r[c] for v, r in rates.items() if c in r}
        if len(have) < 2:
            continue
        hi = max(have, key=have.get)
        lo = min(have, key=have.get)
        diff = have[hi] - have[lo]
        cost = 2 * (TAKER[hi] + TAKER[lo] + 2 * SLIP)
        net = diff * hold_hours - cost
        if net >= min_net:
            rows.append({"ts": ts, "kind": "funding", "base": c, "short": hi, "long": lo, "diff_h": diff,
                         "apr": diff * 24 * 365, "net_hold": net, "net_usd_hold": net * size_usd, "hold_h": hold_hours,
                         "note": "funding can flip; assumes rate persists for the hold"})
    return sorted(rows, key=lambda r: -r["net_hold"])
