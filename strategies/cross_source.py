"""Independent Coinbase BTC/USD daily comparison; diagnostic only, no data replacement."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
from urllib.parse import urlencode

from strategies.history import DATASET, ROOT, csv_bytes, download, sha256, staging_directory, utcnow, verify_dataset
from strategies.market_data import parse_daily_rows

REFERENCE = ROOT / 'data/history/coinbase_btcusd_1d_v1'
POLICY = ROOT / 'docs/cross_source_protocol_v1.json'
SOURCE = 'https://api.exchange.coinbase.com/products/BTC-USD/candles'
DOCS = 'https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles'


def requests_for(start, stop):
    for left in range(start, stop, 280 * 86400):
        right = min(left + 280 * 86400, stop)
        yield left, right, SOURCE + '?' + urlencode({'granularity': 86400, 'start': left, 'end': right})


def normalize_response(blob, start, stop):
    payload = json.loads(blob)
    if not isinstance(payload, list):
        raise ValueError('Expected Coinbase candle array')
    rows = []
    seen = set()
    for row in payload:
        if not isinstance(row, list) or len(row) != 6 or type(row[0]) is not int or row[0] % 86400:
            raise ValueError('Invalid Coinbase candle schema/timestamp')
        if not start <= row[0] < stop:
            continue  # API may include candles outside requested bounds.
        if row[0] in seen:
            raise ValueError('Duplicate reference date')
        seen.add(row[0])
        rows.append([row[0], row[3], row[2], row[1], row[4]])
    rows.sort(key=lambda row: row[0])
    if [row[0] for row in rows] != list(range(start, stop, 86400)):
        raise ValueError('Missing Coinbase reference dates; no filling allowed')
    parse_daily_rows(rows)
    return rows


def collect_reference(destination, primary=DATASET, policy=POLICY):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Reference dataset versions are immutable')
    candles, _ = verify_dataset(primary)
    frozen = json.loads(Path(policy).read_text(encoding='utf-8'))
    if datetime.fromisoformat(frozen['frozen_at_utc']) > datetime.now(timezone.utc):
        raise ValueError('Policy freeze time is in the future')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(destination.parent) as temp:
        stage = temp / 'reference'
        (stage / 'raw').mkdir(parents=True)
        rows, entries = [], []
        for index, (start, stop, url) in enumerate(requests_for(candles[0].timestamp, candles[-1].timestamp + 86400)):
            blob, receipt = download(url)
            batch = normalize_response(blob, start, stop)
            filename = f'{index:03}.json'
            (stage / 'raw' / filename).write_bytes(blob)
            rows.extend(batch)
            entries.append({'file': filename, 'url': url, 'start': start, 'stop': stop,
                            'sha256': sha256(blob), 'rows': len(batch), **receipt})
        normalized = csv_bytes(rows)
        (stage / 'ohlc.csv').write_bytes(normalized)
        manifest = {'schema_version': 1, 'source': 'Coinbase Exchange public candles',
                    'documentation': DOCS, 'symbol': 'BTC-USD', 'quote_asset': 'USD', 'timezone': 'UTC',
                    'interval': '1d', 'start': candles[0].timestamp, 'stop': candles[-1].timestamp + 86400,
                    'rows': len(rows), 'collected_at_utc': utcnow(), 'normalized_sha256': sha256(normalized),
                    'policy_sha256': sha256(Path(policy).read_bytes().replace(b'\r\n', b'\n')),
                    'chunks': entries}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline='\n')
        verify_reference(stage)
        stage.rename(destination)


def verify_reference(path):
    path = Path(path)
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest['schema_version'] != 1 or manifest['symbol'] != 'BTC-USD'
            or manifest['quote_asset'] != 'USD' or manifest['timezone'] != 'UTC'
            or manifest['interval'] != '1d'):
        raise ValueError('Wrong reference dataset identity')
    expected = list(requests_for(manifest['start'], manifest['stop']))
    if len(manifest['chunks']) != len(expected):
        raise ValueError('Missing reference chunks')
    rows = []
    for index, (entry, (start, stop, url)) in enumerate(zip(manifest['chunks'], expected)):
        if (entry['file'] != f'{index:03}.json' or entry['start'] != start
                or entry['stop'] != stop or entry['url'] != url):
            raise ValueError('Invalid reference chunk identity')
        blob = (path / 'raw' / entry['file']).read_bytes()
        if sha256(blob) != entry['sha256']:
            raise ValueError('Reference raw fingerprint mismatch')
        fetched = datetime.fromisoformat(entry['fetched_at_utc'])
        if fetched.tzinfo is None or fetched.timestamp() < stop:
            raise ValueError('Unclosed reference candles')
        batch = normalize_response(blob, start, stop)
        if len(batch) != entry['rows']:
            raise ValueError('Reference row count mismatch')
        rows.extend(batch)
    content = (path / 'ohlc.csv').read_bytes()
    if content != csv_bytes(rows) or sha256(content) != manifest['normalized_sha256']:
        raise ValueError('Reference normalized fingerprint mismatch')
    if len(rows) != manifest['rows']:
        raise ValueError('Reference coverage mismatch')
    return parse_daily_rows(rows), manifest


def compare(primary, reference, policy):
    if policy['ohlc_alert_pct'] != 3 or policy['close_alert_pct'] != 1:
        raise ValueError('Diagnostic thresholds changed from fixed policy')
    if [c.timestamp for c in primary] != [c.timestamp for c in reference]:
        raise ValueError('Reference coverage differs from primary; no filling/intersection-only claims')
    daily = []
    for left, right in zip(primary, reference):
        deviations = {key: (getattr(left, key) / getattr(right, key) - 1) * 100
                      for key in ('open', 'high', 'low', 'close')}
        daily.append({'date': left.date, 'relative_difference_pct': deviations,
                      'primary_ohlc': {key: getattr(left, key) for key in deviations},
                      'reference_ohlc': {key: getattr(right, key) for key in deviations},
                      'ohlc_alert': any(abs(v) > policy['ohlc_alert_pct'] for v in deviations.values()),
                      'close_alert': abs(deviations['close']) > policy['close_alert_pct']})
    return {'rows': len(daily), 'start': primary[0].date, 'end': primary[-1].date,
            'quote_comparison': 'Binance BTC/USDT versus Coinbase BTC/USD; not currency-normalized',
            'interpretation': 'diagnostic_alerts_not_failed_price_truth_or_strategy_evidence',
            'ohlc_alert_days': sum(d['ohlc_alert'] for d in daily),
            'close_alert_days': sum(d['close_alert'] for d in daily),
            'median_absolute_close_difference_pct': statistics.median(abs(d['relative_difference_pct']['close']) for d in daily),
            'max_absolute_close_difference_pct': max(abs(d['relative_difference_pct']['close']) for d in daily),
            'daily': daily}


def render_report(result):
    lines = ['# 第二来源交叉核验', '',
             f"覆盖 {result['start']} 至 {result['end']}，{result['rows']} 个 UTC 日期，主数据与参考数据逐日完整对齐。", '',
             f'参考来源：[Coinbase Exchange 官方日线 API]({DOCS})。原始响应、请求 URL、UTC 采集时间和指纹保存在 `data/history/coinbase_btcusd_1d_v1/`。', '',
             '比较规则在本轮参考数据下载前固定：任一 OHLC 绝对相对差超过 3%，或收盘绝对相对差超过 1%，记录诊断告警。差值为 Binance / Coinbase - 1，阈值不是统计置信区间。', '',
             f"- OHLC 告警：{result['ohlc_alert_days']} 天。",
             f"- 收盘告警：{result['close_alert_days']} 天。",
             f"- 收盘绝对相对差中位数：{result['median_absolute_close_difference_pct']:.4f}%；最大值：{result['max_absolute_close_difference_pct']:.4f}%。", '',
             '## 告警日期（全部保留）', '',
             '| 日期 | 开盘差 | 最高差 | 最低差 | 收盘差 |', '|---|---:|---:|---:|---:|']
    for day in result['daily']:
        if day['ohlc_alert'] or day['close_alert']:
            d = day['relative_difference_pct']
            lines.append(f"| {day['date']} | {d['open']:+.3f}% | {d['high']:+.3f}% | {d['low']:+.3f}% | {d['close']:+.3f}% |")
    lines += ['', '## 最大收盘差异的原始价格复核', '',
              '| 日期 | Binance 收盘（USDT） | Coinbase 收盘（USD） | 相对差 |', '|---|---:|---:|---:|']
    for day in sorted(result['daily'], key=lambda d: abs(d['relative_difference_pct']['close']), reverse=True)[:5]:
        lines.append(f"| {day['date']} | {day['primary_ohlc']['close']} | {day['reference_ohlc']['close']} | {day['relative_difference_pct']['close']:+.4f}% |")
    lines += ['', '这些差异均由保存的原始响应重算确认，日期已对齐；未在本轮确定差异的具体市场原因，不能将其自动归因于 USDT 脱锚或任何一家数据错误。', '',
              '## 核验结论与边界', '',
              '- 完成的是独立交易所逐日价格合理性对照。BTC/USDT 与 BTC/USD 的币种基差、流动性及极端成交都会造成真实差异；告警不能直接判定哪家错误。',
              '- 所有告警原样保留，未剔除日期、替换价格或重新调参。该数据版本保留告警标记，不能标为无异常数据；若要据此修订数据，须另立版本并逐项调查。',
              '- 双来源一致也不证明历史数据绝对正确；两者都可能修订，不是逐历史时点可见的数据。比较未换算 USDT/USD，不替代同市场第二供应商核验。',
              '- 原有固定协议结果保持不变，不将此次对照用于策略选择。模拟账户和观察器规则不变。', '']
    return '\n'.join(lines)


def run(primary=DATASET, reference=REFERENCE, policy_path=POLICY, output_root=ROOT):
    candles, primary_manifest = verify_dataset(primary)
    other, reference_manifest = verify_reference(reference)
    policy_bytes = Path(policy_path).read_bytes().replace(b'\r\n', b'\n')
    policy = json.loads(policy_bytes)
    frozen = datetime.fromisoformat(policy['frozen_at_utc'])
    if (frozen.tzinfo is None or reference_manifest['policy_sha256'] != sha256(policy_bytes)
            or any(frozen > datetime.fromisoformat(c['fetched_at_utc']) for c in reference_manifest['chunks'])):
        raise ValueError('Cross-source policy must precede reference acquisition and match manifest')
    result = compare(candles, other, policy)
    result.update({'policy': policy, 'policy_sha256': sha256(policy_bytes),
                   'primary_sha256': primary_manifest['normalized_sha256'],
                   'reference_sha256': reference_manifest['normalized_sha256'],
                   'reference_collected_at_utc': reference_manifest['collected_at_utc']})
    output_root = Path(output_root)
    for directory in ('data', 'docs'):
        (output_root / directory).mkdir(parents=True, exist_ok=True)
    (output_root / 'data/cross_source_v1.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')
    (output_root / 'docs/第二来源交叉核验.md').write_text(render_report(result), encoding='utf-8', newline='\n')
    print(f"Compared {result['rows']} days; OHLC alerts {result['ohlc_alert_days']}; close alerts {result['close_alert_days']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('collect', 'verify'))
    parser.add_argument('--reference', type=Path, default=REFERENCE)
    parser.add_argument('--output-root', type=Path, default=ROOT)
    args = parser.parse_args()
    if args.action == 'collect':
        collect_reference(args.reference)
    run(reference=args.reference, output_root=args.output_root)


if __name__ == '__main__':
    main()
