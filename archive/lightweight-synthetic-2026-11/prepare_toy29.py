"""Prepare only a separately identified disposable toy; never open holdout."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import uuid

if len(sys.argv)!=1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    print('Use python -I -S -B prepare_toy29.py',file=sys.stderr); sys.exit(2)
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root))
import audited_evaluation29 as entry
import holdout_audit28 as audit
import holdout_synthetic as holdout


def save(path, data):
    with path.open('xb') as stream: stream.write(entry._canonical(data))


def main():
    base=root/'round29-toy-runs'; base.mkdir(exist_ok=True)
    toy_id=uuid.uuid4().hex
    path=base/('toy-'+toy_id); path.mkdir(exist_ok=False)
    plan=copy.deepcopy(holdout.FINAL_PLAN)
    plan.update(dataset_id='round29-disposable-toy-'+toy_id,seed=int(toy_id[:8],16),total=48,
                train=[0,12],tune=[12,22],development_validation=[22,34],holdout=[34,48])
    rows=holdout.generate(plan); seal=holdout.make_seal(plan,rows); seal_hash=audit.digest(seal)
    reg=audit.prepare_registration(plan,rows,expected_seal_sha256=seal_hash)
    reg_hash=audit.digest(reg); ledger=path/'ledger'
    genesis=audit.initialize_ledger(ledger,reg,expected_registration_sha256=reg_hash,
              declarer='toy-only-same-user',prior_history_evidence_sha256='9'*64)
    initial={'head':genesis,'count':0}; save(ledger/'checkpoint.json',initial)
    auth={'version':entry.VERSION,'approver':'toy-only-same-user',
          'evaluation_id':reg['evaluation_id'],'ledger_path':str(ledger.resolve()),
          'registration_sha256':reg_hash,'seal_sha256':seal_hash,'genesis_sha256':genesis,
          'initial_checkpoint':initial,'source_sha256':entry.source_hashes(),
          'purpose':'authorize-one-synthetic-audited-evaluation'}
    for filename,value in (('plan.json',plan),('dataset.json',rows),('seal.json',seal),('authorization.json',auth)):
        save(path/filename,value)
    auth_path=path/'authorization.json'; auth_sha=hashlib.sha256(auth_path.read_bytes()).hexdigest()
    entry.bind_authorization(ledger,authorization_path=auth_path,expected_authorization_sha256=auth_sha)
    summary={'toy_directory':str(path),'authorization_sha256':auth_sha,'evaluation_id':reg['evaluation_id'],
             'status':'prepared_only','origin_authenticated':False,
             'trust':'same-user synthetic demonstration only; no independent approval'}
    save(path/'preparation.json',summary); print(json.dumps(summary,sort_keys=True))


if __name__=='__main__': main()
