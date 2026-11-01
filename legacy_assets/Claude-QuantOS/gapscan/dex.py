"""CEX vs DEX gaps via DexScreener (all chains it indexes: ethereum, solana, bsc, base, arbitrum, aurora/near...).

Token identity comes from CoinGecko contract addresses (one call), so we compare the SAME token, not a lookalike.
DEX price is the pool mid price, so we add a swap fee and a constant-product price-impact estimate for the
trade size. Treat results as candidates: confirm with a real aggregator quote (Jupiter / 0x) before believing them.
"""
import time
from common import http, f

CG_LIST = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
DS_TOKENS = "https://api.dexscreener.com/tokens/v1/{chain}/{addrs}"
# CoinGecko platform id -> DexScreener chain id
CHAINS = {"ethereum": "ethereum", "solana": "solana", "binance-smart-chain": "bsc", "base": "base",
          "arbitrum-one": "arbitrum", "aurora": "aurora", "near-protocol": "near", "polygon-pos": "polygon"}
SWAP_FEE = 0.003
GAS_USD = {"ethereum": 3.0, "solana": 0.01, "bsc": 0.05, "base": 0.02, "arbitrum": 0.05, "aurora": 0.01,
           "near": 0.01, "polygon": 0.02}


def coingecko_addresses(symbols):
    """{SYMBOL: [(chain, address), ...]} for symbols with exactly one CoinGecko coin (ambiguous symbols skipped)."""
    by_sym = {}
    for c in http(CG_LIST, timeout=60):
        by_sym.setdefault(c["symbol"].upper(), []).append(c)
    out = {}
    for s in symbols:
        coins = by_sym.get(s, [])
        if len(coins) != 1:
            continue
        addrs = [(CHAINS[p], a) for p, a in (coins[0].get("platforms") or {}).items() if p in CHAINS and a]
        if addrs:
            out[s] = addrs
    return out


def dex_prices(addr_map, pause=0.25):
    """{SYMBOL: best pool dict} picking the most liquid pool per token across chains."""
    by_chain = {}
    for sym, lst in addr_map.items():
        for chain, a in lst:
            by_chain.setdefault(chain, []).append((sym, a))
    best = {}
    for chain, items in by_chain.items():
        for i in range(0, len(items), 30):
            chunk = items[i:i + 30]
            lookup = {a.lower(): s for s, a in chunk}
            try:
                pairs = http(DS_TOKENS.format(chain=chain, addrs=",".join(a for _, a in chunk)))
            except Exception:
                continue
            for p in pairs or []:
                sym = lookup.get(p.get("baseToken", {}).get("address", "").lower())
                liq = f((p.get("liquidity") or {}).get("usd"))
                if not sym or liq <= 0 or not p.get("priceUsd"):
                    continue
                if sym not in best or liq > best[sym]["liq"]:
                    best[sym] = {"chain": chain, "dex": p.get("dexId"), "pair": p.get("pairAddress"),
                                 "price": f(p["priceUsd"]), "liq": liq, "vol24h": f((p.get("volume") or {}).get("h24"))}
            time.sleep(pause)
    return best


def impact(size_usd, liq_usd):
    """Constant-product price impact for a trade of size_usd into a pool with liq_usd total (half per side)."""
    side = liq_usd / 2
    return size_usd / (side + size_usd) if side > 0 else 1.0


def gaps(cex_books, dex, ts, size_usd=100.0, cex_taker=0.001, min_net=0.003, max_gross=0.10, min_liq=50000.0):
    """Pools under min_liq and DEX/CEX price ratios off by more than max_gross (wrong token or bad decimals) are dropped."""
    rows = []
    for sym, d in dex.items():
        if d["liq"] < min_liq:
            continue
        imp = impact(size_usd, d["liq"]) + SWAP_FEE + GAS_USD.get(d["chain"], 1.0) / size_usd
        for venue, book in cex_books.items():
            q = book.get(sym)
            if not q or q[0] <= 0 or q[2] <= 0:
                continue
            bid, ask = q[0], q[2]
            if abs(d["price"] / ((bid + ask) / 2) - 1) > max_gross:
                continue
            # buy on DEX, sell on CEX
            g1 = bid / (d["price"] * (1 + imp)) - 1 - cex_taker
            # buy on CEX, sell on DEX
            g2 = d["price"] * (1 - imp) / ask - 1 - cex_taker
            for net, direction in ((g1, f"buy dex {d['chain']} -> sell {venue}"), (g2, f"buy {venue} -> sell dex {d['chain']}")):
                if net >= min_net:
                    rows.append({"ts": ts, "kind": "dex", "base": sym, "route": direction, "dex_price": d["price"],
                                 "cex_bid": bid, "cex_ask": ask, "pool_liq": d["liq"], "pool_vol24h": d["vol24h"],
                                 "dex": d["dex"], "pair": d["pair"], "net": net, "net_usd_at_size": net * size_usd,
                                 "size_usd": size_usd, "note": "mid-price estimate; confirm with real quote + bridge/withdraw time"})
    return sorted(rows, key=lambda r: -r["net"])
