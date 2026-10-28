"""Fragile-source-only Binance delist short; signal generation only."""
import re
from decimal import Decimal as D
from urllib.parse import urlparse

from ..types import Snapshot
from .indicators import closed_bars, make_signal


class DelistShortE17Strategy:
    strategy_id = "delist_short_e17"
    version = "1.0.0"

    def evaluate(self, snapshot: Snapshot):
        ins = snapshot.instrument
        if ins.status != "TRADING" or ins.margin_asset != "USDC":
            return None
        daily = closed_bars(snapshot, "1d", 1)
        if daily is None or daily[-1].volume <= 0:
            return None
        minute_start = snapshot.at_ms // 60_000 * 60_000
        if (snapshot.recent_trade_ms is None or snapshot.recent_trade_ms < minute_start
                or snapshot.recent_trade_ms > snapshot.at_ms
                or not snapshot.recent_trade_volume.is_finite() or snapshot.recent_trade_volume <= 0):
            return None
        for event in snapshot.delist_events:
            age = snapshot.at_ms - event.release_ms
            if age < 0 or age > 300_000 or event.observed_ms < event.release_ms or event.observed_ms > snapshot.at_ms:
                continue
            if event.symbol != ins.symbol:
                continue
            parsed = urlparse(event.source_url)
            expected_path = f"/en/support/announcement/{event.article_id}"
            if (parsed.scheme != "https" or parsed.netloc != "www.binance.com"
                    or parsed.query or parsed.fragment or not event.article_id
                    or parsed.path not in (expected_path, expected_path + "/")):
                continue
            if not re.match(r"^Binance Will Delist", event.title):
                continue
            body = event.title.split("Delist", 1)[1].split(" on ")[0]
            tickers = [part.strip() for part in re.split(r",|\band\b", body)
                       if part.strip() and part.strip().isupper()]
            ticker = next((name for name in tickers
                           if ins.symbol in (name + "USDC", "1000" + name + "USDC")), None)
            if ticker is None:
                continue
            entry = snapshot.quote.mark
            if not entry.is_finite() or entry <= 0:
                continue
            return make_signal(snapshot, self.strategy_id, self.version,
                               f"{event.release_ms}:{event.article_id}:{ticker}", "SHORT", "MARKET",
                               entry, entry * D("1.25"), None, 14400, event.release_ms + 300_000,
                               "verified-format Binance catalog delist event",
                               (("release_ms", D(event.release_ms)),))
        return None
