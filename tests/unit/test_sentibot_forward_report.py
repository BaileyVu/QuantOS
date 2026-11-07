import unittest

from sentibot.forward_report import analyze, _forward_return


class ForwardReportTests(unittest.TestCase):
    def test_forward_return_uses_asof_entry_and_first_quote_after_horizon(self):
        quotes = {"BTCUSDC": [(999, 100.0), (1001, 500.0), (14_401_000, 101.0)]}
        value, reason = _forward_return(quotes, "BTCUSDC", 1000, 14_400_000, 5000)
        self.assertIsNone(reason)
        self.assertAlmostEqual(value, 100.0)

    def test_gate_join_uses_candidate_time_symbol_and_direction(self):
        events = [
            (1_000, "CANDIDATE", {"signal_id": "s1", "symbol": "BTCUSDC", "side": "SHORT", "strategy_id": "gated"}),
            (1_050, "SENTIMENT_SHADOW", {"signal_id": "s1", "verdict": "WOULD_BLOCK"}),
            (1_050, "SENTIMENT_SHADOW", {"signal_id": "missing", "verdict": "WOULD_ALLOW"}),
        ]
        quotes = {"BTCUSDC": [(1_000, 100), (14_401_000, 101), (86_401_000, 99)]}
        report = analyze([], events, quotes, gated_strategies={"gated"}, minimum_observations=1)
        four = report["horizons"]["4h"]["sentiment_gate"]
        self.assertEqual(four["WOULD_BLOCK"]["n"], 1)
        self.assertAlmostEqual(four["WOULD_BLOCK"]["mean_directional_return_bps"], -100)
        self.assertEqual(four["missing"]["candidate_not_found"], 1)
        self.assertEqual(four["status"], "INSUFFICIENT_COVERAGE")

    def test_reach_and_baseline_report_marks_missing_and_low_coverage(self):
        from sentibot.sentibot_reach_shadow import ASSETS
        row = {"t": 1, "reach": {"BTCUSDT": 0.5}, "baseline_news": {"BTCUSDT": -0.5}}
        report = analyze([row], [], {"BTCUSDC": [(1000, 100)]}, minimum_observations=2)
        summary = report["horizons"]["4h"]["score_comparison"]
        self.assertEqual(summary["reach"]["missing"]["no_forward_quote"], 1)
        self.assertEqual(summary["baseline"]["status"], "INSUFFICIENT_COVERAGE")
        self.assertGreater(len(ASSETS), 1)


if __name__ == "__main__":
    unittest.main()
