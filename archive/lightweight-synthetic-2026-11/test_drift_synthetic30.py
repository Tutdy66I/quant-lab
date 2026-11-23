import copy
import json
import math
import unittest

import drift_synthetic30 as drift


class DriftSyntheticTests(unittest.TestCase):
    def reference_and_window(self, scenario):
        rows=drift.generate(scenario,seed=20261113)
        return drift.fit_reference(rows[:120],as_of_time=120),rows[160:200]

    def test_fixed_synthetic_types_and_unshifted_reference(self):
        expected={'stable':(False,False,False),'mean_shift':(True,False,False),
                  'variance_shift':(False,True,False),'relation_shift':(False,False,True)}
        for scenario, flags in expected.items():
            ref,rows=self.reference_and_window(scenario)
            result=drift.detect_drift(ref,rows,as_of_time=200)
            with self.subTest(scenario=scenario):
                self.assertEqual((result['mean_alarm'],result['variance_alarm'],
                                  result['relationship']['alarm']),flags)

    def test_unavailable_labels_do_not_examine_target(self):
        ref,rows=self.reference_and_window('relation_shift')
        for row in rows: row['y']=object(); row['label_available_time']=999
        result=drift.detect_drift(ref,rows,as_of_time=199)
        self.assertEqual(result['relationship']['status'],'unavailable')
        self.assertIsNone(result['relationship']['alarm'])
        self.assertFalse(result['mean_alarm']); self.assertFalse(result['variance_alarm'])

    def test_missing_one_label_is_not_a_no_drift_claim(self):
        ref,rows=self.reference_and_window('relation_shift');rows[0]['y']=None
        self.assertEqual(drift.detect_drift(ref,rows,as_of_time=200)['relationship']['status'],'unavailable')

    def test_reference_requires_all_labels_known(self):
        rows=drift.generate('stable')
        with self.assertRaises(ValueError):drift.fit_reference(rows[:120],as_of_time=119)

    def test_reference_information_must_precede_monitor_decision(self):
        rows=drift.generate('stable'); ref=drift.fit_reference(rows[:120],as_of_time=160)
        with self.assertRaises(ValueError):drift.detect_drift(ref,rows[120:140],as_of_time=160)

    def test_time_reordering_and_future_features_rejected(self):
        ref,rows=self.reference_and_window('stable')
        with self.assertRaises(ValueError):drift.detect_drift(ref,list(reversed(rows)),as_of_time=200)
        rows[0]['feature_available_time']=rows[0]['decision_time']+1
        with self.assertRaises(ValueError):drift.detect_drift(ref,rows,as_of_time=200)

    def test_reference_overlap_or_future_observation_rejected(self):
        rows=drift.generate('stable'); ref=drift.fit_reference(rows[:120],as_of_time=120)
        with self.assertRaises(ValueError):drift.detect_drift(ref,rows[100:125],as_of_time=125)
        with self.assertRaises(ValueError):drift.detect_drift(ref,rows[160:200],as_of_time=198)

    def test_degenerate_small_nonfinite_and_overflow_inputs_rejected(self):
        rows=drift.generate('stable'); ref=drift.fit_reference(rows[:120],as_of_time=120)
        with self.assertRaises(ValueError):drift.fit_reference(rows[:10],as_of_time=10)
        with self.assertRaises(ValueError):drift.detect_drift(ref,rows[160:165],as_of_time=165)
        for invalid in (math.nan,math.inf,10**1000,True):
            altered=copy.deepcopy(rows[160:200]);altered[2]['x']=invalid
            with self.subTest(invalid=str(invalid)[:20]),self.assertRaises(ValueError):
                drift.detect_drift(ref,altered,as_of_time=200)
        flat=copy.deepcopy(rows[:120])
        for row in flat:row['x']=1.0
        with self.assertRaises(ValueError):drift.fit_reference(flat,as_of_time=120)

    def test_fixed_experiment_reproducible_and_reports_false_alarms(self):
        first=drift.experiment(trials=5,seed=20261113)
        self.assertEqual(first,drift.experiment(trials=5,seed=20261113))
        for scenario in first['results'].values():
            self.assertEqual(len(scenario['records']),5)
            self.assertGreaterEqual(scenario['pre_any_alarm_count'],0)
            self.assertLessEqual(scenario['pre_any_alarm_count'],5)

    def test_change_only_after_declared_time(self):
        stable=drift.generate('stable',seed=88); shift=drift.generate('relation_shift',seed=88)
        self.assertEqual(stable[:160],shift[:160]);self.assertNotEqual(stable[160:],shift[160:])

    def test_collapsed_feature_is_alarm_with_json_safe_undefined_relation(self):
        ref,rows=self.reference_and_window('stable')
        for row in rows: row['x']=0.0
        result=drift.detect_drift(ref,rows,as_of_time=200)
        self.assertTrue(result['variance_alarm']); self.assertIsNone(result['variance_ratio'])
        self.assertEqual(result['relationship']['status'],'undefined_constant_feature')
        json.dumps(result,allow_nan=False)


if __name__=='__main__':unittest.main()
