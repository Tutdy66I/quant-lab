"""Run only the four fixed, in-memory synthetic examples; emit JSON."""
import json
from pathlib import Path
import sys
import time

if len(sys.argv) != 1 or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    print('Use Python -I -S -B run_experiments.py; no overrides accepted', file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from synthetic_research import run_comparison


def main():
    results, seconds = {}, {}
    for scenario in ('signal', 'no_signal', 'high_noise', 'distribution_shift'):
        start = time.perf_counter()
        results[scenario] = run_comparison(scenario, seed=20261113, replicates=2000)
        seconds[scenario] = round(time.perf_counter() - start, 6)
    print(json.dumps({'scope': 'synthetic_development_not_market_inference',
                      'python': sys.version, 'seed': 20261113, 'replicates': 2000,
                      'scenario_seconds': seconds, 'results': results}, sort_keys=True))


if __name__ == '__main__':
    main()
