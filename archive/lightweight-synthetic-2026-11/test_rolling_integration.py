"""Cross-module synthetic ID and rolling-bootstrap regression."""
from dataclasses import replace
import unittest

from coverage_synthetic import paired_window_bootstrap
from rolling_synthetic import run_rolling


class RollingIntegration(unittest.TestCase):
    def setUp(self):
        self.run = run_rolling('signal', seed=20261113)
        self.window = self.run['windows'][0]
        self.ctx = dict(expected_dataset_id=self.run['dataset_id'],
                        expected_dataset_fingerprint=self.run['dataset_fingerprint'],
                        expected_config_hash=self.run['config_hash'], split_id='validation',
                        window_id=self.window['window_id'], model_a='ridge', model_b='mean',
                        spacing=2, replicates=100, seed=20261113)

    def test_real_rolling_losses_keep_exact_sample_identity(self):
        s = self.window['validation_samples']
        result = paired_window_bootstrap(s, self.window['losses']['ridge'],
                                         self.window['losses']['mean'], **self.ctx)
        self.assertEqual(result['sample_ids'], tuple(x.sample_id for x in s))
        self.assertAlmostEqual(result['mean_difference'], self.window['ridge_minus_mse']['mean'])

    def test_common_omission_against_full_base_rejected(self):
        s = self.window['validation_samples']
        with self.assertRaises(ValueError):
            paired_window_bootstrap(s, self.window['losses']['ridge'][:-1],
                                    self.window['losses']['mean'][:-1], **self.ctx)

    def test_reordered_loss_rejected_without_sorting(self):
        with self.assertRaises(ValueError):
            paired_window_bootstrap(self.window['validation_samples'],
                                    tuple(reversed(self.window['losses']['ridge'])),
                                    self.window['losses']['mean'], **self.ctx)

    def test_other_dataset_is_not_silently_accepted(self):
        s = self.window['validation_samples']
        corrupted = (replace(s[0], config_hash='0' * 64),) + s[1:]
        with self.assertRaises(ValueError):
            paired_window_bootstrap(corrupted, self.window['losses']['ridge'],
                                    self.window['losses']['mean'], **self.ctx)
