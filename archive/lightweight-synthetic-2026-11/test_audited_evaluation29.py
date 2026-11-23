"""Disposable synthetic holdout only: actual scoring and failure tests."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import audited_evaluation29 as entry
import holdout_audit28 as audit
import holdout_synthetic as h


class AuditedToy29(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.plan=copy.deepcopy(h.FINAL_PLAN)
        self.plan.update(dataset_id='review29-disposable-toy',seed=290317,total=48,
            train=[0,12],tune=[12,22],development_validation=[22,34],holdout=[34,48])
        self.rows=h.generate(self.plan); self.seal=h.make_seal(self.plan,self.rows)
        self.seal_hash=audit.digest(self.seal)
        reg=audit.prepare_registration(self.plan,self.rows,expected_seal_sha256=self.seal_hash)
        self.reg_hash=audit.digest(reg); self.reg=reg
        self.ledger=self.root/'ledger'
        self.genesis=audit.initialize_ledger(self.ledger,reg,expected_registration_sha256=self.reg_hash,
                 declarer='toy-preapproval',prior_history_evidence_sha256='c'*64)
        self.initial={'head':self.genesis,'count':0}
        (self.ledger/'checkpoint.json').write_bytes(entry._canonical(self.initial))
        self.auth={'version':entry.VERSION,'approver':'toy-preapproval',
            'evaluation_id':reg['evaluation_id'],'ledger_path':str(self.ledger.resolve()),
            'registration_sha256':self.reg_hash,'seal_sha256':self.seal_hash,
            'genesis_sha256':self.genesis,'initial_checkpoint':self.initial,
            'source_sha256':entry.source_hashes(),'purpose':'authorize-one-synthetic-audited-evaluation'}
        self.auth_path=self.root/'authorization.json'; self.auth_path.write_bytes(entry._canonical(self.auth))
        self.auth_hash=hashlib.sha256(self.auth_path.read_bytes()).hexdigest()
        entry.bind_authorization(self.ledger, authorization_path=self.auth_path,
                                 expected_authorization_sha256=self.auth_hash)

    def run_eval(self):
        return entry.run_evaluation(self.plan,self.rows,self.seal,self.ledger,
             authorization_path=self.auth_path,expected_authorization_sha256=self.auth_hash,run_id='toy-run')

    def verify(self):
        return entry.verify_completion(self.ledger,authorization_path=self.auth_path,
                     expected_authorization_sha256=self.auth_hash)

    def test_actual_scoring_and_audited_evidence(self):
        result=self.run_eval(); self.assertEqual(result['status'],'completed')
        self.assertFalse(result['origin_authenticated']); self.assertEqual(self.verify()['status'],'completed')
        self.assertTrue((self.ledger/'reservation.json').is_file())
        self.assertTrue((self.ledger/'attempted.json').is_file())
        self.assertTrue((self.ledger/'result.json').is_file())
        self.assertEqual(json.loads((self.ledger/'events'/'000001.json').read_text())['kind'],'attempt')
        self.assertEqual(json.loads((self.ledger/'events'/'000002.json').read_text())['kind'],'result')
        with self.assertRaises((ValueError,FileExistsError)): self.run_eval()

    def test_bad_approval_and_missing_anchor_refuse_before_scoring(self):
        for anchor in ('', '0'*64):
            with self.subTest(anchor=anchor),mock.patch.object(h,'evaluate_once') as spy:
                with self.assertRaises(ValueError): entry.run_evaluation(self.plan,self.rows,self.seal,self.ledger,
                    authorization_path=self.auth_path,expected_authorization_sha256=anchor,run_id='no-auth')
                spy.assert_not_called()
        self.assertFalse((self.ledger/'reservation.json').exists())

    def test_changed_rows_or_authorization_refuse(self):
        bad=copy.deepcopy(self.rows);bad[0]['target']+=1
        with mock.patch.object(h,'evaluate_once') as spy:
            with self.assertRaises(ValueError): entry.run_evaluation(self.plan,bad,self.seal,self.ledger,
                   authorization_path=self.auth_path,expected_authorization_sha256=self.auth_hash,run_id='bad-rows')
            spy.assert_not_called()
        auth=copy.deepcopy(self.auth);auth['evaluation_id']='wrong';self.auth_path.write_bytes(entry._canonical(auth))
        with self.assertRaises(ValueError): self.run_eval()

    def test_changed_code_digest_refuses_before_reserve(self):
        changed=dict(self.auth['source_sha256']);changed['ridge_synthetic.py']='0'*64
        with mock.patch.object(entry,'source_hashes',return_value=changed),mock.patch.object(h,'evaluate_once') as spy:
            with self.assertRaises(ValueError): self.run_eval()
            spy.assert_not_called()
        self.assertFalse((self.ledger/'reservation.json').exists())

    def test_crash_before_scoring_keeps_reservation(self):
        with mock.patch.object(audit,'record_attempt',side_effect=RuntimeError('before scoring')),mock.patch.object(h,'evaluate_once') as spy:
            with self.assertRaises(RuntimeError): self.run_eval()
            spy.assert_not_called()
        self.assertTrue((self.ledger/'reservation.json').exists())
        self.assertTrue((self.ledger/'failure.json').exists())
        with self.assertRaises(FileExistsError): self.run_eval()

    def test_result_write_failure_cannot_become_completed(self):
        real=entry._write_new
        def fail(path, raw):
            if Path(path).name=='result.json': raise OSError('synthetic write failure')
            return real(path,raw)
        with mock.patch.object(entry,'_write_new',side_effect=fail):
            with self.assertRaises(OSError): self.run_eval()
        self.assertTrue((self.ledger/'scorer-consumed.json').exists())
        self.assertFalse((self.ledger/'completed.json').exists())
        with self.assertRaises((ValueError,FileNotFoundError)): self.verify()
        with self.assertRaises((ValueError,FileExistsError)): self.run_eval()

    def test_completion_event_failure_preserves_unknown_attempt(self):
        with mock.patch.object(audit,'record_result',side_effect=OSError('synthetic event failure')):
            with self.assertRaises(OSError): self.run_eval()
        self.assertTrue((self.ledger/'result.json').exists())
        self.assertTrue((self.ledger/'scorer-consumed.json').exists())
        self.assertTrue((self.ledger/'failure.json').exists())
        self.assertFalse((self.ledger/'completed.json').exists())
        with self.assertRaises((OSError,ValueError)): self.verify()
        with self.assertRaises((OSError,ValueError)): self.run_eval()

    def test_approval_model_configuration_mismatch_rejected(self):
        changed=copy.deepcopy(self.plan);changed['alphas']=[10.0]
        with mock.patch.object(h,'evaluate_once') as spy:
            with self.assertRaises(ValueError): entry.run_evaluation(changed,self.rows,self.seal,self.ledger,
                 authorization_path=self.auth_path,expected_authorization_sha256=self.auth_hash,run_id='changed-model')
            spy.assert_not_called()

    def test_completion_and_result_tampering_rejected(self):
        self.run_eval()
        (self.ledger/'result.json').write_bytes(b'{"fake":true}')
        with self.assertRaises(ValueError): self.verify()

    def test_completion_count_tampering_rejected(self):
        self.run_eval()
        path=self.ledger/'completed.json'; receipt=json.loads(path.read_text(encoding='utf-8'))
        receipt['count']=999; path.write_text(json.dumps(receipt),encoding='utf-8')
        with self.assertRaises(ValueError): self.verify()

    def test_prepared_only_before_prior_authorization_binding(self):
        p=self.ledger/'authorization-binding.json'; binding=p.read_bytes(); p.unlink()
        prepared=entry.inspect_prepared(self.ledger,expected_registration_sha256=self.reg_hash,
                                        expected_genesis_sha256=self.genesis)
        self.assertEqual(prepared['status'],'prepared')
        self.assertFalse(prepared['origin_authenticated'])
        p.write_bytes(binding)
        advanced=entry.inspect_prepared(self.ledger,expected_registration_sha256=self.reg_hash,
                                        expected_genesis_sha256=self.genesis)
        self.assertEqual(advanced['status'],'unknown')

    def test_direct_scorer_cannot_be_verified_as_audited(self):
        h.evaluate_once(self.plan,self.rows,self.seal,self.seal_hash,self.root/'other-marker',h.APPROVAL)
        with self.assertRaises((FileNotFoundError,ValueError)): self.verify()

    def test_missing_checkpoint_rejects_without_scoring(self):
        (self.ledger/'checkpoint.json').unlink()
        with mock.patch.object(h,'evaluate_once') as spy:
            with self.assertRaises((OSError,ValueError)): self.run_eval()
            spy.assert_not_called()

    def test_absent_prior_binding_rejects_without_scoring(self):
        (self.ledger/'authorization-binding.json').unlink()
        with mock.patch.object(h,'evaluate_once') as spy:
            with self.assertRaises((OSError,ValueError)): self.run_eval()
            spy.assert_not_called()

    def test_tampered_prior_binding_rejects_without_scoring(self):
        p=self.ledger/'authorization-binding.json'
        binding=json.loads(p.read_text(encoding='utf-8')); binding['authorization_sha256']='0'*64
        p.write_text(json.dumps(binding),encoding='utf-8')
        with mock.patch.object(h,'evaluate_once') as spy:
            with self.assertRaises(ValueError): self.run_eval()
            spy.assert_not_called()

    def test_missing_genesis_or_registration_rejects_completion(self):
        self.run_eval()
        for name in ('genesis.json','registration.json'):
            p=self.ledger/name; saved=p.read_bytes(); p.unlink()
            try:
                with self.subTest(name=name), self.assertRaises((OSError,ValueError)): self.verify()
            finally: p.write_bytes(saved)

    def test_attempt_evidence_copy_mismatch_rejected(self):
        completed=self.run_eval()
        p=Path(completed['evidence_directory'])/'attempt.json'
        p.write_text('{"altered":true}',encoding='utf-8')
        with self.assertRaises(ValueError): self.verify()

    def test_prepublication_verification_failure_never_publishes_success(self):
        with mock.patch.object(entry,'verify_completion',side_effect=ValueError('bad evidence')):
            with self.assertRaises(ValueError): self.run_eval()
        self.assertFalse((self.ledger/'completed.json').exists())
        self.assertTrue((self.ledger/'failure.json').exists())
        self.assertEqual(entry.inspect_lifecycle(self.ledger,authorization_path=self.auth_path,
                         expected_authorization_sha256=self.auth_hash)['status'],'failed')

    def test_interrupted_reservation_is_unknown(self):
        (self.ledger/'reservation.json').write_text('{}',encoding='utf-8')
        state=entry.inspect_lifecycle(self.ledger,authorization_path=self.auth_path,
                                      expected_authorization_sha256=self.auth_hash)
        self.assertEqual(state['status'],'unknown')
        self.assertTrue(state['manual_review_required'])

    def test_cli_rejects_invalid_arguments(self):
        run=subprocess.run([sys.executable,'-I','-S','-B',str(Path(entry.__file__)), '--invalid'],
                           capture_output=True,text=True,timeout=20)
        self.assertNotEqual(run.returncode,0)


if __name__=='__main__': unittest.main()
