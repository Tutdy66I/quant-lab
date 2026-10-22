"""Offline evidence checks for the phase-one research artifact only."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from research.binance_archive_phase1 import analyze, digest, dump


SOURCE = Path(__file__).resolve().parents[1] / 'data/archive_feasibility/binance_um_btcusdt_v1'


class ArchiveFeasibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'evidence'
        shutil.copytree(SOURCE, self.root)

    def test_committed_evidence_replays_and_does_not_assume_issue491_shift(self):
        expected = (self.root / 'analysis.json').read_bytes()
        with redirect_stdout(io.StringIO()):
            analyze(self.root)
        self.assertEqual((self.root / 'analysis.json').read_bytes(), expected)
        result = json.loads(expected)
        self.assertEqual(len(result['archives']), 12)
        self.assertEqual(result['boundary_collisions'], [])
        for day in ('2026-06-24', '2026-06-25'):
            windows = result['flow_alignment'][day]
            self.assertGreater(windows['0']['pearson_r'], .99)
            self.assertLess(windows['-300000']['pearson_r'], .2)
        recent = next(x for x in result['archives'] if x['file'] == 'BTCUSDT-metrics-2026-10-20.zip')
        self.assertGreater(recent['backward_steps'], 0)
        self.assertEqual(recent['missing_start_grid_labels'], [])
        self.assertEqual(recent['sorted_non_300_second_steps'], 0)

    def test_raw_response_corruption_is_rejected(self):
        (self.root / 'raw/BTCUSDT-metrics-2026-06-24.zip').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'Receipt integrity failure'):
            analyze(self.root)

    def test_publisher_checksum_checked_even_with_consistent_local_receipt(self):
        relative = 'raw/BTCUSDT-metrics-2026-06-24.zip.CHECKSUM'
        path = self.root / relative
        blob = ('0'*64 + '  BTCUSDT-metrics-2026-06-24.zip\n').encode()
        path.write_bytes(blob)
        receipts = json.loads((self.root / 'receipts.json').read_text(encoding='utf-8'))
        receipt = next(x for x in receipts if x['file'] == relative)
        receipt.update(sha256=digest(blob), bytes=len(blob))
        dump(self.root / 'receipts.json', receipts)
        with self.assertRaisesRegex(ValueError, 'Publisher checksum mismatch'):
            analyze(self.root)
