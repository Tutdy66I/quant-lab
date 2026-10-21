import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from strategies.research import Candle, Config, run_backtest
from strategies.walk_forward import fold_ranges, risk_summary, stitch, walk_forward


ROOT = pathlib.Path(__file__).resolve().parents[1]


def sample_candles():
    prices = [100, 100, 100, 120, 120, 80, 80, 80] * 6
    return [Candle(1704067200 + i * 86400, p, p, p, p) for i, p in enumerate(prices)]


class WalkForwardTests(unittest.TestCase):
    def test_rolling_train_and_disjoint_tests_cover_partial_tail(self):
        self.assertEqual(list(fold_ranges(30, 4, 10, 6)),
                         [(4, 14, 20), (10, 20, 26), (16, 26, 30)])
        for args in ((30, 4, 0, 6), (30, 4, 10, 0), (14, 4, 10, 6)):
            with self.assertRaises(ValueError):
                list(fold_ranges(*args))

    def test_expanding_training_and_aligned_test_origin(self):
        self.assertEqual(list(fold_ranges(35, 4, 10, 6, window='expanding', first_test_index=18)),
                         [(8, 18, 24), (8, 24, 30), (8, 30, 35)])
        self.assertEqual(list(fold_ranges(35, 4, 10, 6, first_test_index=18)),
                         [(8, 18, 24), (14, 24, 30), (20, 30, 35)])
        for options in ({'window': 'unknown'}, {'first_test_index': 13},
                        {'first_test_index': 35}, {'first_test_index': True}):
            with self.assertRaises(ValueError):
                list(fold_ranges(35, 4, 10, 6, **options))

    def test_expanding_training_only_receives_prices_before_each_test(self):
        candles = sample_candles()
        configs = [Config(2, 3), Config(2, 4)]
        first = walk_forward(candles, configs, 12, 8, baseline_config=Config(2, 3),
                             window='expanding', first_test_index=21)
        for fold in first['folds']:
            left, right = fold['train_indices']
            self.assertEqual(left, 9)
            for config in configs:
                self.assertEqual(fold['training'][config.name],
                                 run_backtest(candles[:right], config, left)['summary'])
        cutoff = first['folds'][1]['test_indices'][0]
        changed = candles[:cutoff] + [Candle(c.timestamp, 1, 1, 1, 1) for c in candles[cutoff:]]
        second = walk_forward(changed, configs, 12, 8, baseline_config=Config(2, 3),
                              window='expanding', first_test_index=21)
        self.assertEqual(first['folds'][0], second['folds'][0])
        self.assertEqual(first['folds'][1]['training'], second['folds'][1]['training'])
        self.assertEqual(first['folds'][1]['selected'], second['folds'][1]['selected'])

    def test_future_prices_do_not_change_selection_or_earlier_fold(self):
        candles = sample_candles()
        configs = [Config(2, 3), Config(2, 4)]
        first = walk_forward(candles, configs, 12, 8, baseline_config=Config(2, 3))
        cutoff = configs[1].warmup + 12
        changed = candles[:cutoff] + [
            Candle(c.timestamp, 1, 1, 1, 1) for c in candles[cutoff:]
        ]
        second = walk_forward(changed, configs, 12, 8, baseline_config=Config(2, 3))
        self.assertEqual(first['folds'][0]['training'], second['folds'][0]['training'])
        self.assertEqual(first['folds'][0]['selected'], second['folds'][0]['selected'])
        later = candles[:cutoff + 8] + [
            Candle(c.timestamp, 500, 500, 500, 500) for c in candles[cutoff + 8:]
        ]
        self.assertEqual(first['folds'][0], walk_forward(later, configs, 12, 8, baseline_config=Config(2, 3))['folds'][0])

    def test_selected_holdout_reuses_shared_engine_flat_start_and_costs(self):
        candles = sample_candles()
        configs = [Config(2, 3), Config(2, 4)]
        result = walk_forward(candles, configs, 12, 8, baseline_config=Config(2, 3))
        by_name = {c.name: c for c in configs}
        for fold in result['folds']:
            left, right = fold['test_indices']
            selected = by_name[fold['selected']]
            expected = run_backtest(candles, selected, left, right)
            self.assertEqual(fold['test'], expected['summary'])
            self.assertEqual(fold['test_trades'], expected['trades'])
            stressed = run_backtest(candles, selected, left, right, fee=0.002, slippage=0.001)
            self.assertEqual(fold['stress']['double_both'], stressed['summary'])
        self.assertEqual(result['aggregate']['selected']['days'], len(candles) - 17)
        self.assertEqual(result['aggregate']['buy_hold']['days'], len(candles) - 17)

    def test_stitch_reinvests_exact_equity_instead_of_adding_returns(self):
        self.assertEqual(stitch([[9000, 11000], [9500, 9000]]), [9000, 11000, 10450, 9900])
        self.assertAlmostEqual(risk_summary(stitch([[9000, 11000], [9500, 9000]]))['return_pct'], -1)

    def test_risk_includes_initial_loss_and_underwater_duration(self):
        result = risk_summary([90, 99, 80, 100], initial=100)
        self.assertEqual(result['max_dd_pct'], 20)
        self.assertEqual(result['max_underwater_days'], 3)
        self.assertAlmostEqual(result['worst_daily_return_pct'], (80 / 99 - 1) * 100, places=4)
        self.assertEqual(result['days'], 4)
        self.assertEqual(risk_summary([100, 100], initial=100)['daily_volatility_pct'], 0)
        for values in ([], [0], [float('nan')]):
            with self.assertRaises(ValueError):
                risk_summary(values)

    def test_ties_are_deterministic_and_duplicate_names_rejected(self):
        candles = [Candle(1704067200 + i * 86400, 100, 100, 100, 100) for i in range(30)]
        a = walk_forward(candles, [Config(2, 4), Config(2, 3)], 10, 6, baseline_config=Config(2, 3))
        self.assertTrue(all(f['selected'] == 'ma2_3' for f in a['folds']))
        with self.assertRaises(ValueError):
            walk_forward(candles, [Config(2, 3), Config(2, 3)], 10, 6, baseline_config=Config(2, 3))

    def test_benchmark_costs_and_absent_baseline_candidate(self):
        candles = sample_candles()
        result = walk_forward(candles, [Config(2, 3)], 12, 8, baseline_config=Config(2, 4))
        curves = []
        from strategies.walk_forward import hold_curve
        for fold in result['folds']:
            left, right = fold['test_indices']
            expected = run_backtest(candles, Config(2, 4), left, right)['summary']
            self.assertEqual(fold['baseline'], expected)
            curve = hold_curve(candles, left, right)
            expected_final = 10000 * 0.999 / (candles[left].open * 1.0005) * candles[right - 1].close * 0.9995 * 0.999
            self.assertAlmostEqual(curve[-1], expected_final)
            curves.append(curve)
        self.assertEqual(result['aggregate']['buy_hold'], risk_summary(stitch(curves)))

    def test_baseline_uses_full_configuration_not_just_display_name(self):
        candles = sample_candles()
        candidate = Config(2, 3, max_daily_vol=0.1, vol_window=2)
        baseline = Config(2, 3, max_daily_vol=0.1, vol_window=4)
        self.assertEqual(candidate.name, baseline.name)
        self.assertNotEqual(candidate, baseline)
        result = walk_forward(candles, [candidate], 12, 8, baseline_config=baseline)
        fold = result['folds'][0]
        left, right = fold['test_indices']
        self.assertEqual(fold['baseline'], run_backtest(candles, baseline, left, right)['summary'])
        self.assertNotEqual(fold['baseline'], fold['test'])

    def test_cli_is_reproducible_from_another_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run([sys.executable, '-m', 'strategies.walk_forward', '--help'],
                           cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            subprocess.run([sys.executable, str(ROOT / 'strategies/walk_forward.py'),
                            '--output-root', temp], cwd=temp, check=True, stdout=subprocess.DEVNULL)
            for path in ('data/walk_forward.json', 'docs/Walk-forward风险报告.md'):
                self.assertEqual((pathlib.Path(temp) / path).read_text(encoding='utf-8'),
                                 (ROOT / path).read_text(encoding='utf-8'))
            report = json.loads((pathlib.Path(temp) / 'data/walk_forward.json').read_text(encoding='utf-8'))
            self.assertEqual(report['data']['last_row_policy'], 'exclude_unconfirmed_snapshot_tail')
            self.assertEqual(report['data']['last_closed_date'], '2026-10-13')
            self.assertEqual(report['folds'][-1]['test']['days'], 62)
            expanded_root = pathlib.Path(temp) / 'expanded'
            subprocess.run([sys.executable, str(ROOT / 'strategies/walk_forward.py'),
                            '--window', 'expanding', '--output-root', str(expanded_root)],
                           cwd=temp, check=True, stdout=subprocess.DEVNULL)
            expanded = json.loads((expanded_root / 'data/walk_forward.json').read_text(encoding='utf-8'))
            self.assertEqual(expanded['assumptions']['window'], 'expanding')
            self.assertTrue(all(f['train_indices'][0] == 91 for f in expanded['folds']))
