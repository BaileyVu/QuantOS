import unittest

from sentibot.forward_report import analyze, _forward_return


class ForwardReportTests(unittest.TestCase):
    def test_forward_return_uses_asof_entry_and_first_quote_after_horizon(self):
        quotes = {"BTCUSDC": [(999, 100.0), (1001, 500.0), (14_401_000, 101.0)]}
        value, reason = _forward_return(quotes, "BTCUSDC", 1000, 14_400_000, 5000)
        self.assertIsNone(reason)
        self.assertAlmostEqual(value, 100.0)

    def test_gate_join_uses_verdict_availability_symbol_and_direction(self):
        events = [
            (1_000, "CANDIDATE", {"signal_id": "s1", "symbol": "BTCUSDC", "side": "SHORT", "strategy_id": "gated"}),
            (1_050, "SENTIMENT_SHADOW", {"signal_id": "s1", "verdict": "WOULD_BLOCK"}),
            (1_050, "SENTIMENT_SHADOW", {"signal_id": "missing", "verdict": "WOULD_ALLOW"}),
        ]
        quotes = {"BTCUSDC": [(1_000, 100), (14_401_050, 101), (86_401_050, 99)]}
        report = analyze([], events, quotes, gated_strategies={"gated"}, minimum_observations=1)
        four = report["horizons"]["4h"]["sentiment_gate"]
        self.assertEqual(four["WOULD_BLOCK"]["n"], 1)
        self.assertAlmostEqual(four["WOULD_BLOCK"]["mean_directional_return_bps"], -100)
        self.assertEqual(four["missing"]["candidate_not_found"], 1)
        self.assertEqual(four["status"], "INSUFFICIENT_COVERAGE")

    def test_reach_and_baseline_report_marks_missing_and_low_coverage(self):
        from sentibot.sentibot_reach_shadow import ASSETS
        row = {"t": 1, "available_at": 1, "reach": {"BTCUSDT": 0.5}, "baseline_news": {"BTCUSDT": -0.5}}
        report = analyze([row], [], {"BTCUSDC": [(1000, 100)]}, minimum_observations=2)
        summary = report["horizons"]["4h"]["score_comparison"]
        self.assertEqual(summary["reach"]["missing"]["no_forward_quote"], 1)
        self.assertEqual(summary["baseline"]["status"], "INSUFFICIENT_COVERAGE")
        self.assertGreater(len(ASSETS), 1)

    def test_forward_mark_after_large_gap_is_missing_not_a_valid_horizon(self):
        result, reason = _forward_return({"BTCUSDC": [(1000, 100), (100_000_000, 200)]},
                                         "BTCUSDC", 1000, 14_400_000, 60_000)
        self.assertIsNone(result)
        self.assertEqual(reason, "late_forward_quote")

    def test_duplicate_candidates_and_verdicts_do_not_inflate_gate_n(self):
        candidate = {"signal_id": "s", "symbol": "BTCUSDC", "side": "LONG", "strategy_id": "gated"}
        events = [(1000, "CANDIDATE", candidate), (1000, "SENTIMENT_SHADOW", {"signal_id": "s", "verdict": "WOULD_BLOCK"}),
                  (2000, "CANDIDATE", candidate), (2000, "SENTIMENT_SHADOW", {"signal_id": "s", "verdict": "WOULD_ALLOW"})]
        report = analyze([], events, {"BTCUSDC": [(1000, 100), (14_401_000, 101)]}, gated_strategies={"gated"})
        stats = report["horizons"]["4h"]["sentiment_gate"]
        self.assertEqual(stats["WOULD_BLOCK"]["n"], 1)
        self.assertEqual(stats["WOULD_ALLOW"]["n"], 0)
        self.assertEqual(stats["missing"]["duplicate_signal_verdict"], 1)

    def test_score_return_starts_after_collection_and_old_capture_is_unverified(self):
        row = {"t": 1, "available_at": 2, "reach": {"BTCUSDT": .5}, "baseline_news": {"BTCUSDT": .5}}
        quotes = {"BTCUSDC": [(1000, 100), (2000, 200), (14_402_000, 202)]}
        report = analyze([row], [], quotes)
        self.assertAlmostEqual(report["horizons"]["4h"]["score_comparison"]["reach"]["mean_forward_return_bps"], 100)
        row.pop("available_at")
        report = analyze([row], [], quotes)
        self.assertEqual(report["horizons"]["4h"]["score_comparison"]["reach"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
