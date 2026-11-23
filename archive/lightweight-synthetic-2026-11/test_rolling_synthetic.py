"""Meaningful synthetic checks of window discipline and identities."""
from dataclasses import replace
import unittest
from unittest import mock

import rolling_synthetic as r
from sample_lineage import pair_losses


class RollingSyntheticTests(unittest.TestCase):
    def test_nonoverlapping_validation_and_purges(self):
        result = r.run_rolling('signal')
        seen = set()
        for w in result['windows']:
            self.assertEqual(len(w['validation_samples']), 30)
            self.assertEqual(len(w['purged_train_ids']), 1)
            self.assertEqual(len(w['purged_tune_ids']), 1)
            ids = {s.sample_id for s in w['validation_samples']}
            self.assertFalse(ids & seen)
            seen.update(ids)
            self.assertLessEqual(w['train_samples'][-1].target_end_time + 1,
                                 w['tune_samples'][0].decision_time)
            self.assertLessEqual(w['tune_samples'][-1].target_end_time + 1,
                                 w['validation_samples'][0].decision_time)
        self.assertEqual(len(seen), 90)
        self.assertTrue(result['earlier_validation_reused_for_later_training'])

    def test_four_scenarios_reproducible_with_distinct_fingerprints(self):
        fingerprints = set()
        for name in ('signal', 'no_signal', 'high_noise', 'distribution_shift'):
            with self.subTest(scenario=name):
                x = r.run_rolling(name)
                self.assertEqual(x, r.run_rolling(name))
                fingerprints.add(x['dataset_fingerprint'])
                self.assertEqual(len(x['windows']), 3)
                for w in x['windows']:
                    self.assertIn(w['selected_alpha'], r.ALPHAS)
                    self.assertEqual(set(w['ranking']), {'ridge', 'mean', 'persistence'})
        self.assertEqual(len(fingerprints), 4)

    def test_actual_paired_losses_have_matching_sample_identity(self):
        result = r.run_rolling('signal')
        for w in result['windows']:
            a,b = pair_losses(w['validation_samples'], w['losses']['ridge'], w['losses']['mean'],
                    expected_dataset_id=result['dataset_id'], expected_dataset_fingerprint=result['dataset_fingerprint'],
                    expected_config_hash=result['config_hash'], split_id='validation', window_id=w['window_id'],
                    model_a='ridge', model_b='mean')
            self.assertEqual(len(a), 30)
            self.assertAlmostEqual(sum(x-y for x,y in zip(a,b))/len(a), w['ridge_minus_mse']['mean'])

    def test_invalid_overlapping_window_or_zero_training_rejected(self):
        a = r.DEFAULT_WINDOWS[0]
        for broken in ((a, replace(r.DEFAULT_WINDOWS[1], validation_start=119)),
                       (replace(a, train_stop=0),),
                       (a, replace(r.DEFAULT_WINDOWS[1], window_id=a.window_id))):
            with self.subTest(plan=broken), self.assertRaises(ValueError):
                r.run_rolling('signal', windows=broken)

    def test_purge_keeps_boundary_and_rejects_crossing(self):
        result = r.run_rolling('signal')
        w = result['windows'][0]
        boundary = w['tune_samples'][0].decision_time
        kept, removed = r._purge((w['train_samples'][-1],), boundary, 1)
        self.assertEqual(len(kept), 1)
        crossed = replace(w['train_samples'][-1], target_end_time=boundary)
        self.assertEqual(r._purge((crossed,), boundary, 1)[1], (crossed.sample_id,))

    def test_validation_cannot_be_used_as_tuning(self):
        result = r.run_rolling('signal')
        w = result['windows'][0]
        context = (result['dataset_id'], result['dataset_fingerprint'], result['config_hash'], w['window_id'], None)
        with self.assertRaisesRegex(ValueError, 'split'):
            r._select_alpha(w['train_samples'], w['validation_samples'], {}, context=context)

    def test_first_window_fitting_ignores_its_validation_outcomes(self):
        original = r.generate('signal', seed=r.SEED)
        changed = [dict(row) for row in original]
        for i in range(90, 120):
            changed[i]['target'] += 1000.0
        with mock.patch.object(r, 'generate', return_value=original):
            baseline = r.run_rolling('signal')['windows'][0]
        with mock.patch.object(r, 'generate', return_value=changed):
            perturbed = r.run_rolling('signal')['windows'][0]
        self.assertEqual(baseline['selected_alpha'], perturbed['selected_alpha'])
        self.assertEqual(baseline['alpha_tune_mse'], perturbed['alpha_tune_mse'])
        self.assertEqual(baseline['model'], perturbed['model'])
        self.assertNotEqual(baseline['metrics'], perturbed['metrics'])

    def test_data_fingerprint_changes_on_actual_data_or_seed(self):
        a = r.run_rolling('signal', seed=10)
        b = r.run_rolling('signal', seed=11)
        self.assertNotEqual(a['dataset_fingerprint'], b['dataset_fingerprint'])
        self.assertNotEqual(a['config_hash'], b['config_hash'])

    def test_prediction_ids_bind_different_experiment_configs(self):
        first = r.run_rolling('signal', gap=0)
        second = r.run_rolling('signal', gap=1)
        a = first['windows'][0]['predictions']['ridge'][0]
        b = second['windows'][0]['predictions']['ridge'][0]
        self.assertEqual(first['dataset_fingerprint'], second['dataset_fingerprint'])
        self.assertEqual(a.sample_id, b.sample_id)
        self.assertNotEqual(first['config_hash'], second['config_hash'])
        self.assertNotEqual(a.prediction_id, b.prediction_id)


if __name__ == '__main__': unittest.main(verbosity=2)
