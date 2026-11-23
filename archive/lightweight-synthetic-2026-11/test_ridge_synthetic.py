"""Independent synthetic references and chronology tests for Ridge."""
import math
import random
import unittest
from unittest import mock

import ridge_synthetic as r


class RidgeMathTests(unittest.TestCase):
    def test_one_feature_closed_form_independent_reference(self):
        # x=(1,2,3), y=2*x+1, mean loss + alpha*w^2, alpha=1.
        # Population scale sqrt(2/3), standardized variance=1, so w=sqrt(2/3).
        model = r.fit_ridge([(1,), (2,), (3,)], [3, 5, 7], 1.0)
        self.assertAlmostEqual(model.means[0], 2.0)
        self.assertAlmostEqual(model.scales[0], math.sqrt(2/3))
        self.assertAlmostEqual(model.weights[0], math.sqrt(2/3))
        self.assertAlmostEqual(model.target_mean, 5.0)
        self.assertAlmostEqual(r.predict_ridge(model, (1,)), 4.0)
        self.assertAlmostEqual(r.predict_ridge(model, (3,)), 6.0)

    def test_correlated_columns_regularized(self):
        model = r.fit_ridge([(1,1), (2,2), (3,3), (4,4)], [2,4,6,8], 0.1)
        self.assertAlmostEqual(model.weights[0], model.weights[1], places=10)
        self.assertTrue(math.isfinite(r.predict_ridge(model, (2.5,2.5))))

    def test_reject_small_shape_nonfinite_constant_and_alpha(self):
        cases = [([(1,), (2,)], [1,2],1), ([(1,), (2,), (3,)], [1,2],1),
                 ([(1,), (2,3), (3,)], [1,2,3],1), ([(1,), (1,), (1,)], [1,2,3],1),
                 ([(1,), (2,), (float('nan'),)], [1,2,3],1), ([(1,), (2,), (3,)], [1,2,3],0),
                 ([(1,), (2,), (3,)], [1,2,True],1), ([(1,), (2,), (3,)], [1,2,3],float('inf'))]
        for x,y,alpha in cases:
            with self.subTest(x=x,y=y,alpha=alpha), self.assertRaises(ValueError):
                r.fit_ridge(x,y,alpha)

    def test_extreme_finite_rejected_when_intermediate_overflows(self):
        with self.assertRaises(ValueError):
            r.fit_ridge([(1e308,), (-1e308,), (1.0,)], [1,2,3], 1.0)

    def test_model_rejects_prediction_shape_and_nonfinite(self):
        model = r.fit_ridge([(1,), (2,), (3,)], [3,5,7],1.0)
        for row in ((1,2), (float('nan'),)):
            with self.subTest(row=row), self.assertRaises(ValueError):
                r.predict_ridge(model,row)

    def test_handcrafted_invalid_models_are_rejected_before_prediction(self):
        cases = [
            r.Model((), (1,), 0.0, (1,), 1.0),
            r.Model((0, 0), (1,), 0.0, (1, 1), 1.0),
            r.Model((0,), (0,), 0.0, (1,), 1.0),
            r.Model((0,), (-1,), 0.0, (1,), 1.0),
            r.Model((0,), (1,), float('nan'), (1,), 1.0),
            r.Model((0,), (1,), 0.0, (float('inf'),), 1.0),
            r.Model((0,), (1,), 0.0, (1,), 0.0),
            r.Model((0,), (1,), 0.0, (1,), True),
            r.Model(None, (1,), 0.0, (1,), 1.0),
        ]
        for model in cases:
            with self.subTest(model=model), self.assertRaises(ValueError):
                r.predict_ridge(model,(1,))


class ChronologicalExperimentTests(unittest.TestCase):
    def test_fixed_seed_replay_and_random_state_is_not_global(self):
        random.seed(127)
        state = random.getstate()
        a = r.run_experiment('signal'); b = r.run_experiment('signal')
        self.assertEqual(a,b)
        self.assertEqual(random.getstate(),state)
        self.assertEqual(a['split'], {'train':(0,119),'tune':(120,179),'validation':(180,239)})
        self.assertEqual(a['validation_time'],tuple(range(180,240)))
        self.assertEqual(a['refit'],'training_only')

    def test_validation_mutation_does_not_change_selection_or_fit(self):
        rows = r.generate('signal')
        changed = [dict(row) for row in rows]
        for row in changed[r.TUNE_END:]:
            row['target'] = row['target'] * 9.0 + 5.0
            row['features'] = (row['features'][0] + 100.0, row['features'][1])
        with mock.patch.object(r,'generate',return_value=rows):
            original = r.run_experiment('signal')
        with mock.patch.object(r,'generate',return_value=changed):
            modified = r.run_experiment('signal')
        self.assertEqual(original['selected_alpha'],modified['selected_alpha'])
        self.assertEqual(original['tune_mse'],modified['tune_mse'])
        self.assertEqual(original['model'],modified['model'])
        self.assertNotEqual(original['metrics'],modified['metrics'])

    def test_baselines_and_paired_losses_share_out_of_sample_rows(self):
        result = r.run_experiment('signal')
        self.assertEqual(set(result['metrics']),{'mean','persistence','ridge'})
        self.assertTrue(all(len(v)==60 for v in result['losses'].values()))
        for i,d in enumerate(result['loss_difference_ridge_minus_mean']):
            self.assertAlmostEqual(d,result['losses']['ridge'][i]-result['losses']['mean'][i])
        for name in result['metrics']:
            self.assertGreaterEqual(result['metrics'][name]['mae'],0)
            self.assertGreaterEqual(result['metrics'][name]['rmse'],0)

    def test_scenarios_are_descriptive_no_forced_advantage(self):
        for scenario in r.SCENARIOS:
            with self.subTest(scenario=scenario):
                result = r.run_experiment(scenario)
                self.assertIn(result['selected_alpha'],r.ALPHAS)
                self.assertEqual(result['conclusion_scope'],'synthetic_descriptive_only')
                self.assertTrue(all(math.isfinite(v['rmse']) for v in result['metrics'].values()))

    def test_unknown_scenario_rejected(self):
        with self.assertRaises(ValueError):
            r.run_experiment('real_market')


if __name__ == '__main__':
    unittest.main(verbosity=2)
