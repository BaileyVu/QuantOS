"""Public information feeds. No credentials, signing, account or order methods."""
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import json
import time

from websockets.asyncio.client import connect
from quantos.domain.market_data.intelligence import IntelligenceEvent


COINBASE_URL = "wss://ws-feed.exchange.coinbase.com"
LIQUIDATION_URL = "wss://fstream.binance.com/market/ws/"


def coinbase_ticker(row: dict, received_at: datetime, received_ns: int) -> IntelligenceEvent:
    if row["type"] != "ticker" or row["product_id"] != "BTC-USD":
        raise ValueError("unexpected public leader instrument")
    return IntelligenceEvent(
        "coinbase", "BTC-USD", "BTC/USD:spot", "leader_quote",
        datetime.fromisoformat(row["time"].replace("Z", "+00:00")),
        received_at, received_ns, received_ns, int(row["sequence"]),
        COINBASE_URL + "#ticker", "monotone_snapshot", True,
        Decimal(row["price"]), Decimal(row["best_bid"]), Decimal(row["best_ask"]),
    )


def liquidation_event(row: dict, symbol: str, received_at: datetime, received_ns: int) -> IntelligenceEvent:
    order = row["o"]
    if row["e"] != "forceOrder" or order["s"] != symbol or order["S"] not in {"BUY", "SELL"}:
        raise ValueError("unexpected public liquidation instrument")
    return IntelligenceEvent(
        "binance", symbol, f"BTC/{symbol[3:]}:perpetual", "liquidation",
        datetime.fromtimestamp(int(order["T"]) / 1000, timezone.utc),
        received_at, received_ns, received_ns, int(row["E"]),
        LIQUIDATION_URL + symbol.lower() + "@forceOrder", "sampled_not_complete", True,
        Decimal(order["ap"]), quantity=Decimal(order["z"]),
        direction=1 if order["S"] == "BUY" else -1,
    )


class PublicIntelligenceStream:
    def __init__(self, provider: str, symbol: str, lifecycle=None):
        if provider not in {"coinbase", "liquidation"}:
            raise ValueError("unsupported public provider")
        self.provider, self.symbol = provider, symbol
        self.lifecycle = lifecycle or (lambda event: None)

    async def events(self):
        url = COINBASE_URL if self.provider == "coinbase" else LIQUIDATION_URL + self.symbol.lower() + "@forceOrder"
        async with connect(url, open_timeout=10, close_timeout=2, ping_interval=20,
                           ping_timeout=20, max_queue=256) as socket:
            self.lifecycle({"event": "public_source_connected", "provider": self.provider, "url": url})
            if self.provider == "coinbase":
                await socket.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker", "heartbeat"]}))
            while True:
                # Liquidation silence is not evidence of zero liquidation activity.
                raw = await asyncio.wait_for(socket.recv(), timeout=60)
                wall, ns = datetime.now(timezone.utc), time.monotonic_ns()
                row = json.loads(raw)
                if self.provider == "coinbase":
                    if row.get("type") == "error":
                        raise RuntimeError("public provider error: " + str(row.get("message")))
                    if row.get("type") != "ticker":
                        continue
                    yield coinbase_ticker(row, wall, ns)
                else:
                    yield liquidation_event(row, self.symbol, wall, ns)
