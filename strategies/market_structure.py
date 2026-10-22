"""Descriptive market structure, using only a verified standalone snapshot."""
import argparse
from pathlib import Path

from strategies.derivatives_data import DAY, encode, iso, verify


def analyze(dataset, *, as_of_ms):
    """Fail closed on availability: late backfills never enter earlier decisions."""
    metrics = {name: [r for r in rows if r['available_at_ms'] <= as_of_ms]
               for name, rows in dataset['metrics'].items()}
    indexed = {name: {r['day_start_ms']: r for r in rows}
               for name, rows in metrics.items() if name != 'funding'}
    daily = []
    for day in range(dataset['start_ms'], min(dataset['stop_ms'], as_of_ms // DAY * DAY), DAY):
        spot, previous = indexed['spot'].get(day), indexed['spot'].get(day - DAY)
        oi, prior_oi = indexed['oi'].get(day), indexed['oi'].get(day - DAY)
        ratio, taker = indexed['long_short'].get(day), indexed['taker'].get(day)
        price_change = spot['close'] / previous['close'] - 1 if spot and previous else None
        oi_change = oi['open_interest'] / prior_oi['open_interest'] - 1 if oi and prior_oi and prior_oi['open_interest'] else None
        funding = [r for r in metrics['funding'] if r['day_start_ms'] == day]
        volume = taker['buy_volume'] + taker['sell_volume'] if taker else 0
        if price_change is None or oi_change is None:
            state = '数据不足'
        elif price_change > 0 and oi_change > 0:
            state = '价格与持仓同步增长'
        elif price_change < 0 and oi_change > 0:
            state = '价格下跌、持仓增长'
        elif price_change > 0 and oi_change < 0:
            state = '价格上涨、持仓下降'
        elif price_change < 0 and oi_change < 0:
            state = '价格与持仓同步下降'
        else:
            state = '价格或持仓持平'
        used = [r for r in (spot, previous, oi, prior_oi, ratio, taker) if r] + funding
        daily.append({'date': iso(day)[:10], 'state': state, 'close': spot['close'] if spot else None,
                      'price_change': price_change, 'oi_change': oi_change,
                      'long_short_account_ratio': ratio['long_short_ratio'] if ratio else None,
                      'taker_imbalance': (taker['buy_volume'] - taker['sell_volume']) / volume if volume else None,
                      'observed_funding_rate_sum': sum(r['funding_rate'] for r in funding) if funding else None,
                      'funding_events': len(funding),
                      'available_at_ms': max((r['available_at_ms'] for r in used), default=None),
                      'missing': [name for name, row in (('spot', spot), ('oi', oi), ('long_short', ratio), ('taker', taker)) if row is None] + ([] if funding else ['funding'])})
    return {'schema_version': 1, 'as_of_ms': as_of_ms, 'daily': daily,
            'quality': dataset['quality'], 'purpose': 'descriptive_only_no_trading_signal'}


def render(report, manifest):
    lines = ['# Binance BTC 市场结构研究', '', f"观察截止：{iso(report['as_of_ms'])}", '',
             '仅描述同期关系，不构成交易信号，不证明预测能力；短样本不进行显著性检验或选参。', '',
             '价格为现货 BTC/USDT，衍生品为 USDⓈ-M BTCUSDT。OI 使用 BTC 数量变化，避免美元名义价值受价格机械影响。',
             'OI/多空比例按区间结束对齐，主动量按区间开始对齐；价格仅使用已收盘日线。',
             '所有记录的可用时间不早于实际 HTTP 接收时间；本次回补不能进入此前的历史决策。',
             '资金费率为当日已观测事件之和，不假定每天三次结算，也不保证事件完整；缺值不填零、不前向填充。', '',
             '| UTC 日期 | 收盘价 | 收盘变化 | OI变化 | 多空账户比 | 主动量不平衡 | 已观测费率和 | 事件数 | 状态 | 缺失 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---|---|']
    def pct(value):
        return 'NA' if value is None else f'{value:.3%}'
    for row in report['daily']:
        close = 'NA' if row['close'] is None else f"{row['close']:.2f}"
        ratio = 'NA' if row['long_short_account_ratio'] is None else f"{row['long_short_account_ratio']:.4f}"
        lines.append(f"| {row['date']} | {close} | {pct(row['price_change'])} | {pct(row['oi_change'])} | {ratio} | {pct(row['taker_imbalance'])} | {pct(row['observed_funding_rate_sum'])} | {row['funding_events']} | {row['state']} | {', '.join(row['missing']) or '无'} |")
    lines += ['', '## 完整性与证据', '', '缺失日期只表示本快照没有记录；API 未返回不等于真实数值为零。']
    for name, quality in report['quality'].items():
        lines.append(f"- {name}: {quality['rows']} 条；缺失日：{', '.join(quality['missing_days']) or '无'}")
    lines += ['', '## 只读来源', '']
    for name, source in manifest['sources'].items():
        lines.append(f"- {name}: {source['url']}；接收 {iso(source['received_at_ms'])}；SHA-256 `{source['sha256']}`")
    lines += ['', f"[Binance 官方接口文档]({manifest['documentation']})", '']
    return '\n'.join(lines)


def run(snapshot, output, *, as_of_ms=None):
    dataset, manifest = verify(snapshot)
    report = analyze(dataset, as_of_ms=manifest['completed_at_ms'] if as_of_ms is None else as_of_ms)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report['sources'] = manifest['sources']
    (output / 'report.json').write_bytes(encode(report))
    (output / 'report.md').write_text(render(report, manifest), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing paths are never overwritten')
    parser.add_argument('--as-of-ms', type=int)
    args = parser.parse_args()
    run(args.snapshot, args.output, as_of_ms=args.as_of_ms)
    print(args.output)


if __name__ == '__main__':
    main()
