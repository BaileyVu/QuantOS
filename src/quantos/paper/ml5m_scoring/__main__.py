"""Order-free alt-ML scorer. Run with python -m quantos.paper.ml5m_scoring."""
import argparse
import json
import os
import pickle
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd

from ..strategies.ml5m_alts import USDC_TWINS
from . import feats, gbm

BAR = 300_000
LIVE_KLINE_LIMIT = 700
DAY = 86_400_000
ROLL_DAYS = 30
PUBLIC_HOST = "https://fapi.binance.com"


class ModelLoader(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "gbm" and name in ("Binner", "GBM"):
            return getattr(gbm, name)
        return super().find_class(module, name)


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(value, file, allow_nan=False)
            file.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def public_get(endpoint, **params):
    url = f"{PUBLIC_HOST}/fapi/v1/{endpoint}?{urlencode(params)}"
    for attempt in range(5):
        try:
            with urlopen(url, timeout=20) as response:
                return json.load(response)
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))


def active_universe(model):
    eligible = set(model["coins"])
    return [row["symbol"] for row in public_get("exchangeInfo")["symbols"]
            if row["symbol"] in eligible and row["status"] == "TRADING"]


def candles(symbol, now, limit, start=None):
    rows = []
    end = now
    while True:
        page = public_get("klines", symbol=symbol, interval="5m", limit=limit, endTime=end)
        rows[:0] = page
        if start is None or len(page) < limit or page[0][0] <= start:
            break
        end = page[0][0] - 1
        time.sleep(1.5)
    array = np.array([row[:11] for row in rows if row[6] < now], dtype=float)
    if not len(array):
        raise ValueError(f"no closed bars for {symbol}")
    return array[:, 0].astype(np.int64), array


def set_btc(times, bars):
    feats.BLC[0] = pd.Series(np.log(bars[:, 4]), index=times)


def score(model, symbol, times, bars, *, funding_limit, selection):
    funding = public_get("fundingRate", symbol=symbol, limit=funding_limit)
    if not funding:
        raise ValueError(f"no funding history for {symbol}")
    rates = pd.Series([float(row["fundingRate"]) for row in funding],
                      index=[int(row["fundingTime"]) for row in funding]).sort_index()
    features = feats.feats(times, bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4],
                           bars[:, 5], bars[:, 8], bars[:, 9], rates)
    valid = selection & np.isfinite(features).all(axis=1)
    if not valid.any():
        return {}
    probabilities = model["model"].predict_proba(model["binner"].transform(features[valid]))
    return {int(t): round(float(value), 4) for t, value in zip(times[valid], probabilities)}


def threshold(history, today):
    values = [value for day in range(today - ROLL_DAYS, today)
              for rows in history.get(str(day), {}).values() for value in rows.values()]
    return float(np.quantile(values, 0.99)) if len(values) > 2000 else None


def save_history(path, history, today):
    trimmed = {day: rows for day, rows in history.items() if int(day) >= today - ROLL_DAYS - 1}
    atomic_json(path, trimmed)


def add_scores(history, symbol, scores):
    for opened, value in scores.items():
        history.setdefault(str(opened // DAY), {}).setdefault(symbol, {})[str(opened)] = value


def replay(model, history, path, now):
    today = now // DAY
    missing = {day for day in range(today - ROLL_DAYS, today)
               if sum(map(len, history.get(str(day), {}).values())) < 0.9 * 96 * len(model["coins"])}
    if not missing:
        return
    start = (min(missing) - 3) * DAY
    set_btc(*candles("BTCUSDT", now, 1000, start))
    for symbol in model["coins"]:
        time.sleep(1.5)
        times, bars = candles(symbol, now, 1000, start)
        time.sleep(1.5)
        selected = ((times // BAR) % 3 == 0) & np.isin(times // DAY, list(missing))
        add_scores(history, symbol, score(model, symbol, times, bars,
                                          funding_limit=1000, selection=selected))
    save_history(path, history, today)


def step(model, history, state, now):
    opened = (now // BAR - 1) * BAR
    if opened // BAR % 3:
        return
    btc_times, btc_bars = candles("BTCUSDT", now, LIVE_KLINE_LIMIT)
    if btc_times[-1] != opened:
        raise ValueError("BTC decision candle unavailable")
    set_btc(btc_times, btc_bars)
    scores = {}
    for symbol in model["coins"]:
        times, bars = candles(symbol, now, LIVE_KLINE_LIMIT)
        if times[-1] != opened:
            raise ValueError(f"decision candle unavailable for {symbol}")
        values = score(model, symbol, times, bars, funding_limit=5, selection=times == opened)
        if opened in values:
            scores[symbol] = values[opened]
    today = now // DAY
    thr = threshold(history, today)
    for symbol, value in scores.items():
        add_scores(history, symbol, {opened: value})
    save_history(state / "score_hist.json", history, today)
    signals = [{"symbol": symbol[:-4] + "USDC", "side": "LONG", "score": value}
               for symbol, value in sorted(scores.items(), key=lambda item: -item[1])
               if symbol[:-4] + "USDC" in USDC_TWINS and thr is not None and value >= thr]
    summary = bar_summary(scores, thr)
    atomic_json(state / "signals.json", {"bar_close_ms": opened + BAR - 1, "thr": thr, "signals": signals,
                                         "max_score": summary["max_score"], "max_symbol": summary["max_symbol"]})
    print(f"bar_close_ms={opened + BAR - 1} scored={len(scores)} signals={len(signals)} {summary['line']}", flush=True)


def bar_summary(scores, thr):
    """Per-bar max score among tradeable USDC twins (and over all scored coins) vs the threshold. Log only."""
    def best(items):
        return max(items, key=lambda item: item[1], default=(None, None))
    symbol, top = best((s[:-4] + "USDC", v) for s, v in scores.items() if s[:-4] + "USDC" in USDC_TWINS)
    any_symbol, any_top = best(scores.items())
    fmt = lambda v: "na" if v is None else f"{v:.3f}"
    gap = "na" if top is None or thr is None else f"{top - thr:+.3f}"
    return {"max_score": top, "max_symbol": symbol,
            "line": f"thr={fmt(thr)} max={fmt(top)}@{symbol} gap={gap} max_all={fmt(any_top)}@{any_symbol}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path, help="Trusted existing alt-ML model pickle")
    parser.add_argument("--state", required=True, type=Path, help="Separate scorer state directory")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    with args.model.open("rb") as source:
        model = ModelLoader(source).load()
    model["coins"] = active_universe(model)
    if not model["coins"]:
        raise ValueError("no trading contracts in the model universe")
    path = args.state / "score_hist.json"
    history = json.loads(path.read_text()) if path.exists() else {}
    now_ms = time.time_ns() // 1_000_000
    replay(model, history, path, now_ms)
    if args.once:
        # Prime with the latest decision whose 15s exchange publication grace has passed.
        now_ms = time.time_ns() // 1_000_000
        opened = (now_ms - BAR - 15_000) // (3 * BAR) * (3 * BAR)
        step(model, history, args.state, opened + BAR + 15_000)
        return
    while True:
        now = time.time()
        time.sleep(max(0, (now // 300 + 1) * 300 + 15 - now))
        step(model, history, args.state, time.time_ns() // 1_000_000)


if __name__ == "__main__":
    main()
