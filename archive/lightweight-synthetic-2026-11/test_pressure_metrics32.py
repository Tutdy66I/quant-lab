import copy
import unittest

from pressure_metrics32 import events_summary, recovery, score
from synthetic_paths32 import common_times, make_case


class PressureMetricsTests(unittest.TestCase):
    def test_factorial_changes_only_intended_inputs(self):
        a, full, _ = make_case('reverse_full_short', 61000)
        b, small, _ = make_case('reverse_small_short', 61000)
        c, _, _ = make_case('reverse_full_long', 61000)
        self.assertEqual((full, small), (45, 18))
        self.assertEqual([r['label_available_time'] for r in a], [r['label_available_time'] for r in b])
        self.assertEqual([r['label_available_time'] for r in a[:60]], [r['label_available_time'] for r in c[:60]])
        self.assertEqual(common_times(a, c), set(range(60, 144)))

    def test_latent_path_mismatch_and_empty_pairs_rejected(self):
        a, _, _ = make_case('reverse_full_short', 61000)
        b, _, _ = make_case('reverse_full_long', 61001)
        with self.assertRaises(ValueError):
            common_times(a, b)
        b = copy.deepcopy(a)
        for row in b:
            row['label_available_time'] = None
        with self.assertRaises(ValueError):
            common_times(a, b)

    def ledger(self, t0, t1):
        return {'prediction_start': t0, 'horizon': 180,
                'predictions': [{'strategy': 'a', 'sample_id': str(t), 'decision_time': t}
                                for t in range(t0, t1)],
                'feedback': [{'strategy': 'a', 'sample_id': str(t), 'label_time': t+3,
                              'squared_loss': 1., 'absolute_loss': 1.} for t in range(t0, t1)],
                'events': [{'strategy': 'a', 'event_type': 'activation', 'version': 1}],
                'metrics': {'a': {'candidate_fit_attempts': 1}}}

    def test_cost_is_normalized_and_separate(self):
        run = self.ledger(60, 62)
        run['feedback'][1].update(squared_loss=9., absolute_loss=3.)
        result = score(run, 'a', {60, 61}, .02)
        self.assertEqual(result['mse'], 5.)
        self.assertEqual(result['average_cost'], .01)
        self.assertAlmostEqual(result['objective'], 5.01)
        with self.assertRaises(ValueError):
            score(run, 'a', {62}, .02)

    def test_non_events_remain_in_denominator(self):
        out = events_summary([{'delay': 10, 'cap': 89, 'status': 'observed'},
                              {'delay': None, 'cap': 89, 'status': 'censored'}])
        self.assertEqual(out['not_observed'], 1)
        self.assertEqual(out['mean_capped_delay'], 49.5)

    def test_recovery_uses_earliest_arrival_even_if_first_block_arrives_late(self):
        run = self.ledger(60, 120)
        run['feedback'][30]['label_time'] = 175
        self.assertEqual(recovery(run, 'a', set(range(60, 120)), 90)['delay'], 32)
        self.assertEqual(recovery(run, 'a', set(range(90, 120)), 90)['status'], 'insufficient_baseline')
