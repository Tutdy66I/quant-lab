"""Audited synthetic scoring for a separately preapproved local authorization.

Callers must preserve the authorization digest independently *before* this
entry is invoked. This is a trusted-user workflow, not a security boundary:
the same owner can call the old scorer, replace inputs or roll back files.
Incomplete attempts stay reserved and require manual review.
"""
import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import sys
import uuid

# In isolated mode the script directory is not on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_store28 as evidence
import holdout_audit28 as audit
import holdout_synthetic as scorer
import ridge_synthetic

VERSION = 'synthetic-audited-evaluation-v1'
SOURCE_FILES = (Path(__file__), Path(audit.__file__), Path(evidence.__file__),
                Path(scorer.__file__), Path(ridge_synthetic.__file__))


def source_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE_FILES}


def _canonical(obj):
    return (json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_new(path, raw):
    with Path(path).open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _checkpoint(ledger, old, new):
    """Atomic replacement with an independently checked old state.

    An externally trusted reader must retain new head/count. A crash between
    event and replacement leaves mismatch/unknown, not an automatic retry.
    """
    path = ledger/'checkpoint.json'
    if _read_json(path) != old: raise ValueError('checkpoint mismatch')
    temp = ledger/('checkpoint-'+uuid.uuid4().hex+'.tmp')
    _write_new(temp, _canonical(new))
    os.replace(str(temp), str(path))
    if _read_json(path) != new: raise ValueError('checkpoint reread failed')


def _verify_auth(path, expected):
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('prior external authorization digest required')
    raw=Path(path).read_bytes()
    if _hash(raw)!=expected: raise ValueError('authorization digest mismatch')
    auth=json.loads(raw.decode('utf-8'))
    required={'version','approver','evaluation_id','ledger_path','registration_sha256',
              'seal_sha256','genesis_sha256','initial_checkpoint','source_sha256','purpose'}
    if not isinstance(auth,dict) or set(auth)!=required or auth['version']!=VERSION:
        raise ValueError('invalid authorization schema')
    if not isinstance(auth['approver'],str) or not auth['approver'].strip(): raise ValueError('approver required')
    if auth['purpose']!='authorize-one-synthetic-audited-evaluation': raise ValueError('purpose mismatch')
    return auth


def bind_authorization(ledger, *, authorization_path, expected_authorization_sha256):
    """Separate pre-evaluation anchoring step; local custody is not independent authentication."""
    ledger=Path(ledger).resolve()
    auth=_verify_auth(authorization_path,expected_authorization_sha256)
    if (str(ledger)!=auth['ledger_path'] or auth['source_sha256']!=source_hashes() or
        _read_json(ledger/'checkpoint.json')!=auth['initial_checkpoint'] or
        auth['initial_checkpoint']!={'head':auth['genesis_sha256'],'count':0} or
        audit.digest(_read_json(ledger/'registration.json'))!=auth['registration_sha256'] or
        audit.digest(_read_json(ledger/'genesis.json'))!=auth['genesis_sha256'] or
        list((ledger/'events').iterdir())):
        raise ValueError('authorization does not bind an intact prepared ledger')
    binding={'version':VERSION,'authorization_sha256':expected_authorization_sha256,
             'evaluation_id':auth['evaluation_id'],'registration_sha256':auth['registration_sha256'],
             'trust':'local precommit is not independent approval'}
    _write_new(ledger/'authorization-binding.json',_canonical(binding))
    return binding


def _check_binding(ledger, auth, expected):
    binding=_read_json(ledger/'authorization-binding.json')
    if binding!={'version':VERSION,'authorization_sha256':expected,
                 'evaluation_id':auth['evaluation_id'],'registration_sha256':auth['registration_sha256'],
                 'trust':'local precommit is not independent approval'}:
        raise ValueError('prior authorization binding missing or inconsistent')


def run_evaluation(plan, rows, seal, ledger, *, authorization_path,
                   expected_authorization_sha256, run_id):
    """Perform one audited evaluation. The caller must fix the auth beforehand.

    Returns a completion receipt only after evidence and ledger verification.
    Errors before reservation do not score. Errors after reservation never retry.
    """
    if not isinstance(run_id,str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}',run_id) is None:
        raise ValueError('simple run ID required')
    ledger=Path(ledger).resolve()
    auth=_verify_auth(authorization_path,expected_authorization_sha256)
    _check_binding(ledger,auth,expected_authorization_sha256)
    if str(ledger)!=auth['ledger_path'] or auth['source_sha256']!=source_hashes():
        raise ValueError('ledger path or code differs from authorization')
    registration=audit.prepare_registration(plan,rows,expected_seal_sha256=auth['seal_sha256'])
    if audit.digest(registration)!=auth['registration_sha256'] or registration['evaluation_id']!=auth['evaluation_id']:
        raise ValueError('registration/evaluation identity mismatch')
    if audit.digest(seal)!=auth['seal_sha256'] or scorer.make_seal(plan,rows)!=seal:
        raise ValueError('source, plan or sample seal mismatch')
    initial=auth['initial_checkpoint']
    if initial!={'head':auth['genesis_sha256'],'count':0} or _read_json(ledger/'checkpoint.json')!=initial:
        raise ValueError('missing, advanced or changed checkpoint')
    args=dict(expected_seal_sha256=auth['seal_sha256'],expected_registration_sha256=auth['registration_sha256'],
              expected_genesis_sha256=auth['genesis_sha256'])
    state=audit.inspect(plan,rows,ledger,expected_head_sha256=initial['head'],
                        expected_event_count=initial['count'],**args)
    if state['status']!='first_recorded' or state['evaluation_id']!=auth['evaluation_id']:
        raise ValueError('already consumed or unknown')
    reservation={'version':VERSION,'status':'reserved','run_id':run_id,'evaluation_id':auth['evaluation_id'],
                 'authorization_sha256':expected_authorization_sha256}
    _write_new(ledger/'reservation.json',_canonical(reservation))
    try:
        attempt=audit.record_attempt(plan,rows,ledger,expected_head_sha256=initial['head'],
                    expected_event_count=0,run_id=run_id,
                    approval='AUTHORIZE_SYNTHETIC_EVALUATION:'+auth['evaluation_id'],**args)
        attempted={'head':audit.digest(attempt),'count':1}
        _checkpoint(ledger,initial,attempted)
        checked=audit.inspect(plan,rows,ledger,expected_head_sha256=attempted['head'],
                              expected_event_count=1,**args)
        if checked['status']!='repeat_recorded' or checked['attempt_count']!=1:
            raise ValueError('attempt not durably visible')
        _write_new(ledger/'attempted.json',_canonical({'status':'attempted','run_id':run_id,
                          'attempt_head':attempted['head'],'evaluation_id':auth['evaluation_id']}))
        # Scorer uses a fixed marker under the already exclusively reserved ledger.
        result=scorer.evaluate_once(plan,rows,seal,auth['seal_sha256'],ledger/'scorer-consumed.json',scorer.APPROVAL)
        result_raw=_canonical(result)
        _write_new(ledger/'result.json',result_raw)
        result_sha256=_hash(result_raw)
        audit.verify_result_bytes(ledger/'result.json',result_sha256)
        evidence_base=ledger/'evidence'; evidence_base.mkdir(exist_ok=False)
        run=evidence.create_run(evidence_base,'evaluation')
        inventory={'scorer.py':'source','authorization.json':'configuration','attempt.json':'log','result.json':'result'}
        for name,data in (('scorer.py',Path(scorer.__file__).read_bytes()),
                          ('authorization.json',Path(authorization_path).read_bytes()),
                          ('attempt.json',_canonical(attempt)),('result.json',result_raw)):
            evidence.save_artifact(run['directory'],run['run_id'],run['reviewer'],name,data)
        evidence_sha=evidence.finalize_run(run['directory'],run['run_id'],run['reviewer'],inventory)
        evidence.verify_run(run['directory'],run['run_id'],run['reviewer'],inventory,evidence_sha)
        event=audit.record_result(plan,rows,ledger,expected_head_sha256=attempted['head'],
                    expected_event_count=1,run_id=run_id,result_sha256=result_sha256,**args)
        final={'head':audit.digest(event),'count':2}
        _checkpoint(ledger,attempted,final)
        checked=audit.inspect(plan,rows,ledger,expected_head_sha256=final['head'],expected_event_count=2,**args)
        if checked['result_count']!=1: raise ValueError('completion event not visible')
        completion={'version':VERSION,'status':'completed','run_id':run_id,'evaluation_id':auth['evaluation_id'],
                    'authorization_sha256':expected_authorization_sha256,'result_sha256':result_sha256,
                    'head':final['head'],'count':2,'evidence_run_id':run['run_id'],
                    'evidence_reviewer':run['reviewer'],'evidence_directory':str(run['directory']),
                    'evidence_manifest_sha256':evidence_sha,'origin_authenticated':False}
        # Never publish a completed receipt until all evidence has validated.
        verify_completion(ledger,authorization_path=authorization_path,
                          expected_authorization_sha256=expected_authorization_sha256,
                          _pending_completion=completion)
        _write_new(ledger/'completed.json',_canonical(completion))
        return completion
    except Exception as exc:
        # The scorer might have run even if an output/event is absent.
        try: _write_new(ledger/'failure.json',_canonical({'status':'failed_or_unknown',
                          'run_id':run_id,'error_type':type(exc).__name__,
                          'scoring_may_have_occurred':True}))
        except (OSError,ValueError): pass
        raise


def verify_completion(ledger, *, authorization_path, expected_authorization_sha256, _pending_completion=None):
    """Check evidence linkage; the same file owner can forge all local inputs."""
    ledger=Path(ledger).resolve(); auth=_verify_auth(authorization_path,expected_authorization_sha256)
    _check_binding(ledger,auth,expected_authorization_sha256)
    if str(ledger)!=auth['ledger_path'] or auth['source_sha256']!=source_hashes():
        raise ValueError('authorization or source mismatch')
    if (audit.digest(_read_json(ledger/'registration.json'))!=auth['registration_sha256'] or
        audit.digest(_read_json(ledger/'genesis.json'))!=auth['genesis_sha256'] or
        sorted(p.name for p in (ledger/'events').iterdir())!=['000001.json','000002.json'] or
        (ledger/'failure.json').exists()):
        raise ValueError('missing, unexpected or contradictory ledger evidence')
    c=_read_json(ledger/'completed.json') if _pending_completion is None else _pending_completion
    reservation=_read_json(ledger/'reservation.json')
    attempted=_read_json(ledger/'attempted.json'); checkpoint=_read_json(ledger/'checkpoint.json')
    if (c.get('version')!=VERSION or c.get('status')!='completed' or c.get('count')!=2 or
        c.get('evaluation_id')!=auth['evaluation_id'] or
        c.get('authorization_sha256')!=expected_authorization_sha256 or c.get('origin_authenticated') is not False or
        reservation.get('version')!=VERSION or reservation.get('status')!='reserved' or
        reservation.get('authorization_sha256')!=expected_authorization_sha256 or
        reservation.get('evaluation_id')!=auth['evaluation_id'] or reservation.get('run_id')!=c.get('run_id') or
        attempted.get('evaluation_id')!=auth['evaluation_id'] or attempted.get('status')!='attempted' or
        attempted.get('run_id')!=c.get('run_id') or checkpoint!={'head':c.get('head'),'count':2}):
        raise ValueError('completion metadata mismatch')
    attempt=_read_json(ledger/'events'/'000001.json'); result=_read_json(ledger/'events'/'000002.json')
    if (audit.digest(attempt)!=attempted.get('attempt_head') or attempt.get('previous_sha256')!=auth['genesis_sha256'] or
        result.get('previous_sha256')!=audit.digest(attempt) or audit.digest(result)!=c['head'] or
        attempt.get('kind')!='attempt' or result.get('kind')!='result' or
        attempt.get('seq')!=1 or result.get('seq')!=2 or attempt.get('classification')!='first_recorded' or
        attempt.get('evaluation_id')!=auth['evaluation_id'] or result.get('evaluation_id')!=auth['evaluation_id'] or
        attempt.get('run_id')!=c['run_id'] or result.get('run_id')!=c['run_id'] or
        result.get('result_sha256')!=c.get('result_sha256')):
        raise ValueError('event chain mismatch')
    audit.verify_result_bytes(ledger/'result.json',c['result_sha256'])
    inventory={'scorer.py':'source','authorization.json':'configuration','attempt.json':'log','result.json':'result'}
    base=(ledger/'evidence').resolve(); ev=Path(c['evidence_directory']).resolve()
    if ev.parent!=base: raise ValueError('evidence path outside ledger')
    evidence.verify_run(ev,c['evidence_run_id'],c['evidence_reviewer'],inventory,c['evidence_manifest_sha256'])
    if ((ev/'result.json').read_bytes()!=(ledger/'result.json').read_bytes() or
        (ev/'authorization.json').read_bytes()!=Path(authorization_path).read_bytes() or
        (ev/'attempt.json').read_bytes()!=_canonical(attempt) or
        (ev/'scorer.py').read_bytes()!=Path(scorer.__file__).read_bytes()):
        raise ValueError('result/evidence mismatch')
    return {'status':'completed','result_sha256':c['result_sha256'],'origin_authenticated':False}


def inspect_prepared(ledger, *, expected_registration_sha256, expected_genesis_sha256):
    """Check local prepared state only; does not establish an independent history."""
    try:
        ledger=Path(ledger).resolve()
        if (not re.fullmatch(r'[0-9a-f]{64}',expected_registration_sha256) or
            not re.fullmatch(r'[0-9a-f]{64}',expected_genesis_sha256)):
            raise ValueError('missing expected digest')
        reg=_read_json(ledger/'registration.json'); genesis=_read_json(ledger/'genesis.json')
        if (audit.digest(reg)!=expected_registration_sha256 or audit.digest(genesis)!=expected_genesis_sha256 or
            genesis.get('registration_sha256')!=expected_registration_sha256 or
            genesis.get('evaluation_id')!=reg.get('evaluation_id') or
            _read_json(ledger/'checkpoint.json')!={'head':expected_genesis_sha256,'count':0} or
            list((ledger/'events').iterdir()) or any((ledger/x).exists() for x in
                 ('authorization-binding.json','reservation.json','attempted.json','result.json','completed.json','failure.json'))):
            raise ValueError('unverifiable or advanced ledger')
        return {'status':'prepared','evaluation_id':reg['evaluation_id'], 'origin_authenticated':False}
    except (OSError,ValueError,TypeError,KeyError):
        return {'status':'unknown','manual_review_required':True}


def inspect_lifecycle(ledger, *, authorization_path, expected_authorization_sha256):
    """Conservative post-process status; incomplete attempts are never reset to first."""
    ledger=Path(ledger).resolve()
    try:
        auth=_verify_auth(authorization_path,expected_authorization_sha256)
        _check_binding(ledger,auth,expected_authorization_sha256)
        if str(ledger)!=auth['ledger_path'] or auth['source_sha256']!=source_hashes():
            return {'status':'unknown','reason':'authorization or source mismatch'}
        if (ledger/'completed.json').exists():
            return verify_completion(ledger,authorization_path=authorization_path,
                   expected_authorization_sha256=expected_authorization_sha256)
        if (ledger/'failure.json').exists():
            fail=_read_json(ledger/'failure.json')
            return {'status':'failed','scoring_may_have_occurred':True,
                    'reason':fail.get('error_type','unknown'), 'manual_review_required':True}
        if (ledger/'reservation.json').exists() or (ledger/'attempted.json').exists():
            return {'status':'unknown','reason':'interrupted reservation or attempt',
                    'scoring_may_have_occurred':True,'manual_review_required':True}
        if (_read_json(ledger/'checkpoint.json') != auth['initial_checkpoint'] or
            audit.digest(_read_json(ledger/'registration.json')) != auth['registration_sha256'] or
            audit.digest(_read_json(ledger/'genesis.json')) != auth['genesis_sha256'] or
            list((ledger/'events').iterdir()) or (ledger/'scorer-consumed.json').exists() or
            (ledger/'result.json').exists()):
            return {'status':'unknown','reason':'checkpoint differs from authorized initial state'}
        return {'status':'authorized','evaluation_id':auth['evaluation_id'],
                'origin_authenticated':False}
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError):
        return {'status':'unknown','reason':'missing or inconsistent evidence','manual_review_required':True}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Authorized local synthetic audited evaluation')
    for field in ('plan','dataset','seal','ledger','authorization','authorization-sha256','run-id'):
        parser.add_argument('--'+field,required=True)
    args=parser.parse_args(argv)
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        print(json.dumps({'status':'unknown','reason':'requires -I -S -B'}),file=sys.stderr)
        return 2
    try:
        completed=run_evaluation(_read_json(args.plan),_read_json(args.dataset),_read_json(args.seal),
            args.ledger,authorization_path=args.authorization,
            expected_authorization_sha256=args.authorization_sha256,run_id=args.run_id)
        print(json.dumps(completed,sort_keys=True)); return 0
    except Exception as exc:
        print(json.dumps({'status':'failed_or_unknown','error_type':type(exc).__name__,
                          'manual_review_required':True}),file=sys.stderr); return 1


if __name__=='__main__':
    sys.exit(main())
