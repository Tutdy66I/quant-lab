"""Join three synthetic components without touching files or market inputs.

Time metadata is generated from the toy DGP's declared integer steps, not
independently verified lineage. It illustrates how to wire the diagnostics;
it cannot establish historical point-in-time availability or research admission.
"""
from leakage_diagnostics import Preprocess, Sample, require_clean
from paired_bootstrap import block_sensitivity, paired_bootstrap
from ridge_synthetic import SEED, TOTAL, TRAIN_END, TUNE_END, run_experiment


def declared_timing():
    # The toy DGP declares x_t is known at decision t and y_t resolves one tick
    # later. Ten tick spacing is assumed only for this in-memory example.
    return [Sample(str(t), t * 10, t * 10 - 1, t * 10, t * 10 - 1,
                   t * 10, t * 10 + 1, t * 10 + 1) for t in range(TOTAL)]


def run_comparison(scenario, *, seed=SEED, replicates=2000):
    rows = declared_timing()
    preprocessing = Preprocess(tuple(str(t) for t in range(TRAIN_END)),
                               (TRAIN_END - 1) * 10 + 1)
    require_clean(rows[:TRAIN_END], rows[TRAIN_END:TUNE_END], preprocessing, gap=0)
    require_clean(rows[:TUNE_END], rows[TUNE_END:], preprocessing, gap=0)
    result = run_experiment(scenario, seed)
    if result['validation_time'] != tuple(range(TUNE_END, TOTAL)):
        raise ValueError('validation sample order mismatch')
    a, b = result['losses']['ridge'], result['losses']['mean']
    return {'scenario': scenario, 'seed': seed, 'split': result['split'],
            'selected_alpha': result['selected_alpha'], 'metrics': result['metrics'],
            'paired_loss': 'ridge_squared_error_minus_training_mean_squared_error',
            'iid': paired_bootstrap(a, b, replicates=replicates, seed=seed),
            'moving_blocks': block_sensitivity(a, b, [1, 5, 10], replicates=replicates, seed=seed),
            'limitations': ['synthetic data only; declared time metadata not independently authenticated',
                            'no real-market, trade, or independent holdout claim']}
