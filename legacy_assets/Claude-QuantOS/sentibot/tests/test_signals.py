import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signals, sentiment
from strategy import positions, composite


def fake(n=2000, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c, "volume": 1.0}, index=idx)


def test_no_lookahead():
    df = fake()
    cut = df.iloc[:1500]
    for fn in (signals.momentum, signals.pattern, signals.vol_size):
        a, b = fn(cut), fn(df).iloc[:1500]
        assert np.allclose(a.values, b.values, equal_nan=True), fn.__name__


def test_scores_bounded_and_positions_long_only():
    df = fake()
    sc = {"momentum": signals.momentum(df), "pattern": signals.pattern(df)}
    comp = composite(sc, {"momentum": 0.6, "pattern": 0.4})
    assert comp.abs().max() <= 1 + 1e-9
    pos = positions(comp, signals.vol_size(df))
    assert pos.min() >= 0 and pos.max() <= 1


def test_polarity_direction():
    assert sentiment.polarity("Bitcoin surges as ETF inflows soar") > 0.3
    assert sentiment.polarity("Exchange hacked, SOL plunges") < -0.3
