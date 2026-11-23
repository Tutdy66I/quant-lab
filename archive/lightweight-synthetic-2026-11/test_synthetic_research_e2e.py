"""Integration checks only; no formal research files or test modules."""
import math
import unittest
from dataclasses import replace
from unittest import mock

import synthetic_research as s


class SyntheticResearchIntegration(unittest.TestCase):
    def test_four_scenarios_are_reproducible(self):
        for name in ('signal', 'no_signal', 'high_noise', 'distribution_shift'):
            with self.subTest(scenario=name):
                a = s.run_comparison(name, replicates=100)
                self.assertEqual(a, s.run_comparison(name, replicates=100))
                self.assertEqual(a['iid']['n'], 60)
                self.assertEqual([x['block_length'] for x in a['moving_blocks']], [1, 5, 10])
                self.assertTrue(all(math.isfinite(v['rmse']) for v in a['metrics'].values()))
                self.assertEqual(a['split']['validation'], (180, 239))

    def test_no_bootstrap_if_causal_diagnostic_rejects(self):
        with mock.patch.object(s, 'require_clean', side_effect=ValueError('synthetic causal failure')), \
             mock.patch.object(s, 'run_experiment') as experiment:
            with self.assertRaisesRegex(ValueError, 'causal failure'):
                s.run_comparison('signal', replicates=100)
            experiment.assert_not_called()

    def test_real_diagnostic_rejects_future_feature_before_fitting(self):
        rows = s.declared_timing()
        rows[5] = replace(rows[5], feature_available=rows[5].decision + 1)
        with mock.patch.object(s, 'declared_timing', return_value=rows), \
             mock.patch.object(s, 'run_experiment') as experiment:
            with self.assertRaisesRegex(ValueError, 'feature_availability'):
                s.run_comparison('signal', replicates=100)
            experiment.assert_not_called()

    def test_wrong_sample_order_is_rejected(self):
        bad = {'validation_time': tuple(reversed(range(180, 240)))}
        with mock.patch.object(s, 'run_experiment', return_value=bad):
            with self.assertRaisesRegex(ValueError, 'order'):
                s.run_comparison('signal', replicates=100)
