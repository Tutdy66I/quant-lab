"""Offline, evidence-scoped normalization. No strategy, account or network imports."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/archive_feasibility/binance_um_btcusdt_v1'
TAKER = 'sum_taker_long_short_vol_ratio'
METRICS = ['create_time', 'symbol', 'sum_open_interest', 'sum_open_interest_value',
           'count_toptrader_long_short_ratio', 'sum_toptrader_long_short_ratio',
           'count_long_short_ratio', TAKER]
KLINES = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
          'quote_volume', 'count', 'taker_buy_volume', 'taker_buy_quote_volume', 'ignore']
FUNDING = ['calc_time', 'funding_interval_hours', 'last_funding_rate']
# A review of exact bytes, not an assumed effective-date rule or general API contract.
SEMANTICS = {
    'id': 'btc-usdm-taker-two-days-v1', 'verified_at_utc': '2026-10-22T08:54:57+00:00',
    'field': TAKER, 'symbol': 'BTCUSDT', 'window': '[source_label, source_label + 5 minutes)',
    'event_time': 'exclusive window end', 'scope': 'exact ZIP fingerprints only',
    'evidence': 'docs/Binance归档可行性验证.md; 5m kline volume cross-check',
    'limitations': 'Empirical window alignment, not exact trade reconstruction or an official guarantee.',
    'versions': {
        '2026-06-24': {
            'metrics_sha256': '6bb5ab473189fa7c935b4d9a58d34b2c96cc329cd74fd6f9f17f8abc48ece649',
            'reference_sha256': '9651ac1b307459ed1f570c144af5bec3d8c12c550d2a17027cff25a5c0989fcf'},
        '2026-06-25': {
            'metrics_sha256': '56e0751dabd8108d61d1f9de86931ac68e901dad202d4dbac03173e2768be68e',
            'reference_sha256': 'cb37bcf943fa5a0fe2d142e84064e9b9211ab073fff62c364e3f2ad806e65a69'}
    }
}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def iso(value):
    return value.astimezone(UTC).isoformat()


def utc(value):
    if not isinstance(value, str):
        raise ValueError('Missing UTC timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError('UTC offset required')
    return result


def decimal(value):
    try:
        result = Decimal(value)
    except Exception as error:
        raise ValueError('Invalid numeric value') from error
    if not result.is_finite() or result < 0 or result > Decimal('1e18'):
        raise ValueError('Invalid numeric range')
    return result


def text_number(value):
    return format(value, 'f')


def load_evidence(source):
    """Verify all receipts, including non-data responses; never follow input paths."""
    manifest_blob = (source / 'receipts.json').read_bytes()
    receipts = json.loads(manifest_blob)
    records = {}
    for receipt in receipts:
        name = receipt['file']
        if not re.fullmatch(r'raw/[A-Za-z0-9_.-]+', name) or name in records:
            raise ValueError('Unsafe or duplicate receipt path')
        path = source / name
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('Unsafe receipt target')
        blob = path.read_bytes()
        if sha(blob) != receipt['sha256'] or len(blob) != receipt['bytes']:
            raise ValueError('Receipt SHA-256/size mismatch: ' + name)
        started, collected = utc(receipt['request_started_at_utc']), utc(receipt['collected_at_utc'])
        if collected < started:
            raise ValueError('Collection precedes request')
        # There is no authenticated first-publication evidence in this input format.
        # Preserve publisher metadata, but do not promote a caller-supplied date.
        records[name] = (receipt, blob)
    return records, sha(manifest_blob)


def read_archive(name, records, fields):
    receipt, blob = records['raw/' + name]
    check_receipt, checksum = records['raw/' + name + '.CHECKSUM']
    expected_url = 'https://data.binance.vision/data/futures/um/'
    for r in (receipt, check_receipt):
        if r['status'] != 200 or not r['url'].startswith(expected_url) or r['final_url'] != r['url']:
            raise ValueError('Non-successful or unexpected archive source')
        if r['url'].rsplit('/', 1)[-1] != Path(r['file']).name:
            raise ValueError('Source URL filename mismatch')
    parts = checksum.decode('ascii').split()
    if len(parts) != 2 or parts[0].lower() != sha(blob) or parts[1].lstrip('*') != name:
        raise ValueError('Publisher SHA-256 mismatch: ' + name)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if archive.namelist() != [name[:-4] + '.csv']:
            raise ValueError('Unexpected ZIP members')
        if archive.infolist()[0].file_size > 16_000_000:
            raise ValueError('ZIP expanded size limit')
        csv_blob = archive.read(archive.namelist()[0])
    reader = csv.DictReader(io.StringIO(csv_blob.decode('utf-8-sig')))
    if reader.fieldnames != fields:
        raise ValueError('Unexpected archive schema: ' + name)
    rows = list(reader)
    if not rows or any(None in r or any(v is None for v in r.values()) for r in rows):
        raise ValueError('Empty or ragged archive')
    return rows, {
        'file': name, 'url': receipt['url'], 'zip_sha256': sha(blob), 'csv_sha256': sha(csv_blob),
        'checksum_sha256': sha(checksum), 'collected_at_utc': receipt['collected_at_utc'],
        'checksum_collected_at_utc': check_receipt['collected_at_utc'],
        'source_last_modified': next((v for k,v in receipt['headers'].items() if k.lower() == 'last-modified'), None),
        'published_at_utc': None,
    }


def label_time(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
        raise ValueError('Unexpected source label format')
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)


def grid_quality(name, rows):
    day = datetime.strptime(name[-14:-4], '%Y-%m-%d').replace(tzinfo=UTC)
    expected = {day + timedelta(minutes=5*i) for i in range(288)}
    stamps, invalid = [], []
    for line, row in enumerate(rows, 2):
        try:
            stamps.append(label_time(row['create_time']))
        except ValueError:
            invalid.append(line)
    counts = defaultdict(int)
    for stamp in stamps:
        counts[stamp] += 1
    return {'file': name, 'rows': len(rows), 'backward_steps': sum(b<a for a,b in zip(stamps, stamps[1:])),
            'invalid_label_lines': invalid, 'duplicate_label_rows': sum(v-1 for v in counts.values()),
            'off_grid_labels': sorted(iso(t) for t in set(stamps)-expected),
            'missing_labels': sorted(iso(t) for t in expected-set(stamps)),
            'min_label': iso(min(stamps)) if stamps else None,
            'max_label': iso(max(stamps)) if stamps else None}


def deduplicate(candidates):
    """No first/last-wins conflicts. Exact duplicates retain every provenance link."""
    groups = defaultdict(list)
    for row in candidates:
        groups[(row['symbol'], row['field'], row['window_start_utc'])].append(row)
    accepted, rejected = [], []
    identical = 0
    for key in sorted(groups):
        group = groups[key]
        signatures = {(Decimal(r['value']), r['window_end_utc'], r['event_time_utc'], r['semantic_contract']) for r in group}
        if len(signatures) > 1:
            rejected.append({'reason': 'conflicting_duplicate', 'key': list(key), 'observations': group})
            continue
        merged = dict(group[0])
        merged['provenance'] = sorted([p for r in group for p in r['provenance']], key=lambda p: (p['file'], p['line']))
        # The merged record must not inherit an earlier observation's availability.
        for field in ('collected_at_utc', 'evidence_collected_at_utc', 'normalized_at_utc', 'research_available_at_utc'):
            merged[field] = iso(max(utc(r[field]) for r in group))
        identical += len(group)-1
        accepted.append(merged)
    return accepted, rejected, identical


def describe(rows):
    values = sorted(decimal(r['value']) for r in rows)
    if not values:
        return {'count': 0, 'min': None, 'max': None, 'mean': None, 'median': None, 'population_std': None}
    with localcontext() as context:
        context.prec = 50
        n = len(values)
        mean = sum(values)/n
        median = (values[(n-1)//2] + values[n//2])/2
        std = (sum((v-mean)**2 for v in values)/n).sqrt()
        quant = Decimal('.000000000001')
        return {'count': n, 'min': text_number(values[0]), 'max': text_number(values[-1]),
                'mean': text_number(mean.quantize(quant)), 'median': text_number(median.quantize(quant)),
                'population_std': text_number(std.quantize(quant))}


def derive(source, normalized_at):
    normalized_time = utc(normalized_at)
    if normalized_time < utc(SEMANTICS['verified_at_utc']):
        raise ValueError('Normalization precedes semantic verification')
    records, input_sha = load_evidence(source)
    if any(utc(r['collected_at_utc']) > normalized_time for r,_ in records.values()):
        raise ValueError('Normalization precedes collection')
    tables, sources, quality, quarantine, candidates, checks = {}, {}, [], [], [], []
    for path in sorted(records):
        name = path[4:]
        if not name.endswith('.zip'):
            continue
        if re.fullmatch(r'BTCUSDT-metrics-\d{4}-\d{2}-\d{2}\.zip', name):
            fields = METRICS
        elif re.fullmatch(r'BTCUSDT-5m-\d{4}-\d{2}-\d{2}\.zip', name):
            fields = KLINES
        elif re.fullmatch(r'BTCUSDT-fundingRate-\d{4}-\d{2}\.zip', name):
            fields = FUNDING
        else:
            raise ValueError('Unsupported archive: ' + name)
        tables[name], sources[name] = read_archive(name, records, fields)
    for name, rows in sorted(tables.items()):
        source_info = sources[name]
        if '-5m-' in name:
            continue  # Evidence only, never a price feature in the normalized dataset.
        is_metrics = '-metrics-' in name
        if is_metrics:
            quality.append(grid_quality(name, rows))
        day = name[-14:-4]
        approval = SEMANTICS['versions'].get(day) if is_metrics else None
        approved = approval is not None and source_info['zip_sha256'] == approval['metrics_sha256']
        reference, ref_info = {}, None
        if approved:
            ref_name = f'BTCUSDT-5m-{day}.zip'
            if ref_name not in tables or sources[ref_name]['zip_sha256'] != approval['reference_sha256']:
                raise ValueError('Missing or changed semantic reference')
            ref_info = sources[ref_name]
            for ref in tables[ref_name]:
                start = int(ref['open_time'])
                if int(ref['close_time']) != start+299999 or start in reference:
                    raise ValueError('Invalid reference window')
                buy, volume = decimal(ref['taker_buy_volume']), decimal(ref['volume'])
                if volume <= buy:
                    raise ValueError('Invalid reference volume')
                with localcontext() as context:
                    context.prec = 50
                    reference[start] = buy/(volume-buy)
        pairs, previous_pairs = [], []
        for line, row in enumerate(rows, 2):
            reason, values = 'unconfirmed_time_semantics', dict(row)
            if approved:
                values = {k:v for k,v in row.items() if k != TAKER}
                try:
                    start = label_time(row['create_time'])
                    value = decimal(row[TAKER])
                    if row['symbol'] != 'BTCUSDT' or start.date().isoformat() != day or start.second or start.minute % 5:
                        raise ValueError('Invalid symbol or window label')
                    epoch = int(start.timestamp()*1000)
                    if epoch not in reference:
                        raise ValueError('Missing reference window')
                    pairs.append((float(value), float(reference[epoch])))
                    if epoch-300000 in reference:
                        previous_pairs.append((float(value), float(reference[epoch-300000])))
                    end = start+timedelta(minutes=5)
                    evidence_at = max(utc(s[k]) for s in (source_info, ref_info)
                                      for k in ('collected_at_utc', 'checksum_collected_at_utc'))
                    candidates.append({'symbol': 'BTCUSDT', 'field': TAKER, 'value': row[TAKER],
                        'source_label': row['create_time'], 'window_start_utc': iso(start),
                        'window_end_utc': iso(end), 'event_time_utc': iso(end), 'published_at_utc': None,
                        'collected_at_utc': source_info['collected_at_utc'],
                        'evidence_collected_at_utc': iso(evidence_at),
                        'semantic_verified_at_utc': SEMANTICS['verified_at_utc'],
                        'normalized_at_utc': iso(normalized_time),
                        'research_available_at_utc': iso(max(end, evidence_at, normalized_time)),
                        'semantic_contract': SEMANTICS['id'],
                        'provenance': [{**source_info, 'line': line, 'raw_value': row[TAKER],
                                        'reference_file': ref_info['file'], 'reference_sha256': ref_info['zip_sha256']}]})
                except (ValueError, ArithmeticError):
                    reason, values = 'invalid_candidate', dict(row)
            elif is_metrics:
                reason = 'unverified_date_or_version'
            quarantine.append({'file': name, 'line': line, 'reason': reason,
                'raw_values': values, 'event_time_utc': None, 'published_at_utc': None,
                'collected_at_utc': source_info['collected_at_utc'], 'research_available_at_utc': None,
                'zip_sha256': source_info['zip_sha256']})
        if approved:
            # Recompute against original independent-in-file reference, never trust analysis.json.
            def correlation(values):
                if len(values) < 2:
                    raise ValueError('Insufficient semantic reference pairs')
                a,b = zip(*values)
                ma,mb = math.fsum(a)/len(a),math.fsum(b)/len(b)
                denominator = (math.fsum((x-ma)**2 for x in a)*math.fsum((y-mb)**2 for y in b))**.5
                if not denominator:
                    raise ValueError('Constant semantic reference')
                return math.fsum((x-ma)*(y-mb) for x,y in values)/denominator
            corr, previous_corr = correlation(pairs), correlation(previous_pairs)
            if len(pairs) != 288 or corr < .99 or corr-previous_corr < .5:
                raise ValueError('Semantic reference cross-check failed')
            checks.append({'file': name, 'pairs': len(pairs), 'pearson_r': round(corr,12),
                           'previous_window_pairs': len(previous_pairs), 'previous_window_r': round(previous_corr,12),
                           'mae': round(math.fsum(abs(x-y) for x,y in pairs)/len(pairs),12)})
    accepted, conflicts, identical = deduplicate(candidates)
    quarantine.extend(conflicts)
    stats = describe(accepted)
    stats.update(field=TAKER, unit='dimensionless buy/sell volume ratio',
                 scope='approved exact versions only; no prediction or trading',
                 per_day={day: describe([r for r in accepted if r['window_start_utc'].startswith(day)])
                          for day in sorted({r['window_start_utc'][:10] for r in accepted})})
    accepted_keys = {r['window_start_utc'] for r in accepted}
    expected = {iso(datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=UTC)+timedelta(minutes=5*i))
                for day in SEMANTICS['versions'] for i in range(288)}
    health = {'archives': quality, 'identical_duplicates_removed': identical,
              'conflicting_duplicate_groups': len(conflicts), 'reference_checks': checks,
              'accepted_scope_missing_windows': sorted(expected-accepted_keys),
              'gap_policy': 'report only; no interpolation, zero fill or carry forward',
              'quarantine_records': len(quarantine), 'accepted_records': len(accepted)}
    report = ('# Quant Lab 2.2 离线归档描述性统计\n\n'
        f"接纳 {len(accepted)} 个五分钟 taker 比率窗口；隔离 {len(quarantine)} 条来源记录。\n\n"
        f"平均值 {stats['mean']}，中位数 {stats['median']}，总体标准差 {stats['population_std']}；"
        f"范围 {stats['min']} 至 {stats['max']}。\n\n"
        f"已审核范围缺口 {len(expected-accepted_keys)}，去除相同重复 {identical}，冲突组 {len(conflicts)}。\n\n"
        '上述统计只描述两个精确版本的样本，不评价历史预测、策略收益或纸面交易。'
        '发布时间未知；研究可用时间不早于事件结束、全部支持证据采集和本次标准化时间。'
        'OI、多空比、资金费率及未验证日期/版本全部隔离，不向后续模型隐式暴露。\n\n'
        '来源 Binance Vision；数据及本报告按 CC BY-NC-SA 4.0，详见源证据 LICENSE.md。\n')
    payloads = {'accepted.jsonl': b''.join((json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n').encode('utf-8') for r in accepted),
                'quarantine.jsonl': b''.join((json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n').encode('utf-8') for r in quarantine),
                'quality.json': json_bytes(health), 'statistics.json': json_bytes(stats),
                'semantic_contract.json': json_bytes(SEMANTICS), 'report.md': report.encode('utf-8')}
    manifest = {'version': 1, 'source_receipts_sha256': input_sha,
        'adapter_sha256': sha(Path(__file__).read_bytes().replace(b'\r\n', b'\n')), 'normalized_at_utc': iso(normalized_time),
        'license': 'CC BY-NC-SA 4.0', 'attribution': 'Binance Vision https://data.binance.vision/',
        'files': {k: {'sha256':sha(v), 'bytes':len(v)} for k,v in sorted(payloads.items())}}
    payloads['manifest.json'] = json_bytes(manifest)
    return payloads


def build(source, output, normalized_at=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Use a new output directory outside source evidence')
    payloads = derive(source, normalized_at or iso(datetime.now(UTC)))
    output.mkdir(parents=True, exist_ok=False)
    for name, blob in payloads.items():
        (output / name).write_bytes(blob)
    return json.loads(payloads['statistics.json'])


def verify(source, output):
    output = Path(output)
    manifest = json.loads((output/'manifest.json').read_bytes())
    expected = derive(Path(source), manifest['normalized_at_utc'])
    if {p.name for p in output.iterdir()} != set(expected):
        raise ValueError('Unexpected or missing output files')
    if any((output/name).read_bytes() != blob for name,blob in expected.items()):
        raise ValueError('Offline replay mismatch')
    return {'verified_files': len(expected), 'accepted_records': json.loads(expected['statistics.json'])['count']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build','verify'))
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'build':build,'verify':verify}[args.command](args.source,args.output),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
