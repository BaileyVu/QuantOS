from pathlib import Path
import unittest
from decimal import Decimal as D

from quantos.paper.research.archived_ema import (
    DEFAULT_BACKTEST_PATH, DEFAULT_OPTIMIZE_PATH, DEFAULT_STRATEGY_PATH,
    TRAIN_END_MS, ArchivedConfig, build_archived_candidates,
    load_archived_source,
)
from quantos.paper.research.dataset import DatasetIndex
from quantos.paper.research.data import Bar
from quantos.paper.strategies.candidate_features import HOUR_MS
from quantos.paper.research.archived_ema import TRAIN_START_MS


HAS_ATTACHED_SOURCES = all(path.exists() for path in (
    DEFAULT_STRATEGY_PATH, DEFAULT_OPTIMIZE_PATH, DEFAULT_BACKTEST_PATH))


@unittest.skipUnless(HAS_ATTACHED_SOURCES, "attached archived sources are unavailable")
class ArchivedEmaSourceAuditTests(unittest.TestCase):
    def test_only_whitelisted_strategy_definitions_are_compiled(self):
        namespace, source = load_archived_source()
        self.assertEqual(source.strategy_sha256,
                         "6049ef13b93495de8612b45d8fc53b11e2ce56a903032fdb12392b3aea9f1fd5")
        self.assertEqual(source.optimize_sha256,
                         "f8c0c45a08bab58f780adb9de1692cc87b52e744bc4216c693d19565561c5e7b")
        self.assertEqual(source.backtest_sha256,
                         "10926768c88cd3a0cf371d87a4aa219e6b7d15c0b93491d8d865ff4af744ae1f")
        self.assertEqual(namespace["Params"]().fast, 9)
        self.assertEqual(set(namespace), {"np", "dataclass", "__builtins__", *source.selected_bindings})
        self.assertEqual(source.config, ArchivedConfig())
        self.assertEqual(source.universe, (
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
        ))
        self.assertEqual(dict(source.level_parameters), {
            "fast": 9, "slow": 20, "near_atr": 3, "retest_win": 12,
            "tol_atr": 1.0, "zone_body": True, "lookback": 96,
        })

    def test_empty_train_only_index_builds_no_candidates_and_no_outcomes(self):
        index = DatasetIndex({}, {}, {}, {}, (), {}, {},
                             ("2021-10-01", "2024-12-31"), "fixture", ())
        candidates, source = build_archived_candidates(index)
        self.assertEqual(candidates, ())
        self.assertEqual(TRAIN_END_MS, 1_735_689_600_000)
        self.assertEqual(source.config.exit_rule, "cross")
        self.assertEqual(source.config.slots, 4)
        self.assertEqual(source.config.leverage, 1)

    def test_strict_usdm_2h_wrapper_processes_only_pre_2025_training_rows(self):
        symbol = "BTCUSDT"
        bars = tuple(Bar(symbol, "1h", TRAIN_START_MS + i * HOUR_MS,
                         TRAIN_START_MS + (i + 1) * HOUR_MS - 1,
                         D(10000), D(10100), D(9900), D(10000), D(1))
                     for i in range(2210))
        index = DatasetIndex({symbol: bars}, {}, {}, {}, (),
                             {symbol: {row.open_ms: row for row in bars}}, {},
                             ("2021-10-01", "2024-12-31"), "fixture", ())
        candidates, source = build_archived_candidates(index)
        self.assertEqual(candidates, ())
        self.assertEqual(source.data_hash, "fixture")
        self.assertEqual(len(source.filter_hash), 64)

    def test_nontraining_index_is_rejected_before_source_use(self):
        index = DatasetIndex({}, {}, {}, {}, (), {}, {},
                             ("2025-01-01", "2025-12-31"), "fixture", ())
        with self.assertRaises(ValueError):
            build_archived_candidates(index)


if __name__ == "__main__":
    unittest.main()
