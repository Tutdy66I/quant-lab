"""Preparation-only local audit for trusted synthetic holdouts; no scoring.

Hash/ledger custody and the history assertion are supplied by the caller.
Same-user deletion, rollback, alternate ledgers, reading, or code changes are
not prevented. `first_recorded` never asserts the dataset was never viewed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

# Isolated mode omits script directory from sys.path. These are local,
# pre-reviewed trusted modules, not a malicious-code security boundary.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import holdout_synthetic as holdout


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode('utf-8')).hexdigest()


def _hash(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('a fixed SHA-256 is required')
    return value


def _text(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('nonempty canonical text required')
    return value


def prepare_registration(plan, rows, *, expected_seal_sha256):
    """Commit complete samples and the approved implementation; no model execution."""
    _hash(expected_seal_sha256)
    seal = holdout.make_seal(plan, rows)
    if digest(seal) != expected_seal_sha256:
        raise ValueError('expected seal mismatch')
    start, stop = plan['holdout']
    ids = [row['sample_id'] for row in rows]
    if len(set(ids)) != len(ids) or len(rows) != plan['total'] or stop-start < 1:
        raise ValueError('invalid population or identities')
    body = {'version':'local-holdout-registration-v1', 'plan':plan,
            'seal_sha256':expected_seal_sha256, 'dataset_sha256':seal['dataset_sha256'],
            'holdout_manifest_sha256':seal['holdout_manifest_sha256'],
            'population_count':len(rows), 'holdout_count':stop-start,
            'ordered_population_ids_sha256':digest(ids),
            'source_sha256':seal['source_sha256'],
            'preparer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return dict(body, evaluation_id='evaluation:'+digest(body))


def _write_new(path, data):
    # Atomic exclusive creation avoids ordinary accidental overwrites.
    with open(path, 'x', encoding='utf-8') as f:
        json.dump(data, f, sort_keys=True, separators=(',', ':'), allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


def initialize_ledger(directory, registration, *, expected_registration_sha256,
                      declarer, prior_history_evidence_sha256):
    """Record the caller's explicit no-prior-use assertion, not a verification."""
    if digest(registration) != _hash(expected_registration_sha256):
        raise ValueError('registration mismatch')
    _text(declarer); _hash(prior_history_evidence_sha256)
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    events = directory / 'events'; events.mkdir(exist_ok=False)
    genesis = {'version':'local-holdout-ledger-v1', 'registration_sha256':expected_registration_sha256,
               'evaluation_id':registration['evaluation_id'], 'assertion':'no_prior_evaluations_declared',
               'declarer':declarer, 'prior_history_evidence_sha256':prior_history_evidence_sha256,
               'independence_verified':False}
    _write_new(directory/'registration.json', registration)
    _write_new(directory/'genesis.json', genesis)
    return digest(genesis)


def _read(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def inspect(plan, rows, directory, *, expected_seal_sha256,
            expected_registration_sha256, expected_genesis_sha256,
            expected_head_sha256, expected_event_count):
    """Return first_recorded/repeat_recorded; raise if evidence is unknown.

    `first_recorded` means only no attempt in this intact claimed local history.
    Missing, ambiguous or inconsistent evidence never becomes first_recorded.
    """
    _hash(expected_registration_sha256); _hash(expected_genesis_sha256); _hash(expected_head_sha256)
    if type(expected_event_count) is not int or expected_event_count < 0:
        raise ValueError('unknown: expected event count is required')
    reg = prepare_registration(plan, rows, expected_seal_sha256=expected_seal_sha256)
    if digest(reg) != expected_registration_sha256:
        raise ValueError('pre-registration or source changed')
    directory = Path(directory)
    try:
        recorded = _read(directory/'registration.json'); genesis = _read(directory/'genesis.json')
        names = sorted(p.name for p in (directory/'events').iterdir())
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError('unknown: missing or unreadable audit evidence') from exc
    if recorded != reg or digest(genesis) != expected_genesis_sha256:
        raise ValueError('unknown: altered registration or genesis')
    if (genesis.get('version')!='local-holdout-ledger-v1' or
        genesis.get('registration_sha256')!=expected_registration_sha256 or
        genesis.get('evaluation_id')!=reg['evaluation_id'] or
        genesis.get('assertion')!='no_prior_evaluations_declared' or
        genesis.get('independence_verified') is not False):
        raise ValueError('unknown: invalid genesis')
    _text(genesis.get('declarer')); _hash(genesis.get('prior_history_evidence_sha256'))
    if names != ['%06d.json' % i for i in range(1, len(names)+1)]:
        raise ValueError('unknown: event gap or unexpected file')
    last = expected_genesis_sha256; attempts = {}; result_ids = set()
    for seq,name in enumerate(names,1):
        try: event = _read(directory/'events'/name)
        except (OSError, ValueError, TypeError) as exc: raise ValueError('unknown: event unreadable') from exc
        if not isinstance(event,dict) or event.get('seq')!=seq or event.get('previous_sha256')!=last or event.get('evaluation_id')!=reg['evaluation_id']:
            raise ValueError('unknown: invalid event chain')
        _text(event.get('run_id'))
        if event.get('kind')=='attempt':
            if event['run_id'] in attempts or event.get('classification') != ('repeat_recorded' if attempts else 'first_recorded'):
                raise ValueError('unknown: duplicate or mislabeled attempt')
            attempts[event['run_id']] = event
        elif event.get('kind')=='result':
            if event['run_id'] not in attempts or event['run_id'] in result_ids:
                raise ValueError('unknown: result without a unique attempt')
            _hash(event.get('result_sha256')); result_ids.add(event['run_id'])
        else: raise ValueError('unknown: event kind')
        last=digest(event)
    if last != expected_head_sha256 or len(names) != expected_event_count:
        raise ValueError('unknown: current ledger differs from prior checkpoint')
    return {'status':'repeat_recorded' if attempts else 'first_recorded',
            'evaluation_id':reg['evaluation_id'], 'attempt_count':len(attempts),
            'result_count':len(result_ids), 'last_sha256':last, 'event_count':len(names),
            'independent_origin_authenticated':False,
            'limitation':'local claimed history only; owner can view, delete, restore or use another ledger'}


def record_attempt(plan, rows, directory, *, expected_seal_sha256, expected_registration_sha256,
                   expected_genesis_sha256, expected_head_sha256, expected_event_count, run_id, approval):
    """Record explicit permission and disclose repeat; does not execute scoring."""
    state=inspect(plan, rows, directory, expected_seal_sha256=expected_seal_sha256,
          expected_registration_sha256=expected_registration_sha256, expected_genesis_sha256=expected_genesis_sha256,
          expected_head_sha256=expected_head_sha256, expected_event_count=expected_event_count)
    _text(run_id)
    if approval != 'AUTHORIZE_SYNTHETIC_EVALUATION:'+state['evaluation_id']:
        raise ValueError('explicit evaluation-specific approval required')
    previous=[_read(p) for p in sorted((Path(directory)/'events').iterdir())]
    if any(e['kind']=='attempt' and e['run_id']==run_id for e in previous):
        raise ValueError('run_id already used')
    event={'seq':state['event_count']+1,'previous_sha256':state['last_sha256'],
           'evaluation_id':state['evaluation_id'],'kind':'attempt','run_id':run_id,
           'classification':state['status']}
    _write_new(Path(directory)/'events'/('%06d.json'%event['seq']), event)
    return event


def record_result(plan, rows, directory, *, expected_seal_sha256, expected_registration_sha256,
                  expected_genesis_sha256, expected_head_sha256, expected_event_count, run_id, result_sha256):
    """Record an asserted result fingerprint, without calculating the result.

    This declaration does not prove the output was produced by the evaluator;
    use verify_result_bytes to compare a separate saved artifact afterwards.
    """
    state=inspect(plan, rows, directory, expected_seal_sha256=expected_seal_sha256,
          expected_registration_sha256=expected_registration_sha256, expected_genesis_sha256=expected_genesis_sha256,
          expected_head_sha256=expected_head_sha256, expected_event_count=expected_event_count)
    _text(run_id); _hash(result_sha256)
    # Search existing events before writing, to reject orphan/duplicate results.
    events=[_read(p) for p in sorted((Path(directory)/'events').iterdir())]
    if not any(e['kind']=='attempt' and e['run_id']==run_id for e in events) or any(e['kind']=='result' and e['run_id']==run_id for e in events):
        raise ValueError('result requires unique prior attempt')
    event={'seq':state['event_count']+1,'previous_sha256':state['last_sha256'],
           'evaluation_id':state['evaluation_id'],'kind':'result','run_id':run_id,'result_sha256':result_sha256}
    _write_new(Path(directory)/'events'/('%06d.json'%event['seq']), event)
    return event


def verify_result_bytes(result_path, expected_result_sha256):
    """Check artifact bytes against a declared digest; does not authenticate origin."""
    _hash(expected_result_sha256)
    try:
        value=hashlib.sha256(Path(result_path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError('result artifact missing or unreadable') from exc
    if value != expected_result_sha256:
        raise ValueError('result artifact fingerprint mismatch')
    return {'sha256':value,'origin_authenticated':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Prepare/check local synthetic audit; never score')
    for name in ('plan','dataset','ledger','seal-sha256','registration-sha256','genesis-sha256','head-sha256'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--event-count', type=int, required=True)
    args=parser.parse_args(argv)
    try:
        state=inspect(_read(args.plan),_read(args.dataset),args.ledger,
              expected_seal_sha256=args.seal_sha256, expected_registration_sha256=args.registration_sha256,
              expected_genesis_sha256=args.genesis_sha256, expected_head_sha256=args.head_sha256,
              expected_event_count=args.event_count)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'status':'unknown','error':str(exc)}),file=sys.stderr); return 1
    print(json.dumps(state,sort_keys=True)); return 0


if __name__=='__main__': sys.exit(main())
