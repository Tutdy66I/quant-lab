import json
from pathlib import Path
import tempfile
import unittest

from strategies.cross_source import compare, normalize_response, requests_for, run, verify_reference
from strategies.market_data import Candle


class CrossSourceTests(unittest.TestCase):
    def test_response_mapping_order_boundaries_and_missing_days(self):
        rows = [[i * 86400, 90, 110, 100, 105, 10] for i in range(5)]
        normalized = normalize_response(json.dumps(rows[::-1]).encode(), 86400, 4 * 86400)
        self.assertEqual(normalized, [[i * 86400, 100, 110, 90, 105] for i in range(1, 4)])
        with self.assertRaises(ValueError):
            normalize_response(json.dumps(rows[:2] + rows[3:]).encode(), 86400, 4 * 86400)
        with self.assertRaises(ValueError):
            normalize_response(json.dumps(rows + [rows[1]]).encode(), 86400, 4 * 86400)

    def test_invalid_reference_price_schema_and_fractional_timestamp_rejected(self):
        for bad in ([86400, 110, 90, 100, 105, 10], [86400.5, 90, 110, 100, 105, 10],
                    [86400, 90, 110, 100, float('nan'), 10], [86400, 90]):
            rows = [[0, 90, 110, 100, 105, 10], bad, [172800, 90, 110, 100, 105, 10]]
            with self.assertRaises(ValueError):
                normalize_response(json.dumps(rows).encode(), 0, 259200)

    def test_request_chunks_are_below_api_limit_and_disjoint(self):
        chunks = list(requests_for(0, 3195 * 86400))
        self.assertEqual(len(chunks), 12)
        self.assertEqual(chunks[0][0], 0)
        self.assertEqual(chunks[-1][1], 3195 * 86400)
        self.assertTrue(all(stop - start <= 280 * 86400 for start, stop, _ in chunks))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(chunks, chunks[1:])))

    def test_alerts_are_diagnostic_preserve_dates_and_never_change_prices(self):
        reference = [Candle(i * 86400, 100, 110, 90, 100) for i in range(3)]
        primary = [reference[0], Candle(86400, 100, 120, 90, 102), reference[2]]
        original = list(primary)
        result = compare(primary, reference, {'ohlc_alert_pct': 3, 'close_alert_pct': 1})
        self.assertEqual(result['rows'], 3)
        self.assertEqual(result['ohlc_alert_days'], 1)
        self.assertEqual(result['close_alert_days'], 1)
        self.assertEqual(primary, original)
        self.assertEqual(len(result['daily']), 3)
        with self.assertRaises(ValueError):
            compare(primary, reference[1:], {'ohlc_alert_pct': 3, 'close_alert_pct': 1})

    def test_saved_reference_replays_and_report_rebuild_is_byte_identical(self):
        root = Path(__file__).resolve().parents[1]
        reference = root / 'data/history/coinbase_btcusd_1d_v1'
        candles, manifest = verify_reference(reference)
        self.assertEqual(len(candles), 3195)
        self.assertEqual(len(manifest['chunks']), 12)
        with tempfile.TemporaryDirectory() as temp:
            run(output_root=Path(temp))
            for relative in ('data/cross_source_v1.json', 'docs/第二来源交叉核验.md'):
                self.assertEqual((Path(temp) / relative).read_bytes(), (root / relative).read_bytes())

    def test_reference_tampering_and_missing_chunk_are_rejected(self):
        root = Path(__file__).resolve().parents[1]
        source = root / 'data/history/coinbase_btcusd_1d_v1'
        manifest = json.loads((source / 'manifest.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / 'raw').mkdir()
            first = manifest['chunks'][0]
            (target / 'raw' / first['file']).write_bytes((source / 'raw' / first['file']).read_bytes() + b' ')
            (target / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                verify_reference(target)
            manifest['chunks'].pop()
            (target / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'Missing'):
                verify_reference(target)
