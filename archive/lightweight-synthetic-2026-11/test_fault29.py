"""Independent adversarial checks using disposable synthetic datasets only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import holdout_audit28 as audit
import holdout_synthetic as holdout


class IndependentFault29(unittest.TestCase):
    def setUp(self):
        self.plan = copy.deepcopy(holdout.FINAL_PLAN)
        self.plan.update(dataset_id='independent-fault29-only', seed=290741, total=45,
                         train=[0,12], tune=[12,21], development_validation=[21,33], holdout=[33,45])
        self.rows = holdout.generate(self.plan)
        self.seal = audit.digest(holdout.make_seal(self.plan,self.rows))
        self.registration = audit.prepare_registration(self.plan,self.rows,expected_seal_sha256=self.seal)
        self.reg_hash = audit.digest(self.registration)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.ledger = self.path/'ledger'
        self.genesis = audit.initialize_ledger(self.ledger,self.registration,
            expected_registration_sha256=self.reg_hash,declarer='independent-toy-review',
            prior_history_evidence_sha256='2'*64)
        self.args = dict(expected_seal_sha256=self.seal,expected_registration_sha256=self.reg_hash,
                         expected_genesis_sha256=self.genesis,expected_head_sha256=self.genesis,
                         expected_event_count=0)

    def test_direct_old_scoring_bypasses_audit_but_cannot_claim_audited(self):
        # Establish the limitation with the actual old scorer, never original data.
        old = holdout.evaluate_once(self.plan,self.rows,holdout.make_seal(self.plan,self.rows),
                  self.seal,self.path/'direct-marker',holdout.APPROVAL)
        self.assertEqual(old['status'],'consumed')
        self.assertNotIn('audited',old)
        self.assertEqual(audit.inspect(self.plan,self.rows,self.ledger,**self.args)['status'],'first_recorded')

    def test_deleted_tail_is_unknown_against_saved_checkpoint(self):
        event=audit.record_attempt(self.plan,self.rows,self.ledger,run_id='toy-attempt',
            approval='AUTHORIZE_SYNTHETIC_EVALUATION:'+self.registration['evaluation_id'],**self.args)
        saved=dict(self.args,expected_event_count=event['seq'],expected_head_sha256=audit.digest(event))
        (self.ledger/'events'/'000001.json').unlink()
        with self.assertRaises(ValueError): audit.inspect(self.plan,self.rows,self.ledger,**saved)

    def test_real_process_race_records_at_most_one_attempt(self):
        # Two independent Windows processes race for the same known ledger head.
        payload = {'plan':self.plan,'seal':self.seal,'registration':self.reg_hash,'genesis':self.genesis,
                   'ledger':str(self.ledger),'signal':str(self.path/'start'),'directory':str(self.path)}
        config=self.path/'case.json'; config.write_text(json.dumps(payload),encoding='utf-8')
        script = '''
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1]); import holdout_audit28 as a,holdout_synthetic as h
c=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")); who=sys.argv[3]
Path(c["directory"],"ready-"+who).write_text("ready",encoding="utf-8")
deadline=time.monotonic()+10
while not Path(c["signal"]).exists():
 if time.monotonic()>deadline: sys.exit(4)
 time.sleep(.005)
plan=c["plan"]; rows=h.generate(plan); reg=a.prepare_registration(plan,rows,expected_seal_sha256=c["seal"])
try:
 a.record_attempt(plan,rows,c["ledger"],expected_seal_sha256=c["seal"],expected_registration_sha256=c["registration"],expected_genesis_sha256=c["genesis"],expected_head_sha256=c["genesis"],expected_event_count=0,run_id="race-"+who,approval="AUTHORIZE_SYNTHETIC_EVALUATION:"+reg["evaluation_id"])
 print("attempt recorded"); sys.exit(0)
except Exception as e:
 print(type(e).__name__,str(e)); sys.exit(1)
'''
        procs=[]
        try:
            for who in ('a','b'):
                procs.append(subprocess.Popen([sys.executable,'-I','-S','-B','-c',script,
                    str(Path(__file__).resolve().parent),str(config),who],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
            deadline=time.monotonic()+10
            while not all((self.path/('ready-'+who)).exists() for who in ('a','b')):
                if time.monotonic()>deadline: self.fail('race processes did not start')
                time.sleep(.01)
            (self.path/'start').write_text('start',encoding='utf-8')
            result=[(p.communicate(timeout=15),p.returncode) for p in procs]
            self.assertEqual(sorted(code for _,code in result),[0,1],result)
            self.assertEqual([p.name for p in (self.ledger/'events').iterdir()],['000001.json'])
        finally:
            for p in procs:
                if p.poll() is None: p.kill(); p.communicate()


if __name__=='__main__': unittest.main()
