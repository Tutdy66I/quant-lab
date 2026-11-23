import copy
from dataclasses import replace
import unittest

from ridge_synthetic import generate
from rolling_synthetic import _sample
from synthetic_manifest27 import build_manifest, manifest_sha256, verify_manifest, verify_window


class PopulationManifestTests(unittest.TestCase):
    def setUp(self):
        self.rows = generate('signal', 20261113)
        self.dataset_id = 'synthetic-signal'
        self.fingerprint = 'a' * 64
        self.config_hash = 'b' * 64
        # This is created before any prediction or loss.
        self.manifest = build_manifest(self.rows, dataset_id=self.dataset_id,
                        dataset_fingerprint=self.fingerprint, config_hash=self.config_hash)
        self.anchor = manifest_sha256(self.manifest)
        self.samples = tuple(_sample(row, self.dataset_id, self.fingerprint, self.config_hash,
                                     'validation', 'w0') for row in self.rows[90:120])

    def full(self, rows=None, manifest=None, digest=None, source='preapproved:test'):
        return verify_manifest(self.rows if rows is None else rows,
            self.manifest if manifest is None else manifest,
            expected_manifest_sha256=self.anchor if digest is None else digest,
            anchor_source=source)

    def window(self, samples=None, rows=None, manifest=None, digest=None):
        return verify_window(self.samples if samples is None else samples,
            self.rows if rows is None else rows, self.manifest if manifest is None else manifest,
            expected_manifest_sha256=self.anchor if digest is None else digest,
            anchor_source='preapproved:test', start=90, stop=120, window_id='w0')

    def test_valid_population_and_window(self):
        self.assertEqual(self.full()['sample_count'], 240)
        self.assertEqual(self.window()['stop'], 120)
        self.assertEqual(len(self.manifest['ordered_sample_ids_sha256']), 64)

    def test_joint_truncation_and_regenerated_manifest_rejected(self):
        rows = self.rows[:-1]
        changed = build_manifest(rows, dataset_id=self.dataset_id,
                                 dataset_fingerprint=self.fingerprint, config_hash=self.config_hash)
        with self.assertRaises(ValueError): self.full(rows=rows, manifest=changed)
        with self.assertRaises(ValueError): self.window(samples=self.samples[:-1])

    def test_joint_row_replacement_and_manifest_regeneration_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[90]['target'] += 1
        changed = build_manifest(rows, dataset_id=self.dataset_id,
                                 dataset_fingerprint=self.fingerprint, config_hash=self.config_hash)
        with self.assertRaises(ValueError): self.full(rows=rows, manifest=changed)
        with self.assertRaises(ValueError): self.full(rows=rows)

    def test_same_actor_replacing_all_inputs_is_not_authenticated(self):
        rows = self.rows[:-1]
        changed = build_manifest(rows, dataset_id=self.dataset_id,
                                 dataset_fingerprint=self.fingerprint, config_hash=self.config_hash)
        # This succeeds if the caller also replaces the allegedly trusted anchor;
        # prevents a false claim that this API enforces external independence.
        self.assertEqual(self.full(rows=rows, manifest=changed,
                                   digest=manifest_sha256(changed))['sample_count'], 239)

    def test_missing_or_untrusted_anchor_metadata_rejected(self):
        for digest, source in (('', 'preapproved:test'), ('a'*64, ''), ('bad', 'preapproved:test')):
            with self.subTest(digest=digest, source=source), self.assertRaises(ValueError):
                self.full(digest=digest, source=source)

    def test_data_and_manifest_tamper_individually_rejected(self):
        changed = copy.deepcopy(self.manifest); changed['sample_count'] -= 1
        with self.assertRaises(ValueError): self.full(manifest=changed)
        rows = copy.deepcopy(self.rows); rows[0]['features'] = (123., 456.)
        with self.assertRaises(ValueError): self.full(rows=rows)

    def test_omission_duplicate_reordering_rejected(self):
        cases = [self.rows[1:], self.rows[:20] + self.rows[21:],
                 self.rows[:20] + [self.rows[20]] + self.rows[20:],
                 self.rows[:20] + [self.rows[21], self.rows[20]] + self.rows[22:]]
        for rows in cases:
            with self.subTest(count=len(rows)), self.assertRaises(ValueError): self.full(rows=rows)

    def test_window_omission_duplicate_reordering_rejected(self):
        for samples in (self.samples[:-1], self.samples[:1]*2 + self.samples[2:],
                        self.samples[1:2] + self.samples[:1] + self.samples[2:]):
            with self.assertRaises(ValueError): self.window(samples=samples)

    def test_window_identity_target_and_time_tampering_rejected(self):
        for sample in (replace(self.samples[0], target_value=self.samples[0].target_value+1),
                       replace(self.samples[0], feature_available_time=self.samples[0].decision_time+1),
                       replace(self.samples[0], target_end_time=self.samples[0].target_end_time+1),
                       replace(self.samples[0], sample_id=self.samples[1].sample_id)):
            with self.assertRaises(ValueError): self.window(samples=(sample,)+self.samples[1:])

    def test_window_bounds_cannot_exceed_complete_universe(self):
        with self.assertRaises(ValueError):
            verify_window(self.samples, self.rows, self.manifest, expected_manifest_sha256=self.anchor,
                          anchor_source='preapproved:test', start=90, stop=241, window_id='w0')


if __name__ == '__main__': unittest.main()
