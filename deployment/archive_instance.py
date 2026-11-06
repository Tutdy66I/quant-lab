"""Offline-only isolated instance provisioning. Network admission is closed."""
import argparse
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from research import archive_normalizer as n
from research import archive_observer as o

SOURCE = 'binance-vision-um-btcusdt'
BLOCKED = {'schema_version': 1, 'source': SOURCE, 'state': 'blocked',
           'reason': 'Prior environment requires investigation; no network admission implemented.'}
NAME = re.compile(r'quant-lab-archive-[a-z0-9][a-z0-9-]{0,47}\Z')


def source_block(gate):
    gate = Path(gate)
    if not gate.is_absolute() or gate.is_symlink() or not gate.is_file():
        raise ValueError('Source gate missing or unsafe')
    blob = gate.read_bytes()
    if json.loads(blob) != BLOCKED:
        raise ValueError('Source gate is invalid or changed')
    return n.sha(blob)


def initialize_source(gate):
    gate = Path(gate)
    if not gate.is_absolute() or gate.is_symlink():
        raise ValueError('Use an absolute independent source-gate file')
    if gate.exists():
        return source_block(gate)
    if not gate.parent.is_dir():
        raise ValueError('Source gate parent must already exist')
    o.publish(gate, n.json_bytes(BLOCKED))
    return source_block(gate)


def task_reservation(gate, task_name, root, gate_hash, *, create=False):
    folder = Path(str(gate) + '.tasks')
    path = folder / (task_name + '.json')
    expected = {'schema_version': 1, 'source': SOURCE, 'task_name': task_name,
                'root': str(root), 'source_gate_sha256': gate_hash}
    if folder.is_symlink() or path.is_symlink():
        raise ValueError('Unsafe task reservation')
    if create:
        folder.mkdir(exist_ok=True)
        # Exclusive creation makes concurrent claims for the same name fail.
        # A partial write is retained and blocks rather than being retried.
        try:
            with path.open('xb') as stream:
                stream.write(n.json_bytes(expected))
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise ValueError('Task identity already reserved or initialization incomplete') from error
    if not path.is_file() or json.loads(path.read_bytes()) != expected:
        raise ValueError('Task reservation missing or belongs to another instance')
    return n.sha(path.read_bytes())


def metadata(root):
    root = Path(root).resolve()
    manager = root.with_name(root.name + '_service')
    marker = manager / 'instance.json'
    config = json.loads((root / 'config.json').read_bytes())
    baseline = json.loads((manager / 'baseline.json').read_bytes())
    # Any sign of the new format requires all three mutually bound records.
    if not (marker.exists() or config.get('instance_id') or baseline.get('instance_id')):
        return None
    if marker.is_symlink() or not marker.is_file():
        raise ValueError('Instance metadata missing or unsafe')
    blob = marker.read_bytes()
    info = json.loads(blob)
    if (info.get('schema_version') != 1 or info.get('root') != str(root)
            or info.get('instance_id') != config.get('instance_id')
            or info.get('instance_id') != baseline.get('instance_id')
            or baseline.get('instance_metadata_sha256') != n.sha(blob)
            or not NAME.fullmatch(info.get('task_name', ''))
            or info.get('task_name') == 'quant-lab-archive-observer'
            or info.get('source') != SOURCE):
        raise ValueError('Instance identity or baseline mismatch')
    gate = Path(info['source_gate'])
    if gate == root or gate == manager or gate.is_relative_to(root) or gate.is_relative_to(manager):
        raise ValueError('Source gate must be outside instance directories')
    if source_block(gate) != info.get('source_gate_sha256'):
        raise ValueError('Source gate changed')
    if task_reservation(gate, info['task_name'], root, info['source_gate_sha256']) != info.get('task_reservation_sha256'):
        raise ValueError('Task reservation changed')
    return info


def prohibit_network(root):
    """No production permit format exists yet; all new-instance requests fail closed."""
    if metadata(root) is not None:
        raise PermissionError('Source access blocked: cross-instance admission is not implemented')


def initialize_empty(root, gate, task_name, start_date, clock=o.now):
    from deployment import archive_service as s
    root = Path(root).resolve()
    manager = s.directory(root)
    gate = Path(gate)
    if not NAME.fullmatch(task_name) or task_name == 'quant-lab-archive-observer':
        raise ValueError('Use a distinct validated instance task name')
    o.day(start_date)
    if o.day(start_date) >= clock().date():
        raise ValueError('Archive start date must be a completed UTC date')
    gate_hash = source_block(gate)
    if gate == root or gate == manager or gate.is_relative_to(root) or gate.is_relative_to(manager):
        raise ValueError('Source gate must be outside instance directories')
    if root.exists() or manager.exists() or root.is_symlink() or manager.is_symlink():
        if not root.is_dir() or not manager.is_dir():
            raise ValueError('Partial initialization; manual investigation required')
        info = metadata(root)
        if not info or info['task_name'] != task_name or info['source_gate'] != str(gate):
            raise ValueError('Existing instance differs or is incomplete')
        state, history = s.checked(root)
        if state['config']['start_date'] != start_date:
            raise ValueError('Instance start date differs')
        return {'status': 'already_initialized', 'instance_id': info['instance_id'],
                'committed_runs': len(state['runs']), 'operations_receipts': len(history)}
    if not root.parent.is_dir():
        raise ValueError('Instance parent must already exist')
    # No automatic cleanup on failure: partial state is evidence and blocks retries.
    reservation_hash = task_reservation(gate, task_name, root, gate_hash, create=True)
    root.mkdir()
    manager.mkdir()
    (root / 'objects').mkdir()
    (root / 'runs').mkdir()
    instance_id = uuid4().hex
    created = n.iso(clock())
    info = {'schema_version': 1, 'instance_id': instance_id, 'root': str(root),
            'task_name': task_name, 'source': SOURCE, 'source_gate': str(gate),
            'source_gate_sha256': gate_hash, 'initialized_at_utc': created,
            'task_reservation_sha256': reservation_hash,
            'enabled_at_utc': None, 'first_observed_at_utc': None,
            'historical_downloads': 'retrospective', 'research_admission': 'insufficient_evidence'}
    blob = n.json_bytes(info)
    o.publish(manager / 'instance.json', blob)
    o.publish(root / 'config.json', n.json_bytes({'version': 1, 'instance_id': instance_id,
        'start_date': start_date, 'created_at_utc': created,
        'allowed_terms_sha256': n.sha(o.APPROVED_TERMS.read_bytes()), 'published_at_utc': None}))
    snapshot = manager / 'snapshots' / 'baseline'
    o.export_snapshot(root, start_date, snapshot)
    o.verify_snapshot(root, snapshot)
    o.publish(manager / 'baseline.json', n.json_bytes({'version': 1, 'root': str(root),
        'adopted_at_utc': created, 'ledger_head_sha256': None, 'cadence_seconds': 3600,
        'automatic_task_enabled': False, 'instance_id': instance_id,
        'instance_metadata_sha256': n.sha(blob),
        'note': 'Offline initialization; not an observation or deployment start.'}))
    s.checked(root)
    return {'status': 'initialized_offline', 'instance_id': instance_id,
            'network_admission': 'blocked', 'enabled_at_utc': None}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    gate = sub.add_parser('initialize-source-block')
    gate.add_argument('--gate', type=Path, required=True)
    init = sub.add_parser('initialize-empty')
    init.add_argument('--root', type=Path, required=True)
    init.add_argument('--gate', type=Path, required=True)
    init.add_argument('--task-name', required=True)
    init.add_argument('--start', required=True)
    args = p.parse_args()
    try:
        result = ({'source_gate_sha256': initialize_source(args.gate)} if args.command == 'initialize-source-block'
                  else initialize_empty(args.root, args.gate, args.task_name, args.start))
        print(json.dumps(result))
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
