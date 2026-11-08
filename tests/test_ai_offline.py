"""Synthetic fixtures only; do not read production data or use network."""
from copy import deepcopy
import hashlib
import unittest
from unittest import mock

from research import ai_offline as a


def sample():
    return {'decision_at_utc': '2024-01-04T00:01:00Z',
            'feature_event_end_utc': '2024-01-04T00:00:00Z',
            'feature_available_at_utc': '2024-01-04T00:00:30Z',
            'model_fitted_at_utc': '2024-01-03T23:59:00Z',
            'max_train_label_end_utc': '2024-01-03T20:00:00Z',
            'max_train_label_available_at_utc': '2024-01-03T21:00:00Z',
            'entry_at_utc': '2024-01-04T00:05:00Z',
            'label_end_utc': '2024-01-05T00:00:00Z',
            'label_available_at_utc': '2024-01-05T00:01:00Z',
            'prediction': 0.05, 'actual_return': 0.1,
            'entry_price': 100.0, 'exit_price': 110.0, 'position': 'long'}


class OfflineEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.blob = (b'timestamp,open,high,low,close\n'
                     b'1704067200,100,102,99,101\n'
                     b'1704153600,101,104,100,103\n'
                     b'1704240000,103,106,102,105\n')
        self.digest = hashlib.sha256(self.blob).hexdigest()

    def adapt(self, blob=None, digest=None, collected='2024-02-01T00:00:00Z'):
        return a.adapt_ohlc_bytes(self.blob if blob is None else blob,
                                 expected_sha256=self.digest if digest is None else digest,
                                 collected_at_utc=collected)

    def evaluate(self, rows=None, as_of='2024-01-06T00:00:00Z', kind='synthetic'):
        return a.evaluate_synthetic([sample()] if rows is None else rows, dataset_kind=kind,
                                    source_bytes=b'synthetic fixture', as_of_utc=as_of, model_id='fixture-v1')

    def test_backfill_is_not_visible_before_collection(self):
        snapshot = self.adapt()
        self.assertEqual(snapshot['point_in_time_status'], 'unverified')
        self.assertEqual(snapshot['volume'], 'not_provided')
        self.assertTrue(all(r['acquisition_kind'] == 'retrospective_snapshot' for r in snapshot['rows']))
        self.assertEqual(a.visible_price_features(snapshot['rows'], '2024-01-05T00:00:00Z', max_age_seconds=86400)['status'], 'insufficient_data')
        self.assertEqual(a.visible_price_features(snapshot['rows'], '2024-02-02T00:00:00Z', max_age_seconds=86400)['status'], 'stale')

    def test_fingerprint_gap_schema_and_unfinished_candle_fail_closed(self):
        with self.assertRaises(ValueError):
            self.adapt(digest='0' * 64)
        gap = self.blob.replace(b'1704153600', b'1704326400')
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            self.adapt(gap, hashlib.sha256(gap).hexdigest())
        with self.assertRaisesRegex(ValueError, 'not yet completed'):
            self.adapt(collected='2024-01-03T12:00:00Z')
        bad = self.blob.replace(b'timestamp,open,high,low,close', b'timestamp,open,high,low,volume')
        with self.assertRaises(ValueError):
            self.adapt(bad, hashlib.sha256(bad).hexdigest())

    def test_features_unaffected_by_future_prices_and_reject_missing_day(self):
        rows = self.adapt()['rows']
        for row in rows:
            row['available_at_utc'] = row['end_utc']
        decision = '2024-01-04T00:00:00Z'
        expected = a.visible_price_features(rows, decision, max_age_seconds=86400)
        extra = {'end_utc': '2024-01-05T00:00:00Z', 'available_at_utc': '2024-01-05T00:00:00Z', 'close': 99999}
        self.assertEqual(expected, a.visible_price_features(rows + [extra], decision, max_age_seconds=86400))
        extra['available_at_utc'] = 'not-yet-known'
        self.assertEqual(expected, a.visible_price_features(rows + [extra], decision, max_age_seconds=86400))
        changed = deepcopy(rows)
        changed[1]['end_utc'] = changed[1]['available_at_utc'] = '2024-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'missing'):
            a.visible_price_features(changed, decision, max_age_seconds=86400)

    def test_freshness_boundaries_missing_latest_and_malformed_time(self):
        rows = self.adapt()['rows']
        for row in rows:
            row['available_at_utc'] = row['end_utc']
        def status(decision, age):
            return a.visible_price_features(rows, decision, max_age_seconds=age)['status']
        self.assertEqual(status('2024-01-04T12:00:00Z', 43200), 'fresh')
        self.assertEqual(status('2024-01-04T20:00:00+08:00', 43200), 'fresh')
        self.assertEqual(status('2024-01-04T12:00:01Z', 43200), 'stale')
        self.assertEqual(status('2024-01-05T00:00:00Z', 172800), 'missing_latest')
        self.assertEqual(status('2025-01-01T00:00:00Z', 86400), 'stale')
        for invalid in (-1, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                status('2024-01-04T00:00:00Z', invalid)
        bad = deepcopy(rows); bad[-1]['end_utc'] = 'unknown'
        with self.assertRaises(ValueError):
            a.visible_price_features(bad, '2024-01-04T00:00:00Z', max_age_seconds=86400)

    def test_finite_inputs_cannot_produce_infinite_features_or_metrics(self):
        rows = self.adapt()['rows']
        for row, price in zip(rows, (1e-308, 1e-308, 1e308)):
            row['available_at_utc'] = row['end_utc']; row['close'] = price
        with self.assertRaisesRegex(ValueError, 'Finite'):
            a.visible_price_features(rows, '2024-01-04T00:00:00Z', max_age_seconds=86400)
        row = sample(); row['prediction'] = 1e308
        with self.assertRaisesRegex(ValueError, 'Finite'):
            self.evaluate([row])
        row = sample(); row['entry_price'] = 1e-308; row['exit_price'] = 1e308
        with self.assertRaisesRegex(ValueError, 'Finite'):
            self.evaluate([row])

    def test_unfinished_labels_skip_future_fields_but_finished_labels_are_checked(self):
        cutoff = '2024-01-03T00:00:00Z'
        unfinished = {'decision_at_utc': '2024-01-01T00:00:00Z',
                      'label_end_utc': '2024-01-04T00:00:00Z',
                      'label_available_at_utc': 'invalid future value'}
        self.assertEqual(a.eligible_training([unfinished], cutoff), [])
        broken = deepcopy(unfinished); broken['label_end_utc'] = 'unknown'
        with self.assertRaises(ValueError):
            a.eligible_training([broken], cutoff)
        finished = dict(unfinished, label_end_utc=cutoff,
                        feature_event_end_utc='2023-12-31T00:00:00Z',
                        feature_available_at_utc='2023-12-31T00:00:00Z')
        with self.assertRaises(ValueError):
            a.eligible_training([finished], cutoff)
        finished['label_available_at_utc'] = cutoff
        with self.assertRaises(ValueError):
            a.eligible_training([finished], cutoff)
        finished['label_value'] = float('nan')
        with self.assertRaises(ValueError):
            a.eligible_training([finished], cutoff)
        finished['label_value'] = 0.1
        self.assertEqual(a.eligible_training([finished], cutoff), [finished])
        finished['label_available_at_utc'] = '2024-01-03T00:00:01Z'
        self.assertEqual(a.eligible_training([finished], cutoff), [])

    def test_training_requires_label_end_and_actual_availability(self):
        rows = [{'decision_at_utc': '2024-01-01T00:00:00Z', 'label_end_utc': '2024-01-02T00:00:00Z',
                 'feature_event_end_utc': '2023-12-31T00:00:00Z', 'feature_available_at_utc': '2023-12-31T00:00:00Z',
                 'label_available_at_utc': '2024-01-03T00:00:00Z', 'label_value': 0.02},
                {'decision_at_utc': '2024-01-02T00:00:00Z', 'label_end_utc': '2024-01-04T00:00:00Z',
                 'feature_event_end_utc': '2024-01-01T00:00:00Z', 'feature_available_at_utc': '2024-01-01T00:00:00Z',
                 'label_available_at_utc': '2024-01-04T00:00:00Z'}]
        self.assertEqual(a.eligible_training(rows, '2024-01-03T00:00:00Z'), rows[:1])
        rows.append({'decision_at_utc': '2024-01-04T00:00:00Z', 'label_end_utc': 'not-yet-known'})
        self.assertEqual(a.eligible_training(rows, '2024-01-03T00:00:00Z'), rows[:1])

    def test_matched_mse_costs_and_reproducible_fingerprints(self):
        with mock.patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = self.evaluate()
        self.assertEqual(result, self.evaluate())
        self.assertAlmostEqual(result['model_mse'], 0.0025)
        self.assertAlmostEqual(result['zero_baseline_mse'], 0.01)
        expected = ((1 - 0.001) / (100 * (1 + 0.0005)) * 110 * (1 - 0.0005) * (1 - 0.001)) - 1
        self.assertAlmostEqual(result['net_returns_per_independent_sample'][0], expected)
        self.assertEqual(result['research_admission'], 'insufficient_evidence')
        self.assertFalse(result['formal_statistics_enabled'])
        self.assertEqual(result['source_sha256'], hashlib.sha256(b'synthetic fixture').hexdigest())
        changed = sample(); changed['prediction'] = 0.06
        self.assertNotEqual(result['input_sha256'], self.evaluate([changed])['input_sha256'])

    def test_future_features_training_labels_and_unavailable_outcomes_rejected(self):
        for field, value in [('feature_available_at_utc', '2024-01-04T00:02:00Z'),
                             ('max_train_label_available_at_utc', '2024-01-04T00:00:00Z'),
                             ('entry_at_utc', '2024-01-04T00:01:00Z')]:
            row = sample(); row[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.evaluate([row])
        with self.assertRaises(ValueError):
            self.evaluate(as_of='2024-01-05T00:00:00Z')
        with self.assertRaises(ValueError):
            self.evaluate(kind='market')

    def test_invalid_prices_overlap_and_naive_times_rejected(self):
        row = sample(); row['exit_price'] = float('nan')
        with self.assertRaises(ValueError):
            self.evaluate([row])
        second = deepcopy(sample()); second['decision_at_utc'] = '2024-01-04T00:02:00Z'
        second['entry_at_utc'] = '2024-01-04T00:06:00Z'
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            self.evaluate([sample(), second])
        with self.assertRaises(ValueError):
            a.utc('2024-01-01T00:00:00')


if __name__ == '__main__':
    unittest.main()
