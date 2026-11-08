from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.research.timesfm_dataset import export_index, SYMBOLS, START_MS, END_MS, HOUR_MS
from quantos.paper.research.registry import verify_dataset_manifest


class TimesfmDatasetTest(unittest.TestCase):
    def index(self):
        return SimpleNamespace(selection_bounds=('2021-12-31', '2023-01-01'), manifest_hash='a' * 64,
            bars={symbol: [SimpleNamespace(symbol=symbol, interval='1h', close_exclusive_ms=ts,
                                          high='101', low='99', close='100')
                           for ts in range(START_MS, END_MS, HOUR_MS)] for symbol in SYMBOLS})

    def test_missing_hour_rejected_before_output_creation(self):
        index = self.index()
        index.bars['ETHUSDT'].pop(100)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'export'
            with self.assertRaisesRegex(ValueError, 'exact completed-hour'):
                export_index(index, output, {})
            self.assertFalse(output.exists())

    def test_manifest_binds_full_coverage_and_proxy_series(self):
        with tempfile.TemporaryDirectory() as tmp, patch('quantos.paper.research.timesfm_dataset.persist_audit'):
            result = export_index(self.index(), Path(tmp) / 'export', {'base_manifest_sha256': 'b' * 64})
            for symbol, entry in result['datasets'].items():
                manifest = verify_dataset_manifest(entry['manifest'], expected_hash=entry['manifest_hash'])
                self.assertEqual(entry['rows'], 8760)
                self.assertEqual(manifest['series']['symbol'], symbol)
                self.assertEqual(manifest['series']['timestamp_semantics'], 'completed_close_exclusive_ms')
                self.assertEqual(manifest['source_provenance']['last_close_ms'], END_MS - HOUR_MS)
