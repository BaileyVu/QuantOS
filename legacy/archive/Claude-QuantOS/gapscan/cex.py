"""Cross-exchange spot gaps: buy at the ask on one exchange, sell at the bid on another.

Each venue returns {BASE: (bid, bid_qty, ask, ask_qty, quote_vol_24h)} for USDT pairs.
A gap is only real if (a) both tickers are the SAME token (ticker collisions are common), (b) deposits and
withdrawals are open, or you pre-hold inventory on both sides. The scanner flags, a human verifies.
"""
from common import http, f

TAKER = {"binance": 0.001, "bybit": 0.001, "okx": 0.001, "gate": 0.002, "mexc": 0.0005}


def binance():
    out = {}
    for t in http("https://api.binance.com/api/v3/ticker/bookTicker"):
        s = t["symbol"]
        if s.endswith("USDT"):
            out[s[:-4]] = (f(t["bidPrice"]), f(t["bidQty"]), f(t["askPrice"]), f(t["askQty"]), None)
    return out


def mexc():
    out = {}
    for t in http("https://api.mexc.com/api/v3/ticker/bookTicker"):
        s = t["symbol"]
        if s.endswith("USDT"):
            out[s[:-4]] = (f(t["bidPrice"]), f(t["bidQty"]), f(t["askPrice"]), f(t["askQty"]), None)
    return out


def bybit():
    out = {}
    for t in http("https://api.bybit.com/v5/market/tickers?category=spot")["result"]["list"]:
        s = t["symbol"]
        if s.endswith("USDT"):
            out[s[:-4]] = (f(t.get("bid1Price")), f(t.get("bid1Size")), f(t.get("ask1Price")), f(t.get("ask1Size")),
                           f(t.get("turnover24h")))
    return out


def okx():
    out = {}
    for t in http("https://www.okx.com/api/v5/market/tickers?instType=SPOT")["data"]:
        base, _, quote = t["instId"].partition("-")
        if quote == "USDT":
            out[base] = (f(t.get("bidPx")), f(t.get("bidSz")), f(t.get("askPx")), f(t.get("askSz")), f(t.get("volCcy24h")))
    return out


def gate():
    out = {}
    for t in http("https://api.gateio.ws/api/v4/spot/tickers"):
        base, _, quote = t["currency_pair"].partition("_")
        if quote == "USDT":
            # Gate tickers carry top-of-book sizes as highest_size / lowest_size (may be missing)
            out[base] = (f(t.get("highest_bid")), f(t.get("highest_size")), f(t.get("lowest_ask")), f(t.get("lowest_size")),
                         f(t.get("quote_volume")))
    return out


VENUES = {"binance": binance, "bybit": bybit, "okx": okx, "gate": gate, "mexc": mexc}


def gaps(books, ts, min_net=0.002, max_gross=0.10, min_size=20.0):
    """books: {venue: {BASE: tuple}}. Returns rows where net edge after both taker fees >= min_net.
    Gross gaps above max_gross are almost always different tokens sharing a ticker or frozen withdrawals: dropped.
    Gaps with less than min_size USD at the top of both books (or unknown size) are dropped too."""
    rows = []
    bases = set().union(*[set(b) for b in books.values()]) if books else set()
    for base in bases:
        quotes = {v: b[base] for v, b in books.items() if base in b and b[base][0] > 0 and b[base][2] > 0}
        for buy_v, qb in quotes.items():
            for sell_v, qs in quotes.items():
                if buy_v == sell_v:
                    continue
                ask, ask_qty, bid, bid_qty = qb[2], qb[3], qs[0], qs[1]
                gross = bid / ask - 1
                net = gross - TAKER[buy_v] - TAKER[sell_v]
                if net < min_net:
                    continue
                if gross > max_gross or not (ask_qty and bid_qty):
                    continue
                size_usd = min(ask * ask_qty, bid * bid_qty)
                if size_usd < min_size:
                    continue
                rows.append({"ts": ts, "kind": "cex", "base": base, "buy": buy_v, "sell": sell_v, "ask": ask, "bid": bid,
                             "gross": gross, "net": net, "top_size_usd": size_usd,
                             "net_usd_top": net * size_usd,
                             "note": "verify same token + deposit/withdraw open"})
    return sorted(rows, key=lambda r: -r["net"])


def fetch_all():
    books, errors = {}, {}
    for name, fn in VENUES.items():
        try:
            books[name] = fn()
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"
    return books, errors
