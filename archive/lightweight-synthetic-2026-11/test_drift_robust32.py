"""Targeted deterministic probes of the robust and confirmation rules."""
import unittest

import drift_robust32 as alarm


class RobustAlarmTests(unittest.TestCase):
    def setUp(self):
        self.x = [float(x)/10 for x in range(-10, 10)]
        self.loss = [0.25]*20
        self.bounds = {'mean': 0.8, 'variance': 2.5, 'relation': 2.0}

    def test_one_feature_or_target_outlier_does_not_fire(self):
        x = self.x[:-1] + [100.0]
        y = self.loss[:-1] + [100.0]
        robust = alarm.evaluate(self.x, x, self.loss, y, self.bounds)
        self.assertEqual(robust, {'mean': False, 'variance': False, 'relation': False})
        # Same contamination easily breaches the original RMS input rule.
        self.assertGreater(sum(y)/len(y), 2.5*sum(self.loss)/len(self.loss))

    def test_sustained_location_dispersion_and_residual_shift_fire(self):
        location = alarm.evaluate(self.x, [x+2 for x in self.x],
                                  self.loss, self.loss, self.bounds)
        dispersion = alarm.evaluate(self.x, [x*3 for x in self.x],
                                    self.loss, self.loss, self.bounds)
        relation = alarm.evaluate(self.x, self.x, self.loss, [4.0]*20, self.bounds)
        self.assertTrue(location['mean'])
        self.assertTrue(dispersion['variance'])
        self.assertTrue(relation['relation'])

    def test_short_pending_label_window_never_asserts_relation(self):
        flags = alarm.evaluate(self.x, [x+2 for x in self.x], self.loss,
                               self.loss[:-1], self.bounds)
        self.assertTrue(flags['mean'])
        self.assertIsNone(flags['relation'])
        short_warmup = alarm.evaluate(self.x, self.x, self.loss[:-1],
                                      [999.0]*20, self.bounds)
        self.assertIsNone(short_warmup['relation'])

    def test_degenerate_dispersion_is_finite_and_explicit(self):
        scores = alarm.score(self.x, [0.0]*20, self.loss, self.loss)
        self.assertIsNone(scores['variance'])
        self.assertTrue(scores['collapsed_feature_iqr'])
        flags = alarm.evaluate(self.x, [0.0]*20, self.loss, self.loss, self.bounds)
        self.assertTrue(flags['variance'])

    def test_confirmation_requires_same_axis_and_fresh_relation_evidence(self):
        conf = alarm.Confirmation()
        relation = {'mean': False, 'variance': False, 'relation': True}
        feature = {'mean': True, 'variance': False, 'relation': False}
        stale = {'mean': True, 'variance': True, 'relation': False}
        self.assertFalse(conf.update(relation)['relation'])
        self.assertFalse(conf.update(relation, fresh=stale)['relation'])
        self.assertTrue(conf.update(relation)['relation'])
        self.assertFalse(conf.update(feature)['mean'])
        self.assertTrue(conf.update(feature)['mean'])
        self.assertFalse(conf.update(relation)['relation'])

    def test_unavailable_axis_resets_confirmation(self):
        conf = alarm.Confirmation()
        available = {'mean': False, 'variance': False, 'relation': True}
        unavailable = dict(available, relation=None)
        conf.update(available)
        self.assertIsNone(conf.update(unavailable)['relation'])
        self.assertFalse(conf.update(available)['relation'])

    def test_invalid_observations_and_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            alarm.score(self.x, [float('inf')]*20, self.loss, self.loss)
        with self.assertRaises(ValueError):
            alarm.score(self.x, self.x, self.loss, [-1.0]*20)
        with self.assertRaises(ValueError):
            alarm.evaluate(self.x, self.x, self.loss, self.loss, {'mean': 0.8})


if __name__ == '__main__':
    unittest.main()
