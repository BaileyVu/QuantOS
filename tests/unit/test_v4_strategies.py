from dataclasses import replace
from decimal import Decimal as D
import unittest

from quantos.paper.types import Candle, DelistEvent, Instrument, Quote, Snapshot
from quantos.paper.strategies.tsmom_4h import Tsmom4hStrategy
from quantos.paper.strategies.turtle_55 import Turtle55Strategy
from quantos.paper.strategies.vol_squeeze_breakout import VolSqueezeBreakoutStrategy
from quantos.paper.strategies.delist_short_e17 import DelistShortE17Strategy

PERIODS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def candles(timeframe, closes, at_ms):
    period = PERIODS[timeframe]
    last_close = ((at_ms + 1) // period) * period - 1
    out = []
    for i, value in enumerate(closes):
        value = D(value)
        op = D(closes[i - 1]) if i else value
        op_ms = last_close - (len(closes) - 1 - i) * period - period + 1
        out.append(Candle(op_ms, op_ms + period - 1, op, max(op, value) + D("0.5"),
                          min(op, value) - D("0.5"), value, D("10")))
    return tuple(out)


def snapshot(timeframe, closes, at_ms=1_800_000_000_000, extra=None):
    ins = Instrument("BTCUSDC", "USDC", "TRADING", D("0.1"), D("0.001"), D("0.001"),
                     D("100"), D("5"), base_asset="BTC")
    quote = Quote(ins.symbol, at_ms, at_ms, D("99"), D("101"), D("2"), D("2"), D("100"))
    kw = {"candles_1m": (), "candles_5m": (), f"candles_{timeframe}": candles(timeframe, closes, at_ms)}
    if extra:
        kw.update(extra)
    return Snapshot(at_ms, ins, quote, **kw)


class V4StrategyTests(unittest.TestCase):
    def test_tsmom_signal_and_alignment(self):
        snap = snapshot("4h", [str(100 + i * .1) for i in range(181)])
        sig = Tsmom4hStrategy().evaluate(snap)
        self.assertEqual(sig.strategy_id, "tsmom_4h")
        self.assertEqual((sig.side, sig.entry_type, sig.take_profit), ("LONG", "MARKET", None))
        self.assertLess(sig.stop_loss, sig.entry_price)
        self.assertEqual(sig.maximum_hold_seconds, 259200)
        repeated = Tsmom4hStrategy().evaluate(replace(snap, at_ms=snap.at_ms + 60_000))
        self.assertEqual(sig.signal_id, repeated.signal_id)
        self.assertEqual(sig.expires_ms, repeated.expires_ms)
        self.assertIsNone(Tsmom4hStrategy().evaluate(snapshot("4h", ["100"] * 181)))
        self.assertIsNone(Tsmom4hStrategy().evaluate(replace(snap, at_ms=snap.at_ms + 120_000)))

    def test_turtle_breakout_signal_and_no_breakout(self):
        values = ["100"] * 55 + ["110"]
        sig = Turtle55Strategy().evaluate(snapshot("4h", values))
        self.assertEqual((sig.side, sig.entry_type, sig.take_profit), ("LONG", "STOP", None))
        self.assertEqual(sig.entry_price, D("100.6"))
        self.assertLess(sig.stop_loss, sig.entry_price)
        short = Turtle55Strategy().evaluate(snapshot("4h", ["100"] * 55 + ["90"]))
        self.assertEqual((short.side, short.entry_price), ("SHORT", D("99.4")))
        self.assertIsNone(Turtle55Strategy().evaluate(snapshot("4h", ["100"] * 56)))

    def test_squeeze_signal_range_stop_and_two_r_target(self):
        values = ["100"] * 20 + ["110"]
        sig = VolSqueezeBreakoutStrategy().evaluate(snapshot("1h", values))
        self.assertIsNotNone(sig)
        self.assertEqual((sig.side, sig.entry_type), ("LONG", "STOP"))
        self.assertLess(sig.stop_loss, sig.entry_price)
        self.assertEqual(sig.take_profit - sig.entry_price, 2 * (sig.entry_price - sig.stop_loss))
        self.assertIsNone(VolSqueezeBreakoutStrategy().evaluate(snapshot("1h", ["100"] * 21)))

    def test_delist_requires_verified_url_usdc_recent_trade_and_daily_volume(self):
        at = 1_800_000_010_000
        ev = DelistEvent("XUSDC", at - 30_000, at - 29_000, "abc", "Binance Will Delist X on Friday",
                         "https://www.binance.com/en/support/announcement/abc")
        extra = {"delist_events": (ev,), "recent_trade_ms": at - 5_000,
                 "recent_trade_volume": D("1"), "candles_1d": candles("1d", ["1"], at)}
        # Source instrument symbol is changed to a matching USDC perp.
        base = snapshot("1h", ["100"] * 21, at, extra)
        ins = replace(base.instrument, symbol="XUSDC", base_asset="X")
        quote = replace(base.quote, symbol="XUSDC")
        base = replace(base, instrument=ins, quote=quote)
        sig = DelistShortE17Strategy().evaluate(base)
        self.assertIsNotNone(sig)
        self.assertEqual((sig.side, sig.entry_type, sig.take_profit), ("SHORT", "MARKET", None))
        self.assertEqual(sig.expires_ms, ev.release_ms + 300_000)
        self.assertGreater(sig.stop_loss, sig.entry_price)
        late = replace(ev, release_ms=at - 300_001, observed_ms=at - 300_000)
        self.assertIsNone(DelistShortE17Strategy().evaluate(replace(base, delist_events=(late,))))
        bad_url = replace(ev, source_url="https://evil.example/en/support/announcement/abc")
        self.assertIsNone(DelistShortE17Strategy().evaluate(replace(base, delist_events=(bad_url,))))
        self.assertIsNone(DelistShortE17Strategy().evaluate(replace(base, delist_events=(replace(ev, symbol="YUSDC"),))))
        for suffix in ("extra", "?x=1", "#frag"):
            invalid = replace(ev, source_url="https://www.binance.com/en/support/announcement/abc" + suffix)
            self.assertIsNone(DelistShortE17Strategy().evaluate(replace(base, delist_events=(invalid,))))
        self.assertIsNone(DelistShortE17Strategy().evaluate(replace(base, recent_trade_volume=D(0))))
        self.assertIsNone(DelistShortE17Strategy().evaluate(replace(base, candles_1d=(replace(extra["candles_1d"][0], volume=D(0)),))))

    def test_future_and_gapped_bars_fail_closed(self):
        base = snapshot("4h", [str(100 + i * .1) for i in range(181)])
        strategy = Tsmom4hStrategy()
        future = replace(base.candles_4h[-1], close_ms=base.at_ms + 1)
        self.assertIsNone(strategy.evaluate(replace(base, candles_4h=base.candles_4h[:-1] + (future,))))
        gap = replace(base.candles_4h[-2], open_ms=base.candles_4h[-2].open_ms + 1)
        self.assertIsNone(strategy.evaluate(replace(base, candles_4h=base.candles_4h[:-2] + (gap, base.candles_4h[-1]))))


if __name__ == "__main__":
    unittest.main()
