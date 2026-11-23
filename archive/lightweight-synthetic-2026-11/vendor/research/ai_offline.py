"""Pure offline evaluation primitives; no collection, credentials or file writes.

The synthetic evaluation API is an engineering check, not a research gate.
Callers must not treat self-declared availability or synthetic provenance as
independent evidence. No market data is loaded by this module.
"""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys

from strategies import execution
from strategies.execution import FEE, SLIPPAGE, buy_fill, sell_fill

UTC = timezone.utc


def utc(value):
    if not isinstance(value, str):
        raise ValueError('A timezone-aware ISO timestamp is required')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Invalid ISO timestamp') from error
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Naive timestamps are not allowed')
    return stamp.astimezone(UTC)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Finite numeric value required')
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError('Finite numeric value required') from error
    if not math.isfinite(value):
        raise ValueError('Finite numeric value required')
    return value


def finite_return(numerator, denominator):
    """Reject numerical overflow separately from freshness decisions."""
    ratio = number(numerator / denominator)
    return number(ratio - 1.0)


def mse(pairs):
    terms = []
    for prediction, actual in pairs:
        difference = number(prediction - actual)
        terms.append(number(difference * difference))
    return number(number(sum(terms)) / len(terms))


def require_finite_tree(value):
    if isinstance(value, float):
        number(value)
    elif isinstance(value, dict):
        for item in value.values():
            require_finite_tree(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            require_finite_tree(item)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def adapt_ohlc_bytes(blob, *, expected_sha256, collected_at_utc):
    """Convert verified CSV bytes in memory; all historical rows remain backfill.

    Publication times and prior versions are unknown. The declared collection
    time is a lower bound on when this local snapshot was available, not proof
    of original publication or availability at its historical event time.
    """
    if not isinstance(blob, bytes) or sha(blob) != expected_sha256:
        raise ValueError('Snapshot fingerprint mismatch')
    collected = utc(collected_at_utc)
    reader = csv.DictReader(io.StringIO(blob.decode('utf-8-sig'), newline=''))
    if reader.fieldnames != ['timestamp', 'open', 'high', 'low', 'close']:
        raise ValueError('Unexpected OHLC schema')
    rows, previous = [], None
    for row in reader:
        if None in row or not row['timestamp'].isdigit():
            raise ValueError('Invalid OHLC row')
        seconds = int(row['timestamp'])
        try:
            start = datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError('Invalid UTC epoch seconds') from error
        if start.hour or start.minute or start.second or start.microsecond:
            raise ValueError('Daily candle must start at UTC midnight')
        if previous is not None and start != previous + timedelta(days=1):
            raise ValueError('Daily candles must be unique and contiguous')
        previous = start
        try:
            prices = {key: number(float(row[key])) for key in ('open', 'high', 'low', 'close')}
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid OHLC price') from error
        if min(prices.values()) <= 0 or prices['high'] < max(prices['open'], prices['close'], prices['low']) or prices['low'] > min(prices['open'], prices['close']):
            raise ValueError('Invalid OHLC range')
        end = start + timedelta(days=1)
        if end > collected:
            raise ValueError('Snapshot contains a candle not yet completed at collection')
        rows.append({'start_utc': start.isoformat(), 'end_utc': end.isoformat(),
                     'available_at_utc': max(end, collected).isoformat(),
                     'publication_at_utc': None, 'acquisition_kind': 'retrospective_snapshot',
                     **prices})
    if not rows:
        raise ValueError('Empty snapshot')
    return {'schema_version': 1, 'snapshot_sha256': expected_sha256,
            'collected_at_utc': collected.isoformat(), 'point_in_time_status': 'unverified',
            'volume': 'not_provided', 'rows': rows}


def visible_price_features(rows, decision_at_utc, *, max_age_seconds):
    """UTC daily features with an explicit age limit and missing-day status."""
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        raise ValueError('Explicit nonnegative integer max_age_seconds required')
    decision = utc(decision_at_utc)
    expected_end = decision.replace(hour=0, minute=0, second=0, microsecond=0)
    visible = []
    for row in rows:
        event = utc(row['end_utc'])
        if event != event.replace(hour=0, minute=0, second=0, microsecond=0):
            raise ValueError('Daily candle must end at UTC midnight')
        if event > decision:
            continue
        available = utc(row['available_at_utc'])
        if available < event:
            raise ValueError('Availability precedes event end')
        if available <= decision:
            close = number(row['close'])
            if close <= 0:
                raise ValueError('Close must be positive')
            visible.append((event, close, available))
    visible.sort(key=lambda x: x[0])
    if len({r[0] for r in visible}) != len(visible):
        raise ValueError('Duplicate visible event time')
    result = {'status': 'insufficient_data', 'max_age_seconds': max_age_seconds,
              'expected_latest_end_utc': expected_end.isoformat(),
              'missing_latest': not visible or visible[-1][0] != expected_end}
    if visible:
        age = (decision - visible[-1][0]).total_seconds()
        result.update(latest_end_utc=visible[-1][0].isoformat(), age_seconds=age)
        if age > max_age_seconds:
            result['status'] = 'stale'
            return result
        if result['missing_latest']:
            result['status'] = 'missing_latest'
            return result
    if len(visible) < 3:
        return result
    a, b, c = visible[-3:]
    if b[0] - a[0] != timedelta(days=1) or c[0] - b[0] != timedelta(days=1):
        raise ValueError('Feature window has missing daily data')
    return {**result, 'status': 'fresh', 'return_1d': finite_return(c[1], b[1]), 'return_2d': finite_return(c[1], a[1]),
            'feature_event_end_utc': c[0].isoformat(),
            'feature_available_at_utc': max(x[2] for x in (a, b, c)).isoformat()}


def eligible_training(rows, cutoff_utc):
    """Only finished labels available by cutoff can enter a training sample."""
    cutoff = utc(cutoff_utc)
    eligible = []
    for row in rows:
        decision = utc(row['decision_at_utc'])
        if decision >= cutoff:
            continue
        end = utc(row['label_end_utc'])
        if end <= decision:
            raise ValueError('Invalid training label end')
        if end > cutoff:
            continue
        feature_end = utc(row['feature_event_end_utc'])
        feature_available = utc(row['feature_available_at_utc'])
        available = utc(row['label_available_at_utc'])
        if not (feature_end <= feature_available <= decision < end <= available):
            raise ValueError('Invalid training feature or label timing')
        if available <= cutoff:
            if 'label_value' not in row:
                raise ValueError('Visible training label value is required')
            number(row['label_value'])
            eligible.append(row)
    return eligible


def evaluate_synthetic(rows, *, dataset_kind, source_bytes, as_of_utc, model_id):
    """Evaluate supplied synthetic forecasts against zero and modeled costs.

    No model is fitted here. `dataset_kind` is a declaration, not proof of the
    input's origin; this interface is for offline engineering checks only.
    """
    if dataset_kind != 'synthetic' or not isinstance(source_bytes, bytes) or not model_id:
        raise ValueError('Only declared synthetic data and a model ID are supported')
    as_of = utc(as_of_utc)
    if not rows:
        raise ValueError('No test predictions')
    checked = []
    for row in rows:
        decision = utc(row['decision_at_utc'])
        feature_event = utc(row['feature_event_end_utc'])
        feature_available = utc(row['feature_available_at_utc'])
        fitted = utc(row['model_fitted_at_utc'])
        train_end = utc(row['max_train_label_end_utc'])
        train_available = utc(row['max_train_label_available_at_utc'])
        entry = utc(row['entry_at_utc'])
        end = utc(row['label_end_utc'])
        available = utc(row['label_available_at_utc'])
        if not (feature_event <= feature_available <= decision and train_end <= train_available <= fitted <= decision
                and decision < entry < end <= available <= as_of):
            raise ValueError('Feature, training, execution or outcome timing violation')
        prediction, actual = number(row['prediction']), number(row['actual_return'])
        entry_price, exit_price = number(row['entry_price']), number(row['exit_price'])
        if entry_price <= 0 or exit_price <= 0 or not math.isclose(actual, finite_return(exit_price, entry_price), rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError('Outcome must match positive entry/exit prices')
        if row['position'] not in ('flat', 'long'):
            raise ValueError('Only predeclared flat/long synthetic positions are supported')
        final = 1.0
        if row['position'] == 'long':
            _, quantity, _ = buy_fill(1.0, entry_price)
            _, final, _ = sell_fill(quantity, exit_price)
        checked.append({'decision': decision, 'entry': entry, 'end': end,
                        'prediction': prediction, 'actual': actual, 'net_return': number(final - 1)})
    checked.sort(key=lambda r: r['decision'])
    if len({r['decision'] for r in checked}) != len(checked):
        raise ValueError('Duplicate decisions')
    if any(a['end'] > b['entry'] for a, b in zip(checked, checked[1:])):
        raise ValueError('Overlapping synthetic positions are not supported')
    count = len(checked)
    result = {'schema_version': 1, 'scope': 'synthetic_engineering_only',
            'research_admission': 'insufficient_evidence', 'formal_statistics_enabled': False,
            'model_id': model_id, 'source_sha256': sha(source_bytes),
            'evaluator_sha256': sha(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
            'execution_model_sha256': sha(Path(execution.__file__).read_bytes().replace(b'\r\n', b'\n')),
            'python_version': sys.version.split()[0],
            'input_sha256': sha(canonical(rows)), 'as_of_utc': as_of.isoformat(),
            'samples': count,
            'model_mse': mse((r['prediction'], r['actual']) for r in checked),
            'zero_baseline_mse': mse((0.0, r['actual']) for r in checked),
            'net_returns_per_independent_sample': [r['net_return'] for r in checked],
            'fee_per_side': FEE, 'slippage_per_side': SLIPPAGE,
            'limitations': ['Synthetic input only; timestamps and provenance are caller claims.',
                            'No fitted model, MA baseline, market result or statistical inference.']}
    require_finite_tree(result)
    return result
