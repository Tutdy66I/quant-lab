"""Synthetic comparison tests; no market requests or account state."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest import mock

from research import ai_baselines as b
from research import ai_offline as o
from strategies import research
from strategies.market_data import parse_daily_rows


def rows():
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    result = []
    for index in range(90):
        stamp = start + timedelta(days=index)
        opening = 100 + index * 0.5
        close = opening * (1.01 if (index // 8) % 2 else 0.99)
        result.append({'timestamp': int(stamp.timestamp()), 'open': opening,
                       'high': max(opening, close) + 1, 'low': min(opening, close) - 1,
                       'close': close, 'available_at_utc': (stamp + timedelta(days=1)).isoformat()})
    return result


class SyntheticBaselineTests(unittest.TestCase):
    def compare(self, dataset=None, **kwargs):
        blob = o.canonical(rows() if dataset is None else dataset)
        options = {'dataset_kind': 'synthetic', 'start_index': 61,
                   'as_of_utc': '2021-01-01T00:00:00Z', 'expected_sha256': o.sha(blob)}
        options.update(kwargs)
        return b.compare_synthetic(blob, **options)

    def test_ma_and_buy_hold_share_original_engine_and_costs(self):
        with mock.patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = self.compare()
        candles = parse_daily_rows([[r[k] for k in ('timestamp', 'open', 'high', 'low', 'close')] for r in rows()])
        original = research.run_backtest(candles, research.Config(), 61)
        self.assertEqual(result['metrics']['strategy_final_equity']['ma20_60'], original['equity'][-1])
        self.assertAlmostEqual(result['metrics']['strategy_final_equity']['buy_hold'],
                               original['summary']['buy_hold_pct'] * 100 + research.INITIAL_CASH, places=1)
        self.assertEqual(result['protocol']['fee_per_side'], 0.001)
        self.assertEqual(result['protocol']['slippage_per_side'], 0.0005)
        self.assertEqual(result['metrics']['sample_count'], 29)
        self.assertEqual(result['research_admission'], 'insufficient_evidence')
        self.assertFalse(result['formal_statistics_enabled'])

    def test_future_perturbation_does_not_change_prior_forecasts(self):
        original = self.compare()
        altered = deepcopy(rows()); altered[-1]['close'] *= 1.005
        altered[-1]['high'] = max(altered[-1]['open'], altered[-1]['close']) + 1
        revised = self.compare(altered)
        self.assertEqual(original['features'], revised['features'])
        self.assertEqual(original['predictions'], revised['predictions'])
        self.assertNotEqual(original['provenance']['labels_sha256'], revised['provenance']['labels_sha256'])
        self.assertNotEqual(original['provenance']['source_sha256'], revised['provenance']['source_sha256'])

    def test_single_prefix_is_independent_of_future_broken_content(self):
        decision = '2020-03-02T00:00:00Z'
        options = dict(dataset_kind='synthetic', decision_at_utc=decision, max_age_seconds=86400)
        with mock.patch('socket.socket', side_effect=AssertionError('network forbidden')):
            expected = b.predict_synthetic_at(rows(), **options)
            damaged = deepcopy(rows()); damaged[-1]['high'] = -1
            damaged[-1]['close'] = 'broken'; damaged[-1]['available_at_utc'] = 'unknown'
            self.assertEqual(expected, b.predict_synthetic_at(damaged, **options))
        self.assertEqual(expected['features']['status'], 'fresh')
        self.assertEqual(expected['scope'], 'synthetic_prefix_prediction_only')
        with self.assertRaises(ValueError):
            self.compare(damaged)
        damaged[-1]['timestamp'] = 'unknown'
        with self.assertRaises(ValueError):
            b.predict_synthetic_at(damaged, **options)

    def test_prefix_refuses_damaged_used_data_and_missing_latest(self):
        decision = '2020-03-02T00:00:00Z'
        options = dict(dataset_kind='synthetic', decision_at_utc=decision, max_age_seconds=172800)
        damaged = deepcopy(rows()); damaged[60]['close'] = 'invalid'
        with self.assertRaises(ValueError):
            b.predict_synthetic_at(damaged, **options)
        missing = rows(); del missing[60]
        result = b.predict_synthetic_at(missing, **options)
        self.assertEqual(result['features']['status'], 'missing_latest')
        self.assertNotIn('predictions', result)

    def test_prefix_invariant_for_unavailable_revisions_and_fields(self):
        options = dict(dataset_kind='synthetic', decision_at_utc='2020-03-02T00:00:00Z', max_age_seconds=86400)
        original = rows()
        expected = b.predict_synthetic_at(original, **options)
        revision = deepcopy(original[60]); revision['available_at_utc'] = '2020-03-03T00:00:00Z'
        for value in (999, 'broken', None):
            changed = deepcopy(revision); changed['close'] = value; changed.pop('high')
            self.assertEqual(expected, b.predict_synthetic_at(original + [changed], **options))
        later = deepcopy(original); later[60]['available_at_utc'] = '2020-03-03T00:00:00Z'
        missing = b.predict_synthetic_at(later, **options)
        del later[60]['close']
        self.assertEqual(missing, b.predict_synthetic_at(later, **options))
        self.assertEqual(missing['features']['status'], 'missing_latest')
        visible_duplicate = deepcopy(original[60])
        with self.assertRaisesRegex(ValueError, 'Duplicate visible'):
            b.predict_synthetic_at(original + [visible_duplicate], **options)
        unknown = deepcopy(original); unknown[60]['available_at_utc'] = 'unknown'
        with self.assertRaises(ValueError):
            b.predict_synthetic_at(unknown, **options)
        bad = deepcopy(original); bad[60]['high'] = 0
        with self.assertRaisesRegex(ValueError, 'OHLC'):
            b.predict_synthetic_at(bad, **options)
        with self.assertRaises(ValueError):
            self.compare(bad)

    def test_lineage_v2_records_direct_dependencies_and_rejects_legacy_replay(self):
        report = self.compare()
        self.assertEqual(report['schema_version'], 2)
        self.assertEqual(report['model']['offline_sha256'], b._file_hash(o))
        from strategies import market_data
        self.assertEqual(report['model']['market_data_sha256'], b._file_hash(market_data))
        legacy = deepcopy(report); legacy['schema_version'] = 1
        with self.assertRaisesRegex(ValueError, 'original evaluator'):
            b.verify_synthetic_report(o.canonical(rows()), legacy)

    def test_overflow_is_not_a_valid_prefix_or_complete_report(self):
        dataset = deepcopy(rows())
        for index, value in ((58, 1e-308), (59, 1e-308), (60, 1e308)):
            dataset[index].update(open=value, high=value, low=value, close=value)
        with self.assertRaisesRegex(ValueError, 'Finite'):
            b.predict_synthetic_at(dataset, dataset_kind='synthetic',
                                   decision_at_utc='2020-03-02T00:00:00Z', max_age_seconds=86400)
        with self.assertRaises(ValueError):
            self.compare(dataset)

    def test_complete_report_rejects_boolean_and_string_prices(self):
        for value in (True, '100'):
            dataset = deepcopy(rows())
            dataset[61].update(open=value, low=1, high=200, close=100)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.compare(dataset)

    def test_persistent_positive_signal_matches_buy_hold_with_same_costs(self):
        dataset = deepcopy(rows())
        for index, row in enumerate(dataset):
            price = 100 + index
            row.update(open=price, close=price, high=price + 1, low=price - 1)
        result = self.compare(dataset)
        equity = result['metrics']['strategy_final_equity']
        expected = 10000 * (1 - 0.001) / (161 * (1 + 0.0005)) * 189 * (1 - 0.0005) * (1 - 0.001)
        self.assertAlmostEqual(equity['previous_return_positive_long'], expected)
        self.assertAlmostEqual(equity['previous_return_positive_long'], equity['buy_hold'])
        self.assertAlmostEqual(result['metrics']['forecast_mse']['zero'], 0.0)

    def test_missing_availability_or_future_labels_fail(self):
        late = deepcopy(rows()); late[60]['available_at_utc'] = '2020-03-02T00:00:01Z'
        with self.assertRaisesRegex(ValueError, 'not available'):
            self.compare(late)
        with self.assertRaisesRegex(ValueError, 'not yet available'):
            self.compare(as_of_utc='2020-03-15T00:00:00Z')

    def test_input_lineage_chain_and_external_predictions_are_not_proof(self):
        first = self.compare(); second = self.compare()
        self.assertEqual(first, second)
        for key in ('source_sha256', 'feature_config_sha256', 'features_sha256', 'model_sha256',
                    'predictions_sha256', 'labels_sha256', 'metrics_sha256'):
            self.assertEqual(len(first['provenance'][key]), 64)
        self.assertTrue(all('target' not in row for row in first['predictions']))
        self.assertIsNone(first['provenance']['training_sample_sha256'])
        self.assertFalse(first['provenance']['execution_proven'])
        payload = dict(first); recorded = payload.pop('report_payload_sha256')
        self.assertEqual(recorded, o.sha(o.canonical(payload)))
        verified = b.verify_synthetic_report(o.canonical(rows()), first)
        self.assertEqual(verified['status'], 'internally_recomputed')
        self.assertFalse(verified['execution_proven'])
        self.assertEqual(verified['source_authenticity'], 'unverified')
        forged = deepcopy(first); forged['predictions'][0]['previous_return'] = 0.5
        forged_payload = dict(forged); forged_payload.pop('report_payload_sha256')
        forged['report_payload_sha256'] = o.sha(o.canonical(forged_payload))
        with self.assertRaisesRegex(ValueError, 'recomputation'):
            b.verify_synthetic_report(o.canonical(rows()), forged)
        with self.assertRaises(ValueError):
            b.verify_synthetic_report(b'wrong source bytes', first)
        changed = self.compare()
        changed['protocol']['fee_per_side'] = 0
        self.assertEqual(b.PROTOCOL['fee_per_side'], 0.001)
        with self.assertRaises(ValueError):
            b.verify_synthetic_report(o.canonical(rows()), changed)
        external = b.external_prediction_record(b'{"prediction": 1}')
        self.assertEqual(external['lineage_status'], 'unverified_external')
        self.assertFalse(external['execution_proven'])
        self.assertFalse(external['eligible_for_formal_research'])

    def test_bad_kind_hash_and_invalid_bars_are_rejected(self):
        with self.assertRaises(ValueError):
            self.compare(dataset_kind='market')
        with self.assertRaises(ValueError):
            self.compare(expected_sha256='0' * 64)
        invalid = deepcopy(rows()); invalid[20]['timestamp'] += 86400
        with self.assertRaises(ValueError):
            self.compare(invalid)
        with self.assertRaises(ValueError):
            self.compare(start_index=60)


if __name__ == '__main__':
    unittest.main()
