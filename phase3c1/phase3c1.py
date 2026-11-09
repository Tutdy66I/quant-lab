"""Synthetic, memory-only protocol exercise; no files, network, or trading.

The synthetic designation is a caller claim, not authenticated provenance.
Do not use this module to admit or evaluate real market observations.
"""
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import random
import sys
from pathlib import Path

PROTOCOL_SHA256 = '4c9a904d12c469a23cec928e195bf2c91a111cb7f47d4cbb1f52177fef5bc82e'
ALPHAS = (0.01, 0.1, 1.0, 10.0)
PARTITIONS = {'train': (date(2018, 1, 1), date(2021, 12, 31)),
              'tune': (date(2022, 1, 1), date(2023, 12, 31)),
              'evaluation': (date(2024, 1, 1), date(2026, 9, 30))}
UTC = timezone.utc


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('finite numeric value required')
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError('finite numeric value required') from error
    if not math.isfinite(value):
        raise ValueError('finite numeric value required')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def capture_synthetic_input(rows):
    """Return immutable canonical JSON bytes; callers may retain for replay.

    The bytes are the input identity. They do not authenticate provenance or
    assert that a caller-owned mutable object was atomic during serialization.
    """
    if type(rows) is bytes:
        try:
            parsed = json.loads(rows.decode('utf-8'))
            if not isinstance(parsed, list) or canonical(parsed) != rows:
                raise ValueError('snapshot must be a canonical JSON array')
        except (UnicodeError, TypeError, ValueError) as error:
            raise ValueError('invalid canonical input snapshot') from error
        return rows
    if not isinstance(rows, list):
        raise ValueError('synthetic rows must be a list or canonical snapshot bytes')
    return canonical(rows)


def _samples_from_snapshot(snapshot, **kwargs):
    """Decode an immutable snapshot into private objects for sample creation."""
    if type(snapshot) is not bytes:
        raise ValueError('immutable input snapshot required')
    return build_samples(json.loads(snapshot.decode('utf-8')), **kwargs)


def frozen_method_v1():
    """Fresh canonical specification for the fixed, hash-bound protocol v1.

    This guards ordinary configuration drift, not malicious Python code able
    to replace the verifier itself. No mutable caller object is the authority.
    """
    return {'protocol_sha256': '4c9a904d12c469a23cec928e195bf2c91a111cb7f47d4cbb1f52177fef5bc82e',
        'schema': 'phase3b-v1-synthetic', 'calendar': 'utc_daily_contiguous',
        'dataset_start': '2018-01-01', 'dataset_end': '2026-09-30',
        'partitions': {'train': ['2018-01-01', '2021-12-31'],
                       'tune': ['2022-01-01', '2023-12-31'],
                       'evaluation': ['2024-01-01', '2026-09-30']},
        'partition_basis': 'decision_day', 'partition_cutoff': 'day_after_end_0000_utc_inclusive',
        'decision_utc_hour': 1, 'entry_day_offset': 1, 'entry_utc_hour': 0,
        'label_end_day_offset': 2, 'label_end_utc_hour': 0,
        'label_available_day_offset': 2, 'label_available_utc_hour': 1,
        'availability': 'retrospective_assumption_actual_unknown', 'max_age_seconds': 3600,
        'features': ['close_d_over_close_d_minus_1_minus_1', 'close_d_over_close_d_minus_2_minus_1'],
        'warmup_completed_bars': 3, 'target': 'entry_day_close_over_open_minus_1',
        'baselines': ['zero', 'previous_close_return'], 'primary_loss': 'ridge_squared_error_minus_previous_squared_error',
        'ridge': {'alphas': [0.01, 0.1, 1.0, 10.0], 'objective': 'mean_squared_error_plus_alpha_l2',
                  'intercept': 'centered_unpenalized', 'scale': 'train_only_population_std_two_pass_fsum',
                  'solver': 'centered_two_feature_cholesky_binary64', 'tie': 'largest_alpha_exact_mse',
                  'selection': 'tune_mse_train_fitted_no_refit', 'evaluation_update': False},
        'diagnostic_strategies': {'names': ['ma20_60', 'flat', 'buy_hold'],
                                  'computed_in_this_prototype': False, 'fee_per_side': 0.001,
                                  'slippage_per_side': 0.0005, 'forecast_to_position': False},
        'numeric_acceptance': {'absolute_tolerance': 1e-12, 'relative_tolerance': 1e-10,
                               'input_order': 'decision_date_ascending', 'arithmetic': 'binary64'},
        'bootstrap': {'block_length': 14, 'resamples': 2000, 'seed': 20261108,
                      'rng': 'cpython-3.12.14-random-randrange', 'blocks': 'overlap_non_circular',
                      'sample': 'replacement_concat_truncate', 'minimum_full_blocks': 20,
                      'quantiles': [0.025, 0.975], 'quantile_method': 'linear_b_minus_1',
                      'failure': 'reject_entire_interval'},
        'missing': 'fail_no_imputation', 'invalid_numeric': 'fail_nonfinite',
        'labels': 'complete_and_simulated_available_by_partition_cutoff',
        'alignment': 'exact_complete_evaluation_dates_and_targets',
        'failure': 'no_complete_partial_report', 'research_admission': 'insufficient_evidence'}


def _checked_method(method_config=None):
    frozen = frozen_method_v1()
    expected_partitions = {name: tuple(date.fromisoformat(s) for s in dates)
                           for name, dates in frozen['partitions'].items()}
    if (PROTOCOL_SHA256 != frozen['protocol_sha256'] or UTC is not timezone.utc
            or PARTITIONS != expected_partitions or ALPHAS != tuple(frozen['ridge']['alphas'])):
        raise ValueError('runtime configuration conflicts with frozen protocol')
    if method_config is not None and canonical(method_config) != canonical(frozen):
        raise ValueError('method configuration conflicts with frozen protocol')
    return frozen


def _moment(day, hour=0):
    return datetime.combine(day, time(hour), timezone.utc)


def build_samples(rows, *, dataset_kind, snapshot_collected_at, dataset_version):
    """Build all partition samples from complete caller-declared synthetic daily bars.

    Actual source availability is deliberately unknown, not fabricated. Simulation
    times are assumptions. This interface never reads a dataset from storage.
    """
    method = _checked_method()
    partitions = {name: tuple(date.fromisoformat(s) for s in dates) for name, dates in method['partitions'].items()}
    if dataset_kind != 'synthetic' or not isinstance(rows, list) or not rows or not isinstance(dataset_version, str) or not dataset_version:
        raise ValueError('declared synthetic input and version required')
    try:
        captured = datetime.fromisoformat(snapshot_collected_at.replace('Z', '+00:00'))
        if captured.tzinfo is None or captured.utcoffset() is None:
            raise ValueError('snapshot time must be timezone-aware')
        captured = captured.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError('invalid snapshot time') from error
    data = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'date', 'open', 'high', 'low', 'close'}:
            raise ValueError('invalid synthetic bar schema')
        try:
            day = date.fromisoformat(row['date'])
        except (TypeError, ValueError) as error:
            raise ValueError('invalid date') from error
        if day.isoformat() != row['date'] or day in data:
            raise ValueError('duplicate or invalid date')
        prices = {key: finite(row[key]) for key in ('open', 'high', 'low', 'close')}
        if min(prices.values()) <= 0 or prices['high'] < max(prices.values()) or prices['low'] > min(prices.values()):
            raise ValueError('invalid OHLC')
        data[day] = prices
    days = sorted(data)
    if len(days) != (days[-1] - days[0]).days + 1 or days[0] != date(2018, 1, 1) or days[-1] != date(2026, 9, 30):
        raise ValueError('synthetic input must cover the complete frozen calendar')
    if _moment(days[-1] + timedelta(days=1)) > captured:
        raise ValueError('snapshot precedes completed synthetic input')
    result = {name: [] for name in partitions}
    for name, (first, last) in partitions.items():
        cutoff = _moment(last + timedelta(days=1))
        day = first
        while day <= last:
            decision = _moment(day, 1)
            entry = _moment(day + timedelta(days=1))
            label_end = _moment(day + timedelta(days=2))
            simulated = _moment(day + timedelta(days=2), 1)
            needed = [day - timedelta(days=i) for i in (3, 2, 1)]
            target = day + timedelta(days=1)
            if simulated <= cutoff and all(k in data for k in needed) and target in data:
                a, b, c = (data[k]['close'] for k in needed)
                x1, x2 = finite(c / b - 1), finite(c / a - 1)
                y = finite(data[target]['close'] / data[target]['open'] - 1)
                result[name].append({'decision': decision.isoformat(), 'entry': entry.isoformat(),
                    'label_end': label_end.isoformat(), 'simulated_label_available': simulated.isoformat(),
                    'actual_available_at': None, 'availability_basis': 'retrospective_assumption',
                    'snapshot_collected_at': captured.isoformat(), 'dataset_version': dataset_version,
                    'feature_start': _moment(day - timedelta(days=1)).isoformat(),
                    'feature_end': _moment(day).isoformat(), 'simulated_information_cutoff': decision.isoformat(),
                    'feature_age_seconds': 3600,
                    'x': [x1, x2], 'y': y})
            day += timedelta(days=1)
    return result


def fit_ridge(x, y, alpha):
    method = _checked_method()
    alpha = finite(alpha)
    if alpha not in method['ridge']['alphas'] or not x or len(x) != len(y):
        raise ValueError('invalid training or alpha')
    if any(not isinstance(row, (list, tuple)) or len(row) != 2 for row in x):
        raise ValueError('two features required')
    x = [[finite(v) for v in row] for row in x]
    y = [finite(v) for v in y]
    n = len(y)
    means = [finite(math.fsum(row[j] for row in x) / n) for j in range(2)]
    try:
        std = [finite(math.sqrt(finite(math.fsum((row[j] - means[j]) ** 2 for row in x) / n))) for j in range(2)]
        if min(std) <= 0:
            raise ValueError('zero standard deviation')
        z = [[finite((row[j] - means[j]) / std[j]) for j in range(2)] for row in x]
        zm = [finite(math.fsum(row[j] for row in z) / n) for j in range(2)]
        ym = finite(math.fsum(y) / n)
        zc = [[finite(row[j] - zm[j]) for j in range(2)] for row in z]
        yc = [finite(v - ym) for v in y]
        s11 = finite(math.fsum(row[0] * row[0] for row in zc) / n)
        s12 = finite(math.fsum(row[0] * row[1] for row in zc) / n)
        s22 = finite(math.fsum(row[1] * row[1] for row in zc) / n)
        t1 = finite(math.fsum(row[0] * v for row, v in zip(zc, yc)) / n)
        t2 = finite(math.fsum(row[1] * v for row, v in zip(zc, yc)) / n)
        l11 = finite(math.sqrt(finite(s11 + alpha)))
        if l11 <= 0: raise ValueError('invalid Cholesky pivot')
        l21 = finite(s12 / l11)
        q = finite(finite(s22 + alpha) - finite(l21 * l21))
        if q <= 0: raise ValueError('invalid Cholesky pivot')
        l22 = finite(math.sqrt(q))
        u1 = finite(t1 / l11); u2 = finite((t2 - l21 * u1) / l22)
        w2 = finite(u2 / l22); w1 = finite((u1 - l21 * w2) / l11)
        intercept = finite(ym - w1 * zm[0] - w2 * zm[1])
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError('numeric calculation failed') from error
    return {'alpha': alpha, 'mean': means, 'std': std, 'intercept': intercept, 'weights': [w1, w2]}


def predict(model, x):
    if len(x) != 2: raise ValueError('two features required')
    z = [finite((finite(x[j]) - model['mean'][j]) / model['std'][j]) for j in range(2)]
    return finite(model['intercept'] + model['weights'][0] * z[0] + model['weights'][1] * z[1])


def mse(pairs):
    errors = []
    for predicted, actual in pairs:
        diff = finite(finite(predicted) - finite(actual))
        errors.append(finite(diff * diff))
    if not errors: raise ValueError('empty sample')
    return finite(math.fsum(errors) / len(errors))


def choose_ridge(train, tune):
    alphas = _checked_method()['ridge']['alphas']
    if not train or not tune: raise ValueError('empty training or tuning')
    models = [fit_ridge([r['x'] for r in train], [r['y'] for r in train], a) for a in alphas]
    scores = [mse((predict(m, r['x']), r['y']) for r in tune) for m in models]
    index = min(range(len(alphas)), key=lambda i: (scores[i], -alphas[i]))
    return models[index], scores


def _require_protocol_runtime():
    if sys.implementation.name != 'cpython' or sys.version_info[:3] != (3, 12, 14):
        raise ValueError('bootstrap requires CPython 3.12.14')


def bootstrap(dates, differences):
    _checked_method()
    _require_protocol_runtime()
    if len(dates) != len(differences) or len(dates) // 14 < 20:
        raise ValueError('insufficient aligned bootstrap data')
    try:
        ds = [date.fromisoformat(s) for s in dates]
    except (ValueError, TypeError) as error:
        raise ValueError('invalid dates') from error
    if any(d.isoformat() != s for d, s in zip(ds, dates)) or any(b-a != timedelta(days=1) for a,b in zip(ds,ds[1:])):
        raise ValueError('dates must be contiguous')
    values = [finite(v) for v in differences]
    n = len(values); rng = random.Random(20261108); means = []
    all_starts = []
    for _ in range(2000):
        starts = [rng.randrange(n - 14 + 1) for _ in range((n + 13) // 14)]
        sample = [v for start in starts for v in values[start:start+14]][:n]
        means.append(finite(math.fsum(sample) / n)); all_starts.append(starts)
    means.sort()
    def quantile(p):
        pos = p * (len(means)-1); lo = math.floor(pos); hi = math.ceil(pos)
        return finite(means[lo] + (means[hi] - means[lo]) * (pos - lo))
    return {'mean': finite(math.fsum(values)/n), 'lower': quantile(.025), 'upper': quantile(.975),
            'n': n, 'block_length': 14, 'resamples': 2000, 'seed': 20261108,
            'unique_possible_starts': n-13, 'starts_sha256': digest(all_starts),
            'status': 'descriptive_only'}


def _seal_success_envelope(envelope):
    """Finalize only after complete results and metadata are available."""
    envelope['envelope_sha256'] = digest(envelope)
    return envelope


def run_synthetic(rows, *, dataset_kind, snapshot_collected_at, dataset_version, protocol_bytes, method_config=None):
    """One in-memory run; failure envelope never exposes partial evaluation.

    No persistence or external authentication is claimed. Caller-provided
    synthetic provenance and collection times remain unverified declarations.
    """
    config = None
    input_hash = None
    stage = 'method_validation'
    try:
        method = _checked_method(method_config)
        stage = 'runtime_validation'
        _require_protocol_runtime()
        stage = 'protocol_validation'
        if not isinstance(protocol_bytes, bytes) or hashlib.sha256(protocol_bytes).hexdigest() != method['protocol_sha256']:
            raise ValueError('protocol fingerprint mismatch')
        config = {'method': method, 'dataset_kind': dataset_kind,
                  'dataset_version': dataset_version, 'snapshot_collected_at': snapshot_collected_at}
        stage = 'input_snapshot'
        snapshot = capture_synthetic_input(rows)
        input_hash = hashlib.sha256(snapshot).hexdigest()
        stage = 'sample_construction'
        partitions = _samples_from_snapshot(snapshot, dataset_kind=dataset_kind,
                                            snapshot_collected_at=snapshot_collected_at,
                                            dataset_version=dataset_version)
        train, tune, evaluation = (partitions[k] for k in ('train', 'tune', 'evaluation'))
        stage = 'training_and_tuning'
        model, scores = choose_ridge(train, tune)
        stage = 'evaluation'
        forecasts = [{'decision': s['decision'], 'ridge': predict(model, s['x']),
                      'previous_return': s['x'][0], 'zero': 0.0, 'target': s['y']} for s in evaluation]
        if not forecasts: raise ValueError('empty evaluation')
        errors = [finite(finite(finite(r['ridge'] - r['target']) ** 2) -
                         finite(finite(r['previous_return'] - r['target']) ** 2)) for r in forecasts]
        stage = 'bootstrap'
        uncertainty = bootstrap([r['decision'][:10] for r in forecasts], errors)
        stage = 'evaluation_metrics'
        metrics = {'ridge_mse': mse((r['ridge'], r['target']) for r in forecasts),
                   'previous_mse': mse((r['previous_return'], r['target']) for r in forecasts),
                   'zero_mse': mse((0.0, r['target']) for r in forecasts),
                   'loss_difference_bootstrap': uncertainty}
        _checked_method(method_config)
        outcome = {'status': 'complete', 'model': model, 'tuning_mse': scores,
                   'partition_counts': {name: len(v) for name, v in partitions.items()},
                   'sample_sha256': {name: digest(v) for name, v in partitions.items()},
                   'predictions_sha256': digest(forecasts), 'metrics': metrics}
        stage = 'receipt_generation'
        config_hash = digest(config)
        envelope = {'scope': 'synthetic_engineering_only', 'protocol_sha256': PROTOCOL_SHA256,
                    'supplied_protocol_sha256': hashlib.sha256(protocol_bytes).hexdigest(),
                    'config': config, 'config_sha256': config_hash, 'input_sha256': input_hash,
                    'input_hash_status': 'available', 'input_hash_semantics': 'sha256_canonical_json_v1',
                    'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'python_version': sys.version.split()[0], 'execution_proven': False,
                    'source_authenticity': 'unverified', 'research_admission': 'insufficient_evidence',
                    'outcome': outcome}
        return _seal_success_envelope(envelope)
    except Exception as error:
        # Do not re-read caller inputs, serialize untrusted config, or retry the
        # failed receipt construction. A missing digest is not fabricated.
        return {'scope': 'synthetic_engineering_only', 'protocol_sha256': '4c9a904d12c469a23cec928e195bf2c91a111cb7f47d4cbb1f52177fef5bc82e',
                'input_sha256': input_hash, 'input_hash_status': 'available' if input_hash is not None else 'unavailable',
                'envelope_sha256': None, 'receipt_integrity': 'unavailable',
                'execution_proven': False, 'source_authenticity': 'unverified',
                'research_admission': 'insufficient_evidence',
                'outcome': {'status': 'failed', 'stage': stage, 'error_type': type(error).__name__,
                            'error': 'ordinary run exception; details not inspected'}}


def verify_synthetic_run(envelope, rows, *, dataset_kind, snapshot_collected_at, dataset_version, protocol_bytes, method_config=None):
    """Recompute exact in-memory results; never proves origin or execution."""
    if not isinstance(envelope, dict) or envelope.get('outcome', {}).get('status') != 'complete':
        raise ValueError('complete synthetic envelope required')
    _checked_method(method_config)
    try:
        _checked_method(envelope['config']['method'])
        if envelope['config_sha256'] != digest(envelope['config']):
            raise ValueError('configuration fingerprint mismatch')
    except (KeyError, TypeError) as error:
        raise ValueError('invalid configuration evidence') from error
    replay = run_synthetic(rows, dataset_kind=dataset_kind, snapshot_collected_at=snapshot_collected_at,
                           dataset_version=dataset_version, protocol_bytes=protocol_bytes, method_config=method_config)
    if replay['outcome']['status'] != 'complete' or canonical(replay) != canonical(envelope):
        raise ValueError('synthetic replay mismatch')
    return {'status': 'internally_recomputed', 'execution_proven': False,
            'source_authenticity': 'unverified', 'research_admission': 'insufficient_evidence'}
