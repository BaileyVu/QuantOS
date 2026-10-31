"""Technical signals on hourly candles. Every score uses data up to and including bar t's close only.

Parameters are fixed here BEFORE backtesting (in-sample 2021-2023, out-of-sample 2024 onward). Do not tune on OOS.
Each component returns a score in [-1, 1]: +1 = bullish, -1 = bearish.
"""
import numpy as np
import pandas as pd

MOM_LOOKBACKS = (24, 72, 168)      # 1 day, 3 days, 1 week
EMA_FAST, EMA_SLOW = 20, 100
DONCHIAN = 48                      # 2-day breakout channel
RSI_N, RSI_LO, RSI_HI = 14, 25, 75
VOL_N = 72                         # realised vol window (hours)
TARGET_VOL = 0.40                  # annualised; position size = min(1, TARGET_VOL / vol)
VOL_SPIKE_PCT = 0.95               # vol above its 30-day 95th percentile halves the size
HOURS_PER_YEAR = 24 * 365


def momentum(df):
    c = df["close"]
    parts = [np.sign(c / c.shift(n) - 1) for n in MOM_LOOKBACKS]
    parts.append(np.sign(c.ewm(span=EMA_FAST).mean() - c.ewm(span=EMA_SLOW).mean()))
    return sum(parts) / len(parts)


def rsi(c, n=RSI_N):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def pattern(df):
    """Donchian breakout state (+1 after an upside break until a downside break) plus RSI extremes and engulfing candles."""
    c, o = df["close"], df["open"]
    hi = df["high"].rolling(DONCHIAN).max().shift(1)
    lo = df["low"].rolling(DONCHIAN).min().shift(1)
    brk = pd.Series(np.where(c > hi, 1.0, np.where(c < lo, -1.0, np.nan)), index=df.index).ffill().fillna(0)
    r = rsi(c)
    rsi_sig = pd.Series(np.where(r < RSI_LO, 1.0, np.where(r > RSI_HI, -1.0, 0.0)), index=df.index)
    body, pbody = c - o, (c - o).shift(1)
    bull = (pbody < 0) & (body > 0) & (c > o.shift(1)) & (o < c.shift(1))
    bear = (pbody > 0) & (body < 0) & (c < o.shift(1)) & (o > c.shift(1))
    engulf = bull.astype(float) - bear.astype(float)
    return (brk + 0.5 * rsi_sig + 0.5 * engulf).clip(-1, 1)


def realised_vol(df):
    return np.log(df["close"]).diff().rolling(VOL_N).std() * np.sqrt(HOURS_PER_YEAR)


def vol_size(df):
    """Position size multiplier in [0, 1] from volatility targeting, halved during vol spikes."""
    v = realised_vol(df)
    size = (TARGET_VOL / v).clip(upper=1.0)
    spike = v > v.rolling(24 * 30).quantile(VOL_SPIKE_PCT)
    return (size * np.where(spike, 0.5, 1.0)).fillna(0)


def fear_greed_signal(fng, index):
    """Contrarian at extremes: extreme fear (<20) = +1, extreme greed (>80) = -1. Daily value applies from the next day."""
    if fng is None:
        return pd.Series(0.0, index=index)
    s = pd.Series(np.where(fng < 20, 1.0, np.where(fng > 80, -1.0, 0.0)), index=fng.index)
    s.index = s.index + pd.Timedelta(days=1)  # published for day d, usable from d+1 00:00 UTC
    return s.reindex(index, method="ffill").fillna(0)
