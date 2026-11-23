"""Fixed synthetic rolling experiment; stdout contains full identity records."""
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import sys
import time

if len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    print('Use Python -I -S -B run_rolling_experiments.py; no overrides accepted', file=sys.stderr)
    sys.exit(2)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from coverage_synthetic import checked_block_sensitivity, paired_window_bootstrap
from rolling_synthetic import run_rolling

SEED = 20261113
REPLICATES = 500


def json_value(value):
    if is_dataclass(value):
        return asdict(value)
    raise TypeError('unsupported synthetic result')


def main():
    results, seconds = {}, {}
    for scenario in ('signal', 'no_signal', 'high_noise', 'distribution_shift'):
        started = time.perf_counter()
        run = run_rolling(scenario, seed=SEED)
        for window in run['windows']:
            context = dict(expected_dataset_id=run['dataset_id'],
                           expected_dataset_fingerprint=run['dataset_fingerprint'],
                           expected_config_hash=run['config_hash'], split_id='validation',
                           window_id=window['window_id'], model_a='ridge', spacing=2,
                           seed=SEED, replicates=REPLICATES)
            window['bootstrap'] = {}
            for baseline in ('mean', 'persistence'):
                args = (window['validation_samples'], window['losses']['ridge'], window['losses'][baseline])
                window['bootstrap'][baseline] = {
                    'iid': paired_window_bootstrap(*args, model_b=baseline, method='iid', **context),
                    'moving_blocks': checked_block_sensitivity(*args, model_b=baseline,
                                        block_lengths=[1, 3, 5], **context)}
        results[scenario] = run
        seconds[scenario] = round(time.perf_counter() - started, 6)
    sources = ('sample_lineage.py', 'rolling_synthetic.py', 'coverage_synthetic.py',
               'ridge_synthetic.py', 'paired_bootstrap.py', 'run_rolling_experiments.py')
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}
    print(json.dumps({'scope':'synthetic_exploratory_not_market_inference', 'python':sys.version,
                      'seed':SEED, 'bootstrap_replicates':REPLICATES, 'source_sha256':hashes,
                      'scenario_seconds':seconds, 'results':results}, default=json_value, sort_keys=True,
                     allow_nan=False))


if __name__ == '__main__':
    main()
