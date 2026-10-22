from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from strategies.derivatives_data import (
    DAY, build_dataset, collect, digest, encode, fetch_public, normalize,
    request_specs, verify,
)
from strategies.market_structure import analyze, run

START = 20_000 * DAY
STOP = START + 3 * DAY
OBSERVED = STOP + 60_000


def fixture():
    raw = {name: [] for name in ('spot', 'oi', 'long_short', 'taker', 'funding')}
    for i in range(3):
        t = START + i * DAY
        raw['spot'].append([t, '100', '120', '90', str(100 + i * 5), '10', t + DAY - 1, '0', 1, '0', '0', '0'])
        raw['oi'].append({'symbol': 'BTCUSDT', 'timestamp': t + DAY,
                          'sumOpenInterest': str(10 + i), 'sumOpenInterestValue': '1000'})
        raw['long_short'].append({'symbol': 'BTCUSDT', 'timestamp': t + DAY,
                                 'longAccount': '0.6', 'shortAccount': '0.4', 'longShortRatio': '1.5'})
        raw['taker'].append({'timestamp': t, 'buyVol': '6', 'sellVol': '4', 'buySellRatio': '1.5'})
        raw['funding'].append({'symbol': 'BTCUSDT', 'fundingTime': t, 'fundingRate': '-0.0001'})
    return raw


def dataset(raw=None):
    raw = {k: encode(v) for k, v in (raw or fixture()).items()}
    manifest = {'schema_version': 1, 'symbol': 'BTCUSDT', 'start_ms': START,
                'stop_ms': STOP, 'completed_at_ms': OBSERVED, 'sources': {}}
    for name, url in request_specs(START, STOP):
        manifest['sources'][name] = {'url': url, 'sha256': digest(raw[name]), 'received_at_ms': OBSERVED}
    return build_dataset(manifest, raw)


class DerivativesTests(unittest.TestCase):
    def test_end_and_start_stamps_align_with_same_closed_spot_day(self):
        result = dataset()
        for name in result['metrics']:
            self.assertEqual([r['day_start_ms'] for r in result['metrics'][name]],
                             [START, START + DAY, START + 2 * DAY])
        self.assertEqual(result['metrics']['oi'][0]['timestamp_ms'], START + DAY)
        self.assertEqual(result['metrics']['taker'][0]['timestamp_ms'], START)
        self.assertEqual(result['metrics']['oi'][0]['available_at_ms'], OBSERVED)

    def test_no_backfill_before_actual_observation_time(self):
        data = dataset()
        past = analyze(data, as_of_ms=OBSERVED - 1)
        self.assertTrue(all(r['state'] == '数据不足' and r['close'] is None for r in past['daily']))
        current = analyze(data, as_of_ms=OBSERVED)
        self.assertEqual(current['daily'][1]['state'], '价格与持仓同步增长')
        self.assertAlmostEqual(current['daily'][1]['oi_change'], 0.1)
        self.assertAlmostEqual(current['daily'][1]['taker_imbalance'], 0.2)
        self.assertEqual(current['daily'][0]['observed_funding_rate_sum'], -0.0001)
        self.assertEqual(current['daily'][0]['long_short_account_ratio'], 1.5)

    def test_future_rows_do_not_change_prior_as_of_report(self):
        data = dataset()
        # Only first two days have been observed at this cutoff.
        for rows in data['metrics'].values():
            for row in rows:
                row['available_at_ms'] = OBSERVED if row['day_start_ms'] < START + 2 * DAY else OBSERVED + DAY
        before = analyze(data, as_of_ms=OBSERVED)
        changed = deepcopy(data)
        changed['metrics']['spot'][-1]['close'] = 999999
        changed['metrics']['oi'][-1]['open_interest'] = 999999
        changed['metrics']['funding'][-1]['funding_rate'] = 1
        self.assertEqual(before, analyze(changed, as_of_ms=OBSERVED))

    def test_missing_days_do_not_bridge_changes_or_fill_zero(self):
        raw = fixture()
        raw['oi'].pop(1)
        raw['funding'] = []
        raw['spot'].pop(1)
        data = dataset(raw)
        report = analyze(data, as_of_ms=OBSERVED)
        self.assertIsNone(report['daily'][2]['price_change'])
        self.assertIsNone(report['daily'][2]['oi_change'])
        self.assertIsNone(report['daily'][1]['observed_funding_rate_sum'])
        self.assertEqual(data['quality']['oi']['missing_days'], ['2024-10-05'])

    def test_identical_duplicates_deduplicated_conflicts_rejected(self):
        raw = fixture()['oi']
        raw.append(deepcopy(raw[0]))
        self.assertEqual(len(normalize('oi', encode(raw[::-1]), START, STOP, OBSERVED)), 3)
        raw[-1]['sumOpenInterest'] = '12'
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            normalize('oi', encode(raw), START, STOP, OBSERVED)

    def test_invalid_values_symbols_schema_timestamps_and_future_rejected(self):
        cases = [('oi', 'sumOpenInterest', '-1'), ('oi', 'sumOpenInterest', 'NaN'),
                 ('oi', 'symbol', 'ETHUSDT'), ('oi', 'timestamp', START + 1),
                 ('oi', 'timestamp', START + DAY + 0.5),
                 ('oi', 'timestamp', STOP + DAY), ('oi', 'timestamp', True),
                 ('long_short', 'longAccount', '1.2'), ('funding', 'fundingRate', 'inf'),
                 ('taker', 'buyVol', True)]
        for name, key, value in cases:
            raw = fixture()[name]
            raw[0][key] = value
            with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                normalize(name, encode(raw), START, STOP, OBSERVED)
        with self.assertRaises(ValueError):
            normalize('oi', b'{"code":-1}', START, STOP, OBSERVED)
        raw = fixture()['spot']
        raw[-1][6] += 1
        with self.assertRaises(ValueError):
            normalize('spot', encode(raw), START, STOP, OBSERVED)
        with self.assertRaises(ValueError):
            normalize('taker', encode(fixture()['taker']), START, STOP, STOP - 1)

    def test_retry_after_and_raw_error_evidence(self):
        class Response(BytesIO):
            status = 200
            headers = {}
        outcomes = [HTTPError('url', 429, 'limited', {'Retry-After': '3'}, BytesIO(b'limited')), Response(b'[]')]
        def opener(*args, **kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        delays = []
        raw, attempts = fetch_public('https://example.test', opener=opener, sleep=delays.append, clock=lambda: OBSERVED)
        self.assertEqual(raw, b'[]')
        self.assertEqual(delays, [3])
        self.assertEqual(attempts[0]['body'], b'limited')
        self.assertEqual([a['status'] for a in attempts], [429, 200])

    def test_bans_permanent_errors_and_long_cooldowns_are_not_retried(self):
        for code, cooldown in ((418, '0'), (400, '0'), (429, '120')):
            calls = []
            def opener(*args, **kwargs):
                calls.append(1)
                raise HTTPError('url', code, 'failure', {'Retry-After': cooldown}, BytesIO(b'failure'))
            raw, attempts = fetch_public('https://example.test', opener=opener, sleep=lambda _: self.fail('must not sleep'))
            self.assertIsNone(raw)
            self.assertEqual(len(calls), 1)
            self.assertEqual(attempts[0]['body'], b'failure')

    def test_network_retry_exhaustion(self):
        def opener(*args, **kwargs):
            raise URLError('offline')
        delays = []
        raw, attempts = fetch_public('https://example.test', opener=opener, sleep=delays.append)
        self.assertIsNone(raw)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(delays, [1, 2])

    def collect_fixture(self, root, fail=False):
        responses = {url: encode(fixture()[name]) for name, url in request_specs(START, STOP)}
        def fetcher(url):
            raw = b'blocked' if fail else responses[url]
            return (None if fail else raw), [{'status': 451 if fail else 200, 'body': raw, 'received_at_ms': OBSERVED}]
        with patch('strategies.derivatives_data.time.sleep'):
            return collect(root, days=3, fetcher=fetcher, clock=lambda: OBSERVED)

    def test_immutable_snapshots_raw_integrity_and_deterministic_report_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.collect_fixture(root)
            second = self.collect_fixture(root)
            self.assertNotEqual(first, second)
            data, manifest = verify(first)
            self.assertEqual(data, dataset())
            run(first, root / 'report1')
            run(first, root / 'report2')
            for file in ('report.md', 'report.json'):
                self.assertEqual((root / 'report1' / file).read_bytes(), (root / 'report2' / file).read_bytes())
            with self.assertRaises(FileExistsError):
                run(first, root / 'report1')
            file = first / manifest['sources']['oi']['file']
            file.write_bytes(file.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                verify(first)

    def test_normalized_tampering_and_unsafe_raw_path_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            first = self.collect_fixture(Path(temp))
            (first / 'normalized.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'raw replay'):
                verify(first)
            manifest = json.loads((first / 'manifest.json').read_bytes())
            manifest['sources']['oi']['attempts'][0]['file'] = '../outside'
            (first / 'manifest.json').write_bytes(encode(manifest))
            with self.assertRaisesRegex(ValueError, 'raw path'):
                verify(first)

    def test_failed_collection_preserves_bodies_and_does_not_publish_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'retained for diagnosis'):
                self.collect_fixture(Path(temp), fail=True)
            first = next(Path(temp).iterdir())
            manifest = json.loads((first / 'manifest.json').read_bytes())
            self.assertEqual(manifest['status'], 'failed')
            self.assertEqual(len(list((first / 'raw').iterdir())), 5)
            self.assertFalse((first / 'normalized.json').exists())
            with self.assertRaisesRegex(ValueError, 'incomplete/failed'):
                verify(first)


if __name__ == '__main__':
    unittest.main()
