"""Fixed, point-in-time walk-forward diagnostics; no strategy/account changes."""
import argparse
from bisect import bisect_right
from collections import Counter
import json
import math
from pathlib import Path
import random
import statistics

from strategies.derivatives_data import DAY, DEFAULT_ROOT, ROOT, digest, encode, iso, utc_ms, verify

PROTOCOL_PATH = ROOT / 'docs' / 'derivatives_protocol_v1.json'
# Freeze choices independently of the editable protocol document.
PROTOCOL = {
    'version': 1, 'symbol': 'BTCUSDT', 'horizon_days': 7, 'decision_stride_days': 7,
    'train_days': 252, 'validation_days': 63, 'min_train_samples': 24,
    'max_feature_age_days': 2,
    'price_features': ['return_1d', 'momentum_7d', 'volatility_7d'],
    'derivative_features': ['oi_change', 'long_short_ratio', 'taker_imbalance', 'observed_funding_sum'],
    'targets': ['future_volatility', 'future_drawdown', 'trend_persistence'],
    'ridge_penalty': 1.0, 'bootstrap_seed': 20261022,
    'bootstrap_replicates': 1000, 'bootstrap_block_samples': 4,
}


def load_archive(root, as_of_ms):
    """Failed snapshots are evidence, not observations. Corrupt complete ones abort."""
    archive, evidence = [], []
    for path in sorted(Path(root).glob('*/manifest.json')):
        manifest = json.loads(path.read_bytes())
        item = {'snapshot': path.parent.name, 'manifest_sha256': digest(path.read_bytes()),
                'status': manifest.get('status')}
        if manifest.get('status') == 'complete':
            data, manifest = verify(path.parent)
            if manifest['completed_at_ms'] <= as_of_ms:
                archive.append(data)
            else:
                item['excluded'] = 'snapshot completed after analysis cutoff'
        evidence.append(item)
    return archive, evidence


class Versions:
    """Metric/day revisions indexed by real observation time, never latest-only."""
    def __init__(self, archive):
        grouped = {}
        for dataset in archive:
            for metric, rows in dataset['metrics'].items():
                if metric == 'funding':
                    days = {}
                    for row in rows:
                        days.setdefault(row['day_start_ms'], []).append(row)
                    rows = [{'day_start_ms': day, 'available_at_ms': max(r['available_at_ms'] for r in events),
                             'observed_funding_sum': sum(r['funding_rate'] for r in events)}
                            for day, events in days.items()]
                for row in rows:
                    key = metric, row['day_start_ms']
                    stamp = row['available_at_ms']
                    versions = grouped.setdefault(key, {})
                    if stamp in versions and versions[stamp] != row:
                        raise ValueError('Conflicting revisions at the same availability time')
                    versions[stamp] = row
        self.rows = {key: sorted(versions.items()) for key, versions in grouped.items()}
        self.times = {key: [time for time, _ in rows] for key, rows in self.rows.items()}

    def at(self, metric, day, cutoff, *, first=False):
        key = metric, day
        index = bisect_right(self.times.get(key, []), cutoff) - 1
        if index < 0:
            return None
        return self.rows[key][0 if first else index][1]


def features_at(versions, decision):
    # Overnight collection often arrives 00:15: at next midnight use D-2.
    for age in range(1, PROTOCOL['max_feature_age_days'] + 1):
        day = decision - age * DAY
        spot = [versions.at('spot', day - i * DAY, decision) for i in range(8)]
        oi = [versions.at('oi', day - i * DAY, decision) for i in range(2)]
        ratio = versions.at('long_short', day, decision)
        taker = versions.at('taker', day, decision)
        funding = versions.at('funding', day, decision)
        if not all(spot + oi + [ratio, taker, funding]) or oi[1]['open_interest'] <= 0:
            continue
        total = taker['buy_volume'] + taker['sell_volume']
        if total <= 0:
            continue
        returns = [spot[i]['close'] / spot[i + 1]['close'] - 1 for i in range(7)]
        values = [returns[0], spot[0]['close'] / spot[7]['close'] - 1, statistics.pstdev(returns),
                  oi[0]['open_interest'] / oi[1]['open_interest'] - 1,
                  ratio['long_short_ratio'], (taker['buy_volume'] - taker['sell_volume']) / total,
                  funding['observed_funding_sum']]
        used = spot + oi + [ratio, taker, funding]
        return {'feature_day_ms': day, 'features': values,
                'features_available_at_ms': max(r['available_at_ms'] for r in used)}
    return None


def labels_at(versions, decision, as_of_ms, momentum):
    # First-observed prices fix label revisions and preserve training-time availability.
    prices = [versions.at('spot', decision + i * DAY, as_of_ms, first=True)
              for i in range(PROTOCOL['horizon_days'])]
    if not all(prices):
        return None
    previous = prices[0]['open']
    drawdown = 0.0
    returns = []
    for price in prices:
        returns.append(price['close'] / previous - 1)
        previous = price['close']
    peak = prices[0]['open']
    for price in prices:
        peak = max(peak, price['close'])
        drawdown = max(drawdown, 1 - price['close'] / peak)
    persistence = sum((r > 0 and momentum > 0) or (r < 0 and momentum < 0) for r in returns) / len(returns)
    return {'targets': [statistics.pstdev(returns), drawdown, persistence],
            'label_end_ms': decision + PROTOCOL['horizon_days'] * DAY,
            'labels_available_at_ms': max(r['available_at_ms'] for r in prices)}


def make_samples(archive, as_of_ms):
    if not archive:
        return [], {'candidate_decisions': 0, 'missing_features': 0, 'missing_labels': 0}
    versions = Versions(archive)
    first = min(data['start_ms'] for data in archive) + 8 * DAY
    stop = min(max(data['stop_ms'] for data in archive), as_of_ms // DAY * DAY)
    stride = PROTOCOL['decision_stride_days'] * DAY
    first = ((first + stride - 1) // stride) * stride  # fixed Unix epoch anchor
    samples = []
    counts = Counter(candidate_decisions=0, missing_features=0, missing_labels=0)
    for decision in range(first, stop, stride):
        counts['candidate_decisions'] += 1
        features = features_at(versions, decision)
        if features is None:
            counts['missing_features'] += 1
            continue
        labels = labels_at(versions, decision, as_of_ms, features['features'][1])
        if labels is None:
            counts['missing_labels'] += 1
            continue
        samples.append({'decision_ms': decision, **features, **labels})
    return samples, dict(counts)


def solve(matrix, values):
    """Pivoted Gaussian elimination; ridge makes the feature block positive definite."""
    rows = [list(row) + [value] for row, value in zip(matrix, values)]
    n = len(rows)
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(rows[row][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        if abs(rows[col][col]) < 1e-12:
            raise ValueError('Singular regression matrix')
        divisor = rows[col][col]
        rows[col] = [x / divisor for x in rows[col]]
        for row in range(n):
            if row != col:
                factor = rows[row][col]
                rows[row] = [a - factor * b for a, b in zip(rows[row], rows[col])]
    return [row[-1] for row in rows]


def fit(train, width):
    centers = [statistics.mean(r['features'][i] for r in train) for i in range(width)]
    scales = [statistics.pstdev(r['features'][i] for r in train) or 1.0 for i in range(width)]
    x = [[1.0] + [(r['features'][i] - centers[i]) / scales[i] for i in range(width)] for r in train]
    gram = [[sum(row[i] * row[j] for row in x) + (PROTOCOL['ridge_penalty'] if i == j and i else 0)
             for j in range(width + 1)] for i in range(width + 1)]
    coefficients = [solve(gram, [sum(row[i] * sample['targets'][target] for row, sample in zip(x, train))
                               for i in range(width + 1)]) for target in range(3)]
    return {'centers': centers, 'scales': scales, 'coefficients': coefficients}


def predict(model, sample):
    x = [1.0] + [(v - c) / s for v, c, s in zip(sample['features'], model['centers'], model['scales'])]
    return [sum(a * b for a, b in zip(x, coefficients)) for coefficients in model['coefficients']]


def walk_forward(samples):
    if not samples:
        return [], []
    samples = sorted(samples, key=lambda row: row['decision_ms'])
    if len({r['decision_ms'] for r in samples}) != len(samples):
        raise ValueError('Duplicate decision dates')
    for row in samples:
        if row['features_available_at_ms'] > row['decision_ms'] or row['label_end_ms'] <= row['decision_ms']:
            raise ValueError('Invalid sample availability')
        if row['labels_available_at_ms'] < row['label_end_ms'] or len(row['features']) != 7 or len(row['targets']) != 3:
            raise ValueError('Invalid label availability or sample dimensions')
        if not all(math.isfinite(x) for x in row['features'] + row['targets']):
            raise ValueError('Nonfinite study sample')
    first = samples[0]['decision_ms'] + PROTOCOL['train_days'] * DAY
    stop = samples[-1]['decision_ms'] + DAY
    forecasts, folds = [], []
    for cutoff in range(first, stop, PROTOCOL['validation_days'] * DAY):
        train = [r for r in samples if cutoff - PROTOCOL['train_days'] * DAY <= r['decision_ms'] < cutoff
                 and r['label_end_ms'] <= cutoff and r['labels_available_at_ms'] <= cutoff]
        test = [r for r in samples if cutoff <= r['decision_ms'] < cutoff + PROTOCOL['validation_days'] * DAY]
        fold = {'cutoff_ms': cutoff, 'train_decisions': [r['decision_ms'] for r in train],
                'test_decisions': [r['decision_ms'] for r in test]}
        if len(train) < PROTOCOL['min_train_samples']:
            fold['status'] = 'insufficient_train_samples'
            folds.append(fold)
            continue
        baseline, augmented = fit(train, 3), fit(train, 7)
        fold.update(status='evaluated', price_model=baseline, derivatives_model=augmented)
        folds.append(fold)
        for row in test:
            forecasts.append({'decision_ms': row['decision_ms'], 'fold_cutoff_ms': cutoff,
                              'targets': row['targets'], 'price_prediction': predict(baseline, row),
                              'derivatives_prediction': predict(augmented, row)})
    return forecasts, folds


def bootstrap(values):
    if len(values) < 2 * PROTOCOL['bootstrap_block_samples']:
        return None
    rng = random.Random(PROTOCOL['bootstrap_seed'])
    means = []
    n, block = len(values), PROTOCOL['bootstrap_block_samples']
    for _ in range(PROTOCOL['bootstrap_replicates']):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start + i) % n] for i in range(block))
        means.append(statistics.mean(sample[:n]))
    means.sort()
    return [means[24], means[974]]


def summarize(forecasts):
    result = {}
    for i, target in enumerate(PROTOCOL['targets']):
        if not forecasts:
            result[target] = {'samples': 0, 'mse_improvement': None, 'bootstrap_95_interval': None}
            continue
        base = [r['price_prediction'][i] - r['targets'][i] for r in forecasts]
        aug = [r['derivatives_prediction'][i] - r['targets'][i] for r in forecasts]
        improvement = [b * b - a * a for b, a in zip(base, aug)]
        result[target] = {'samples': len(forecasts), 'price_mse': statistics.mean(x * x for x in base),
                          'derivatives_mse': statistics.mean(x * x for x in aug),
                          'price_mae': statistics.mean(abs(x) for x in base),
                          'derivatives_mae': statistics.mean(abs(x) for x in aug),
                          'mse_improvement': statistics.mean(improvement),
                          'bootstrap_95_interval': bootstrap(improvement)}
    return result


def render(report):
    lines = ['# 衍生品指标 Walk-forward 研究', '', f"研究截止：{iso(report['as_of_ms'])}", '',
             f"状态：`{report['status']}`；有效样本 {len(report['samples'])}，验证预测 {len(report['forecasts'])}。", '',
             '固定 252 天训练 / 63 天验证，7 天非重叠标签；价格基线与加入衍生品的岭回归共享决策日期。',
             '训练仅使用当时已采集的特征和已结束且实际可获得的标签，标准化仅拟合训练集。',
             'MSE 改善 = 价格基线误差 − 增加衍生品后的误差；正值表示该样本上误差较小。',
             '区间为固定种子的连续区块重采样诊断；小样本、三个结果的多重比较和结构变化限制结论，不自动判定显著性或部署。', '',
             '| 未来目标 | 验证样本 | MSE 改善 | 95% 区块区间 |', '|---|---:|---:|---|']
    for name, metric in report['summary'].items():
        lines.append(f"| {name} | {metric['samples']} | {metric['mse_improvement']} | {metric['bootstrap_95_interval']} |")
    lines += ['', '## 数据与局限', '',
              f"完整快照 {report['complete_snapshots']}；其他快照 {len(report['evidence']) - report['complete_snapshots']}。",
              f"候选与缺失：`{json.dumps(report['sample_counts'], ensure_ascii=False)}`", '',
              '无足够数据时只报告待积累，不生成模拟市场结论。资金费率使用当日实际返回的事件和，不保证事件齐全。',
              '波动率为未来七段开盘/前收盘至收盘收益的总体标准差；回撤为决策日开盘和随后每日收盘路径的最大回撤，未计成本。',
              '趋势持续性为未来七段收益与此前七日动量同号的比例；动量为零时该比例为零。',
              '这是条件预测误差对照，不是交易收益回测，不改动 MA 策略、费用、Walk-forward 协议或纸面账户。', '',
              '## 来源指纹', '']
    for evidence in report['evidence']:
        lines.append(f"- {evidence['snapshot']}: {evidence['status']}，manifest SHA-256 `{evidence['manifest_sha256']}`")
    return '\n'.join(lines) + '\n'


def run(root=DEFAULT_ROOT, output=None, *, as_of_ms=None, protocol_path=PROTOCOL_PATH):
    if json.loads(Path(protocol_path).read_bytes()) != PROTOCOL:
        raise ValueError('Fixed derivatives protocol changed; create a new version instead')
    cutoff = utc_ms() if as_of_ms is None else as_of_ms
    archive, evidence = load_archive(root, cutoff)
    samples, counts = make_samples(archive, cutoff)
    forecasts, folds = walk_forward(samples)
    report = {'schema_version': 1, 'as_of_ms': cutoff, 'protocol': PROTOCOL,
              'protocol_sha256': digest(encode(PROTOCOL)),
              'status': 'evaluated' if forecasts else 'insufficient_data',
              'complete_snapshots': len(archive), 'evidence': evidence, 'sample_counts': counts,
              'samples': samples, 'folds': folds, 'forecasts': forecasts, 'summary': summarize(forecasts)}
    if output is not None:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        (output / 'study.json').write_bytes(encode(report))
        (output / 'study.md').write_text(render(report), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--as-of-ms', type=int)
    args = parser.parse_args()
    report = run(args.root, args.output, as_of_ms=args.as_of_ms)
    print(f"{report['status']}: {len(report['samples'])} samples; {len(report['forecasts'])} forecasts")


if __name__ == '__main__':
    main()
