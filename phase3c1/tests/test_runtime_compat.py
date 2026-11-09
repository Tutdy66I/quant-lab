"""Standalone runtime-rejection tests; no network or market inputs."""
import unittest
from unittest.mock import patch
import phase3c1 as p


class RuntimeCompatibilityTests(unittest.TestCase):
    @unittest.skipIf(p.sys.implementation.name == 'cpython' and p.sys.version_info[:3] == (3, 12, 14),
                     'actual-version rejection applies to other interpreters')
    def test_actual_nondesignated_runtime_rejects_before_input(self):
        with patch.object(p, 'capture_synthetic_input', side_effect=AssertionError('input must not be read')) as capture:
            result = p.run_synthetic([], dataset_kind='synthetic', dataset_version='generated',
                                     snapshot_collected_at='2027-01-01T00:00:00Z', protocol_bytes=b'')
            self.assertEqual(result['outcome']['status'], 'failed')
            self.assertEqual(result['outcome']['stage'], 'runtime_validation')
            self.assertNotIn('metrics', result['outcome'])
            capture.assert_not_called()

    def test_nondesignated_versions_fail_before_input_processing(self):
        for version in ((3, 10, 0), (3, 13, 0)):
            with self.subTest(version=version), patch.object(p.sys, 'version_info', version), \
                 patch.object(p, 'capture_synthetic_input', side_effect=AssertionError('input must not be read')) as capture:
                result = p.run_synthetic([], dataset_kind='synthetic', dataset_version='generated',
                                         snapshot_collected_at='2027-01-01T00:00:00Z', protocol_bytes=b'')
                self.assertEqual(result['outcome']['status'], 'failed')
                self.assertEqual(result['outcome']['stage'], 'runtime_validation')
                self.assertEqual(result['outcome']['error_type'], 'ValueError')
                self.assertNotIn('metrics', result['outcome'])
                capture.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'CPython 3.12.14'):
                    p.bootstrap([], [])


if __name__ == '__main__': unittest.main()
