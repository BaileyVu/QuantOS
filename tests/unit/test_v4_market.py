import json
import unittest
from decimal import Decimal
from urllib.parse import urlparse

from quantos.paper.announcements import BinanceAnnouncements, CMS_URL
from quantos.paper.market import BASE, PublicMarket, RateLimited
from quantos.paper.types import Instrument


def instrument(symbol="BTCUSDC", *, status="TRADING", base=None):
    return {
        "symbol": symbol, "baseAsset": base or symbol[:-4],
        "quoteAsset": "USDC", "marginAsset": "USDC",
        "contractType": "PERPETUAL", "status": status,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.1", "minPrice": "0.1", "maxPrice": "100000"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
            {"filterType": "PERCENT_PRICE", "multiplierUp": "1.05", "multiplierDown": "0.95"},
        ],
    }


class FixtureTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        self.assert_public(request)
        response = self.responses.pop(0)
        return 200, json.dumps(response).encode(), {}

    @staticmethod
    def assert_public(request):
        parsed = urlparse(request.full_url)
        assert request.method == "GET"
        assert parsed.scheme == "https"
        assert parsed.netloc in {"fapi.binance.com", "www.binance.com"}


class MarketUniverseTests(unittest.TestCase):
    @staticmethod
    def book(symbol, time=99_000):
        return {"symbol": symbol, "bidPrice": "10", "askPrice": "10.1",
                "bidQty": "2", "askQty": "3", "time": time}

    @staticmethod
    def mark(symbol, time=99_000):
        return {"symbol": symbol, "markPrice": "10.05", "time": time}

    def test_batch_quotes_use_two_array_requests_and_validate_after_io(self):
        transport = FixtureTransport(
            [self.book("BTCUSDC"), {"symbol": "UNREQUESTED"}, self.book("ETHUSDC")],
            [self.mark("ETHUSDC"), self.mark("BTCUSDC"), {"symbol": "UNREQUESTED"}],
        )
        quotes = PublicMarket(transport=transport, clock_ms=lambda: 100_000).quotes(
            ["BTCUSDC", "ETHUSDC"])
        self.assertEqual(list(quotes), ["BTCUSDC", "ETHUSDC"])
        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(all(not urlparse(request.full_url).query
                            for request, _timeout in transport.requests))
        self.assertEqual([urlparse(request.full_url).path for request, _ in transport.requests],
                         ["/fapi/v1/ticker/bookTicker", "/fapi/v1/premiumIndex"])
        self.assertTrue(all(quote.received_ms == 100_000 for quote in quotes.values()))

    def test_batch_quotes_reject_stale_book_or_mark(self):
        for books, marks in (
            ([self.book("BTCUSDC", 94_999)], [self.mark("BTCUSDC")]),
            ([self.book("BTCUSDC")], [self.mark("BTCUSDC", 94_999)]),
        ):
            with self.subTest(books=books, marks=marks), self.assertRaises(ValueError):
                PublicMarket(transport=FixtureTransport(books, marks),
                             clock_ms=lambda: 100_000).quotes(["BTCUSDC"])

    def test_batch_quotes_reject_missing_duplicate_and_malformed_requested_rows(self):
        invalid_batches = (
            ([self.book("BTCUSDC")], []),
            ([self.book("BTCUSDC"), self.book("BTCUSDC")], [self.mark("BTCUSDC")]),
            ([{key: value for key, value in self.book("BTCUSDC").items()
               if key != "askQty"}], [self.mark("BTCUSDC")]),
        )
        for books, marks in invalid_batches:
            with self.subTest(books=books, marks=marks), self.assertRaises(ValueError):
                PublicMarket(transport=FixtureTransport(books, marks),
                             clock_ms=lambda: 100_000).quotes(["BTCUSDC"])

    def test_turnover_filters_status_and_contract_and_parses_decimal(self):
        exchange = {"symbols": [
            instrument("ETHUSDC"), instrument(),
            instrument("SOLUSDC", status="BREAK"),
            {**instrument("XRPUSDT"), "quoteAsset": "USDT", "marginAsset": "USDT"},
        ]}
        tickers = [
            {"symbol": "BTCUSDC", "quoteVolume": "20000000.0001"},
            {"symbol": "ETHUSDC", "quoteVolume": "19999999.99"},
            {"symbol": "SOLUSDC", "quoteVolume": "999999999"},
        ]
        transport = FixtureTransport(exchange, tickers)
        found = PublicMarket(transport=transport).eligible_instruments()
        self.assertEqual(list(found), ["BTCUSDC"])
        self.assertEqual(found["BTCUSDC"].base_asset, "BTC")
        self.assertEqual(urlparse(transport.requests[1][0].full_url).netloc, "fapi.binance.com")
        self.assertTrue(urlparse(transport.requests[1][0].full_url).path.endswith("/ticker/24hr"))

    def test_configurable_floor_has_no_btc_eth_bypass(self):
        transport = FixtureTransport(
            {"symbols": [instrument("BTCUSDC"), instrument("ETHUSDC")]},
            [{"symbol": "BTCUSDC", "quoteVolume": "10"},
             {"symbol": "ETHUSDC", "quoteVolume": "20"}],
        )
        found = PublicMarket(transport=transport).eligible_instruments(Decimal("15"))
        self.assertEqual(list(found), ["ETHUSDC"])
        with self.assertRaises(ValueError):
            PublicMarket(transport=FixtureTransport()).eligible_instruments(-1)

    def test_malformed_ticker_response_fails_closed(self):
        market = PublicMarket(transport=FixtureTransport({"symbols": [instrument()]}, {"bad": True}))
        with self.assertRaisesRegex(ValueError, "ticker24hr"):
            market.eligible_instruments()
        market = PublicMarket(transport=FixtureTransport(
            {"symbols": [instrument()]}, [{"symbol": "BTCUSDC", "quoteVolume": "NaN"}]))
        with self.assertRaisesRegex(ValueError, "quoteVolume"):
            market.eligible_instruments()

    def test_new_closed_candle_intervals_and_limit_ceiling(self):
        width = 3_600_000
        now = 2 * width
        rows = [[0, "10", "11", "9", "10.5", "2", width - 1],
                [width, "10.5", "12", "10", "11", "3", 2 * width - 1]]
        transport = FixtureTransport(rows)
        candles = PublicMarket(transport=transport).candles("BTCUSDC", "1h", now)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[-1].close_ms, now - 1)
        query = urlparse(transport.requests[0][0].full_url).query
        self.assertIn("interval=1h", query)
        with self.assertRaises(ValueError):
            PublicMarket(transport=FixtureTransport([])).candles("BTCUSDC", "4h", 0, 1001)


def article(title, release=1_000_000, code="article-1"):
    return {"title": title, "releaseDate": release, "code": code}


def payload(*articles):
    return {"data": {"catalogs": [{"articles": list(articles)}]}}


class AnnouncementTests(unittest.TestCase):
    @staticmethod
    def perp(symbol, *, status="TRADING", margin="USDC", base=None):
        return Instrument(symbol, margin, status, Decimal("0.1"), Decimal("0.01"),
                          Decimal("0.01"), Decimal("100"), Decimal("5"),
                          base_asset=base or symbol[:-4])

    def test_fixed_public_get_and_exact_usdc_mapping(self):
        transport = FixtureTransport(payload(article("Binance Will Delist AAA and BBB on 2025-01-01")))
        adapter = BinanceAnnouncements(transport=transport, clock_ms=lambda: 1_100_000)
        result = adapter.poll({"AAAUSDC", "1000BBBUSDC"})
        self.assertEqual([event.symbol for event in result.events], ["AAAUSDC", "1000BBBUSDC"])
        event = result.events[0]
        self.assertEqual((event.release_ms, event.observed_ms, event.article_id),
                         (1_000_000, 1_100_000, "article-1"))
        self.assertEqual(event.source_url,
                         "https://www.binance.com/en/support/announcement/article-1")
        self.assertEqual(transport.requests[0][0].full_url, CMS_URL)
        self.assertEqual(transport.requests[0][0].method, "GET")

    def test_no_match_emits_auditable_skip_diagnostic(self):
        result = BinanceAnnouncements(clock_ms=lambda: 1_100_000).parse(
            payload(article("Binance Will Delist MISSING")), {"OTHERUSDC"})
        self.assertEqual(result.events, ())
        self.assertEqual(result.diagnostics[0].status, "SKIPPED_NO_USDC_PERP")
        self.assertEqual(result.diagnostics[0].ticker, "MISSING")

    def test_mapping_requires_matching_trading_usdc_instrument_metadata(self):
        adapter = BinanceAnnouncements(clock_ms=lambda: 1_100_000)
        document = payload(article("Binance Will Delist AAA and BBB"))
        instruments = {
            "AAAUSDC": self.perp("AAAUSDC", status="BREAK"),
            "BBBUSDC": self.perp("BBBUSDC", margin="USDT"),
        }
        result = adapter.parse(document, instruments)
        self.assertEqual(result.events, ())
        self.assertEqual([item.status for item in result.diagnostics],
                         ["SKIPPED_NO_USDC_PERP", "SKIPPED_NO_USDC_PERP"])
        ok = {"AAAUSDC": self.perp("AAAUSDC")}
        self.assertEqual(adapter.parse(document, ok).events[0].symbol, "AAAUSDC")

    def test_rate_limit_status_and_http_error_use_market_delay(self):
        class StatusLimited:
            def __call__(self, request, timeout):
                return 429, b"{}", {"Retry-After": "123"}

        with self.assertRaises(RateLimited) as status_error:
            BinanceAnnouncements(transport=StatusLimited()).fetch()
        self.assertEqual(status_error.exception.retry_after_seconds, 123)

        class ExceptionLimited:
            def __call__(self, request, timeout):
                from urllib.error import HTTPError
                raise HTTPError(request.full_url, 418, "limited", {"Retry-After": "90"}, None)

        with self.assertRaises(RateLimited) as http_error:
            BinanceAnnouncements(transport=ExceptionLimited()).fetch()
        self.assertEqual(http_error.exception.retry_after_seconds, 90)

    def test_future_and_stale_articles_are_skipped(self):
        result = BinanceAnnouncements(clock_ms=lambda: 1_000_000).parse(
            payload(article("Binance Will Delist FUTURE", 1_000_001, "future"),
                    article("Binance Will Delist OLD", 699_999, "old")), {"FUTUREUSDC"})
        self.assertEqual(result.events, ())
        self.assertEqual([item.status for item in result.diagnostics],
                         ["SKIPPED_FUTURE_RELEASE", "SKIPPED_STALE_RELEASE"])

    def test_malformed_schema_title_time_and_code_rejected(self):
        adapter = BinanceAnnouncements(clock_ms=lambda: 1_000_000)
        for malformed in ([], {"data": {}}, payload({"title": 5}),
                          payload(article("Binance Will Delist AAA", "1000000")),
                          payload(article("Binance Will Delist AAA", 999_999, "../bad"))):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                adapter.parse(malformed, {"AAAUSDC"})

    def test_repeated_parse_is_deterministic(self):
        adapter = BinanceAnnouncements(clock_ms=lambda: 1_000_000)
        document = payload(article("Binance Will Delist AAA"))
        first = adapter.parse(document, {"AAAUSDC"})
        second = adapter.parse(document, {"AAAUSDC"})
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
