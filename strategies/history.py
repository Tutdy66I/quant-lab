"""Versioned Binance spot daily archives; no observer imports or account access."""
import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import time
import urllib.request
import zipfile
from uuid import uuid4

from strategies.market_data import parse_daily_rows

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/'
DOCS = 'https://github.com/binance/binance-public-data'
DATASET = ROOT / 'data/history/binance_btcusdt_1d_v1'


@contextmanager
def staging_directory(parent):
    # Windows TemporaryDirectory uses a private ACL which survives rename.
    # Use a normal inherited directory so published datasets remain readable.
    parent = Path(parent).resolve()
    stage = parent / ('.collect-' + uuid4().hex)
    stage.mkdir()
    try:
        yield stage
    finally:
        if stage.resolve().parent != parent or not stage.name.startswith('.collect-'):
            raise ValueError('Unsafe staging cleanup path')
        shutil.rmtree(stage)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def months(start, end):
    first, last = (datetime.strptime(value, '%Y-%m') for value in (start, end))
    if first > last:
        raise ValueError('Start month must precede end month')
    values = []
    while first <= last:
        values.append(first.strftime('%Y-%m'))
        first = first.replace(year=first.year + (first.month == 12), month=first.month % 12 + 1)
    return values


def normalize_archive(blob, checksum, month):
    name = f'BTCUSDT-1d-{month}'
    parts = checksum.decode('ascii').split()
    if (len(parts) != 2 or not re.fullmatch('[0-9a-fA-F]{64}', parts[0])
            or parts[1].lstrip('*') != name + '.zip' or sha256(blob) != parts[0].lower()):
        raise ValueError('Publisher checksum mismatch')
    year, number = map(int, month.split('-'))
    unit = 1_000_000 if year >= 2025 else 1000
    start = int(datetime(year, number, 1, tzinfo=timezone.utc).timestamp())
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if archive.namelist() != [name + '.csv']:
            raise ValueError('Unexpected archive members')
        rows = list(csv.reader(io.StringIO(archive.read(name + '.csv').decode('utf-8'))))
    if len(rows) != calendar.monthrange(year, number)[1]:
        raise ValueError('Incomplete monthly archive')
    normalized = []
    for index, row in enumerate(rows):
        expected = start + index * 86400
        if (len(row) != 12 or int(row[0]) != expected * unit
                or int(row[6]) != (expected + 86400) * unit - 1):
            raise ValueError('Invalid timestamp, close time, order, or row schema')
        # Preserve source decimal price text. No interpolation or forward filling.
        normalized.append([expected, *row[1:5]])
    parse_daily_rows(normalized)
    return normalized


def download(url):
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'quant-lab-history/1'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read(), {'fetched_at_utc': utcnow(),
                                         'last_modified': response.headers.get('Last-Modified'),
                                         'etag': response.headers.get('ETag')}
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def collect_month(month, raw_dir):
    filename = f'BTCUSDT-1d-{month}.zip'
    blob, receipt = download(BASE + filename)
    checksum, check_receipt = download(BASE + filename + '.CHECKSUM')
    rows = normalize_archive(blob, checksum, month)
    (raw_dir / filename).write_bytes(blob)
    (raw_dir / (filename + '.CHECKSUM')).write_bytes(checksum)
    return rows, {'month': month, 'url': BASE + filename, 'file': filename,
                  'sha256': sha256(blob), 'checksum_url': BASE + filename + '.CHECKSUM',
                  'checksum_sha256': sha256(checksum), 'checksum_receipt': check_receipt,
                  'rows': len(rows), **receipt}


def csv_bytes(rows):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(['timestamp', 'open', 'high', 'low', 'close'])
    writer.writerows(rows)
    return buffer.getvalue().encode('utf-8')


def collect(destination, start='2018-01', end='2026-09'):
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Dataset versions are immutable; choose a new destination')
    requested = months(start, end)
    if end >= datetime.now(timezone.utc).strftime('%Y-%m'):
        raise ValueError('Only completed calendar months may be collected')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with staging_directory(destination.parent) as temp:
        stage = Path(temp) / 'dataset'
        raw = stage / 'raw'
        raw.mkdir(parents=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            collected = list(pool.map(lambda month: collect_month(month, raw), requested))
        rows = [row for batch, _ in collected for row in batch]
        candles = parse_daily_rows(rows)
        normalized = csv_bytes(rows)
        (stage / 'ohlc.csv').write_bytes(normalized)
        manifest = {
            'schema_version': 1, 'source': 'Binance public spot monthly archives',
            'documentation': DOCS, 'symbol': 'BTCUSDT', 'quote_asset': 'USDT',
            'interval': '1d', 'timezone': 'UTC', 'collected_at_utc': utcnow(),
            'start_month': start, 'end_month': end,
            'start_date': candles[0].date, 'end_date': candles[-1].date, 'rows': len(rows),
            'normalized_file': 'ohlc.csv', 'normalized_sha256': sha256(normalized),
            'normalization': 'UTC integer seconds; original OHLC decimal text; UTF-8 LF CSV',
            'close_policy': 'complete_months_exact_daily_close_timestamp_no_unconfirmed_tail',
            'quality': {'publisher_checksums_verified': True, 'contiguous_daily_rows': True,
                        'ohlc_ranges_valid': True, 'imputed_rows': 0},
            'limitations': ['single_exchange', 'USDT_not_USD', 'revised_historical_archive_not_point_in_time',
                            'publisher_checksum_is_integrity_not_independent_price_verification'],
            'archives': [receipt for _, receipt in collected],
        }
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        verify_dataset(stage)
        stage.rename(destination)
    return manifest


def verify_dataset(path):
    """Offline replay from raw archives; reject missing, tampered or inconsistent data."""
    path = Path(path)
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest['schema_version'] != 1 or manifest['symbol'] != 'BTCUSDT'
            or manifest['timezone'] != 'UTC' or manifest['interval'] != '1d'
            or manifest['normalized_file'] != 'ohlc.csv'):
        raise ValueError('Unsupported dataset identity/schema')
    if [a['month'] for a in manifest['archives']] != months(manifest['start_month'], manifest['end_month']):
        raise ValueError('Missing or reordered monthly archives')
    rows = []
    for entry in manifest['archives']:
        filename = f"BTCUSDT-1d-{entry['month']}.zip"
        if entry['file'] != filename or entry['url'] != BASE + filename:
            raise ValueError('Unexpected archive identity')
        blob = (path / 'raw' / filename).read_bytes()
        checksum = (path / 'raw' / (filename + '.CHECKSUM')).read_bytes()
        if sha256(blob) != entry['sha256'] or sha256(checksum) != entry['checksum_sha256']:
            raise ValueError('Raw archive fingerprint mismatch')
        fetched = datetime.fromisoformat(entry['fetched_at_utc'])
        if fetched.tzinfo is None:
            raise ValueError('Missing acquisition timezone')
        batch = normalize_archive(blob, checksum, entry['month'])
        if len(batch) != entry['rows'] or batch[-1][0] + 86400 > fetched.timestamp():
            raise ValueError('Unclosed or miscounted source rows')
        rows.extend(batch)
    normalized = (path / 'ohlc.csv').read_bytes()
    if normalized != csv_bytes(rows) or sha256(normalized) != manifest['normalized_sha256']:
        raise ValueError('Normalized data does not match raw archives')
    candles = parse_daily_rows(rows)
    if (len(candles) != manifest['rows'] or candles[0].date != manifest['start_date']
            or candles[-1].date != manifest['end_date']):
        raise ValueError('Dataset coverage mismatch')
    return candles, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('collect', 'verify'))
    parser.add_argument('--dataset', type=Path, default=DATASET)
    parser.add_argument('--start', default='2018-01')
    parser.add_argument('--end', default='2026-09')
    args = parser.parse_args()
    if args.action == 'collect':
        collect(args.dataset, args.start, args.end)
    candles, _ = verify_dataset(args.dataset)
    print(f'Verified {len(candles)} daily rows: {candles[0].date} to {candles[-1].date}')


if __name__ == '__main__':
    main()
