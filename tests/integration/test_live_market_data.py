"""Offline public-adapter integration with canonical contracts and logging."""

from contextlib import aclosing
import json
import unittest

from quantos.domain.market_data import MarketEvent
from quantos.infrastructure.binance import (
    BinanceSpotHistoricalKlineAdapter,
    BinanceSpotLiveMarketDataAdapter,
)
from quantos.infrastructure.logging.structured import JsonFormatter
from tests.unit.test_binance_live_klines import combined_kline, encode
from tests.unit.test_binance_live_stream import FakeConnector, FakeSleep, message


class LiveMarketDataIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_and_historical_paths_use_equal_canonical_candles(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                payload = combined_kline(symbol)
                kline = payload["data"]["k"]
                # Historical REST has millisecond precision; retain that same
                # provider-supplied close instant in this live comparison.
                kline["T"] -= 999
                row = [kline["t"] // 1000, kline["o"], kline["h"], kline["l"],
                       kline["c"], kline["v"], kline["T"] // 1000, kline["q"],
                       kline["n"], "0", "0", "0"]
                historical = BinanceSpotHistoricalKlineAdapter(
                    http_get=lambda url, timeout: json.dumps([row]).encode()
                ).fetch_klines(symbol=symbol, interval="1m")
                adapter = BinanceSpotLiveMarketDataAdapter(
                    symbols=[symbol], connector=FakeConnector([encode(payload)]), sleep=FakeSleep()
                )
                async with aclosing(adapter):
                    event = await anext(adapter)
                self.assertIs(type(event), MarketEvent)
                self.assertEqual(event.candle, historical[0])

    async def test_public_combined_iterator_logs_lifecycle_and_completed_events(self) -> None:
        connector = FakeConnector(
            [message(closed=False), message(), OSError("disconnected")],
            [message(), message("ETHUSDT"), message(minute=1)],
        )
        adapter = BinanceSpotLiveMarketDataAdapter(
            symbols=["BTCUSDT", "ETHUSDT"], connector=connector, sleep=FakeSleep()
        )
        with self.assertLogs("quantos.binance.live", level="DEBUG") as captured:
            async with aclosing(adapter):
                events = [await anext(adapter) for _ in range(3)]
        self.assertEqual([event.candle.symbol for event in events], ["BTCUSDT", "ETHUSDT", "BTCUSDT"])
        records = [json.loads(JsonFormatter().format(record)) for record in captured.records]
        names = [record["event"] for record in records]
        self.assertEqual(names[0], "live_market_data_started")
        self.assertEqual(names[-1], "live_market_data_stopped")
        self.assertEqual(names.count("live_market_data_completed"), 3)
        self.assertEqual(names.count("live_market_data_duplicate"), 1)
        retry = next(record for record in records if record["event"] == "live_market_data_reconnect")
        self.assertEqual(retry["context"], {"delay_seconds": 1.0, "reason": "OSError"})
        self.assertTrue(all(connection.closed for connection in connector.connections))
        self.assertEqual(connector.peak_active, 1)
