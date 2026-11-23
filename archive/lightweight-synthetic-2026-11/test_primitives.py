"""New synthetic tests of two audited, locally copied repository modules."""
import math
import unittest
from datetime import timezone

from research import ai_offline as off
from strategies import execution as fills


class PureFunctions(unittest.TestCase):
    def test_utc_conversion_and_naive_rejection(self):
        d = off.utc('2024-03-01T08:00:00+08:00')
        self.assertEqual((d.hour, d.tzinfo), (0, timezone.utc))
        with self.assertRaises(ValueError):
            off.utc('2024-03-01T00:00:00')

    def test_numbers_reject_bool_and_nonfinite(self):
        for n in (True, math.inf, -math.inf, math.nan, '1'):
            with self.subTest(value=repr(n)), self.assertRaises(ValueError):
                off.number(n)
        self.assertEqual(off.number(3), 3.0)

    def test_mse_reference(self):
        self.assertAlmostEqual(off.mse([(0, 2), (1, 4), (3, 3)]), 13 / 3)

    def test_serialization_reproducible_and_nonfinite_rejected(self):
        self.assertEqual(off.canonical({'b': 2, 'a': 1}), b'{"a":1,"b":2}')
        self.assertEqual(off.sha(b'abc'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        with self.assertRaises(ValueError):
            off.canonical({'bad': math.nan})

    def test_buy_sell_reference_costs(self):
        p, q, fee = fills.buy_fill(1000.0, 100.0, fee=0.01, slippage=0.02)
        self.assertAlmostEqual(p, 102.0)
        self.assertAlmostEqual(q, 990.0 / 102.0)
        self.assertAlmostEqual(fee, 10.0)
        p2, cash, fee2 = fills.sell_fill(q, 100.0, fee=0.01, slippage=0.02)
        self.assertAlmostEqual(p2, 98.0)
        self.assertAlmostEqual(fee2, q * 98.0 * .01)
        self.assertAlmostEqual(cash, q * 98.0 * .99)

    def test_fill_bad_parameters(self):
        for cash, price, fee in ((0, 1, 0), (1, 0, 0), (1, math.nan, 0), (1, 1, 1), (1, 1, -0.1)):
            with self.subTest(cash=cash, price=price, fee=fee), self.assertRaises(ValueError):
                fills.buy_fill(cash, price, fee=fee)

    def test_crossover_signal_and_warmup(self):
        self.assertEqual(fills.crossover([3, 3, 3, 1, 5], 1, 3, 4), 'buy')
        self.assertEqual(fills.crossover([1, 1, 1, 3, 0], 1, 3, 4), 'sell')
        self.assertEqual(fills.crossover([1, 1, 1, 1], 1, 3, 3), 'hold')
        with self.assertRaises(ValueError):
            fills.crossover([1, 2, 3], 1, 3, 2)


class CausalFeatures(unittest.TestCase):
    @staticmethod
    def rows():
        return [{'end_utc': f'2024-01-0{i}T00:00:00Z',
                 'available_at_utc': f'2024-01-0{i}T00:00:00Z', 'close': 100.0 + i}
                for i in (1, 2, 3)]

    def test_freshness_boundary_and_reference_returns(self):
        rows = self.rows()
        f = off.visible_price_features(rows, '2024-01-03T01:00:00Z', max_age_seconds=3600)
        self.assertEqual(f['status'], 'fresh')
        self.assertAlmostEqual(f['return_1d'], 103 / 102 - 1)
        self.assertAlmostEqual(f['return_2d'], 103 / 101 - 1)
        self.assertEqual(off.visible_price_features(rows, '2024-01-03T01:00:01Z', max_age_seconds=3600)['status'], 'stale')

    def test_future_event_and_unavailable_values_do_not_leak(self):
        original = self.rows()
        baseline = off.visible_price_features(original, '2024-01-03T01:00:00Z', max_age_seconds=3600)
        future = {'end_utc': '2024-01-04T00:00:00Z', 'available_at_utc': 'not-parsed-for-future', 'close': float('nan')}
        self.assertEqual(off.visible_price_features(original + [future], '2024-01-03T01:00:00Z', max_age_seconds=3600), baseline)
        hidden = {'end_utc': '2024-01-03T00:00:00Z', 'available_at_utc': '2024-01-04T00:00:00Z', 'close': float('nan')}
        self.assertEqual(off.visible_price_features(original[:2] + [hidden], '2024-01-03T01:00:00Z', max_age_seconds=3600)['status'], 'stale')

    def test_visible_duplicate_and_missing_day_rejected(self):
        rows = self.rows()
        with self.assertRaises(ValueError):
            off.visible_price_features(rows + [dict(rows[-1])], '2024-01-03T01:00:00Z', max_age_seconds=3600)
        rows[0]['end_utc'] = rows[0]['available_at_utc'] = '2023-12-31T00:00:00Z'
        with self.assertRaises(ValueError):
            off.visible_price_features(rows, '2024-01-03T01:00:00Z', max_age_seconds=3600)

    def test_training_label_boundary(self):
        row = {'decision_at_utc': '2024-01-01T01:00:00Z', 'feature_event_end_utc': '2024-01-01T00:00:00Z',
               'feature_available_at_utc': '2024-01-01T00:30:00Z', 'label_end_utc': '2024-01-03T00:00:00Z',
               'label_available_at_utc': '2024-01-03T01:00:00Z', 'label_value': 0.01}
        self.assertEqual(off.eligible_training([row], '2024-01-03T00:59:59Z'), [])
        self.assertEqual(off.eligible_training([row], '2024-01-03T01:00:00Z'), [row])
        bad = {**row, 'feature_available_at_utc': '2024-01-01T02:00:00Z'}
        with self.assertRaises(ValueError):
            off.eligible_training([bad], '2024-01-03T01:00:00Z')

    def test_csv_backfill_in_memory_and_digest_mismatch(self):
        blob = b'timestamp,open,high,low,close\n1704067200,10,12,9,11\n'
        adapted = off.adapt_ohlc_bytes(blob, expected_sha256=off.sha(blob), collected_at_utc='2024-01-03T00:00:00Z')
        self.assertEqual(adapted['point_in_time_status'], 'unverified')
        rows = adapted['rows']
        self.assertEqual(rows[0]['acquisition_kind'], 'retrospective_snapshot')
        self.assertEqual(rows[0]['available_at_utc'], '2024-01-03T00:00:00+00:00')
        self.assertIsNone(rows[0]['publication_at_utc'])
        with self.assertRaises(ValueError):
            off.adapt_ohlc_bytes(blob, expected_sha256='0' * 64, collected_at_utc='2024-01-03T00:00:00Z')


class AuditedModuleIntegration(unittest.TestCase):
    def row(self):
        return {'decision_at_utc': '2024-01-01T01:00:00Z',
                'feature_event_end_utc': '2024-01-01T00:00:00Z',
                'feature_available_at_utc': '2024-01-01T00:30:00Z',
                'model_fitted_at_utc': '2023-12-31T23:00:00Z',
                'max_train_label_end_utc': '2023-12-31T00:00:00Z',
                'max_train_label_available_at_utc': '2023-12-31T01:00:00Z',
                'entry_at_utc': '2024-01-02T00:00:00Z',
                'label_end_utc': '2024-01-03T00:00:00Z',
                'label_available_at_utc': '2024-01-03T01:00:00Z',
                'prediction': 0.05, 'actual_return': 0.1,
                'entry_price': 100.0, 'exit_price': 110.0, 'position': 'long'}

    def test_synthetic_evaluation_matches_independent_calculation(self):
        result = off.evaluate_synthetic([self.row()], dataset_kind='synthetic', source_bytes=b'synthetic',
                                        as_of_utc='2024-01-03T01:00:00Z', model_id='synthetic-only')
        self.assertEqual(result['samples'], 1)
        self.assertAlmostEqual(result['model_mse'], .0025)
        self.assertAlmostEqual(result['zero_baseline_mse'], .01)
        expected = ((1 - fills.FEE) / (100 * (1 + fills.SLIPPAGE))) * 110 * (1 - fills.SLIPPAGE) * (1 - fills.FEE) - 1
        self.assertAlmostEqual(result['net_returns_per_independent_sample'][0], expected)
        self.assertEqual(result['research_admission'], 'insufficient_evidence')
        self.assertFalse(result['formal_statistics_enabled'])

    def test_future_feature_is_rejected(self):
        row = {**self.row(), 'feature_available_at_utc': '2024-01-01T02:00:00Z'}
        with self.assertRaisesRegex(ValueError, 'timing'):
            off.evaluate_synthetic([row], dataset_kind='synthetic', source_bytes=b'synthetic',
                                   as_of_utc='2024-01-03T01:00:00Z', model_id='synthetic-only')
