"""Deterministic closed-candle lifecycle fixtures for the six library strategies."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

from ..types import Candle, DelistEvent, Instrument, Quote, Snapshot
from . import library

_PERIODS = {"5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _completed_at(now_ms: int, timeframe: str) -> int:
    period = _PERIODS[timeframe]
    return ((now_ms + 1) // period) * period - 1


def _candles(timeframe: str, closes, at_ms: int, high_pad=D("0.2"), low_pad=D("0.2"), volume=D("100")):
    period = _PERIODS[timeframe]
    last_close = _completed_at(at_ms, timeframe)
    out = []
    for i, close in enumerate(closes):
        close = D(str(close))
        op = D(str(closes[i - 1])) if i else close
        open_ms = last_close - (len(closes) - 1 - i) * period - period + 1
        out.append(Candle(open_ms, open_ms + period - 1, op,
                          max(op, close) + high_pad, min(op, close) - low_pad,
                          close, volume))
    return tuple(out)


def _instrument(symbol="BTCUSDC", base="BTC"):
    return Instrument(symbol, "USDC", "TRADING", D("0.1"), D("0.001"), D("0.001"),
                      D("100000"), D("5"), D("0.0001"), D("1000000"),
                      D("0.001"), D("0.001"), D("100000"), D("1.05"), D("0.95"), base)


def _quote(symbol: str, now_ms: int, mid: D):
    # Four basis points total spread, fresh quote and mark at the midpoint.
    half = max(mid * D("0.0002"), D("0.0001"))
    return Quote(symbol, now_ms, now_ms, mid - half, mid + half, D("100"), D("100"), mid, now_ms)


def _snapshot(strategy, now_ms: int) -> Snapshot:
    timeframe = {"ema_atr_limit_pullback": "5m", "donchian_atr_stop_breakout": "5m",
                 "tsmom_4h": "4h", "turtle_55": "4h", "vol_squeeze_breakout": "1h",
                 "delist_short_e17": "event"}[strategy.strategy_id]
    aligned = (now_ms // 60_000 * 60_000 + 10_000) if timeframe == "event" else _completed_at(now_ms, timeframe)
    ins = _instrument()
    quote = _quote(ins.symbol, aligned, D("100"))
    kwargs = {"candles_1m": (), "candles_5m": (), "candles_1h": (), "candles_4h": (), "candles_1d": ()}
    if timeframe == "5m":
        if strategy.strategy_id == "ema_atr_limit_pullback":
            closes = [D("100") + D(i) * D("0.3") for i in range(80)]
            kwargs["candles_5m"] = _candles("5m", closes, aligned, D("3"), D("3"))
        else:
            closes = [D("100")] * 64 + [D("130")]
            kwargs["candles_5m"] = _candles("5m", closes, aligned, D("10"), D("10"))
    elif timeframe == "4h":
        if strategy.strategy_id == "tsmom_4h":
            closes = [D("100") + D(i) * D("0.1") for i in range(181)]
            kwargs["candles_4h"] = _candles("4h", closes, aligned, D("0.5"), D("0.5"))
        else:
            closes = [D("100")] * 55 + [D("110")]
            kwargs["candles_4h"] = _candles("4h", closes, aligned, D("2"), D("2"))
    elif timeframe == "1h":
        closes = [D("100")] * 20 + [D("110")]
        kwargs["candles_1h"] = _candles("1h", closes, aligned, D("2"), D("0.2"))
    else:
        # E17 uses a previous completed UTC-day candle with positive volume.
        kwargs["candles_1d"] = _candles("1d", [D("100")], aligned, D("1"), D("1"), D("100"))
        code = "fixture-article-17"
        release = aligned - 30_000
        kwargs["delist_events"] = (DelistEvent(
            "BTCUSDC", release, release + 1_000, code, "Binance Will Delist BTC on fixture date",
            f"https://www.binance.com/en/support/announcement/{code}"),)
        kwargs["recent_trade_ms"] = aligned - 1_000
        kwargs["recent_trade_volume"] = D("1")
    initial = Snapshot(aligned, ins, quote, **kwargs)
    signal = strategy.evaluate(initial)
    if signal is None:
        raise AssertionError(f"fixture did not produce a signal for {strategy.strategy_id}")
    if signal.entry_type == "STOP":
        # Keep the trigger unfilled: long ask below STOP; short bid above STOP.
        mid = signal.entry_price - D("0.5") if signal.side == "LONG" else signal.entry_price + D("0.5")
        quote = _quote(ins.symbol, aligned, mid)
    elif signal.entry_type == "LIMIT":
        # A buy limit rests at or below the bid; keep quote close enough for filters.
        mid = signal.entry_price + max(signal.entry_price * D("0.0005"), D("0.1")) if signal.side == "LONG" else signal.entry_price - D("0.1")
        quote = _quote(ins.symbol, aligned, mid)
    elif signal.entry_type == "MARKET" and strategy.strategy_id != "delist_short_e17":
        quote = _quote(ins.symbol, aligned, signal.entry_price)
    elif strategy.strategy_id == "delist_short_e17":
        quote = _quote(ins.symbol, aligned, D("100"))
    return strategy, replace(initial, quote=quote)


def lifecycle_snapshots(now_ms: int) -> list[tuple[object, Snapshot]]:
    """Return (strategy, snapshot) pairs for all six registered strategy instances."""
    return [_snapshot(strategy, now_ms) for strategy in library()]
