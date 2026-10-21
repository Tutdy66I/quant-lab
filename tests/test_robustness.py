import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from strategies.research import Candle, Config, run_backtest
from strategies.robustness import robustness_matrix
from strategies.walk_forward import risk_summary, walk_forward


ROOT = Path(__file__).resolve().parents[1]


def sample_candles():
    prices = [100, 100, 100, 120, 120, 80, 80, 80] * 10
    return [Candle(1704067200 + i * 86400, p, p, p, p) for i, p in enumerate(prices)]


class RobustnessTests(unittest.TestCase):
    def test_every_protocol_uses_identical_validation_dates_and_fixed_candidates(self):
        candles = sample_candles()
        configs = [Config(2, 3), Config(2, 4)]
        result = robustness_matrix(candles, configs, train_windows=(12, 20),
                                   test_windows=(8, 13), baseline_config=Config(2, 3))
        self.assertEqual(len(result['protocols']), 8)
        self.assertNotIn('best_protocol', result)
        for row in result['protocols']:
            options = row['protocol']
            expected = walk_forward(candles, configs, options['train_bars'], options['test_bars'],
                                    window=options['window'], first_test_index=25,
                                    baseline_config=Config(2, 3))
            self.assertEqual(row['aggregate'], expected['aggregate'])
            self.assertEqual(row['test_period'], result['test_period'])
            self.assertEqual(row['folds'][0]['test_indices'][0], 25)
            self.assertEqual(row['folds'][-1]['test_indices'][1], len(candles))
            self.assertEqual(sum(f['test']['days'] for f in row['folds']), len(candles) - 25)
            for a, b in zip(row['folds'], row['folds'][1:]):
                self.assertEqual(a['test_indices'][1], b['test_indices'][0])
            self.assertEqual(sum(row['selection_counts'].values()), len(row['folds']))

    def test_continuous_baseline_matches_engine_without_artificial_fold_exits(self):
        candles = sample_candles()
        config = Config(2, 3)
        result = robustness_matrix(candles, [config], train_windows=(12,),
                                   test_windows=(8,), baseline_config=config)
        expected = run_backtest(candles, config, 16)
        self.assertEqual(result['continuous_reference']['baseline'], risk_summary(expected['equity']))
        self.assertEqual(result['continuous_reference']['baseline_trades'], expected['trades'])
        self.assertEqual(result['continuous_reference']['baseline_summary'], expected['summary'])

    def test_future_prices_do_not_change_earlier_folds_for_any_protocol(self):
        candles = sample_candles()
        options = dict(configs=[Config(2, 3), Config(2, 4)], train_windows=(12, 20),
                       test_windows=(8,), baseline_config=Config(2, 3))
        first = robustness_matrix(candles, **options)
        cutoff = 33
        altered = candles[:cutoff] + [Candle(c.timestamp, 1, 1, 1, 1) for c in candles[cutoff:]]
        second = robustness_matrix(altered, **options)
        for a, b in zip(first['protocols'], second['protocols']):
            self.assertEqual(a['folds'][0], b['folds'][0])
            self.assertEqual(a['folds'][1]['selected'], b['folds'][1]['selected'])

    def test_invalid_or_duplicate_protocols_fail_before_generating_results(self):
        for options in ({'train_windows': ()}, {'train_windows': (12, 12)},
                        {'test_windows': (0,)}, {'windows': ('rolling', 'rolling')},
                        {'windows': ('bad',)}, {'train_windows': (100,)}):
            with self.assertRaises(ValueError):
                robustness_matrix(sample_candles(), [Config(2, 3)],
                                  baseline_config=Config(2, 3), **options)

    def test_matrix_report_is_reproducible_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([sys.executable, str(ROOT / 'strategies/robustness.py'),
                            '--output-root', temp], cwd=temp, check=True, stdout=subprocess.DEVNULL)
            for relative in ('data/walk_forward_robustness.json', 'docs/Walk-forward稳健性对照.md'):
                self.assertEqual((Path(temp) / relative).read_text(encoding='utf-8'),
                                 (ROOT / relative).read_text(encoding='utf-8'))
            result = json.loads((Path(temp) / 'data/walk_forward_robustness.json').read_text(encoding='utf-8'))
            self.assertEqual(len(result['protocols']), 12)
            self.assertEqual(result['data']['last_closed_date'], '2026-10-13')
            self.assertEqual(result['test_period']['start'], '2025-10-02')
            report = (Path(temp) / 'docs/Walk-forward稳健性对照.md').read_text(encoding='utf-8')
            self.assertNotRegex(report, r'\?{2,}|\ufffd')
            self.assertIn('回顾性', report)
