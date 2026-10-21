"""Fixed-protocol retrospective window sensitivity, isolated from paper accounts.

Run python -m strategies.robustness. No market requests or observer operations.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
from strategies.research import Config, candidates, load_snapshot, run_backtest
from strategies.execution import FEE, SLIPPAGE
from strategies.walk_forward import hold_curve, risk_summary, walk_forward


TRAIN_WINDOWS = (126, 252)
TEST_WINDOWS = (31, 63, 126)
WINDOW_MODES = ('rolling', 'expanding')


def robustness_matrix(candles, configs=None, *, train_windows=TRAIN_WINDOWS,
                      test_windows=TEST_WINDOWS, windows=WINDOW_MODES, baseline_config=None):
    """Compare fixed protocols on one common test range; never select a winner.

    The longest initial train window determines the common validation origin.
    Shorter training windows end at that same origin. Expanding windows then
    retain their own initial start; rolling windows retain their length.
    """
    configs = list(candidates() if configs is None else configs)
    baseline = Config() if baseline_config is None else baseline_config
    train_windows, test_windows, windows = tuple(train_windows), tuple(test_windows), tuple(windows)
    for values in (train_windows, test_windows):
        if (not values or any(type(value) is not int or value < 1 for value in values)
                or len(set(values)) != len(values)):
            raise ValueError('Require unique positive integer windows')
    if (not windows or any(mode not in WINDOW_MODES for mode in windows)
            or len(set(windows)) != len(windows)):
        raise ValueError('Require unique rolling/expanding modes')
    if not configs or len({c.name for c in configs}) != len(configs):
        raise ValueError('Require nonempty candidates with unique names')
    origin = max(c.warmup for c in [*configs, baseline]) + max(train_windows)
    if origin >= len(candles):
        raise ValueError('Not enough history for common warm-up/training and validation')
    period = {'start': candles[origin].date, 'end': candles[-1].date, 'days': len(candles) - origin}
    protocols = []
    for mode in windows:
        for train_bars in train_windows:
            for test_bars in test_windows:
                result = walk_forward(candles, configs, train_bars, test_bars,
                                      window=mode, first_test_index=origin, baseline_config=baseline)
                folds = result['folds']
                protocols.append({
                    'protocol': {'window': mode, 'train_bars': train_bars, 'test_bars': test_bars},
                    'test_period': dict(period), 'aggregate': result['aggregate'],
                    'selection_counts': result['selection_counts'],
                    'fold_activity': {
                        'folds': len(folds),
                        'positive_folds': sum(f['test']['return_pct'] > 0 for f in folds),
                        'zero_trade_folds': sum(not f['test_trades'] for f in folds),
                        'natural_exits': sum(f['test']['completed_trades'] for f in folds),
                        'terminal_liquidations': sum(f['test']['terminal_liquidations'] for f in folds),
                        'selection_switches': sum(a['selected'] != b['selected'] for a, b in zip(folds, folds[1:])),
                    },
                    'folds': [{
                        'fold': f['fold'], 'train_indices': f['train_indices'],
                        'test_indices': f['test_indices'], 'partial_test': f['partial_test'],
                        'selected': f['selected'], 'selected_train': f['training'][f['selected']],
                        'test': f['test'], 'baseline': f['baseline'],
                    } for f in folds],
                })
    continuous = run_backtest(candles, baseline, origin)
    return {
        'schema_version': 1,
        'assumptions': {
            'validation_status': 'retrospective_previously_inspected_snapshot',
            'protocol_status': 'fixed_grid_for_diagnostics_not_prospective_preregistration',
            'train_windows': list(train_windows), 'test_windows': list(test_windows), 'windows': list(windows),
            'first_test_index': origin,
            'common_test_period': 'start_after_longest_initial_training_window_include_partial_tail',
            'selection': 'each_fold_training_return_then_name_no_protocol_winner_selection',
            'fold_boundary': 'flat_start_and_terminal_liquidation_with_costs',
            'aggregation': 'exact_terminal_equity_reinvested',
            'costs': {'fee_per_side': FEE, 'slippage_per_side': SLIPPAGE},
            'continuous_reference': 'flat_at_common_test_start_one_final_liquidation_no_internal_resets',
            'risk_valuation': 'daily_close_including_initial_cash_and_terminal_costs',
            'observer_access': 'none',
        },
        'candidates': [{'name': c.name, 'config': asdict(c)} for c in configs],
        'baseline_config': asdict(baseline), 'test_period': period,
        'continuous_reference': {
            'baseline': risk_summary(continuous['equity']),
            'baseline_summary': continuous['summary'], 'baseline_trades': continuous['trades'],
            'buy_hold': risk_summary(hold_curve(candles, origin, len(candles))),
        },
        'protocols': protocols,
    }


def render_report(result):
    period, assumptions = result['test_period'], result['assumptions']
    lines = [
        '# Quant Lab 2.0：Walk-forward 稳健性对照', '',
        '运行 `python -m strategies.robustness` 离线重建本报告和 `data/walk_forward_robustness.json`。候选、费用、滑点和信号继续使用既有引擎。', '',
        '## 本轮固定研究口径', '',
        f"- 数据 SHA-256（UTF-8/LF）：`{result['data']['sha256']}`；剔除未确认快照末行后 {result['data']['closed_rows']} 根日线。",
        f"- 比较 {len(result['protocols'])} 个协议：初始训练 {assumptions['train_windows']} 天，验证 {assumptions['test_windows']} 天；rolling 训练保持长度，expanding 训练保持首次起点。",
        f"- 所有协议验证期严格相同：{period['start']} 至 {period['end']}，{period['days']} 天。起点为共同预热后加最长初始训练长度；较短训练窗向该起点对齐。验证窗互不重叠且保留尾段，训练只读取验证前的数据。",
        f"- 候选固定为既有 {len(result['candidates'])} 组；每窗只按训练收益选参、同分按名称排序。本轮窗口网格在执行前固定，但历史数据已经看过，不能称为事先登记的全新未见样本试验。全表按固定顺序展示，不自动选最佳窗口。",
        '- 所有分窗结果空仓开始、窗末扣费和滑点清算，再精确复投；同窗固定基线、买入持有亦如此。成本压力固定原训练选择，分别将手续费、滑点或两者翻倍。',
        '- 连续参考也在相同验证起点空仓开始，但内部不人为清仓，只在最终日结算；其差异展示窗口边界的影响，不能把分窗变化全归因于选参或行情，也不能等同纸面观察账户。', '',
        '## 相同验证区间的窗口对照', '',
        '| 模式 | 初始训练 / 验证天数 | 窗数 / 零交易窗 | 自然退出 / 期末结算 | 选参收益 | 选参回撤 | 同窗固定基线收益 | 同窗买入持有收益 | 双成本压力收益 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for row in result['protocols']:
        p, a, activity = row['protocol'], row['aggregate'], row['fold_activity']
        lines.append(f"| {p['window']} | {p['train_bars']} / {p['test_bars']} | {activity['folds']} / {activity['zero_trade_folds']} | {activity['natural_exits']} / {activity['terminal_liquidations']} | {a['selected']['return_pct']:+.2f}% | {a['selected']['max_dd_pct']:.2f}% | {a['baseline']['return_pct']:+.2f}% | {a['buy_hold']['return_pct']:+.2f}% | {a['double_both']['return_pct']:+.2f}% |")
    lines += ['', '## 去掉内部窗末清仓的参考', '',
              '| 参考 | 收益 | 最大回撤 | 最长水下期（天） |', '|---|---:|---:|---:|']
    for label, key in (('连续固定基线', 'baseline'), ('连续买入持有', 'buy_hold')):
        r = result['continuous_reference'][key]
        lines.append(f"| {label} | {r['return_pct']:+.2f}% | {r['max_dd_pct']:.2f}% | {r['max_underwater_days']} |")
    positive = sum(row['aggregate']['selected']['return_pct'] > 0 for row in result['protocols'])
    ahead = sum(row['aggregate']['selected']['return_pct'] > row['aggregate']['baseline']['return_pct'] for row in result['protocols'])
    lines += ['', '## 解释与研究边界', '',
              f"- {len(result['protocols'])} 个固定协议中，{positive} 个选参组合净收益为正，{ahead} 个超过各自同窗固定基线。这些协议共享数据、窗口和候选，彼此并非独立试验，计数不是显著性检验。",
              '- 零交易窗不等于策略稳健；空仓开始及跨窗持仓被切断都会改变信号覆盖。自然退出和强制结算分别列示，尾段长度不同也应一并查看。',
              '- 所有结果均为回顾性窗口敏感性诊断；不能根据表中收益修改候选或挑选窗口，再把同份数据当作新样本外证据。当前历史区间短、交易少，未覆盖足够多独立市场环境。',
              '- 下一步优先建立来源可核验的更长历史数据，再按事先固定的协议观察不同市场区间；新增前瞻数据继续来自独立纸面观察。此报告不读取或改写观察账本，也不改变观察策略。',
              '- 仍按前收盘信号、次开盘成交；单边手续费 0.1%、不利滑点 0.05%。风险仅按日收盘权益，包含初始资金与结算成本，不覆盖日内最低权益。Hummingbot 保持暂停。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT / 'data/btc_usd_1d_raw.json')
    parser.add_argument('--output-root', type=Path, default=ROOT)
    args = parser.parse_args()
    candles = load_snapshot(args.data)
    result = robustness_matrix(candles)
    result['data'] = {
        'source_file': args.data.name,
        'sha256': hashlib.sha256(args.data.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        'hash_normalization': 'utf8_lf', 'closed_rows': len(candles),
        'last_closed_date': candles[-1].date, 'last_row_policy': 'exclude_unconfirmed_snapshot_tail',
    }
    for directory in ('data', 'docs'):
        (args.output_root / directory).mkdir(parents=True, exist_ok=True)
    (args.output_root / 'data/walk_forward_robustness.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')
    (args.output_root / 'docs/Walk-forward稳健性对照.md').write_text(
        render_report(result), encoding='utf-8', newline='\n')
    print(f"Robustness complete: {len(result['protocols'])} fixed protocols; {result['test_period']['days']} common test days")


if __name__ == '__main__':
    main()
