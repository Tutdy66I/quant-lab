"""Synthetic-only scenario inputs and controlled post-warmup releases."""
from synthetic_paths32 import common_times, make_case as prior_case


FACTORIAL = ('small_short', 'small_long', 'full_short', 'full_long')


def make_case(case, seed):
    if case in FACTORIAL:
        rows, _, _ = prior_case('reverse_full_long' if case.endswith('_long') else 'reverse_full_short', seed)
        size, failure, strict = (27 if case.startswith('small') else 45), (), True
    else:
        base = {'cluster': 'stable_signal', 'insufficient': 'missing'}.get(case, case)
        rows, size, failure = prior_case(base, seed)
        strict = False
        if case == 'cluster':
            for row in rows[94:102]:
                row['target'] += 7.
        if case == 'insufficient':
            size = 18
    dataset = f'synthetic33:{case}:{seed}'
    for row in rows:
        row['synthetic_dataset_id'] = dataset
        row['sample_id'] = f"{dataset}:{row['decision_time']}"
    return rows, dict(initial_limit=size, failure_times=failure, strict_initial_ridge=strict)


def factorial_clock(paths):
    first = paths[FACTORIAL[0]][0]
    clocks = [common_times(first, paths[case][0]) for case in FACTORIAL]
    return set.intersection(*clocks)
