"""Run the frozen long-history protocol offline, keeping original artifacts intact."""
import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from strategies.execution import FEE, SLIPPAGE
from strategies.history import DATASET, ROOT, sha256, verify_dataset
from strategies.research import Config, INITIAL_CASH, candidates, run_backtest
from strategies.walk_forward import hold_curve, risk_summary, stitch, walk_forward

PROTOCOL = ROOT / 'docs/history_protocol_v1.json'


def annual_metrics(dates, curve):
    """Slice one equity path; carry the prior day's equity across year boundaries."""
    if len(dates) != len(curve):
        raise ValueError('Date/equity lengths differ')
    result = []
    start = 0
    for stop in range(1, len(dates) + 1):
        if stop == len(dates) or dates[stop][:4] != dates[start][:4]:
            initial = curve[start - 1] if start else INITIAL_CASH
            result.append({'year': dates[start][:4], 'start': dates[start], 'end': dates[stop - 1],
                           'metrics': risk_summary(curve[start:stop], initial)})
            start = stop
    return result


def study(candles, protocol):
    configs = candidates()
    if (protocol['schema_version'] != 1 or protocol['candidates'] != [asdict(c) for c in configs]
            or protocol['baseline'] != asdict(Config())
            or protocol['fee_per_side'] != FEE or protocol['slippage_per_side'] != SLIPPAGE
            or protocol['train_bars'] != 252 or protocol['test_bars'] != 63
            or protocol['window'] != 'rolling' or protocol['include_partial_tail'] is not True):
        raise ValueError('Protocol differs from frozen candidates, windows or execution costs')
    if candles[0].date != protocol['data_start'] or candles[-1].date != protocol['data_end']:
        raise ValueError('History coverage differs from fixed protocol')
    start = next(i for i, candle in enumerate(candles) if candle.date == protocol['validation_start'])
    result = walk_forward(candles, configs, 252, 63, first_test_index=start)
    by_name = {config.name: config for config in configs}
    parts = {key: [] for key in ('selected', 'baseline', 'buy_hold', 'double_both')}
    for fold in result['folds']:
        left, right = fold['test_indices']
        config = by_name[fold['selected']]
        for key, chosen, fee, slip in (
                ('selected', config, FEE, SLIPPAGE), ('baseline', Config(), FEE, SLIPPAGE),
                ('double_both', config, FEE * 2, SLIPPAGE * 2)):
            parts[key].append(run_backtest(candles[:right], chosen, left, right, fee, slip)['equity'])
        parts['buy_hold'].append(hold_curve(candles, left, right))
    curves = {key: stitch(value) for key, value in parts.items()}
    for key, curve in curves.items():
        if risk_summary(curve) != result['aggregate'][key]:
            raise ValueError('Reconstructed equity differs from shared walk-forward engine')
    curves['continuous_baseline'] = run_backtest(candles, Config(), start)['equity']
    curves['continuous_buy_hold'] = hold_curve(candles, start, len(candles))
    dates = [candle.date for candle in candles[start:]]
    return {
        'validation_status': 'retrospective_fixed_protocol_not_prospective_evidence',
        'protocol': protocol,
        'aggregate': {key: risk_summary(curve) for key, curve in curves.items()},
        'calendar_phases': {key: annual_metrics(dates, curve) for key, curve in curves.items()},
        'selection_counts': result['selection_counts'],
        'folds': result['folds'],
        'daily_equity': [{'date': date, **{key: curve[i] for key, curve in curves.items()}}
                         for i, date in enumerate(dates)],
    }


def render_report(result):
    manifest = result['dataset']
    lines = ['# 长期历史数据与固定协议验证', '',
             f"数据：Binance 现货 BTC/USDT，UTC 日线 {manifest['start_date']} 至 {manifest['end_date']}，共 {manifest['rows']} 根。",
             f"采集完成时间：{manifest['collected_at_utc']}。原始包、官方校验文件、逐包采集时间均保存在版本目录。",
             f"标准化 CSV SHA-256：`{manifest['normalized_sha256']}`。",
             f"协议 SHA-256：`{result['protocol_sha256']}`，固定时间 {result['protocol']['frozen_at_utc']}。", '',
             '来源与字段说明：[Binance 官方归档文档](https://github.com/binance/binance-public-data)。', '',
             '## 数据与研究边界', '',
             '- 数据完整性已逐月校验官方 SHA-256；离线验证从原始 ZIP 重建 CSV，检查每日连续性、OHLC 范围、完整月及已收盘时间。无补值、无拼接其他交易所。',
             '- 官方归档可能修订；本地版本保留采集时的字节和指纹。校验和证明传输完整性，不能替代第二来源价格核验。本版尚未完成跨来源交叉核验。',
             '- BTC/USDT 与原 Kraken BTC/USD 的交易场所、报价币不同，本报告独立展示，不能把差异全部解释为历史长度带来的改善。',
             '- 协议在本轮下载及结果计算前固定，但历史行情早已公开，候选也已经研究过；这仍是回顾性研究，不是前瞻验证或正式独立预注册。',
             '- 仅沿用原有 11 个候选、252 天滚动训练、63 天验证、训练净收益选参，费用和滑点不变。验证从 2019-01-01 开始；保留尾窗，成本压力冻结原选择。',
             '- 阶段按 UTC 自然年划分，2026 年只到 9 月末；同一条权益曲线按年切片，年界不清仓、不重置资金，年初基数取上一日权益。没有按结果重新选择牛熊区间。',
             '- 分窗结果每 63 天空仓开始并清算；连续参考只有最终清算。收益、回撤均按日收盘估值，不含日内风险。', '',
             '## 全验证区间', '',
             '| 方案 | 净收益 | 最大回撤 | 最长水下天数 |', '|---|---:|---:|---:|']
    names = {'selected': '训练选参', 'baseline': '同窗 MA20/60', 'buy_hold': '分窗买入持有',
             'double_both': '选参双成本压力', 'continuous_baseline': '连续 MA20/60',
             'continuous_buy_hold': '连续买入持有'}
    for key, risk in result['aggregate'].items():
        lines.append(f"| {names[key]} | {risk['return_pct']:+.2f}% | {risk['max_dd_pct']:.2f}% | {risk['max_underwater_days']} |")
    lines += ['', '## 自然年阶段（同一权益路径）', '',
              '| 阶段 | 天数 | 选参收益 / 回撤 | 同窗基线收益 | 连续基线收益 | 连续买入持有收益 |',
              '|---|---:|---:|---:|---:|---:|']
    phases = result['calendar_phases']
    for index, row in enumerate(phases['selected']):
        risk = row['metrics']
        others = [phases[key][index]['metrics']['return_pct']
                  for key in ('baseline', 'continuous_baseline', 'continuous_buy_hold')]
        lines.append(f"| {row['start']}—{row['end']} | {risk['days']} | {risk['return_pct']:+.2f}% / {risk['max_dd_pct']:.2f}% | {others[0]:+.2f}% | {others[1]:+.2f}% | {others[2]:+.2f}% |")
    lines += ['', '## 结论使用范围', '',
              f"- 全期训练选参收益 {result['aggregate']['selected']['return_pct']:+.2f}%，同窗固定 MA20/60 收益 {result['aggregate']['baseline']['return_pct']:+.2f}%；对应最大回撤分别为 {result['aggregate']['selected']['max_dd_pct']:.2f}% 与 {result['aggregate']['baseline']['max_dd_pct']:.2f}%。更长样本没有自动证明动态选参优于固定基线。",
              '- 本轮增加历史覆盖和来源证据，不扩大候选或窗口搜索，也不挑选最佳年份作为盈利证据。年份之间并非独立样本；年内回撤不能替代全期回撤。',
              '- 本报告不构成策略盈利能力已获证实的结论。单一交易所、历史修订、USDT 报价、固定成本模型及选择偏差仍限制外推。',
              '- 现有模拟账户继续按原 MA20/60 规则积累前瞻数据，本模块不读写其账本、计划任务或运行配置。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DATASET)
    parser.add_argument('--protocol', type=Path, default=PROTOCOL)
    parser.add_argument('--output-root', type=Path, default=ROOT)
    args = parser.parse_args()
    candles, manifest = verify_dataset(args.dataset)
    protocol_bytes = args.protocol.read_bytes().replace(b'\r\n', b'\n')
    protocol = json.loads(protocol_bytes)
    if args.dataset.name != protocol['dataset']:
        raise ValueError('Wrong dataset version')
    frozen = datetime.fromisoformat(protocol['frozen_at_utc'])
    if frozen.tzinfo is None or any(frozen > datetime.fromisoformat(a['fetched_at_utc'])
                                  for a in manifest['archives']):
        raise ValueError('Protocol must be fixed before archive acquisition')
    result = study(candles, protocol)
    result['dataset'] = manifest
    result['protocol_sha256'] = sha256(protocol_bytes)
    result['protocol_hash_normalization'] = 'utf8_lf'
    result['manifest_sha256'] = sha256((args.dataset / 'manifest.json').read_bytes())
    result['engine_sha256'] = {name: sha256((ROOT / 'strategies' / name).read_bytes().replace(b'\r\n', b'\n'))
                               for name in ('research.py', 'execution.py', 'walk_forward.py', 'history_study.py')}
    for directory in ('data', 'docs'):
        (args.output_root / directory).mkdir(parents=True, exist_ok=True)
    (args.output_root / 'data/history_study_v1.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')
    (args.output_root / 'docs/长期历史验证报告.md').write_text(render_report(result), encoding='utf-8', newline='\n')
    print(f"Completed fixed protocol: {len(result['folds'])} folds, {len(result['daily_equity'])} validation days")


if __name__ == '__main__':
    main()
