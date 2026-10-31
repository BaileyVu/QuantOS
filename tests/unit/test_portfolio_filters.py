import unittest
from quantos.paper.market import parse_exchange_info
from tests.unit.test_v3_market import instrument


class ResearchFilterTests(unittest.TestCase):
    def test_usdt_requires_explicit_opt_in_and_preserves_filter_values(self):
        item = instrument("BTCUSDT")
        item.update(quoteAsset="USDT", marginAsset="USDT", baseAsset="BTC")
        data = {"symbols": [item, instrument()]}
        self.assertEqual(set(parse_exchange_info(data)), {"BTCUSDC"})
        result = parse_exchange_info(data, "USDT")
        self.assertEqual(set(result), {"BTCUSDT"})
        self.assertEqual(result["BTCUSDT"].tick, parse_exchange_info(data)["BTCUSDC"].tick)
        self.assertEqual(result["BTCUSDT"].base_asset, "BTC")

    def test_mismatched_asset_or_missing_filters_is_not_guessed(self):
        item = instrument("BTCUSDT")
        item.update(quoteAsset="USDT", marginAsset="USDC")
        self.assertEqual(parse_exchange_info({"symbols": [item]}, "USDT"), {})
        item.update(marginAsset="USDT", filters=[])
        self.assertEqual(parse_exchange_info({"symbols": [item]}, "USDT"), {})
        with self.assertRaises(ValueError): parse_exchange_info({"symbols": []}, "BUSD")


if __name__ == "__main__": unittest.main()
