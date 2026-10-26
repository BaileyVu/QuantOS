import json
import unittest
from decimal import Decimal
from urllib.parse import urlparse, parse_qs

from quantos.paper.market import PublicMarket, BASE, RateLimited


class FixtureTransport:
    def __init__(self, responses): self.responses, self.requests = list(responses), []
    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if request.method != "GET" or urlparse(request.full_url).scheme != "https" or urlparse(request.full_url).netloc != "fapi.binance.com":
            raise AssertionError("non-public request")
        value = self.responses.pop(0)
        return 200, json.dumps(value).encode(), {}


def instrument(symbol="BTCUSDC"):
    return {"symbol":symbol,"quoteAsset":"USDC","marginAsset":"USDC","contractType":"PERPETUAL","status":"TRADING","filters":[
        {"filterType":"PRICE_FILTER","tickSize":"0.1","minPrice":"0.1","maxPrice":"100000"},
        {"filterType":"LOT_SIZE","stepSize":"0.001","minQty":"0.001","maxQty":"100"},
        {"filterType":"MARKET_LOT_SIZE","stepSize":"0.001","minQty":"0.001","maxQty":"100"},
        {"filterType":"MIN_NOTIONAL","notional":"5"},
        {"filterType":"PERCENT_PRICE","multiplierUp":"1.05","multiplierDown":"0.95"}]}


class PublicMarketTests(unittest.TestCase):
    def test_exchange_info_filters_non_usdc_and_missing_filters(self):
        bad = instrument("SOLUSDC"); bad["filters"] = []
        tr = FixtureTransport([{"symbols":[instrument("ETHUSDC"), instrument(), instrument("BTCUSDT"), bad]}])
        result = PublicMarket(transport=tr).exchange_info()
        self.assertEqual(list(result), ["BTCUSDC", "ETHUSDC"])
        self.assertEqual(result["BTCUSDC"].market_step, Decimal("0.001"))

    def test_server_time_and_fixed_host(self):
        tr = FixtureTransport([{"serverTime":1234}])
        self.assertEqual(PublicMarket(transport=tr).server_time(), 1234)
        self.assertEqual(urlparse(tr.requests[0][0].full_url).netloc, "fapi.binance.com")
        self.assertEqual(tr.requests[0][0].method, "GET")

    def test_closed_contiguous_candles_and_no_open_row(self):
        rows = [[0,"10","11","9","10.5","2",59999], [60000,"10.5","12","10","11","3",119999], [120000,"11","12","10","11","1",179999]]
        got = PublicMarket(transport=FixtureTransport([rows])).candles("BTCUSDC","1m",150000)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[-1].close, Decimal("11"))

    def test_non_contiguous_and_stale_candles_fail(self):
        rows = [[0,"10","11","9","10.5","2",59999], [120000,"10.5","12","10","11","3",179999]]
        with self.assertRaises(ValueError): PublicMarket(transport=FixtureTransport([rows])).candles("BTCUSDC","1m",180000)
        row = [[0,"10","11","9","10.5","2",59999]]
        with self.assertRaises(ValueError): PublicMarket(transport=FixtureTransport([row])).candles("BTCUSDC","1m",200000)

    def test_quote_validates_freshness_and_values(self):
        tr = FixtureTransport([{"symbol":"BTCUSDC","bidPrice":"10","askPrice":"10.1","bidQty":"2","askQty":"3","time":99900},{"symbol":"BTCUSDC","markPrice":"10.05","time":99900}])
        q = PublicMarket(transport=tr, clock_ms=lambda:100000).quote("BTCUSDC")
        self.assertEqual(q.bid, Decimal("10")); self.assertEqual(q.received_ms, 100000)
        stale = FixtureTransport([{"symbol":"BTCUSDC","bidPrice":"10","askPrice":"10.1","bidQty":"2","askQty":"3","time":99900},{"symbol":"BTCUSDC","markPrice":"10.05","time":1}])
        with self.assertRaises(ValueError): PublicMarket(transport=stale, clock_ms=lambda:100000).quote("BTCUSDC")

    def test_quote_latency_covers_both_requests(self):
        now = [1000]
        class SlowTransport(FixtureTransport):
            def __call__(self, request, timeout):
                response = super().__call__(request, timeout)
                if len(self.requests) == 2: now[0] = 7000
                return response
        tr = SlowTransport([{"symbol":"BTCUSDC","bidPrice":"10","askPrice":"10.1","bidQty":"2","askQty":"3","time":1000},{"symbol":"BTCUSDC","markPrice":"10.05","time":6999}])
        with self.assertRaises(ValueError): PublicMarket(transport=tr, clock_ms=lambda:now[0]).quote("BTCUSDC")

    def test_aggregate_trade_shape_chronology_and_cursor(self):
        rows = [{"a":4,"T":11,"p":"10","q":"2","m":True},{"a":5,"T":12,"p":"10.1","q":"1","m":False}]
        tr = FixtureTransport([rows]); trades = PublicMarket(transport=tr).trades("BTCUSDC",from_id=4)
        self.assertEqual([x.trade_id for x in trades], [4,5])
        self.assertEqual(parse_qs(urlparse(tr.requests[0][0].full_url).query)["fromId"], ["4"])
        with self.assertRaises(ValueError): PublicMarket(transport=FixtureTransport([rows])).trades("BTCUSDC")

    def test_funding_range(self):
        rows = [{"symbol":"BTCUSDC","fundingTime":100,"fundingRate":"0.0001","markPrice":"10"}]
        got = PublicMarket(transport=FixtureTransport([rows])).funding("BTCUSDC",100,200)
        self.assertEqual(got[0].rate, Decimal("0.0001"))

    def test_auth_and_arbitrary_endpoints_unavailable(self):
        market = PublicMarket(transport=FixtureTransport([]))
        with self.assertRaises(ValueError): market._get("/fapi/v1/order", {"signature":"x"})
        with self.assertRaises(ValueError): market._get("https://evil.example/order")
        self.assertEqual(BASE, "https://fapi.binance.com")

    def test_rate_limit_returns_full_retry_delay_without_sleep(self):
        class Limited:
            def __call__(self, request, timeout): return 429, b'{}', {"Retry-After":"123"}
        with self.assertRaises(RateLimited) as caught:
            PublicMarket(transport=Limited()).server_time()
        self.assertEqual(caught.exception.retry_after_seconds, 123)

    def test_candle_malformed_ohlc_raises_and_latest_alignment_required(self):
        malformed = [[60000,"10","11","12","10.5","2",119999]]
        with self.assertRaises(ValueError): PublicMarket(transport=FixtureTransport([malformed])).candles("BTCUSDC","1m",120000)
        old = [[0,"10","11","9","10.5","2",59999]]
        with self.assertRaises(ValueError): PublicMarket(transport=FixtureTransport([old])).candles("BTCUSDC","1m",120000)

    def test_aggregate_trade_gap_rejected(self):
        rows = [{"a":4,"T":11,"p":"10","q":"2","m":True},{"a":6,"T":12,"p":"10.1","q":"1","m":False}]
        with self.assertRaises(ValueError): PublicMarket(transport=FixtureTransport([rows])).trades("BTCUSDC",from_id=4)


if __name__ == "__main__": unittest.main()
