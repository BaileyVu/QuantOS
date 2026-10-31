"""Simulated USDT-margined perpetual positions with a leverage decision (isolated margin per coin sleeve).

Leverage is picked at entry from signal strength and capped by volatility, inside [MIN_LEV, MAX_LEV]:
  conf = (|composite| - ENTER) / (1 - ENTER)        0 at the entry threshold, 1 at a perfect score
  lev  = MIN_LEV + (MAX_LEV - MIN_LEV) * conf
  cap  = 1 / (LIQ_SIGMAS * daily_vol + MMR)        keeps liquidation >= LIQ_SIGMAS daily moves away
  lev  = max(MIN_LEV, min(lev, cap))               the 3x floor wins over the cap (owner's rule)
Margin = sleeve equity * vol size, notional = margin * lev. A stop closes the trade when it has lost STOP_FRAC of
its margin; if the bar gaps through the liquidation price the whole margin is lost instead.
Used by both backtest.py and paper.py so the two cannot drift apart. Nothing here talks to an exchange.
"""
import math
from strategy import ENTER, EXIT

MIN_LEV, MAX_LEV = 3, 20
MMR = 0.005               # maintenance margin rate (Binance tier 1 for BTC/ETH/SOL is 0.4-0.5%)
LIQ_SIGMAS = 3.0
STOP_FRAC = 0.5           # stop out at -50% of margin, well before liquidation
FEE, SLIP = 0.0005, 0.0005  # futures taker fee and slippage, per side on notional
FUNDING_8H = 0.0001       # typical +0.01% per 8h: longs pay, shorts receive
SHORTS = True             # perps can short; enter short when composite < -ENTER


def choose_leverage(score, vol_ann):
    conf = min(1.0, max(0.0, (abs(score) - ENTER) / (1 - ENTER)))
    lev = MIN_LEV + (MAX_LEV - MIN_LEV) * conf
    if vol_ann and vol_ann == vol_ann and vol_ann > 0:
        lev = min(lev, 1 / (LIQ_SIGMAS * vol_ann / math.sqrt(365) + MMR))
    return int(max(MIN_LEV, min(MAX_LEV, lev)))


def want_side(score, side, shorts=SHORTS):
    """Hysteresis: open at +-ENTER, close when the score crosses EXIT (0) against the position."""
    if side > 0:
        return 1 if score >= EXIT else 0
    if side < 0:
        return -1 if score <= -EXIT else 0
    if score > ENTER:
        return 1
    if shorts and score < -ENTER:
        return -1
    return 0


def open_pos(side, price, margin, lev):
    """Returns (position dict, fee). Fill includes slippage."""
    entry = price * (1 + side * SLIP)
    notional = margin * lev
    p = {"side": side, "entry": entry, "margin": margin, "lev": lev, "notional": notional,
         "stop": entry * (1 - side * STOP_FRAC / lev), "liq": entry * (1 - side * (1 / lev - MMR))}
    return p, notional * FEE


def upnl(p, price):
    return p["side"] * p["notional"] * (price / p["entry"] - 1)


def close_pos(p, price, reason="signal"):
    """Returns (realised pnl incl. exit fee, exit price, reason). Liquidation loses exactly the margin."""
    if reason == "liquidated":
        return -p["margin"], p["liq"], reason
    px = price * (1 - p["side"] * SLIP)
    pnl = upnl(p, px) - p["notional"] * px / p["entry"] * FEE
    return max(pnl, -p["margin"]), px, reason


def check_bar(p, o, h, l):
    """Stop / liquidation inside one candle. Returns (pnl, exit price, reason) or None if the position survives."""
    s = p["side"]
    worst = l if s > 0 else h
    if s * (worst - p["stop"]) > 0:  # stop not touched
        return None
    px = min(o, p["stop"]) if s > 0 else max(o, p["stop"])  # a gap through the stop fills at the open
    if s * (px - p["liq"]) <= 0:
        return close_pos(p, px, "liquidated")
    return close_pos(p, px, "stop")


def funding(p, price):
    """Funding paid (positive = cost) at one 8-hour settlement."""
    return p["side"] * p["notional"] * price / p["entry"] * FUNDING_8H
