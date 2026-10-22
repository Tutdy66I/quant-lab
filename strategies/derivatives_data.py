"""Read-only Binance research snapshots. No dependency on trading/account modules."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

DAY = 86_400_000
VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / 'data' / 'derivatives' / 'binance_btcusdt_v1'
DOCS = 'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'
ENDPOINTS = {
    'spot': ('https://api.binance.com', '/api/v3/klines'),
    'oi': ('https://fapi.binance.com', '/futures/data/openInterestHist'),
    'long_short': ('https://fapi.binance.com', '/futures/data/globalLongShortAccountRatio'),
    'taker': ('https://fapi.binance.com', '/futures/data/takerlongshortRatio'),
    'funding': ('https://fapi.binance.com', '/fapi/v1/fundingRate'),
}


def utc_ms():
    return time.time_ns() // 1_000_000


def iso(timestamp):
    return datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('Expected nonnegative integer milliseconds')
    return value


def number(value, minimum=None):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a numeric metric')
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError('Nonfinite or out-of-range metric')
    return result


def request_specs(start, stop):
    """UTC days [start, stop); end-stamped metrics include stop midnight."""
    for name, (base, path) in ENDPOINTS.items():
        params = {'symbol': 'BTCUSDT', 'startTime': start, 'endTime': stop - 1}
        if name == 'spot':
            params.update(interval='1d', limit=1000)
        elif name == 'funding':
            params['limit'] = 1000
        else:
            params.update(period='1d', limit=500)
            if name in ('oi', 'long_short'):
                params.update(startTime=start + DAY, endTime=stop)
        yield name, base + path + '?' + urlencode(params)


def fetch_public(url, *, opener=urlopen, sleep=time.sleep, clock=utc_ms):
    """Bounded retries; retain all HTTP bodies including unsuccessful attempts."""
    attempts = []
    for attempt in range(3):
        headers = {}
        try:
            with opener(Request(url, headers={'User-Agent': 'quant-lab-research/1'}), timeout=20) as response:
                status, raw = response.status, response.read()
                headers = dict(response.headers)
        except HTTPError as error:
            status, raw, headers = error.code, error.read(), dict(error.headers or {})
        except (URLError, TimeoutError, OSError) as error:
            status, raw = None, str(error).encode('utf-8')
        evidence_headers = {key: value for key, value in headers.items()
                            if key.lower() in ('date', 'retry-after') or key.lower().startswith('x-mbx-')}
        attempts.append({'status': status, 'body': raw, 'received_at_ms': clock(),
                         'headers': evidence_headers})
        if status == 200:
            return raw, attempts
        if status == 418 or (status is not None and status != 429 and status < 500):
            break
        if attempt < 2:
            try:
                delay = max(2 ** attempt, float(headers.get('Retry-After', '0')))
            except ValueError:
                delay = 2 ** attempt
            if not math.isfinite(delay) or delay > 60:
                break  # preserve server cooldown rather than retrying too soon
            sleep(delay)
    return None, attempts


def normalize(name, raw, start, stop, observed):
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError('Expected array, not API error/object')
    rows = {}
    for item in payload:
        if name == 'spot':
            if not isinstance(item, list) or len(item) != 12:
                raise ValueError('Expected 12-field spot kline')
            stamp = integer(item[0])
            end = integer(item[6]) + 1
            if end != stamp + DAY:
                raise ValueError('Invalid daily kline close time')
            o, h, low, close = [number(x, 0) for x in item[1:5]]
            if low <= 0 or not low <= min(o, close) <= max(o, close) <= h:
                raise ValueError('Invalid OHLC range')
            number(item[5], 0)
            values = {'open': o, 'high': h, 'low': low, 'close': close}
        else:
            if not isinstance(item, dict) or (name != 'taker' and item.get('symbol') != 'BTCUSDT'):
                raise ValueError('Invalid metric symbol/schema')
            stamp = integer(item['fundingTime' if name == 'funding' else 'timestamp'])
            end = stamp + DAY if name == 'taker' else stamp
            if name == 'oi':
                values = {'open_interest': number(item['sumOpenInterest'], 0),
                          'open_interest_value': number(item['sumOpenInterestValue'], 0)}
            elif name == 'long_short':
                long, short = number(item['longAccount'], 0), number(item['shortAccount'], 0)
                if long > 1 or short > 1 or abs(long + short - 1) > 0.001:
                    raise ValueError('Invalid account proportions')
                values = {'long_account': long, 'short_account': short,
                          'long_short_ratio': number(item['longShortRatio'], 0)}
            elif name == 'taker':
                values = {'buy_volume': number(item['buyVol'], 0),
                          'sell_volume': number(item['sellVol'], 0),
                          'reported_buy_sell_ratio': number(item['buySellRatio'], 0)}
            elif name == 'funding':
                values = {'funding_rate': number(item['fundingRate'])}
            else:
                raise ValueError('Unknown metric')
        if name != 'funding' and stamp % DAY:
            raise ValueError('Daily timestamp must align to UTC midnight')
        lower, upper = (start + DAY, stop) if name in ('oi', 'long_short') else (start, stop - 1)
        if not lower <= stamp <= upper or end > observed:
            raise ValueError('Out-of-window, future or unclosed record')
        day = (stamp // DAY) * DAY if name == 'funding' else (end - DAY)
        row = {'timestamp_ms': stamp, 'day_start_ms': day, 'event_end_ms': end,
               'available_at_ms': max(end, observed), **values}
        if stamp in rows and rows[stamp] != row:
            raise ValueError('Conflicting duplicate timestamp')
        rows[stamp] = row
    if len(payload) >= (1000 if name in ('spot', 'funding') else 500):
        raise ValueError('Response may be truncated; pagination required')
    return sorted(rows.values(), key=lambda row: row['timestamp_ms'])


def build_dataset(manifest, raw_by_name):
    if manifest['schema_version'] != VERSION or manifest['symbol'] != 'BTCUSDT':
        raise ValueError('Unsupported snapshot version/symbol')
    start, stop = integer(manifest['start_ms']), integer(manifest['stop_ms'])
    if start % DAY or stop % DAY or not DAY <= stop - start <= 28 * DAY:
        raise ValueError('Invalid snapshot window')
    expected = dict(request_specs(start, stop))
    if set(manifest['sources']) != set(expected):
        raise ValueError('Missing sources')
    result = {'schema_version': VERSION, 'symbol': 'BTCUSDT', 'start_ms': start,
              'stop_ms': stop, 'metrics': {}, 'quality': {}}
    for name, url in expected.items():
        source = manifest['sources'][name]
        if source['url'] != url or source.get('error'):
            raise ValueError('Invalid/failed source: ' + name)
        observed = integer(source['received_at_ms'])
        if observed < stop or observed > integer(manifest['completed_at_ms']):
            raise ValueError('Invalid observation time')
        raw = raw_by_name[name]
        if digest(raw) != source['sha256']:
            raise ValueError('Raw fingerprint mismatch: ' + name)
        rows = normalize(name, raw, start, stop, observed)
        result['metrics'][name] = rows
        days = {row['day_start_ms'] for row in rows}
        result['quality'][name] = {
            'rows': len(rows), 'missing_days': [iso(t)[:10] for t in range(start, stop, DAY) if t not in days],
            'funding_completeness': 'unknown; no fixed funding cadence assumed' if name == 'funding' else None,
        }
    return result


def collect(root=DEFAULT_ROOT, *, days=28, fetcher=fetch_public, clock=utc_ms):
    if type(days) is not int or not 1 <= days <= 28:
        raise ValueError('days must be 1..28 (recent-history window)')
    started = clock()
    stop = started // DAY * DAY
    start = stop - days * DAY
    target = Path(root) / (datetime.fromtimestamp(started / 1000, timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:12])
    target.mkdir(parents=True, exist_ok=False)
    (target / 'raw').mkdir()
    manifest = {'schema_version': VERSION, 'symbol': 'BTCUSDT', 'started_at_ms': started,
                'start_ms': start, 'stop_ms': stop, 'documentation': DOCS,
                'status': 'collecting', 'sources': {}}
    (target / 'manifest.json').write_bytes(encode(manifest))
    raw_by_name = {}
    for name, url in request_specs(start, stop):
        raw, attempts = fetcher(url)
        source = {'url': url, 'attempts': []}
        for i, attempt in enumerate(attempts):
            filename = f'raw/{name}-{i}.bin'
            body = attempt['body']
            (target / filename).write_bytes(body)
            source['attempts'].append({key: value for key, value in attempt.items() if key != 'body'} |
                                      {'file': filename, 'sha256': digest(body)})
        if raw is None:
            source['error'] = 'HTTP/network collection failed'
        else:
            source.update(file=source['attempts'][-1]['file'], sha256=digest(raw),
                          received_at_ms=source['attempts'][-1]['received_at_ms'])
            raw_by_name[name] = raw
        manifest['sources'][name] = source
        (target / 'manifest.json').write_bytes(encode(manifest))
        time.sleep(0.2)  # sequential, low-rate public requests
    manifest['completed_at_ms'] = clock()
    try:
        dataset = build_dataset(manifest, raw_by_name)
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        manifest.update(status='failed', validation_error=str(error))
        (target / 'manifest.json').write_bytes(encode(manifest))
        raise ValueError(f'Collection retained for diagnosis at {target}: {error}') from error
    manifest['status'] = 'complete'
    (target / 'manifest.json').write_bytes(encode(manifest))
    (target / 'normalized.json').write_bytes(encode(dataset))
    return target


def safe_read(snapshot, filename):
    path = (snapshot / filename).resolve()
    if not path.is_relative_to(snapshot.resolve() / 'raw'):
        raise ValueError('Invalid raw path')
    return path.read_bytes()


def verify(snapshot):
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / 'manifest.json').read_bytes())
    if manifest.get('status') != 'complete':
        raise ValueError('Snapshot incomplete/failed')
    raw_by_name = {}
    for name, source in manifest['sources'].items():
        for attempt in source['attempts']:
            if digest(safe_read(snapshot, attempt['file'])) != attempt['sha256']:
                raise ValueError('Attempt fingerprint mismatch')
        last = source['attempts'][-1]
        if last['status'] != 200 or any(source[k] != last[k] for k in ('file', 'sha256', 'received_at_ms')):
            raise ValueError('Invalid successful attempt metadata')
        raw_by_name[name] = safe_read(snapshot, source['file'])
    dataset = build_dataset(manifest, raw_by_name)
    if (snapshot / 'normalized.json').read_bytes() != encode(dataset):
        raise ValueError('Normalized dataset differs from raw replay')
    return dataset, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    gather = sub.add_parser('collect')
    gather.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    gather.add_argument('--days', type=int, default=28)
    check = sub.add_parser('verify')
    check.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    if args.command == 'collect':
        print(collect(args.root, days=args.days))
    else:
        dataset, _ = verify(args.snapshot)
        print(json.dumps(dataset['quality'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
