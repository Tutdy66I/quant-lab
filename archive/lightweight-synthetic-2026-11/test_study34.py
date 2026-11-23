"""Negative tests for the Round 34 preregistration and result gate."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine_five34 as engine
from paths33 import make_case
import study34 as study


class StudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = study.read(study.ROOT / 'protocol34.json')
        cls.lock = {
            'protocol': copy.deepcopy(cls.protocol),
            'source_sha256': study.source_hashes(),
            'engine_config': study.json.loads(study.json.dumps(engine.CONFIG)),
            'alarm_thresholds': study.read(study.ROOT / 'round32-alarm-dev-results.json')['thresholds'],
        }
        rows, options = make_case('full_long', 64100)
        cls.run_data = engine.run_online(rows, alarm_config=cls.lock['alarm_thresholds'], **options)
        cls.clock = set(study.by_time(cls.run_data, 'fixed_ridge'))

    def test_locked_protocol_and_engine_parameters(self):
        study.verify_lock(self.protocol, self.lock)
        with patch.dict(engine.CONFIG, cooldown=999):
            with self.assertRaisesRegex(ValueError, 'parameters'):
                study.verify_lock(self.protocol, self.lock)

    def test_replacing_primary_comparison_is_rejected(self):
        modified = dict(self.protocol, original_comparison_cases=['stable_no_signal'])
        with self.assertRaisesRegex(ValueError, 'comparison'):
            study.verify_lock(modified, self.lock)

    def test_current_or_prior_seed_overlap_rejected(self):
        for seed in (self.protocol['report_seeds'][1], 64100, min(study.historical_seeds())):
            with self.subTest(seed=seed):
                modified = copy.deepcopy(self.protocol)
                modified['report_seeds'][0] = seed
                with self.assertRaisesRegex(ValueError, 'seed'):
                    study.verify_lock(modified, dict(self.lock, protocol=modified))

    def test_repeat_reservation_cannot_replace_first_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'reservations'
            study.reserve_seeds(path, [1, 2], 'first')
            with self.assertRaises(FileExistsError):
                study.reserve_seeds(path, [1, 2], 'second')
            self.assertEqual(study.read(path / '1.json')['lock_sha256'], 'first')

    def test_wrong_clock_and_cost_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'scoring clocks'):
            study.summarize('full_long', 64100, self.run_data, self.clock | {999}, self.protocol)
        with self.assertRaisesRegex(ValueError, 'cost mismatch'):
            study.summarize('full_long', 64100, self.run_data, self.clock,
                            dict(self.protocol, cost_per_activation=.04))

    def test_censoring_and_incomplete_evidence(self):
        self.assertEqual(study.timed(None, 90, 179),
                         {'delay': None, 'cap': 89, 'status': 'censored'})
        self.assertEqual(study.timed(179, 90, 179)['status'], 'observed')
        with self.assertRaisesRegex(ValueError, 'missing or duplicate'):
            study.result_summary(self.protocol, [])
        with self.assertRaisesRegex(ValueError, '24'):
            study.estimate([0.] * 23, 4.)
