import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from quantos.paper.research.timesfm_drift import (
    CONTEXT, HOUR_MS, MAX_TIMEOUT_SECONDS, drift_features, generate,
    plan_document, read_progress, validate_bars,
)
from quantos.paper.research.timesfm_experiment import BUILD_START_MS, Forecast


def bars(count=515):
    return [(BUILD_START_MS + i * HOUR_MS, 100.0 + i) for i in range(count)]


def predictor(prefix):
    ts, close = prefix[-1]
    return Forecast(ts, close,
                    {4: close * math.exp(.04), 12: close * math.exp(.12), 24: close * math.exp(.24)},
                    {}, {})


class TimesfmDriftTest(unittest.TestCase):
    def test_features_use_exact_512_close_prefix_and_only_median_drifts(self):
        seen = []

        def fake(prefix):
            seen.append(prefix)
            return predictor(prefix)

        result = drift_features(bars(CONTEXT)[-CONTEXT:], fake)
        self.assertEqual(len(seen[0]), CONTEXT)
        self.assertEqual(result["origin_ms"], seen[0][-1][0])
        self.assertAlmostEqual(result["drift_4h"], .04)
        self.assertAlmostEqual(result["drift_12h"], .12)
        self.assertAlmostEqual(result["drift_24h"], .24)
        self.assertEqual(set(result), {"origin_ms", "drift_4h", "drift_12h", "drift_24h"})

    def test_bars_reject_gaps_and_non_build_timestamps(self):
        data = bars(CONTEXT)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            validate_bars(data[:4] + [(data[4][0] + HOUR_MS, data[4][1])])
        with self.assertRaisesRegex(ValueError, "outside BUILD"):
            validate_bars([(1_735_689_600_000, 100.0)])

    def test_origin_and_horizons_fail_closed(self):
        data = bars(CONTEXT)
        with self.assertRaisesRegex(ValueError, "origin"):
            drift_features(data, lambda p: Forecast(p[-1][0] + HOUR_MS, p[-1][1], {4: 1, 12: 1, 24: 1}, {}, {}))
        with self.assertRaisesRegex(ValueError, "missing median"):
            drift_features(data, lambda p: Forecast(p[-1][0], p[-1][1], {4: 1, 12: 1}, {}, {}))

    def test_plan_bounds_wall_timeout_and_commit_is_audit_only(self):
        kwargs = dict(symbol="BTCUSDT", manifest_sha256="a" * 64,
                      dependencies={"torch": "x"}, timeout_seconds=MAX_TIMEOUT_SECONDS)
        first, h1 = plan_document(**kwargs, source={"commit": "one", "source_sha256": "same"}, created_at=100)
        second, h2 = plan_document(**kwargs, source={"commit": "two", "source_sha256": "same"}, created_at=200)
        self.assertEqual(h1, h2)
        self.assertNotEqual(first["audit_sha256"], second["audit_sha256"])
        with self.assertRaises(ValueError):
            plan_document(**{**kwargs, "timeout_seconds": MAX_TIMEOUT_SECONDS + 1}, source={}, created_at=0)

    def test_generate_resumes_without_repeating_completed_origins(self):
        data = bars(514)
        clock = Mock(side_effect=[100.0, 100.1, 100.1, 100.2, 100.3, 100.4, 100.5, 100.6, 100.7])
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            first = generate(data, predictor, output_dir=output, symbol="BTCUSDT",
                             manifest_sha256="b" * 64, source={"commit": "a", "source_sha256": "src"},
                             dependencies={"torch": "synthetic"}, timeout_seconds=100, clock=clock)
            self.assertEqual(len(first), 3)
            never = Mock(side_effect=AssertionError("cached forecast repeated"))
            second = generate(data, never, output_dir=output, symbol="BTCUSDT",
                              manifest_sha256="b" * 64, source={"commit": "b", "source_sha256": "src"},
                              dependencies={"torch": "synthetic"}, timeout_seconds=100,
                              clock=Mock(side_effect=[102.0, 102.1, 102.1]))
            never.assert_not_called()
            self.assertEqual(first, second)
            plan = json.loads((output / "drift_plan.json").read_text())
            completed, _, elapsed = read_progress(output / "drift_progress.jsonl",
                                                   plan_hash=plan["plan_sha256"],
                                                   expected={r["origin_ms"] for r in first})
            self.assertEqual(len(completed), 3)
            self.assertGreater(elapsed, 0)

    def test_changed_identity_tamper_and_duplicate_origin_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "run"
            generate(bars(512), predictor, output_dir=output, symbol="BTCUSDT",
                     manifest_sha256="c" * 64, source={"source_sha256": "src"},
                     dependencies={"torch": "x"}, timeout_seconds=100,
                     clock=Mock(side_effect=[100, 101, 101, 101, 102]))
            with self.assertRaisesRegex(ValueError, "different or tampered"):
                generate(bars(512), predictor, output_dir=output, symbol="ETHUSDT",
                         manifest_sha256="c" * 64, source={"source_sha256": "src"},
                         dependencies={"torch": "x"}, timeout_seconds=100, clock=lambda: 103)
            progress = output / "drift_progress.jsonl"
            text = progress.read_text()
            progress.write_text(text + text)
            plan = json.loads((output / "drift_plan.json").read_text())
            with self.assertRaisesRegex(ValueError, "hash-chain|duplicate"):
                read_progress(progress, plan_hash=plan["plan_sha256"],
                              expected={BUILD_START_MS + (CONTEXT - 1) * HOUR_MS})

    def test_expired_deadline_blocks_additional_forecasts(self):
        data = bars(512)
        with tempfile.TemporaryDirectory() as tmp:
            calls = Mock(side_effect=predictor)
            with self.assertRaisesRegex(TimeoutError, "deadline"):
                generate(data, calls, output_dir=Path(tmp) / "run", symbol="BTCUSDT",
                         manifest_sha256="d" * 64, source={}, dependencies={}, timeout_seconds=1,
                         clock=Mock(side_effect=[100, 102, 102]))
            calls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
