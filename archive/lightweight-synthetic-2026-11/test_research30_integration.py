"""Cross-module checks on newly generated, exploratory synthetic data only."""
import unittest

import drift_synthetic30 as drift
import robustness_synthetic30 as robust


class SyntheticResearchIntegration30(unittest.TestCase):
    def data(self, scenario):
        rows=robust.generate(scenario)
        monitoring=[{'decision_time':r['decision_time'],
                     'feature_available_time':r['feature_available_time'],
                     'label_available_time':r['target_end_time'],
                     'x':r['features'][1], 'y':r['target']} for r in rows]
        reference=drift.fit_reference(monitoring[:100],as_of_time=200)
        return rows,monitoring,reference

    def test_feature_shift_and_model_comparison_use_same_synthetic_window(self):
        rows,monitor,reference=self.data('feature_shift')
        comparison=robust.evaluate_window(rows,scenario='feature_shift',seed=robust.SEED,
                                           validation_start=140,train_size=80)
        alert=drift.detect_drift(reference,monitor[140:160],as_of_time=monitor[159]['label_available_time'])
        self.assertTrue(alert['mean_alarm']); self.assertTrue(alert['variance_alarm'])
        self.assertEqual(comparison['ranges']['validation'][0],140)
        self.assertEqual(alert['first_decision_time'],rows[140]['decision_time'])

    def test_relationship_shift_requires_available_labels(self):
        _,monitor,reference=self.data('relation_shift')
        window=monitor[140:160]
        before=drift.detect_drift(reference,window,as_of_time=window[-1]['decision_time'])
        self.assertEqual(before['relationship']['status'],'unavailable')
        after=drift.detect_drift(reference,window,as_of_time=window[-1]['label_available_time'])
        self.assertEqual(after['relationship']['status'],'available')
        self.assertTrue(after['relationship']['alarm'])

    def test_fixed_model_fits_do_not_depend_on_later_validation_target(self):
        rows,_,_=self.data('signal')
        original=robust.evaluate_window(rows,scenario='signal',seed=robust.SEED,
                                        validation_start=140,train_size=80)
        rows[149]['target']+=99
        altered=robust.evaluate_window(rows,scenario='signal',seed=robust.SEED,
                                       validation_start=140,train_size=80)
        self.assertEqual(original['selected_alpha'],altered['selected_alpha'])
        self.assertEqual(original['fitted_model'],altered['fitted_model'])


if __name__=='__main__': unittest.main()
