import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import holdout_audit28 as audit
import holdout_synthetic as holdout


class LocalAuditTests(unittest.TestCase):
    def setUp(self):
        # Sacrificial synthetic dataset only; never use FINAL_PLAN's dataset.
        self.plan=copy.deepcopy(holdout.FINAL_PLAN)
        self.plan.update(dataset_id='sacrificial-audit28',seed=281928,total=45,
            train=[0,12],tune=[12,21],development_validation=[21,33],holdout=[33,45])
        self.rows=holdout.generate(self.plan)
        self.seal=audit.digest(holdout.make_seal(self.plan,self.rows))
        self.reg=audit.prepare_registration(self.plan,self.rows,expected_seal_sha256=self.seal)
        self.reg_hash=audit.digest(self.reg)
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'audit'
        self.genesis=audit.initialize_ledger(self.path,self.reg,expected_registration_sha256=self.reg_hash,
                      declarer='toy operator',prior_history_evidence_sha256='a'*64)
        self.args=dict(expected_seal_sha256=self.seal,expected_registration_sha256=self.reg_hash,
                       expected_genesis_sha256=self.genesis,expected_head_sha256=self.genesis,
                       expected_event_count=0)

    def state(self): return audit.inspect(self.plan,self.rows,self.path,**self.args)
    def attempt(self,run_id='toy-1',approval=None):
        event = audit.record_attempt(self.plan,self.rows,self.path,run_id=run_id,
            approval=approval or 'AUTHORIZE_SYNTHETIC_EVALUATION:'+self.reg['evaluation_id'],**self.args)
        self.args.update(expected_head_sha256=audit.digest(event), expected_event_count=event['seq'])
        return event

    def result(self,run_id='toy-1',sha='b'*64):
        event=audit.record_result(self.plan,self.rows,self.path,run_id=run_id,result_sha256=sha,**self.args)
        self.args.update(expected_head_sha256=audit.digest(event), expected_event_count=event['seq'])
        return event

    def test_first_attempt_result_then_repeat_is_explicit(self):
        self.assertEqual(self.state()['status'],'first_recorded')
        self.assertEqual(self.attempt()['classification'],'first_recorded')
        # An unrelated toy scoring substitute; audit records only its digest.
        toy_result={'toy_mean':sum([2,4,6])/3}
        event=self.result(sha=audit.digest(toy_result))
        self.assertEqual(event['result_sha256'],audit.digest(toy_result))
        self.assertEqual(self.state()['status'],'repeat_recorded')
        self.assertEqual(self.attempt('toy-2')['classification'],'repeat_recorded')
        self.assertEqual(self.state()['attempt_count'],2)

    def test_missing_history_and_altered_digest_never_claim_first(self):
        with self.assertRaises(ValueError):
            audit.inspect(self.plan,self.rows,self.path,**dict(self.args,expected_genesis_sha256=None))
        (self.path/'genesis.json').unlink()
        with self.assertRaises(ValueError): self.state()

    def test_missing_authorization_is_rejected(self):
        with self.assertRaises(ValueError): self.attempt(approval='not-approved')
        self.assertEqual(self.state()['status'],'first_recorded')

    def test_duplicate_run_id_and_duplicate_result_fail(self):
        self.attempt()
        with self.assertRaises(ValueError): self.attempt()
        self.result()
        with self.assertRaises(ValueError):
            audit.record_result(self.plan,self.rows,self.path,run_id='toy-1',result_sha256='c'*64,**self.args)

    def test_unknown_gap_and_event_tamper_rejected(self):
        self.attempt(); path=self.path/'events'/'000001.json'
        item=json.loads(path.read_text(encoding='utf-8')); item['previous_sha256']='0'*64
        path.write_text(json.dumps(item),encoding='utf-8')
        with self.assertRaises(ValueError): self.state()
        path.rename(self.path/'events'/'000002.json')
        with self.assertRaises(ValueError): self.state()

    def test_registration_sample_and_method_drift_are_rejected(self):
        rows=copy.deepcopy(self.rows); rows[0]['target']+=1
        with self.assertRaises(ValueError):
            audit.inspect(self.plan,rows,self.path,**self.args)
        plan=copy.deepcopy(self.plan); plan['alpha_selection']='use holdout'
        with self.assertRaises(ValueError): audit.inspect(plan,self.rows,self.path,**self.args)

    def test_same_owner_can_forge_new_ledger_not_independent(self):
        # Limitation: a second caller-controlled genesis can be generated.
        other=Path(self.temp.name)/'other'
        new=audit.initialize_ledger(other,self.reg,expected_registration_sha256=self.reg_hash,
              declarer='same owner',prior_history_evidence_sha256='f'*64)
        state=audit.inspect(self.plan,self.rows,other,**dict(self.args,expected_genesis_sha256=new,
                     expected_head_sha256=new,expected_event_count=0))
        self.assertEqual(state['status'],'first_recorded')
        self.assertFalse(state['independent_origin_authenticated'])

    def test_toy_result_bytes_are_checked_and_missing_rejected(self):
        p=Path(self.temp.name)/'toy-result.json'
        data=b'{"toy_mean":4.0}'
        p.write_bytes(data); sha=hashlib.sha256(data).hexdigest()
        self.attempt(); self.result(sha=sha)
        self.assertFalse(audit.verify_result_bytes(p,sha)['origin_authenticated'])
        p.write_bytes(data+b' ')
        with self.assertRaises(ValueError): audit.verify_result_bytes(p,sha)
        p.unlink()
        with self.assertRaises(ValueError): audit.verify_result_bytes(p,sha)

    def test_tail_deletion_detected_against_preserved_checkpoint(self):
        self.attempt()
        (self.path/'events'/'000001.json').unlink()
        with self.assertRaises(ValueError): self.state()
        # Replacing the checkpoint as well defeats local-only evidence.
        forged=dict(self.args, expected_head_sha256=self.genesis, expected_event_count=0)
        self.assertEqual(audit.inspect(self.plan,self.rows,self.path,**forged)['status'],'first_recorded')


if __name__=='__main__': unittest.main()
