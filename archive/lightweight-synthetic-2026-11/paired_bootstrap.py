"""Descriptive paired bootstrap for synthetic, ordered out-of-sample losses.

The statistic is mean(loss_a - loss_b): negative means A has lower loss.
Inputs are paired per observation and, for moving blocks, must be an equally
spaced contiguous series; the function cannot verify provenance, chronology,
independence, stationarity or whether a chosen block length is sufficient.
These are synthetic-development statistics, not real-market inference.
"""
import math
import random

MIN_SAMPLES = 20
MIN_BLOCKS = 5
MIN_REPLICATES = 100


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + ' must be a finite number')
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(label + ' must be a finite number') from exc
    if not math.isfinite(result):
        raise ValueError(label + ' must be a finite number')
    return result


def _mean(values):
    try:
        result = math.fsum(values) / len(values)
    except (OverflowError, ValueError, ZeroDivisionError) as exc:
        raise ValueError('mean is not finite') from exc
    return _finite(result, 'mean')


def _quantile(sorted_values, probability):
    """Linear interpolation at index (B - 1) * probability."""
    position = (len(sorted_values) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    # Difference-based interpolation can overflow for opposite large values.
    result = ((1.0 - (position - lower)) * sorted_values[lower]
              + (position - lower) * sorted_values[upper])
    return _finite(result, 'quantile')


def _losses(loss_a, loss_b):
    try:
        a, b = list(loss_a), list(loss_b)
    except TypeError as exc:
        raise ValueError('losses must be finite sequences') from exc
    if len(a) != len(b):
        raise ValueError('paired loss arrays must have the same length')
    if len(a) < MIN_SAMPLES:
        raise ValueError('at least 20 paired observations required')
    differences = []
    for x, y in zip(a, b):
        x, y = _finite(x, 'loss_a'), _finite(y, 'loss_b')
        if x < 0 or y < 0:
            raise ValueError('squared losses must be nonnegative')
        differences.append(_finite(x - y, 'paired difference'))
    return differences


def paired_bootstrap(loss_a, loss_b, *, method='iid', block_length=None,
                     replicates=2000, seed=20261113, confidence=0.95):
    """Return percentile interval for the paired mean squared-loss difference.

    iid draws n individual indices. moving_block draws starts uniformly from
    0..n-L inclusive with replacement, concatenates the overlapping blocks,
    and truncates to n.  One RNG is initialized once per function call. Arrays
    must have matching length/order; any temporal gaps must be handled upstream.
    Intervals are descriptive and do not prove predictive or trading value.
    """
    differences = _losses(loss_a, loss_b)
    n = len(differences)
    if method not in ('iid', 'moving_block'):
        raise ValueError('method must be iid or moving_block')
    if type(replicates) is not int or replicates < MIN_REPLICATES:
        raise ValueError('at least 100 replicates required')
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    confidence = _finite(confidence, 'confidence')
    if not 0.0 < confidence < 1.0:
        raise ValueError('confidence must be between 0 and 1')
    if method == 'iid':
        if block_length is not None:
            raise ValueError('block_length must be absent for iid')
    elif (type(block_length) is not int or block_length < 1 or
          n // block_length < MIN_BLOCKS):
        raise ValueError('moving blocks require a positive length and at least five nonoverlapping blocks')

    rng = random.Random(seed)
    estimates = []
    for _ in range(replicates):
        if method == 'iid':
            sample = [differences[rng.randrange(n)] for _ in range(n)]
        else:
            sample = []
            while len(sample) < n:
                start = rng.randrange(n - block_length + 1)
                sample.extend(differences[start:start + block_length])
            sample = sample[:n]
        estimates.append(_mean(sample))
    estimates.sort()
    alpha = (1.0 - confidence) / 2.0
    return {'n': n, 'mean_difference': _mean(differences),
            'interval': [_quantile(estimates, alpha), _quantile(estimates, 1.0 - alpha)],
            'method': method, 'block_length': block_length, 'replicates': replicates,
            'seed': seed, 'confidence': confidence,
            'quantile_method': 'linear_(B-1)*p',
            'scope': 'synthetic_descriptive_not_formal_inference',
            'assumptions': ('paired same-order samples; moving blocks assume regular spacing '
                            'and local stability; block adequacy unproven')}


def block_sensitivity(loss_a, loss_b, block_lengths, *, replicates=2000,
                      seed=20261113, confidence=0.95):
    """Report preselected lengths; do not choose the most favorable interval."""
    # Snapshot both sequences once so generators are not consumed per length.
    a, b = list(loss_a), list(loss_b)
    lengths = list(block_lengths)
    if not lengths or any(type(value) is not int for value in lengths) or len(set(lengths)) != len(lengths):
        raise ValueError('distinct block lengths required')
    return [paired_bootstrap(a, b, method='moving_block', block_length=length,
                             replicates=replicates, seed=seed, confidence=confidence)
            for length in lengths]
