import calendar
import copy
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

from strategies.history import collect, months, normalize_archive, sha256, verify_dataset
from strategies.history_study import annual_metrics, study
from strategies.market_data import Candle


def archive_fixture(month, alter=None):
    year, number = map(int, month.split('-'))
    start = int(datetime(year, number, 1, tzinfo=timezone.utc).timestamp())
    unit = 1_000_000 if year >= 2025 else 1000
    rows = [[(start + i * 86400) * unit, '100.00', '110.00', '90.00', '105.00', '1',
             (start + (i + 1) * 86400) * unit - 1, '100', '1', '1', '100', '0']
            for i in range(calendar.monthrange(year, number)[1])]
    if alter:
        alter(rows)
    text = io.StringIO(newline='')
    csv.writer(text, lineterminator='\n').writerows(rows)
    buffer = io.BytesIO()
    filename = f'BTCUSDT-1d-{month}'
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(filename + '.csv', text.getvalue())
    blob = buffer.getvalue()
    return blob, f'{sha256(blob)}  {filename}.zip\n'.encode()


class HistoryTests(unittest.TestCase):
    def test_timestamp_units_leap_year_and_decimal_text_preserved(self):
        for month, count in (('2024-02', 29), ('2025-01', 31)):
            blob, checksum = archive_fixture(month)
            rows = normalize_archive(blob, checksum, month)
            self.assertEqual(len(rows), count)
            self.assertEqual(rows[0][1], '100.00')
            self.assertEqual(rows[-1][0] - rows[0][0], (count - 1) * 86400)

    def test_tampered_archive_and_wrong_checksum_filename_rejected(self):
        blob, checksum = archive_fixture('2025-01')
        for bad_blob, bad_checksum in ((blob + b'x', checksum),
                                       (blob, checksum.replace(b'2025-01', b'2025-02'))):
            with self.assertRaises(ValueError):
                normalize_archive(bad_blob, bad_checksum, '2025-01')

    def test_missing_duplicate_bad_close_and_invalid_prices_rejected(self):
        def missing(rows):
            rows.pop()
        def duplicate(rows):
            rows[1] = rows[0]
        def close_time(rows):
            rows[0][6] += 1
        def price(rows):
            rows[0][2] = '80'
        def nan(rows):
            rows[0][4] = 'NaN'
        for mutate in (missing, duplicate, close_time, price, nan):
            blob, checksum = archive_fixture('2025-01', mutate)
            with self.assertRaises(ValueError):
                normalize_archive(blob, checksum, '2025-01')

    def test_collection_is_replayable_immutable_and_detects_normalized_tampering(self):
        blob, checksum = archive_fixture('2025-01')
        def fetch(url):
            return (checksum if url.endswith('.CHECKSUM') else blob), {
                'fetched_at_utc': '2026-10-21T06:00:00+00:00', 'etag': None, 'last_modified': None}
        with tempfile.TemporaryDirectory() as temp, patch('strategies.history.download', side_effect=fetch):
            path = Path(temp) / 'version'
            collect(path, '2025-01', '2025-01')
            candles, manifest = verify_dataset(path)
            self.assertEqual(len(candles), 31)
            self.assertEqual(manifest['quote_asset'], 'USDT')
            with self.assertRaises(FileExistsError):
                collect(path, '2025-01', '2025-01')
            content = (path / 'ohlc.csv').read_bytes()
            (path / 'ohlc.csv').write_bytes(content.replace(b'105.00', b'104.00', 1))
            with self.assertRaises(ValueError):
                verify_dataset(path)

    def test_failed_download_does_not_publish_partial_dataset(self):
        with tempfile.TemporaryDirectory() as temp, patch('strategies.history.download', side_effect=OSError('offline')):
            path = Path(temp) / 'version'
            with self.assertRaises(OSError):
                collect(path, '2025-01', '2025-01')
            self.assertFalse(path.exists())

    def test_year_slices_use_prior_equity_and_compound_to_whole_path(self):
        result = annual_metrics(['2024-12-30', '2024-12-31', '2025-01-01', '2025-01-02'],
                                [9000, 11000, 12100, 9900])
        self.assertEqual(result[0]['metrics']['return_pct'], 10)
        self.assertEqual(result[1]['metrics']['return_pct'], -10)
        self.assertEqual(result[1]['metrics']['days'], 2)
        self.assertAlmostEqual((1.1 * 0.9 - 1) * 100, -1)
        with self.assertRaises(ValueError):
            annual_metrics(['2025-01-01'], [])

    def test_frozen_protocol_rejects_candidate_window_and_cost_changes(self):
        protocol = json.loads((Path(__file__).resolve().parents[1] / 'docs/history_protocol_v1.json').read_text())
        candles = [Candle(0, 100, 100, 100, 100)]
        for key, value in (('train_bars', 126), ('fee_per_side', 0), ('candidates', [])):
            changed = copy.deepcopy(protocol)
            changed[key] = value
            with self.assertRaises(ValueError):
                study(candles, changed)

    def test_month_range_crosses_year_and_rejects_inverted_range(self):
        self.assertEqual(months('2024-12', '2025-02'), ['2024-12', '2025-01', '2025-02'])
        with self.assertRaises(ValueError):
            months('2025-02', '2024-12')

    def test_saved_dataset_and_report_rebuild_offline_from_another_directory(self):
        root = Path(__file__).resolve().parents[1]
        dataset = root / 'data/history/binance_btcusdt_1d_v1'
        candles, manifest = verify_dataset(dataset)
        self.assertEqual(len(candles), 3195)
        self.assertEqual(len(manifest['archives']), 105)
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, 'PYTHONPATH': str(root), 'PYTHONUTF8': '1'}
            subprocess.run([sys.executable, '-m', 'strategies.history_study', '--output-root', temp],
                           cwd=temp, env=env, check=True, stdout=subprocess.DEVNULL)
            for relative in ('data/history_study_v1.json', 'docs/长期历史验证报告.md'):
                self.assertEqual((Path(temp) / relative).read_bytes(), (root / relative).read_bytes())
            result = json.loads((Path(temp) / 'data/history_study_v1.json').read_text(encoding='utf-8'))
            self.assertEqual(len(result['daily_equity']), 2830)
            self.assertEqual(len(result['folds']), 45)
            self.assertEqual(result['daily_equity'][0]['date'], '2019-01-01')
            for key in result['aggregate']:
                capital = 1
                for phase in result['calendar_phases'][key]:
                    capital *= 1 + phase['metrics']['return_pct'] / 100
                self.assertAlmostEqual(capital - 1, result['aggregate'][key]['return_pct'] / 100, places=4)
