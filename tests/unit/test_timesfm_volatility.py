import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.research.registry import create_dataset_manifest
from quantos.paper.research.timesfm_experiment import BUILD_START_MS, HOUR_MS, Forecast
from quantos.paper.research import timesfm_volatility as protocol
from quantos.paper.research.timesfm_volatility import compare, load_build_bars


class TimesfmVolatilityTest(unittest.TestCase):
    def test_nonoverlapping_targets_and_prefix_only_predictions(self):
        rows = [(BUILD_START_MS + i * HOUR_MS, 102. * math.exp(.001 * i), 98. * math.exp(.001 * i), 100. * math.exp(.001 * i)) for i in range(560)]
        calls = []
        def predictor(prefix):
            self.assertEqual(len(prefix), 512)
            calls.append(prefix[-1][0])
            ts, close = prefix[-1]
            return Forecast(ts, close, {h: close for h in (4, 12, 24)}, {24: close * .99}, {24: close * 1.01})
        result = compare(rows, predictor)
        self.assertEqual(result['n'], 2)
        self.assertEqual(calls, [rows[511][0], rows[535][0]])
        for row in result['observations']:
            self.assertAlmostEqual(row['realized'], math.sqrt(24) * .001)
            self.assertAlmostEqual(row['predicted']['persistence'], row['realized'])
            self.assertAlmostEqual(row['predicted']['ewma'], row['realized'])
            self.assertEqual(row['target_start_ms'], row['origin_ms'] + HOUR_MS)
            self.assertEqual(row['target_end_ms'], row['origin_ms'] + 24 * HOUR_MS)
        self.assertGreater(result['mae']['timesfm'], result['mae']['persistence'])
        # Modifying an unseen target never changes forecasts at prior origins.
        changed = list(rows)
        ts, hi, lo, close = changed[-1]
        changed[-1] = (ts, hi, lo, close * 2)
        after = compare(changed, predictor)
        self.assertEqual(result['observations'][1]['predicted'], after['observations'][1]['predicted'])
        self.assertNotEqual(result['observations'][1]['realized'], after['observations'][1]['realized'])

    def test_manifest_range_blocks_held_before_dataset_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / 'manifest.json'
            manifest.write_text(json.dumps({'range': {'start': '2023-07-01', 'end': '2024-12-31'}}))
            with patch('quantos.paper.research.timesfm_volatility.verify_dataset_manifest') as verify:
                with self.assertRaisesRegex(ValueError, 'full 2022'):
                    load_build_bars(manifest, 'a' * 64)
                verify.assert_not_called()

    def test_completed_hour_gap_and_manifest_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, manifest = Path(tmp) / 'bars.jsonl', Path(tmp) / 'manifest.json'
            start = protocol._utc_ms(protocol.START_DATE)
            rows = [{'time_ms': start + i * HOUR_MS, 'symbol': 'BTCUSDT',
                     'high': 101, 'low': 99, 'close': 100}
                    for i in range(8760)]
            data.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            created = create_dataset_manifest(
                data, manifest, source='synthetic fixture', start='2022-01-01', end='2022-12-31',
                series={'symbol': 'BTCUSDT', 'market': protocol.MARKET,
                        'timeframe': '1h', 'timestamp_semantics': protocol.TIMESTAMP_SEMANTICS})
            symbol, loaded, verified = load_build_bars(manifest, created['manifest_sha256'])
            self.assertEqual(symbol, 'BTCUSDT')
            self.assertEqual(len(loaded), 8760)
            self.assertEqual(verified['manifest_sha256'], created['manifest_sha256'])
            with self.assertRaisesRegex(ValueError, 'manifest checksum'):
                load_build_bars(manifest, '0' * 64)
            data.write_text(data.read_text().replace(str(start), str(start + 1), 1))
            with self.assertRaisesRegex(ValueError, 'dataset checksum'):
                load_build_bars(manifest, created['manifest_sha256'])
