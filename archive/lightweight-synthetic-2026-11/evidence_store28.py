"""Local accidental-overwrite protection for synthetic research evidence.

The caller must retain the returned manifest digest outside the run directory.
This is a trusted-code audit aid, not a tamper-proof store or independent trust
root. A file owner can replace all files, code and the caller's digest together.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

KINDS = frozenset({'source', 'configuration', 'log', 'result'})
MANIFEST = 'evidence-manifest.json'


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', value):
        raise ValueError('simple evidence name required')
    if value in ('.', '..', MANIFEST) or value.endswith('.'):
        raise ValueError('reserved evidence name')
    return value


def _reviewer(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,39}', value):
        raise ValueError('reviewer identifier required')
    return value


def _run_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'run-[0-9a-f]{32}', value):
        raise ValueError('invalid run ID')
    return value


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or non-regular evidence: ' + path.name)
    return path.read_bytes()


def create_run(base_dir, reviewer):
    """Atomically reserve a fresh reviewer-specific run directory."""
    reviewer = _reviewer(reviewer)
    base = Path(base_dir)
    if base.is_symlink() or not base.is_dir():
        raise ValueError('existing trusted base directory required')
    run_id = 'run-' + uuid.uuid4().hex
    directory = base / (reviewer + '-' + run_id)
    directory.mkdir(exist_ok=False)
    return {'run_id': run_id, 'reviewer': reviewer, 'directory': str(directory)}


def _validate_directory(directory, run_id, reviewer):
    directory = Path(directory)
    if (directory.name != _reviewer(reviewer) + '-' + _run_id(run_id) or
            directory.is_symlink() or not directory.is_dir()):
        raise ValueError('run directory identity mismatch')
    return directory


def save_artifact(directory, run_id, reviewer, name, data):
    """Write once with O_EXCL; failures never overwrite an existing artifact."""
    directory = _validate_directory(directory, run_id, reviewer)
    _name(name)
    if not isinstance(data, bytes):
        raise ValueError('artifact content must be bytes')
    if (directory / MANIFEST).exists():
        raise ValueError('run already finalized; start a fresh run')
    path = directory / name
    with open(path, 'xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {'name': name, 'size': len(data), 'sha256': _digest(data)}


def finalize_run(directory, run_id, reviewer, artifacts):
    """Write a manifest exclusively and return its raw-byte SHA-256.

    ``artifacts`` maps each simple filename to its evidence category; all four
    required categories must be present. Verification compares the same expected
    names and types, rather than allowing the manifest to define completeness.
    """
    directory = _validate_directory(directory, run_id, reviewer)
    if not isinstance(artifacts, dict) or not artifacts or set(artifacts.values()) != KINDS:
        raise ValueError('source, configuration, log and result evidence required')
    for name in artifacts:
        _name(name)
    actual = {p.name for p in directory.iterdir()}
    if actual != set(artifacts):
        raise ValueError('missing or unexpected evidence before finalization')
    items = []
    for name, kind in sorted(artifacts.items()):
        raw = _read_regular(directory / name)
        items.append({'name': name, 'kind': kind, 'size': len(raw), 'sha256': _digest(raw)})
    manifest = {'version': 'synthetic-evidence-v1', 'run_id': run_id, 'reviewer': reviewer,
                'artifacts': items, 'trust': 'local digest is not an independent trust root'}
    raw = (json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('utf-8')
    with open(directory / MANIFEST, 'xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return _digest(raw)


def verify_run(directory, run_id, reviewer, artifacts, expected_manifest_sha256):
    """Verify original bytes, expected file inventory and caller-held digest."""
    directory = _validate_directory(directory, run_id, reviewer)
    if (not isinstance(expected_manifest_sha256, str) or
            not re.fullmatch(r'[0-9a-f]{64}', expected_manifest_sha256)):
        raise ValueError('previously retained manifest digest required')
    if not isinstance(artifacts, dict) or not artifacts or set(artifacts.values()) != KINDS:
        raise ValueError('independent expected inventory required')
    raw = _read_regular(directory / MANIFEST)
    if _digest(raw) != expected_manifest_sha256:
        raise ValueError('manifest hash mismatch')
    try:
        manifest = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid manifest encoding') from exc
    if (manifest.get('version') != 'synthetic-evidence-v1' or manifest.get('run_id') != run_id or
            manifest.get('reviewer') != reviewer or not isinstance(manifest.get('artifacts'), list)):
        raise ValueError('manifest identity mismatch')
    if {p.name for p in directory.iterdir()} != set(artifacts) | {MANIFEST}:
        raise ValueError('missing or unexpected evidence')
    items = manifest['artifacts']
    if len(items) != len(artifacts) or any(not isinstance(item, dict) for item in items):
        raise ValueError('manifest inventory mismatch')
    seen = set()
    for item in items:
        name = _name(item.get('name'))
        if name in seen or name not in artifacts or item.get('kind') != artifacts[name]:
            raise ValueError('duplicate or unexpected manifest entry')
        seen.add(name)
        data = _read_regular(directory / name)
        if item.get('size') != len(data) or item.get('sha256') != _digest(data):
            raise ValueError('evidence content mismatch: ' + name)
    if seen != set(artifacts):
        raise ValueError('incomplete manifest inventory')
    return {'run_id': run_id, 'reviewer': reviewer, 'files': len(seen),
            'manifest_sha256': expected_manifest_sha256,
            'origin_authenticated': False}
