"""Metadata-based synthetic temporal leakage checks, not an information-flow proof.

All times are integer ticks on one common UTC timeline. Label intervals are
half-open [label_start, label_end). Caller-supplied metadata must be truthful;
this checker cannot discover hidden features, dishonest provenance, human
exposure or all possible future perturbations.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Sample:
    key: str
    decision: int
    feature_event: int
    feature_available: int
    window_last_observation: int
    label_start: int
    label_end: int
    label_available: int


@dataclass(frozen=True)
class Preprocess:
    """Actual IDs claimed used to fit preprocessing and time fit was complete."""
    fit_ids: tuple
    fitted_at: int


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str


class LeakageError(ValueError):
    pass


def _integer(value, field):
    if type(value) is not int:
        raise ValueError(field + ' must be an integer tick')


def diagnose(train, validation, preprocess, *, gap):
    """Return all detectable issues; malformed metadata raises ValueError.

    gap is the minimum number of ticks between the latest training label end
    and the first validation decision. A boundary touch is not interval overlap.
    The caller must build each feature window and preprocess fit-ID list from
    actual data lineage, rather than just asserting they are safe.
    """
    if not isinstance(train, (list, tuple)) or not isinstance(validation, (list, tuple)) or not train or not validation:
        raise ValueError('nonempty training and validation sequences required')
    if not isinstance(preprocess, Preprocess) or not isinstance(preprocess.fit_ids, tuple):
        raise ValueError('preprocess metadata with tuple fit_ids required')
    _integer(gap, 'gap')
    _integer(preprocess.fitted_at, 'fitted_at')
    if gap < 0:
        raise ValueError('gap must be nonnegative')
    rows = list(train) + list(validation)
    seen = set()
    for row in rows:
        if not isinstance(row, Sample) or not isinstance(row.key, str) or not row.key or row.key in seen:
            raise ValueError('each sample needs a unique nonempty string key')
        seen.add(row.key)
        for field in ('decision', 'feature_event', 'feature_available', 'window_last_observation',
                      'label_start', 'label_end', 'label_available'):
            _integer(getattr(row, field), field)
        if row.label_start < row.decision or row.label_end <= row.label_start or row.label_available < row.label_end:
            raise ValueError('invalid label interval or availability')
    if any(not isinstance(i, str) for i in preprocess.fit_ids) or len(set(preprocess.fit_ids)) != len(preprocess.fit_ids):
        raise ValueError('fit IDs must be distinct strings')
    issues = []
    def add(code, detail):
        issues.append(Issue(code, detail))
    for row in rows:
        if row.feature_event > row.decision:
            add('feature_after_decision', row.key)
        if row.feature_available < row.feature_event or row.feature_available > row.decision:
            add('feature_availability', row.key)
        if row.window_last_observation > row.decision:
            add('rolling_future_observation', row.key)
        if row.window_last_observation > row.feature_event:
            add('window_after_feature_event', row.key)
    for name, group in (('train', train), ('validation', validation)):
        if any(a.decision >= b.decision for a, b in zip(group, group[1:])):
            add('split_order', name + ' decisions not strictly increasing')
    first_validation = min(row.decision for row in validation)
    if max(row.decision for row in train) >= first_validation:
        add('split_order', 'training must precede validation')
    for row in train:
        if row.label_end > first_validation:
            add('training_label_crosses_validation', row.key)
        if row.label_available > first_validation:
            add('training_label_unavailable', row.key)
        for val in validation:
            if row.label_start < val.label_end and val.label_start < row.label_end:
                add('overlapping_labels', row.key + ':' + val.key)
    if first_validation - max(row.label_end for row in train) < gap:
        add('insufficient_gap', 'label end to first validation decision')
    train_ids = {row.key for row in train}
    if not preprocess.fit_ids or not set(preprocess.fit_ids) <= train_ids:
        add('preprocess_nontraining_data', 'fit IDs must be nonempty training IDs only')
    if preprocess.fitted_at > first_validation:
        add('preprocess_after_validation', 'fit completion after first validation decision')
    fitted = [r for r in train if r.key in preprocess.fit_ids]
    if any(r.feature_available > preprocess.fitted_at for r in fitted):
        add('preprocess_before_data_available', 'fit completed before an included feature was available')
    return tuple(issues)


def require_clean(train, validation, preprocess, *, gap):
    issues = diagnose(train, validation, preprocess, gap=gap)
    if issues:
        raise LeakageError(', '.join(issue.code for issue in issues))


def check_future_perturbation(baseline, perturbed, *, atol=0.0):
    """Flag changed past outputs under a controlled future-only perturbation.

    This requires independently produced, aligned outputs and deterministic
    inference. Passing one perturbation cannot prove absence of future leaks.
    """
    if isinstance(atol, bool) or not isinstance(atol, (int, float)) or not math.isfinite(atol) or atol < 0:
        raise ValueError('finite nonnegative tolerance required')
    if not isinstance(baseline, (tuple, list)) or not isinstance(perturbed, (tuple, list)) or not baseline or len(baseline) != len(perturbed):
        raise ValueError('nonempty aligned outputs required')
    for a, b in zip(baseline, perturbed):
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (a, b)):
            raise ValueError('finite numeric outputs required')
        if abs(a - b) > atol:
            return (Issue('future_perturbation_changed_output', 'past output differs'),)
    return ()
