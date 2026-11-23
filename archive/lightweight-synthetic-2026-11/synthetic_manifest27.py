"""Synthetic full-population manifests, anchored by a caller-held digest.

Generate the manifest before predictions/losses. Verification cannot establish
who approved an anchor: ``anchor_source`` is an explicit caller assertion, not
authentication. An adversary controlling all inputs and the expected digest can
replace all evidence. Treat this as trusted-code research, not a security wall.
"""
import hashlib
import json
import math
import re

from sample_lineage import sample_id_for, validate_samples

VERSION = 'synthetic-population-manifest-v1'


def _sha(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('noncanonical manifest data') from exc
    return hashlib.sha256(raw).hexdigest()


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('expected lowercase SHA-256')
    return value


def _text(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError('nonempty canonical text required')
    return value


def _validate_rows(rows):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError('complete nonempty generated rows required')
    for ordinal, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {'time', 'features', 'previous', 'target'}:
            raise ValueError('unexpected synthetic row shape')
        if type(row['time']) is not int or row['time'] != ordinal:
            raise ValueError('rows must contain every ordinal in original order')
        if not isinstance(row['features'], (list, tuple)) or not row['features']:
            raise ValueError('nonempty numeric features required')
        for v in (*row['features'], row['previous'], row['target']):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError('finite numeric synthetic values required')


def build_manifest(rows, *, dataset_id, dataset_fingerprint, config_hash):
    """Create frozen population commitment before any model evaluation.

    Canonical JSON represents a Python tuple and list alike; integer/float
    lexical types are distinct. Dataset fingerprint remains a caller claim;
    independently committed row digests protect actual supplied row content.
    """
    _validate_rows(rows); _text(dataset_id); _digest(dataset_fingerprint); _digest(config_hash)
    entries = []
    for row in rows:
        t = row['time']
        entries.append({'sample_id': sample_id_for(dataset_id, dataset_fingerprint, t),
                        'source_ordinal': t, 'decision_time': 2*t,
                        'feature_available_time': 2*t, 'target_start_time': 2*t,
                        'target_end_time': 2*t+2, 'row_sha256': _sha(row)})
    ids = [e['sample_id'] for e in entries]
    return {'manifest_version': VERSION, 'dataset_id': dataset_id,
            'dataset_fingerprint': dataset_fingerprint, 'config_hash': config_hash,
            'sample_count': len(entries), 'ordered_sample_ids_sha256': _sha(ids),
            'rows_sha256': _sha(rows), 'samples': entries}


def manifest_sha256(manifest):
    return _sha(manifest)


def verify_manifest(rows, manifest, *, expected_manifest_sha256, anchor_source):
    """Compare with a previously fixed digest supplied by a trusted caller.

    Neither a missing digest nor an unrecorded source can certify completeness.
    The identity and authority of the source are external assumptions.
    """
    _digest(expected_manifest_sha256); _text(anchor_source)
    if not isinstance(manifest, dict) or manifest_sha256(manifest) != expected_manifest_sha256:
        raise ValueError('manifest does not match prior trusted anchor')
    try:
        rebuilt = build_manifest(rows, dataset_id=manifest['dataset_id'],
                                 dataset_fingerprint=manifest['dataset_fingerprint'],
                                 config_hash=manifest['config_hash'])
    except (KeyError, TypeError) as exc:
        raise ValueError('invalid manifest') from exc
    if manifest != rebuilt:
        raise ValueError('population differs from anchored manifest')
    return {'manifest_sha256': expected_manifest_sha256, 'anchor_source': anchor_source,
            'rows_sha256': manifest['rows_sha256'], 'sample_count': manifest['sample_count'],
            'trust_note': 'caller asserts anchor source; not independently authenticated'}


def verify_window(samples, rows, manifest, *, expected_manifest_sha256, anchor_source,
                  start, stop, window_id, split_id='validation'):
    """Check a declared contiguous window against the complete anchored universe."""
    receipt = verify_manifest(rows, manifest, expected_manifest_sha256=expected_manifest_sha256,
                              anchor_source=anchor_source)
    if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= len(rows):
        raise ValueError('invalid predeclared window bounds')
    samples = validate_samples(samples, expected_dataset_id=manifest['dataset_id'],
        expected_dataset_fingerprint=manifest['dataset_fingerprint'],
        expected_config_hash=manifest['config_hash'], split_id=split_id, window_id=window_id)
    if len(samples) != stop-start:
        raise ValueError('window has missing or excess samples')
    for row, sample, expected in zip(rows[start:stop], samples, manifest['samples'][start:stop]):
        if (sample.sample_id != expected['sample_id'] or sample.source_ordinal != row['time'] or
            sample.decision_time != expected['decision_time'] or
            sample.feature_available_time != expected['feature_available_time'] or
            sample.target_start_time != expected['target_start_time'] or
            sample.target_end_time != expected['target_end_time'] or
            sample.target_value != row['target']):
            raise ValueError('window differs from anchored rows')
    return dict(receipt, window_id=window_id, split_id=split_id, start=start, stop=stop,
                ordered_sample_ids_sha256=_sha([s.sample_id for s in samples]))
