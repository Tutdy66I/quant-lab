"""Identity-checked descriptive bootstrap and controlled coverage experiments.

Only one explicitly identified validation window is accepted per call. This
does not authenticate upstream provenance, prove stationarity, validate block
length, or support market inference. The simple Monte Carlo DGP below is not
data, a strategy, or a performance guarantee for other processes.
"""
import hashlib
import json
import math
import random

from paired_bootstrap import paired_bootstrap
from sample_lineage import (Prediction, Sample, make_losses, pair_losses,
                            sample_id_for, prediction_id_for)

SEED = 20261113
MAX_SYNTHETIC_LOSS = 1_000_000.0


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + ' must be finite')
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(name + ' must be finite') from exc
    if not math.isfinite(value):
        raise ValueError(name + ' must be finite')
    return value


def autocorrelation_lag1(differences):
    """Return descriptive centered lag-1 sample autocorrelation, not a test."""
    values = [_finite(x, 'difference') for x in differences]
    if len(values) < 20:
        raise ValueError('at least 20 observations required')
    mean = math.fsum(values) / len(values)
    centered = [x - mean for x in values]
    denominator = math.fsum(x*x for x in centered)
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError('degenerate difference sequence')
    return _finite(math.fsum(a*b for a, b in zip(centered[:-1], centered[1:])) / denominator,
                   'autocorrelation')


def paired_window_bootstrap(samples, losses_a, losses_b, *, expected_dataset_id,
                            expected_dataset_fingerprint, expected_config_hash,
                            split_id, window_id, model_a, model_b, spacing,
                            method='iid', block_length=None, replicates=2000,
                            seed=SEED, confidence=0.95,
                            max_synthetic_loss=MAX_SYNTHETIC_LOSS):
    """Verify identity/order/context, then resample *one* regular window.

    Finite outliers beyond a predeclared engineering bound are rejected, not
    dropped or winsorized; the bound is not a statistical outlier detector.
    Changing a bound after observing a result is not a predeclared analysis.
    """
    if type(spacing) is not int or spacing <= 0:
        raise ValueError('positive integer spacing required')
    if split_id != 'validation':
        raise ValueError('validation split required for out-of-sample comparison')
    limit = _finite(max_synthetic_loss, 'max_synthetic_loss')
    if limit <= 0:
        raise ValueError('max_synthetic_loss must be positive')
    a, b = pair_losses(samples, losses_a, losses_b,
                       expected_dataset_id=expected_dataset_id,
                       expected_dataset_fingerprint=expected_dataset_fingerprint,
                       expected_config_hash=expected_config_hash, split_id=split_id,
                       window_id=window_id, model_a=model_a, model_b=model_b)
    for x, y in zip(a, b):
        if _finite(x, 'loss_a') > limit or _finite(y, 'loss_b') > limit:
            raise ValueError('synthetic loss exceeds predeclared bound')
    times = [s.decision_time for s in samples]
    if any(b_time - a_time != spacing for a_time, b_time in zip(times, times[1:])):
        raise ValueError('irregular or noncontiguous decision times')
    differences = [x - y for x, y in zip(a, b)]
    overlap_count = sum(a_row.target_end_time > b_row.target_start_time
                        for a_row, b_row in zip(samples, samples[1:]))
    # A constant zero/constant difference is degenerate under this stricter
    # interface. A zero *population* difference with nonconstant paired
    # observations remains valid and is used by coverage_experiment.
    correlation = autocorrelation_lag1(differences)
    result = paired_bootstrap(a, b, method=method, block_length=block_length,
                              replicates=replicates, seed=seed, confidence=confidence)
    return {**result, 'synthetic_dataset_id': expected_dataset_id,
            'dataset_fingerprint': expected_dataset_fingerprint,
            'config_hash': expected_config_hash, 'window_id': window_id,
            'split_id': split_id, 'model_a': model_a, 'model_b': model_b,
            'first_sample_id': samples[0].sample_id, 'last_sample_id': samples[-1].sample_id,
            'sample_ids': tuple(row.sample_id for row in samples),
            'adjacent_target_overlap_count': overlap_count,
            'lag1_correlation': correlation, 'spacing': spacing,
            'max_synthetic_loss': limit,
            'alignment': 'strict_identity_and_order_not_independent_source_authentication',
            'dependence_warning': ('iid assumes independent observations; moving blocks require '
                                   'regular spacing and approximate local stationarity; '
                                   'window overlap or repeated selection invalidates naive inference')}


def checked_block_sensitivity(samples, losses_a, losses_b, *, block_lengths, **kwargs):
    """Preserve preselected block list; never choose the most favorable result."""
    lengths = list(block_lengths)
    if not lengths or any(type(v) is not int for v in lengths) or len(set(lengths)) != len(lengths):
        raise ValueError('distinct integer block lengths required')
    if 'method' in kwargs or 'block_length' in kwargs:
        raise ValueError('method and block length are selected by wrapper')
    return [paired_window_bootstrap(samples, losses_a, losses_b, method='moving_block',
                                    block_length=length, **kwargs) for length in lengths]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def synthetic_pairs(n, *, phi, effect, seed):
    """Generate known-mean paired losses via bounded AR(1) differences.

    x_t = phi*x_(t-1) + U(-.2,.2) after 200 burn-in steps; difference is
    effect + x_t, with E[x_t]=0 under this symmetric process. Targets are 0,
    predictions sqrt(8+difference) and sqrt(8), so squared losses differ by
    the generated value up to floating point. Purely synthetic.
    """
    if type(n) is not int or n < 20 or type(seed) is not int:
        raise ValueError('at least 20 samples and integer seed required')
    phi, effect = _finite(phi, 'phi'), _finite(effect, 'effect')
    if not 0 <= phi < .95 or abs(effect) > 1:
        raise ValueError('phi/effect outside supported synthetic bounds')
    config = {'n': n, 'phi': phi, 'effect': effect, 'seed': seed,
              'innovation': 'uniform(-0.2,0.2)', 'burn_in': 200, 'base_loss': 8.0}
    cfg = _digest(config)
    rng = random.Random(seed)
    x = 0.0
    values = []
    for t in range(n + 200):
        x = phi * x + rng.uniform(-0.2, 0.2)
        if t >= 200:
            values.append(effect + x)
    dataset_id = 'synthetic-ar1-' + cfg[:24]
    fingerprint = _digest({'config': config, 'differences': values})
    samples, pa, pb = [], [], []
    for t, difference in enumerate(values):
        sid = sample_id_for(dataset_id, fingerprint, t)
        # Ten ticks per decision, one-tick horizon, so target windows do not
        # overlap and timestamps are a declared synthetic contract.
        sample = Sample(sid, dataset_id, fingerprint, cfg, t*10, t*10,
                        t*10, t*10+1, 0.0, 'validation', 'coverage-window', t)
        samples.append(sample)
        pa.append(Prediction(prediction_id_for(sid, 'coverage-window', 'a', cfg),
                             sid, dataset_id, 'coverage-window', 'a', math.sqrt(8.0+difference)))
        pb.append(Prediction(prediction_id_for(sid, 'coverage-window', 'b', cfg),
                             sid, dataset_id, 'coverage-window', 'b', math.sqrt(8.0)))
    context = {'expected_dataset_id': dataset_id, 'expected_dataset_fingerprint': fingerprint,
               'expected_config_hash': cfg, 'split_id': 'validation',
               'window_id': 'coverage-window', 'model_a': 'a', 'model_b': 'b'}
    la = make_losses(samples, pa, model_id='a', **{k:v for k,v in context.items() if k not in ('model_a','model_b')})
    lb = make_losses(samples, pb, model_id='b', **{k:v for k,v in context.items() if k not in ('model_a','model_b')})
    return tuple(samples), la, lb, context


def _wilson(successes, total):
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z*z/total
    center = (p + z*z/(2*total)) / denominator
    radius = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return [max(0.0, center-radius), min(1.0, center+radius)]


def coverage_experiment(*, trials=50, n=80, phi=0.0, effect=0.0,
                        bootstrap_replicates=200, block_length=8, seed=SEED,
                        confidence=0.95):
    """Estimate interval coverage under one fixed, bounded synthetic process.

    Uses independent per-trial RNG seeds; common datasets for iid/block. The
    Wilson interval describes finite Monte Carlo uncertainty in the coverage
    fraction, not uncertainty about a real-world market statistic.
    """
    if type(trials) is not int or trials < 20 or type(seed) is not int:
        raise ValueError('at least 20 trials and integer seed required')
    phi, effect = _finite(phi, 'phi'), _finite(effect, 'effect')
    rng = random.Random(seed)
    hits = {'iid': 0, 'moving_block': 0}
    widths = {'iid': [], 'moving_block': []}
    trial_records = []
    for _ in range(trials):
        trial_seed = rng.randrange(2**32)
        samples, la, lb, ctx = synthetic_pairs(n, phi=phi, effect=effect, seed=trial_seed)
        trial = {'trial_seed':trial_seed, 'dataset_id':ctx['expected_dataset_id'],
                 'dataset_fingerprint':ctx['expected_dataset_fingerprint'],
                 'config_hash':ctx['expected_config_hash'], 'window_id':ctx['window_id'],
                 'sample_ids':[s.sample_id for s in samples],
                 'decision_times':[s.decision_time for s in samples],
                 'target_values':[s.target_value for s in samples],
                 'prediction_ids_a':[r.prediction.prediction_id for r in la],
                 'prediction_ids_b':[r.prediction.prediction_id for r in lb],
                 'prediction_values_a':[r.prediction.prediction_value for r in la],
                 'prediction_values_b':[r.prediction.prediction_value for r in lb],
                 'loss_values_a':[r.loss_value for r in la], 'loss_values_b':[r.loss_value for r in lb],
                 'intervals':{}}
        for method in ('iid', 'moving_block'):
            out = paired_window_bootstrap(samples, la, lb, **ctx, spacing=10, method=method,
                    block_length=block_length if method == 'moving_block' else None,
                    replicates=bootstrap_replicates, seed=trial_seed, confidence=confidence)
            lo, hi = out['interval']
            hits[method] += lo <= effect <= hi
            widths[method].append(hi-lo)
            trial['intervals'][method] = [lo,hi]
        trial_records.append(trial)
    return {'scope':'synthetic_monte_carlo_not_market_inference', 'seed':seed, 'n':n,
            'phi':phi, 'known_mean_difference':effect, 'trials':trials,
            'bootstrap_replicates':bootstrap_replicates, 'block_length':block_length,
            'confidence':confidence, 'monte_carlo_uncertainty':'95% Wilson binomial interval',
            'methods':{m:{'covered':hits[m], 'coverage':hits[m]/trials,
                          'coverage_wilson_95':_wilson(hits[m],trials),
                          'mean_interval_width':math.fsum(widths[m])/trials} for m in hits},
            'trial_records':trial_records,
            'limitations':'single bounded AR(1) DGP and chosen parameters; coverage is not universal'}
