"""Preregistered strategies. Parameters are fixed here BEFORE looking at results; do not tune them on the test period.

Each strategy maps a dict of close-price histories (aligned dates) to target weights for the NEXT day.
Weights are long-only, sum <= 1, the remainder sits in USDT. Spot only, no leverage, no shorting.
"""

UNIVERSE = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT"]


def sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def btc_trend(closes, n=100):
    """A: hold BTC when BTC close > SMA(n), else cash."""
    c = closes.get("BTCUSDT", [])
    m = sma(c, n)
    return {"BTCUSDT": 1.0} if m and c[-1] > m else {}


def basket_trend(closes, n=100):
    """B: each coin gets 1/len(UNIVERSE) when its close > its SMA(n), else that slice is cash."""
    w = {}
    for s in UNIVERSE:
        c = closes.get(s, [])
        m = sma(c, n)
        if m and c[-1] > m:
            w[s] = 1.0 / len(UNIVERSE)
    return w


def xs_momentum(closes, lookback=28, top=3, regime_n=100):
    """C: hold the top-k coins by `lookback`-day return, equal weight, only while BTC > SMA(regime_n)."""
    if not btc_trend(closes, regime_n):
        return {}
    rets = {s: closes[s][-1] / closes[s][-1 - lookback] - 1 for s in UNIVERSE
            if len(closes.get(s, [])) > lookback and closes[s][-1] / closes[s][-1 - lookback] - 1 > 0}
    best = sorted(rets, key=rets.get, reverse=True)[:top]
    return {s: 1.0 / top for s in best}


def hold_btc(closes):
    return {"BTCUSDT": 1.0} if closes.get("BTCUSDT") else {}


def hold_basket(closes):
    live = [s for s in UNIVERSE if closes.get(s)]
    return {s: 1.0 / len(UNIVERSE) for s in live}


STRATEGIES = {"A_btc_trend": btc_trend, "B_basket_trend": basket_trend, "C_xs_momentum": xs_momentum}
BENCHMARKS = {"hold_btc": hold_btc, "hold_basket": hold_basket}
# Rebalance schedule: C trades weekly (Mondays) to keep turnover down; A and B check daily.
WEEKLY = {"C_xs_momentum"}
