import copy
import unittest
from unittest import mock

import research_chain27 as chain
from synthetic_manifest27 import manifest_sha256, build_manifest


class ResearchChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.manifest = chain.prepare_population('signal')
        cls.anchor = manifest_sha256(cls.manifest)
        cls.result, cls.payload, cls.receipt = chain.run_verified('signal', cls.manifest,
            expected_manifest_sha256=cls.anchor, anchor_source='test prior anchor', replicates=100)

    def evaluate(self, run):
        return chain.evaluate_verified(self.rows, self.manifest, run,
                    expected_manifest_sha256=self.anchor, anchor_source='test prior anchor', replicates=100)

    def test_complete_research_receipt(self):
        self.assertTrue(chain.verify_receipt(self.payload, self.receipt,
            expected_manifest_sha256=self.anchor, rows=self.rows, run=self.result))
        self.assertEqual(len(self.payload['windows']), 3)

    def test_forged_or_missing_receipt_rejected(self):
        with self.assertRaises(ValueError):
            chain.verify_receipt(self.payload, None, expected_manifest_sha256=self.anchor,
                                 rows=self.rows, run=self.result)
        changed = copy.deepcopy(self.result); changed['windows'][0]['selected_alpha'] = 999
        with self.assertRaises(ValueError):
            chain.verify_receipt(self.payload, self.receipt, expected_manifest_sha256=self.anchor,
                                 rows=self.rows, run=changed)

    def test_joint_truncation_against_fixed_manifest(self):
        changed = copy.deepcopy(self.result); w = changed['windows'][0]
        w['validation_samples'] = w['validation_samples'][:-1]
        for name in ('ridge','mean'):
            w['losses'][name] = w['losses'][name][:-1]
        with self.assertRaises(ValueError): self.evaluate(changed)

    def test_forged_population_and_anchor_must_not_run_model(self):
        rows = copy.deepcopy(self.rows); rows[0]['target'] += 1
        forged = build_manifest(rows, dataset_id=self.manifest['dataset_id'],
                 dataset_fingerprint=self.manifest['dataset_fingerprint'], config_hash=self.manifest['config_hash'])
        with mock.patch.object(chain, 'run_rolling', side_effect=AssertionError('model must not run')):
            with self.assertRaises(ValueError):
                chain.run_verified('signal', forged, expected_manifest_sha256=self.anchor,
                                   anchor_source='test prior anchor', replicates=100)

    def test_unaligned_missing_and_wrong_type_loss_rejected(self):
        for mode in ('reordered','missing','array'):
            changed = copy.deepcopy(self.result); w = changed['windows'][0]
            if mode == 'reordered': w['losses']['mean'] = tuple(reversed(w['losses']['mean']))
            if mode == 'missing': w['losses']['mean'] = w['losses']['mean'][:-1]
            if mode == 'array': w['losses']['mean'] = [0.0] * len(w['validation_samples'])
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.evaluate(changed)

    def test_missing_or_reordered_prediction_records_rejected(self):
        for mode in ('missing', 'reordered'):
            changed = copy.deepcopy(self.result); w = changed['windows'][0]
            if mode == 'missing': w['predictions']['mean'] = w['predictions']['mean'][:-1]
            else: w['predictions']['mean'] = tuple(reversed(w['predictions']['mean']))
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.evaluate(changed)

    def test_wrong_config_and_split_rejected(self):
        changed = copy.deepcopy(self.result); changed['config']['gap_ticks'] = 0
        with self.assertRaises(ValueError): self.evaluate(changed)
        changed = copy.deepcopy(self.result); w=changed['windows'][0]
        w['train_samples'] = w['validation_samples']
        with self.assertRaises(ValueError): self.evaluate(changed)
