"""Outlier-resistant online diagnostics on observations already available.

The interface accepts observed feature values and *arrived* squared errors,
not rows containing unreleased labels. Relation is shorthand for residual
scale drift, which can also respond to target noise or other misspecification.
"""
import math
from statistics import median, quantiles

import prequential_synthetic31 as baseline
from ridge_synthetic import predict_ridge

MIN_OBSERVED = 20
AXES = ('mean', 'variance', 'relation')
DEV_SEEDS = tuple(range(61100, 61132))
CALIBRATION = {
    'family_alarm_goal': 0.2,
    'candidate_multipliers': (1.0, 1.25, 1.5, 2.0, 3.0),
    'base_thresholds': {'mean': 0.8, 'variance': 2.5, 'relation': 2.0},
}


def _numbers(values, *, nonnegative=False):
    values = tuple(values)
    if any(type(v) not in (int, float) or not math.isfinite(v)
           or (nonnegative and v < 0) for v in values):
        raise ValueError('finite observed values required')
    return values


def _iqr(values):
    low, _, high = quantiles(values, n=4, method='inclusive')
    return high - low


def score(features_ref, features_window, losses_ref, losses_window):
    """Return robust scores, with None where an axis cannot be evaluated.

    Location is the median shift in reference IQR units standardized by
    1.349. Dispersion compares squared IQR ratios in both directions. A
    collapsed current IQR is represented by None plus an explicit flag.
    Relation compares medians of absolute residuals, resisting minority
    contamination. A zero scale is floored to keep scores finite.
    """
    features_ref = _numbers(features_ref)
    features_window = _numbers(features_window)
    losses_ref = _numbers(losses_ref, nonnegative=True)
    losses_window = _numbers(losses_window, nonnegative=True)
    scores = dict.fromkeys(AXES)
    scores['collapsed_feature_iqr'] = False
    if min(len(features_ref), len(features_window)) >= MIN_OBSERVED:
        ref_iqr = _iqr(features_ref)
        current_iqr = _iqr(features_window)
        if ref_iqr > 1e-12:
            scores['mean'] = abs(median(features_window)-median(features_ref))/(ref_iqr/1.349)
            if current_iqr <= 1e-12:
                scores['collapsed_feature_iqr'] = True
            else:
                scores['variance'] = max(current_iqr/ref_iqr, ref_iqr/current_iqr)**2
    if min(len(losses_ref), len(losses_window)) >= MIN_OBSERVED:
        scores['relation'] = median(tuple(map(math.sqrt, losses_window)))/max(
            median(tuple(map(math.sqrt, losses_ref))), 1e-9)
    return scores


def _evaluate_scores(scores, thresholds):
    return {
        axis: (True if axis == 'variance' and scores['collapsed_feature_iqr']
               else None if scores[axis] is None else scores[axis] >= thresholds[axis])
        for axis in AXES
    }


def evaluate(features_ref, features_window, losses_ref, losses_window, thresholds):
    """Threshold the robust scores, leaving unavailable axes explicit."""
    if not isinstance(thresholds, dict) or set(thresholds) != set(AXES):
        raise ValueError('exact robust threshold axes required')
    if any(x <= 0 for x in _numbers(thresholds.values())) or thresholds['variance'] < 1:
        raise ValueError('invalid robust thresholds')
    return _evaluate_scores(score(features_ref, features_window, losses_ref, losses_window),
                            thresholds)


class Confirmation:
    """Confirm an axis after two alarming checks with fresh evidence.

    Freshness is optional for direct calls. An unavailable observation resets
    its axis. An unchanged available window is not a second confirmation.
    An already confirmed alarm remains true while its prior evidence is held.
    """
    def __init__(self):
        self._streak = dict.fromkeys(AXES, 0)

    def update(self, axes, *, fresh=None):
        if set(axes) != set(AXES) or any(v is not None and type(v) is not bool for v in axes.values()):
            raise ValueError('exact three-state alarm axes required')
        if fresh is not None and (set(fresh) != set(AXES) or any(type(v) is not bool for v in fresh.values())):
            raise ValueError('exact boolean freshness axes required')
        confirmed = {}
        for axis in AXES:
            flag = axes[axis]
            if not flag:
                self._streak[axis] = 0
            elif fresh is None or fresh[axis]:
                self._streak[axis] += 1
            confirmed[axis] = None if flag is None else self._streak[axis] >= 2
        return confirmed


def _development_scores(seed):
    # The baseline replay produces feedback only when it arrives. Each check
    # explicitly filters to its as-of time. The training reference uses the
    # subset with labels already available before the initial prediction.
    rows = baseline.generate('stable_signal', seed, n=180)
    cut = baseline.CONFIG['warmup']
    window = baseline.CONFIG['monitor_window']
    ref_x = [row['features'][0] for row in rows[:cut]]
    labeled = [row for row in rows[:cut] if row['label_available_time'] < cut]
    model, _ = baseline._fit(labeled, cut-1)
    ref_losses = [(row['target']-predict_ridge(model, row['features']))**2
                  for row in labeled]
    run = baseline.run_online(rows)
    feedback = [f for f in run['feedback'] if f['strategy'] == 'fixed_ridge']
    for event in run['events']:
        if event['event_type'] != 'alarm':
            continue
        tick = event['alarm_time']
        x = [r['features'][0] for r in rows[max(cut, tick-window+1):tick+1]]
        arrived = [f['squared_loss'] for f in feedback if f['label_time'] <= tick][-window:]
        yield score(ref_x, x, ref_losses, arrived)


def calibrate():
    """Choose the smallest multiplier meeting the development alarm budget."""
    samples = [tuple(_development_scores(seed)) for seed in DEV_SEEDS]
    candidates = []
    for multiplier in CALIBRATION['candidate_multipliers']:
        thresholds = {k: x*multiplier for k, x in CALIBRATION['base_thresholds'].items()}
        alarms = sum(any(any(_evaluate_scores(s, thresholds).values()) for s in run)
                     for run in samples)
        candidates.append({'multiplier': multiplier, 'thresholds': thresholds,
                           'runs_with_alarm': alarms, 'runs': len(samples)})
    acceptable = [c for c in candidates if c['runs_with_alarm']/c['runs']
                  <= CALIBRATION['family_alarm_goal']]
    chosen = acceptable[0] if acceptable else candidates[-1]
    return {'thresholds': chosen['thresholds'], 'selected': chosen,
            'goal_met': bool(acceptable), 'candidates': candidates,
            'development_seeds': DEV_SEEDS, 'calibration': CALIBRATION,
            'scope': 'stable-signal development only; per-run any-check alarm fraction'}
