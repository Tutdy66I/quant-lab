"""Bounded, manual feasibility probe; never imported by strategies or scheduled jobs.

Run `python research/binance_archive_phase1.py collect NEW_DIRECTORY`, then
`python research/binance_archive_phase1.py analyze DIRECTORY` offline.
No API fallback, credentials, retry, proxy selection, or timestamp normalization.
"""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import math
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

VISION = 'https://data.binance.vision/'
BUCKET = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
DOC = 'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'
DAYS = ['2021-12-01', '2025-06-24', '2026-06-23', '2026-06-24',
        '2026-06-25', '2026-06-26', '2026-07-01', '2026-10-20']


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    # Stable derived artifacts on Windows and Linux; raw responses stay untouched.
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def collect(root):
    root.mkdir(parents=True, exist_ok=False)
    raw = root / 'raw'
    raw.mkdir()
    receipts = []

    def fetch(url, name):
        started = now()
        try:
            with urlopen(Request(url, headers={'User-Agent': 'quant-lab-feasibility/1'}), timeout=60) as r:
                status, blob, headers, final = r.status, r.read(), dict(r.headers), r.url
        except HTTPError as e:
            status, blob, headers, final = e.code, e.read(), dict(e.headers), e.url
        path = raw / name
        path.write_bytes(blob)
        receipts.append({'url': url, 'final_url': final, 'file': 'raw/' + name,
                         'request_started_at_utc': started, 'collected_at_utc': now(),
                         'status': status, 'headers': headers, 'bytes': len(blob),
                         'sha256': digest(blob), 'published_at_utc': None})
        dump(root / 'receipts.json', receipts)
        print(status, name, len(blob), flush=True)
        if status in (401, 403, 429, 451) or status >= 500:
            raise RuntimeError(f'Stop on service restriction/failure {status}; no fallback or retry')
        return blob if status == 200 else None

    fetch('https://raw.githubusercontent.com/binance/binance-public-data/master/TERMS_AND_CONDITIONS.md', 'dataset_terms.md')
    fetch(VISION + 'terms-of-use.html', 'vision_terms.html')
    fetch(VISION + 'Binance_Vision-Terms_of_Use.pdf', 'vision_terms.pdf')
    fetch('https://raw.githubusercontent.com/binance/binance-public-data/master/README.md', 'publisher_readme.md')
    fetch('https://api.github.com/repos/binance/binance-public-data/issues/491', 'issue491.json')
    fetch('https://api.github.com/repos/binance/binance-public-data/issues/491/comments?per_page=100', 'issue491_comments.json')
    fetch(DOC, 'market_data_docs.html')
    fetch(DOC.rsplit('/', 1)[0] + '/1.0.0/schema.yaml', 'market_data_schema.yaml')
    fetch(VISION, 'vision_index.html')
    fetch(VISION + 'list.js', 'vision_list.js')
    # The public website explicitly configures this S3 URL for listing. Objects
    # are still fetched via Vision. Stop on any restriction; no alternate host.
    for kind, prefix in [('metrics', 'data/futures/um/daily/metrics/BTCUSDT/'),
                         ('funding', 'data/futures/um/monthly/fundingRate/BTCUSDT/')]:
        marker = ''
        for page in range(20):
            blob = fetch(BUCKET + '?' + urlencode({'prefix': prefix, 'marker': marker}), f'{kind}_listing_{page:02}.xml')
            if blob is None:
                break
            tree = ET.fromstring(blob)
            if tree.findtext('s:IsTruncated', namespaces=NS) != 'true':
                break
            keys = [x.findtext('s:Key', namespaces=NS) for x in tree.findall('s:Contents', NS)]
            marker = tree.findtext('s:NextMarker', namespaces=NS) or keys[-1]
        else:
            raise RuntimeError('Listing exceeded bounded page budget')
    files = [f'data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{d}.zip' for d in DAYS]
    files += [f'data/futures/um/daily/klines/BTCUSDT/5m/BTCUSDT-5m-{d}.zip'
              for d in ['2026-06-24', '2026-06-25']]
    files += [f'data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{m}.zip'
              for m in ['2026-06', '2026-09']]
    for key in files:
        name = key.rsplit('/', 1)[-1]
        if fetch(VISION + key, name) is not None:
            fetch(VISION + key + '.CHECKSUM', name + '.CHECKSUM')


def analyze(root):
    receipts = json.loads((root / 'receipts.json').read_text(encoding='utf-8'))
    for r in receipts:
        blob = (root / r['file']).read_bytes()
        if digest(blob) != r['sha256'] or len(blob) != r['bytes']:
            raise ValueError('Receipt integrity failure: ' + r['file'])
    findings = {'receipt_files_verified': len(receipts), 'archives': [], 'coverage': {}, 'flow_alignment': {}}
    tables = {}
    for r in receipts:
        path = root / r['file']
        if r['status'] != 200 or path.suffix != '.zip':
            continue
        blob = path.read_bytes()
        check = path.with_name(path.name + '.CHECKSUM').read_text(encoding='ascii').split()
        if len(check) != 2 or check[0].lower() != digest(blob) or check[1].lstrip('*') != path.name:
            raise ValueError('Publisher checksum mismatch: ' + path.name)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            if z.namelist() != [path.stem + '.csv']:
                raise ValueError('Unexpected ZIP members')
            csv_blob = z.read(z.namelist()[0])
        rows = list(csv.DictReader(io.StringIO(csv_blob.decode('utf-8-sig'))))
        tables[path.name] = rows
        field = 'create_time' if 'metrics' in path.name else ('calc_time' if 'fundingRate' in path.name else 'open_time')
        labels = [x[field] for x in rows]
        summary = {'file': path.name, 'zip_sha256': digest(blob), 'csv_sha256': digest(csv_blob),
                   'rows': len(rows), 'fields': list(rows[0]), 'first': rows[0], 'last': rows[-1],
                   'duplicate_labels': len(labels) - len(set(labels))}
        if field == 'create_time':
            ts = [datetime.fromisoformat(x) for x in labels]
            ordered = sorted(ts)
            day = datetime.strptime(path.stem[-10:], '%Y-%m-%d')
            expected = {day + timedelta(minutes=5*i) for i in range(288)}
            summary['min_label'] = min(labels)
            summary['max_label'] = max(labels)
            summary['backward_steps'] = sum(b<a for a,b in zip(ts,ts[1:]))
            summary['non_300_second_steps'] = sum((b-a).total_seconds() != 300 for a,b in zip(ts,ts[1:]))
            summary['sorted_non_300_second_steps'] = sum((b-a).total_seconds() != 300 for a,b in zip(ordered,ordered[1:]))
            summary['missing_start_grid_labels'] = sorted(t.isoformat() for t in expected-set(ts))
            summary['outside_start_grid_labels'] = sorted(t.isoformat() for t in set(ts)-expected)
            summary['off_grid_labels'] = [t.isoformat() for t in ts if t.second or t.microsecond or t.minute % 5]
            summary['nonfinite_or_negative_values'] = sum(not Decimal(v).is_finite() or Decimal(v) < 0
                for row in rows for k,v in row.items() if k not in ('create_time', 'symbol'))
        elif field == 'calc_time':
            summary['funding_intervals_hours'] = sorted({x['funding_interval_hours'] for x in rows})
            summary['min_calc_time_utc'] = datetime.fromtimestamp(min(map(int, labels))/1000, timezone.utc).isoformat()
            summary['max_calc_time_utc'] = datetime.fromtimestamp(max(map(int, labels))/1000, timezone.utc).isoformat()
        findings['archives'].append(summary)
    for kind in ('metrics', 'funding'):
        objects = []
        for p in sorted((root / 'raw').glob(kind + '_listing_*.xml')):
            for x in ET.fromstring(p.read_bytes()).findall('s:Contents', NS):
                key = x.findtext('s:Key', namespaces=NS)
                if key.endswith('.zip'):
                    objects.append({'key': key, 'last_modified': x.findtext('s:LastModified', namespaces=NS)})
        keys = [x['key'] for x in objects]
        result = {'zip_objects': len(objects), 'first': objects[0] if objects else None,
                  'last': objects[-1] if objects else None, 'objects': objects}
        if kind == 'metrics' and objects:
            dates = {k[-14:-4] for k in keys}
            start, end = datetime.fromisoformat(min(dates)), datetime.fromisoformat(max(dates))
            result['missing_daily_objects'] = [(start + timedelta(days=i)).date().isoformat()
                for i in range((end-start).days+1) if (start+timedelta(days=i)).date().isoformat() not in dates]
        findings['coverage'][kind] = result
    before = tables.get('BTCUSDT-metrics-2026-06-24.zip', [])
    after = tables.get('BTCUSDT-metrics-2026-06-25.zip', [])
    collisions = [(a,b) for a in before for b in after if a['create_time'] == b['create_time']]
    findings['boundary_collisions'] = [{'before': a, 'after': b, 'differing_fields': [k for k in a if a[k] != b[k]]} for a,b in collisions]
    klines = {}
    for day in ['2026-06-24', '2026-06-25']:
        for row in tables.get(f'BTCUSDT-5m-{day}.zip', []):
            buy = Decimal(row['taker_buy_volume'])
            sell = Decimal(row['volume']) - buy
            if sell:
                klines[int(row['open_time'])] = float(buy / sell)
    def correlation(a, b):
        ma, mb = math.fsum(a)/len(a), math.fsum(b)/len(b)
        value = math.fsum((x-ma)*(y-mb) for x,y in zip(a,b)) / (math.fsum((x-ma)**2 for x in a)*math.fsum((y-mb)**2 for y in b))**0.5
        return round(value, 12)
    for day in ['2026-06-24', '2026-06-25']:
        result = {}
        for offset in [-300000, 0]:
            pairs = []
            for row in tables.get(f'BTCUSDT-metrics-{day}.zip', []):
                ts = int(datetime.fromisoformat(row['create_time']).replace(tzinfo=timezone.utc).timestamp()*1000)
                if ts+offset in klines:
                    pairs.append((float(row['sum_taker_long_short_vol_ratio']), klines[ts+offset]))
            if len(pairs) > 1:
                a,b = zip(*pairs)
                result[str(offset)] = {'pairs': len(pairs), 'pearson_r': correlation(a,b),
                    'mean_absolute_error': round(math.fsum(abs(x-y) for x,y in pairs)/len(pairs), 12)}
        findings['flow_alignment'][day] = result
    dump(root / 'analysis.json', findings)
    print(json.dumps({k:v for k,v in findings.items() if k != 'coverage'}, indent=2))
    print('Coverage:', {k: {a:b for a,b in v.items() if a != 'objects'} for k,v in findings['coverage'].items()})


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('collect', 'analyze'):
        raise SystemExit('Usage: binance_archive_phase1.py collect|analyze DIRECTORY')
    {'collect': collect, 'analyze': analyze}[sys.argv[1]](Path(sys.argv[2]))
