"""New synthetic leakage diagnostics tests; no production or historical input."""
from dataclasses import replace
import math
import unittest

from leakage_diagnostics import (LeakageError, Preprocess, Sample,
                                 check_future_perturbation, diagnose, require_clean)


def sample(key, decision):
    return Sample(key, decision, decision - 1, decision, decision - 1,
                  decision + 1, decision + 3, decision + 4)


class LeakageDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.train = [sample('t1', 10), sample('t2', 20)]
        self.val = [sample('v1', 30), sample('v2', 40)]
        self.pre = Preprocess(('t1', 't2'), 24)

    def check(self, code, train=None, val=None, pre=None, gap=7):
        train = self.train if train is None else train
        val = self.val if val is None else val
        pre = self.pre if pre is None else pre
        codes = {x.code for x in diagnose(train, val, pre, gap=gap)}
        self.assertIn(code, codes)
        with self.assertRaises(LeakageError):
            require_clean(train, val, pre, gap=gap)

    def test_clean_boundary_and_deterministic(self):
        require_clean(self.train, self.val, self.pre, gap=7)
        self.assertEqual(diagnose(self.train, self.val, self.pre, gap=7), ())
        self.assertEqual(diagnose(self.train, self.val, self.pre, gap=0), ())

    def test_feature_and_availability_after_decision(self):
        v = [replace(self.val[0], feature_event=31, feature_available=31), self.val[1]]
        self.check('feature_after_decision', val=v)
        self.check('feature_availability', val=v)

    def test_availability_before_event_rejected(self):
        v = [replace(self.val[0], feature_available=28), self.val[1]]
        self.check('feature_availability', val=v)

    def test_rolling_future_observation(self):
        v = [replace(self.val[0], window_last_observation=31), self.val[1]]
        self.check('rolling_future_observation', val=v)
        self.check('window_after_feature_event', val=v)

    def test_training_label_crosses_validation_and_overlaps(self):
        t = [self.train[0], replace(self.train[1], label_end=32, label_available=32)]
        self.check('training_label_crosses_validation', train=t)
        self.check('overlapping_labels', train=t)

    def test_adjacent_label_intervals_are_not_overlap(self):
        t = [self.train[0], replace(self.train[1], label_end=31, label_available=31)]
        codes = {i.code for i in diagnose(t, self.val, self.pre, gap=0)}
        self.assertNotIn('overlapping_labels', codes)
        self.assertIn('training_label_crosses_validation', codes)

    def test_training_label_must_be_available(self):
        t = [self.train[0], replace(self.train[1], label_available=31)]
        self.check('training_label_unavailable', train=t)

    def test_preprocessor_validation_data_rejected(self):
        self.check('preprocess_nontraining_data', pre=Preprocess(('t1', 'v1'), 24))
        self.check('preprocess_nontraining_data', pre=Preprocess((), 24))
        self.check('preprocess_after_validation', pre=Preprocess(('t1',), 31))
        self.check('preprocess_before_data_available', pre=Preprocess(('t2',), 19))

    def test_split_and_gap_boundaries(self):
        self.check('split_order', train=list(reversed(self.train)))
        self.check('split_order', val=list(reversed(self.val)))
        self.check('insufficient_gap', gap=8)

    def test_future_perturbation_and_tolerance(self):
        self.assertEqual(check_future_perturbation([1.0, 2.0], [1.0, 2.0]), ())
        issues = check_future_perturbation([1.0, 2.0], [1.0, 2.5])
        self.assertEqual(issues[0].code, 'future_perturbation_changed_output')
        self.assertEqual(check_future_perturbation([1.0], [1.01], atol=.02), ())
        for a, b, tol in (([], [], 0), ([1], [1, 2], 0), ([math.nan], [1], 0), ([1], [1], -1)):
            with self.subTest(a=a, b=b, tol=tol), self.assertRaises(ValueError):
                check_future_perturbation(a, b, atol=tol)

    def test_invalid_metadata_fails_closed(self):
        with self.assertRaises(ValueError):
            diagnose([], self.val, self.pre, gap=0)
        with self.assertRaises(ValueError):
            diagnose(self.train, self.val, self.pre, gap=-1)
        with self.assertRaises(ValueError):
            diagnose(self.train, [replace(self.val[0], key='t1')], self.pre, gap=0)
        with self.assertRaises(ValueError):
            diagnose(self.train, [replace(self.val[0], label_available=32)], self.pre, gap=0)
        with self.assertRaises(ValueError):
            diagnose(self.train, self.val, Preprocess((['bad'],), 24), gap=0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
