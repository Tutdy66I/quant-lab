import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import evidence_store28 as evidence


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = evidence.create_run(self.temp.name, 'reviewer_a')
        self.directory = Path(self.context['directory'])
        self.inventory = {'source.py': 'source', 'configuration.json': 'configuration',
                          'failure.log': 'log', 'result.json': 'result'}
        for name in self.inventory:
            evidence.save_artifact(self.directory, self.context['run_id'], self.context['reviewer'],
                                   name, ('original ' + name).encode())

    def finish(self):
        return evidence.finalize_run(self.directory, self.context['run_id'],
                                     self.context['reviewer'], self.inventory)

    def verify(self, anchor):
        return evidence.verify_run(self.directory, self.context['run_id'], self.context['reviewer'],
                                   self.inventory, anchor)

    def test_valid_real_directory_and_no_origin_claim(self):
        anchor = self.finish()
        result = self.verify(anchor)
        self.assertEqual(result['files'], 4)
        self.assertFalse(result['origin_authenticated'])

    def test_unique_runs_and_reviewer_specific_names(self):
        other = evidence.create_run(self.temp.name, 'reviewer_b')
        self.assertNotEqual(self.context['run_id'], other['run_id'])
        self.assertNotEqual(self.context['directory'], other['directory'])
        self.assertIn('reviewer_b-', other['directory'])

    def test_existing_run_id_collision_fails(self):
        class SameUuid:
            hex = self.context['run_id'][4:]
        with mock.patch.object(evidence.uuid, 'uuid4', return_value=SameUuid()):
            with self.assertRaises(FileExistsError): evidence.create_run(self.temp.name, 'reviewer_a')

    def test_existing_failure_log_is_not_overwritten(self):
        old = (self.directory / 'failure.log').read_bytes()
        with self.assertRaises(FileExistsError):
            evidence.save_artifact(self.directory, self.context['run_id'], self.context['reviewer'],
                                   'failure.log', b'replacement')
        self.assertEqual((self.directory / 'failure.log').read_bytes(), old)

    def test_missing_required_log_rejected(self):
        (self.directory / 'failure.log').unlink()
        with self.assertRaises(ValueError): self.finish()

    def test_modified_or_missing_file_rejected(self):
        anchor = self.finish()
        (self.directory / 'failure.log').write_bytes(b'tampered')
        with self.assertRaises(ValueError): self.verify(anchor)
        (self.directory / 'failure.log').unlink()
        with self.assertRaises(ValueError): self.verify(anchor)

    def test_manifest_modification_rejected(self):
        anchor = self.finish()
        p = self.directory / evidence.MANIFEST
        doc = json.loads(p.read_text(encoding='utf-8'))
        doc['reviewer'] = 'other'
        p.write_text(json.dumps(doc), encoding='utf-8')
        with self.assertRaises(ValueError): self.verify(anchor)

    def test_finalized_run_cannot_be_appended_or_finalized_again(self):
        anchor = self.finish()
        with self.assertRaises(ValueError):
            evidence.save_artifact(self.directory, self.context['run_id'], self.context['reviewer'],
                                   'later.log', b'later')
        with self.assertRaises(ValueError): self.finish()
        self.assertTrue(self.verify(anchor))

    def test_missing_or_wrong_anchor_and_inventory_rejected(self):
        anchor = self.finish()
        with self.assertRaises(ValueError): self.verify(None)
        with self.assertRaises(ValueError): self.verify('0' * 64)
        wrong = dict(self.inventory); wrong.pop('failure.log')
        with self.assertRaises(ValueError):
            evidence.verify_run(self.directory, self.context['run_id'], self.context['reviewer'], wrong, anchor)

    def test_path_and_identity_tricks_rejected(self):
        for name in ('../outside', 'evidence-manifest.json', 'sub/file', '', 'x.'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                evidence.save_artifact(self.directory, self.context['run_id'], self.context['reviewer'], name, b'x')
        with self.assertRaises(ValueError):
            evidence.save_artifact(self.directory, self.context['run_id'], 'other', 'new.log', b'x')

    def test_unexpected_file_or_incomplete_categories_rejected(self):
        (self.directory / 'unexpected.log').write_text('extra', encoding='utf-8')
        with self.assertRaises(ValueError): self.finish()
        (self.directory / 'unexpected.log').unlink()
        bad = dict(self.inventory); bad.pop('result.json')
        with self.assertRaises(ValueError):
            evidence.finalize_run(self.directory, self.context['run_id'], self.context['reviewer'], bad)


if __name__ == '__main__':
    unittest.main()
