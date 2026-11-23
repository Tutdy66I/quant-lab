"""Fixed artificial paths for point-in-time delay attribution."""

from prequential_synthetic31 import generate

CASES = {
    'stable_signal': 'stable_signal',
    'stable_no_signal': 'stable_no_signal',
    'high_noise': 'high_noise',
    'single_outlier': 'stable_signal',
    'outliers': 'outliers',
    'transient': 'transient',
    'gradual': 'gradual',
    'mean_shift': 'mean_shift',
    'reverse_full_short': 'relation_reverse',
    'reverse_full_long': 'relation_reverse',
    'reverse_small_short': 'relation_reverse',
    'reverse_small_long': 'relation_reverse',
    'reverse_native_long': 'relation_reverse',
    'backlog': 'relation_reverse',
    'missing': 'relation_reverse',
    'training_failure': 'relation_reverse',
}


def make_case(case, seed):
    rows = generate(CASES[case], seed)
    dataset = f'synthetic32:{case}:{seed}'
    for t, row in enumerate(rows):
        row['synthetic_dataset_id'] = dataset
        row['sample_id'] = f'{dataset}:{t}'
        if case == 'reverse_native_long' or (case.endswith('_long') and t >= 60):
            row['label_available_time'] = t + 36
        if case == 'backlog' and 82 <= t < 130:
            row['label_available_time'] = 135 + (t % 5)
        if case == 'missing' and t % 3:
            row['label_available_time'] = None
        if case == 'single_outlier' and t == 94:
            row['target'] += 7.0
    return rows, (18 if '_small_' in case else 45), (100, 120, 140) if case == 'training_failure' else ()


def common_times(left, right):
    """Require identical complete potential paths before matching observed clocks."""
    def path(rows):
        return [(r['decision_time'], r['feature_available_time'], r['target_end_time'],
                 r['features'], r['target']) for r in rows]
    if not left or path(left) != path(right):
        raise ValueError('paired cases must have the same complete latent path')
    horizon = left[-1]['decision_time']
    available = lambda row: row['label_available_time'] is not None and row['label_available_time'] <= horizon
    common = {a['decision_time'] for a, b in zip(left, right)
              if a['decision_time'] >= 60 and available(a) and available(b)}
    if not common:
        raise ValueError('no common scoreable decisions')
    return common
