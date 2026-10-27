"""Closed daily-bar descriptive tags; never an eligibility filter."""
from .strategies.indicators import closed_bars, ema, wilder_atr
from .types import D


def regime_tags(snapshot):
    bars = closed_bars(snapshot, '1d', 104)
    if bars is None:
        return {'trend_regime': 'UNKNOWN', 'vol_regime': 'UNKNOWN'}
    closes = tuple(b.close for b in bars)
    slope = ema(closes, 50)-ema(closes[:-1], 50)
    ratios = [wilder_atr(bars[:i],14)/bars[i-1].close for i in range(len(bars)-89,len(bars)+1)]
    percentile = D(100)*D(sum(x <= ratios[-1] for x in ratios))/D(90)
    return {'trend_regime': 'UP' if slope > 0 else 'DOWN' if slope < 0 else 'FLAT',
            'vol_regime': str(percentile.quantize(D('.01')))}
