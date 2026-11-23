import unittest

from paths33 import FACTORIAL, factorial_clock, make_case
from run_cross33 import complexity, shadow_count, timed_event


class CrossProtocolTests(unittest.TestCase):
    def test_paired_latent_path_and_identical_warmup_release(self):
        paths = {case: make_case(case, 63100) for case in FACTORIAL}
        self.assertEqual(factorial_clock(paths), set(range(60, 144)))
        warmups = [tuple(r['label_available_time'] for r in rows[:60]) for rows, _ in paths.values()]
        self.assertEqual(len(set(warmups)), 1)
        self.assertEqual([opts['initial_limit'] for _, opts in paths.values()], [27, 27, 45, 45])
        self.assertTrue(all(opts['strict_initial_ridge'] for _, opts in paths.values()))

    def test_mismatched_paired_seed_rejected(self):
        paths = {case: make_case(case, 63100) for case in FACTORIAL}
        paths['full_long'] = make_case('full_long', 63101)
        with self.assertRaises(ValueError):
            factorial_clock(paths)

    def test_denied_shadow_without_future_scoring_is_unavailable(self):
        self.assertEqual(shadow_count([{'decision_time':100}], set(), {}, [], {}, 'x'),
                         {'evaluated':0, 'unavailable':1, 'beneficial':0, 'windows':[]})

    def test_no_response_stays_censored_and_complexity_is_ordered(self):
        self.assertEqual(timed_event(None,90,179), {'delay':None,'cap':89,'status':'censored'})
        self.assertLess(complexity('fixed_ridge'), complexity('original__original'))
        self.assertLess(complexity('original__original'), complexity('confirmed__conservative'))
