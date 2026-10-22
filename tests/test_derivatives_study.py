from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from strategies.derivatives_data import DAY, DEFAULT_ROOT, ROOT, encode
from strategies.derivatives_study import (
    PROTOCOL, Versions, bootstrap, features_at, fit, labels_at, load_archive,
    make_samples, predict, run, summarize, walk_forward,
)
from strategies.derivatives_pipeline import run as pipeline


def daily_archive(days=440):
    metrics = {name: [] for name in ('spot', 'oi', 'long_short', 'taker', 'funding')}
    start = 19_000 * DAY
    close = 100.0
    for i in range(days):
        day = start + i * DAY
        observed = day + DAY + 15 * 60_000
        previous = close
        close *= 1 + math.sin(i / 11) * 0.02 + 0.001
        common = {'day_start_ms': day, 'available_at_ms': observed}
        metrics['spot'].append({**common, 'open': previous, 'close': close, 'high': max(previous, close) * 1.01})
        metrics['oi'].append({**common, 'open_interest': 100 + i})
        metrics['long_short'].append({**common, 'long_short_ratio': 1.1 + math.sin(i / 9) * 0.1})
        metrics['taker'].append({**common, 'buy_volume': 6 + math.cos(i / 11), 'sell_volume': 4})
        metrics['funding'].append({**common, 'funding_rate': 0.0001})
    return [{'start_ms': start, 'stop_ms': start + days * DAY, 'metrics': metrics}]


def samples():
    archive = daily_archive()
    return make_samples(archive, archive[0]['stop_ms'] + 2 * DAY)[0]


class DerivativesStudyTests(unittest.TestCase):
    def test_actual_availability_and_nonoverlapping_future_labels(self):
        archive = daily_archive(60)
        rows, counts = make_samples(archive, archive[0]['stop_ms'] + DAY)
        self.assertGreater(len(rows), 3)
        self.assertGreater(counts['missing_labels'], 0)
        for row in rows:
            self.assertLessEqual(row['features_available_at_ms'], row['decision_ms'])
            self.assertGreater(row['labels_available_at_ms'], row['label_end_ms'])
            self.assertEqual(row['decision_ms'] - row['feature_day_ms'], 2 * DAY)
        self.assertTrue(all(a['label_end_ms'] <= b['decision_ms'] for a, b in zip(rows, rows[1:])))

    def test_backfills_and_revisions_are_not_visible_before_observed(self):
        archive = daily_archive(30)
        versions = Versions(archive)
        day = archive[0]['start_ms'] + 10 * DAY
        original = features_at(versions, day + 2 * DAY)
        revised = deepcopy(archive)
        record = deepcopy(revised[0]['metrics']['oi'][10])
        record.update(open_interest=999999, available_at_ms=day + 3 * DAY)
        revised[0]['metrics']['oi'].append(record)
        self.assertEqual(original, features_at(Versions(revised), day + 2 * DAY))
        record['available_at_ms'] = day + DAY + 60 * 60_000
        self.assertNotEqual(original['features'], features_at(Versions(revised), day + 2 * DAY)['features'])
        backfill = deepcopy(archive)
        for rows in backfill[0]['metrics'].values():
            for row in rows:
                row['available_at_ms'] += 100 * DAY
        result, _ = make_samples(backfill, archive[0]['stop_ms'] + DAY)
        self.assertEqual(result, [])

    def test_missing_day_zero_volume_and_unknown_funding_are_not_imputed(self):
        archive = daily_archive(20)
        decision = archive[0]['start_ms'] + 12 * DAY
        archive[0]['metrics']['spot'].pop(7)
        self.assertIsNone(features_at(Versions(archive), decision))
        for metric in ('taker', 'funding'):
            broken = daily_archive(20)
            if metric == 'funding':
                broken[0]['metrics'][metric] = []
            else:
                for row in broken[0]['metrics'][metric]:
                    row.update(buy_volume=0, sell_volume=0)
            self.assertIsNone(features_at(Versions(broken), decision))

    def test_first_observed_outcome_prices_are_fixed_after_revisions(self):
        archive = daily_archive(20)
        day = archive[0]['start_ms'] + 10 * DAY
        before = labels_at(Versions(archive), day, day + 10 * DAY, 0.1)
        row = deepcopy(archive[0]['metrics']['spot'][12])
        row.update(close=9999, available_at_ms=day + 8 * DAY)
        archive[0]['metrics']['spot'].append(row)
        self.assertEqual(before, labels_at(Versions(archive), day, day + 10 * DAY, 0.1))

    def test_future_labels_and_features_do_not_change_earlier_model_or_predictions(self):
        rows = samples()
        forecasts, folds = walk_forward(rows)
        self.assertGreater(len(forecasts), 0)
        self.assertGreaterEqual(len(folds), 2)
        cutoff = folds[0]['cutoff_ms']
        changed = deepcopy(rows)
        for row in changed:
            if row['decision_ms'] >= cutoff:
                row['targets'] = [999, 998, 997]
        new_forecasts, new_folds = walk_forward(changed)
        self.assertEqual(folds[0], new_folds[0])
        for before, after in zip(forecasts, new_forecasts):
            if before['fold_cutoff_ms'] == cutoff:
                self.assertEqual(before['price_prediction'], after['price_prediction'])
                self.assertEqual(before['derivatives_prediction'], after['derivatives_prediction'])
        for row in changed:
            if row['decision_ms'] >= cutoff + PROTOCOL['validation_days'] * DAY:
                row['features'] = [999] * 7
        new_forecasts, _ = walk_forward(changed)
        self.assertEqual([r for r in forecasts if r['fold_cutoff_ms'] == cutoff],
                         [dict(r, targets=o['targets']) for r, o in zip(new_forecasts, forecasts) if r['fold_cutoff_ms'] == cutoff])

    def test_training_purges_unobserved_labels_and_respects_rolling_window(self):
        rows = samples()
        _, folds = walk_forward(rows)
        cutoff = folds[0]['cutoff_ms']
        train_days = folds[0]['train_decisions']
        self.assertTrue(all(cutoff - 252 * DAY <= day < cutoff for day in train_days))
        delayed = deepcopy(rows)
        removed = train_days[-1]
        for row in delayed:
            if row['decision_ms'] == removed:
                row['labels_available_at_ms'] = cutoff + DAY
        _, later = walk_forward(delayed)
        self.assertNotIn(removed, later[0]['train_decisions'])

    def test_baseline_uses_same_test_dates_and_train_only_scaling(self):
        rows = samples()
        forecast, folds = walk_forward(rows)
        fold = folds[0]
        training = [r for r in rows if r['decision_ms'] in fold['train_decisions']]
        model = fit(training, 3)
        self.assertEqual(model, fold['price_model'])
        for row in forecast:
            self.assertEqual(len(row['price_prediction']), 3)
            self.assertEqual(len(row['derivatives_prediction']), 3)
        self.assertTrue(all(math.isfinite(x) for x in predict(model, rows[-1])))

    def test_ridge_constant_columns_and_bootstrap_reproducibility(self):
        train = [{'features': [1] * 7, 'targets': [2, 3, 4]} for _ in range(24)]
        self.assertEqual(predict(fit(train, 7), train[0]), [2, 3, 4])
        self.assertEqual(bootstrap([1] * 12), [1, 1])
        self.assertIsNone(bootstrap([1] * 7))
        self.assertEqual(bootstrap(list(range(10))), bootstrap(list(range(10))))
        result = summarize([{'targets': [0] * 3, 'price_prediction': [2] * 3, 'derivatives_prediction': [1] * 3}] * 10)
        self.assertEqual(result['future_volatility']['mse_improvement'], 3)

    def test_invalid_protocol_duplicates_and_future_features_rejected(self):
        rows = samples()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            walk_forward(rows + [rows[0]])
        rows[0]['features_available_at_ms'] = rows[0]['decision_ms'] + 1
        with self.assertRaisesRegex(ValueError, 'availability'):
            walk_forward(rows)
        with tempfile.TemporaryDirectory() as temp:
            protocol = Path(temp) / 'protocol.json'
            protocol.write_bytes(encode(dict(PROTOCOL, train_days=100)))
            with self.assertRaisesRegex(ValueError, 'protocol changed'):
                run(temp, protocol_path=protocol)

    def test_empty_archive_creates_honest_reproducible_waiting_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = run(root, root / 'one', as_of_ms=1_800_000_000_000)
            run(root, root / 'two', as_of_ms=1_800_000_000_000)
            self.assertEqual(report['status'], 'insufficient_data')
            self.assertEqual(report['summary']['future_volatility']['samples'], 0)
            for name in ('study.json', 'study.md'):
                self.assertEqual((root / 'one' / name).read_bytes(), (root / 'two' / name).read_bytes())
            with self.assertRaises(FileExistsError):
                run(root, root / 'one')

    def test_committed_initial_report_replays_from_source_evidence(self):
        saved = ROOT / 'data' / 'derivatives' / 'study_v1_initial'
        original = json.loads((saved / 'study.json').read_bytes())
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'replay'
            run(DEFAULT_ROOT, output, as_of_ms=original['as_of_ms'])
            self.assertEqual(json.loads((output / 'study.json').read_bytes()), original)
            self.assertEqual((output / 'study.md').read_text(encoding='utf-8'),
                             (saved / 'study.md').read_text(encoding='utf-8'))

    def test_complete_snapshot_corruption_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshot'
            path.mkdir()
            (path / 'manifest.json').write_bytes(encode({'status': 'complete'}))
            with self.assertRaises(KeyError):
                load_archive(temp, 1_800_000_000_000)

    def test_pipeline_retains_failure_runs_study_and_suppresses_unchanged_state(self):
        def blocked(root):
            target = root / str(len(list(root.iterdir())))
            target.mkdir()
            (target / 'manifest.json').write_bytes(encode({'status': 'failed', 'started_at_ms': 100,
                'sources': {'oi': {'attempts': [{'status': 451}]}}}))
            raise ValueError('HTTP 451 evidence retained')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = pipeline(root, collector=blocked, clock=lambda: 100)
            second = pipeline(root, collector=blocked, clock=lambda: 100)
            self.assertEqual(first['collection'], 'failed')
            self.assertEqual(first['study'], 'insufficient_data')
            self.assertEqual(first['http_statuses'], [451])
            self.assertTrue(first['meaningful_change'])
            self.assertFalse(second['meaningful_change'])
            self.assertFalse((root / 'RUNNING').exists())
            (root / 'RUNNING').write_text('another run')
            with self.assertRaises(FileExistsError):
                pipeline(root, collector=blocked)


if __name__ == '__main__':
    unittest.main()
