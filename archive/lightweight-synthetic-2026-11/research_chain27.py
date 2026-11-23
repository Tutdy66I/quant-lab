"""Anchored synthetic research path. Anchor custody remains an external trust assumption."""
from dataclasses import asdict, is_dataclass
import hashlib
import json

from coverage_synthetic import paired_window_bootstrap
from ridge_synthetic import ALPHAS, SEED, generate
from rolling_synthetic import DEFAULT_WINDOWS, _canonical_hash, run_rolling
from sample_lineage import make_losses
from synthetic_manifest27 import build_manifest, verify_manifest, verify_window


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    raise TypeError('unsupported receipt value')


def digest(value):
    return hashlib.sha256(json.dumps(value, default=_json_default, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def prepare_population(scenario, seed=SEED):
    """Pre-prediction population preparation; caller must preserve its anchor separately."""
    rows = generate(scenario, seed)
    dataset_id = 'synthetic-ridge-v1:' + scenario + ':' + str(seed)
    config = {'schema':'rolling-synthetic-v1', 'generator':'ridge_synthetic.generate-v1',
              'scenario':scenario, 'seed':seed, 'windows':[asdict(w) for w in DEFAULT_WINDOWS],
              'gap_ticks':1, 'alphas':list(ALPHAS), 'refit':'training_only',
              'target_interval':'[2*t,2*t+2)', 'feature_available':'2*t'}
    fingerprint = _canonical_hash({'schema':'synthetic-generated-rows-v1',
                                    'scenario':scenario, 'seed':seed, 'rows':rows})
    manifest = build_manifest(rows, dataset_id=dataset_id, dataset_fingerprint=fingerprint,
                              config_hash=_canonical_hash(config))
    return rows, manifest


def evaluate_verified(rows, manifest, run, *, expected_manifest_sha256, anchor_source, replicates=200):
    """Run bootstrap only after the precommitted population and full windows validate."""
    reference = verify_manifest(rows, manifest, expected_manifest_sha256=expected_manifest_sha256,
                                anchor_source=anchor_source)
    if (run['dataset_id'] != manifest['dataset_id'] or
        run['dataset_fingerprint'] != manifest['dataset_fingerprint'] or
        run['config_hash'] != manifest['config_hash'] or
        _canonical_hash(run['config']) != manifest['config_hash'] or
        len(run['windows']) != len(DEFAULT_WINDOWS)):
        raise ValueError('run differs from precommitted dataset or configuration')
    # Replay the fixed trusted development pipeline so callers cannot supply
    # fabricated fits, metrics, selections or intermediate records.
    expected_run = run_rolling(run['config']['scenario'], seed=run['config']['seed'])
    if digest(run) != digest(expected_run):
        raise ValueError('run does not match deterministic development pipeline')
    checked = []
    for plan, window in zip(DEFAULT_WINDOWS, run['windows']):
        if window['bounds'] != asdict(plan) or window['window_id'] != plan.window_id:
            raise ValueError('unexpected window plan')
        for key, start, stop, split, cutoff in (
                ('train_samples', plan.train_start, plan.train_stop - 1, 'train', 2*plan.tune_start - 1),
                ('tune_samples', plan.tune_start, plan.tune_stop - 1, 'tune', 2*plan.validation_start - 1)):
            verify_window(window[key], rows, manifest, expected_manifest_sha256=expected_manifest_sha256,
                          anchor_source=anchor_source, start=start, stop=stop,
                          window_id=plan.window_id, split_id=split)
            if window[key][-1].target_end_time > cutoff:
                raise ValueError('label or embargo crosses split boundary')
        sample_receipt = verify_window(window['validation_samples'], rows, manifest,
            expected_manifest_sha256=expected_manifest_sha256, anchor_source=anchor_source,
            start=plan.validation_start, stop=plan.validation_stop, window_id=plan.window_id)
        for name in ('ridge', 'mean', 'persistence'):
            expected_losses = make_losses(window['validation_samples'], window['predictions'][name],
                expected_dataset_id=run['dataset_id'], expected_dataset_fingerprint=run['dataset_fingerprint'],
                expected_config_hash=run['config_hash'], split_id='validation', window_id=plan.window_id,
                model_id=name)
            if tuple(window['losses'][name]) != expected_losses:
                raise ValueError('predictions and bound losses differ')
        context = dict(expected_dataset_id=run['dataset_id'],
            expected_dataset_fingerprint=run['dataset_fingerprint'], expected_config_hash=run['config_hash'],
            split_id='validation', window_id=plan.window_id, model_a='ridge', model_b='mean',
            spacing=2, seed=SEED, replicates=replicates)
        comparison = paired_window_bootstrap(window['validation_samples'], window['losses']['ridge'],
                    window['losses']['mean'], method='moving_block', block_length=3, **context)
        checked.append({'window':plan.window_id, 'population_check':sample_receipt,
                        'bootstrap':comparison})
    payload = {'version':'synthetic-research-receipt-v1', 'population':reference,
               'run_sha256':digest(run), 'rows_sha256':digest(rows),
               'manifest_sha256':expected_manifest_sha256, 'replicates':replicates,
               'windows':checked, 'scope':'synthetic_exploratory_only'}
    return payload, {'payload_sha256':digest(payload), 'anchor_sha256':expected_manifest_sha256}


def verify_receipt(payload, receipt, *, expected_manifest_sha256, rows, run):
    if not isinstance(payload, dict) or not isinstance(receipt, dict):
        raise ValueError('missing payload or receipt')
    if (set(receipt) != {'payload_sha256','anchor_sha256'} or
        receipt['anchor_sha256'] != expected_manifest_sha256 or
        payload.get('manifest_sha256') != expected_manifest_sha256 or
        payload.get('run_sha256') != digest(run) or payload.get('rows_sha256') != digest(rows) or
        receipt['payload_sha256'] != digest(payload)):
        raise ValueError('receipt does not match result or anchor')
    return True


def run_verified(scenario, manifest, *, expected_manifest_sha256, anchor_source, seed=SEED, replicates=200):
    rows = generate(scenario, seed)
    # Crucially this check precedes run_rolling, which computes predictions and losses.
    verify_manifest(rows, manifest, expected_manifest_sha256=expected_manifest_sha256,
                    anchor_source=anchor_source)
    run = run_rolling(scenario, seed=seed)
    payload, receipt = evaluate_verified(rows, manifest, run,
        expected_manifest_sha256=expected_manifest_sha256, anchor_source=anchor_source,
        replicates=replicates)
    return run, payload, receipt
